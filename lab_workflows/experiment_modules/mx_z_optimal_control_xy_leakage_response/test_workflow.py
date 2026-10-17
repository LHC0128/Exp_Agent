"""真实工作点、任意波、R 采集与写盘配假仪器；不访问真实设备。"""
from dataclasses import replace
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import os
import sys
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import DAQResult
from ...common import WorkflowCancelled
from ..mx_z_optimal_control_rf_sensitivity.test_workflow import install_rig as install_control
from ..mx_y_optimal_control_rf_frequency_response.test_workflow import copy_config as copy_base
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from . import workflow, preparation, models
from .models import MxZOptimalControlXYLeakageParams
from .scan import iter_serpentine_grid, signed_control_hardware
from .stages import ACQUISITION, FINISH_ID, definition, validate_selection

ACQUISITION_CASES = {
    "mx-z-optimal-control-xy-leakage-response": {"mode": "single", "cases": {
        "order": ["test_real_order_slow_disk_and_signed_waveforms"],
        "slow_disk": ["test_real_order_slow_disk_and_signed_waveforms"],
        "cancel": ["test_original_failure_preserves_partial_data_and_current_outputs"],
        "acquire_failure": ["test_original_failure_preserves_partial_data_and_current_outputs"],
        "restore_failure": ["test_restore_failure_salvages_current_record_and_keeps_original_error"],
        "save_failure": ["test_save_failure_stops_after_restore"],
        "partial_save": ["test_original_failure_preserves_partial_data_and_current_outputs", "test_restore_failure_salvages_current_record_and_keeps_original_error"],
        "entrypoints": ["test_full_staged_and_rerun_preserve_old_files"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/workflow.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/acquisition.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/excitation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/preparation.py",
    ]},
}


def copy_config(root):
    copy_base(root)
    source = root / "theory/v1"
    (source / "waveforms").mkdir(parents=True)
    (source / "parameters").mkdir()
    axis = np.arange(16) / 1600.
    np.savetxt(source / "waveforms/optimal_control_waveform.csv",
        np.column_stack((axis, 50 * np.sin(2 * np.pi * 100 * axis))), delimiter=",")
    (source / "parameters/optimal_control_params.csv").write_text("# Output version,v1\nf_rf_Hz,100\n", encoding="utf-8")
    calibration = root / "data/Mx_Z_Field_Calibration/calibration/results/analysis.yaml"
    calibration.parent.mkdir(parents=True)
    calibration.write_text(yaml.safe_dump(dict(experiment_id="mx-z-field-calibration", success=True,
        K_Z_Hz_per_V=1000., f_0V_Hz=12000., linear_fit={"r_squared": .999})), encoding="utf-8")


def parameters(root, **changes):
    return replace(MxZOptimalControlXYLeakageParams(), **(dict(control_results_root=str(root / "theory"),
        control_version="v1", z_calibration_source_run="calibration", z_aw_output_vpp=.2,
        x_control_amp_start_vpp=-.02, x_control_amp_stop_vpp=.02, x_control_amp_points=3,
        y_control_amp_start_vpp=-.02, y_control_amp_stop_vpp=.02, y_control_amp_points=3,
        response_duration_s=.016, system_settle_s=.001, temp_switch_on_lag_s=0.) | changes))


def install_rig(root, monkeypatch, params=None):
    rig = install_control(root, monkeypatch, params or parameters(root))
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(models, "find_project_root", lambda: root)
    rig.commands, rig.xy_states, rig.uploaded, rig.bad_attempts = [], [], [], 0
    for device in rig.generators.values():
        for name in ("setup_arbitrary", "setup_dc", "set_output", "set_amplitude", "set_burst_phase", "set_burst_state",
                     "set_burst_mode", "set_burst_trigger_source", "set_burst_trigger_slope"):
            original = getattr(device, name)
            def command(*args, _original=original, _device=device, _name=name, **kwargs):
                channel = kwargs.get("channel", 1)
                rig.commands.append((_device.resource, _name, channel, args))
                if _name == "setup_arbitrary":
                    rig.uploaded.append((_device.resource, channel, np.asarray(args[0]).copy(), kwargs["freq"]))
                return _original(*args, **kwargs)
            monkeypatch.setattr(device, name, command)
    def acquire(hf2, config, **kwargs):
        assert not rig.enabled and config.signal_paths == ["sample.r"]
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(list(config.signal_paths))
        rig.xy_states.append(output_state(rig))
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        signed = []
        for key in ("X_magnetic_field", "Y_magnetic_field"):
            source = rig.mapping[key]
            device, channel = rig.generators[source["resource"]], source["channel"]
            signed.append(0. if not device.output.get(channel) else
                device.amplitude[channel] * np.cos(np.deg2rad(device.phase[channel] - rig.params.control_burst_phase_deg)))
        count = max(8, config.grid_cols)
        values = .01 + signed[0] ** 2 + signed[1] ** 2 + 1e-6 * np.sin(np.arange(count) * .7)
        if rig.quality_bad or index < rig.bad_attempts:
            values += np.linspace(-1, 1, count)
        return [DAQResult(signal_name="sample.r", demod_index=kwargs["demod_idx"], values=values,
            time=np.arange(count) / kwargs["actual_rate"])]
    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


def output_state(rig):
    return {key: {"shape": rig.generators[cfg["resource"]].shape.get(cfg["channel"]),
        "output": rig.generators[cfg["resource"]].output.get(cfg["channel"]),
        "amplitude": rig.generators[cfg["resource"]].amplitude.get(cfg["channel"]),
        "phase": rig.generators[cfg["resource"]].phase.get(cfg["channel"])}
        for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "Time_sequence_2")
        for cfg in [rig.mapping[key]]}


def read_config(directory):
    return yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))


def hashes(directory):
    return {p.relative_to(directory).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in directory.rglob("*") if p.is_file()}


def through(last):
    ids = [s.id for s in ACQUISITION if s.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def test_real_order_slow_disk_and_signed_waveforms(rig, monkeypatch):
    save, write = np.savez, Path.write_text
    def slow(path, **data):
        assert rig.enabled
        if "r_v" in data:
            rig.events.append("save")
            time.sleep(.002)
        save(path, **data)
    def text(path, *args, **kwargs):
        assert rig.enabled
        return write(path, *args, **kwargs)
    monkeypatch.setattr(np, "savez", slow)
    monkeypatch.setattr(Path, "write_text", text)
    directory = workflow.run(rig.params)
    for i, event in enumerate(rig.events):
        if event == "acquire":
            assert rig.events[i - 2:i] == ["off", "settle"]
            assert rig.events[i + 1:i + 3] == ["on", "save"]
    assert rig.enabled and len(rig.captures) == 9
    config = read_config(directory)
    assert config["completion_status"] == "completed" and config["raw_format_version"] == 1
    assert config["safety_shutdown"]["completed"] and config["main_field_restore"]["success"]
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["target_temperature_c"] == rig.params.temperature_c
    assert config["temperature_control"]["controlled_by_experiment"] is True
    assert config["measured_grid_minimum"]["acquisition_index"] == 4
    assert config["best_point_applied"] and not config["measured_grid_minimum"]["combined_state_remeasured"]
    with np.load(directory / "raw/xy_leakage_scan.npz") as aggregate:
        for sequence, xi, yi, x, y in iter_serpentine_grid(rig.params):
            assert aggregate["acquisition_order"][xi, yi] == sequence
            with np.load(directory / "raw" / str(aggregate["accepted_file"][xi, yi])) as raw:
                assert raw["x_signed_amplitude_vpp"] == x and raw["y_signed_amplitude_vpp"] == y
                assert raw["actual_rate_sa_s"] > 0 and raw["r_v"].size >= 8
            state = rig.xy_states[sequence]
            for key, value in (("X_magnetic_field", x), ("Y_magnetic_field", y)):
                amplitude, phase, output = signed_control_hardware(value, rig.params.control_burst_phase_deg)
                assert state[key]["output"] == output
                if output:
                    assert state[key]["shape"] == "USER" and state[key]["amplitude"] == amplitude
                    assert state[key]["phase"] == phase
                else:
                    assert state[key]["shape"] == "DC"
    reference = rig.uploaded[0][2]
    for _, _, values, frequency in rig.uploaded:
        np.testing.assert_array_equal(values, reference)
        assert frequency == 100.
    final = output_state(rig)
    assert final["Time_sequence_2"]["output"] and final["Z_magnetic_field"]["output"]
    assert not final["X_magnetic_field"]["output"] and not final["Y_magnetic_field"]["output"]
    rig.gs.set_current.assert_any_call(.001)
    trigger = rig.mapping["Time_sequence_2"]
    assert rig.commands[-1][0] != trigger["resource"]


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败"), KeyboardInterrupt()])
def test_original_failure_preserves_partial_data_and_current_outputs(rig, failure):
    rig.fault_index, rig.acquire_error = 2, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(list((directory / "raw").glob("grid_*_attempt_*.npz"))) == 2
    assert not (directory / "raw/xy_leakage_scan.npz").exists()
    assert output_state(rig) == rig.xy_states[-1]
    assert rig.enabled and read_config(directory)["completion_status"] != "completed"
    assert read_config(directory)["main_field_restore"]["success"]


@pytest.mark.parametrize("read_fails", [False, True])
def test_restore_failure_salvages_current_record_and_keeps_original_error(rig, read_fails):
    rig.restore_error, rig.restore_after = True, 2
    if read_fails:
        rig.fault_index, rig.acquire_error = 1, ValueError("原始读数失败")
    with pytest.raises(ValueError if read_fails else OSError) as caught:
        workflow.run(rig.params)
    if read_fails:
        assert caught.value is rig.acquire_error
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert (directory / "raw/grid_X000_Y000_attempt_00.npz").is_file()
    assert len(rig.captures) == 2
    if not read_fails:
        with np.load(directory / "raw/grid_X000_Y001_attempt_00.npz") as raw:
            assert raw["r_v"].size >= 8
    assert not read_config(directory)["safety_shutdown"]["completed"]
    assert output_state(rig) == rig.xy_states[-1]


def test_save_failure_stops_after_restore(rig, monkeypatch):
    save = np.savez
    def fail(path, **data):
        assert rig.enabled
        if "r_v" in data:
            raise OSError("模拟磁盘故障")
        save(path, **data)
    monkeypatch.setattr(np, "savez", fail)
    with pytest.raises(OSError, match="磁盘故障"):
        workflow.run(rig.params)
    assert rig.enabled and len(rig.captures) == 1
    assert output_state(rig) == rig.xy_states[-1]


def test_configuration_failure_does_not_turn_trigger_back_on(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A03"))
    failure = OSError("模拟任意波配置中断")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(workflow._XYWaveformController, "_configure_channel", fail)
    with pytest.raises(OSError) as caught:
        ctx.execute(["A04"])
    assert caught.value is failure
    before = output_state(rig)
    assert not before["Time_sequence_2"]["output"] and len(rig.captures) == 0
    ctx.finish()
    assert output_state(rig) == before and rig.enabled
    assert read_config(ctx.directory.root)["completion_status"] == "failed"


def test_quality_retry_saves_rejected_and_accepted_attempts_and_blocks_successor(rig):
    rig.bad_attempts = 1
    directory = workflow.run(rig.params)
    for attempt in (0, 1):
        with np.load(directory / f"raw/grid_X000_Y000_attempt_{attempt:02d}.npz") as raw:
            assert bool(raw["quality_accepted"]) == bool(attempt)
    ctx = workflow.Workflow(rig.params)
    rig.quality_bad = True
    ctx.execute(through("A05"))
    assert ctx.states["A04"]["status"] == "quality_failed" and ctx.states["A05"]["status"] != "completed"
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A05"])
    before = output_state(rig)
    ctx.finish()
    assert output_state(rig) == before


def test_full_staged_and_rerun_preserve_old_files(rig):
    full = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    for stage in ACQUISITION:
        if stage.id != FINISH_ID:
            ctx.execute([stage.id])
    with np.load(full / "raw/xy_leakage_scan.npz") as left, np.load(ctx.directory.raw / "xy_leakage_scan.npz") as right:
        for key in left.files:
            np.testing.assert_array_equal(left[key], right[key])
    old, before = ctx.directory.root, hashes(ctx.directory.root)
    ctx.execute(["A03", "A04", "A05", "A06"])
    assert ctx.directory.root != old and hashes(old) == before
    ctx.finish()
    assert rig.enabled


def test_preconditions_ownership_shared_impacts_and_empty_finish(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A04"])
    assert not rig.events
    visible = {f["name"] for f in rig.params.schema()["fields"]}
    owned = {k for col in ("acquisition", "analysis") for s in definition()[col] for k in s["display_parameters"]}
    assert visible <= owned
    ctx.execute(through("A04"))
    states = deepcopy(ctx.states)
    ctx.recheck()
    assert ctx.states == states
    ctx.update_parameters({"SYSTEM_SETTLE_S": .002, "CONTROL_BURST_PHASE_DEG": 10.})
    impacts = definition()["parameter_impacts"]["acquisition"]
    expected = set(impacts["SYSTEM_SETTLE_S"]) | set(impacts["CONTROL_BURST_PHASE_DEG"])
    assert {k for k, v in ctx.states.items() if v["status"] == "stale"} == {
        key for key in expected if states[key]["status"] == "completed"}
    ctx.finish()
    empty = workflow.Workflow(rig.params)
    empty.execute([FINISH_ID])
    assert empty.closed and empty.directory is None


def test_unexecuted_draft_clock_failure_and_safety_failure_are_not_success(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A04"))
    baseline = ctx.shutdown_parameters
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "RESPONSE_RATE_SA_S": 2000.})
    rig.clock_error = True
    with pytest.raises(RuntimeError):
        ctx.execute(["A03"])
    assert ctx.shutdown_parameters is baseline
    current = output_state(rig)
    ctx.finish()
    assert output_state(rig) == current
    cfg = rig.mapping["Pump_modulation"]
    assert rig.generators[cfg["resource"]].frequency[cfg["channel"]] == baseline.pump_carrier_frequency_hz
    rig.clock_error = False
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A05"))
    rig.restore_error = True
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.execute(["A06"])
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.finish()
    assert read_config(ctx.directory.root)["completion_status"] == "failed"


def test_source_drift_copy_hash_and_envelope_reject_before_outputs(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(["A01"])
    frozen = ctx.sources
    path = rig.root / "theory/v1/parameters/optimal_control_params.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="来源文件已改变"):
        ctx.execute(["A02"])
    assert not rig.events
    raw = rig.root / "copy"
    raw.mkdir()
    with pytest.raises(ValueError, match="冻结内容不一致"):
        preparation.save_source(raw, frozen)
    ctx.finish()
    with pytest.raises(ValueError, match="X_magnetic_field"):
        workflow.Workflow(replace(rig.params, x_control_amp_stop_vpp=100.))
    assert not rig.events


def test_zero_first_point_after_rerun_does_not_assume_dc_mode(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A04"))
    ctx.update_parameters({"X_CONTROL_AMP_START_VPP": 0., "Y_CONTROL_AMP_START_VPP": 0.})
    ctx.execute(["A04"])
    first = rig.xy_states[9]
    assert first["X_magnetic_field"]["shape"] == first["Y_magnetic_field"]["shape"] == "DC"
    assert not first["X_magnetic_field"]["output"] and not first["Y_magnetic_field"]["output"]
    ctx.finish()


def main():
    """仅此测试子进程显式注入假仪器，生产入口不导入测试。"""
    from ...experiment_runtime import load_runtime_params
    from ..noise_spectrum_xy.session import serve
    root = Path(os.environ["MX_Z_XY_LEAKAGE_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    patch = pytest.MonkeyPatch()
    install_rig(root, patch)
    ctx = workflow.Workflow(load_runtime_params(MxZOptimalControlXYLeakageParams), session_id=session_id)
    if gate_path := os.environ.get("MX_Z_XY_LEAKAGE_TEST_SHUTDOWN_GATE"):
        gate, shutdown = Path(gate_path), ctx._shutdown
        def blocked_shutdown(**kwargs):
            gate.with_suffix(".started").touch()
            deadline = time.monotonic() + 25
            while not gate.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("测试未释放收尾")
                time.sleep(.02)
            errors = shutdown(**kwargs)
            gate.with_suffix(".finished").write_text(json.dumps(dict(
                pump_frequency_hz=ctx.shutdown_parameters.pump_carrier_frequency_hz,
                response_rate_sa_s=ctx.response_parameters.response_rate_sa_s, errors=errors)), encoding="utf-8")
            return errors
        ctx._shutdown = blocked_shutdown
    try:
        serve(ctx, sys.stdin, stages=ACQUISITION, validate=validate_selection, finish_id=FINISH_ID)
    finally:
        patch.undo()


if __name__ == "__main__":
    main()
