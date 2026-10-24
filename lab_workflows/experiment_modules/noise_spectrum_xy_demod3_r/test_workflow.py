"""真实配置、共享校相与真实批次落盘的假仪器回归；不连接硬件。"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
import shutil
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import daq
from ...common import WorkflowCancelled, load_mapping
from ...steps import routed_signal_generator
from . import workflow, preparation, acquisition, excitation, stages
from .models import NoiseSpectrumXYDemod3RParams

ACQUISITION_CASES = {
    "noise-spectrum-xy-demod3-r": {"mode": "grouped", "cases": {
        "order": ["test_group_order_real_writes_slow_disk_and_last_group"],
        "slow_disk": ["test_group_order_real_writes_slow_disk_and_last_group"],
        "group_change": ["test_group_order_real_writes_slow_disk_and_last_group"],
        "last_group": ["test_group_order_real_writes_slow_disk_and_last_group"],
        "cancel": ["test_cancel_or_configuration_failure_rescues_current_batch"],
        "acquire_failure": ["test_invalid_point_remains_nan_and_does_not_swallow_cancel"],
        "restore_failure": ["test_restore_failure_rescues_data_and_preserves_primary"],
        "save_failure": ["test_save_failure_atomic_previous_and_recovery_matrix"],
        "partial_save": ["test_cancel_or_configuration_failure_rescues_current_batch", "test_restore_failure_rescues_data_and_preserves_primary", "test_save_failure_atomic_previous_and_recovery_matrix"],
        "entrypoints": ["test_full_stepwise_rerun_and_parameter_impacts", "test_real_full_entry_subprocess_writes_raw_and_shutdown"],
    }, "sources": [
        "lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/preparation.py",
        "lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/excitation.py",
        "lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/acquisition.py",
        "lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/workflow.py",
        "lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/session.py",
    ]},
}
ROOT = Path(__file__).resolve().parents[3]


def copy_config(root):
    (root / "params").mkdir(exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)
    # 当前用户选择 MFLI；只修改临时副本以测试已发布的 HF2 接线。
    path = root / "params/mapping.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        data["mapping"][key]["device_id"] = "hf2_dev18246"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def small_params():
    return replace(NoiseSpectrumXYDemod3RParams(), control_freq_points=2, demod3_freq_points=3,
        temp_recovery_batch_points=2, xy_aw_points=1000, system_settle_s=0.001,
        temp_recovery_time_s=0, demod3_acquisition_duration_s=0.01)


class FakeDG:
    def __init__(self, rig, resource):
        self.rig, self.resource = rig, resource
        self.output, self.dc, self.calls = {}, {}, []
        self.shape, self.frequency, self.burst = {}, {}, {}

    def connect(self):
        self.calls.append(("connect",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def setup_dc(self, value, *, channel):
        key = next((key for key, cfg in self.rig.mapping.items()
                    if cfg.get("resource") == self.resource and cfg.get("channel") == channel), "")
        self.calls.append(("dc", channel, value))
        if key == "Temp_Switch":
            self.rig.events.append("on" if value else "off")
            if value and self.rig.restore_error and not self.rig.enabled:
                raise OSError("温控恢复失败")
            self.rig.enabled = bool(value)
        self.dc[channel], self.output[channel] = value, True

    def set_output(self, value, *, channel):
        self.output[channel] = bool(value)
        self.calls.append(("output", channel, bool(value)))

    def setup_square(self, frequency, amplitude, *, channel, **kwargs):
        self.calls.append(("setup_square", channel, frequency))
        self.frequency[channel], self.shape[channel], self.output[channel] = frequency, "SQU", True

    def get_output(self, *, channel):
        return self.output[channel]

    def get_shape(self, *, channel):
        return self.shape.get(channel, "DC")

    def get_frequency(self, *, channel):
        return self.frequency.get(channel, 0)

    def set_burst_state(self, enabled, *, channel):
        self.burst[channel] = bool(enabled)

    def get_burst_state(self, *, channel):
        return self.burst.get(channel, False)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return call


class FakeLockin:
    model, acquisition_mode, device_id, clockbase = "HF2", "auto", "dev18246", 210000000
    capabilities = {"voltage_inputs": 2, "demodulators": 6}

    def __init__(self, rig):
        self.rig, self.nodes = rig, {}

    def connect(self):
        pass

    def disconnect(self):
        self.rig.events.append("lockin-disconnect")

    def sig_in_path(self, index):
        return f"/dev18246/sigins/{index}"

    def osc_path(self, index):
        return f"/dev18246/oscs/{index}"

    def demod_path(self, index):
        return f"/dev18246/demods/{index}"

    def aux_out_path(self, index):
        return f"/dev18246/auxouts/{index}"

    def set_double(self, path, value):
        if path.endswith("/oscs/1/freq"):
            self.rig.events.append("configure")
            if self.rig.config_error and len(self.rig.acquisitions) == 1:
                raise self.rig.config_error
        self.nodes[path] = value

    def set_int(self, path, value):
        self.nodes[path] = value

    def get_double(self, path):
        return float(self.nodes.get(path, 0))

    def get_int(self, path):
        return int(self.nodes.get(path, 0))

    def sync(self):
        pass

    def get_sample(self, path):
        assert not self.rig.enabled
        self.rig.events.append("phase-read")
        return dict(x=[0.0 if self.rig.low_r else 1.0], y=[0.0], timestamp=[0])


def install_rig(root, monkeypatch, params=None, *, progress=False):
    rig = SimpleNamespace(root=root, params=params or small_params(), mapping=load_mapping(root),
        events=[], acquisitions=[], enabled=True, restore_error=False, cancel=False, low_r=False,
        config_error=None, acquire_error=None, acquire_error_index=0, clock_error=False, generators={})
    for key in preparation.SIGNALS:
        resource = rig.mapping[key]["resource"]
        rig.generators.setdefault(resource, FakeDG(rig, resource))
    rig.gs, rig.tec, rig.hfi = Mock(), Mock(), FakeLockin(rig)
    monkeypatch.setattr(preparation, "GS200Instrument", lambda *a: rig.gs)
    monkeypatch.setattr(preparation, "TECInstrument", lambda **kw: rig.tec)
    monkeypatch.setattr(preparation, "create_lockin", lambda cfg: rig.hfi)
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda cfg: rig.generators[cfg["resource"]])
    def clocks(*args, **kwargs):
        if rig.clock_error:
            raise RuntimeError("时钟回读失败")
        return {"verified": True}
    monkeypatch.setattr(preparation, "synchronize_connected_clocks", clocks)
    original = workflow.check_cancelled
    def check():
        original()
        if rig.cancel:
            raise WorkflowCancelled("模拟取消")
    def sleep(seconds):
        rig.events.append("settle" if not rig.enabled else "recovery")
        check()
    monkeypatch.setattr(excitation, "sleep", sleep)
    for module in (workflow, acquisition, excitation):
        monkeypatch.setattr(module, "check_cancelled", check)
    def acquire(instr, config, demod_idx, actual_rate, *args, **kwargs):
        assert not rig.enabled
        kwargs["cancellation_check"]()
        index = len(rig.acquisitions)
        rig.events.append("acquire")
        rig.acquisitions.append(config)
        if rig.acquire_error is not None and index == rig.acquire_error_index:
            raise rig.acquire_error
        return [SimpleNamespace(signal_name="sample.r", values=np.array([index + 1., index + 3.]),
            config_snapshot=dict(acquisition_module="daq", requested_rate=rig.params.demod3_rate_sa_s,
                actual_rate=actual_rate, demod_stream_rate=actual_rate, actual_duration=config.duration, time_source="device"))]
    monkeypatch.setattr(daq, "acquire_data", acquire)
    monkeypatch.setattr(workflow, "find_project_root", lambda: root)
    if not progress:
        for module in (workflow, acquisition):
            monkeypatch.setattr(module, "report_runtime_progress", lambda *a, **kw: None)
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    return install_rig(tmp_path, monkeypatch)


def through(last):
    ids = [s.id for s in stages.ACQUISITION if s.id != stages.FINISH_ID]
    return ids[:ids.index(last) + 1]


def assert_safe(rig):
    assert rig.enabled
    for key in ("X_magnetic_field", "Y_magnetic_field", "X_magnetic_field_AM", "Y_magnetic_field_AM", "Z_magnetic_field"):
        cfg = rig.mapping[key]
        generator = rig.generators[cfg["resource"]]
        assert generator.dc[cfg["channel"]] == 0 and not generator.output[cfg["channel"]]
    rig.tec.set_enable.assert_not_called()
    rig.tec.get_temperature.assert_not_called()


def test_group_order_real_writes_slow_disk_and_last_group(rig, monkeypatch):
    original_open, savez = Path.open, np.savez
    def open_file(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            assert rig.enabled, f"关闭窗口禁止写盘: {path}"
        return original_open(path, mode, *args, **kwargs)
    def slow(stream, *args, **kwargs):
        assert rig.enabled
        rig.events.append("save")
        time.sleep(0.001)
        return savez(stream, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(np, "savez", slow)
    progress = []
    monkeypatch.setattr(acquisition, "report_runtime_progress", lambda *a, **kw: progress.append(kw["estimated_remaining_seconds"]))
    path = workflow.run(rig.params)
    assert progress[0] is None and progress[-1] == 0
    assert any(value is not None and value > 0 for value in progress)
    assert_safe(rig)
    off = rig.events.index("off", rig.events.index("configure"))
    scan_events = rig.events[off:]
    # 两个外层控制点，每个内层 2+1，组首先配置再关温控，组内换频重新等待。
    assert scan_events.count("acquire") == 6 and scan_events.count("save") == 4
    assert scan_events.count("off") == 4
    for i, event in enumerate(scan_events):
        if event == "acquire":
            assert scan_events[i-1] == "settle"
        if event == "save":
            assert "on" in scan_events[:i] and scan_events[i-1] == "on"
    with np.load(path / "raw/demod3_r_mean_matrix.npz") as data:
        np.testing.assert_array_equal(data["r_mean_V"], [[2, 3, 4], [5, 6, 7]])
    config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completed_points"] == 6 and config["raw_format_version"] == 1 and config["schema_version"] == 2
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]
    assert config["initial_temperature_c"] is None
    assert config["temperature_control"]["target_temperature_c"] == rig.params.temperature


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), OSError("换频失败"), KeyboardInterrupt("模拟中断")])
def test_cancel_or_configuration_failure_rescues_current_batch(rig, failure):
    rig.config_error = failure
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(type(failure), match=str(failure)):
        try:
            ctx.execute(through("A11"))
        except BaseException as exc:
            ctx.finish(exc)
            raise
    assert_safe(rig)
    with np.load(ctx.directory.raw / "demod3_r_mean_matrix.npz") as data:
        assert np.isfinite(data["r_mean_V"]).sum() == 1
    assert ctx.state["failure_reason"] == str(failure)


def test_invalid_point_remains_nan_and_does_not_swallow_cancel(rig, capsys):
    rig.acquire_error = OSError("单点读取失败")
    path = workflow.run(rig.params)
    assert "单点读取失败" in capsys.readouterr().out
    with np.load(path / "raw/demod3_r_mean_matrix.npz") as data:
        assert np.isnan(data["r_mean_V"][0, 0]) and np.isfinite(data["r_mean_V"]).sum() == 5
    rig.acquisitions.clear()
    rig.acquire_error = WorkflowCancelled("读取中取消")
    with pytest.raises(WorkflowCancelled, match="读取中取消"):
        workflow.run(rig.params)
    assert len(rig.acquisitions) == 1
    assert_safe(rig)


@pytest.mark.parametrize("primary", [None, OSError("原始换频错误")])
def test_restore_failure_rescues_data_and_preserves_primary(rig, primary):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A10"))
    rig.restore_error = True
    rig.config_error = primary
    with pytest.raises(OSError, match="原始换频错误" if primary else "温控恢复失败"):
        try:
            ctx.execute(["A11"])
        except BaseException as exc:
            ctx.finish(exc)
            raise
    with np.load(ctx.directory.raw / "demod3_r_mean_matrix.npz") as data:
        assert np.isfinite(data["r_mean_V"]).sum() == (1 if primary else 2)
    assert not ctx.state["safety_shutdown"]["completed"] and not ctx.round_sealed
    assert len(rig.acquisitions) == (1 if primary else 2)


def test_save_failure_atomic_previous_and_recovery_matrix(rig, monkeypatch):
    replace_file, calls = acquisition.os.replace, []
    def fail(source, target):
        if target.name == "demod3_r_mean_matrix.npz":
            calls.append(target)
            if len(calls) == 2:
                raise OSError("磁盘原子替换失败")
        return replace_file(source, target)
    monkeypatch.setattr(acquisition.os, "replace", fail)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError, match="磁盘原子替换失败"):
        try:
            ctx.execute(through("A11"))
        except BaseException as exc:
            ctx.finish(exc)
            raise
    assert_safe(rig)
    with np.load(ctx.directory.raw / "demod3_r_mean_matrix.npz") as old, np.load(
            ctx.directory.raw / "demod3_r_mean_matrix_recovered.npz") as rescued:
        assert np.isnan(old["r_mean_V"]).all() and np.isfinite(rescued["r_mean_V"]).sum() == 2
    assert not list(ctx.directory.raw.glob("*.tmp"))


def test_full_stepwise_rerun_and_parameter_impacts(rig):
    path = workflow.run(rig.params)
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A11"])
    ctx.execute(through("A10"))
    before = dict(ctx.states)
    ctx.execute(["A01"])
    assert all(ctx.states[k] == v for k, v in before.items() if k != "A01")
    ctx.execute(["A11"])
    first, config = ctx.directory.root, ctx.directory.config_path.read_bytes()
    raw = (first / "raw/demod3_r_mean_matrix.npz").read_bytes()
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A11"])
    ctx.execute([f"A{i:02d}" for i in range(4, 12)])
    assert ctx.directory.root not in (first, path)
    assert (first / "raw/demod3_r_mean_matrix.npz").read_bytes() == raw
    assert (first / "experiment_config.yaml").read_bytes() == config
    # 草稿只更新未来参数，不重设 Pump 或改写封存目录。
    pump = rig.mapping["Time_sequence"]
    generator = rig.generators[pump["resource"]]
    previous = len(generator.calls)
    ctx.update_parameters({"PUMP_MOD_DUTY": 10., "SYSTEM_SETTLE_S": 0.02})
    expected = set(stages.definition()["parameter_impacts"]["acquisition"]["PUMP_MOD_DUTY"]) | set(
        stages.definition()["parameter_impacts"]["acquisition"]["SYSTEM_SETTLE_S"])
    assert all(ctx.states[key]["status"] == "stale" for key in expected)
    ctx.finish()
    assert not any(c[0] == "setup_pulse" for c in generator.calls[previous:])
    assert_safe(rig)


def test_quality_gate_and_mapping_change(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A08"))
    rig.low_r = True
    with pytest.raises(ValueError, match="未收敛"):
        ctx.execute(["A09"])
    assert ctx.states["A09"]["status"] == "quality_failed" and ctx.state["xy_phase_calibration"]["history"]
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["A10"])
    ctx.finish()
    assert_safe(rig)
    path = rig.root / "params/safety_limits.yaml"
    other = workflow.Workflow(rig.params)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="已改变"):
        other.execute(["A01"])
    other.finish()


def test_clock_rerun_failure_shuts_outputs_and_disconnects(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A10"))
    rig.clock_error = True
    with pytest.raises(RuntimeError, match="时钟"):
        try:
            ctx.execute(["A03"])
        except BaseException as exc:
            ctx.finish(exc)
            raise
    assert_safe(rig)
    assert "lockin-disconnect" in rig.events


@pytest.mark.parametrize("source", ["lockin", "trigger"])
def test_actual_mode_drift_rejected_before_reading(rig, source):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A10"))
    if source == "lockin":
        rig.hfi.set_int(rig.hfi.demod_path(0) + "/oscselect", 1)
    else:
        cfg = rig.mapping["X_magnetic_field_AM"]
        rig.generators[cfg["resource"]].set_output(False, channel=cfg["channel"])
    with pytest.raises(ValueError, match="实际模式"):
        try:
            ctx.execute(["A11"])
        except BaseException as exc:
            ctx.finish(exc)
            raise
    assert not rig.acquisitions
    assert_safe(rig)


def test_missing_aux_safety_and_mfli_are_rejected_without_connection(rig):
    path = rig.root / "params/safety_limits.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["safety_limits"].pop("hf2_dev18246_aux_output_1")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="上下限"):
        workflow.Workflow(rig.params)
    mapping_path = rig.root / "params/mapping.yaml"
    data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        data["mapping"][key]["device_id"] = "mfli_dev30661"
    mapping_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="两路独立主电压"):
        workflow.Workflow(rig.params)
    assert not rig.events


def test_zero_envelope_keeps_outputs_off(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A06"))
    excitation.upload(ctx, 0, 90)
    for key in ("X_magnetic_field", "Y_magnetic_field"):
        cfg = rig.mapping[key]
        assert not rig.generators[cfg["resource"]].output[cfg["channel"]]
    ctx.finish()
    assert_safe(rig)


def test_all_fields_legacy_migration_and_safety_without_hardware(rig):
    flow = stages.definition()
    assert {key for s in flow["acquisition"] for key in s["display_parameters"]} == set(rig.params.to_external(include_hidden=False))
    for changes in ({"SYSTEM_SETTLE_S": 0.2}, {"CONTROL_FREQ_POINTS": 3, "DEMOD3_RATE_SA_S": 2400}):
        ctx = workflow.Workflow(rig.params)
        ctx.states = {s.id: dict(status="completed", error="") for s in stages.ACQUISITION}
        ctx.update_parameters(changes)
        expected = {sid for key in changes for sid in flow["parameter_impacts"]["acquisition"][key]}
        assert {key for key, value in ctx.states.items() if value["status"] == "stale"} == expected
        assert not ctx.devices
    old = NoiseSpectrumXYDemod3RParams.from_external({"DEMOD3_FREQ_SETTLE_TIME_S": 0.8, "XY_AW_OUTPUT_VPP": 5}, schema_version=1)
    assert old.system_settle_s == 0.8 and "XY_AW_OUTPUT_VPP" not in old.to_external()
    explicit = NoiseSpectrumXYDemod3RParams.from_external({"DEMOD3_FREQ_SETTLE_TIME_S": 0.8, "SYSTEM_SETTLE_S": 0.2})
    assert explicit.system_settle_s == 0.2
    from .definition import ADAPTER, preflight_runner
    saved = ADAPTER.defaults()
    assert NoiseSpectrumXYDemod3RParams.from_external(saved.to_external()).to_external() == saved.to_external()
    path = rig.root / "params/experiments/default.yaml"
    saved.save_yaml(path)
    assert NoiseSpectrumXYDemod3RParams.from_yaml(path).to_external() == saved.to_external()
    assert "rf_coil" not in preparation.run_format.REQUIRED_MAPPING_KEYS


@pytest.mark.parametrize("field,value", [("xy_ctrl_k_hz_per_v", 0), ("demod3_rate_sa_s", float("nan")),
    ("xy_trigger_offset", 100), ("auxout_scale", 100), ("demod3_osc_idx", 0)])
def test_invalid_parameters_stop_before_connection(rig, field, value):
    with pytest.raises(ValueError):
        workflow.Workflow(replace(rig.params, **{field: value}))
    assert not rig.events


def test_final_disconnect_failure_not_safe_success(rig):
    ctx = workflow.Workflow(rig.params)
    ctx.execute(through("A11"))
    rig.tec.disconnect.side_effect = OSError("TEC 断开失败")
    with pytest.raises(RuntimeError, match="TEC"):
        ctx.finish()
    assert ctx.closed and ctx.states["A12"]["status"] == "failed"
    assert not ctx.snapshot()["safety_shutdown"]["completed"]


def test_connection_failure_cleans_partial_session(rig):
    cfg = rig.mapping["X_magnetic_field"]
    rig.generators[cfg["resource"]].connect = Mock(side_effect=OSError("模拟连接失败"))
    ctx = workflow.Workflow(rig.params)
    with pytest.raises(OSError, match="模拟连接失败"):
        try:
            ctx.execute(through("A02"))
        except BaseException as exc:
            ctx.finish(exc)
            raise
    rig.gs.disconnect.assert_called_once()
    assert not rig.acquisitions and not ctx.devices


def test_real_full_entry_subprocess_writes_raw_and_shutdown(tmp_path):
    import subprocess
    import sys
    copy_config(tmp_path)
    env = dict(os.environ, DEMOD3_TEST_ROOT=str(tmp_path), DEMOD3_TEST_FULL="1",
        LAB_TYPED_PARAMETERS=json.dumps(small_params().to_external()))
    env.pop("LAB_CANCEL_FILE", None)
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", __name__], cwd=ROOT,
        env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    files = list((tmp_path / "data" / preparation.run_format.DATA_TYPE).glob("*/raw/demod3_r_mean_matrix.npz"))
    assert len(files) == 1
    with np.load(files[0]) as raw:
        np.testing.assert_array_equal(raw["r_mean_V"], [[2, 3, 4], [5, 6, 7]])
    config = yaml.safe_load((files[0].parents[1] / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]


def fake_session_main():
    """测试显式选择的假仪器子进程入口，共用生产 stdin/完整运行入口。"""
    import sys
    from pytest import MonkeyPatch
    from .session import serve
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id", None)
    root = Path(os.environ["DEMOD3_TEST_ROOT"])
    params = NoiseSpectrumXYDemod3RParams.from_external(payload)
    with MonkeyPatch.context() as patch:
        install_rig(root, patch, params, progress=True)
        if os.environ.get("DEMOD3_TEST_FULL") == "1":
            workflow.main()
        else:
            serve(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_session_main()
