"""真实工作点、三轴 DC 控制、R 采集与写盘配假仪器；不访问真实设备。"""

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
from ..mx_y_optimal_control_rf_frequency_response.test_workflow import (
    FakeSignal,
    copy_config as copy_base,
)
from ..mx_y_optimal_control_rf_frequency_response import test_workflow as base_module
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from ..mx_y_rf_sensitivity import acquisition_steps as r_steps
from . import models, preparation, workflow
from .models import MxZOptimalControlXYZBalanceParams
from .scan import iter_serpentine_grid
from .stages import ACQUISITION, FINISH_ID, definition, validate_selection

ACQUISITION_CASES = {
    "mx-z-optimal-control-xyz-balance": {"mode": "single", "cases": {
        "order": ["test_real_order_slow_disk_and_xyz_dc_outputs"],
        "slow_disk": ["test_real_order_slow_disk_and_xyz_dc_outputs"],
        "cancel": ["test_original_failure_preserves_partial_data_and_pre_run_outputs"],
        "acquire_failure": ["test_original_failure_preserves_partial_data_and_pre_run_outputs"],
        "restore_failure": ["test_restore_failure_salvages_current_record_and_keeps_original_error"],
        "save_failure": ["test_save_failure_stops_after_restore"],
        "partial_save": ["test_original_failure_preserves_partial_data_and_pre_run_outputs",
            "test_restore_failure_salvages_current_record_and_keeps_original_error"],
        "entrypoints": ["test_full_staged_and_rerun_preserve_old_files"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/workflow.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/acquisition.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/excitation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/preparation.py",
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
    (source / "parameters/optimal_control_params.csv").write_text(
        "# Output version,v1\nf_rf_Hz,100\n", encoding="utf-8")
    calibration = root / "data/Mx_Z_Field_Calibration/calibration/results/analysis.yaml"
    calibration.parent.mkdir(parents=True)
    calibration.write_text(yaml.safe_dump(dict(experiment_id="mx-z-field-calibration", success=True,
        K_Z_Hz_per_V=1000., f_0V_Hz=12000., linear_fit={"r_squared": .999})), encoding="utf-8")


def parameters(root, **changes):
    return replace(MxZOptimalControlXYZBalanceParams(), **(dict(
        control_results_root=str(root / "theory"),
        control_version="v1",
        z_calibration_source_run="calibration",
        z_aw_output_vpp=.2,
        x_field_start_v=-.02, x_field_stop_v=.02, x_field_points=3,
        y_field_start_v=-.02, y_field_stop_v=.02, y_field_points=3,
        z_field_start_ma=-.01, z_field_stop_ma=.01, z_field_points=2,
        response_duration_s=.016, system_settle_s=.001, temp_switch_on_lag_s=0.) | changes))


class StatefulSignal(FakeSignal):
    """补充 DG 通道快照/恢复所需的完整状态读写。"""

    def __init__(self, resource, rig):
        super().__init__(resource, rig)
        self.offset, self.burst, self.mod = {}, {}, {}
        self.burst_mode, self.burst_ncycles, self.burst_period = {}, {}, {}
        self.burst_delay, self.burst_source, self.burst_slope = {}, {}, {}

    def set_shape(self, value, *, channel):
        self.shape[channel] = str(value)

    def set_dc_voltage(self, value, *, channel):
        self.shape[channel], self.dc[channel] = "DC", float(value)

    def set_offset(self, value, *, channel):
        self.offset[channel] = float(value)

    def set_phase_adjust(self, value, *, channel):
        self.phase[channel] = float(value)

    def set_burst_state(self, value, *, channel):
        self.burst[channel] = bool(value)

    def set_mod_state(self, value, *, channel):
        self.mod[channel] = bool(value)

    def set_burst_mode(self, value, *, channel):
        self.burst_mode[channel] = value

    def set_burst_ncycles(self, value, *, channel):
        self.burst_ncycles[channel] = value

    def set_burst_period(self, value, *, channel):
        self.burst_period[channel] = float(value)

    def set_burst_delay(self, value, *, channel):
        self.burst_delay[channel] = float(value)

    def set_burst_trigger_source(self, value, *, channel):
        self.burst_source[channel] = value

    def set_burst_trigger_slope(self, value, *, channel):
        self.burst_slope[channel] = value

    def get_offset(self, *, channel):
        return self.offset.get(channel, 0.)

    def get_dc_voltage(self, *, channel):
        return self.dc.get(channel, 0.)

    def get_frequency(self, *, channel):
        return self.frequency.get(channel, 100.)

    def get_amplitude(self, *, channel):
        return self.amplitude.get(channel, 1.)

    def get_phase_adjust(self, *, channel):
        return self.phase.get(channel, 0.)

    def get_burst_state(self, *, channel):
        return self.burst.get(channel, False)

    def get_burst_mode(self, *, channel):
        return self.burst_mode.get(channel, "TRIG")

    def get_burst_ncycles(self, *, channel):
        return self.burst_ncycles.get(channel, 1)

    def get_burst_phase(self, *, channel):
        return 0.

    def get_burst_period(self, *, channel):
        return self.burst_period.get(channel, .01)

    def get_burst_delay(self, *, channel):
        return self.burst_delay.get(channel, 0.)

    def get_burst_trigger_source(self, *, channel):
        return self.burst_source.get(channel, "EXT")

    def get_burst_trigger_slope(self, *, channel):
        return self.burst_slope.get(channel, "NEG")

    def get_mod_state(self, *, channel):
        return self.mod.get(channel, False)


def install_rig(root, monkeypatch, params=None):
    monkeypatch.setattr(base_module, "FakeSignal", StatefulSignal)
    rig = install_control(root, monkeypatch, params or parameters(root))
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    monkeypatch.setattr(models, "find_project_root", lambda: root)
    monkeypatch.setattr(r_steps, "_sleep", lambda seconds: rig.events.append("settle"))
    rig.xy_states, rig.z_states, rig.uploaded, rig.bad_attempts = [], [], [], 0
    for device in rig.generators.values():
        original = device.setup_arbitrary

        def upload(*args, _original=original, _device=device, **kwargs):
            rig.uploaded.append((_device.resource, kwargs["channel"],
                np.asarray(args[0]).copy(), kwargs["freq"]))
            return _original(*args, **kwargs)

        monkeypatch.setattr(device, "setup_arbitrary", upload)

    def acquire(hf2, config, **kwargs):
        assert not rig.enabled and config.signal_paths == ["sample.r"]
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(list(config.signal_paths))
        state = output_state(rig)
        rig.xy_states.append(state)
        rig.z_states.append(rig.gs.set_current.call_args[0][0] if rig.gs.set_current.call_args else None)
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        x = state["X_magnetic_field"]["dc"]
        y = state["Y_magnetic_field"]["dc"]
        z_ma = rig.z_states[-1] * 1000.
        count = max(8, config.grid_cols)
        values = (.01 + (x - 0.0) ** 2 + (y - .02) ** 2 + .25 * (z_ma - .01) ** 2
            + 1e-6 * np.sin(np.arange(count) * .7))
        if rig.quality_bad or index < rig.bad_attempts:
            values += np.linspace(-1, 1, count)
        return [DAQResult(signal_name="sample.r", demod_index=kwargs["demod_idx"],
            values=values, time=np.arange(count) / kwargs["actual_rate"])]

    monkeypatch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


def output_state(rig):
    return {key: {"shape": rig.generators[cfg["resource"]].shape.get(cfg["channel"]),
        "output": rig.generators[cfg["resource"]].output.get(cfg["channel"]),
        "dc": rig.generators[cfg["resource"]].dc.get(cfg["channel"], 0.)}
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


def test_real_order_slow_disk_and_xyz_dc_outputs(rig, monkeypatch):
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
    assert rig.enabled and len(rig.captures) == 18
    config = read_config(directory)
    assert config["completion_status"] == "completed" and config["raw_format_version"] == 1
    assert config["safety_shutdown"]["completed"]
    assert config["main_field_restore"]["success"] and config["xy_restore"]["success"]
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["target_temperature_c"] == rig.params.temperature_c
    assert config["measured_grid_minimum"]["acquisition_index"] == 14
    with np.load(directory / "raw/xyz_balance_scan.npz") as aggregate:
        for sequence, zi, xi, yi, z_ma, x, y in iter_serpentine_grid(rig.params):
            assert aggregate["acquisition_order"][zi, xi, yi] == sequence
            with np.load(directory / "raw" / str(aggregate["accepted_file"][zi, xi, yi])) as raw:
                assert raw["x_field_v"] == x and raw["y_field_v"] == y
                assert raw["z_field_ma"] == z_ma
                assert raw["actual_rate_sa_s"] > 0 and raw["r_v"].size >= 8
            state = rig.xy_states[sequence]
            assert state["X_magnetic_field"]["output"] and state["Y_magnetic_field"]["output"]
            assert state["X_magnetic_field"]["shape"] == "DC" and state["Y_magnetic_field"]["shape"] == "DC"
            assert state["X_magnetic_field"]["dc"] == x and state["Y_magnetic_field"]["dc"] == y
    final = output_state(rig)
    assert final["Time_sequence_2"]["output"] and final["Z_magnetic_field"]["output"]
    # X/Y 恢复运行前状态（未配置的正弦且关闭）。
    assert not final["X_magnetic_field"]["output"] and not final["Y_magnetic_field"]["output"]
    assert final["X_magnetic_field"]["shape"] == "SIN" and final["Y_magnetic_field"]["shape"] == "SIN"
    rig.gs.set_current.assert_any_call(-1e-5)
    rig.gs.set_current.assert_any_call(1e-5)
    reference = rig.uploaded[0][2]
    for _, _, values, frequency in rig.uploaded:
        np.testing.assert_array_equal(values, reference)
        assert frequency == 100.


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败"), KeyboardInterrupt()])
def test_original_failure_preserves_partial_data_and_pre_run_outputs(rig, failure):
    rig.fault_index, rig.acquire_error = 2, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(list((directory / "raw").glob("grid_*_attempt_*.npz"))) == 2
    assert not (directory / "raw/xyz_balance_scan.npz").exists()
    final = output_state(rig)
    assert not final["X_magnetic_field"]["output"] and not final["Y_magnetic_field"]["output"]
    assert final["Time_sequence_2"]["output"] and final["Z_magnetic_field"]["output"]
    assert rig.enabled and read_config(directory)["completion_status"] != "completed"
    assert read_config(directory)["main_field_restore"]["success"]


@pytest.mark.parametrize("read_fails", [False, True])
def test_restore_failure_salvages_current_record_and_keeps_original_error(rig, read_fails):
    rig.restore_error, rig.restore_after = True, 2
    if read_fails:
        rig.fault_index, rig.acquire_error = 1, ValueError("原始读数失败")
    with pytest.raises(ValueError if read_fails else OSError):
        workflow.run(rig.params)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert (directory / "raw/grid_Z000_X000_Y000_attempt_00.npz").is_file()
    assert len(rig.captures) == 2
    if not read_fails:
        with np.load(directory / "raw/grid_Z000_X000_Y001_attempt_00.npz") as raw:
            assert raw["r_v"].size >= 8
    assert not read_config(directory)["safety_shutdown"]["completed"]


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
    final = output_state(rig)
    assert not final["X_magnetic_field"]["output"] and not final["Y_magnetic_field"]["output"]


def test_configuration_failure_does_not_turn_outputs_back_on(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A03"))
    failure = OSError("模拟网格配置中断")
    monkeypatch.setattr(workflow._XYZFieldController, "apply", lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises(OSError) as caught:
        ctx.execute(["A04"])
    assert caught.value is failure
    before = output_state(rig)
    assert len(rig.captures) == 0
    assert before["Z_magnetic_field"]["output"] and before["Time_sequence_2"]["output"]
    ctx.finish()
    after = output_state(rig)
    assert after["Time_sequence_2"]["output"] and after["Z_magnetic_field"]["output"]
    assert not after["X_magnetic_field"]["output"] and not after["Y_magnetic_field"]["output"]
    assert read_config(ctx.directory.root)["completion_status"] == "failed"


def test_quality_retry_saves_rejected_and_accepted_attempts_and_blocks_successor(rig):
    rig.bad_attempts = 1
    directory = workflow.run(rig.params)
    for attempt in (0, 1):
        with np.load(directory / f"raw/grid_Z000_X000_Y000_attempt_{attempt:02d}.npz") as raw:
            assert bool(raw["quality_accepted"]) == bool(attempt)
    ctx = workflow.Workflow(rig.params)
    rig.quality_bad = True
    ctx.execute(through("A03"))
    ctx.execute(["A04"])
    assert ctx.states["A04"]["status"] == "quality_failed" and ctx.states["A05"]["status"] != "completed"
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A05"])
    ctx.finish()


def test_full_staged_and_rerun_preserve_old_files(rig):
    full = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    for stage in ACQUISITION:
        if stage.id != FINISH_ID:
            ctx.execute([stage.id])
    with np.load(full / "raw/xyz_balance_scan.npz") as left, np.load(ctx.directory.raw / "xyz_balance_scan.npz") as right:
        for key in left.files:
            np.testing.assert_array_equal(left[key], right[key])
    old, before = ctx.directory.root, hashes(ctx.directory.root)
    ctx.execute(["A03", "A04", "A05"])
    assert ctx.directory.root != old and hashes(old) == before
    ctx.finish()
    assert rig.enabled


def test_preconditions_ownership_shared_impacts_and_empty_finish(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A04"])
    assert not rig.events.count("acquire")
    visible = {f["name"] for f in rig.params.schema()["fields"] if f.get("visible", True)}
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
    after = output_state(rig)
    # 时钟失败后 X/Y 恢复运行前原态；Z 控制与共同触发保持当前状态。
    assert not after["X_magnetic_field"]["output"] and not after["Y_magnetic_field"]["output"]
    assert after["X_magnetic_field"]["shape"] == "SIN" and after["Y_magnetic_field"]["shape"] == "SIN"
    assert after["Time_sequence_2"]["output"] == current["Time_sequence_2"]["output"]
    assert after["Z_magnetic_field"]["output"] == current["Z_magnetic_field"]["output"]
    cfg = rig.mapping["Pump_modulation"]
    assert rig.generators[cfg["resource"]].frequency[cfg["channel"]] == baseline.pump_carrier_frequency_hz
    rig.clock_error = False
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A04"))
    rig.restore_error = True
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        ctx.execute(["A05"])
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
    assert not rig.events.count("acquire")
    raw = rig.root / "copy"
    raw.mkdir()
    with pytest.raises(ValueError, match="冻结内容不一致"):
        preparation.save_source(raw, frozen)
    ctx.finish()
    with pytest.raises(ValueError, match="X_magnetic_field"):
        workflow.Workflow(replace(rig.params, x_field_stop_v=100.))
    assert not rig.events.count("acquire")


def main():
    """仅此测试子进程显式注入假仪器，生产入口不导入测试。"""
    from ...experiment_runtime import load_runtime_params
    from ..noise_spectrum_xy.session import serve
    root = Path(os.environ["MX_Z_XYZ_BALANCE_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    patch = pytest.MonkeyPatch()
    install_rig(root, patch)
    ctx = workflow.Workflow(load_runtime_params(MxZOptimalControlXYZBalanceParams), session_id=session_id)
    if gate_path := os.environ.get("MX_Z_XYZ_BALANCE_TEST_SHUTDOWN_GATE"):
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
