"""假仪器运行真实 offset 配置、同步 R/X/Y 采集、文件保存与原态恢复。"""
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
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
from ..mx_y_rf_sensitivity import acquisition as r_acquisition
from ..mx_z_optimal_control_xyz_balance.test_workflow import (
    copy_config, install_rig as install_xyz_rig, output_state, read_config, hashes)
from . import models, workflow, preparation
from .models import MxZOptimalControlDG4000BiasXYZBalanceParams
from .scan import iter_serpentine_grid
from .stages import ACQUISITION, FINISH_ID, definition, validate_selection

ACQUISITION_CASES = {
    "mx-z-optimal-control-dg4000-bias-xyz-balance": {"mode": "single", "cases": {
        "order": ["test_real_rxy_order_slow_disk_offset_only_and_original_states"],
        "slow_disk": ["test_real_rxy_order_slow_disk_offset_only_and_original_states"],
        "cancel": ["test_original_failure_preserves_read_data_and_original_states"],
        "acquire_failure": ["test_original_failure_preserves_read_data_and_original_states"],
        "restore_failure": ["test_temperature_restore_failure_salvages_current_frame"],
        "save_failure": ["test_save_failure_occurs_after_restore_and_stops_next_point"],
        "partial_save": ["test_original_failure_preserves_read_data_and_original_states",
            "test_temperature_restore_failure_salvages_current_frame"],
        "entrypoints": ["test_full_staged_rerun_and_parameters_keep_old_run"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/workflow.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/preparation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/excitation.py",
        "lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/acquisition.py",
    ]},
}


def parameters(root, **changes):
    return replace(MxZOptimalControlDG4000BiasXYZBalanceParams(), **(dict(
        control_results_root=str(root / "theory"), control_version="v1",
        control_waveform_source="corrected_run", corrected_control_source_run="frozen",
        z_calibration_source_run="calibration", confirm_gs200_disconnected=True,
        z_aw_output_vpp=.2, x_field_start_v=-.02, x_field_stop_v=.02, x_field_points=3,
        y_field_start_v=-.02, y_field_stop_v=.02, y_field_points=3,
        z_field_start_ma=-.01, z_field_stop_ma=.01, z_field_points=2,
        response_duration_s=.016, system_settle_s=.001, temp_switch_on_lag_s=0.) | changes))


def install_rig(root, patch, params=None):
    rig = install_xyz_rig(root, patch, params or parameters(root))
    patch.setattr(workflow, "find_project_root", lambda: root)
    patch.setattr(models, "find_project_root", lambda: root)
    from ..mx_z_field_calibration import preparation as connections
    rig.tec = Mock()
    patch.setattr(connections, "TECInstrument", lambda **kwargs: rig.tec)
    rig.bad_attempts = 0
    rig.frames = []
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "Time_sequence_2"):
        cfg = rig.mapping[key]
        device = rig.generators[cfg["resource"]]
        device.setup_dc(.003, channel=cfg["channel"])
        device.set_output(False, channel=cfg["channel"])
    rig.original = {key: output_state(rig)[key] for key in (
        "X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "Time_sequence_2")}

    def acquire(hf2, config, **kwargs):
        assert not rig.enabled
        assert set(config.signal_paths) == {"sample.r", "sample.x", "sample.y"}
        rig.events.append("acquire")
        index = len(rig.captures)
        rig.captures.append(list(config.signal_paths))
        state = output_state(rig)
        z = rig.mapping["Z_magnetic_field"]
        offset = rig.generators[z["resource"]].offset.get(z["channel"], 0.)
        rig.frames.append((state, offset))
        if index == rig.fault_index:
            raise rig.acquire_error or WorkflowCancelled("模拟取消")
        kwargs["cancellation_check"]()
        count = max(8, config.grid_cols)
        noise = 1e-6 * np.sin(np.arange(count) * .7)
        if rig.quality_bad or index < rig.bad_attempts:
            noise += np.linspace(-1, 1, count)
        x, y = state["X_magnetic_field"]["dc"], state["Y_magnetic_field"]["dc"]
        values = {"sample.x": .01 + x + .1 * offset + noise,
                  "sample.y": .02 + y + .2 * offset + noise}
        values["sample.r"] = np.hypot(values["sample.x"], values["sample.y"])
        return [DAQResult(signal_name=name, demod_index=kwargs["demod_idx"],
            values=values[name], time=np.arange(count) / kwargs["actual_rate"])
            for name in config.signal_paths]

    patch.setattr(r_acquisition.daq, "acquire_data", acquire)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [s.id for s in ACQUISITION if s.id != FINISH_ID]
    return ids[:ids.index(last) + 1]


def assert_restored(rig):
    assert rig.enabled
    assert output_state(rig) == rig.original
    rig.gs.connect.assert_not_called()
    rig.gs.set_current.assert_not_called()
    rig.tec.set_enable.assert_not_called()


@pytest.mark.parametrize("mode", ["theory", "corrected_run"])
def test_real_rxy_order_slow_disk_offset_only_and_original_states(rig, monkeypatch, mode):
    rig.params = replace(rig.params, control_waveform_source=mode)
    save, write = np.savez, Path.write_text

    def slow(path, **data):
        assert rig.enabled
        if "r_v" in data:
            rig.events.append("save")
            time.sleep(.002)
        return save(path, **data)

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
    assert len(rig.captures) == 18
    expected = list(iter_serpentine_grid(rig.params))
    for (state, offset), (_, _, _, _, bias, x, y) in zip(rig.frames, expected):
        assert state["X_magnetic_field"]["output"] and state["Y_magnetic_field"]["output"]
        assert state["X_magnetic_field"]["dc"] == x and state["Y_magnetic_field"]["dc"] == y
        assert offset == pytest.approx(bias)
    config = read_config(directory)
    assert config["completion_status"] == "completed" and config["raw_format_version"] == 1
    assert config["safety_shutdown"]["completed"]
    assert all(config[k]["success"] for k in ("xyz_restore", "z_channel_restore", "trigger_channel_restore"))
    assert config["temperature_control"]["actual_temperature_c"] is None
    assert config["temperature_control"]["target_temperature_c"] == rig.params.temperature_c
    # 只在工作点上传 Z；网格点包括零值，只改 offset，不重新上传或退出 Burst。
    assert len(rig.uploaded) == 1
    with np.load(directory / "raw/xyz_balance_scan.npz", allow_pickle=False) as data:
        assert data["r_mean_v"].shape == (2, 3, 3)
        assert np.isfinite(data["x_mean_v"]).all() and np.isfinite(data["y_mean_v"]).all()
    assert_restored(rig)


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("模拟读取失败"), KeyboardInterrupt()])
def test_original_failure_preserves_read_data_and_original_states(rig, failure):
    rig.fault_index, rig.acquire_error = 2, failure
    with pytest.raises(type(failure)) as caught:
        workflow.run(rig.params)
    assert caught.value is failure
    assert_restored(rig)
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(list((directory / "raw").glob("grid_*_attempt_*.npz"))) == 2
    assert not (directory / "raw/xyz_balance_scan.npz").exists()
    assert read_config(directory)["completion_status"] != "completed"


@pytest.mark.parametrize("read_fails", [False, True])
def test_temperature_restore_failure_salvages_current_frame(rig, read_fails):
    rig.restore_error = True
    if read_fails:
        rig.fault_index, rig.acquire_error = 0, OSError("原始读取故障")
    with pytest.raises(Exception) as caught:
        workflow.run(rig.params)
    if read_fails:
        assert caught.value is rig.acquire_error
    directory = next((rig.root / "data" / workflow.DATA_TYPE).iterdir())
    assert len(rig.captures) == 1
    assert len(list((directory / "raw").glob("grid_*_attempt_*.npz"))) == (0 if read_fails else 1)
    config = read_config(directory)
    assert config["completion_status"] != "completed" and not config["safety_shutdown"]["completed"]


def test_save_failure_occurs_after_restore_and_stops_next_point(rig, monkeypatch):
    original = np.savez
    failure = OSError("模拟慢磁盘写入失败")

    def fail(path, **data):
        assert rig.enabled
        if "r_v" in data:
            raise failure
        return original(path, **data)

    monkeypatch.setattr(np, "savez", fail)
    with pytest.raises(OSError) as caught:
        workflow.run(rig.params)
    assert caught.value is failure and len(rig.captures) == 1
    assert_restored(rig)


def test_complex_noise_retry_not_r_std_and_quality_blocks_successor(rig):
    ctx = workflow.Workflow(rig.params)
    try:
        rig.quality_bad = True
        ctx.execute(through("A04"))
        assert ctx.states["A04"]["status"] == "quality_failed"
        assert len(rig.captures) == rig.params.r_point_max_attempts
        assert len(list(ctx.directory.raw.glob("*attempt*.npz"))) == rig.params.r_point_max_attempts
        with pytest.raises(ValueError, match="前置"):
            ctx.execute(["A05"])
        rig.quality_bad = False
        ctx.execute(["A04", "A05"])
        assert ctx.round_sealed
    finally:
        ctx.finish()
    assert_restored(rig)


def test_full_staged_rerun_and_parameters_keep_old_run(rig):
    full = workflow.run(rig.params)
    with np.load(full / "raw/xyz_balance_scan.npz", allow_pickle=False) as scan:
        expected = {key: scan[key].copy() for key in scan.files}
    ctx = workflow.Workflow(rig.params)
    try:
        for step in through("A05"):
            ctx.execute([step])
        first = ctx.directory.root
        with np.load(first / "raw/xyz_balance_scan.npz", allow_pickle=False) as scan:
            assert set(scan.files) == set(expected)
            for key, value in expected.items():
                np.testing.assert_array_equal(scan[key], value)
        before = hashes(first)
        ctx.update_parameters({"Z_BIAS_STOP_V": .02})
        assert ctx.states["A04"]["status"] != "completed"
        with pytest.raises(ValueError, match="前置"):
            ctx.execute(["A04"])
        ctx.execute(["A03", "A04", "A05"])
        assert ctx.directory.root != first and hashes(first) == before
        assert read_config(first)["parameters"]["Z_BIAS_STOP_V"] == .01
    finally:
        ctx.finish()
    assert_restored(rig)


def test_preconditions_ownership_drafts_and_finish_ignore_new_parameters(rig):
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["A04"])
    assert not ctx.devices
    visible = {f["name"] for f in rig.params.schema(rig.root)["fields"]}
    displayed = {name for s in definition()["acquisition"] for name in s["display_parameters"]}
    assert visible <= displayed and "BZ_GAMMA_HZ_PER_NT" not in displayed
    ctx.execute(through("A04"))
    ctx.update_parameters({"PUMP_CARRIER_FREQUENCY_HZ": 99e6, "TRIGGER_AMPLITUDE_VPP": 4.})
    ctx.finish()
    assert ctx.shutdown_parameters.pump_carrier_frequency_hz == rig.params.pump_carrier_frequency_hz
    assert_restored(rig)


@pytest.mark.parametrize("channel", ["z_field", "trigger", "x_field"])
def test_failed_channel_restore_never_seals_success(rig, monkeypatch, channel):
    original = workflow.restore_dg_channel
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A04"))
    target = ctx.devices["xy_field" if channel == "x_field" else "z_field"]

    def fail(device, index, state):
        if device is target and index == ctx.channels[channel]:
            raise OSError("模拟通道恢复失败")
        return original(device, index, state)

    monkeypatch.setattr(workflow, "restore_dg_channel", fail)
    try:
        with pytest.raises(RuntimeError, match="恢复失败|收尾失败") as caught:
            ctx.execute(["A05"])
    finally:
        ctx.finish(failure=RuntimeError("模拟通道恢复失败"))
    assert "恢复失败" in str(caught.value)
    assert read_config(ctx.directory.root)["completion_status"] != "completed"


def test_sources_hash_drift_calibration_and_actual_frozen_envelope(rig, monkeypatch):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(["A01"])
    source = rig.root / "data/Mx_Z_Field_Calibration/calibration/results/analysis.yaml"
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="来源文件已改变"):
        ctx.execute(["A02"])
    assert not ctx.devices
    ctx.finish()
    assert parameters(rig.root, confirm_gs200_disconnected=False).validate(rig.root)
    # corrected_run 下 Vpp 参数不用于下发；真实包络由冻结文件决定。
    ctx = workflow.Workflow(parameters(rig.root, z_aw_output_vpp=100.))
    try:
        ctx.execute(through("A05"))
        assert ctx.round_sealed
        assert (ctx.directory.raw / "source_z_calibration_analysis.yaml").is_file()
    finally:
        ctx.finish()


def test_clock_and_copy_hash_fail_before_capture(rig, monkeypatch):
    rig.clock_error = True
    with pytest.raises(RuntimeError, match="时钟"):
        workflow.run(rig.params)
    assert not rig.captures
    assert_restored(rig)
    rig.clock_error = False
    original = preparation.save_control_source

    def corrupt(raw, source):
        files = original(raw, source)
        # 复用的复制函数本身检查来源哈希；这里模拟独立 K_Z 复制失败。
        calibration = source[2]
        path = calibration.analysis_path
        path.write_bytes(path.read_bytes() + b"\n")
        return files

    monkeypatch.setattr(preparation, "save_control_source", corrupt)
    ctx = workflow.Workflow(rig.params)
    try:
        with pytest.raises(ValueError, match="冻结内容不一致"):
            ctx.execute(through("A02"))
        assert not ctx.devices
    finally:
        ctx.finish()


def test_non_dc_burst_and_trigger_snapshot_fields_are_restored(rig):
    from ...steps.dg_channel_state import snapshot_dg_channel
    z = rig.mapping["Z_magnetic_field"]
    device = rig.generators[z["resource"]]
    device.setup_sine(137., .04, offset=.002, phase=17., channel=1)
    device.set_burst_state(True, channel=1)
    device.set_mod_state(True, channel=1)
    device.set_output(True, channel=1)
    before = snapshot_dg_channel(device, 1)
    workflow.run(rig.params)
    assert snapshot_dg_channel(device, 1) == before


def main():
    """只允许测试子进程显式注入假仪器；生产入口不引用测试。"""
    from ...experiment_runtime import load_runtime_params
    from ..noise_spectrum_xy.session import serve
    root = Path(os.environ["MX_Z_DG_BIAS_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    patch = pytest.MonkeyPatch()
    install_rig(root, patch)
    ctx = workflow.Workflow(load_runtime_params(MxZOptimalControlDG4000BiasXYZBalanceParams), session_id=session_id)
    if gate_path := os.environ.get("MX_Z_DG_BIAS_TEST_SHUTDOWN_GATE"):
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
