"""最优控制工作点、共享 Y RF 输出与微型调用边界契约。"""

from functools import partial
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest

from . import mx_z_optimal_control as workpoint

# 只关联真实执行的微型采集分支；慢盘、完整入口及科学长测继续使用原声明。
ACQUISITION_CASES = {
    "mx-z-optimal-control-rf-sensitivity": {"mode": "single", "cases": {
        "order": ["test_y_rf_root_two_points_keep_gate_and_partial_data"],
        "cancel": ["test_y_rf_root_two_points_keep_gate_and_partial_data"],
        "acquire_failure": ["test_y_rf_root_two_points_keep_gate_and_partial_data"],
        "restore_failure": ["test_y_rf_root_two_points_keep_gate_and_partial_data"],
        "save_failure": ["test_y_rf_root_two_points_keep_gate_and_partial_data"],
        "partial_save": ["test_y_rf_root_two_points_keep_gate_and_partial_data"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/acquisition.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/acquisition_steps.py",
        "lab_workflows/steps/mx_z_optimal_control.py",
    ]},
    "mx-y-optimal-control-rf-frequency-response": {"mode": "single", "cases": {
        "order": ["test_y_rf_frequency_callers_use_two_point_real_writers"],
    }, "sources": [
        "lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/acquisition.py",
        "lab_workflows/experiment_modules/mx_y_rf_sensitivity/acquisition_steps.py",
        "lab_workflows/steps/mx_z_optimal_control.py",
    ]},
}


@pytest.mark.parametrize("module", [
    "mx_y_optimal_control_rf_frequency_response",
    "mx_z_optimal_control_rf_sensitivity",
    "mx_z_optimal_control_xy_leakage_response",
    "mx_z_optimal_control_xy_rf_phase_response",
    "mx_z_optimal_control_xyz_balance",
    "mx_z_optimal_control_dg4000_bias_xyz_balance",
])
def test_typed_callers_only_set_ui_temperature(monkeypatch, module):
    params = import_module(f"lab_workflows.experiment_modules.{module}.definition").ADAPTER.params_type()
    tec = SimpleNamespace(set_target_temperature=Mock())
    devices = {name: Mock() for name in ("z_field", "laser", "pump_rf", "temp_switch", "hf2")}
    devices["tec"] = tec
    devices["hf2"].get_double.return_value = 0.0
    channels = dict(trigger=2, z_field=1, pump_laser=1, probe_laser=2,
                    pump_carrier=1, pump_gate=2, temp_switch=2)
    monkeypatch.setattr(workpoint, "configure_optimal_control_trigger", Mock())
    monkeypatch.setattr(workpoint, "configure_z_optimal_control_output", Mock())
    monkeypatch.setattr(workpoint.demod, "configure_signal_input", Mock())
    monkeypatch.setattr(workpoint.demod, "configure_oscillator", Mock())
    monkeypatch.setattr(workpoint.demod, "configure_demodulator", Mock(return_value=1000.0))
    waiter = Mock(side_effect=AssertionError("普通气室采集不得调用 TEC 判稳"))

    result = workpoint.configure_mx_z_optimal_control_workpoint(
        params, devices, channels, {}, None, None, demod_frequency_hz=1000.0,
        cancellation=None, xy_output_configurator=Mock(),
        reference_clock_configurator=Mock(return_value={}), pump_gate_setter=Mock(),
        temperature_stability_waiter=waiter, configure_main_field_output=False,
    )

    tec.set_target_temperature.assert_called_once_with(params.temperature_c, channel=1)
    waiter.assert_not_called()
    assert result[0] == 1000.0
    assert result[1] is None

@pytest.fixture
def y_rf(monkeypatch, tmp_path):
    """只替换仪器和等待；校验、采集窗口、暂存及小文件写入保持真实。"""
    from .. import common
    from .run_directory import RunDirectory
    from ..experiment_modules.mx_y_rf_sensitivity import acquisition_steps as points

    monkeypatch.delenv("LAB_CANCEL_FILE", raising=False)
    limits = {key: {"min": -1.0, "max": 1.0} for key in ("X_magnetic_field", "Y_magnetic_field")}
    limits["rf_coil"] = {"min": 0.0, "max": 2.0}
    monkeypatch.setattr(common, "load_safety_limits", lambda: limits)
    raw, results = tmp_path / "raw", tmp_path / "results"
    raw.mkdir()
    results.mkdir()
    run = RunDirectory(tmp_path, raw, results, tmp_path / "experiment_config.yaml")
    run.update_config(parameters={"test": True})
    rig = SimpleNamespace(device=Mock(), devices={}, channels={"y_rf": 2, "x_field": 1, "temp_switch": 2},
        run=run, enabled=True, events=[], waits=[], clock=0.0, reads=0, fault=None,
        primary=OSError("原始读入失败"), restore=OSError("温控恢复失败"), write=OSError("写入失败"))
    for method in ("set_output", "set_burst_state", "set_mod_state", "setup_sine", "setup_dc",
                   "set_burst_mode", "set_burst_trigger_source", "set_burst_trigger_slope",
                   "set_burst_phase", "set_amplitude", "set_frequency", "set_offset"):
        def record(*args, _method=method, **kwargs):
            assert rig.enabled, "实际输出配置必须在关闭温控前完成"
            rig.events.append(("configure", _method, args, kwargs))
        getattr(rig.device, method).side_effect = record
    rig.devices.update(xy_field=rig.device, hf2=Mock(), temp_switch=Mock())

    def switch(device, enabled, *, channel):
        rig.enabled = enabled
        rig.events.append("on" if enabled else "off")
        if enabled and rig.reads == 2 and rig.fault in ("restore", "acquire_and_restore"):
            raise rig.restore

    def wait(seconds):
        rig.waits.append(seconds)
        rig.clock += seconds
        rig.events.append("settle")

    def recover_wait(seconds):
        rig.clock += seconds

    def reader(device, **kwargs):
        assert not rig.enabled
        rig.reads += 1
        rig.events.append("acquire")
        if rig.reads == 2 and rig.fault in ("cancel", "acquire_and_restore"):
            raise rig.primary
        return dict(time_s=np.arange(8, dtype=float) / 1000.0,
                    r=np.full(8, rig.reads * 0.001), actual_rate_Sa_s=1000.0)

    save = np.savez
    def save_after_restore(path, *args, **kwargs):
        assert rig.enabled
        target = path.name if hasattr(path, "write") else path
        assert Path(target).resolve().is_relative_to(tmp_path.resolve())
        rig.events.append("save")
        rig.clock += 0.005
        if rig.fault == "save" and rig.reads == 2:
            raise rig.write
        return save(path, *args, **kwargs)

    monkeypatch.setattr(points, "_uncancellable_sleep", recover_wait)
    monkeypatch.setattr(np, "savez", save_after_restore)
    rig.point = partial(points._acquire_valid_r_point, reader=reader, switch=switch, wait=wait)
    rig.params = SimpleNamespace(demod_idx=0, demod_osc_idx=0, r_point_max_attempts=2,
        r_bad_point_std_threshold_v=0.0001, response_rate_sa_s=1000.0,
        response_duration_s=0.008, system_settle_s=3600.0, temp_switch_on_lag_s=0.25,
        y_rf_frequency_hz=1200.0, y_rf_offset_v=0.1, x_dc_field_v=0.0)
    return rig


def test_y_rf_burst_rearm_keeps_waveform_and_normalizes_values(y_rf):
    state = {"mode": "burst_sine"}
    result = workpoint.rearm_y_rf(y_rf.device, 2, amplitude_vpp=-0.2, phase_deg=-30,
                                 offset_v=0.1, waveform_state=state)
    assert y_rf.device.method_calls == [
        call.set_output(False, channel=2), call.set_burst_phase(330.0, channel=2),
        call.set_amplitude(0.2, channel=2), call.set_output(True, channel=2)]
    assert result == dict(hardware_amplitude_vpp=0.2, hardware_phase_deg=330.0,
                         dc_offset_v=0.1, waveform_mode="burst_sine", output_on=True)
    assert state == {"mode": "burst_sine"}


@pytest.mark.parametrize("offset", [0.0, 0.1])
def test_y_rf_zero_amplitude_is_pure_dc(y_rf, offset):
    state = {"mode": "burst_sine"}
    result = workpoint.rearm_y_rf(y_rf.device, 2, amplitude_vpp=0.0, phase_deg=37.0,
                                 offset_v=offset, waveform_state=state)
    assert y_rf.device.method_calls == [
        call.set_output(False, channel=2), call.set_burst_state(False, channel=2),
        call.set_mod_state(False, channel=2), call.setup_dc(offset, channel=2),
        call.set_output(offset != 0.0, channel=2)]
    assert result == dict(hardware_amplitude_vpp=0.0, hardware_phase_deg=37.0,
                         dc_offset_v=offset, waveform_mode="dc", output_on=offset != 0.0)
    assert state == {"mode": "dc"}


def test_y_rf_dc_restores_burst_then_enables_output(y_rf):
    state = {"mode": "dc"}
    result = workpoint.rearm_y_rf(y_rf.device, 2, amplitude_vpp=-0.2, phase_deg=-30.0,
                                 frequency_hz=1200.0, offset_v=0.1, waveform_state=state)
    assert y_rf.device.method_calls == [
        call.set_output(False, channel=2), call.set_output(False, channel=2),
        call.set_burst_state(False, channel=2), call.set_mod_state(False, channel=2),
        call.setup_sine(1200.0, 0.2, offset=0.1, phase=0.0, channel=2),
        call.set_burst_state(True, channel=2), call.set_burst_mode("INFinity", channel=2),
        call.set_burst_trigger_source("EXTernal", channel=2),
        call.set_burst_trigger_slope(workpoint.OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE, channel=2),
        call.set_burst_phase(330.0, channel=2), call.set_output(False, channel=2),
        call.set_output(True, channel=2)]
    assert result["hardware_amplitude_vpp"] == 0.2 and result["hardware_phase_deg"] == 330.0
    assert state == {"mode": "burst_sine"}


def test_y_rf_explicit_restore_keeps_output_off(y_rf):
    assert workpoint.restore_y_rf_burst_sine(y_rf.device, 2, frequency_hz=1200.0,
        amplitude_vpp=-0.2, offset_v=0.1, phase_deg=-30.0) is None
    y_rf.device.setup_sine.assert_called_once_with(1200.0, 0.2, offset=0.1, phase=0.0, channel=2)
    assert y_rf.device.method_calls[-2:] == [
        call.set_burst_phase(330.0, channel=2), call.set_output(False, channel=2)]


@pytest.mark.parametrize("offset,amplitude", [(1.1, 0.2), (0.95, 0.2), (-0.95, 0.2), (0.0, 2.1)])
def test_y_rf_envelope_rejects_before_output(y_rf, offset, amplitude):
    with pytest.raises(ValueError, match="安全拦截"):
        workpoint.rearm_y_rf(y_rf.device, 2, amplitude_vpp=amplitude, phase_deg=0.0, offset_v=offset)
    assert y_rf.device.method_calls == []


def test_y_rf_missing_frequency_keeps_original_off_side_effect(y_rf):
    state = {"mode": "dc"}
    with pytest.raises(ValueError, match="frequency_hz"):
        workpoint.rearm_y_rf(y_rf.device, 2, amplitude_vpp=0.2, phase_deg=0.0, waveform_state=state)
    assert y_rf.device.method_calls == [call.set_output(False, channel=2)]
    assert state == {"mode": "dc"}


def test_y_rf_hardware_failure_preserves_exception_and_state(y_rf):
    error = OSError("幅度写入失败")
    y_rf.device.set_amplitude.side_effect = error
    state = {"mode": "burst_sine"}
    with pytest.raises(OSError) as caught:
        workpoint.rearm_y_rf(y_rf.device, 2, amplitude_vpp=0.2, phase_deg=30.0, waveform_state=state)
    assert caught.value is error and state == {"mode": "burst_sine"}
    assert y_rf.device.method_calls == [
        call.set_output(False, channel=2), call.set_burst_phase(30.0, channel=2),
        call.set_amplitude(0.2, channel=2)]


@pytest.mark.parametrize("name,kwargs,result", [
    ("validate_y_rf_envelope", dict(offset_v=0.0, amplitude_vpp=0.2), None),
    ("restore_y_rf_burst_sine", dict(frequency_hz=1200.0, amplitude_vpp=0.2, offset_v=0.0, phase_deg=0.0), None),
    ("rearm_y_rf", dict(amplitude_vpp=0.2, phase_deg=0.0, frequency_hz=None, offset_v=0.0, waveform_state=None), {"marker": True}),
])
def test_y_rf_legacy_imports_forward_to_live_public_entry(monkeypatch, name, kwargs, result):
    from ..experiment_modules.mx_z_optimal_control_rf_sensitivity import excitation, workflow, acquisition

    spy = Mock(return_value=result)
    monkeypatch.setattr(workpoint, name, spy)
    old = getattr(excitation, "_" + name)
    assert getattr(workflow, "_" + name) is old
    args = () if name == "validate_y_rf_envelope" else (Mock(), 2)
    assert old(*args, **kwargs) == result
    if name == "validate_y_rf_envelope":
        spy.assert_called_once_with(kwargs["offset_v"], kwargs["amplitude_vpp"])
    else:
        spy.assert_called_once_with(*args, **kwargs)
    assert workflow._rearm_control is excitation._rearm_control
    assert acquisition._rearm_control is excitation._rearm_control


@pytest.mark.parametrize("fault", ["normal", "cancel", "acquire_and_restore", "restore", "save"])
def test_y_rf_root_two_points_keep_gate_and_partial_data(y_rf, monkeypatch, fault):
    from ..common import WorkflowCancelled
    from ..experiment_modules.mx_z_optimal_control_rf_sensitivity import acquisition

    assert acquisition._rearm_y_rf is workpoint.rearm_y_rf
    monkeypatch.setattr(acquisition, "build_amplitude_axis", lambda params: np.array([0.0, 0.2]))
    y_rf.fault = fault
    if fault == "cancel":
        y_rf.primary = WorkflowCancelled("模拟第二点取消")
    expected = {"cancel": y_rf.primary, "acquire_and_restore": y_rf.primary,
                "restore": y_rf.restore, "save": y_rf.write}.get(fault)
    def scan():
        return acquisition._acquire_amplitude_scan(y_rf.params, y_rf.run, y_rf.devices,
            y_rf.channels, 1000.0, "fake", 30.0, point_acquirer=y_rf.point)
    if expected is None:
        scan()
    else:
        with pytest.raises(type(expected)) as caught:
            scan()
        assert caught.value is expected
    assert y_rf.enabled and y_rf.reads == 2
    assert y_rf.waits == [3600.0, 3600.0]
    # switch 抛错时不会进入恢复等待；只累计实际执行的恢复等待与写入尝试。
    recoveries = 1 if fault in ("restore", "acquire_and_restore") else 2
    assert y_rf.clock == pytest.approx(7200.0 + 0.25 * recoveries + 0.005 * y_rf.events.count("save"))
    for index, event in enumerate(y_rf.events):
        if event == "acquire":
            assert y_rf.events[index-2:index] == ["off", "settle"]
            assert y_rf.events[index+1] == "on"
    files = sorted(y_rf.run.raw.glob("amplitude_*_attempt_*.npz"))
    assert len(files) == (2 if fault in ("normal", "restore") else 1)
    with np.load(files[0]) as data:
        assert data["r_v"].shape == data["time_s"].shape == (8,)
        assert data["r_v"].dtype == np.dtype("float64") and np.isfinite(data["r_v"]).all()
        assert float(data["signed_amplitude_vpp"]) == 0.0
        assert str(data["waveform_mode"]) == "dc" and bool(data["output_on"])
        assert float(data["r_mean_v"]) == pytest.approx(0.001)
    aggregate = y_rf.run.raw / "amplitude_scan.npz"
    assert aggregate.exists() == (fault == "normal")
    if fault == "normal":
        with np.load(aggregate) as data:
            np.testing.assert_array_equal(data["signed_amplitude_vpp"], [0.0, 0.2])
            np.testing.assert_array_equal(data["hardware_amplitude_vpp"], [0.0, 0.2])
            np.testing.assert_array_equal(data["waveform_mode"], ["dc", "burst_sine"])
            np.testing.assert_allclose(data["r_mean_v"], [0.001, 0.002])
            assert float(data["actual_rate_sa_s"]) == 1000.0


@pytest.mark.parametrize("mode", ["optimal", "constant"])
def test_y_rf_frequency_callers_use_two_point_real_writers(y_rf, monkeypatch, mode):
    from ..experiment_modules.mx_y_optimal_control_rf_frequency_response import acquisition

    assert acquisition._rearm_y_rf is workpoint.rearm_y_rf
    params = y_rf.params
    params.frequency_rf_amplitude_vpp = 0.2
    params.frequency_duration_s = 0.008
    monkeypatch.setattr(acquisition, "build_frequency_axis", lambda params: np.array([1200.0, 1300.0]))
    monkeypatch.setattr(acquisition, "build_phase_axis", lambda params: np.array([0.0, 180.0]))
    oscillator = Mock()
    monkeypatch.setattr(acquisition.demod, "configure_oscillator", oscillator)
    progress = SimpleNamespace(advance=Mock())
    args = (params, y_rf.run, y_rf.devices, y_rf.channels, 1000.0, "fake")
    if mode == "optimal":
        acquisition._scan_optimal_control(*args, progress, point_acquirer=y_rf.point)
        filename, shape, count = "optimal_control_phase_frequency_scan.npz", (2, 2), 4
    else:
        plan = SimpleNamespace(target_voltage_v=0.03, extrapolated=False)
        acquisition._scan_constant_control(*args, plan, progress, point_acquirer=y_rf.point)
        filename, shape, count = "constant_control_frequency_scan.npz", (2,), 2
    assert y_rf.reads == count and oscillator.call_count == count and y_rf.enabled
    with np.load(y_rf.run.raw / filename) as data:
        assert data["r_mean_v"].shape == shape and data["r_mean_v"].dtype == np.dtype("float64")
        np.testing.assert_array_equal(data["frequency_hz"], [1200.0, 1300.0])
        assert np.isfinite(data["r_mean_v"]).all() and float(data["actual_rate_sa_s"]) == 1000.0
    points = sorted(y_rf.run.raw.glob("*frequency_*_attempt_00.npz"))
    assert len(points) == count
    for path in points:
        with np.load(path) as data:
            assert data["r_v"].shape == (8,) and float(data["y_rf_amplitude_vpp"]) == 0.2
            assert float(data["phase_deg"]) in (0.0, 180.0)
    y_rf.device.setup_sine.assert_not_called()


def test_y_rf_xy_restore_keeps_output_off_before_response_configuration(y_rf, monkeypatch):
    from ..experiment_modules.mx_z_optimal_control_xy_rf_sensitivity import acquisition

    assert acquisition._restore_y_rf_burst_sine is workpoint.restore_y_rf_burst_sine
    params = SimpleNamespace(y_rf_frequency_hz=1200.0, phase_cal_rf_amplitude_vpp=0.2, y_rf_offset_v=0.0)
    def configure(params, hf2):
        assert y_rf.enabled
        assert y_rf.device.set_output.call_args == call(False, channel=2)
        return 1000.0
    monkeypatch.setattr(acquisition, "_configure_response_demodulator", configure)
    for offset in (-0.1, 0.1):
        params.y_rf_offset_v = offset
        rate = acquisition._restore_xy_response_acquisition_state(params, y_rf.devices, y_rf.channels, 45.0)
        assert type(rate) is float and rate == 1000.0
    assert y_rf.device.setup_sine.call_args_list == [
        call(1200.0, 0.2, offset=-0.1, phase=0.0, channel=2),
        call(1200.0, 0.2, offset=0.1, phase=0.0, channel=2)]


def test_y_rf_offset_phase_caller_keeps_pair_and_burst(y_rf):
    from ..experiment_modules.mx_z_optimal_control_xy_rf_phase_response import excitation

    assert excitation._validate_y_rf_envelope is workpoint.validate_y_rf_envelope
    params = SimpleNamespace(phase_cal_rf_amplitude_vpp=0.2, y_rf_frequency_hz=1200.0)
    excitation._configure_y_rf_at_offset(params, y_rf.device, 2, -0.1)
    y_rf.device.reset_mock()
    for offset, phase in ((-0.1, 0.0), (0.1, 180.0)):
        excitation._rearm_y_rf_at_offset(params, y_rf.device, 2, offset_v=offset, phase_deg=phase)
    y_rf.device.setup_sine.assert_not_called()
    assert y_rf.device.set_burst_phase.call_args_list == [call(0.0, channel=2), call(180.0, channel=2)]
    assert y_rf.device.set_offset.call_args_list == [call(-0.1, channel=2), call(0.1, channel=2)]
    y_rf.device.reset_mock()
    with pytest.raises(ValueError, match="Y_magnetic_field"):
        excitation._rearm_y_rf_at_offset(params, y_rf.device, 2, offset_v=0.95, phase_deg=0.0)
    assert y_rf.device.method_calls == []


def test_y_rf_noise_initialization_keeps_indirect_zero_rf_path(y_rf, monkeypatch):
    from ..experiment_modules.mx_z_optimal_control_xy_noise_spectrum import preparation as noise
    from ..experiment_modules.mx_z_optimal_control_rf_sensitivity import preparation as original

    assert noise._configure_common_outputs is original._configure_common_outputs
    validate = Mock(wraps=workpoint.validate_y_rf_envelope)
    monkeypatch.setattr(workpoint, "validate_y_rf_envelope", validate)
    def configure(*args, xy_output_configurator, **kwargs):
        assert y_rf.enabled
        xy_output_configurator()
        return (1000.0, None, {}, {})
    monkeypatch.setattr(original, "configure_mx_z_optimal_control_workpoint", configure)
    params = SimpleNamespace(x_dc_field_v=0.0, y_rf_offset_v=0.0,
                             phase_cal_rf_amplitude_vpp=0.0, y_rf_frequency_hz=1200.0)
    result = noise._configure_common_outputs(params, y_rf.devices, y_rf.channels, {}, None, None)
    assert result == (1000.0, None, {}, {})
    validate.assert_called_once_with(0.0, 0.0)
    assert y_rf.device.setup_dc.call_args_list == [call(0.0, channel=1), call(0.0, channel=2)]
    assert all(item.args == (False,) for item in y_rf.device.set_output.call_args_list)


@pytest.mark.parametrize("enabled", [False, True])
def test_y_rf_control_noise_keeps_metadata_and_real_file(y_rf, enabled):
    from .. import common
    from ..experiment_modules.mx_z_optimal_control_rf_sensitivity import acquisition

    params = y_rf.params
    params.noise_rf_enabled, params.noise_rf_amplitude_vpp = enabled, 0.2
    params.y_rf_amp_stop_vpp = 0.0
    def noise(p, run, devices, channels, device_id, *, set_y_rf_off_state, noise_metadata, **kwargs):
        summary, _, _ = y_rf.point(p, run, devices, channels, file_stem="noise_000", metadata=noise_metadata,
            set_y_rf=set_y_rf_off_state, settle_time_s=kwargs["settle_time_s"], duration_s=0.008,
            actual_rate=1000.0, device_id=device_id)
        return summary["actual_rate_sa_s"]
    rate = acquisition._acquire_control_noise(params, y_rf.run, y_rf.devices, y_rf.channels, "fake", -30.0, noise_acquirer=noise)
    assert rate == 1000.0 and y_rf.enabled
    expected = dict(y_rf_enabled=enabled, y_rf_amplitude_vpp=0.2 if enabled else 0.0,
        y_rf_frequency_hz=1200.0, y_rf_burst_phase_deg=330.0, y_rf_dc_v=0.1, y_rf_output_on=True)
    assert common.load_yaml(y_rf.run.config_path)["noise_rf"] == expected
    with np.load(y_rf.run.raw / "noise_000_attempt_00.npz") as data:
        assert data["r_v"].shape == (8,)
        for key, value in expected.items():
            assert data[key].item() == value
    assert y_rf.device.setup_sine.call_count == int(enabled)
