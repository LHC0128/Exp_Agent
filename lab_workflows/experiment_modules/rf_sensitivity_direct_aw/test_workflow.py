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
from .stages import ACQUISITION
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
                           for index in range(self.capabilities["voltage_inputs"]) for mode in ("ac", "imp50")})

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
        if "/demods/0/" not in path:
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
    (tmp_path / "params").mkdir(exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        (tmp_path / "params" / name).write_bytes((root / "params" / name).read_bytes())
    # 假仪器固定为 HF2，临时快照不依赖用户当前选择的锁相型号。
    mapping_file = tmp_path / "params/mapping.yaml"
    config = yaml.safe_load(mapping_file.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        config["mapping"][key]["device_id"] = "hf2_dev18246"
    mapping_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    mapping = load_mapping(tmp_path)
    instance = SimpleNamespace(mapping=mapping, events=[], generators={}, xy_angle=0.0, xy_r=1.0, in_daq=False, in_shutdown=False, daq_error=None, shutdown_error=False, restore_error=False, cancel=False, z_phi0=37.0, z_scan_baseline=0.5, z_scan_amplitude=5.0, z_scan_phases=[], bw_gamma=300.0)
    instance.captures = []
    for resource in {config.get("resource") for config in mapping.values() if config["instrument"] == "signal_generator"}:
        instance.generators[resource] = FakeSignal(resource, instance)
    monkeypatch.setattr(routed_signal_generator, "create_signal_generator", lambda config: instance.generators[config["resource"]])
    monkeypatch.setattr(preparation, "GS200Instrument", lambda resource: Mock())
    monkeypatch.setattr(preparation, "create_lockin", lambda config: FakeHF2(instance, device_id=config["device_id"]))
    monkeypatch.setattr(preparation, "TECInstrument", lambda resource: Mock())
    monkeypatch.setattr(preparation, "configure_temperature_control", lambda *args, **kwargs: SimpleNamespace(to_dict=lambda: {"control_source": "tec103"}))
    monkeypatch.setattr(workflow, "create_run_directory", lambda data_type, tag, parameters, **kwargs: create_run_directory(data_type, tag, parameters, schema_version=RFSensitivityDirectAWParams.schema_version, project_root=tmp_path))
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
        z_config = instance.mapping["Z_magnetic_field"]
        z_device = instance.generators[z_config["resource"]]
        base = hf2.demod_path(kwargs["demod_idx"])
        instance.captures.append({"signals": tuple(config.signal_paths), "duration_s": config.duration,
            "rate": kwargs["actual_rate"], "time_constant": hf2.get_double(base + "/timeconstant"),
            "order": hf2.get_int(base + "/order"),
            "rf_frequency": z_device.frequency[z_config["channel"]],
            "demod_frequency": hf2.get_double(hf2.osc_path(hf2.get_int(base + "/oscselect")) + "/freq")})
        if config.signal_paths != ["sample.x"]:
            # 相位/幅度扫描：按 Z 通道当前幅度与 Burst 相位返回已知响应模型（相位 180° 表示负号）。
            z_config = instance.mapping["Z_magnetic_field"]
            z_device = instance.generators[z_config["resource"]]
            channel = z_config["channel"]
            phase = float(z_device.phase.get(channel, 0.0))
            amplitude = float(z_device.amplitude.get(channel, 0.0)) if z_device.output.get(channel, False) else 0.0
            if amplitude == instance.params.z_rf_amplitude_init:
                instance.z_scan_phases.append(phase)
            # 带宽扫频时响应随频率呈洛伦兹线形；中心频率（=当前 z_rf_freq）处因子为 1，不影响相位/幅度扫描断言。
            frequency = float(z_device.frequency.get(channel, 0.0))
            factor = instance.bw_gamma ** 2 / ((frequency - instance.params.z_rf_freq) ** 2 + instance.bw_gamma ** 2)
            response = complex(instance.z_scan_baseline) + instance.z_scan_amplitude * amplitude * factor * np.exp(1j * math.radians(phase - instance.z_phi0))
            columns = max(8, config.grid_cols)
            time = np.arange(columns) / kwargs["actual_rate"]
            kwargs["cancellation_check"]()
            return [DAQResult(signal_name=name, demod_index=kwargs["demod_idx"], values=np.full(columns, value), time=time) for name, value in (("sample.r", abs(response)), ("sample.x", response.real), ("sample.y", response.imag))]
        assert kwargs["actual_rate"] == 90000
        assert config.grid_cols == int(90000 * config.duration)
        assert kwargs["strict_cleanup"] is True
        if instance.daq_error:
            raise instance.daq_error
        kwargs["cancellation_check"]()
        return [DAQResult(signal_name="sample.x", demod_index=kwargs["demod_idx"], values=np.arange(100, dtype=float), time=np.arange(100) / 90000)]
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
    assert temp_switch_events == [0.0, *([5.0, 0.0] * 3)]
    after_scan_events = [
        event[3] for index, event in enumerate(rig.events)
        if scan_phase_indices[-1] < index < z_phase_indices[1 + len(rig.z_scan_phases)]
        and event[:3] == (temp["resource"], "setup_dc", temp["channel"])
    ]
    assert after_scan_events == [5.0, 5.0]
    # 幅度扫描与相位扫描节奏一致：3 点为一组（分组点数 5），组前关温控、组末恢复；噪声逐条开关。
    demod3_calib_end = z_phase_indices[1 + len(rig.z_scan_phases)]
    response_stage_events = [
        event[3] for index, event in enumerate(rig.events)
        if index > demod3_calib_end and event[:3] == (temp["resource"], "setup_dc", temp["channel"])
    ]
    assert response_stage_events == [0.0, 5.0, 0.0, 5.0, 5.0, 0.0, 5.0, 5.0]
    assert config["hf2_response_snapshot"]["/dev18246/demods/3/adcselect"] == 3
    assert config["hf2_response_snapshot"]["/dev18246/auxouts/1/demodselect"] == 0
    assert config["hf2_response_snapshot"]["/dev18246/auxouts/1/outputselect"] == 1
    assert config["hf2_noise_snapshot"]["/dev18246/sigins/1/range"] == 0.0
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


def test_bandwidth_node_rejects_dispersive_mode(rig):
    ctx = workflow.Workflow(rig.params)
    for stage in ACQUISITION:
        ctx.states[stage.id] = {"status": "completed", "error": ""}
        if stage.id == "A14":
            break
    with pytest.raises(ValueError, match="线宽模式"):
        ctx.execute(["A13B"])
    assert ctx.states["A13B"]["status"] == "failed"
    ctx.finish()


@pytest.mark.parametrize("points", [21, 49])
def test_bandwidth_sweep_measures_lorentzian_and_restores_frequency(rig, points):
    rig.params = replace(rig.params, z_rf_freq=3000.0, linewidth_mode="frequency_sweep", bw_freq_points=points)
    frequencies = np.linspace(2000, 4000, points)
    directory = workflow.run(rig.params)
    config, _ = saved_config(rig)
    assert config["completion_status"] == "completed"
    assert config["bandwidth_points_completed"] == points
    assert config["bandwidth_acquisition"]["amplitude_vpp"] == pytest.approx(rig.params.z_rf_amplitude_init)
    assert config["bandwidth_acquisition"]["frequency_start_Hz"] == pytest.approx(2000.0)
    assert config["bandwidth_acquisition"]["response_component"] == "R"
    noise_index = next(index for index, capture in enumerate(rig.captures) if capture["signals"] == ("sample.x",))
    bandwidth_captures = rig.captures[noise_index + 1:]
    assert len(bandwidth_captures) == rig.params.bw_freq_points
    for capture, frequency in zip(bandwidth_captures, frequencies):
        assert capture["rf_frequency"] == capture["demod_frequency"] == pytest.approx(frequency)
        assert capture["duration_s"] == rig.params.scan_point_duration_s
        for key in ("time_constant", "order", "rate"):
            assert capture[key] == rig.captures[0][key]
    base = f"/dev18246/demods/{rig.params.demod3_idx}"
    assert config["hf2_bandwidth_snapshot"][base + "/timeconstant"] == rig.params.demod3_tc
    assert config["hf2_noise_snapshot"][base + "/timeconstant"] == rig.params.demod3_noise_tc
    with np.load(directory / "raw/bandwidth_data.npz") as data:
        assert data["frequency_Hz"] == pytest.approx(frequencies)
        assert data["z_rf_amplitude_vpp"] == pytest.approx(rig.params.z_rf_amplitude_init)
        assert data["z_burst_phase_deg"] == pytest.approx(37.0)
        assert data["z_rf_freq_Hz"] == pytest.approx(3000.0)
        # 中心 3000 Hz 处 R 最大，并直接取每点 R 时序的均值。
        r = np.asarray(data["demod3_r_V"], dtype=float)
        center = points // 2
        assert int(np.argmax(r)) == center and r[0] < r[center] and r[-1] < r[center]
        assert r == pytest.approx(data["demod3_r_series_V"].mean(axis=1))
        assert data["demod3_r_std_V"] == pytest.approx(data["demod3_r_series_V"].std(axis=1, ddof=1))
        assert data["duration_s"] == rig.params.scan_point_duration_s
        assert data["actual_rate_Sa_s"] == config["actual_rates"]["demod3_bandwidth_Sa_s"]
    # 扫频后 Z 通道频率与实际设置逐点可追溯，且收尾恢复到 z_rf_freq。
    z = rig.mapping["Z_magnetic_field"]
    device = rig.generators[z["resource"]]
    assert device.frequency[z["channel"]] == pytest.approx(3000.0)
    swept = [event[3] for event in rig.events if event[:3] == (z["resource"], "set_frequency", z["channel"])]
    assert swept == pytest.approx([*frequencies, 3000.0])
    assert_shutdown(rig)


def test_bandwidth_requires_completed_noise_before_hardware(rig):
    ctx = workflow.Workflow(replace(rig.params, z_rf_freq=3000.0, linewidth_mode="frequency_sweep"))
    for stage in ACQUISITION:
        if stage.id == "A14":
            break
        ctx.states[stage.id]["status"] = "completed"
    with pytest.raises(ValueError, match="A14 零 RF 工作点 X 噪声采集"):
        ctx.execute(["A13B"])
    assert not rig.events and not ctx.devices


@pytest.mark.parametrize("change", ["rerun", "before_first", "after_first"])
def test_bandwidth_new_directory_inherits_response_and_noise(rig, change):
    rig.params = replace(rig.params, z_rf_freq=3000.0, linewidth_mode="frequency_sweep")
    ctx = workflow.Workflow(rig.params)
    ids = [stage.id for stage in ACQUISITION]
    ctx.execute(ids[:ids.index("A14") + 1])
    if change != "before_first":
        ctx.execute(["A13B"])
    previous = ctx.directory.root
    original = {path.name: path.read_bytes() for path in (previous / "raw").iterdir() if path.is_file()}
    noise_snapshot = dict(ctx.state["hf2_noise_snapshot"])
    noise_frequencies = dict(ctx.state["stage_frequencies"]["noise"])
    if change != "rerun":
        ctx.update_parameters({"BW_FREQ_POINTS": 11})
    ctx.execute(["A13B"])
    assert ctx.directory.root != previous
    assert all((previous / "raw" / name).read_bytes() == content for name, content in original.items())
    for name in ("response_data.npz", "noise_D3_0000.npz"):
        assert (ctx.directory.raw / name).read_bytes() == original[name]
        assert "raw/" + name in ctx.state["data_files"]
    assert ctx.state["noise_records_completed"] == rig.params.noise_n_avg
    assert ctx.state["hf2_noise_snapshot"] == noise_snapshot
    assert ctx.state["stage_frequencies"]["noise"] == noise_frequencies
    saved = yaml.safe_load(ctx.directory.config_path.read_text(encoding="utf-8"))
    assert saved["parameters"]["BW_FREQ_POINTS"] == (21 if change == "rerun" else 11)
    ctx.execute(["A15"])
    from .analysis import AnalysisWorkflow
    AnalysisWorkflow(ctx.directory.root).execute(["B01"])
    ctx.finish()


def test_bandwidth_uses_phase_scan_temperature_groups(rig, monkeypatch):
    rig.params = replace(rig.params, z_rf_freq=3000.0, linewidth_mode="frequency_sweep",
                         bw_freq_stop=3800.0, bw_freq_points=7, temp_group_size=3)
    ctx = workflow.Workflow(rig.params)
    ids = [stage.id for stage in ACQUISITION]
    ctx.execute(ids[:ids.index("A14") + 1])
    waits = []
    monkeypatch.setattr(acquisition, "wait", waits.append)
    point_rates = np.arange(7, dtype=float) + 900.0
    def acquire(hf2, config, **kwargs):
        rate = point_rates[ctx.state["bandwidth_points_completed"]]
        return [replace(result, config_snapshot={"actual_rate": rate}, time=np.arange(result.values.size) / rate)
                for result in rig.acquire(hf2, config, **kwargs)]
    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    ctx.execute(["A13B"])
    assert waits == [*([rig.params.system_settle_s] * 3), rig.params.temp_switch_on_lag_s,
        *([rig.params.system_settle_s] * 3), rig.params.temp_switch_on_lag_s,
        rig.params.system_settle_s, rig.params.temp_switch_on_lag_s]
    with np.load(ctx.directory.raw / "bandwidth_data.npz") as data:
        assert data["point_actual_rate_Sa_s"] == pytest.approx(point_rates)
        assert data["actual_rate_Sa_s"] == point_rates[-1]
    ctx.finish()


@pytest.mark.parametrize("cancel,restore_error", [(True, False), (False, False), (False, True)])
def test_bandwidth_failure_keeps_data_and_restores_without_hiding_primary(rig, monkeypatch, cancel, restore_error):
    rig.params = replace(rig.params, z_rf_freq=3000.0, linewidth_mode="frequency_sweep")
    ctx = workflow.Workflow(rig.params)
    ids = [stage.id for stage in ACQUISITION]
    ctx.execute(ids[:ids.index("A14") + 1])
    noise = (ctx.directory.raw / "noise_D3_0000.npz").read_bytes()
    primary = WorkflowCancelled("模拟带宽取消") if cancel else RuntimeError("模拟带宽 DAQ 失败")
    acquire = rig.acquire
    def fail(hf2, config, **kwargs):
        if ctx.state["bandwidth_points_completed"] == 1:
            raise primary
        return acquire(hf2, config, **kwargs)
    monkeypatch.setattr(acquisition.daq, "acquire_data", fail)
    if restore_error:
        original = phase.set_rf_frequency
        def restore(ctx, frequency):
            if frequency == ctx.params.z_rf_freq:
                raise RuntimeError("模拟扫频恢复失败")
            original(ctx, frequency)
        monkeypatch.setattr(phase, "set_rf_frequency", restore)
    with pytest.raises(type(primary)) as error:
        ctx.execute(["A13B"])
    assert error.value is primary
    z = rig.mapping["Z_magnetic_field"]
    device = rig.generators[z["resource"]]
    assert device.output[z["channel"]] is False
    temp = rig.mapping["Temp_Switch"]
    assert rig.generators[temp["resource"]].dc[temp["channel"]] == 5.0
    if restore_error:
        assert "模拟扫频恢复失败" in ctx.state["bandwidth_restore_errors"][0]
    else:
        assert device.frequency[z["channel"]] == rig.params.z_rf_freq
        assert ctx.devices["hf2"].get_double(ctx.devices["hf2"].osc_path(rig.params.demod3_osc_idx) + "/freq") == rig.params.z_rf_freq
    with np.load(ctx.directory.raw / "bandwidth_data.npz") as data:
        assert data["frequency_Hz"].shape == (1,)
    assert (ctx.directory.raw / "noise_D3_0000.npz").read_bytes() == noise
    ctx.finish(primary)
    assert ctx.state["failure_reason"] == str(primary)
    assert_shutdown(rig)


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
    assert stage_events == [0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 5.0, 0.0, 5.0, 5.0]


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


def test_parameter_waveform_and_mapping_preflight(control_source, rig, monkeypatch):
    from .definition import ADAPTER
    monkeypatch.setattr(ADAPTER, "root", rig.tmp_path)
    params = control_source
    assert not params.validate()
    assert "必须一致" in "；".join(replace(params, z_trigger_freq=200).validate())
    assert "正整数倍" in "；".join(replace(params, xy_trigger_freq=300, z_trigger_freq=300).validate())
    assert not replace(params, demod3_idx=2, demod3_adc_select=2).validate()
    assert replace(params, demod3_idx=0).validate()
    assert "安全拦截" in "；".join(replace(params, xy_trigger_amplitude=50, z_trigger_amplitude=50).validate())
    assert "整除" in "；".join(replace(params, z_phase_scan_step_deg=25).validate())
    assert "360° 周期" in "；".join(replace(params, z_phase_scan_stop_deg=320).validate())
    definition = get_experiment("rf-sensitivity-aw")
    assert "X_magnetic_field_AM" not in definition.required_mapping_keys
    schema = definition.schema()
    assert schema["schema_version"] == RFSensitivityDirectAWParams.schema_version
    assert not any(field["name"].startswith("Z_TRIGGER_") for field in schema["fields"])
    assert not any(field["name"] == "DEMOD3_ADC_SELECT" for field in schema["fields"])
    assert next(field for field in schema["fields"] if field["name"] == "DEMOD3_IDX")["read_only"] is False
    assert definition.preflight(params.to_external()) == []
    bandwidth_params = replace(params, linewidth_mode="frequency_sweep", z_rf_freq=3000.0, bw_freq_points=49)
    assert definition.preflight(bandwidth_params.to_external()) == []
    for key, value in (("BW_FREQ_START", 2001.0), ("BW_FREQ_STOP", 3999.0)):
        assert f"{key} 必须是共同触发频率的正整数倍" in definition.preflight(bandwidth_params.to_external() | {key: value})
    # 旧外部键自动迁移：稳定等待/采样时长改名，响应平均键剥离。
    legacy = RFSensitivityDirectAWParams.from_external({
        "RF_SETTLE_TIME": 0.12, "XY_PHASE_SETTLE_TIME": 0.6, "Z_PHASE_SCAN_DURATION_S": 0.3,
        "RESPONSE_N_AVG": 5, "RESPONSE_AVG_INTERVAL": 0.2,
        **{key: value for key, value in params.to_external().items() if key != "SYSTEM_SETTLE_S"}})
    assert legacy.system_settle_s == 0.6
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


@pytest.mark.parametrize("stage,group_size", [("A11", 5), ("A13", 2), ("A13B", 3)])
def test_real_group_config_settle_restore_and_slow_disk_order(rig, monkeypatch, stage, group_size):
    """实际相位/幅度/带宽业务：组首配置在开启时，慢写盘不占关闭窗口。"""
    rig.params = replace(rig.params, temp_group_size=group_size, linewidth_mode="frequency_sweep",
                         z_rf_freq=3000.0, bw_freq_stop=3800.0, bw_freq_points=7)
    ctx = workflow.Workflow(rig.params)
    ids = [item.id for item in ACQUISITION]
    ctx.execute(ids[:ids.index(stage)])
    enabled, clock, windows, configured, captured = [True], [0.0], [], [], []
    events = []
    real_switch = _helpers.set_temperature_switch
    def switch(device, value, **kwargs):
        real_switch(device, value, **kwargs)
        if not value:
            windows.append([clock[0], None])
        elif not enabled[0]:
            windows[-1][1] = clock[0]
        enabled[0] = value
        events.append("on" if value else "off")
    for module in (phase, acquisition, _helpers):
        monkeypatch.setattr(module, "set_temperature_switch", switch)
    def wait(seconds):
        if not enabled[0]:
            assert seconds == rig.params.system_settle_s
            events.append("settle")
        clock[0] += seconds
    monkeypatch.setattr(phase, "wait", wait)
    monkeypatch.setattr(acquisition, "wait", wait)
    real_z = excitation.set_z_rf
    def configure(*args, **kwargs):
        if len(configured) % group_size == 0:
            assert enabled[0], "分组首点必须在关闭前完成实际仪器配置"
        configured.append(len(captured))
        events.append("configure")
        return real_z(*args, **kwargs)
    monkeypatch.setattr(phase, "set_z_rf", configure)
    monkeypatch.setattr(acquisition, "set_z_rf", configure)
    real_acquire = rig.acquire
    def acquire(*args, **kwargs):
        assert not enabled[0] and events[-1] == "settle"
        captured.append(clock[0])
        events.append("acquire")
        result = real_acquire(*args, **kwargs)
        clock[0] += 1
        return result
    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    real_save = np.savez
    def save(*args, **kwargs):
        assert enabled[0], "波形/快照不能在关闭窗口写盘"
        events.append("save")
        clock[0] += 30
        return real_save(*args, **kwargs)
    monkeypatch.setattr(np, "savez", save)
    real_update = type(ctx.directory).update_config
    def update(directory, **kwargs):
        assert enabled[0], "采集元数据不能在关闭窗口写盘"
        return real_update(directory, **kwargs)
    monkeypatch.setattr(type(ctx.directory), "update_config", update)
    ctx.execute([stage])
    assert len(captured) == (18 if stage == "A11" else 3 if stage == "A13" else 7)
    sizes = [min(group_size, len(captured) - start) for start in range(0, len(captured), group_size)]
    assert [end - start for start, end in windows] == pytest.approx([
        size * (rig.params.system_settle_s + 1) for size in sizes])
    assert enabled[0] and "save" in events
    ctx.finish()
