"""逐控制 × Scale 的真实四级校相、分组采集及故障保护；仅使用假仪器。"""
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import numpy as np
import pytest
import yaml
from ...common import WorkflowCancelled
from ..rf_sensitivity_direct_aw.test_workflow import control_source, rig, assert_shutdown
from ..rf_sensitivity_direct_aw import acquisition as direct, phase, _helpers
from . import workflow, acquisition, preparation, stages, run_format
from .models import RFControlScaleParams

ACQUISITION_CASES = {
    "rf-control-scale-sensitivity": {
        "mode": "grouped",
        "cases": {
            "entrypoints": ["test_manual_rf_phase_is_shared_by_all_control_scale_points"],
            "order": ["test_real_scales_independent_four_level_phase_and_slow_disk"],
            "slow_disk": ["test_real_scales_independent_four_level_phase_and_slow_disk"],
            "group_change": ["test_real_scales_independent_four_level_phase_and_slow_disk"],
            "last_group": ["test_real_scales_independent_four_level_phase_and_slow_disk"],
            "cancel": ["test_failure_saves_partial_and_stops"],
            "acquire_failure": ["test_failure_saves_partial_and_stops"],
            "restore_failure": ["test_failure_saves_partial_and_stops"],
            "save_failure": ["test_failure_saves_partial_and_stops"],
            "partial_save": ["test_failure_saves_partial_and_stops"],
        },
        "sources": [
            "lab_workflows/experiment_modules/rf_control_scale_sensitivity/workflow.py",
            "lab_workflows/experiment_modules/rf_control_scale_sensitivity/preparation.py",
            "lab_workflows/experiment_modules/rf_control_scale_sensitivity/acquisition.py",
            "lab_workflows/experiment_modules/rf_control_scale_sensitivity/resume.py",
            "lab_workflows/experiment_modules/rf_control_scale_sensitivity/session.py",
            "experiments/RF_Control_Scale_Sensitivity.py",
        ],
    },
}


@pytest.fixture
def scale_rig(rig):
    from ...control_sources import CONTROL_SOURCE_SET_ROOTS, load_theory_control
    source = CONTROL_SOURCE_SET_ROOTS["oc_sens"]
    original = source / rig.params.arb_waveform_file
    for name in ("ControlA", "ControlB"):
        target = source / name / "waveforms/optimal_control_waveform.csv"
        target.parent.mkdir(parents=True)
        target.write_bytes(original.read_bytes())
    frequency = load_theory_control(source, "ControlA").theory_rf_frequency_hz
    values = rig.params.to_external() | {"CONTROL_WAVEFORM_FILES": [rig.params.arb_waveform_file]}
    params = RFControlScaleParams.from_external(values)
    rig.params = replace(params, control_waveform_files=[
        "ControlA/waveforms/optimal_control_waveform.csv",
        "ControlB/waveforms/optimal_control_waveform.csv"],
        control_scales=[0.9, 1.0, 1.1], rf_amp_points=7, slope_fit_points=7, temp_group_size=2,
        noise_n_avg=1, linewidth_mode="frequency_sweep", bw_freq_points=5,
        z_phase_scan_stop_deg=315., z_phase_scan_step_deg=45.,
        z_rf_freq=frequency, bw_freq_start=frequency - 1000, bw_freq_stop=frequency + 1000)
    return rig


def prepared(scale_rig):
    ctx = workflow.Workflow(scale_rig.params, project_root=scale_rig.tmp_path)
    ctx.execute([step.id for step in stages.ACQUISITION if step.id not in ("A13", "A15", "A16")])
    return ctx


def test_manual_rf_phase_is_shared_by_all_control_scale_points(scale_rig, monkeypatch):
    scale_rig.params = replace(scale_rig.params, scan_rf_phase=False, z_burst_phase_init=181.5,
                              control_scales=[0.9, 1.0])
    calls = []
    for name in ("calibrate_demod0", "calibrate_xy", "calibrate_rf", "calibrate_demod3"):
        real = getattr(phase, name)
        def calibrate(point, _name=name, _real=real):
            before = len(scale_rig.captures)
            calls.append(_name)
            result = _real(point)
            if _name == "calibrate_rf":
                assert len(scale_rig.captures) == before, "手动相位步骤不能采集扫描时序"
            return result
        monkeypatch.setattr(phase, name, calibrate)
    ctx = prepared(scale_rig)
    try:
        ctx.execute(["A13", "A15"])
        config = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
        from .analysis_steps import load_inputs
        points = load_inputs(ctx.directory.root, config)
        assert len(points) == 4
        assert calls == ["calibrate_demod0", "calibrate_xy", "calibrate_rf", "calibrate_demod3"] * 4
        for _, directory, child in points:
            assert child["parameters"]["Z_BURST_PHASE_INIT"] == 181.5
            assert child["rf_phase_calibration"]["mode"] == "manual"
            assert child["rf_phase_calibration"]["selected_phase_deg"] == 181.5
            assert child["rf_phase_calibration"]["points_completed"] == 0
            assert not (directory / "raw/z_phase_scan.npz").exists()
            with np.load(directory / "raw/response_data.npz") as data:
                np.testing.assert_allclose(data["z_burst_phase_deg"],
                    (181.5 + np.where(data["amplitudes_V"] < 0, 180, 0)) % 360)
        from .analysis import AnalysisWorkflow
        analysis = AnalysisWorkflow(ctx.directory.root)
        analysis.execute(["B01"])
        assert analysis.states["B01"]["status"] == "completed"
    finally:
        ctx.finish()
    assert_shutdown(scale_rig)


@pytest.mark.parametrize("use_range", [False, True])
def test_real_scales_independent_four_level_phase_and_slow_disk(scale_rig, monkeypatch, use_range):
    if use_range:
        scale_rig.params = replace(scale_rig.params, control_scale_start=1.6, control_scale_stop=.6,
            control_scale_points=3, control_scale_factors={"ControlA": 1.2, "ControlB": .8})
    ctx = prepared(scale_rig)
    enabled, events, windows, clock, calls, active = [True], [], [], [0.0], [], [None]
    real_point = acquisition.make_point
    def make_point(*args):
        active[0] = real_point(*args)
        return active[0]
    monkeypatch.setattr(acquisition, "make_point", make_point)
    for name in ("calibrate_demod0", "calibrate_xy", "calibrate_rf", "calibrate_demod3"):
        original = getattr(phase, name)
        def calibrate(point, _name=name, _original=original):
            identity = (Path(point.params.arb_waveform_file).parent.parent.name, point.state["control_scale"])
            calls.append((*identity, _name))
            if _name == "calibrate_demod0":
                assert point.phase0 is point.xy_phase is point.response_config is point.z_positive_phase is None
                assert "demod0" not in point.state["calibration"], "不能继承前一点的校准"
                scale_rig.z_phi0 = 25. + 17 * (len(calls) // 7)
            return _original(point)
        monkeypatch.setattr(phase, name, calibrate)
    for name in ("acquire_response", "acquire_noise", "acquire_bandwidth"):
        original = getattr(direct, name)
        def measure(point, _name=name, _original=original):
            calls.append((Path(point.params.arb_waveform_file).parent.parent.name, point.state["control_scale"], _name))
            return _original(point)
        monkeypatch.setattr(direct, name, measure)
    real_switch, real_acquire, real_save = _helpers.set_temperature_switch, direct.daq.acquire_data, np.savez
    def switch(device, value, **kwargs):
        real_switch(device, value, **kwargs)
        if not value:
            if active[0].state["point_stage"] in ("response", "noise", "bandwidth"):
                assert events[-1] == "configure"
            windows.append([clock[0], None])
        elif not enabled[0]:
            windows[-1][1] = clock[0]
        enabled[0] = value
        events.append("on" if value else "off")
    real_set = direct.set_z_rf
    def configure(*args, **kwargs):
        result = real_set(*args, **kwargs)
        events.append("configure")
        return result
    def wait(seconds):
        if not enabled[0]:
            assert seconds == ctx.params.system_settle_s
            events.append("settle")
        clock[0] += seconds
    def collect(*args, **kwargs):
        assert not enabled[0] and events[-1] == "settle"
        events.append("read")
        clock[0] += 1
        return real_acquire(*args, **kwargs)
    def save(*args, **kwargs):
        assert enabled[0], "慢磁盘不能占用任何校相/测量的温控关闭窗口"
        events.append("save")
        clock[0] += 30
        return real_save(*args, **kwargs)
    real_update = type(ctx.directory).update_config
    def update(directory, **kwargs):
        assert enabled[0], "温控关闭窗口内不能写采集元数据"
        return real_update(directory, **kwargs)
    monkeypatch.setattr(type(ctx.directory), "update_config", update)
    for module in (direct, phase, _helpers):
        monkeypatch.setattr(module, "set_temperature_switch", switch)
    monkeypatch.setattr(direct, "set_z_rf", configure)
    for module in (direct, phase):
        monkeypatch.setattr(module, "wait", wait)
    monkeypatch.setattr(direct.daq, "acquire_data", collect)
    monkeypatch.setattr(np, "savez", save)
    ctx.execute(["A13", "A15"])
    manifest = json.loads((ctx.directory.raw / "point_manifest.json").read_text(encoding="utf-8"))
    assert [(p["control"], p["scale"]) for p in manifest["points"]] == [
        (name, scale) for name in ("ControlA", "ControlB") for scale in scale_rig.params.control_scales]
    expected_calls = ("calibrate_demod0", "calibrate_xy", "calibrate_rf", "calibrate_demod3",
                      "acquire_response", "acquire_noise", "acquire_bandwidth")
    assert [call[2] for call in calls] == list(expected_calls) * 6
    assert len(windows) == 6 * (4 + 1 + 3 + 3 + 4)
    assert all(end is not None and end - begin < 30 for begin, end in windows)
    for index, record in enumerate(manifest["points"]):
        point = ctx.directory.root / record["path"]
        config = yaml.safe_load((point / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["calibration_scale"] == record["scale"]
        assert config["calibration_source"] == "."
        assert not run_format.point_calibration_block_reason(config)
        assert config["rf_phase_calibration"]["selected_phase_deg"] == pytest.approx(25. + 17 * index)
        with np.load(point / "raw/response_data.npz") as response:
            assert response["xy_ctrl_phase_deg"] == config["xy_phase_calibration"]["final_phase_deg"]
            np.testing.assert_allclose(response["z_burst_phase_deg"],
                (config["rf_phase_calibration"]["selected_phase_deg"] + np.where(response["amplitudes_V"] < 0, 180, 0)) % 360)
        for name in ("response_data.npz", "bandwidth_data.npz", "noise_D3_0000.npz", "z_phase_scan.npz", "xy_aw_waveforms_initial.npz"):
            with np.load(point / "raw" / name, allow_pickle=False) as saved:
                assert saved.files
        with np.load(point / "raw/xy_aw_waveforms.npz", allow_pickle=False) as saved:
            base = ctx.controls[record["control"]].envelope["omega_ctrl_Hz"]
            factor = ctx.params.control_scale_factor(ctx.controls[record["control"]].params.arb_waveform_file)
            assert record["control_scale_factor"] == config["control_scale_factor"] == factor
            assert record["effective_control_scale"] == config["effective_control_scale"] == record["scale"] * factor
            np.testing.assert_allclose(saved["omega_ctrl_Hz"], base * record["scale"] * factor)
    from .analysis import AnalysisWorkflow
    AnalysisWorkflow(ctx.directory.root).execute(["B01"])
    ctx.finish()
    assert_shutdown(scale_rig)


@pytest.mark.parametrize("fault", ["cancel", "acquire", "restore", "save"])
def test_failure_saves_partial_and_stops(scale_rig, monkeypatch, fault):
    ctx = prepared(scale_rig)
    real_acquire, real_save, real_switch = direct.daq.acquire_data, np.savez, _helpers.set_temperature_switch
    count, restore_attempt, failed, in_response = [0], [0], [False], [False]
    real_response = direct.acquire_response
    def response(point):
        in_response[0] = True
        try:
            return real_response(point)
        finally:
            in_response[0] = False
    monkeypatch.setattr(direct, "acquire_response", response)
    def collect(*args, **kwargs):
        if in_response[0]:
            count[0] += 1
        if in_response[0] and count[0] == 3 and fault in ("cancel", "acquire"):
            raise WorkflowCancelled("模拟取消") if fault == "cancel" else RuntimeError("模拟采集失败")
        return real_acquire(*args, **kwargs)
    def switch(device, value, **kwargs):
        if value:
            restore_attempt[0] += 1
            if fault == "restore" and in_response[0] and count[0] >= 2:
                raise OSError("模拟恢复失败")
        return real_switch(device, value, **kwargs)
    def save(file, *args, **kwargs):
        if count[0] >= 2:
            assert restore_attempt[0], "保存前必须先尝试恢复"
        if fault == "save" and in_response[0] and count[0] >= 2 and not failed[0]:
            failed[0] = True
            raise OSError("模拟保存失败")
        return real_save(file, *args, **kwargs)
    failure = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(direct.daq, "acquire_data", collect)
            patch.setattr(np, "savez", save)
            for module in (direct, _helpers):
                patch.setattr(module, "set_temperature_switch", switch)
            with pytest.raises((WorkflowCancelled, RuntimeError, OSError)) as caught:
                ctx.execute(["A13"])
            failure = caught.value
            assert count[0] <= 3
            files = list(ctx.directory.root.glob("controls/*/scales/*/raw/response_data.npz"))
            assert files
            with np.load(files[0], allow_pickle=False) as data:
                assert len(data["demod3_x_V"]) >= 1
    finally:
        ctx.finish(failure)
    assert_shutdown(scale_rig)


def test_missing_dependencies_and_rerun_preserve_raw(scale_rig, monkeypatch):
    # 保留两控制 × 两倍率与 RF 合法最少五点；独立校相不省略，仅隔离 RF 扫描出图。
    params = replace(scale_rig.params, control_waveform_files=scale_rig.params.control_waveform_files[:2],
                     control_scales=[.9, 1.], rf_amp_points=5, slope_fit_points=5,
                     scan_rf_phase=False, noise_rate=1000, noise_duration=0.01,
                     scan_point_duration_s=0.001)
    monkeypatch.setattr(workflow.shared.SingleControlWorkflow, "publish", lambda *_: None)
    factory = workflow.ControlWorkflow.create_control_run.__func__
    created = []

    def create(cls, params, **kwargs):
        assert not {"shared_calibration", "stage_states", "previous", "affected_stages"}.intersection(kwargs)
        before = len(scale_rig.events)
        child = factory(cls, params, **kwargs)
        assert len(scale_rig.events) == before and child.devices is kwargs["devices"]
        assert not {"demod0", "demod3"}.intersection(child.state["calibration"])
        assert all(state["status"] == "pending" for state in child.states.values())
        assert child.state["raw_format_version"] == run_format.RAW_FORMAT_VERSION
        assert child.state["phase_calibration_scope"] == run_format.PHASE_CALIBRATION_SCOPE
        assert "control_layout" not in child.state
        assert set(child.state["control_metrics"]) == {"envelope_rms_V", "envelope_peak_V"}
        assert child.state["control_source_sha256"] == hashlib.sha256(
            Path(child.envelope["source_waveform_path"]).read_bytes()).hexdigest()
        created.append(child)
        return child

    monkeypatch.setattr(workflow.ControlWorkflow, "create_control_run", classmethod(create))
    ctx = workflow.Workflow(params, project_root=scale_rig.tmp_path)
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A13"])
    assert not scale_rig.events
    ctx.execute([step.id for step in stages.ACQUISITION if step.id not in ("A15", "A16")])
    previous = ctx.directory.root
    raw = next(previous.glob("controls/*/scales/*/raw/response_data.npz"))
    before = raw.read_bytes()
    originals = {path: path.read_bytes() for path in previous.rglob("*.npz")}
    ctx.execute(["A13", "A15"])
    assert ctx.directory.root != previous and raw.read_bytes() == before
    assert len(created) == 4
    assert all(path.read_bytes() == value for path, value in originals.items())
    for root in (previous, ctx.directory.root):
        configs = list(root.glob("controls/*/scales/*/experiment_config.yaml"))
        assert len(configs) == 4
        for path in configs:
            point = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert point["phase_calibration_status"] == "completed"
            assert point["calibration_scale"] == point["control_scale"]
            assert point["calibration_source"] == "."
            assert {"demod0", "demod3"}.issubset(point["calibration"])
            assert all(point["phase_calibration_steps"][item]["status"] == "completed"
                       for item in run_format.POINT_CALIBRATION_STAGES)
    assert all(not {"demod0", "demod3"}.intersection(child.state["calibration"]) for child in created)
    ctx.finish()
    assert_shutdown(scale_rig)


@pytest.mark.parametrize("stage", ["calibrate_demod0", "calibrate_demod3"])
def test_phase_failure_blocks_measurement_and_retry_uses_new_directory(scale_rig, monkeypatch, stage):
    scale_rig.params = replace(scale_rig.params, control_waveform_files=scale_rig.params.control_waveform_files[:1], control_scales=[1.])
    ctx = prepared(scale_rig)
    with monkeypatch.context() as patch:
        def fail(point):
            raise phase.CalibrationQualityError("模拟每点校相失败")
        patch.setattr(phase, stage, fail)
        ctx.execute(["A13"])
    assert ctx.states["A13"]["status"] == "quality_failed"
    previous = ctx.directory.root
    assert not list(previous.glob("controls/*/scales/*/raw/response_data.npz"))
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in previous.rglob("*.npz")}
    ctx.execute(["A13", "A15"])
    assert ctx.directory.root != previous and ctx.state["calibration_points_completed"] == 1
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in originals.items())
    ctx.finish()
    assert_shutdown(scale_rig)


def test_incomplete_phase_return_is_rejected_before_acquisition():
    from types import SimpleNamespace
    point = SimpleNamespace(state={"raw_format_version": 2, "control_scale": 1., "calibration_scale": 1.,
        "phase_calibration_steps": {}}, params=RFControlScaleParams(arb_waveform_file="ControlA/waveforms/optimal_control_waveform.csv"),
        states={}, save=lambda: None, _execute_step=lambda item: None)
    with pytest.raises(phase.CalibrationQualityError, match="校相未完成"):
        acquisition.calibrate_point(point)
    assert point.state["phase_calibration_status"] == "failed"


def test_modified_source_rejected_before_connecting(tmp_path):
    from types import SimpleNamespace
    path = tmp_path / "wave.csv"
    path.write_bytes(b"original")
    control = SimpleNamespace(envelope={"source_waveform_path": str(path)},
        state={"control_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    path.write_bytes(b"modified")
    with pytest.raises(ValueError, match="源.*改变"):
        acquisition.make_point(None, control, 1.0)


@pytest.mark.parametrize("scales", [[], [1., 1.], [float("nan")], [float("inf")], [[1.]]])
def test_invalid_scale_ranges_rejected(scales):
    params = RFControlScaleParams(control_scales=scales)
    assert any("CONTROL_SCALES" in reason for reason in params.validate_model())


def test_scaled_envelope_includes_mean_and_preserves_calibration():
    params = RFControlScaleParams(xy_ctrl_k_hz_per_v=2.0, xy_ctrl_b_hz=3.0,
        arb_waveform_file="ControlA/waveforms/optimal_control_waveform.csv", control_scale_factors={"ControlA": 1.5})
    original = {"omega_ctrl_Hz": np.array([5., 9.]), "envelope_V": np.array([1., 3.])}
    result = preparation.scaled_envelope(params, original, 1.1)
    np.testing.assert_allclose(result["envelope_V"], (original["omega_ctrl_Hz"] * 1.1 * 1.5 - 3) / 2)
    np.testing.assert_array_equal(original["omega_ctrl_Hz"], [5., 9.])


def test_range_and_legacy_roundtrip_and_explicit_range_wins(tmp_path):
    legacy = RFControlScaleParams.from_external({"CONTROL_SCALES": [2., 0., -1., .5]})
    assert legacy.control_scales == [2., 0., -1., .5]
    assert not any("CONTROL_SCALE" in reason for reason in legacy.validate_model())
    params = RFControlScaleParams.from_external(legacy.to_external() | {
        "CONTROL_SCALE_START": 2., "CONTROL_SCALE_STOP": -2., "CONTROL_SCALE_POINTS": 5,
        "CONTROL_SCALE_FACTORS": {"ControlA": 1.25, "ControlB": .8}})
    assert params.control_scales == [2., 1., 0., -1., -2.]
    saved = params.save_yaml(tmp_path / "params.yaml")
    assert RFControlScaleParams.from_yaml(saved).to_external() == params.to_external()
    assert replace(params, control_scale_points=1).control_scales == [2.]
    from .definition import ADAPTER
    resolved = ADAPTER._resolved({"CONTROL_SCALES": [2., -1.]})
    assert resolved.control_scales == [2., -1.]
    assert resolved.control_scale_start is None
    assert ADAPTER.derive(legacy.to_external())["parameters"]["CONTROL_SCALES"] == [2., 0., -1., .5]


@pytest.mark.parametrize("values", [
    {"CONTROL_SCALE_START": 1.},
    {"CONTROL_SCALE_START": 1., "CONTROL_SCALE_STOP": 1., "CONTROL_SCALE_POINTS": 3},
    {"CONTROL_SCALE_START": 1., "CONTROL_SCALE_STOP": 2., "CONTROL_SCALE_POINTS": 0},
    {"CONTROL_SCALE_START": float("inf"), "CONTROL_SCALE_STOP": 2., "CONTROL_SCALE_POINTS": 2},
    {"CONTROL_SCALE_FACTORS": {"ControlA": float("nan")}},
    {"CONTROL_SCALE_FACTORS": {"ControlA": True}},
    {"CONTROL_SCALE_FACTORS": {"ControlA": "1.2"}},
    {"CONTROL_SCALE_FACTORS": [1.2]},
])
def test_invalid_range_or_compensation_rejected(values):
    try:
        params = RFControlScaleParams.from_external(values)
    except (ValueError, TypeError):
        return
    assert any("倍率" in reason or "CONTROL_SCALE" in reason for reason in params.validate_model())


def test_compensation_is_checked_before_connecting_and_zero_waveform_is_zero(scale_rig):
    scale_rig.events.clear()
    with pytest.raises(ValueError):
        workflow.Workflow(replace(scale_rig.params, control_scale_factors={"ControlB": 1e8}),
                          project_root=scale_rig.tmp_path)
    assert not scale_rig.events
    from ..rf_sensitivity_direct_aw.waveform import synthesize_xy, AW_AMPLITUDE_FLOOR_VPP
    params = RFControlScaleParams(xy_ctrl_b_hz=0)
    original = {"time_s": np.array([0., 1e-6]), "omega_ctrl_Hz": np.array([5., 9.])}
    zero = preparation.scaled_envelope(params, original, 0)
    result = synthesize_xy(params, zero, 0)
    assert result["xy_aw_vpp"] == AW_AMPLITUDE_FLOOR_VPP
    for key in ("omega_ctrl_Hz", "x_waveform_V", "y_waveform_V", "x_normalized", "y_normalized"):
        np.testing.assert_array_equal(result[key], np.zeros(2))
    negative = preparation.scaled_envelope(params, original, -2.)
    np.testing.assert_array_equal(negative["omega_ctrl_Hz"], -2 * original["omega_ctrl_Hz"])


def test_close_scales_get_distinct_directories(scale_rig):
    scale_rig.params = replace(scale_rig.params, control_scales=[1., 1. + 1e-10])
    ctx = prepared(scale_rig)
    try:
        control = ctx.controls["ControlA"]
        points = [acquisition.make_point(ctx, control, scale) for scale in ctx.params.control_scales]
        assert points[0].directory.root != points[1].directory.root
    finally:
        ctx.finish()

