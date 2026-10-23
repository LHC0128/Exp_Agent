"""复合输出、同频筛选、多控制采集与离线分析的无硬件回归。"""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import math
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from lockin_amplifier import DAQResult
from lab_workflows import control_sources
from lab_workflows.common import WorkflowCancelled
from ..rf_sensitivity_direct_aw.test_workflow import rig, control_source, assert_shutdown
from ..rf_sensitivity_direct_aw import acquisition as rf_acquisition
from . import workflow, excitation, acquisition, analysis_steps
from .models import RFSensitivityKnownNoiseParams
from .waveform import generate_noise, synthesize_z
from .preparation import preflight
from .analysis import AnalysisWorkflow, analyze
from .stages import ACQUISITION

ACQUISITION_CASES = {
    "rf-sensitivity-aw-known-noise": {
        "mode": "grouped",
        "cases": {"entrypoints": ["test_manual_rf_phase_is_shared_without_moving_injected_noise",
                                   "test_bandwidth_never_acquires_configured_rf_frequency"]},
    },
}


@pytest.fixture
def known_rig(rig, monkeypatch):
    base = control_sources.resolve_control_results_root("oc_sens")
    # 自定义控制：参数文件版本号与目录名不一致；常数控制：无参数目录。
    for version, peak, parameters in (("v1", 1000, True), ("v2", 1500, True),
                                      ("自定义控制", 1200, True), ("常数控制", 800, False)):
        path = base / version / "waveforms/optimal_control_waveform.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        time = np.arange(100) / 300000
        np.savetxt(path, np.column_stack((time, peak * np.cos(2 * np.pi * 3000 * time))),
            delimiter=",", header="time_s,Omega_ctrl_Hz")
        if not parameters:
            continue
        params = base / version / "parameters/optimal_control_params.csv"
        params.parent.mkdir(parents=True, exist_ok=True)
        params.write_text(f"# Output version,{version if version.startswith('v') else 'v99'}\nf_rf_Hz,3000\n", encoding="utf-8")
    values = rig.params.to_external()
    values.update(CONTROL_WAVEFORM_FILES=["v1/waveforms/optimal_control_waveform.csv", "v2/waveforms/optimal_control_waveform.csv"],
        Z_RF_FREQ=3000, AW_REPEAT_FREQ=3000, PUMP_MOD_FREQ=90000, RF_AMP_START=-0.02, RF_AMP_STOP=0.02,
        RF_AMP_POINTS=9, NOISE_N_AVG=2, NOISE_RATE=1000, NOISE_NPERSEG=128, LINEWIDTH_MODE="dispersive_equivalent")
    rig.params = RFSensitivityKnownNoiseParams.from_external(values)
    for device in rig.generators.values():
        device.get_offset = lambda channel=1: 0.0
    for module in (excitation, acquisition):
        monkeypatch.setattr(module, "wait", lambda *_: None)
    def capture(hf2, config, **kwargs):
        if rig.daq_error:
            raise rig.daq_error
        kwargs["cancellation_check"]()
        columns = max(8, config.grid_cols)
        time = np.arange(columns) / kwargs["actual_rate"]
        if config.signal_paths == ["sample.x"]:
            z = rig.mapping["Z_magnetic_field"]
            assert rig.generators[z["resource"]].output[z["channel"]] is True
            noise = np.random.default_rng(3).normal(0, 1e-4, columns)
            return [DAQResult(signal_name="sample.x", demod_index=kwargs["demod_idx"], values=noise, time=time)]
        z = rig.mapping["Z_magnetic_field"]
        device = rig.generators[z["resource"]]
        normalized = next(event[3] for event in reversed(rig.events)
            if event[:3] == (z["resource"], "setup_arbitrary", z["channel"]))
        physical = normalized * device.amplitude[z["channel"]] / 2
        grid = np.arange(len(physical)) / (len(physical) * device.frequency[z["channel"]])
        frequency = hf2.get_double(hf2.osc_path(rig.params.demod3_osc_idx) + "/freq")
        # 直接由实际上传数组提取 RF 复幅度，独立检验相位只作用于正弦分量。
        coefficient = 2 * np.mean(physical * np.exp(-2j * np.pi * frequency * grid))
        response = 0.02 + 1e-4 * coefficient * 1j * np.exp(-1j * math.radians(37)) / (abs(coefficient) ** 2 + 0.01 ** 2)
        return [DAQResult(signal_name=name, demod_index=kwargs["demod_idx"], values=np.full(columns, value), time=time)
            for name, value in (("sample.r", abs(response)), ("sample.x", response.real), ("sample.y", response.imag))]
    monkeypatch.setattr(rf_acquisition.daq, "acquire_data", capture)
    return rig



def test_bandwidth_grid_skips_current_carrier():
    params = RFSensitivityKnownNoiseParams(z_rf_freq=3000, bw_freq_start=2500, bw_freq_stop=3500, bw_freq_points=51)
    assert len(params.bandwidth_frequencies) == 50
    assert 3000 not in params.bandwidth_frequencies
    assert 2980 in params.bandwidth_frequencies and 3020 in params.bandwidth_frequencies
    changed = replace(params, z_rf_freq=3100)
    assert 3100 not in changed.bandwidth_frequencies and 3000 in changed.bandwidth_frequencies
    too_few = replace(params, linewidth_mode="frequency_sweep", bw_freq_points=5)
    assert any("至少保留 5" in error for error in too_few.validate_model())
    stage = next(item for item in ACQUISITION if item.id == "A13B")
    assert "Z_RF_FREQ" in stage.display_parameters
    assert any("自动跳过" in text for text in stage.quality_gates)


def test_bandwidth_skip_marker_preserves_legacy_analysis(tmp_path):
    from ..rf_sensitivity_direct_aw.analysis_steps import fit_bandwidth
    raw = tmp_path / "raw"
    raw.mkdir()
    full = np.linspace(2500, 3500, 51)
    selected = full[full != 3000]
    config = {"parameters": {"LINEWIDTH_MODE": "frequency_sweep", "BW_FREQ_START": 2500,
        "BW_FREQ_STOP": 3500, "BW_FREQ_POINTS": 51, "Z_RF_FREQ": 3000}}
    def save(frequencies, **metadata):
        response = 0.1 + 0.3 * 100 ** 2 / ((frequencies - 3000) ** 2 + 100 ** 2)
        np.savez(raw / "bandwidth_data.npz", frequency_Hz=frequencies, demod3_r_V=response,
                 z_rf_amplitude_vpp=0.01, **metadata)
    save(selected, skipped_z_rf_frequency_Hz=3000)
    assert fit_bandwidth(tmp_path, config)["n_points"] == 50
    save(full)
    assert fit_bandwidth(tmp_path, config)["n_points"] == 51
    save(selected)
    with pytest.raises(ValueError, match="不完整"):
        fit_bandwidth(tmp_path, config)
    save(selected[:-1], skipped_z_rf_frequency_Hz=3000)
    with pytest.raises(ValueError, match="不完整"):
        fit_bandwidth(tmp_path, config)


def test_bandwidth_never_acquires_configured_rf_frequency(known_rig, monkeypatch):
    known_rig.params = replace(known_rig.params, scan_rf_phase=False, linewidth_mode="frequency_sweep",
        bw_freq_start=2400, bw_freq_stop=3600, bw_freq_points=7, temp_group_size=2)
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    frequencies = []
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id not in ("A13B", "A15", "A16")])
        acquire = rf_acquisition.daq.acquire_data
        def capture(hf2, *args, **kwargs):
            value = hf2.get_double(hf2.osc_path(known_rig.params.demod3_osc_idx) + "/freq")
            assert not math.isclose(value, known_rig.params.z_rf_freq, abs_tol=1e-6)
            frequencies.append(value)
            return acquire(hf2, *args, **kwargs)
        monkeypatch.setattr(rf_acquisition.daq, "acquire_data", capture)
        ctx.execute(["A13B"])
        expected = known_rig.params.bandwidth_frequencies
        np.testing.assert_allclose(frequencies, np.tile(expected, 2))
        for version in ("v1", "v2"):
            child = ctx.directory.root / "controls" / version
            with np.load(child / "raw/bandwidth_data.npz", allow_pickle=False) as data:
                np.testing.assert_allclose(data["frequency_Hz"], expected)
                assert float(data["skipped_z_rf_frequency_Hz"]) == 3000
            config = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
            assert config["bandwidth_acquisition"]["points"] == 6
            assert config["bandwidth_acquisition"]["requested_points"] == 7
    finally:
        ctx.finish()
    assert_shutdown(known_rig)


def test_sum_normalization_preserves_noise_and_rf_phase():
    params = RFSensitivityKnownNoiseParams()
    noise = generate_noise(params)
    # 噪声幅度自动对齐设计 PSD：实际谱与设计谱一致。
    assert math.isclose(noise["noise_vpp_V"], 2.0 * float(np.max(np.abs(noise["noise_V"]))))
    np.testing.assert_allclose(noise["actual_psd_V2_per_Hz"], noise["design_psd_V2_per_Hz"])
    for amplitude in (-0.1, 0.0, 0.04):
        for phase in (0, 37, 180):
            wave = synthesize_z(noise, amplitude, 3000, phase)
            reconstructed = wave["normalized"] * wave["vpp_V"] / 2
            np.testing.assert_allclose(reconstructed - wave["rf_V"], noise["noise_V"], atol=1e-17)
            assert np.max(np.abs(wave["normalized"])) <= 1
            if amplitude == 0:
                np.testing.assert_array_equal(wave["voltage_V"], noise["noise_V"])
    with pytest.raises(ValueError, match="整数倍"):
        synthesize_z(noise, 0.01, 3001)


def test_custom_named_control_versions(known_rig):
    """自定义命名的控制目录（含中文、无参数文件）可列出、校验并通过预检。"""
    schema = RFSensitivityKnownNoiseParams().schema()
    field = next(item for item in schema["fields"] if item["name"] == "CONTROL_WAVEFORM_FILES")
    offered = {option["value"] for option in field["options"]}
    assert "自定义控制/waveforms/optimal_control_waveform.csv" in offered
    assert "常数控制/waveforms/optimal_control_waveform.csv" in offered
    values = known_rig.params.to_external() | {"CONTROL_WAVEFORM_FILES": [
        "自定义控制/waveforms/optimal_control_waveform.csv",
        "常数控制/waveforms/optimal_control_waveform.csv"]}
    params = RFSensitivityKnownNoiseParams.from_external(values, schema_version=4)
    assert params.validate(known_rig.tmp_path) == []
    preflight(params, known_rig.tmp_path)
    assert not known_rig.events


def test_v3_config_migrates_manual_amplitude_away(known_rig):
    values = known_rig.params.to_external() | {"NOISE_AMPLITUDE_VPP": 0.0034}
    params = RFSensitivityKnownNoiseParams.from_external(values, schema_version=3)
    assert not hasattr(params, "noise_amplitude_vpp")
    with pytest.raises(ValueError, match="未知参数"):
        RFSensitivityKnownNoiseParams.from_external(values, schema_version=4)


def test_wrong_frequency_rejected_without_hardware(known_rig):
    base = control_sources.resolve_control_results_root("oc_sens")
    (base / "v2/parameters/optimal_control_params.csv").write_text("# Output version,v2\nf_rf_Hz,12000\n", encoding="utf-8")
    with pytest.raises(ValueError):
        preflight(known_rig.params, known_rig.tmp_path)
    assert not known_rig.events


def test_output_readback_mismatch_stops_before_acquisition(known_rig):
    z = known_rig.mapping["Z_magnetic_field"]
    known_rig.generators[z["resource"]].get_amplitude = lambda channel=1: 0.5
    with pytest.raises(RuntimeError, match="回读不一致"):
        workflow.run(known_rig.params, project_root=known_rig.tmp_path)
    assert_shutdown(known_rig)


def test_manual_rf_phase_is_shared_without_moving_injected_noise(known_rig, monkeypatch):
    known_rig.params = replace(known_rig.params, scan_rf_phase=False, z_burst_phase_init=181.5)
    real_upload = excitation.upload_z
    manual_temperatures = []
    def upload(ctx, *args, **kwargs):
        if ctx.current_step == "A11":
            temp = known_rig.mapping["Temp_Switch"]
            manual_temperatures.append(known_rig.generators[temp["resource"]].dc[temp["channel"]])
        return real_upload(ctx, *args, **kwargs)
    monkeypatch.setattr(excitation, "upload_z", upload)
    # 工作流导入的上传入口同样指向真实实现，记录仅观察手动相位时的温控。
    monkeypatch.setattr(workflow, "upload_z", upload)
    directory = workflow.run(known_rig.params, project_root=known_rig.tmp_path)
    assert manual_temperatures == [5.0, 5.0]
    for version in ("v1", "v2"):
        child = directory / "controls" / version
        config = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["rf_phase_calibration"]["mode"] == "manual"
        assert config["rf_phase_calibration"]["selected_phase_deg"] == 181.5
        assert config["rf_phase_calibration"]["points_completed"] == 0
        assert not (child / "raw/z_phase_scan.npz").exists()
        with np.load(next(child.glob("raw/z_sum_A11_*.npz"))) as saved:
            time = np.arange(len(saved["noise_V"])) / (len(saved["noise_V"]) * float(saved["repeat_frequency_Hz"]))
            rf = float(saved["rf_vpp_V"]) / 2 * np.sin(2 * np.pi * float(saved["rf_frequency_Hz"]) * time + np.deg2rad(181.5))
            np.testing.assert_allclose(saved["voltage_V"] - saved["noise_V"], rf, atol=1e-14)
        with np.load(child / "raw/response_data.npz") as saved:
            np.testing.assert_allclose(saved["z_burst_phase_deg"],
                (181.5 + np.where(saved["amplitudes_V"] < 0, 180, 0)) % 360)
    assert_shutdown(known_rig)
    result = analyze(directory)
    assert result["status"] == "completed" and result["rf_phase_scan_status"] == "skipped"
    assert not (directory / "results/z_phase_response_comparison.png").exists()
    assert (directory / "results/full_analysis.png").is_file()


def test_two_controls_complete_and_analyze_without_sources(known_rig, monkeypatch):
    figures = []
    original = analysis_steps.save_figure
    def capture(figure, path):
        if path.name == "full_analysis.png":
            figures.append(figure)
        return original(figure, path)
    monkeypatch.setattr(analysis_steps, "save_figure", capture)
    directory = workflow.run(known_rig.params, project_root=known_rig.tmp_path)
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["schema_version"] == RFSensitivityKnownNoiseParams.schema_version
    assert config["completion_status"] == "completed"
    assert list(config["controls"]) == ["v1", "v2"]
    for version in ("v1", "v2"):
        child = directory / "controls" / version
        saved = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert saved["schema_version"] == RFSensitivityKnownNoiseParams.schema_version
        assert saved["completion_status"] == "completed"
        assert saved["stage_frequencies"]["noise"]["rf_frequency_Hz"] == 3000
        assert saved["noise_output_snapshot"]["z"]["frequency_Hz"] == 10
        assert saved["noise_output_snapshot"]["z"]["output"] is True
        zero = next(child.glob("raw/z_sum_A14_*.npz"))
        with np.load(zero) as data:
            assert float(data["rf_vpp_V"]) == 0
            np.testing.assert_array_equal(data["voltage_V"], data["noise_V"])
    assert_shutdown(known_rig)
    monkeypatch.setitem(control_sources.CONTROL_SOURCE_SET_ROOTS, "oc_sens", Path("missing_control_source"))
    result = analyze(directory)
    assert result["status"] == "completed"
    assert list(result["controls"]) == ["v1", "v2"]
    assert result["controls"]["v1"]["plot_label"] == "v1"
    assert result["controls"]["v2"]["plot_label"] == "v2"
    assert (directory / "results/full_analysis.png").is_file()
    assert len(figures[0].axes) == 2
    assert figures[0].axes[1].get_ylabel() == "Sensitivity (fT/sqrt(Hz))"
    assert all(axis.get_legend_handles_labels()[1] == ["v1", "v2"] for axis in figures[0].axes)
    assert (directory / "results/z_phase_response_comparison.png").is_file()
    assert (directory / "results/z_phase_response_comparison.pdf").is_file()
    ctx = AnalysisWorkflow(directory, {"NOISE_NPERSEG": 64})
    assert ctx.states["B02"]["status"] == "completed"
    assert ctx.states["B03"]["status"] == "stale"
    ctx.execute(["B03", "B04"])


def test_missing_dependency_does_not_connect(known_rig):
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    with pytest.raises(ValueError):
        ctx.execute(["A13"])
    assert not known_rig.events


def test_noise_rerun_keeps_predecessors_and_uses_new_directory(known_rig):
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    first_steps = [stage.id for stage in ACQUISITION if stage.id not in ("A14", "A13B", "A15", "A16")]
    ctx.execute(first_steps)
    old = ctx.directory.root
    before = (old / "controls/v1/raw/response_data.npz").read_bytes()
    ctx.update_parameters({"NOISE_N_AVG": 1})
    ctx.execute(["A14", "A15"])
    assert ctx.directory.root != old
    assert (old / "controls/v1/raw/response_data.npz").read_bytes() == before
    for version in ("v1", "v2"):
        assert len(list((ctx.directory.root / "controls" / version / "raw").glob("noise_D3_*.npz"))) == 1
    assert analyze(ctx.directory.root)["status"] == "completed"
    ctx.finish()


def test_quality_failure_blocks_successors_and_can_retry(known_rig):
    known_rig.xy_r = 0.0
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    ctx.execute([stage.id for stage in ACQUISITION if stage.id not in ("A13B", "A16")])
    assert ctx.states["A09"]["status"] == "quality_failed"
    old = ctx.directory.root
    with pytest.raises(ValueError):
        ctx.execute(["A13"])
    known_rig.xy_r = 1.0
    ctx.execute([stage.id for stage in ACQUISITION if stage.id in ("A09", "A10", "A11", "A12", "A13", "A14", "A15")])
    assert ctx.directory.root != old
    assert ctx.state["completion_status"] == "completed"
    ctx.finish()


def test_cancel_preserves_error_and_shutdown(known_rig):
    known_rig.daq_error = WorkflowCancelled("模拟取消")
    with pytest.raises(WorkflowCancelled, match="模拟取消"):
        workflow.run(known_rig.params, project_root=known_rig.tmp_path)
    assert_shutdown(known_rig)


def test_session_subprocess_eof_shuts_down_fake_instruments(tmp_path):
    program = '''
import json, os, sys
from pathlib import Path
import pytest
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise import session, workflow
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.test_workflow import control_source, rig, known_rig, assert_shutdown
mp = pytest.MonkeyPatch()
root = Path(sys.argv[1])
source = control_source.__wrapped__(root, mp)
devices = known_rig.__wrapped__(rig.__wrapped__(root, mp, source), mp)
contexts = []
def make(params, session_id):
    assert params.to_external() == devices.params.to_external()
    ctx = workflow.Workflow(params, project_root=root, session_id=session_id)
    contexts.append(ctx)
    return ctx
session.Workflow = make
os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(devices.params.to_external() | {"session_id": "subprocess-test"})
session.main()
assert contexts[0].closed
assert contexts[0].state["completion_status"] == "cancelled"
assert (contexts[0].directory.root / "controls/v1/raw/response_data.npz").is_file()
assert_shutdown(devices)
print("SESSION_TEST_OK")
'''
    steps = [stage.id for stage in ACQUISITION if stage.id not in ("A14", "A13B", "A15", "A16")]
    command = {"request_id": "first", "revision": 0, "action": "steps", "step_ids": steps}
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("LAB_CANCEL_FILE", None)
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)],
        input=json.dumps(command) + "\n", capture_output=True, text=True, encoding="utf-8",
        cwd=Path(__file__).resolve().parents[3], env=env, timeout=120)
    assert result.returncode == 0, result.stderr + result.stdout[-4000:]
    assert "SESSION_TEST_OK" in result.stdout


def test_save_failure_keeps_primary_error_and_actual_demod_mode(monkeypatch):
    child = SimpleNamespace(demod_mode="response", response_config=object(), outputs={"Z": "ON"},
        states={}, state={}, params=SimpleNamespace())
    def measure(_):
        child.demod_mode = "noise"
        raise RuntimeError("采集原始错误")
    def save():
        raise OSError("磁盘写入失败")
    child._execute_step, child.save = measure, save
    parent = SimpleNamespace(controls={"v1": child}, d0_config=None, phase0=None,
        state={"calibration": {}}, outputs={}, response_config=None, demod_mode="response")
    monkeypatch.setattr(acquisition, "activate", lambda *_: None)
    with pytest.raises(RuntimeError, match="采集原始错误"):
        acquisition.execute_controls(parent, "A14")
    assert parent.demod_mode == "noise"
    assert parent.response_config is child.response_config
    assert parent.state["state_save_error"] == "磁盘写入失败"
