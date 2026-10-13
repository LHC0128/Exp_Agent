"""DirectAW 的参数、触发、校相、DAQ 与安全收尾无硬件回归。"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import math

import numpy as np
import pytest
import yaml

from lockin_amplifier import HF2Instrument, DAQResult
from lab_workflows import control_sources
from lab_workflows.common import WorkflowCancelled, find_project_root, load_mapping
from lab_workflows.experiments import get_experiment
from lab_workflows.steps import create_run_directory
from lab_workflows.steps import routed_signal_generator
from . import workflow, preparation, excitation, phase, acquisition, _helpers
from .models import RFSensitivityDirectAWParams
from .waveform import load_envelope, synthesize_xy


class FakeSignal:
    def __init__(self, resource, rig):
        self.resource, self.rig = resource, rig
        self.output, self.burst, self.sync, self.amplitude, self.phase, self.dc = ({}, {}, {}, {}, {}, {})
        self.shape, self.frequency, self.mod, self.burst_mode, self.trigger_source = ({}, {}, {}, {}, {})
        self.clock = "INT"

    def connect(self):
        self.rig.events.append((self.resource, "connect", None, None))

    def disconnect(self):
        self.rig.events.append((self.resource, "disconnect", None, None))

    def set_ref_clock_source(self, value):
        self.clock = value

    def get_ref_clock_source(self):
        return self.clock

    def __getattr__(self, name):
        def command(*args, channel=1, **kwargs):
            value = args[0] if args else kwargs.copy()
            self.rig.events.append((self.resource, name, channel, value))
            attributes = {"output": "output", "burst_state": "burst", "sync_state": "sync",
                          "amplitude": "amplitude", "burst_phase": "phase", "shape": "shape",
                          "frequency": "frequency", "mod_state": "mod", "burst_mode": "burst_mode",
                          "burst_trigger_source": "trigger_source"}
            if name.startswith("get_") and name[4:] in attributes:
                return getattr(self, attributes[name[4:]]).get(channel)
            if name.startswith("set_") and name[4:] in attributes:
                getattr(self, attributes[name[4:]])[channel] = value
            if name == "setup_dc":
                self.dc[channel] = float(value)
                self.shape[channel] = "DC"
            if name in ("setup_sine", "setup_square", "setup_pulse", "setup_arbitrary"):
                self.shape[channel] = {"setup_sine": "SIN", "setup_square": "SQU",
                                       "setup_pulse": "PULS", "setup_arbitrary": "ARB"}[name]
                self.frequency[channel] = kwargs["freq"]
                self.amplitude[channel] = kwargs["amplitude"]
            if self.rig.shutdown_error and name == "setup_dc" and self.rig.in_shutdown:
                raise RuntimeError("模拟安全归零失败")
        return command


class FakeHF2(HF2Instrument):
    def __init__(self, rig, **kwargs):
        super().__init__(**kwargs)
        self.rig, self.nodes = rig, {}
        self.nodes.update({f"{self.sig_in_path(index)}/{mode}": 1
                           for index in (0, 1) for mode in ("ac", "imp50")})

    def connect(self):
        self.rig.events.append(("hf2", "connect", None, None))

    def disconnect(self):
        self.rig.events.append(("hf2", "disconnect", None, None))

    def sync(self):
        pass

    def set_int(self, path, value):
        if "/sigins/" in path and path.endswith(("/ac", "/imp50")):
            self.rig.events.append(("hf2", "input_mode", path, value))
        self.nodes[path] = value

    def set_double(self, path, value):
        if self.rig.restore_error and self.rig.in_daq and path.endswith("demods/3/rate") and value == 1000:
            raise RuntimeError("模拟 HF2 恢复失败")
        self.nodes[path] = value * 0.9 if path.endswith("/rate") else value

    def get_int(self, path):
        return 210000000 if path.endswith("/clockbase") else int(self.nodes.get(path, 0))

    def get_double(self, path):
        return float(self.nodes.get(path, 0))

    def get_sample(self, path):
        x_device = self.rig.generators[self.rig.mapping["X_magnetic_field"]["resource"]]
        xy_active = x_device.output.get(self.rig.mapping["X_magnetic_field"]["channel"], False)
        angle, radius = (self.rig.xy_angle, self.rig.xy_r) if "/demods/0/" in path and xy_active else (0.0, 1.0)
        if "/demods/3/" in path:
            z_device = self.rig.generators[self.rig.mapping["Z_magnetic_field"]["resource"]]
            channel = self.rig.mapping["Z_magnetic_field"]["channel"]
            radius = z_device.amplitude.get(channel, 0) * 0.05 if z_device.output.get(channel) else 0.0
            angle = math.pi if z_device.phase.get(channel, 0) > 180 else 0.0
        return {"x": np.array([radius * math.cos(angle)]), "y": np.array([radius * math.sin(angle)])}


@pytest.fixture
def control_source(tmp_path, monkeypatch):
    directory = tmp_path / "oc_sens"
    waveform = directory / "v1/waveforms/optimal_control_waveform.csv"
    waveform.parent.mkdir(parents=True, exist_ok=True)
    waveform.write_bytes((find_project_root() / "experiments/control_waveform.csv").read_bytes())
    monkeypatch.setitem(control_sources.CONTROL_SOURCE_SET_ROOTS, "oc_sens", directory)
    return replace(RFSensitivityDirectAWParams(), arb_waveform_file="v1/waveforms/optimal_control_waveform.csv")


@pytest.fixture
def rig(tmp_path, monkeypatch, control_source):
    root = find_project_root()
    mapping = load_mapping(root)
    instance = SimpleNamespace(mapping=mapping, events=[], generators={}, xy_angle=0.0, xy_r=1.0, in_daq=False, in_shutdown=False, daq_error=None, shutdown_error=False, restore_error=False, cancel=False, z_phi0=37.0, z_scan_baseline=0.5, z_scan_amplitude=5.0, z_scan_phases=[])
    for resource in {config.get("resource") for config in mapping.values() if config["instrument"] == "signal_generator"}:
        instance.generators[resource] = FakeSignal(resource, instance)
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda config: instance.generators[config["resource"]])
    monkeypatch.setattr(preparation, "GS200Instrument", lambda resource: Mock())
    monkeypatch.setattr(preparation, "create_lockin", lambda config: FakeHF2(instance, device_id=config["device_id"]))
    monkeypatch.setattr(preparation, "TECInstrument", lambda resource: Mock())
    monkeypatch.setattr(preparation, "configure_temperature_control", lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"control_source": "tec103"}))
    monkeypatch.setattr(workflow, "create_run_directory", lambda data_type, tag, parameters, **kwargs: create_run_directory(data_type, tag, parameters, schema_version=2, project_root=tmp_path))
    # 目录快照仍读取真实配置文件，不连接硬件。
    (tmp_path / "params").mkdir(exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        (tmp_path / "params" / name).write_bytes((root / "params" / name).read_bytes())
    monkeypatch.setattr(workflow, "find_project_root", lambda: tmp_path)

    runtime_check = _helpers.check_cancelled
    def check():
        runtime_check()
        if instance.cancel:
            raise WorkflowCancelled("模拟取消")
    monkeypatch.setattr(workflow, "check_cancelled", check)
    for module in (workflow, preparation, excitation, phase, acquisition, _helpers):
        monkeypatch.setattr(module, "wait", lambda seconds: check())
    for module in (excitation, phase, acquisition, _helpers):
        monkeypatch.setattr(module, "check_cancelled", check)
    def report(stage, *args, **kwargs):
        if stage == "shutdown":
            instance.in_shutdown = True
    monkeypatch.setattr(workflow, "report_runtime_progress", report)
    def acquire(hf2, config, **kwargs):
        instance.in_daq = True
        if config.signal_paths != ["sample.x"]:
            # 相位/幅度扫描：按 Z 通道当前幅度与 Burst 相位返回已知响应模型（相位 180° 表示负号）。
            z_config = instance.mapping["Z_magnetic_field"]
            z_device = instance.generators[z_config["resource"]]
            channel = z_config["channel"]
            phase = float(z_device.phase.get(channel, 0.0))
            amplitude = float(z_device.amplitude.get(channel, 0.0)) if z_device.output.get(channel, False) else 0.0
            if amplitude == instance.params.z_rf_amplitude_init:
                instance.z_scan_phases.append(phase)
            response = complex(instance.z_scan_baseline) + instance.z_scan_amplitude * amplitude * np.exp(1j * math.radians(phase - instance.z_phi0))
            columns = max(8, config.grid_cols)
            time = np.arange(columns) / kwargs["actual_rate"]
            kwargs["cancellation_check"]()
            return [DAQResult(signal_name=name, demod_index=3, values=np.full(columns, value), time=time) for name, value in (("sample.r", abs(response)), ("sample.x", response.real), ("sample.y", response.imag))]
        assert kwargs["actual_rate"] == 90000
        assert config.grid_cols == int(90000 * config.duration)
        assert kwargs["strict_cleanup"] is True
        if instance.daq_error:
            raise instance.daq_error
        kwargs["cancellation_check"]()
        return [DAQResult(signal_name="sample.x", demod_index=3, values=np.arange(100, dtype=float), time=np.arange(100) / 90000)]
    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    instance.acquire = acquire
    instance.tmp_path = tmp_path
    instance.params = replace(control_source, rf_amp_points=3, noise_n_avg=1, z_burst_phase_init=17.0)
    return instance


def saved_config(rig):
    path = next((rig.tmp_path / "data" / "RF_Field_Sensitivity_AW").glob("*/experiment_config.yaml"))
    return yaml.safe_load(path.read_text(encoding="utf-8")), path.parent


def assert_shutdown(rig):
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field"):
        config = rig.mapping[key]
        device = rig.generators[config["resource"]]
        assert device.output[config["channel"]] is False
        assert device.burst[config["channel"]] is False
    # 时序信号2（共同触发）与 Pump 门控在收尾后按要求保持运行。
    for key in ("Time_sequence_2", "Time_sequence"):
        config = rig.mapping[key]
        device = rig.generators[config["resource"]]
        assert device.output[config["channel"]] is True
    config = rig.mapping["Temp_Switch"]
    device = rig.generators[config["resource"]]
    assert device.dc[config["channel"]] == 5.0
    assert device.output[config["channel"]] is True


def test_complete_run_preserves_trigger_and_signed_phase(rig):
    directory = workflow.run(rig.params)
    config, _ = saved_config(rig)
    assert config["completion_status"] == "completed"
    assert config["phase_calibration_status"] == "completed"
    assert config["actual_rates"]["demod3_noise_Sa_s"] == 90000
    assert config["common_trigger"]["output"] == "channel"
    assert config["calibration"]["z_positive_field_phase_deg"] == pytest.approx(37.0)
    assert config["calibration"]["z_negative_field_phase_deg"] == pytest.approx(217.0)
    assert config["rf_phase_calibration"]["selected_phase_deg"] == pytest.approx(37.0)
    assert config["rf_phase_calibration"]["points_completed"] == 18
    assert config["z_phase_scan_plot"] == "results/z_phase_scan.png"
    assert (directory / "results/z_phase_scan.png").is_file()
    with np.load(directory / "raw/z_phase_scan.npz") as data:
        assert data["selected_phase_deg"] == pytest.approx(37.0)
        assert data["z_rf_amplitude_vpp"] == pytest.approx(rig.params.z_rf_amplitude_init)
    # 扫描按 φ、φ+180° 成对顺序 arm，且只依赖 set_burst_phase/set_amplitude 重臂。
    assert rig.z_scan_phases == pytest.approx([phase for start in range(0, 180, 20) for phase in (start, start + 180)])
    z = rig.mapping["Z_magnetic_field"]
    z_phase_indices = [
        index for index, event in enumerate(rig.events)
        if event[:3] == (z["resource"], "set_burst_phase", z["channel"])
    ]
    scan_phase_indices = z_phase_indices[1:1 + len(rig.z_scan_phases)]
    temp = rig.mapping["Temp_Switch"]
    temp_switch_events = [
        event[3] for index, event in enumerate(rig.events)
        if scan_phase_indices[0] < index < scan_phase_indices[-1]
        and event[:3] == (temp["resource"], "setup_dc", temp["channel"])
    ]
    # 18 个点按五点分组：前三组之间先恢复温控，再关闭后继续下一组。
    assert temp_switch_events == [5.0, 0.0] * 3
    after_scan_events = [
        event[3] for index, event in enumerate(rig.events)
        if scan_phase_indices[-1] < index < z_phase_indices[1 + len(rig.z_scan_phases)]
        and event[:3] == (temp["resource"], "setup_dc", temp["channel"])
    ]
    assert after_scan_events == [5.0]
    # 幅度扫描与相位扫描节奏一致：3 点为一组（分组点数 5），组前关温控、组末恢复；噪声逐条开关。
    demod3_calib_end = z_phase_indices[1 + len(rig.z_scan_phases)]
    response_stage_events = [
        event[3] for index, event in enumerate(rig.events)
        if index > demod3_calib_end and event[:3] == (temp["resource"], "setup_dc", temp["channel"])
    ]
    assert response_stage_events == [0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 5.0]
    assert config["hf2_response_snapshot"]["/dev18246/demods/3/adcselect"] == 1
    assert config["hf2_response_snapshot"]["/dev18246/auxouts/1/demodselect"] == 0
    assert config["hf2_response_snapshot"]["/dev18246/auxouts/1/outputselect"] == 1
    assert config["hf2_noise_snapshot"]["/dev18246/sigins/1/range"] == rig.params.loopback_input_range_v
    for snapshot in ("hf2_response_snapshot", "hf2_noise_snapshot", "hf2_final_snapshot"):
        for index in (0, 1):
            for mode in ("ac", "imp50"):
                assert config[snapshot][f"/dev18246/sigins/{index}/{mode}"] == 0
    input_events = [event for event in rig.events if event[1] == "input_mode"]
    assert len(input_events) >= 4 and all(event[3] == 0 for event in input_events)
    assert config["hf2_final_snapshot"]["/dev18246/demods/3/timeconstant"] == rig.params.demod3_tc
    trigger = rig.mapping["Time_sequence_2"]
    events = [(index, event) for index, event in enumerate(rig.events) if event[:3] == (trigger["resource"], "set_output", trigger["channel"])]
    # 初始化关闭→启动后保持连续输出，收尾不再关闭。
    assert [event[3] for _, event in events] == [False, True]
    xy_upload = next(index for index, event in enumerate(rig.events) if event[1] == "setup_arbitrary")
    assert events[1][0] < xy_upload
    # Demod0 校相前 XY 输出保持真正关闭（setup_dc 不再顺带打开输出）。
    x_config = rig.mapping["X_magnetic_field"]
    x_outputs = [event[3] for event in rig.events[:xy_upload] if event[:3] == (x_config["resource"], "set_output", x_config["channel"])]
    assert x_outputs[-1] is False
    # 时序信号2 的 SYNC 在方波启动后显式重新打开（仪器在 DC 波形下会自动关闭 SYNC）。
    assert any(event[1] == "set_sync_state" and event[3] is True and event[0] == trigger["resource"] for event in rig.events)
    z = rig.mapping["Z_magnetic_field"]
    assert sum(event[:3] == (z["resource"], "setup_sine", z["channel"]) for event in rig.events) == 1
    connected = [event[0] for event in rig.events if event[1] == "connect"]
    assert len(connected) == len(set(connected))
    assert rig.mapping["X_magnetic_field_AM"]["resource"] not in connected
    with np.load(directory / "raw/response_data.npz") as data:
        assert data["z_burst_phase_deg"].tolist() == pytest.approx([217.0, 37.0, 37.0])
        assert data["demod3_x_V"][0] < 0 < data["demod3_x_V"][-1]
        assert data["z_v_to_nT"] == pytest.approx(rig.params.z_v_to_nt)
        assert data["amplitudes_nT"].tolist() == pytest.approx((np.asarray(data["amplitudes_V"]) * rig.params.z_v_to_nt).tolist())
        # 幅度扫描改为定长 DAQ：保存逐点时序、均值/标准差、采样时长与实际采样率。
        assert data["demod3_x_series_V"].ndim == 2 and data["demod3_x_series_V"].shape[0] == 3
        assert data["demod3_x_std_V"].shape == (3,) and np.all(data["demod3_x_std_V"] == 0.0)
        assert data["duration_s"] == pytest.approx(rig.params.scan_point_duration_s)
        assert data["actual_rate_Sa_s"] == pytest.approx(config["actual_rates"]["demod3_response_Sa_s"])
        assert config["response_acquisition"]["mode"] == "daq_duration"
        assert config["response_acquisition"]["temperature_group_size"] == rig.params.temp_group_size
    with np.load(directory / "raw/noise_D3_0000.npz") as data:
        assert data["actual_rate_Sa_s"] == 90000
    assert_shutdown(rig)
    for key in ("Pump_laser_power", "Probe_laser_power", "Pump_modulation"):
        channel_config = rig.mapping[key]
        assert rig.generators[channel_config["resource"]].output[channel_config["channel"]] is True
    assert config["safety_shutdown"]["preserved_outputs"] == ["main_magnetic_field", "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "HF2_configuration", "Time_sequence_2", "Time_sequence"]


def test_amplitude_scan_groups_temperature(rig):
    params = replace(rig.params, rf_amp_points=7, temp_group_size=3)
    directory = workflow.run(params)
    config, _ = saved_config(rig)
    assert config["completion_status"] == "completed"
    assert config["response_points_completed"] == 7
    assert config["response_acquisition"]["temperature_group_size"] == 3
    with np.load(directory / "raw/response_data.npz") as data:
        assert len(data["amplitudes_V"]) == 7
        assert data["z_burst_phase_deg"].tolist() == pytest.approx([217.0] * 3 + [37.0] * 4)
    # 幅度阶段温控事件：Demod3 校相前关闭、校相后恢复→入组关闭；每 3 点恢复/再关闭，末组恢复后进入噪声逐条开关，收尾再恢复。
    z = rig.mapping["Z_magnetic_field"]
    temp = rig.mapping["Temp_Switch"]
    z_phase_events = [index for index, event in enumerate(rig.events) if event[:3] == (z["resource"], "set_burst_phase", z["channel"])]
    stage_events = [event[3] for index, event in enumerate(rig.events) if index > z_phase_events[1 + 18] and event[:3] == (temp["resource"], "setup_dc", temp["channel"])]
    assert stage_events == [0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 5.0]


@pytest.mark.parametrize("failure", ["low_r", "nonconverged", "nonfinite"])
def test_xy_failure_saves_diagnostics_without_measurement(rig, failure):
    rig.xy_r = 1e-20 if failure == "low_r" else float("nan") if failure == "nonfinite" else 1.0
    rig.xy_angle = 0.2 if failure == "nonconverged" else 0
    with pytest.raises(RuntimeError, match="XY 校相"):
        workflow.run(replace(rig.params, xy_ctrl_phase_max_iter=1))
    config, directory = saved_config(rig)
    assert config["completion_status"] == "failed"
    assert config["phase_calibration_status"] == "failed"
    assert config["xy_phase_calibration"]["history"]
    assert not (directory / "raw/response_data.npz").exists()
    assert config["safety_shutdown"]["completed"]
    assert_shutdown(rig)


@pytest.mark.parametrize("cancel", [False, True])
def test_daq_failure_or_cancel_restores_hf2_and_outputs(rig, monkeypatch, cancel):
    error = WorkflowCancelled("模拟取消") if cancel else RuntimeError("模拟 DAQ 异常")
    rig.daq_error = error
    with pytest.raises(type(error), match=str(error)):
        workflow.run(rig.params)
    config, directory = saved_config(rig)
    assert config["completion_status"] == ("cancelled" if cancel else "failed")
    assert config["hf2_final_snapshot"]["/dev18246/demods/3/timeconstant"] == rig.params.demod3_tc
    assert (directory / "raw/response_data.npz").exists()
    assert config["noise_records_completed"] == 0
    assert_shutdown(rig)


def test_primary_daq_error_survives_restore_and_shutdown_errors(rig):
    rig.daq_error = RuntimeError("主要 DAQ 错误")
    rig.restore_error = rig.shutdown_error = True
    with pytest.raises(RuntimeError, match="主要 DAQ 错误"):
        workflow.run(rig.params)
    config, _ = saved_config(rig)
    assert config["failure_reason"] == "主要 DAQ 错误"
    assert "HF2 恢复失败" in config["hf2_restore_error"]
    assert config["safety_shutdown"]["completed"] is False


def test_shutdown_failure_rejects_success(rig):
    rig.shutdown_error = True
    with pytest.raises(RuntimeError, match="安全收尾失败"):
        workflow.run(rig.params)
    assert saved_config(rig)[0]["completion_status"] == "failed"


def test_cancel_in_scan_preserves_first_point(rig, monkeypatch):
    def progress(stage, *args, **kwargs):
        if stage == "response":
            rig.cancel = True
    monkeypatch.setattr(workflow, "report_runtime_progress", progress)
    with pytest.raises(WorkflowCancelled):
        workflow.run(rig.params)
    config, directory = saved_config(rig)
    assert config["response_points_completed"] == 1
    assert config["completion_status"] == "cancelled"
    with np.load(directory / "raw/response_data.npz") as data:
        assert len(data["amplitudes_V"]) == 1
    assert_shutdown(rig)


def test_rf_phase_scan_fit_failure_stops_run(rig):
    rig.z_scan_baseline = 0.0
    with pytest.raises(ValueError, match="基线复幅度为零"):
        workflow.run(rig.params)
    config, directory = saved_config(rig)
    assert config["completion_status"] == "failed"
    assert config["phase_calibration_status"] == "failed"
    assert config["rf_phase_calibration"]["points_completed"] == 18
    assert (directory / "raw/z_phase_scan.npz").is_file()
    assert (directory / "results/z_phase_scan.png").is_file()
    assert not (directory / "raw/response_data.npz").exists()
    assert config["safety_shutdown"]["completed"]
    assert_shutdown(rig)


def test_cancel_in_rf_phase_scan_keeps_completed_points(rig, monkeypatch):
    original = acquisition.daq.acquire_data
    calls = {"count": 0}

    def acquire(hf2, config, **kwargs):
        if config.signal_paths != ["sample.x"]:
            calls["count"] += 1
            if calls["count"] == 2:
                raise WorkflowCancelled("模拟扫描取消")
        return original(hf2, config, **kwargs)

    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    with pytest.raises(WorkflowCancelled):
        workflow.run(rig.params)
    config, directory = saved_config(rig)
    assert config["completion_status"] == "cancelled"
    assert config["rf_phase_calibration"]["points_completed"] == 1
    with np.load(directory / "raw/z_phase_scan.npz") as data:
        assert len(data["scanned_phase_deg"]) == 1
    assert not (directory / "results/z_phase_scan.png").exists()
    assert_shutdown(rig)


def test_mapping_rejects_missing_or_incapable_devices(monkeypatch):
    mapping = load_mapping()
    definition = get_experiment("rf-sensitivity-aw")
    mapping.pop("Time_sequence_2")
    mapping["Z_magnetic_field"]["capabilities"]["supports_infinite_burst"] = False
    mapping["X_magnetic_field"]["capabilities"]["supports_arbitrary"] = False
    monkeypatch.setattr("lab_workflows.common.load_mapping", lambda: mapping)
    errors = "；".join(definition.preflight({}))
    assert "Time_sequence_2" in errors
    assert "supports_infinite_burst" in errors
    assert "supports_arbitrary" in errors


def test_wait_observes_cancel_file(tmp_path, monkeypatch):
    path = tmp_path / "cancel"
    path.touch()
    monkeypatch.setenv("LAB_CANCEL_FILE", str(path))
    with pytest.raises(WorkflowCancelled):
        workflow.wait(10)


def test_parameter_waveform_and_mapping_preflight(control_source):
    params = control_source
    assert not params.validate()
    assert "必须一致" in "；".join(replace(params, z_trigger_freq=200).validate())
    assert "正整数倍" in "；".join(replace(params, xy_trigger_freq=300, z_trigger_freq=300).validate())
    assert "固定为 1" in "；".join(replace(params, demod3_adc_select=2).validate())
    assert "安全拦截" in "；".join(replace(params, xy_trigger_amplitude=50, z_trigger_amplitude=50).validate())
    assert "整除" in "；".join(replace(params, z_phase_scan_step_deg=25).validate())
    assert "360° 周期" in "；".join(replace(params, z_phase_scan_stop_deg=320).validate())
    definition = get_experiment("rf-sensitivity-aw")
    assert "X_magnetic_field_AM" not in definition.required_mapping_keys
    schema = definition.schema()
    assert schema["schema_version"] == 2
    assert not any(field["name"].startswith("Z_TRIGGER_") for field in schema["fields"])
    assert definition.preflight(params.to_external()) == []
    # 旧外部键自动迁移：稳定等待/采样时长改名，响应平均键剥离。
    legacy = RFSensitivityDirectAWParams.from_external({
        "RF_SETTLE_TIME": 0.12, "XY_PHASE_SETTLE_TIME": 0.6, "Z_PHASE_SCAN_DURATION_S": 0.3,
        "RESPONSE_N_AVG": 5, "RESPONSE_AVG_INTERVAL": 0.2, **params.to_external()})
    assert legacy.rf_step_settle_s == 0.12
    assert legacy.temp_off_settle_s == 0.6
    assert legacy.scan_point_duration_s == 0.3
    assert legacy.temp_group_size == 5


def test_resampling_and_quadrature_match_old_rules(control_source):
    params = control_source
    envelope = load_envelope(params, 16384)
    assert envelope["time_s"].size == 8000
    assert envelope["repeat_freq_Hz"] == pytest.approx(2000)
    waveforms = synthesize_xy(params, envelope, 0)
    angle = 2 * np.pi * params.pump_mod_freq * envelope["time_s"]
    assert np.allclose(waveforms["y_waveform_V"], -envelope["envelope_V"] * np.sin(angle), atol=1e-12)
    peak = float(abs(envelope["envelope_V"]).max())
    assert waveforms["xy_aw_vpp"] == pytest.approx(2.0 * peak)
    assert waveforms["xy_aw_output_offset_v"] == 0.0
    assert float(np.max(np.abs(waveforms["x_normalized"]))) <= 1.0 + 1e-12
    assert float(np.max(np.abs(waveforms["y_normalized"]))) <= 1.0 + 1e-12
    tiny = dict(envelope, envelope_V=envelope["envelope_V"] * 0.0)
    with pytest.raises(ValueError, match="低于仪器最小幅度"):
        synthesize_xy(params, tiny, 0)
    with pytest.raises(ValueError, match="整数倍"):
        load_envelope(replace(params, pump_mod_freq=9000), 16384)
    with pytest.raises(ValueError, match="限制"):
        load_envelope(params, 4000)
    waveform = control_sources.resolve_control_results_root("oc_sens") / "v2/waveforms/optimal_control_waveform.csv"
    waveform.parent.mkdir(parents=True)
    waveform.write_bytes((find_project_root() / "experiments/optimal_control_waveform.csv").read_bytes())
    with pytest.raises(ValueError, match="time_ms"):
        load_envelope(replace(params, arb_waveform_file="v2/waveforms/optimal_control_waveform.csv"), 16384)


def test_control_versions_require_explicit_selection(control_source):
    directory = control_sources.resolve_control_results_root("oc_sens")
    waveform = directory / control_source.arb_waveform_file
    for version in ("v2", "v10", "v_bad"):
        copy = directory / version / "waveforms/optimal_control_waveform.csv"
        copy.parent.mkdir(parents=True)
        copy.write_bytes(waveform.read_bytes())
    (directory / "v3").mkdir()
    defaults = RFSensitivityDirectAWParams()
    field = next(item for item in defaults.schema()["fields"] if item["name"] == "ARB_WAVEFORM_FILE")
    assert field["default"] == ""
    assert [option["label"] for option in field["options"]] == ["请选择最优控制版本", "v1", "v2", "v10"]
    assert [option["value"] for option in field["options"]][1:] == [f"{version}/waveforms/optimal_control_waveform.csv" for version in ("v1", "v2", "v10")]
    assert "请选择" in "；".join(defaults.validate())
    assert "ARB_WAVEFORM_FILE 必须是" in "；".join(replace(defaults, arb_waveform_file="control_waveform.csv").validate())
    with pytest.raises(ValueError, match="请选择"):
        load_envelope(defaults, 16384)
    # 带 # 表头的理论导出可直接读取，仍按原规则重采样。
    waveform.write_text("# " + waveform.read_text(encoding="utf-8"), encoding="utf-8")
    envelope = load_envelope(control_source, 16384)
    assert envelope["source_waveform_path"] == str(waveform.resolve())
    assert envelope["time_s"].size == 8000


def test_envelope_period_float_noise_within_tolerance(control_source):
    # 时间轴浮点噪声使包络频率落在整数倍下方仪容差之内时应当放行（v9 @3000 Hz 的实际场景）。
    directory = control_sources.resolve_control_results_root("oc_sens")
    period = 0.0005 * (1 + 5e-7)
    time_s = np.arange(8000) * (period / 8000)
    path = directory / "v_float" / "waveforms" / "optimal_control_waveform.csv"
    path.parent.mkdir(parents=True)
    np.savetxt(path, np.column_stack((time_s, np.full(8000, 100.0))), delimiter=",", header="time_s,Omega_ctrl_Hz", comments="")
    params = replace(control_source, arb_waveform_file="v_float/waveforms/optimal_control_waveform.csv")
    envelope = load_envelope(params, 16384)
    assert envelope["repeat_freq_Hz"] == pytest.approx(2000.0, rel=1e-6)


def test_point_limit_error_lists_available_frequencies(control_source):
    # 点数超限时报错应给出每源周期点数和可选重复频率，便于判断是否降低重复频率。
    with pytest.raises(ValueError, match="每个源周期 2000 点") as excinfo:
        load_envelope(replace(control_source, aw_repeat_freq=500.0), 16384)
    message = str(excinfo.value)
    assert "32000" in message
    assert "8000/N" in message
    assert "N≤8" in message
    assert "1000" in message
    with pytest.raises(ValueError, match="无可用重复频率"):
        load_envelope(replace(control_source, aw_repeat_freq=500.0), 1000)


@pytest.mark.parametrize("source", ["../outside.csv", "v1/../../../outside.csv"])
def test_control_source_rejects_path_escape(control_source, source):
    with pytest.raises(ValueError, match="必须位于 oc_sens"):
        load_envelope(replace(control_source, arb_waveform_file=source), 16384)
