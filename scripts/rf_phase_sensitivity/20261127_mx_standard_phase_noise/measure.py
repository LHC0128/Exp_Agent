"""标准 Mx（不打脉冲）射频相位图，以及用 theta 与用 y 求噪声谱算相位灵敏度的差异。

问题（O1）：射频相位灵敏度现有方案打 Z 双脉冲，必须用脉冲理论式拟合。标准 Mx 构型下
（GS200 常数主场 + Z 脉冲链关闭 + MFLI Out1 连续 5 kHz 载波）相图是否为圆、各相位处
斜率是否接近；在同一工作点采集 XY 时间序列后，用 theta=atan2(y-y0, x-x0) 的功率谱
直接得到相位噪声，和用 y 的功率谱除以斜率 K=|dy/dtheta| 得到的相位噪声，是否一致。

流程：
1. 门控（.lab_runtime）通过后读取 rf-phase-sensitivity 默认参数（--vpp / --freq-hz /
   --k-hz-per-ma / --b-hz 可覆盖）。GS200 主场电流只由标定公式 I=(f-b)/K 反算，
   不手填；--dry-run 只打印计划与电流，不连仪器。
2. 连接设备与参考时钟、准备载波后立即关闭 Z 脉冲链（1200B 与 Z 信号源 OFF 回读），
   把 GS200 设到公式电流；王湾 Pump 必须初始为 ON，收尾恢复原状态。
3. 相图：先等原子响应稳定（相邻两次直流相量差 < 0.1 mV），再按 --grid-deg（默认 24 点）
   连续改写 MFLI 载波相移，每点一段 XY；末尾回采首点量整圈漂移。圆拟合给出圆心、
   半径与残差，并给出逐段切线斜率 |dS/dphi| 与 |dY/dphi|。
4. 工作点：按圆模型取 |dy/dphi| = R|cos phi| 最大的相图点，在该相位附近等步长细扫，
   用实测相位角线性拟合 |dy/dphi|，与圆模型的 R|cos phi| 互校后定最终工作点。
5. 噪声：沿用相图那条静态解调配置（1 ms 时间常数、5 kSa/s），按独立温控窗口采集
   --segments 段 x --duration 秒的 XY 序列；每帧先体检，单音幅度超过直流 20% 判废。
   动态短时间常数（1 us、50 kSa/s）会把 5 kHz 射频分量原样放过，不用于噪声。
6. 分析：Welch 单边 PSD -> 三条路径的相位噪声谱（rad/sqrt(Hz)）：theta 直接、
   y 除以实测工作点斜率、以及沿切向投影；输出 summary.json 与两张图。
7. finally 恢复 Pump、preparation.park 安全收尾、断开设备；
   产物在 data/rf_phase_sensitivity/_studies/<时间戳>_<tag>/。

用法（运行目录内，开发目录无 .lab_runtime 会被门控拒绝）：
    $env:LAB_DATA_ROOT = "D:/Code/exp_agent_run/data"
    agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --dry-run
    agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --tag mx_std_stage1
    # 纯离线重算（不连仪器）：
    agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --analyze <研究目录>
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np
from pyvisa.errors import VisaIOError
import yaml

try:
    import hardware_gate as _hg  # noqa: F401
except ImportError:
    _REPO_ROOT = Path(__file__).resolve().parents[3]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from hardware_gate import require_hardware_access, runtime_root
from signal_generator import DG900Instrument

from lab_workflows.common import data_root, load_safety_limits, validate_safety_limit
from lab_workflows.devices import create_signal_generator
from lab_workflows.experiment_modules.rf_phase_sensitivity import (
    acquisition,
    preparation,
    run_format,
)
from lab_workflows.experiment_modules.rf_phase_sensitivity.models import RfPhaseSensitivityParams
from lab_workflows.experiment_modules.z_chain_common import prep as z_prep
from lab_workflows.experiment_modules.z_chain_common import rf_phase
from lab_workflows.experiment_modules.z_chain_common.keys import MAIN_FIELD_KEY
from lab_workflows.experiment_modules.z_chain_common.params import amplifier_enabled
from lab_workflows.experiment_runtime import check_cancelled
from lab_workflows.instrument_config import load_device_definitions
from lab_workflows.steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from lab_workflows.steps.coil_drive_chain import SafetyAbort, sleep
from lab_workflows.steps.run_directory import write_run_config

PUMP_RESOURCE = "TCPIP0::192.168.124.32::5025::SOCKET"
PUMP_DEVICE_ID = "dg9q275000733"
PUMP_CHANNEL = 1
DEFAULT_K_HZ_PER_MA = 11089.0
DEFAULT_B_HZ = 219.7
FORBIDDEN_TAG_CHARS = set('/:*?"<>|') | {chr(92), chr(0)}
# 与 rf_phase_response / rf_phase_sensitivity 研究同口径的低频段；50/100 Hz 工频另行报告。
BAND_HZ = (3.0, 50.0)
MAINS_HZ = (50.0, 100.0, 150.0, 200.0, 250.0)
# 帧体检：帧内最强单音幅度不得超过直流的这一比例，否则该帧不是滤波后的解调直流。
# 只查 100 Hz 以上的单音：低频段的涨落就是待测噪声本身，不能当单音。
MAX_TONE_OVER_DC = 0.2
TONE_MIN_HZ = 100.0
MAX_STD_OVER_DC = 5.0
MIN_DC_MV = 0.5
# 工作点局部细扫半宽与步长（度）：量 |dy/dphi|，与圆模型 R|cos phi| 互校。
FINE_SPAN_DEG = 5.0
FINE_STEP_DEG = 1.0
FINE_POINT_S = 0.5
# 开扫前等原子响应建立：相邻两次直流相量差降到阈值以下再扫，超时带读数继续并记录。
STABLE_TOL_V = 0.1e-3
STABLE_TIMEOUT_S = 180.0
STABLE_POLL_S = 3.0
STABLE_READ_S = 0.2


@dataclass(frozen=True, slots=True)
class StudyDirectory:
    root: Path
    raw: Path
    results: Path
    config_path: Path

    def update_config(self, **updates: Any) -> dict[str, Any]:
        write_run_config(self.config_path, dict(updates))
        return updates


class StudyContext:
    """复用 rf_phase_sensitivity 采集原语的轻量研究会话上下文。"""

    def __init__(self, params: RfPhaseSensitivityParams, root: Path, directory: StudyDirectory) -> None:
        self.root = Path(root).resolve()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.directory = directory
        self.device_session = DeviceSession()
        self.devices: dict[str, Any] = {}
        self.outputs: dict[str, str] = {}
        self.active = False
        self.main_field_state = None
        self.state = run_format.initial_metadata(self.params)
        self.state["study_type"] = "mx_standard_phase_noise"
        self.state["study_root"] = str(directory.root)

    def save(self) -> None:
        self.directory.update_config(**self.state)


def _create_study_directory(root: Path, tag: str) -> StudyDirectory:
    if any(ch in FORBIDDEN_TAG_CHARS for ch in tag) or not tag.strip():
        raise ValueError(f"非法研究目录标签：{tag!r}")
    studies_root = data_root(root) / run_format.DATA_TYPE / "_studies"
    studies_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%m%d_%H%M%S")
    stem = f"{timestamp}_{tag}"
    counter = 1
    while True:
        study_root = studies_root / (stem if counter == 1 else f"{stem}_{counter}")
        try:
            study_root.mkdir()
            break
        except FileExistsError:
            counter += 1
    (study_root / "raw").mkdir()
    (study_root / "results").mkdir()
    return StudyDirectory(root=study_root, raw=study_root / "raw", results=study_root / "results",
                          config_path=study_root / "study_config.yaml")


def _load_params(root: Path, args: argparse.Namespace) -> RfPhaseSensitivityParams:
    defaults_path = root / "params" / "experiments" / f"{run_format.EXPERIMENT_ID}.yaml"
    base = RfPhaseSensitivityParams.from_yaml(defaults_path)
    merged = base.to_external()
    schema_ver = RfPhaseSensitivityParams.schema_version
    local_path = defaults_path.with_name(f"{run_format.EXPERIMENT_ID}.local.yaml")
    if local_path.is_file():
        loaded = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            overlay = loaded.get("parameters", loaded)
            if isinstance(overlay, dict):
                merged.update(overlay)
            if isinstance(loaded.get("schema_version"), int):
                schema_ver = int(loaded["schema_version"])
    if args.vpp is not None:
        merged["Y_RF_VPP"] = float(args.vpp)
    if args.freq_hz is not None:
        merged["Y_RF_FREQ_HZ"] = float(args.freq_hz)
    if args.duration is not None:
        merged["NOISE_DURATION_S"] = float(args.duration)
    if args.segments is not None:
        merged["NOISE_N_AVG"] = int(args.segments)
    if args.temp_lag is not None:
        merged["DYNAMIC_TEMP_SWITCH_ON_LAG_S"] = float(args.temp_lag)
    if args.welch_segment is not None:
        merged["WELCH_SEGMENT_S"] = float(args.welch_segment)
    if args.point_duration is not None:
        merged["STATIC_POINT_DURATION_S"] = float(args.point_duration)
    params = RfPhaseSensitivityParams.from_external(merged, schema_version=schema_ver)
    errors = params.validate(root)
    if errors:
        raise ValueError("参数校验失败：" + "；".join(errors))
    return params


def gs200_current_for_frequency_ma(freq_hz: float, k_hz_per_ma: float, b_hz: float) -> float:
    """由主磁场标定式 f = K*I + b 反算 GS200 电流（mA）；只此一条路径，不接受手填覆盖。"""
    for name, value in (("射频频率", freq_hz), ("标定斜率 K", k_hz_per_ma), ("标定截距 b", b_hz)):
        if not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限值：{value}")
    if k_hz_per_ma <= 0:
        raise ValueError(f"标定斜率 K 必须为正：{k_hz_per_ma}")
    return (freq_hz - b_hz) / k_hz_per_ma


def _resolve_pump_config(ctx: StudyContext) -> dict[str, Any]:
    mapped = ctx.mapping.get("Pump_modulation")
    if isinstance(mapped, dict) and mapped.get("resource") == PUMP_RESOURCE:
        ch = int(mapped.get("channel", PUMP_CHANNEL))
        if ch != PUMP_CHANNEL:
            raise SafetyAbort(f"Pump_modulation 当前映射在 CH{ch}，与目标 CH{PUMP_CHANNEL} 不一致")
        return {**mapped, "channel": PUMP_CHANNEL, "mapping_key": "Pump_modulation"}
    library = load_device_definitions(ctx.root)
    defn = library.get(PUMP_DEVICE_ID)
    if defn is None:
        raise SafetyAbort(f"设备库缺少 {PUMP_DEVICE_ID}（王湾pump {PUMP_RESOURCE}）")
    if defn.instrument != "signal_generator" or defn.resource != PUMP_RESOURCE:
        raise SafetyAbort(f"{PUMP_DEVICE_ID} 资源或类型与 {PUMP_RESOURCE} 不符：{defn}")
    return {**defn.to_config(), "channel": PUMP_CHANNEL, "mapping_key": "Pump_modulation"}


def _connect_or_reuse_pump(ctx: StudyContext) -> DG900Instrument | Any:
    pump_cfg = _resolve_pump_config(ctx)
    signals = ctx.devices.get("signals")
    if signals is not None:
        for route in signals.routes.values():
            if getattr(route.device, "resource_string", None) == PUMP_RESOURCE:
                if route.actual_channel == PUMP_CHANNEL:
                    raise SafetyAbort(f"{PUMP_RESOURCE} CH{PUMP_CHANNEL} 已在本实验信号路径中占用，拒绝重复控制")
                return route.device
    device = ctx.device_session.connect(
        "pump",
        f"pump://{PUMP_RESOURCE}",
        lambda: create_signal_generator(pump_cfg, channel=PUMP_CHANNEL, apply_load_policy=False),
    )
    if not isinstance(device, DG900Instrument):
        raise SafetyAbort(f"王湾pump 驱动类型异常：{type(device).__name__}")
    return device


def _set_pump_output(pump_dev: Any, enabled: bool) -> bool:
    last_exc: VisaIOError | None = None
    can_reconnect = callable(getattr(pump_dev, "disconnect", None)) and callable(getattr(pump_dev, "connect", None))
    for attempt in range(3):
        if attempt > 0:
            print(f"[警告] 王湾pump CH{PUMP_CHANNEL} VISA 超时/异常，断开重连重试（{attempt}/2）：{last_exc}", flush=True)
            try:
                pump_dev.disconnect()
            except Exception:
                pass
            try:
                pump_dev.connect()
            except Exception as exc:
                last_exc = exc if isinstance(exc, VisaIOError) else last_exc
                continue
        try:
            pump_dev.set_output(bool(enabled), channel=PUMP_CHANNEL)
            actual = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
            if actual != bool(enabled):
                raise SafetyAbort(f"王湾pump CH{PUMP_CHANNEL} 回读不符：期望 {enabled}，实际 {actual}")
            return actual
        except VisaIOError as exc:
            last_exc = exc
            if attempt == 2 or not can_reconnect:
                raise
    raise SafetyAbort(f"王湾pump CH{PUMP_CHANNEL} 设置失败：{last_exc}")


def _disable_z(ctx: StudyContext) -> dict[str, Any]:
    """关闭 Z 双脉冲链：先关 1200B、再关 Z 信号源输出，并逐项回读。"""
    z_dev, z_ch = preparation._route(ctx, "z")
    if bool(ctx.state.get("amp_output_on")):
        z_prep.set_amp_output(ctx, False)
        if bool(ctx.state.get("amp_output_on")):
            raise SafetyAbort("1200B 功放输出未回读为 OFF")
    if preparation.dg_output(z_dev, z_ch):
        z_dev.set_output(False, channel=z_ch)
        z_dev.wait_for_operation_complete()
        if preparation.dg_output(z_dev, z_ch):
            raise SafetyAbort("Z 信号源输出未回读为 OFF")
    record = dict(amp_output_on=bool(ctx.state.get("amp_output_on")),
                  z_source_output=preparation.dg_output(z_dev, z_ch))
    ctx.state["z_pulse_chain"] = record
    ctx.outputs["Z 通道"] = "脉冲链关闭（标准 Mx：不打脉冲）"
    return record



def _safety_envelope(params) -> tuple[float, float]:
    return params.y_output_envelope(pm_enabled=False)


def _carrier_sigout(dev: str, node: str) -> str:
    return f"/{dev}/sigouts/0/{node}"


def _carrier_mod(dev: str, node: str) -> str:
    return f"/{dev}/mods/{rf_phase.MOD_IDX}/{node}"


def _carrier_safety(params, mapping: dict, root: Path) -> str:
    """按 Signal Output 1 安全限值校验零、偏置与调相包络，返回限值键名。"""
    check_cancelled()
    limits = load_safety_limits(root)
    key = rf_phase.mfli_sigout_safety_key(mapping)
    y_bias_v = float(params.y_bias_v)
    y_min, y_max = _safety_envelope(params)
    for value in (0.0, y_bias_v, float(y_min), float(y_max)):
        validate_safety_limit(key, value, limits)
    return key


def _carrier_verify(ctx: StudyContext, layout: dict[str, Any], *, output: bool) -> dict[str, Any]:
    """回读载波、外时钟、量程、偏置与读出参考；边带只要求关闭，不校其振荡器分配。"""
    device = ctx.devices["hf2"]
    dev, p = device.device_id, ctx.params
    device.sync()
    got = dict(
        extclk=bool(device.get_extclk()),
        output=bool(device.get_int(_carrier_sigout(dev, "on"))),
        range_v=float(device.get_double(_carrier_sigout(dev, "range"))),
        offset_v=float(device.get_double(_carrier_sigout(dev, "offset"))),
        add_enabled=bool(device.get_int(_carrier_sigout(dev, "add"))),
        mod_enable=bool(device.get_int(_carrier_mod(dev, "enable"))),
        mode=int(device.get_int(_carrier_mod(dev, "mode"))),
        freqdevenable=int(device.get_int(_carrier_mod(dev, "freqdevenable"))),
        mod_output=int(device.get_int(_carrier_mod(dev, "output"))),
        carrier_enable=bool(device.get_int(_carrier_mod(dev, "carrier/enable"))),
        carrier_osc=int(device.get_int(_carrier_mod(dev, "carrier/oscselect"))),
        carrier_hz=float(device.get_double(f"{device.osc_path(layout['carrier_osc_idx'])}/freq")),
        carrier_vpk=float(device.get_double(_carrier_mod(dev, "carrier/amplitude"))),
        command_deg=float(device.get_double(_carrier_mod(dev, "carrier/phaseshift"))),
        sideband0_enable=bool(device.get_int(_carrier_mod(dev, "sidebands/0/enable"))),
        sideband1_enable=bool(device.get_int(_carrier_mod(dev, "sidebands/1/enable"))),
        readout_osc_hz=float(device.get_double(f"{device.osc_path(layout['readout_osc_idx'])}/freq")),
    )
    envelope = _safety_envelope(p)
    valid = (
        got["extclk"]
        and got["output"] is output
        and not got["add_enabled"]
        and got["range_v"] >= max(abs(value) for value in envelope) - 1e-9
        and math.isclose(got["offset_v"], p.y_bias_v, abs_tol=1e-4)
        and got["mod_enable"]
        and got["mode"] == 1
        and got["freqdevenable"] == 0
        and got["mod_output"] == 1
        and got["carrier_enable"]
        and got["carrier_osc"] == layout["carrier_osc_idx"]
        and math.isclose(got["carrier_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
        and math.isclose(got["carrier_vpk"], p.y_carrier_vpk, abs_tol=1e-5)
        and abs(((got["command_deg"] - layout["command_deg"] + 180.0) % 360.0) - 180.0) <= 0.02
        and not got["sideband0_enable"]
        and not got["sideband1_enable"]
        and math.isclose(got["readout_osc_hz"], p.y_rf_freq_hz, rel_tol=1e-6)
    )
    if not valid:
        raise SafetyAbort(f"MFLI 标准 Mx 载波回读不符：{got}")
    return got


def _carrier_begin(ctx: StudyContext) -> dict[str, Any]:
    """写 Out1 与 MF-MOD 载波节点（不启用 PM 边带），回读确认后返回载波会话状态。"""
    p = ctx.params
    device = ctx.devices["hf2"]
    dev = device.device_id
    safety_key = _carrier_safety(p, ctx.mapping, ctx.root)
    if int(device.get_int(_carrier_sigout(dev, "add"))) != 0:
        raise SafetyAbort("MFLI Signal Output 1 开启了外部 Add 叠加，无法确保总电压安全")
    carrier_osc_idx, _, readout_osc_idx = rf_phase.oscillator_layout(ctx)
    device.set_int(_carrier_sigout(dev, "on"), 0)
    device.set_int(_carrier_mod(dev, "enable"), 0)
    device.set_double(_carrier_sigout(dev, "range"), rf_phase.select_mfli_output_range(p))
    device.set_double(_carrier_sigout(dev, "offset"), float(p.y_bias_v))
    device.set_int(_carrier_mod(dev, "mode"), 1)
    device.set_int(_carrier_mod(dev, "freqdevenable"), 0)
    device.set_int(_carrier_mod(dev, "carrier/oscselect"), carrier_osc_idx)
    device.set_double(f"{device.osc_path(carrier_osc_idx)}/freq", float(p.y_rf_freq_hz))
    device.set_double(f"{device.osc_path(readout_osc_idx)}/freq", float(p.y_rf_freq_hz))
    device.set_double(_carrier_mod(dev, "carrier/amplitude"), float(p.y_carrier_vpk))
    device.set_double(_carrier_mod(dev, "carrier/phaseshift"), 0.0)
    device.set_int(_carrier_mod(dev, "carrier/enable"), 1)
    device.set_int(_carrier_mod(dev, "sidebands/0/enable"), 0)
    device.set_int(_carrier_mod(dev, "sidebands/1/enable"), 0)
    device.set_int(_carrier_mod(dev, "output"), 1)
    device.set_int(_carrier_mod(dev, "enable"), 1)
    device.set_int(_carrier_sigout(dev, "on"), 1)
    layout = dict(carrier_osc_idx=carrier_osc_idx, readout_osc_idx=readout_osc_idx,
                  command_deg=0.0, safety_key=safety_key)
    _carrier_verify(ctx, layout, output=True)
    ctx.outputs["Y 射频"] = (f"MFLI Out1 连续载波（标准 Mx，无 PM 边带），"
                             f"{p.y_carrier_vpk:g} Vpk，偏置 {p.y_bias_v:+g} V，指令相位 0°")
    return layout


def _carrier_set_phase(ctx: StudyContext, layout: dict[str, Any], phase_deg: float) -> float:
    device = ctx.devices["hf2"]
    _carrier_safety(ctx.params, ctx.mapping, ctx.root)
    layout["command_deg"] = float(phase_deg) % 360.0
    device.set_double(_carrier_mod(device.device_id, "carrier/phaseshift"), layout["command_deg"])
    _carrier_verify(ctx, layout, output=True)
    ctx.outputs["Y 射频"] = (f"MFLI Out1 连续载波（标准 Mx，无 PM 边带），"
                             f"指令相位 {layout['command_deg']:g}°")
    return layout["command_deg"]


def _prepare_standard_chain(ctx: StudyContext) -> dict[str, Any]:
    """镜像 preparation.prepare_measurement 的公共步骤，但不启用 PM 边带、也不准备 Z 脉冲。"""
    preparation.apply_oscillator_layout(ctx)
    preparation._verify_generators(ctx)
    check_cancelled()
    ctx.active = True
    if amplifier_enabled(ctx.params):
        z_prep.set_amp_output(ctx, False)
    preparation._prepare_temperature(ctx)
    z_prep.configure_chain(ctx)
    z_prep.configure_main_field(ctx, ctx.params.main_field_current_ma)
    preparation._configure_lockin(ctx)
    preparation._configure_x_bias(ctx)
    preparation._prepare_trigger(ctx)
    carrier = _carrier_begin(ctx)
    check_cancelled()
    return carrier

def _set_main_field_ma(ctx: StudyContext, target_ma: float) -> dict[str, Any]:
    limits = load_safety_limits(ctx.root)
    target_ma = float(target_ma)
    validate_safety_limit(MAIN_FIELD_KEY, target_ma, limits)
    gs200 = ctx.devices["gs200"]
    target_a = target_ma / 1000.0
    cur_a = float(gs200.get_current())
    on_now = bool(gs200.get_output())
    if target_ma == 0.0:
        if on_now:
            gs200.set_output(False)
        if gs200.get_output():
            raise SafetyAbort("GS200 主磁场未回读为 OFF")
        return dict(output_on=False, current_ma=0.0)
    if not (on_now and abs(cur_a - target_a) <= 1e-6):
        limit_a = float(limits[MAIN_FIELD_KEY]["max"]) / 1000.0
        gs200.set_output(False)
        gs200.set_source_function("CURRent")
        if float(gs200.get_current_limit()) < abs(target_a):
            gs200.set_current_limit(limit_a)
        if float(gs200.get_current_range()) < abs(target_a):
            gs200.set_current_range(max(0.01, abs(target_a)))
        gs200.set_current(target_a)
        gs200.set_output(True)
    actual_a = float(gs200.get_current())
    actual_on = bool(gs200.get_output())
    if not actual_on or abs(actual_a - target_a) > max(1e-6, abs(target_a) * 0.01):
        raise SafetyAbort(
            f"GS200 主磁场回读不符：设定 {target_ma:g} mA，回读 {actual_a * 1000.0:g} mA（output={actual_on}）")
    return dict(output_on=actual_on, current_ma=float(actual_a * 1000.0))


def circle_fit(x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """代数最小二乘圆拟合：x^2+y^2 = a*x + b*y + c。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 3 or x.size != y.size:
        raise ValueError(f"圆拟合至少需要 3 个同长数据点：x={x.size}, y={y.size}")
    design = np.column_stack([x, y, np.ones_like(x)])
    sol, *_ = np.linalg.lstsq(design, x * x + y * y, rcond=None)
    cx, cy = float(sol[0] / 2.0), float(sol[1] / 2.0)
    radius_sq = float(sol[2] + cx * cx + cy * cy)
    if not math.isfinite(radius_sq) or radius_sq <= 0:
        raise ValueError(f"圆拟合失败：半径平方 {radius_sq}")
    radius = math.sqrt(radius_sq)
    residual = np.hypot(x - cx, y - cy) - radius
    return dict(center_x_v=cx, center_y_v=cy, radius_v=radius,
                rms_residual_v=float(np.sqrt(np.mean(residual ** 2))),
                max_abs_residual_v=float(np.max(np.abs(residual))))


def phasor_angles_deg(phasors: np.ndarray, fit: dict[str, float]) -> np.ndarray:
    """相量相对拟合圆心的角度（度）；相图上沿圆周转过的角度，用它做差分而不是用指令相位。"""
    rel = np.asarray(phasors, dtype=complex) - complex(fit["center_x_v"], fit["center_y_v"])
    return np.degrees(np.arctan2(rel.imag, rel.real))


def diagram_metrics(phasors: np.ndarray, fit: dict[str, float]) -> dict[str, Any]:
    """逐段差分给出切线斜率 |dS/dphi| 与 |dY/dphi|；角度取实测相位角，闭合回绕到首点。

    圆上切线斜率恒等于半径 |dS/dphi| = R；|dY/dphi| = R|cos phi| 只是切线在 Y 上的投影，
    随相位角在 0 与 R 之间变化。两者不是同一个量，报告斜率时只用前者。
    """
    phasors = np.asarray(phasors, dtype=complex)
    angles = phasor_angles_deg(phasors, fit)
    # 相位角跨 ±180° 时原始差分会出现 ±360° 跳变：先解缠再差分，闭合方向随扫描方向。
    unwrapped = np.unwrap(np.radians(angles))
    steps = np.diff(unwrapped)
    if steps.size == 0 or np.median(np.abs(steps)) <= 0:
        raise ValueError(f"相图相邻点相位角没有推进：{np.round(angles, 3).tolist()}")
    closing = unwrapped[0] + math.copysign(2.0 * math.pi, float(np.median(steps)))
    closed_angles = np.r_[unwrapped, closing]
    closed = np.r_[phasors, phasors[0]]
    dphi = np.diff(closed_angles)
    dphi_deg = np.degrees(dphi)
    step_deg = float(np.median(np.abs(dphi_deg)))
    dphasor = np.diff(closed)
    with np.errstate(invalid="ignore", divide="ignore"):
        # 弦长 = 2R sin(dphi/2)：直接用弦长除 dphi 会带 sinc 偏差（15° 步进低 0.29%），
        # 这里按 sin(dphi/2) 精确反解，圆上得到的就是半径本身。
        tangent = np.abs(dphasor) / (2.0 * np.abs(np.sin(dphi / 2.0)))
        abs_dy = np.abs(np.diff(closed.imag) / dphi)
    accepted = np.abs(np.abs(dphi_deg) - step_deg) <= max(0.2 * step_deg, 1.0)
    valid = accepted & np.isfinite(tangent) & np.isfinite(abs_dy)
    if not np.any(valid):
        raise ValueError(f"相图没有可用的段差分：步进 {step_deg:.3f}°，相位角 {np.round(angles, 3).tolist()}")
    return dict(
        phasor_angle_deg=angles.tolist(),
        tangent_v_per_rad=tangent.tolist(),
        abs_dy_v_per_rad=abs_dy.tolist(),
        segment_dphi_deg=dphi_deg.tolist(),
        segment_center_deg=(np.degrees((closed_angles[:-1] + closed_angles[1:]) / 2.0) % 360.0).tolist(),
        accepted=accepted.tolist(),
        step_deg_median=step_deg,
        tangent_median_v_per_rad=float(np.median(tangent[valid])),
        tangent_relative_spread=float((np.max(tangent[valid]) - np.min(tangent[valid]))
                                      / max(np.median(tangent[valid]), 1e-15)),
        abs_dy_median_v_per_rad=float(np.median(abs_dy[valid])),
        accepted_count=int(np.sum(valid)),
        accepted_total=int(valid.size),
    )


def choose_working_point(phases_deg: np.ndarray, phasors: np.ndarray,
                         fit: dict[str, float]) -> dict[str, Any]:
    """按圆模型取 |dY/dphi| = R|cos phi| 最大的相图点，作为细扫中心。"""
    angles = phasor_angles_deg(phasors, fit)
    score = np.abs(np.cos(np.radians(angles)))
    index = int(np.argmax(score))
    radius = float(fit["radius_v"])
    return dict(index=index,
                command_deg=float(np.asarray(phases_deg, dtype=float)[index]),
                phasor_angle_deg=float(angles[index]),
                cos_phi=float(score[index]),
                radius_v=radius,
                analytic_dy_dphi_v_per_rad=float(radius * score[index]))


def frame_quality(label: str, x_v: np.ndarray, y_v: np.ndarray, rate_sa_s: float) -> dict[str, Any]:
    """体检一段 XY 帧：直流幅度、最强单音幅度与频率；不合格直接报错，不把坏帧当有效记录。"""
    x = np.asarray(x_v, dtype=float)
    y = np.asarray(y_v, dtype=float)
    if x.size != y.size or x.size < 64:
        raise ValueError(f"{label} 帧样本数异常：x={x.size}, y={y.size}")
    z = x + 1j * y
    dc = complex(np.mean(z))
    spectrum = np.fft.fft(z - dc)
    freqs = np.fft.fftfreq(z.size, 1.0 / float(rate_sa_s))
    high = np.abs(freqs) >= TONE_MIN_HZ
    peak = int(np.argmax(np.where(high, np.abs(spectrum), -1.0)))
    tone_v = float(2.0 * abs(spectrum[peak]) / z.size)
    quality = dict(label=label, samples=int(z.size), rate_sa_s=float(rate_sa_s),
                   dc_v=float(abs(dc)), tone_v=tone_v, tone_hz=float(freqs[peak]),
                   std_x_v=float(np.std(x)), std_y_v=float(np.std(y)))
    if quality["dc_v"] * 1e3 < MIN_DC_MV:
        raise ValueError(f"{label} 直流幅度 {quality['dc_v'] * 1e3:.3f} mV 低于 {MIN_DC_MV:g} mV，"
                         "不能当解调输出")
    if tone_v > MAX_TONE_OVER_DC * quality["dc_v"]:
        raise ValueError(
            f"{label} 帧内 {quality['tone_hz']:.1f} Hz 单音 {tone_v * 1e3:.1f} mV 超过直流 "
            f"{quality['dc_v'] * 1e3:.1f} mV 的 {MAX_TONE_OVER_DC:.0%}；该帧不是滤波后的解调直流")
    if quality["std_x_v"] > MAX_STD_OVER_DC * quality["dc_v"]:
        raise ValueError(
            f"{label} 帧内标准差 {quality['std_x_v'] * 1e3:.1f} mV 超过直流 "
            f"{quality['dc_v'] * 1e3:.1f} mV 的 {MAX_STD_OVER_DC:g} 倍；该帧被宽带分量主导，"
            "不能用于噪声谱")
    return quality


def _read_point(ctx: StudyContext, label: str, phase_deg: float, duration_s: float,
                lockin: dict[str, Any]) -> complex:
    """采一段 XY 并返回其直流相量；温控窗口、写盘与前后回读一致性由 _xy 内部保证。"""
    return acquisition._xy(ctx, label, duration_s, lockin=lockin,
                           burst_command_deg=float(phase_deg), save_frame=acquisition._save_frame,
                           write_temperature=preparation.write_temperature, wait=sleep)


def _wait_for_stable(ctx: StudyContext, carrier: dict[str, Any], phase_deg: float,
                     lockin: dict[str, Any]) -> dict[str, Any]:
    """等原子响应建立：相邻两次直流相量差小于阈值才开扫；超时也继续，但记进研究目录。"""
    _carrier_set_phase(ctx, carrier, phase_deg)
    history: list[complex] = []
    started = time.monotonic()
    index = 0
    while True:
        mean = _read_point(ctx, f"mx_std_settle_{index:02d}", phase_deg, STABLE_READ_S, lockin)
        history.append(mean)
        elapsed = time.monotonic() - started
        delta = abs(history[-1] - history[-2]) if len(history) > 1 else float("nan")
        shown = "—" if not math.isfinite(delta) else f"{delta * 1e3:.3f} mV"
        print(f"  稳定等待 {index + 1}：|S|={abs(mean) * 1e3:.3f} mV，与上次差 {shown}（已 {elapsed:.1f} s）",
              flush=True)
        if len(history) > 1 and delta <= STABLE_TOL_V:
            record = dict(stable=True, waited_s=elapsed, reads=len(history), last_delta_v=float(delta),
                          phasors=[[float(z.real), float(z.imag)] for z in history])
            ctx.state["settle"] = record
            ctx.save()
            return record
        if elapsed >= STABLE_TIMEOUT_S:
            record = dict(stable=False, waited_s=elapsed, reads=len(history), last_delta_v=float(delta),
                          phasors=[[float(z.real), float(z.imag)] for z in history])
            ctx.state["settle"] = record
            ctx.save()
            print(f"  [警告] 稳定等待超时（{elapsed:.1f} s），按当前读数继续", flush=True)
            return record
        index += 1
        sleep(STABLE_POLL_S)


def _scan_phase(ctx: StudyContext, carrier: dict[str, Any], grid_deg: list[float],
                point_duration_s: float) -> tuple[list[str], np.ndarray, dict[str, Any]]:
    """整周相位扫描；先等响应稳定并在首点采一段丢弃，末尾再回采首点量整圈漂移。"""
    coarse_lockin = acquisition.lockin_readback(ctx)
    settle = _wait_for_stable(ctx, carrier, grid_deg[0], coarse_lockin)
    warm = _read_point(ctx, "mx_std_warmup_00", grid_deg[0], point_duration_s, coarse_lockin)
    print(f"  预热丢弃：{grid_deg[0]:6.1f}° -> X={warm.real:+.6f} V, Y={warm.imag:+.6f} V "
          f"（稳定等待 {settle['waited_s']:.1f} s，{settle['reads']} 次读数）", flush=True)
    labels: list[str] = []
    phasors: list[complex] = []
    for index, phase in enumerate(grid_deg):
        _carrier_set_phase(ctx, carrier, phase)
        label = f"mx_std_coarse_{index:02d}"
        mean = _read_point(ctx, label, phase, point_duration_s, coarse_lockin)
        labels.append(label)
        phasors.append(mean)
        print(f"  相图 {index + 1}/{len(grid_deg)}：{phase:6.1f}° -> "
              f"X={mean.real:+.6f} V, Y={mean.imag:+.6f} V", flush=True)
    _carrier_set_phase(ctx, carrier, grid_deg[0])
    repeat = _read_point(ctx, "mx_std_repeat_00", grid_deg[0], point_duration_s, coarse_lockin)
    drift_v = float(abs(repeat - phasors[0]))
    print(f"  回采首点：{grid_deg[0]:6.1f}° -> 与首点相差 {drift_v * 1e6:.3f} uV", flush=True)
    info = dict(warmup_label="mx_std_warmup_00", repeat_label="mx_std_repeat_00",
                first_point_drift_v=drift_v,
                warmup_delta_v=float(abs(warm - phasors[0])),
                settle=settle,
                warmup_phasor=[float(warm.real), float(warm.imag)],
                repeat_phasor=[float(repeat.real), float(repeat.imag)])
    ctx.state["phase_diagram"] = {"labels": labels, "grid_deg": list(grid_deg), **info}
    ctx.save()
    return labels, np.asarray(phasors, dtype=complex), info


def as_phasors(values) -> np.ndarray:
    """把复数相量或 [[real, imag], ...] 记录统一成一维复数数组。"""
    items = []
    for item in values:
        if isinstance(item, (complex, int, float, np.floating, np.integer)):
            items.append(complex(item))
        else:
            items.append(complex(float(item[0]), float(item[1])))
    return np.asarray(items, dtype=complex)


def fine_scan_slope(commands_deg: list[float], phasors, fit: dict[str, float]) -> dict[str, Any]:
    """用细扫各点的实测相位角线性拟合 dY/dphi，并给出最接近切向沿 Y 的那个点。"""
    values = as_phasors(phasors)
    if values.size < 3:
        raise ValueError(f"细扫点数不足 3 个，无法拟合斜率：{values.size}")
    angles = phasor_angles_deg(values, fit)
    unwrapped = np.unwrap(np.radians(angles))
    design = unwrapped - unwrapped.mean()
    slope, intercept = np.polyfit(design, values.imag, 1)
    score = np.abs(np.cos(np.radians(angles)))
    best = int(np.argmax(score))
    residual = values.imag - (intercept + slope * design)
    return dict(slope_v_per_rad=float(slope), intercept_v=float(intercept),
                residual_v=float(np.std(residual)),
                best_index=best,
                best_command_deg=float(np.asarray(commands_deg, dtype=float)[best]),
                best_phasor_angle_deg=float(angles[best]),
                best_cos_phi=float(score[best]),
                analytic_v_per_rad=float(fit["radius_v"] * score[best]),
                phasor_angle_deg=angles.tolist())


def _fine_scan(ctx: StudyContext, carrier: dict[str, Any], center_deg: float,
               point_duration_s: float, lockin: dict[str, Any]) -> dict[str, Any]:
    """工作点附近等步长细扫；用实测相位角回归 |dY/dphi|，与圆模型 R|cos phi| 互校。"""
    offsets = np.arange(-FINE_SPAN_DEG, FINE_SPAN_DEG + 0.5 * FINE_STEP_DEG, FINE_STEP_DEG)
    commands = [float((center_deg + float(offset)) % 360.0) for offset in offsets]
    labels: list[str] = []
    phasors: list[complex] = []
    for index, phase in enumerate(commands):
        _carrier_set_phase(ctx, carrier, phase)
        label = f"mx_std_fine_{index:02d}"
        mean = _read_point(ctx, label, phase, point_duration_s, lockin)
        labels.append(label)
        phasors.append(mean)
        print(f"  细扫 {index + 1}/{len(commands)}：{phase:7.2f}° -> "
              f"X={mean.real:+.6f} V, Y={mean.imag:+.6f} V", flush=True)
    record = dict(labels=labels, commands_deg=commands,
                  phasors=[[float(z.real), float(z.imag)] for z in phasors])
    ctx.state["fine_scan"] = record
    ctx.save()
    return record


def _capture_noise(ctx: StudyContext, segments: int, duration_s: float) -> list[str]:
    """静态解调配置下分段采集噪声：与相图同一条时间常数与采样率，5 kHz 分量已被滤掉。

    动态配置（1 us 时间常数、50 kSa/s）是为脉冲瞬态准备的，会把 5 kHz 射频分量原样放过，
    噪声谱会被这个单音主导，不能用于相位噪声。
    """
    lockin = acquisition.lockin_readback(ctx)
    labels: list[str] = []
    for index in range(segments):
        label = f"mx_std_noise_{index:02d}_xy"
        acquisition._xy(ctx, label, duration_s, lockin=lockin,
                        segment_index=index, segment_count=segments,
                        save_frame=acquisition._save_frame,
                        write_temperature=preparation.write_temperature, wait=sleep,
                        source_mode="mx_standard_unpulsed")
        labels.append(label)
        print(f"  噪声 {index + 1}/{segments} 段完成（温控已恢复并等待）", flush=True)
    ctx.state["noise_paths"] = dict(labels=labels, segments=segments, duration_s=duration_s,
                                    demod_dynamic=False,
                                    time_constant_s=float(lockin["time_constant_s"]),
                                    stream_rate_sa_s=float(lockin["stream_rate_sa_s"]))
    ctx.save()
    return labels


def _load_xy_frames(study_root: Path, labels: list[str]) -> list[dict[str, Any]]:
    frames = []
    for label in labels:
        meta, arrays = run_format.read_frame(study_root, label, "xy")
        frames.append(dict(label=label, metadata=meta, time_s=arrays["time_s"],
                           x_v=arrays["x_v"], y_v=arrays["y_v"]))
    return frames


def _welch_psd(signals: list[np.ndarray], rate_sa_s: float,
               segment_s: float) -> tuple[np.ndarray, np.ndarray]:
    from scipy.signal import welch

    nperseg = max(16, int(round(segment_s * rate_sa_s)))
    spectra = []
    freqs = None
    for values in signals:
        if values.size < nperseg:
            raise ValueError(f"单段样本 {values.size} 少于 Welch 段长 {nperseg}")
        freqs, psd = welch(values - np.mean(values), fs=rate_sa_s, nperseg=nperseg,
                           detrend="constant", scaling="density")
        spectra.append(psd)
    return freqs, np.mean(np.stack(spectra, axis=0), axis=0)


def _band_median(freqs: np.ndarray, asd: np.ndarray, band: tuple[float, float]) -> float:
    mask = (freqs >= band[0]) & (freqs <= band[1])
    return float(np.median(asd[mask])) if np.any(mask) else float("nan")


def _mains_median(freqs: np.ndarray, asd: np.ndarray) -> float:
    values = []
    for line in MAINS_HZ:
        mask = np.abs(freqs - line) <= 1.0
        if np.any(mask):
            values.append(float(np.median(asd[mask])))
    return float(np.median(values)) if values else float("nan")


def analyze_study(study_root: Path, *, welch_segment_s: float | None = None) -> dict[str, Any]:
    """离线重算：读原始 XY 帧，体检后给出相图切线斜率、工作点斜率与三条相位噪声路径。"""
    config = yaml.safe_load((study_root / "study_config.yaml").read_text(encoding="utf-8")) or {}
    diagram = config.get("phase_diagram") or {}
    noise = config.get("noise_paths") or {}
    grid = [float(v) for v in diagram.get("grid_deg") or ()]
    coarse_labels = [str(v) for v in diagram.get("labels") or ()]
    noise_labels = [str(v) for v in noise.get("labels") or ()]
    if not coarse_labels or not noise_labels:
        raise ValueError("研究目录缺少相图或噪声帧清单，无法分析")
    segment_s = float(welch_segment_s or config.get("WELCH_SEGMENT_S") or 1.0)

    coarse = _load_xy_frames(study_root, coarse_labels)
    coarse_quality = [frame_quality(f["label"], f["x_v"], f["y_v"],
                                    float(f["metadata"]["rate_sa_s"])) for f in coarse]
    phasors = np.asarray([complex(np.mean(f["x_v"]), np.mean(f["y_v"])) for f in coarse], dtype=complex)
    fit = circle_fit(phasors.real, phasors.imag)
    radius = float(fit["radius_v"])
    metrics = diagram_metrics(phasors, fit)
    chosen = choose_working_point(np.asarray(grid, dtype=float), phasors, fit)
    tangent_over_radius = metrics["tangent_median_v_per_rad"] / radius

    frames = _load_xy_frames(study_root, noise_labels)
    rate = float(frames[0]["metadata"]["rate_sa_s"])
    noise_quality = [frame_quality(f["label"], f["x_v"], f["y_v"],
                                   float(f["metadata"]["rate_sa_s"])) for f in frames]
    for frame in frames:
        time = frame["time_s"]
        if not np.allclose(1.0 / np.median(np.diff(time)), rate, rtol=3e-3):
            raise ValueError(f"{frame['label']} 采样率与首段不一致")

    cx, cy = float(fit["center_x_v"]), float(fit["center_y_v"])
    # 工作点斜率取噪声帧自身的实测相位角：K = R|cos phi|，与帧内数据同口径，
    # 不用指令相位，也不把弦长当斜率。
    noise_means = np.asarray([complex(np.mean(f["x_v"]), np.mean(f["y_v"])) for f in frames],
                             dtype=complex)
    noise_angles = phasor_angles_deg(noise_means, fit)
    phi_deg = float(np.degrees(np.mean(np.unwrap(np.radians(noise_angles)))))
    slope_y = float(radius * abs(math.cos(math.radians(phi_deg))))
    if not math.isfinite(slope_y) or slope_y <= 1e-12:
        raise ValueError(f"工作点斜率无效：相位角 {phi_deg:.3f}°，K={slope_y}")
    fine = config.get("fine_scan") or {}
    fine_summary = None
    if fine.get("commands_deg") and fine.get("phasors"):
        fine_summary = fine_scan_slope([float(v) for v in fine["commands_deg"]],
                                       fine["phasors"], fit)

    tangent_dir = complex(-math.sin(math.radians(phi_deg)), math.cos(math.radians(phi_deg)))
    centered, projected = [], []
    for frame in frames:
        theta = np.arctan2(frame["y_v"] - cy, frame["x_v"] - cx)
        mean_angle = np.angle(np.mean(np.exp(1j * theta)))
        centered.append(np.angle(np.exp(1j * (theta - mean_angle))))
        dx = frame["x_v"] - cx
        dy = frame["y_v"] - cy
        projected.append((dx * tangent_dir.real + dy * tangent_dir.imag) / radius)

    freqs, psd_y = _welch_psd([frame["y_v"] for frame in frames], rate, segment_s)
    _, psd_theta = _welch_psd(centered, rate, segment_s)
    _, psd_proj = _welch_psd(projected, rate, segment_s)
    asd_theta = np.sqrt(psd_theta)
    asd_y_route = np.sqrt(psd_y) / slope_y
    asd_proj = np.sqrt(psd_proj)
    asd_raw_y = np.sqrt(psd_y)

    ratio = asd_theta / asd_y_route
    band_mask = (freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])
    ratio_band = float(np.median(ratio[band_mask])) if np.any(band_mask) else float("nan")
    consistent = math.isfinite(ratio_band) and abs(ratio_band - 1.0) <= 0.1
    verdict = ("theta 与 y 两条路径在 3-50 Hz 的相位噪声一致（中位比 %.2f）" % ratio_band if consistent
               else "theta 与 y 两条路径在 3-50 Hz 的相位噪声不一致（中位比 %.2f）" % ratio_band)
    slope_tolerance = 0.05
    circle_verdict = (
        "相图 R=%.3f mV，%d/%d 段切线斜率中位 %.3f mV/rad（散布 ±%.1f%%），与 R 之比 %.3f；"
        % (radius * 1e3, metrics["accepted_count"], metrics["accepted_total"],
           metrics["tangent_median_v_per_rad"] * 1e3, metrics["tangent_relative_spread"] * 100.0,
           tangent_over_radius)
        + ("切线斜率在圆上等于半径" if abs(tangent_over_radius - 1.0) <= slope_tolerance
           else "切线斜率与半径不符"))

    summary = {
        "study_root": str(study_root),
        "gs200_target_ma": config.get("gs200_target_ma"),
        "rf_frequency_hz": config.get("Y_RF_FREQ_HZ"),
        "rf_vpp": config.get("Y_RF_VPP"),
        "circle_fit": fit,
        "diagram": {
            "grid_deg": grid,
            "phasors": [[float(z.real), float(z.imag)] for z in phasors],
            "phasor_angle_deg": metrics["phasor_angle_deg"],
            "tangent_v_per_rad": metrics["tangent_v_per_rad"],
            "tangent_median_v_per_rad": metrics["tangent_median_v_per_rad"],
            "tangent_relative_spread": metrics["tangent_relative_spread"],
            "tangent_over_radius": tangent_over_radius,
            "abs_dy_v_per_rad": metrics["abs_dy_v_per_rad"],
            "abs_dy_median_v_per_rad": metrics["abs_dy_median_v_per_rad"],
            "segment_center_deg": metrics["segment_center_deg"],
            "segment_dphi_deg": metrics["segment_dphi_deg"],
            "accepted": metrics["accepted"],
            "step_deg_median": metrics["step_deg_median"],
            "accepted_count": metrics["accepted_count"],
            "accepted_total": metrics["accepted_total"],
        },
        "working_point": {
            "coarse_candidate": chosen,
            "noise_phasor_angle_deg": phi_deg,
            "noise_slope_v_per_rad": slope_y,
            "noise_cos_phi": abs(math.cos(math.radians(phi_deg))),
            "fine_scan": fine_summary,
            "first_point_drift_v": diagram.get("first_point_drift_v"),
        },
        "rate_sa_s": rate,
        "welch_segment_s": segment_s,
        "segments": len(frames),
        "demod_dynamic": noise.get("demod_dynamic"),
        "time_constant_s": noise.get("time_constant_s"),
        "frame_quality": {"coarse": coarse_quality, "noise": noise_quality},
        "asd": {
            "frequency_hz": freqs.tolist(),
            "theta_rad_per_sqrt_hz": asd_theta.tolist(),
            "y_over_slope_rad_per_sqrt_hz": asd_y_route.tolist(),
            "projected_rad_per_sqrt_hz": asd_proj.tolist(),
            "y_v_per_sqrt_hz": asd_raw_y.tolist(),
            "theta_over_y_route_ratio": ratio.tolist(),
        },
        "band_3_50_hz": {
            "theta_rad_per_sqrt_hz": _band_median(freqs, asd_theta, BAND_HZ),
            "y_over_slope_rad_per_sqrt_hz": _band_median(freqs, asd_y_route, BAND_HZ),
            "projected_rad_per_sqrt_hz": _band_median(freqs, asd_proj, BAND_HZ),
            "theta_over_y_route_ratio": ratio_band,
            "mains_theta_rad_per_sqrt_hz": _mains_median(freqs, asd_theta),
        },
        "verdict": verdict,
        "circle_verdict": circle_verdict,
    }
    results = study_root / "results"
    results.mkdir(exist_ok=True)
    (results / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_diagram(results, summary)
    _plot_noise(results, summary)
    return summary


def _plot_diagram(results: Path, summary: dict[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = np.asarray(summary["diagram"]["phasors"], dtype=float)
    fit = summary["circle_fit"]
    grid = np.asarray(summary["diagram"]["grid_deg"], dtype=float)
    theta_line = np.linspace(0.0, 2.0 * math.pi, 361)
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8))
    ax = axes[0]
    ax.scatter(points[:, 0], points[:, 1], c=grid, cmap="twilight", s=28, zorder=3, label="measured")
    ax.plot(fit["center_x_v"] + fit["radius_v"] * np.cos(theta_line),
            fit["center_y_v"] + fit["radius_v"] * np.sin(theta_line), "--", c="0.35", lw=1.0, label="circle fit")
    ax.plot([fit["center_x_v"]], [fit["center_y_v"]], "+", c="k", ms=9, label="center")
    ax.set_xlabel("X (V)")
    ax.set_ylabel("Y (V)")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(f"phase diagram, R={fit['radius_v'] * 1e3:.3f} mV, rms={fit['rms_residual_v'] * 1e3:.3f} mV")
    ax.legend(loc="best", fontsize=8)
    ax = axes[1]
    centers = np.asarray(summary["diagram"]["segment_center_deg"], dtype=float)
    tangent = np.asarray(summary["diagram"]["tangent_v_per_rad"], dtype=float)
    abs_dy = np.asarray(summary["diagram"]["abs_dy_v_per_rad"], dtype=float)
    accepted = np.asarray(summary["diagram"]["accepted"], dtype=bool)
    radius = float(summary["circle_fit"]["radius_v"])
    ax.plot(centers, tangent, "o-", ms=3, lw=1.0, label="|dS/dphi| tangent slope")
    if np.any(~accepted):
        ax.plot(centers[~accepted], tangent[~accepted], "x", ms=8, mec="C3", label="excluded segment")
    ax.axhline(radius, ls="--", c="C3", lw=1.0, label=f"R = {radius * 1e3:.2f} mV/rad")
    ax.plot(centers, abs_dy, ":", lw=0.9, c="0.5", label="|dY/dphi| = R|cos phi|")
    ax.axvline(summary["working_point"]["noise_phasor_angle_deg"], ls=":", c="0.2", lw=0.9,
               label="noise-phase angle")
    ax.set_xlabel("segment center on the circle (deg)")
    ax.set_ylabel("V/rad")
    ax.set_title("tangent slope = R; |dY/dphi| is only its Y projection")
    ax.legend(loc="best", fontsize=7)
    fig.tight_layout()
    fig.savefig(results / "phase_diagram.png", dpi=160)
    fig.savefig(results / "phase_diagram.pdf")
    plt.close(fig)


def _plot_noise(results: Path, summary: dict[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    freq = np.asarray(summary["asd"]["frequency_hz"], dtype=float)
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.6))
    ax = axes[0]
    ax.loglog(freq, summary["asd"]["theta_rad_per_sqrt_hz"], lw=1.0, label="theta = atan2(y-y0, x-x0)")
    ax.loglog(freq, summary["asd"]["y_over_slope_rad_per_sqrt_hz"], lw=1.0,
              label="Y / K, K=%.4f V/rad" % summary["working_point"]["noise_slope_v_per_rad"])
    ax.loglog(freq, summary["asd"]["projected_rad_per_sqrt_hz"], lw=0.8, ls=":", label="tangent projection")
    ax.axvspan(*BAND_HZ, color="0.9", zorder=0)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("phase noise (rad/sqrt(Hz))")
    ax.set_title("phase sensitivity: theta vs Y route (same frames)")
    ax.legend(loc="best", fontsize=8)
    ax = axes[1]
    ax.semilogx(freq, summary["asd"]["theta_over_y_route_ratio"], lw=1.0)
    ax.axhline(1.0, ls="--", c="0.4", lw=0.9)
    ax.axvspan(*BAND_HZ, color="0.9", zorder=0)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("theta / Y-route ratio")
    ax.set_title("ratio of the two paths")
    fig.tight_layout()
    fig.savefig(results / "noise_paths.png", dpi=160)
    fig.savefig(results / "noise_paths.pdf")
    plt.close(fig)


def run_capture(args: argparse.Namespace) -> int:
    require_hardware_access("Mx 标准射频相位噪声脚本")
    root = runtime_root()
    params = _load_params(root, args)
    grid = [float(v) for v in (args.grid_deg or list(np.arange(0.0, 360.0, 15.0)))]
    if len(grid) < 6:
        raise ValueError(f"相图相位点数过少：{len(grid)}")
    current_ma = gs200_current_for_frequency_ma(params.y_rf_freq_hz, args.k_hz_per_ma, args.b_hz)
    validate_safety_limit(MAIN_FIELD_KEY, current_ma, load_safety_limits(root))

    study_dir = _create_study_directory(root, args.tag or "mx_std_phase_noise")
    ctx = StudyContext(params, root, study_dir)
    ctx.state.update(main_field_current_ma=float(current_ma),
                     gs200_target_ma=float(current_ma),
                     calibration_formula=dict(k_hz_per_ma=float(args.k_hz_per_ma), b_hz=float(args.b_hz)),
                     phase_grid_deg=list(grid),
                     Y_RF_FREQ_HZ=float(params.y_rf_freq_hz),
                     Y_RF_VPP=float(params.y_rf_vpp),
                     WELCH_SEGMENT_S=float(params.welch_segment_s))
    ctx.outputs["主磁场"] = f"GS200 {current_ma:.4f} mA（由 f={params.y_rf_freq_hz:g} Hz 与标定式反算）"
    ctx.save()
    print(f"研究目录：{study_dir.root}", flush=True)
    print(f"GS200 目标电流：{current_ma:.4f} mA（K={args.k_hz_per_ma:g} Hz/mA，b={args.b_hz:g} Hz，"
          f"f={params.y_rf_freq_hz:g} Hz）", flush=True)

    pump_dev: Any = None
    pump_initial_on: bool | None = None
    capture_failed: BaseException | None = None
    try:
        preparation.connect_and_synchronize(ctx)
        pump_dev = _connect_or_reuse_pump(ctx)
        pump_initial_on = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
        ctx.state["pump_control"] = dict(resource=PUMP_RESOURCE, device_id=PUMP_DEVICE_ID,
                                         channel=PUMP_CHANNEL, initial_output_on=pump_initial_on,
                                         restored=False)
        ctx.save()
        if not pump_initial_on:
            raise SafetyAbort(f"王湾pump ({PUMP_RESOURCE}) CH{PUMP_CHANNEL} 当前为 OFF；请先开启泵浦光")

        carrier = _prepare_standard_chain(ctx)
        ctx.save()
        _disable_z(ctx)
        readback = _set_main_field_ma(ctx, current_ma)
        ctx.state["readbacks"] = dict(gs200=readback, carrier=_carrier_verify(ctx, carrier, output=True))
        ctx.save()
        if params.system_settle_s > 0:
            sleep(params.system_settle_s)

        print(f"开始整周相位扫描：{len(grid)} 点 x {params.static_point_duration_s:g} s", flush=True)
        _, phasors, scan_info = _scan_phase(ctx, carrier, grid, float(params.static_point_duration_s))
        fit = circle_fit(phasors.real, phasors.imag)
        print(f"圆拟合 R={fit['radius_v'] * 1e3:.3f} mV，圆心 ({fit['center_x_v'] * 1e3:.2f}, "
              f"{fit['center_y_v'] * 1e3:.2f}) mV，残差 rms {fit['rms_residual_v'] * 1e3:.3f} mV", flush=True)
        chosen = choose_working_point(np.asarray(grid, dtype=float), phasors, fit)
        print(f"工作点候选：指令 {chosen['command_deg']:.1f}°，相位角 {chosen['phasor_angle_deg']:.1f}°，"
              f"R|cos phi|={chosen['analytic_dy_dphi_v_per_rad']:.6g} V/rad", flush=True)
        fine = _fine_scan(ctx, carrier, chosen["command_deg"], FINE_POINT_S,
                          acquisition.lockin_readback(ctx))
        slope = fine_scan_slope(fine["commands_deg"], fine["phasors"], fit)
        work_phase = float(slope["best_command_deg"])
        print(f"细扫定工作点：指令 {work_phase:.2f}°，相位角 {slope['best_phasor_angle_deg']:.2f}°，"
              f"局部斜率 {slope['slope_v_per_rad']:.6g} V/rad（圆模型 "
              f"{slope['analytic_v_per_rad']:.6g} V/rad）", flush=True)
        _carrier_set_phase(ctx, carrier, work_phase)
        ctx.state["working_point_deg"] = work_phase
        ctx.state["working_point"] = dict(
            command_deg=work_phase,
            phasor_angle_deg=float(slope["best_phasor_angle_deg"]),
            local_slope_v_per_rad=float(slope["slope_v_per_rad"]),
            analytic_slope_v_per_rad=float(slope["analytic_v_per_rad"]),
            circle_fit=fit, coarse_candidate=chosen, scan=scan_info)
        ctx.save()
        print(f"开始噪声采集：{params.noise_n_avg} 段 x {params.noise_duration_s:g} s（静态解调配置）",
              flush=True)
        _capture_noise(ctx, int(params.noise_n_avg), float(params.noise_duration_s))
        ctx.state["completion_status"] = "completed"
        ctx.save()
    except BaseException as exc:
        capture_failed = exc
        ctx.state["completion_status"] = "failed"
        ctx.state["failure_reason"] = str(exc)
        ctx.save()
    finally:
        cleanup_interrupt: KeyboardInterrupt | None = None
        pump_errors: list[str] = []
        if pump_dev is not None and pump_initial_on is not None:
            try:
                restored = _set_pump_output(pump_dev, pump_initial_on)
                ctx.state["pump_control"]["restored"] = True
                ctx.state["pump_control"]["restored_output_on"] = restored
            except (Exception, KeyboardInterrupt) as exc:
                if isinstance(exc, KeyboardInterrupt):
                    cleanup_interrupt = cleanup_interrupt or exc
                pump_errors.append(f"恢复 王湾pump CH{PUMP_CHANNEL} 失败：{exc}")
                ctx.state["pump_control"]["restore_error"] = str(exc)
        park_errors: list[str] = []
        if ctx.active:
            try:
                park_errors = preparation.park(ctx)
            except (Exception, KeyboardInterrupt) as exc:
                if isinstance(exc, KeyboardInterrupt):
                    cleanup_interrupt = cleanup_interrupt or exc
                park_errors = [f"安全收尾异常：{exc}"]
        devices = reversed(ctx.device_session.unique_devices())
        disconnect_report = run_safety_shutdown(
            disconnect_targets=(DisconnectTarget(str(i), dev) for i, dev in enumerate(devices)))
        prior_sd = ctx.state.get("safety_shutdown") or disconnect_report.to_dict()
        all_errors = [*pump_errors, *park_errors, *disconnect_report.errors]
        ctx.state["safety_shutdown"] = {**prior_sd, "completed": not all_errors, "errors": all_errors,
                                        "pump_errors": pump_errors, "action_errors": park_errors,
                                        "disconnect_errors": list(disconnect_report.disconnect_errors)}
        ctx.save()
        if capture_failed is None and cleanup_interrupt is not None:
            raise cleanup_interrupt
        if capture_failed is None and all_errors:
            raise SafetyAbort("安全收尾存在未完成项：" + "；".join(all_errors))

    if capture_failed is not None:
        raise capture_failed

    summary = analyze_study(study_dir.root, welch_segment_s=float(params.welch_segment_s))
    print(f"完成：{study_dir.root}", flush=True)
    print(f"相图：{study_dir.results / 'phase_diagram.png'}", flush=True)
    print(f"噪声路径对比：{study_dir.results / 'noise_paths.png'}", flush=True)
    print("3-50 Hz：" + json.dumps(summary["band_3_50_hz"], ensure_ascii=False), flush=True)
    print(summary["circle_verdict"], flush=True)
    print(summary["verdict"], flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="标准 Mx（不打脉冲）射频相位图与 theta/y 噪声谱路径对比")
    parser.add_argument("--analyze", type=Path, default=None, help="离线重算并重绘已有研究目录，不连接硬件")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与反算电流，不连接硬件")
    parser.add_argument("--tag", default=None, help="研究目录名后缀（默认 mx_std_phase_noise）")
    parser.add_argument("--vpp", type=float, default=None, help="覆盖 Y_RF_VPP（Vpp）")
    parser.add_argument("--freq-hz", type=float, default=None, help="覆盖射频频率（Hz），同时用于反算电流")
    parser.add_argument("--k-hz-per-ma", type=float, default=DEFAULT_K_HZ_PER_MA, help="主磁场标定斜率 K（Hz/mA）")
    parser.add_argument("--b-hz", type=float, default=DEFAULT_B_HZ, help="主磁场标定截距 b（Hz）")
    parser.add_argument("--grid-deg", type=float, nargs="*", default=None, help="整周相位网格（度，默认每 15°）")
    parser.add_argument("--point-duration", type=float, default=None, help="覆盖单点相图采样时长（s）")
    parser.add_argument("--duration", type=float, default=None, help="覆盖单段噪声采样时长（s）")
    parser.add_argument("--segments", type=int, default=None, help="覆盖噪声分段数")
    parser.add_argument("--temp-lag", type=float, default=None, help="覆盖宽带温控恢复等待（s）")
    parser.add_argument("--welch-segment", type=float, default=None, help="覆盖 Welch 段长（s）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.analyze is not None:
        summary = analyze_study(Path(args.analyze).resolve(), welch_segment_s=args.welch_segment)
        print(json.dumps(summary["band_3_50_hz"], ensure_ascii=False, indent=2))
        print(summary["verdict"])
        return 0
    if args.dry_run:
        freq = args.freq_hz if args.freq_hz is not None else 5000.0
        current_ma = gs200_current_for_frequency_ma(freq, args.k_hz_per_ma, args.b_hz)
        print(f"[dry-run] GS200 电流 = ({freq:g} - {args.b_hz:g}) / {args.k_hz_per_ma:g} = {current_ma:.4f} mA（不连接仪器）")
        try:
            params = _load_params(Path(__file__).resolve().parents[3], args)
        except (ValueError, OSError) as exc:
            print(f"[dry-run] 参数校验不通过：{exc}")
            return 2
        grid = list(args.grid_deg) if args.grid_deg else list(np.arange(0.0, 360.0, 15.0))
        print(f"[dry-run] 参数校验通过：f={params.y_rf_freq_hz:g} Hz、{params.y_rf_vpp:g} Vpp，"
              f"相图 {len(grid)} 点 x {params.static_point_duration_s:g} s（含预热丢弃与回采首点），"
              f"细扫 {int(2 * FINE_SPAN_DEG / FINE_STEP_DEG) + 1} 点 x {FINE_POINT_S:g} s，"
              f"噪声 {params.noise_n_avg} 段 x {params.noise_duration_s:g} s（Welch {params.welch_segment_s:g} s，静态解调）")
        return 0
    return run_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
