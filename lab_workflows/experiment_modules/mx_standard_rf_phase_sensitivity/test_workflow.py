"""标准 Mx 相图与噪声路径的假仪器回归：真实编排、当前格式与安全路径；不连接仪器。

假仪器只替换硬件边界（连接、时钟、DAQ 取样与温度开关回读），业务编排、帧体检、
圆拟合、安全收尾与离线分析都跑真实实现。
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled, load_mapping
from ...experiment_runtime import PROGRESS_PREFIX, PROGRESS_PROTOCOL_ENV
from ...steps import RoutedSignalGenerator, SignalGeneratorRoute
from ...steps.coil_drive_chain import SafetyAbort
from ..z_chain_common import welch_psd
from . import acquisition, analysis, analysis_steps, preparation, run_format, workflow
from .definition import ADAPTER
from .models import (MOD_CARRIER_OSC_IDX, MOD_SIDEBAND_OSC_IDXS, READOUT_DEMOD_IDX, READOUT_OSC_IDX,
                     MxStandardRfPhaseSensitivityParams)
from .workflow import Workflow

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity"
# 合成相图：|S| 恒为 RADIUS_V，相位角 = 指令相位 + PHASE_OFFSET_DEG；噪声为 30 uV 白噪声。
# 边带开启时把调相深度按 1/sqrt(1 + (f/FREQ_CORNER_HZ)²) 衰减，模拟基带频响滚降。
RADIUS_V = 16.4e-3
NOISE_V = 3.0e-5
PHASE_OFFSET_DEG = 2.9
TONE_HZ = 200.0
FREQ_CORNER_HZ = 85.0

ACQUISITION_CASES = {"mx-standard-rf-phase-sensitivity": {"mode": "single", "cases": {
    "order": ["test_full_round_recovers_circle_and_working_point",
              "test_a05_sweeps_every_pm_frequency_in_wideband",
              "test_a06_noise_keeps_wideband_and_finish_closes_pm"],
    "slow_disk": ["test_full_round_recovers_circle_and_working_point"],
    "cancel": ["test_cancel_restores_temperature_and_keeps_frames"],
    "acquire_failure": ["test_frame_quality_rejects_tone_frame", "test_short_frame_is_rejected"],
    "restore_failure": ["test_restore_failure_keeps_original_error"],
    "save_failure": ["test_save_failure_keeps_previous_frames"],
    "partial_save": ["test_cancel_restores_temperature_and_keeps_frames",
                     "test_save_failure_keeps_previous_frames"],
    "entrypoints": ["test_entrypoint_subprocess_full_round"]},
    "sources": ["lab_workflows/experiment_modules/mx_standard_rf_phase_sensitivity/preparation.py",
                "lab_workflows/experiment_modules/mx_standard_rf_phase_sensitivity/acquisition.py",
                "lab_workflows/experiment_modules/mx_standard_rf_phase_sensitivity/workflow.py",
                "lab_workflows/experiment_modules/mx_standard_rf_phase_sensitivity/session.py",
                "experiments/MxStandardRfPhaseSensitivity.py"]}}
REGRESSION_CASES = {"mx-standard-rf-phase-sensitivity": {
    "parameters": {
        "sources": ["lab_workflows/experiment_modules/mx_standard_rf_phase_sensitivity/models.py",
                    "params/experiments/mx-standard-rf-phase-sensitivity.yaml"],
        "tests": ["test_preflight_and_defaults", "test_main_field_current_follows_calibration"]},
    "analysis": {
        "sources": ["lab_workflows/experiment_modules/mx_standard_rf_phase_sensitivity/analysis_steps.py",
                    "lab_workflows/experiment_modules/mx_standard_rf_phase_sensitivity/analysis.py",
                    "lab_workflows/experiment_modules/z_chain_common/tone_fit.py",
                    "lab_workflows/experiment_modules/z_chain_common/transfer.py",
                    "lab_workflows/experiment_modules/z_chain_common/spectrum_plot.py",
                    "experiments/MxStandardRfPhaseSensitivity_plot.py"],
        "tests": ["test_full_round_recovers_circle_and_working_point",
                  "test_noise_theta_route_isolates_carrier_image",
                  "test_transfer_fit_uses_shared_leakage_model"]}}}


class FakeGenerator:
    """保留真实驱动的读写分离；setup_dc 只写 DC 电平与波形，不改变输出开关。"""

    def __init__(self, events, name):
        self.events, self.name = events, name
        self.channels = {channel: dict(output=False, shape="DC", frequency=5000.0, amplitude=0.05,
                                       offset=0.0, dc_level=0.0, output_load="INF", voltage_unit="VPP",
                                       burst_state=False, mod_state=False, sync_state=False,
                                       square_dcycle=50.0, burst_phase=0.0)
                         for channel in (1, 2)}
        # 温控恢复失败注入：正整数表示后续恢复次数，None 表示不注入。
        self.restore_failures: int | None = None

    # ---- 开关与波形 ----
    def set_output(self, value, *, channel):
        self.events.append((self.name, channel, "set_output", bool(value)))
        self.channels[channel]["output"] = bool(value)

    def get_output(self, *, channel):
        return self.channels[channel]["output"]

    def set_shape(self, value, *, channel):
        self.channels[channel]["shape"] = str(value)

    def get_shape(self, *, channel):
        return self.channels[channel]["shape"]

    def set_offset(self, value, *, channel):
        self.channels[channel]["offset"] = float(value)

    def get_offset(self, *, channel):
        return self.channels[channel]["offset"]

    def get_dc_voltage(self, *, channel):
        return self.channels[channel]["dc_level"]

    def setup_dc(self, value, *, channel):
        if self.name == "temp" and value == 5.0 and self.restore_failures:
            self.restore_failures -= 1
            raise IOError("注入温控恢复失败")
        self.channels[channel].update(shape="DC", dc_level=float(value), offset=float(value))

    def setup_square(self, *, freq, amplitude, offset, dcycle, phase, channel):
        self.channels[channel].update(shape="SQUare", frequency=float(freq), amplitude=float(amplitude),
                                      offset=float(offset), square_dcycle=float(dcycle))

    def get_frequency(self, *, channel):
        return self.channels[channel]["frequency"]

    def get_amplitude(self, *, channel):
        return self.channels[channel]["amplitude"]

    def get_square_dcycle(self, *, channel):
        return self.channels[channel]["square_dcycle"]

    def set_frequency(self, value, *, channel):
        self.channels[channel]["frequency"] = float(value)

    def set_amplitude(self, value, *, channel):
        self.channels[channel]["amplitude"] = float(value)

    def set_burst_state(self, value, *, channel):
        self.channels[channel]["burst_state"] = bool(value)

    def get_burst_state(self, *, channel):
        return self.channels[channel]["burst_state"]

    def set_mod_state(self, value, *, channel):
        self.channels[channel]["mod_state"] = bool(value)

    def get_mod_state(self, *, channel):
        return self.channels[channel]["mod_state"]

    def set_sync_state(self, value, *, channel):
        self.channels[channel]["sync_state"] = bool(value)

    def get_sync_state(self, *, channel):
        return self.channels[channel]["sync_state"]

    def set_burst_phase(self, value, *, channel):
        self.channels[channel]["burst_phase"] = float(value)

    def get_burst_phase(self, *, channel):
        return self.channels[channel]["burst_phase"]

    def set_burst_mode(self, value, *, channel):
        self.channels[channel]["burst_mode"] = str(value)

    def set_burst_ncycles(self, value, *, channel):
        self.channels[channel]["burst_ncycles"] = value

    def set_burst_trigger_source(self, value, *, channel):
        self.channels[channel]["burst_trigger_source"] = str(value)

    def set_burst_trigger_slope(self, value, *, channel):
        self.channels[channel]["burst_trigger_slope"] = str(value)

    def set_output_load(self, value, *, channel):
        self.channels[channel]["output_load"] = value

    def get_output_load(self, *, channel):
        return self.channels[channel]["output_load"]

    def phase_init(self, *, channel):
        pass

    def wait_for_operation_complete(self):
        pass

    def raise_for_errors(self):
        pass

    def get_ref_clock_source(self):
        return "EXTernal"

    def idn(self):
        return f"RIGOL,DG4162,{self.name},FAKE"

    def query(self, command):
        if "COUPling" in command:
            return "OFF,OFF,OFF"
        if command.endswith(":STATe?") and ":OUTPut" in command:
            channel = int(command.split(":OUTPut", 1)[1].split(":", 1)[0])
            return "1" if self.channels[channel]["output"] else "0"
        if command.endswith(":POLarity?"):
            return "NORM"
        raise AssertionError(f"未预期的查询：{command}")

    def connect(self):
        self.events.append((self.name, "connect"))

    def disconnect(self):
        self.events.append((self.name, "disconnect"))


class FakeGS200:
    """GS200 电流源：记录运行前状态，供收尾恢复核对。"""

    def __init__(self):
        self.state = dict(source_function="CURR", current=-0.001, output=True,
                          current_range=0.01, current_limit=0.02)
        self.writes = []
        self.connected = True

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def _live(self):
        if not self.connected:
            raise IOError("未连接到 GS200，请先调用 connect()")

    def get_source_function(self):
        self._live()
        return self.state["source_function"]

    def set_source_function(self, value):
        self._live()
        self.state["source_function"] = str(value)

    def get_current(self):
        self._live()
        return self.state["current"]

    def set_current(self, value):
        self._live()
        self.writes.append(float(value))
        self.state["current"] = float(value)

    def get_output(self):
        self._live()
        return self.state["output"]

    def set_output(self, value):
        self._live()
        self.state["output"] = bool(value)

    def get_current_range(self):
        self._live()
        return self.state["current_range"]

    def set_current_range(self, value):
        self._live()
        self.state["current_range"] = float(value)

    def get_current_limit(self):
        self._live()
        return self.state["current_limit"]

    def set_current_limit(self, value):
        self._live()
        self.state["current_limit"] = float(value)


class FakeTEC:
    """TEC 只接收目标温度；不启停、不判稳，串口失败由连接步骤记录。"""

    def __init__(self):
        self.target_c = None

    def connect(self):
        pass

    def disconnect(self):
        pass

    def set_target_temperature(self, value, channel=1):
        self.target_c = float(value)

    def get_temperature(self, channel=1):
        return 90.0


class FakeLockin:
    """按节点路径读写，复现 MF-MOD 载波、Demod 4 静态读出与收尾归零。"""

    model = "MFLI"
    device_id = "dev30661"
    acquisition_mode = "daq"

    def __init__(self, events):
        self.events = events
        self.connected = True
        self.capabilities = {
            "model": "MFLI",
            "options": ["MF-F5M", "MF-MD", "MF-MOD", "MF-DIG"],
            "demodulators": 4,
            "oscillators": 4,
            "voltage_inputs": 1,
            "signal_outputs": 1,
            "aux_outputs": 4,
            "max_frequency_hz": 5e6,
        }
        dev = self.device_id
        self.values = {}
        for index in range(4):
            self.values.update({
                f"/{dev}/oscs/{index}/freq": 0.0,
                f"/{dev}/demods/{index}/phaseshift": 0.0,
                f"/{dev}/demods/{index}/timeconstant": 0.0,
                f"/{dev}/demods/{index}/rate": 0.0,
                f"/{dev}/demods/{index}/order": 0,
                f"/{dev}/demods/{index}/adcselect": 0,
                f"/{dev}/demods/{index}/oscselect": 0,
                f"/{dev}/demods/{index}/harmonic": 0,
                f"/{dev}/demods/{index}/enable": 0,
            })
        self.values.update({
            f"/{dev}/sigins/0/range": 1.0,
            f"/{dev}/sigins/0/ac": 0,
            f"/{dev}/sigins/0/diff": 0,
            f"/{dev}/sigins/0/imp50": 0,
            f"/{dev}/sigouts/0/on": 0,
            f"/{dev}/sigouts/0/add": 0,
            f"/{dev}/sigouts/0/range": 1.0,
            f"/{dev}/sigouts/0/offset": 0.0,
            f"/{dev}/mods/0/enable": 0,
            f"/{dev}/mods/0/mode": 1,
            f"/{dev}/mods/0/freqdevenable": 0,
            f"/{dev}/mods/0/output": 0,
            f"/{dev}/mods/0/index": 0.0,
            f"/{dev}/mods/0/carrier/enable": 0,
            f"/{dev}/mods/0/carrier/oscselect": 0,
            f"/{dev}/mods/0/carrier/amplitude": 0.0,
            f"/{dev}/mods/0/carrier/phaseshift": 0.0,
            f"/{dev}/mods/0/sidebands/0/enable": 0,
            f"/{dev}/mods/0/sidebands/1/enable": 0,
            f"/{dev}/mods/0/sidebands/0/oscselect": 0,
            f"/{dev}/mods/0/sidebands/1/oscselect": 0,
        })

    def demod_path(self, index):
        return f"/{self.device_id}/demods/{index}"

    def osc_path(self, index):
        return f"/{self.device_id}/oscs/{index}"

    def sig_in_path(self, index):
        return f"/{self.device_id}/sigins/{index}"

    def set_double(self, path, value):
        self._live()
        self.values[path] = float(value)

    def get_double(self, path):
        self._live()
        return self.values[path]

    def set_int(self, path, value):
        self._live()
        self.values[path] = int(value)

    def get_int(self, path):
        self._live()
        return self.values[path]

    def sync(self):
        self._live()

    def get_extclk(self):
        self._live()
        return True

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def _live(self):
        if not self.connected:
            raise RuntimeError("未连接到数据服务器，请先调用 connect()")

    @property
    def command_deg(self):
        return float(self.values[f"/{self.device_id}/mods/0/carrier/phaseshift"])


def fast_params(**overrides):
    """缩短等待与采样时长；相图 8 点、细扫 11 点、调相 2 档、噪声 2 段。"""
    base = MxStandardRfPhaseSensitivityParams(
        run_tag="mx_std_test", phase_grid_step_deg=45.0, point_duration_s=0.1,
        noise_segments=2, noise_duration_s=1.0, welch_segment_s=1.0,
        system_settle_s=0.1, temp_switch_on_lag_s=0.1, dynamic_temp_switch_on_lag_s=0.1,
        demod_dynamic_rate_sa_s=2000.0, pm_frequencies_hz=[3.0, 40.0, 80.0], pm_tone_duration_s=4.0)
    return replace(base, **overrides)


def install_rig(root: Path, monkeypatch, params=None):
    """在无硬件环境下装配假仪器并替换硬件边界；返回可断言的 rig。"""
    params = params or fast_params()
    (root / "params").mkdir(parents=True, exist_ok=True)
    if not (root / "params" / "mapping.yaml").is_file():
        shutil.copytree(ROOT / "params", root / "params", dirs_exist_ok=True)
    monkeypatch.setenv("LAB_DATA_ROOT", str(root / "data"))
    events = []
    zgen, xgen, tempgen = (FakeGenerator(events, name) for name in ("z", "x", "temp"))
    lockin = FakeLockin(events)
    gs200 = FakeGS200()
    tec = FakeTEC()
    channels = dict(z=1, trigger=2, x=3, temp=4)
    proxy = RoutedSignalGenerator({index: SignalGeneratorRoute(device, channel, name)
                                   for index, (name, device, channel) in enumerate((
                                       ("z", zgen, 1), ("trigger", zgen, 2), ("x", xgen, 1), ("temp", tempgen, 2)),
                                   1)})
    devices = dict(signals=proxy, channels=channels, hf2=lockin, gs200=gs200, tec=tec)

    def connect(ctx):
        preparation.require_mfli_capabilities(lockin.capabilities)
        for name, device in (("z", zgen), ("x", xgen), ("temp", tempgen), ("hf2", lockin),
                             ("gs200", gs200), ("tec", tec)):
            ctx.device_session.connect(name, name, lambda device=device: device)
        ctx.devices.update(devices)

    rnd = np.random.default_rng(20261127)
    dev = lockin.device_id
    rig = SimpleNamespace(root=root, params=params, events=events, z=zgen, x=xgen, temp=tempgen,
                          lockin=lockin, gs=gs200, tec=tec, reads=0, tone_v=0.0, tone_hz=TONE_HZ,
                          acquisitions=[],
                          short_frame=False, cancel_after=None, cancel_file=None, daq_error=None,
                          arm_restore_failure=False)

    def collect(instr, config, index, rate, **kwargs):
        assert index == READOUT_DEMOD_IDX
        assert kwargs["strict_cleanup"] and config.grid_mode == 4
        tc_now = lockin.values[f"/{dev}/demods/{READOUT_DEMOD_IDX}/timeconstant"]
        expected_rate = (params.demod_dynamic_rate_sa_s
                         if math.isclose(tc_now, params.demod_dynamic_tc_s, rel_tol=1e-6)
                         else params.demod_rate_sa_s)
        assert kwargs["requested_rate"] == pytest.approx(expected_rate)
        assert tempgen.channels[2]["dc_level"] == 0.0, "采集窗口内温控必须处于关闭电平"
        rig.acquisitions.append(dict(rate_sa_s=float(rate), time_constant_s=float(tc_now),
                                     sidebands_on=bool(lockin.values[f"/{dev}/mods/0/sidebands/0/enable"]
                                                       and lockin.values[f"/{dev}/mods/0/sidebands/1/enable"]),
                                     carrier_deg=float(lockin.command_deg),
                                     pm_hz=float(lockin.values[f"/{dev}/oscs/{MOD_SIDEBAND_OSC_IDXS[0]}/freq"])))
        rig.reads += 1
        if rig.cancel_after is not None and rig.cancel_file is not None and rig.reads >= rig.cancel_after:
            rig.cancel_file.touch()
        if rig.arm_restore_failure:
            # 采集已开始时才注入温控恢复失败，避免打在 A02 的首次开启温控上。
            tempgen.restore_failures = 99
            rig.arm_restore_failure = False
        if rig.daq_error is not None:
            raise rig.daq_error
        cols = 8 if rig.short_frame else config.grid_cols
        t = np.arange(cols) / rate
        theta = np.deg2rad(lockin.command_deg + PHASE_OFFSET_DEG)
        # A05 边带开启时，把 2° 峰值调相按基带一阶滚降叠加到载波相位上。
        sidebands_on = (lockin.values[f"/{dev}/mods/0/sidebands/0/enable"]
                        and lockin.values[f"/{dev}/mods/0/sidebands/1/enable"])
        if lockin.values[f"/{dev}/mods/0/enable"] and sidebands_on:
            fm_hz = lockin.values[f"/{dev}/oscs/{MOD_SIDEBAND_OSC_IDXS[0]}/freq"]
            depth_rad = lockin.values[f"/{dev}/mods/0/index"]
            theta = theta + depth_rad * np.sin(2 * np.pi * fm_hz * t) / np.sqrt(1 + (fm_hz / FREQ_CORNER_HZ) ** 2)
        z = RADIUS_V * np.exp(1j * theta)
        x = z.real + rnd.normal(0, NOISE_V, cols)
        y = z.imag + rnd.normal(0, NOISE_V, cols)
        if rig.tone_v:
            x = x + rig.tone_v * np.sin(2 * np.pi * rig.tone_hz * t)
        snaps = dict(actual_rate=rate, time_source="module_timestamp", acquisition_module="daq", unit="V")
        return [SimpleNamespace(signal_name=f"sample.{key}", values=signal, time=t,
                                record_slices=[(0, cols)], config_snapshot=snaps.copy())
                for key, signal in (("x", x), ("y", y))]

    monkeypatch.setattr(preparation, "_connect_devices", connect)
    monkeypatch.setattr(preparation, "clocks", lambda ctx: {"fake": "EXT"})
    monkeypatch.setattr(preparation, "wait", lambda seconds: events.append(("wait", float(seconds))))
    monkeypatch.setattr(acquisition.daq, "acquire_data", collect)
    monkeypatch.setenv(PROGRESS_PROTOCOL_ENV, "jsonl")
    return rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return install_rig(tmp_path, monkeypatch)


def copy_config(root: Path) -> None:
    """把仓库 params/ 复制到临时根，供设备库与映射解析。"""
    shutil.copytree(ROOT / "params", root / "params", dirs_exist_ok=True)


def run_config(run_dir: Path) -> dict:
    return yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))


def test_preflight_and_defaults():
    p = MxStandardRfPhaseSensitivityParams.from_yaml(ROOT / "params/experiments/mx-standard-rf-phase-sensitivity.yaml")
    assert p == MxStandardRfPhaseSensitivityParams()
    assert p.demod_tc_s == pytest.approx(0.001) and p.demod_rate_sa_s == pytest.approx(5000.0)
    # A05/A06 宽带档与调相扫频默认值：与 Mx 脉冲射频相位灵敏度实验完全一致。
    assert p.demod_dynamic_tc_s == pytest.approx(1e-6) and p.demod_dynamic_rate_sa_s == pytest.approx(50000.0)
    assert p.dynamic_temp_switch_on_lag_s == pytest.approx(4.0)
    assert p.pm_frequencies_hz == ([float(f) for f in range(3, 204, 10)]
                                   + [float(f) for f in range(250, 1001, 50)])
    assert p.pm_tone_duration_s == pytest.approx(4.0)
    assert p.y_pm_depth_rad == pytest.approx(math.radians(2.0))
    assert p.demod_idx == READOUT_DEMOD_IDX and p.phase_grid_step_deg == pytest.approx(15.0)
    assert p.noise_segments == 4 and p.noise_duration_s == pytest.approx(4.0)
    assert p.welch_segment_s == pytest.approx(4.0) and p.system_settle_s == pytest.approx(0.5)
    assert p.cal_k_hz_per_ma == pytest.approx(11089.0) and p.cal_b_hz == pytest.approx(219.7)
    # 默认 K/b 下 5 kHz 对应 0.4311 mA，落在 main_magnetic_field 限值内。
    assert p.main_field_current_ma == pytest.approx((5000.0 - 219.7) / 11089.0)
    assert len(p.phase_grid_deg) == 24 and p.phase_grid_deg[-1] == pytest.approx(345.0)
    assert ADAPTER.preflight({}) == []
    # 参数语义与安全边界
    assert ADAPTER.preflight({"PHASE_GRID_STEP_DEG": 7.0})
    assert ADAPTER.preflight({"PHASE_GRID_STEP_DEG": 60.0}) == []
    assert ADAPTER.preflight({"PHASE_GRID_STEP_DEG": 90.0})
    assert ADAPTER.preflight({"DEMOD_IDX": 0})
    assert ADAPTER.preflight({"Y_RF_VPP": 2.0, "Y_BIAS_V": 9.5})
    assert ADAPTER.preflight({"Y_RF_VPP": 2.0, "Y_BIAS_V": 1.0}) == []
    assert ADAPTER.preflight({"CAL_K_HZ_PER_MA": 0.0})
    assert ADAPTER.preflight({"CAL_K_HZ_PER_MA": 200.0, "CAL_B_HZ": 0.0})  # 25 mA 超出 ±20 mA
    assert ADAPTER.preflight({"WELCH_SEGMENT_S": 4.0, "NOISE_DURATION_S": 1.0})
    assert ADAPTER.preflight({"DEMOD_RATE": 1000.0, "POINT_DURATION_S": 0.05})
    # 调相频率表、周期覆盖与 A05/A06 宽带档的参数语义。
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [3.0, 103.0, 203.0]}) == []
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [5.0, 5.0, 20.0]})
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [10.0, 1100.0]})
    assert ADAPTER.preflight({"PM_FREQUENCIES_HZ": [3.0, 103.0, 203.0], "PM_TONE_DURATION_S": 2.0})
    assert ADAPTER.preflight({"DEMOD_DYNAMIC_RATE": 500.0})
    assert ADAPTER.preflight({"DEMOD_DYNAMIC_RATE": 5000.0})
    assert ADAPTER.preflight({"DEMOD_DYNAMIC_RATE": 20000.0}) == []
    assert ADAPTER.preflight({"SYSTEM_SETTLE_S": 0.101, "DEMOD_TC": 0.01})
    # v1 历史快照缺新键时按默认值补齐，仍可离线读取。
    legacy = p.to_external()
    for key in ("DEMOD_DYNAMIC_TC", "DEMOD_DYNAMIC_RATE", "PM_FREQUENCIES_HZ", "PM_TONE_DURATION_S",
                "DYNAMIC_TEMP_SWITCH_ON_LAG_S"):
        legacy.pop(key)
    migrated = MxStandardRfPhaseSensitivityParams.from_external(legacy, schema_version=1)
    assert migrated.pm_tone_duration_s == pytest.approx(4.0) and len(migrated.pm_frequencies_hz) == 37
    assert migrated.demod_dynamic_tc_s == pytest.approx(1e-6) and migrated.validate_model() == []
    assert ADAPTER.preflight({"TARGET_TEMPERATURE_C": 200.0}) == []
    assert ADAPTER.preflight({"SET_TEC_TARGET": True, "TARGET_TEMPERATURE_C": 200.0})
    fields = MxStandardRfPhaseSensitivityParams().schema(ROOT)["fields"]
    from . import stages
    owned = {key for stage in (*stages.ACQUISITION, *stages.ANALYSIS)
             for key in (stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    assert owned == {field["name"] for field in fields}


def test_main_field_current_follows_calibration(tmp_path, monkeypatch, capsys):
    """K/b 改变时 GS200 电流按标定式变化，且越界值在预检阶段被拒绝。"""
    for k_hz_per_ma, b_hz in ((11089.0, 219.7), (9000.0, 100.0)):
        root = tmp_path / f"k{k_hz_per_ma:g}"
        rig = install_rig(root, monkeypatch, fast_params(cal_k_hz_per_ma=k_hz_per_ma, cal_b_hz=b_hz))
        expected_ma = (rig.params.y_rf_freq_hz - b_hz) / k_hz_per_ma
        ctx = Workflow(rig.params, project_root=rig.root)
        ctx.execute(["A01", "A02"])
        try:
            assert rig.gs.writes == [pytest.approx(expected_ma / 1000.0)]
            assert rig.gs.state["output"] is True
            config = run_config(ctx.directory.root)
            assert config["main_field_current_ma"] == pytest.approx(expected_ma)
            assert config["calibration_formula"]["main_field_current_ma"] == pytest.approx(expected_ma)
            assert config["z_pulse_chain"]["z_source_output"] is False
            assert rig.temp.channels[2]["dc_level"] == pytest.approx(5.0)
        finally:
            ctx.finish(None)
        assert rig.gs.state["output"] is True and rig.gs.state["current"] == pytest.approx(-0.001)
    with pytest.raises(ValueError, match="main_magnetic_field"):
        Workflow(replace(MxStandardRfPhaseSensitivityParams(), cal_k_hz_per_ma=200.0, cal_b_hz=0.0),
                 project_root=ROOT)


def test_full_round_recovers_circle_and_working_point(rig, capsys):
    run_dir = Workflow.run(rig.params, project_root=rig.root)
    config = run_config(run_dir)
    diagram = config["phase_diagram"]
    # 预热帧保存但丢弃：不进相图点清单；末尾回采首点给出整圈漂移。
    assert (run_dir / "raw" / "mx_std_warmup_00.npz").is_file()
    assert diagram["warmup_label"] == "mx_std_warmup_00" and "mx_std_warmup_00" not in diagram["labels"]
    assert diagram["labels"][0] == "mx_std_coarse_00" and diagram["repeat_label"] == "mx_std_repeat_00"
    assert len(diagram["labels"]) == 8 and diagram["grid_deg"][-1] == pytest.approx(315.0)
    assert diagram["first_point_drift_v"] < RADIUS_V * 0.5
    assert diagram["settle"]["reads"] >= 2 and diagram["settle"]["stable"] is True
    fit = diagram["circle_fit"]
    assert fit["radius_v"] == pytest.approx(RADIUS_V, rel=0.02)
    assert fit["rms_residual_v"] < RADIUS_V * 0.01
    assert diagram["tangent_over_radius"] == pytest.approx(1.0, abs=0.02)
    assert diagram["accepted_count"] == diagram["accepted_total"] == 8
    # 工作点：圆模型取 |cos phi| 最大的相图点；圆上 0° 与 180° 两处同样取极大，两个候选都合法。
    chosen = diagram["coarse_candidate"]
    assert chosen["cos_phi"] > 0.99 and chosen["command_deg"] in (0.0, 180.0)
    working = config["working_point"]
    analytic = RADIUS_V * abs(math.cos(math.radians(working["phasor_angle_deg"])))
    assert abs(working["local_slope_v_per_rad"]) == pytest.approx(analytic, rel=0.05)
    assert abs(working["analytic_slope_v_per_rad"]) == pytest.approx(analytic, rel=0.05)
    angle = abs(working["phasor_angle_deg"])
    assert min(angle, 180.0 - angle) <= 1.0
    assert abs(working["command_deg"] - chosen["command_deg"]) <= 5.0 + 1e-9
    assert config["noise"]["segments"] == 2 and config["noise"]["demod_dynamic"] is True
    assert config["noise"]["time_constant_s"] == pytest.approx(1e-6, rel=0.01)
    # A05 每档调相频率各出一帧，且宽带档时间常数/传输率回读写入配置。
    transfer = config["transfer"]
    assert [row["frequency_hz"] for row in transfer] == [3.0, 40.0, 80.0]
    assert all(row["accepted"] and row["xy_label"] for row in transfer)
    assert len({row["xy_label"] for row in transfer}) == 3
    assert config["dynamic_lockin"]["time_constant_s"] == pytest.approx(1e-6, rel=0.01)
    assert config["pm_alignment"]["index_rad"] == pytest.approx(math.radians(2.0))
    assert config["completion_status"] == "completed"
    raw = sorted(path.name for path in (run_dir / "raw").glob("*.npz"))
    assert len(raw) == 2 + 1 + 8 + 1 + 11 + 3 + 2
    events = [json.loads(line.removeprefix(PROGRESS_PREFIX)) for line in capsys.readouterr().out.splitlines()
              if line.startswith(PROGRESS_PREFIX)]
    for scope, stage_id, count in (("当前相位扫描", "A03", 8), ("当前工作点细扫", "A04", 11),
                                   ("当前基带扫频", "A05", 3), ("当前噪声分段", "A06", 2)):
        updates = [event for event in events if event["data"].get("eta_scope") == scope]
        assert updates and updates[-1]["stage"] == stage_id and len(updates) == count
    # 安全收尾：载波关闭归零、Z/X 归零、温控恢复、主场恢复、解调传输停止并断开。
    assert rig.lockin.values[f"/{rig.lockin.device_id}/sigouts/0/on"] == 0
    assert rig.lockin.values[f"/{rig.lockin.device_id}/mods/0/carrier/amplitude"] == 0.0
    assert rig.lockin.values[f"/{rig.lockin.device_id}/mods/0/sidebands/0/enable"] == 0
    assert rig.lockin.values[f"/{rig.lockin.device_id}/mods/0/sidebands/1/enable"] == 0
    assert rig.lockin.values[f"/{rig.lockin.device_id}/demods/{READOUT_DEMOD_IDX}/enable"] == 0
    for generator, channel in ((rig.z, 1), (rig.x, 1)):
        assert generator.channels[channel]["output"] is False
        assert generator.channels[channel]["dc_level"] == 0.0
    assert rig.temp.channels[2]["dc_level"] == pytest.approx(5.0)
    assert rig.temp.channels[2]["output"] is True
    assert rig.gs.state["output"] is True and rig.gs.state["current"] == pytest.approx(-0.001)
    assert config["safety_shutdown"]["completed"] is True
    # 离线分析：真实算法在同一目录上输出 summary.json 与四张图。
    summary = analysis.analyze(run_dir)
    assert (run_dir / "results" / run_format.SUMMARY).is_file()
    for name in (run_format.DIAGRAM_PLOT, run_format.NOISE_PLOT,
                 run_format.PHASE_TRANSFER_PLOT, run_format.PHASE_NOISE_PLOT):
        assert (run_dir / "results" / name).is_file() and (run_dir / "results" / name).stat().st_size > 0
    assert summary["diagram"]["tangent_over_radius"] == pytest.approx(1.0, abs=0.02)
    assert "切线斜率在圆上等于半径" in summary["circle_verdict"]
    assert summary["band_3_50_hz"]["theta_over_y_route_ratio"] == pytest.approx(1.0, abs=0.15)
    assert summary["measurement_route"] == "y_tangent_projection"
    assert summary["theta_diagnostic"]["available"] is True
    assert "y 走线上报" in summary["verdict"] and "θ 走线诊断通过" in summary["verdict"]
    # A05 频响：低频 |H|/H0≈1、高频下降，√Lorentzian 拐点与合成模型一致。
    assert summary["demod_dynamic"] is True
    assert summary["transfer"]["accepted_points"] == 3
    assert summary["transfer"]["bands_hz"] == [[3.0, 80.0]]
    rows = analysis_steps.transfer_rows(run_dir, run_config(run_dir)["transfer"])
    accepted = {row["frequency_hz"]: row for row in rows if row["accepted"]}
    analysis_steps.transfer.fit_transfer_lorentzian(rows, accepted, 1.0)
    gains = {row["frequency_hz"]: row.get("normalized_h0_gain") for row in rows}
    assert gains[3.0] == pytest.approx(1.0, abs=0.02)
    assert gains[80.0] < 0.8 < gains[3.0]
    assert summary["transfer"]["lorentzian_available"] is True
    assert summary["transfer"]["fc_hz"] == pytest.approx(FREQ_CORNER_HZ, rel=0.05)
    assert summary["transfer"]["k0_closure"]["normalized_gain"] == pytest.approx(1.0, abs=0.02)
    assert summary["phase_referral"]["available"] is True


def test_noise_theta_route_isolates_carrier_image():
    """宽带档 −5 kHz 载波镜像不隔离时 θ 整周缠绕，隔离后 θ 与 y 走线一致。

    真机 Demod 4 在宽带档（TC 1 µs）把输入直流偏置混频成 −5 kHz 镜像，实测幅值 0.68 V，
    是圆半径 13.9 mV 的 49 倍；不隔离时 atan2 被该旋转拖成整周缠绕（散布 ≈ π/√3），
    θ 走线在 3–50 Hz 只剩假象小值（实测比值 0.0022）。
    """
    rate, count = 53571.428571, 4 * 53571
    t = np.arange(count) / rate
    rng = np.random.default_rng(20261227)
    noise_x = rng.normal(0.0, 4.0e-5, count)
    noise_y = rng.normal(0.0, 4.0e-5, count)
    image = 0.68 * np.exp(-2j * np.pi * 5000.0 * t)
    phi = math.radians(PHASE_OFFSET_DEG)
    x = RADIUS_V * math.cos(phi) + noise_x + image.real
    y = RADIUS_V * math.sin(phi) + noise_y + image.imag

    def spread(xs, ys):
        angles = np.arctan2(ys, xs)
        mean = np.angle(np.mean(np.exp(1j * angles)))
        return float(np.std(np.angle(np.exp(1j * (angles - mean)))))

    assert spread(x, y) > 1.0
    clean_x = analysis_steps.lowpass_keep_dc(x, rate)
    clean_y = analysis_steps.lowpass_keep_dc(y, rate)
    assert spread(clean_x, clean_y) < 0.05

    angles = np.arctan2(clean_y, clean_x)
    mean = np.angle(np.mean(np.exp(1j * angles)))
    freqs, psd_theta, _ = welch_psd.segment_average(
        [(t, np.angle(np.exp(1j * (angles - mean))), rate)], 4.0)
    _, psd_y, _ = welch_psd.segment_average([(t, clean_y, rate)], 4.0)
    band = (freqs >= 3.0) & (freqs <= 50.0)
    asd_theta = float(np.median(np.sqrt(psd_theta)[band]))
    asd_y = float(np.median(np.sqrt(psd_y)[band])) / (RADIUS_V * abs(math.cos(phi)))
    assert asd_theta / asd_y == pytest.approx(1.0, abs=0.15)


def test_finish_after_sealed_round_keeps_single_shutdown(rig):
    """运行节点内已安全收尾并断开后，结束会话的再次收尾必须复用上次结果。

    真实驱动在断开后读写会报“未连接的数据服务器”；若重复收尾，本已完成的收尾会被
    这些伪错误覆盖成失败，运行目录的 completion_status 也随之写成 failed。
    """
    ctx, failure = Workflow(rig.params, project_root=rig.root), None
    try:
        ctx.execute([stage.id for stage in Workflow.stages if stage.id != Workflow.finish_id])
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)
    config = run_config(ctx.directory.root)
    shutdown = config["safety_shutdown"]
    assert config["completion_status"] == "completed"
    assert shutdown["completed"] is True and shutdown["errors"] == []
    assert rig.lockin.connected is False and rig.gs.connected is False
    disconnects = sorted(event[0] for event in rig.events if event[1:2] == ("disconnect",))
    assert disconnects == ["temp", "x", "z"]


def test_frame_quality_excludes_offset_carrier_leak():
    """宽带档的 5 kHz 泄漏会偏离整频点约 1 Hz，剔除窗口按 bin 宽自适应，不能误判为坏帧。"""
    rate, seconds = 50000.0, 2.0
    n = int(rate * seconds)
    t = np.arange(n) / rate
    dc_v, leak_v = 13.9e-3, 5.5e-3
    z = np.full(n, dc_v + 0j) + leak_v * np.exp(2j * np.pi * 4999.0 * t)
    for offset in (0.0, 1.0, -1.0):
        shifted = np.full(n, dc_v + 0j) + leak_v * np.exp(2j * np.pi * (5000.0 + offset) * t)
        quality = analysis_steps.frame_quality("probe", shifted.real, shifted.imag, rate, dynamic=True)
        assert quality["dynamic"] is True and quality["dc_v"] == pytest.approx(dc_v, rel=1e-6)
        assert quality["excluded_hz"], "宽带档必须记录被剔除的已知寄生频点"
    with pytest.raises(ValueError, match="单音"):
        analysis_steps.frame_quality("probe", z.real, z.imag, rate)


def test_frame_quality_rejects_tone_frame(rig):
    """含 100 Hz 以上单音的帧必须抛错且不写盘；温控在异常路径上仍恢复。"""
    rig.tone_v = 5e-3
    with pytest.raises(ValueError, match="单音"):
        Workflow.run(rig.params, project_root=rig.root)
    runs = sorted((Path(rig.root) / "data" / run_format.DATA_TYPE).glob("*"))
    labels = [path.name for path in (runs[-1] / "raw").glob("*.npz")]
    assert not any(name.startswith("mx_std_settle") for name in labels)
    assert rig.temp.channels[2]["dc_level"] == pytest.approx(5.0)
    assert rig.lockin.values[f"/{rig.lockin.device_id}/sigouts/0/on"] == 0
    assert run_config(runs[-1])["completion_status"] == "failed"


def test_short_frame_is_rejected(rig):
    """短帧不能当有效记录：DAQ 少返回样本即停止，且不写盘。"""
    rig.short_frame = True
    with pytest.raises(SafetyAbort, match="短帧"):
        Workflow.run(rig.params, project_root=rig.root)
    runs = sorted((Path(rig.root) / "data" / run_format.DATA_TYPE).glob("*"))
    assert not list((runs[-1] / "raw").glob("*.npz"))


def test_cancel_restores_temperature_and_keeps_frames(rig, tmp_path):
    rig.cancel_file = tmp_path / "cancel.flag"
    rig.cancel_after = 4
    token = os.environ.get("LAB_CANCEL_FILE")
    os.environ["LAB_CANCEL_FILE"] = str(rig.cancel_file)
    try:
        with pytest.raises(WorkflowCancelled):
            Workflow.run(rig.params, project_root=rig.root)
    finally:
        if token is None:
            os.environ.pop("LAB_CANCEL_FILE", None)
        else:
            os.environ["LAB_CANCEL_FILE"] = token
    runs = sorted((Path(rig.root) / "data" / run_format.DATA_TYPE).glob("*"))
    labels = sorted(path.name for path in (runs[-1] / "raw").glob("*.npz"))
    assert 0 < len(labels) <= 4 and all(name.startswith("mx_std_") for name in labels)
    assert rig.temp.channels[2]["dc_level"] == pytest.approx(5.0)
    assert rig.gs.state["current"] == pytest.approx(-0.001)
    assert run_config(runs[-1])["completion_status"] == "cancelled"


def test_restore_failure_keeps_original_error(rig):
    """温控恢复失败不得记为安全完成，也不得覆盖原始采集错误。"""
    rig.arm_restore_failure = True
    rig.daq_error = RuntimeError("注入采集异常")
    with pytest.raises(RuntimeError, match="注入采集异常"):
        Workflow.run(rig.params, project_root=rig.root)
    runs = sorted((Path(rig.root) / "data" / run_format.DATA_TYPE).glob("*"))
    config = run_config(runs[-1])
    assert config["completion_status"] == "failed"
    assert "注入采集异常" in config["failure_reason"]
    shutdown = config["safety_shutdown"]
    assert shutdown["completed"] is False and shutdown["errors"]
    assert any("温控" in error for error in shutdown["errors"])


def test_save_failure_keeps_previous_frames(rig, monkeypatch):
    """写盘失败保留已写帧，安全收尾照常执行。"""
    original = run_format.write_frame
    calls = []

    def checked(path, label, kind, metadata, **arrays):
        calls.append(label)
        if len(calls) == 4:
            raise OSError("注入磁盘故障")
        return original(path, label, kind, metadata, **arrays)

    monkeypatch.setattr(acquisition.run_format, "write_frame", checked)
    with pytest.raises(OSError, match="注入磁盘故障"):
        Workflow.run(rig.params, project_root=rig.root)
    runs = sorted((Path(rig.root) / "data" / run_format.DATA_TYPE).glob("*"))
    labels = sorted(path.name for path in (runs[-1] / "raw").glob("*.npz"))
    assert len(labels) == 3 and not any(name.startswith("mx_std_coarse") for name in labels)
    assert rig.temp.channels[2]["dc_level"] == pytest.approx(5.0)
    assert rig.lockin.values[f"/{rig.lockin.device_id}/sigouts/0/on"] == 0
    assert run_config(runs[-1])["completion_status"] == "failed"


def test_safety_shutdown_runs_on_late_failure(rig, monkeypatch):
    """A05 失败时仍关闭载波、Z/X 归零、恢复温控与主场并断开。"""
    def broken(ctx):
        raise SafetyAbort("注入噪声段失败")

    monkeypatch.setattr(acquisition, "acquire_noise", broken)
    with pytest.raises(SafetyAbort, match="注入噪声段失败"):
        Workflow.run(rig.params, project_root=rig.root)
    assert rig.lockin.values[f"/{rig.lockin.device_id}/sigouts/0/on"] == 0
    assert rig.lockin.values[f"/{rig.lockin.device_id}/mods/0/output"] == 0
    assert rig.lockin.values[f"/{rig.lockin.device_id}/sigouts/0/offset"] == 0.0
    for generator, channel in ((rig.z, 1), (rig.x, 1)):
        assert generator.channels[channel]["output"] is False
    assert rig.temp.channels[2]["dc_level"] == pytest.approx(5.0)
    assert rig.gs.state == dict(source_function="CURR", current=-0.001, output=True,
                                current_range=0.01, current_limit=0.02)
    assert any(event[:2] == ("z", "disconnect") for event in rig.events)
    runs = sorted((Path(rig.root) / "data" / run_format.DATA_TYPE).glob("*"))
    config = run_config(runs[-1])
    assert config["completion_status"] == "failed" and "注入噪声段失败" in config["failure_reason"]
    assert config["safety_shutdown"]["completed"] is True


def test_analysis_rejects_unfinished_run(rig):
    run_dir = Workflow.run(rig.params, project_root=rig.root)
    path = run_dir / "experiment_config.yaml"
    config = run_config(run_dir)
    path.write_text(yaml.safe_dump({**config, "completion_status": "running"}), encoding="utf-8")
    with pytest.raises(ValueError, match="仍在写入"):
        analysis.analyze(run_dir)
    path.write_text(yaml.safe_dump({**config, "experiment_id": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="不属于本实验"):
        analysis.analyze(run_dir)


def test_raw_frame_round_trip_and_overwrite_guard(rig):
    run_dir = Path(rig.root) / "frame_round_trip"
    (run_dir / "raw").mkdir(parents=True)
    meta = dict(rate_sa_s=1000.0, time_source="module_timestamp", lockin={"phase_deg": 0.0})
    time = np.arange(16) / 1000.0
    path = run_format.write_frame(run_dir, "probe_00", "xy", meta, time_s=time,
                                  x_v=np.ones(16) * 1e-3, y_v=np.zeros(16))
    assert path == "raw/probe_00.npz"
    got_meta, arrays = run_format.read_frame(run_dir, "probe_00", "xy")
    assert got_meta["kind"] == "xy" and got_meta["label"] == "probe_00"
    assert arrays["x_v"].shape == time.shape
    with pytest.raises(FileExistsError):
        run_format.write_frame(run_dir, "probe_00", "xy", meta, time_s=time, x_v=time, y_v=time)
    with pytest.raises(ValueError):
        run_format.write_frame(run_dir, "probe_01", "xy", {**meta, "time_source": "synthetic_grid"},
                               time_s=time, x_v=time, y_v=time)
    with pytest.raises(ValueError):
        run_format.read_frame(run_dir, "../escape", "xy")


def test_entrypoint_subprocess_full_round(rig, tmp_path):
    """fake_main 子进程入口：采集模块替换为本测试模块，随后用真实分析模块分析该目录。"""
    env = os.environ.copy()
    env.update({"LAB_DATA_ROOT": str(Path(rig.root) / "data"), "MX_STD_TEST_ROOT": str(rig.root),
                "LAB_TYPED_PARAMETERS": json.dumps(rig.params.to_external()),
                "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("LAB_CANCEL_FILE", None)
    process = subprocess.run([sys.executable, "-u", "-m", f"{PACKAGE}.test_workflow"], cwd=ROOT, env=env,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert process.returncode == 0, process.stdout + process.stderr
    runs = sorted((Path(rig.root) / "data" / run_format.DATA_TYPE).glob("*"))
    assert len(runs) == 1
    run_dir = runs[0]
    config = run_config(run_dir)
    assert config["completion_status"] == "completed"
    assert len(config["phase_diagram"]["labels"]) == 8
    analysis_env = {**env, "LAB_RUN_DIR": str(run_dir)}
    analyzed = subprocess.run([sys.executable, "-u", "-m", f"{PACKAGE}.analysis"], cwd=ROOT, env=analysis_env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert analyzed.returncode == 0, analyzed.stdout + analyzed.stderr
    assert (run_dir / "results" / run_format.SUMMARY).is_file()
    assert (run_dir / "results" / run_format.DIAGRAM_PLOT).is_file()


def test_a05_sweeps_every_pm_frequency_in_wideband(rig):
    """A05 按频率表逐点出帧：每档一帧、宽带档写入并回读、2° 峰值深度记录在帧元数据。"""
    run_dir = Workflow.run(rig.params, project_root=rig.root)
    config = run_config(run_dir)
    frequencies = list(rig.params.sweep_order_hz)
    transfer = config["transfer"]
    assert [row["frequency_hz"] for row in transfer] == frequencies
    labels = [row["xy_label"] for row in transfer]
    assert len(set(labels)) == len(frequencies)
    for row in transfer:
        metadata, _ = run_format.read_frame(run_dir, row["xy_label"], "xy")
        assert metadata["frequency_hz"] == pytest.approx(row["frequency_hz"])
        assert metadata["phase_pk_rad"] == pytest.approx(math.radians(2.0))
        assert metadata["dynamic"] is True and metadata["source_mode"] == acquisition.SOURCE_PM
        assert metadata["rate_sa_s"] == pytest.approx(rig.params.demod_dynamic_rate_sa_s)
        assert metadata["lockin"]["time_constant_s"] == pytest.approx(rig.params.demod_dynamic_tc_s, rel=0.01)
    # A03/A04 的窄带静态读出没有被改动。
    coarse_meta, _ = run_format.read_frame(run_dir, "mx_std_coarse_00", "xy")
    assert coarse_meta["rate_sa_s"] == pytest.approx(rig.params.demod_rate_sa_s)
    assert coarse_meta["lockin"]["time_constant_s"] == pytest.approx(rig.params.demod_tc_s, rel=0.01)
    assert coarse_meta["dynamic"] is False
    # 宽带档参数确实写入并被回读：解调器节点值、两档配置记录与边带振荡器状态一致。
    dev = rig.lockin.device_id
    demod_base = f"/{dev}/demods/{READOUT_DEMOD_IDX}"
    assert rig.lockin.values[f"{demod_base}/timeconstant"] == pytest.approx(rig.params.demod_dynamic_tc_s)
    assert rig.lockin.values[f"{demod_base}/rate"] == pytest.approx(rig.params.demod_dynamic_rate_sa_s)
    assert config["lockin"]["time_constant_s"] == pytest.approx(rig.params.demod_tc_s)
    assert config["lockin"]["requested_rate_Sa_s"] == pytest.approx(rig.params.demod_rate_sa_s)
    assert config["dynamic_lockin"]["time_constant_s"] == pytest.approx(rig.params.demod_dynamic_tc_s)
    assert config["dynamic_lockin"]["requested_rate_Sa_s"] == pytest.approx(rig.params.demod_dynamic_rate_sa_s)
    assert rig.lockin.values[f"/{dev}/mods/0/index"] == pytest.approx(math.radians(2.0))
    assert rig.lockin.values[f"/{dev}/oscs/{MOD_SIDEBAND_OSC_IDXS[0]}/freq"] == pytest.approx(frequencies[-1])


def test_a06_noise_keeps_wideband_and_finish_closes_pm(rig):
    """A06 噪声沿用宽带档且已关边带；A07 收尾后 PM 边带、载波与解调传输全部归零。"""
    run_dir = Workflow.run(rig.params, project_root=rig.root)
    config = run_config(run_dir)
    frequencies = list(rig.params.sweep_order_hz)
    noise_labels = config["noise"]["labels"]
    assert len(noise_labels) == rig.params.noise_segments
    for label in noise_labels:
        metadata, _ = run_format.read_frame(run_dir, label, "xy")
        assert metadata["dynamic"] is True and metadata["source_mode"] == acquisition.SOURCE_UNMODULATED
        assert metadata["rate_sa_s"] == pytest.approx(rig.params.demod_dynamic_rate_sa_s)
        assert metadata["lockin"]["time_constant_s"] == pytest.approx(rig.params.demod_dynamic_tc_s, rel=0.01)
    # 采集顺序：A05 各档边带开启，A06 各段边带关闭。
    acquisitions = rig.acquisitions
    assert [item["sidebands_on"] for item in acquisitions[-len(frequencies) - len(noise_labels):-len(noise_labels)]]         == [True] * len(frequencies)
    assert [item["sidebands_on"] for item in acquisitions[-len(noise_labels):]] == [False] * len(noise_labels)
    assert config["carrier"]["pm_sidebands_enabled"] is False
    dev = rig.lockin.device_id
    assert rig.lockin.values[f"/{dev}/mods/0/sidebands/0/enable"] == 0
    assert rig.lockin.values[f"/{dev}/mods/0/sidebands/1/enable"] == 0
    assert rig.lockin.values[f"/{dev}/mods/0/carrier/amplitude"] == 0.0
    assert rig.lockin.values[f"/{dev}/sigouts/0/on"] == 0
    assert rig.lockin.values[f"/{dev}/demods/{READOUT_DEMOD_IDX}/enable"] == 0
    assert config["safety_shutdown"]["completed"] is True


def test_transfer_fit_uses_shared_leakage_model(tmp_path):
    """A05 帧里的 5 kHz 载波泄漏按共享泄漏模型的干扰项扣除，|H| 不受污染。"""
    rate, duration, frequency = 50000.0, 4.0, 40.0
    depth_rad = math.radians(2.0)
    t = np.arange(int(rate * duration)) / rate
    rng = np.random.default_rng(7)
    y = RADIUS_V * depth_rad * np.cos(2 * np.pi * frequency * t)
    y = y + 4e-3 * np.sin(2 * np.pi * 5000.0 * t) + rng.normal(0.0, 2e-5, t.size)
    run_dir = tmp_path / "leakage_probe"
    (run_dir / "raw").mkdir(parents=True)
    metadata = dict(rate_sa_s=rate, time_source="module_timestamp", dynamic=True,
                    source_mode=acquisition.SOURCE_PM, frequency_hz=frequency, phase_pk_rad=depth_rad,
                    lockin=dict(phase_deg=0.0, order=4, time_constant_s=1e-6,
                                stream_rate_sa_s=rate, oscillator_hz=5000.0))
    run_format.write_frame(run_dir, "mx_std_pm_00_xy", "xy", metadata,
                           time_s=t, x_v=np.zeros_like(t), y_v=y)
    rows = analysis_steps.transfer_rows(run_dir, [dict(frequency_hz=frequency, phase_pk_rad=depth_rad,
                                                       xy_label="mx_std_pm_00_xy")])
    row = rows[0]
    assert row["accepted"] is True, row["reason"]
    assert "5000" in row["carrier_terms_hz"]
    assert row["y_snr_clean"] > row["y_snr"]
    assert row["h_v_per_rad"] == pytest.approx(RADIUS_V, rel=0.01)


def test_historical_v1_run_without_transfer_still_analyzes(tmp_path):
    """v1 历史目录（窄带噪声、没有 A05 频响）仍可离线分析，只是相位折算不可用。"""
    params = replace(fast_params(), run_tag="legacy")
    run_dir = tmp_path / "legacy_run"
    (run_dir / "raw").mkdir(parents=True)
    rng = np.random.default_rng(11)
    rate = float(params.demod_rate_sa_s)

    def write(label, seconds, phasor_deg):
        t = np.arange(int(rate * seconds)) / rate
        z = RADIUS_V * np.exp(1j * np.deg2rad(phasor_deg))
        base = dict(rate_sa_s=rate, time_source="module_timestamp", dynamic=False,
                    source_mode=acquisition.SOURCE_UNPULSED,
                    lockin=dict(phase_deg=0.0, order=4, time_constant_s=params.demod_tc_s,
                                stream_rate_sa_s=rate, oscillator_hz=5000.0))
        run_format.write_frame(run_dir, label, "xy", base, time_s=t,
                               x_v=z.real + rng.normal(0.0, 3e-5, t.size),
                               y_v=z.imag + rng.normal(0.0, 3e-5, t.size))

    grid = [float(index * 45.0) for index in range(8)]
    for index, degree in enumerate(grid):
        write(f"mx_std_coarse_{index:02d}", 0.1, degree + PHASE_OFFSET_DEG)
    for index in range(2):
        write(f"mx_std_noise_{index:02d}_xy", 1.0, 180.0 + PHASE_OFFSET_DEG)
    parameters = params.to_external()
    for key in ("DEMOD_DYNAMIC_TC", "DEMOD_DYNAMIC_RATE", "PM_FREQUENCIES_HZ", "PM_TONE_DURATION_S",
                "DYNAMIC_TEMP_SWITCH_ON_LAG_S"):
        parameters.pop(key)
    config = run_format.initial_metadata(params) | {
        "completion_status": "completed", "schema_version": 1, "parameters": parameters,
        "phase_diagram": dict(labels=[f"mx_std_coarse_{index:02d}" for index in range(8)], grid_deg=grid),
        "noise": dict(labels=["mx_std_noise_00_xy", "mx_std_noise_01_xy"], segments=2),
    }
    config.pop("transfer")
    config.pop("pm_alignment")
    (run_dir / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    assert run_format.format_block_reason(config) == ""
    assert run_format.analysis_block_reason(config) == ""
    summary = analysis_steps.analyze_data(run_dir, config)
    assert summary["demod_dynamic"] is False and summary["segments"] == 2
    assert summary["transfer"]["accepted_points"] == 0
    assert summary["phase_referral"]["available"] is False
    for name in (run_format.DIAGRAM_PLOT, run_format.NOISE_PLOT,
                 run_format.PHASE_TRANSFER_PLOT, run_format.PHASE_NOISE_PLOT):
        assert (run_dir / "results" / name).is_file() and (run_dir / "results" / name).stat().st_size > 0


def fake_main():
    """子进程入口：按环境变量装配假仪器，再跑完整采集（或分步会话）。"""
    root = Path(os.environ["MX_STD_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id", None)
    params = MxStandardRfPhaseSensitivityParams.from_external(payload)
    with pytest.MonkeyPatch.context() as mp:
        install_rig(root, mp, params)
        if os.environ.get("MX_STD_TEST_SESSION") == "1":
            from lab_workflows.staged_workflow import serve_workflow
            serve_workflow(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)
        else:
            workflow.run(params, project_root=root)


if __name__ == "__main__":
    fake_main()
