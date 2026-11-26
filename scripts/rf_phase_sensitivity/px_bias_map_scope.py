"""RF 关断、脉冲控制场开启时，扫描 X/Y 静态偏置，测量 Px（DHO924 CH1）时域波形随偏置的变化。

回答的问题：Px 上 5 kHz 的方波状调制是否随 X/Y 静态偏置变化——若不变，更像电学串扰；若随偏置变，
说明它来自极化/预cession 的物理响应。

流程概要：
1. 沿用 ``rf-phase-sensitivity`` 的准备步骤（时钟、温控、1200B、GS200 脉冲工作点、共同触发与
   Z 通道 5 kHz 突发脉冲），随后关闭 MFLI Y 射频并回读，本次测量全程 RF OFF、脉冲控制场 ON、
   泵浦 ON。
2. X/Y 偏置按 ``--x-range/--x-step/--y-range/--y-step`` 组成二维网格（默认 −0.1…+0.1 V、步长
   0.02 V，即 11×11=121 点），逐点写 DG X/Y 通道 DC 电平并回读；每点沿用温控开窗
   （``Temp_Switch`` 5 V→0 V）后取 ``--frames`` 帧（默认 3 帧 × 10 ms、``--rate`` 默认 1 MS/s，
   即每帧 1 万点），拍完恢复温控并等待。
3. ``finally`` 先恢复泵浦与 X/Y 偏置，再停示波器、恢复示波器原设置并断开，随后执行
   ``preparation.park(ctx)`` 安全关断（1200B OFF、Z 归零、MFLI RF OFF、温控恢复、GS200 回运行前状态）。
4. 出图（全英文）：``px_bias_map.png`` 三个二维图（5 kHz 同步 p2p、DC 均值、去同步残差 std），
   ``px_bias_folded_slices.png`` 沿 X 与沿 Y 的折叠波形切片，``px_bias_shapes.png`` 网格四角与
   中心的折叠波形对比；``--drift-control`` 模式另出 ``px_drift.png``（同步 p2p 随时间的单指数拟合，
   可用 ``--monitor-channel 2`` 同步记录线圈采样电阻电压做对照）；连同 ``px_bias_stats.csv``、``summary.json`` 与 README 写入
   ``data/rf_phase_sensitivity/_studies/<MMDD_HHMMSS>_px_bias_map/``。``--analyze <dir>`` 纯离线重绘。
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import numpy as np
from pyvisa.errors import VisaIOError
import yaml

try:
    import hardware_gate as _hg  # noqa: F401
except ImportError:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from hardware_gate import require_hardware_access, runtime_root
from oscilloscope import create_oscilloscope, create_scope_acquisition
from sds_acquisition.config import AcquisitionConfig, ChannelConfig, TriggerConfig
from signal_generator import DG900Instrument

from lab_workflows.common import data_root, load_safety_limits, validate_safety_limit
from lab_workflows.devices import create_signal_generator
from lab_workflows.experiment_modules.rf_phase_sensitivity import preparation, run_format
from lab_workflows.experiment_modules.rf_phase_sensitivity.models import RfPhaseSensitivityParams
from lab_workflows.experiment_modules.z_chain_common.keys import MAIN_FIELD_KEY
from lab_workflows.instrument_config import load_device_definitions
from lab_workflows.plotting import new_figure, plot_style_context, style_legend
from lab_workflows.steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from lab_workflows.steps.coil_drive_chain import SafetyAbort, sleep
from lab_workflows.steps.run_directory import write_run_config


PUMP_RESOURCE = "TCPIP0::192.168.124.32::5025::SOCKET"
PUMP_DEVICE_ID = "dg9q275000733"
PUMP_CHANNEL = 1
SCOPE_DEVICE_ID = "rigol_dho900_192_168_124_21"
PX_CHANNEL = 1
TRIGGER_CHANNEL = 3
X_KEY = "X_magnetic_field"
Y_KEY = "Y_magnetic_field"
DEFAULT_X_RANGE = (-0.10, 0.10)
DEFAULT_X_STEP = 0.02
DEFAULT_Y_RANGE = (-0.10, 0.10)
DEFAULT_Y_STEP = 0.02
DEFAULT_WINDOW_S = 10e-3
DEFAULT_RATE_SA_S = 1e6
DEFAULT_FRAMES = 3
DEFAULT_TRIGGER_LEVEL_V = 2.5
MAX_MEMORY_POINTS = 5_000_000
DHO_DEPTHS = (1_000, 10_000, 100_000, 1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000)


# ---------------------------------------------------------------- 研究目录与会话


@dataclass(slots=True)
class StudyDirectory:
    root: Path
    raw: Path
    results: Path
    config_path: Path

    def update_config(self, **updates: Any) -> dict[str, Any]:
        payload = dict(updates)
        write_run_config(self.config_path, payload)
        return payload


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
        self.state["study_type"] = "rf_phase_px_bias_map_scope"
        self.state["study_root"] = str(directory.root)

    def save(self) -> None:
        self.directory.update_config(**self.state)


def _create_study_directory(root: Path, tag: str) -> StudyDirectory:
    if any(ch in tag for ch in '/\\:*?"<>|\x00') or not tag.strip():
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
    raw = study_root / "raw"
    results = study_root / "results"
    raw.mkdir()
    results.mkdir(exist_ok=True)
    return StudyDirectory(root=study_root, raw=raw, results=results, config_path=study_root / "study_config.yaml")


def _load_params(root: Path) -> RfPhaseSensitivityParams:
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
    params = RfPhaseSensitivityParams.from_external(merged, schema_version=schema_ver)
    errors = params.validate(root)
    if errors:
        raise ValueError("参数校验失败：" + "；".join(errors))
    return params


def _grid(start: float, stop: float, step: float) -> np.ndarray:
    step = float(step)
    if not (math.isfinite(step) and step > 0):
        raise ValueError("偏置步长必须为有限正数")
    count = int(round((float(stop) - float(start)) / step)) + 1
    if count < 2 or count > 41:
        raise ValueError(f"偏置点数 {count} 不合法（需 2--41）")
    values = np.round(np.linspace(float(start), float(stop), count), 10)
    if not np.allclose(np.diff(values), step, atol=1e-9):
        raise ValueError("偏置范围不能被步长整除")
    return values


# ---------------------------------------------------------------- 泵浦、主磁场与偏置


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
                    raise SafetyAbort(
                        f"{PUMP_RESOURCE} CH{PUMP_CHANNEL} 已在本实验信号路径中占用，拒绝重复控制"
                    )
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
            time.sleep(0.3)
        try:
            if attempt > 0:
                pump_dev.connect()
            pump_dev.set_output(bool(enabled), channel=PUMP_CHANNEL)
            pump_dev.wait_for_operation_complete()
            pump_dev.raise_for_errors()
            actual = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
            if actual is not bool(enabled):
                raise SafetyAbort(
                    f"王湾pump ({PUMP_RESOURCE}) CH{PUMP_CHANNEL} 回读为 {'ON' if actual else 'OFF'}，"
                    f"与目标 {'ON' if enabled else 'OFF'} 不符"
                )
            return actual
        except VisaIOError as exc:
            last_exc = exc
            if attempt == 2 or not can_reconnect:
                raise
    raise SafetyAbort("王湾pump 输出切换失败")


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
            f"GS200 主磁场回读不符：设定 {target_ma:g} mA，回读 {actual_a * 1000.0:g} mA（output={actual_on}）"
        )
    return dict(output_on=actual_on, current_ma=float(actual_a * 1000.0))


def _bias_route(ctx: StudyContext, role: str) -> tuple[Any, int]:
    """本实验 signals 路由只接 X 通道；Y 偏置直接写 X 所在 DG4000 的另一通道。"""
    device, channel = preparation._route(ctx, "x")
    if role == "x":
        return device, channel
    entry = ctx.mapping.get(Y_KEY) or {}
    endpoint = entry.get("endpoint") or {}
    index = int(entry.get("channel") or endpoint.get("index") or 0)
    if index not in (1, 2):
        raise SafetyAbort(f"Y 偏置映射通道异常：{entry}")
    resource = getattr(device, "resource_string", None)
    mapped_resource = str(entry.get("resource") or "").strip()
    if mapped_resource and mapped_resource != resource:
        raise SafetyAbort(
            f"Y 偏置映射在 {mapped_resource}，与 X 通道所在 {resource} 不是同一台 DG4000；本脚本不跨机写偏置"
        )
    return device, index


def _set_bias(ctx: StudyContext, role: str, volts: float) -> dict[str, Any]:
    """把 X 或 Y 通道的 DC 偏置写到目标值并回读；0 V 时明确关输出。"""
    key = X_KEY if role == "x" else Y_KEY
    limits = load_safety_limits(ctx.root)
    volts = float(volts)
    validate_safety_limit(key, 0.0, limits)
    if volts:
        validate_safety_limit(key, volts, limits)
    device, channel = _bias_route(ctx, role)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.set_output(False, channel=channel)
    if preparation.dg_output(device, channel):
        raise SafetyAbort(f"{role.upper()} 偏置未回读为 OFF，拒绝改写 DC 电平")
    # DG4162 在 DC 波形下忽略 APPLy/OFFSET 写入；先切正弦写偏置再切回 DC（与 preparation._dc_level 同一处理）。
    preparation._dc_level(device, channel, volts)  # noqa: SLF001
    device.set_output(abs(volts) > 0.0, channel=channel)
    device.wait_for_operation_complete()
    actual = float(device.get_dc_voltage(channel=channel))
    on = bool(device.get_output(channel=channel))
    if on is not (abs(volts) > 0.0) or abs(actual - volts) > 1e-4:
        raise SafetyAbort(f"{role.upper()} 偏置回读不符：设定 {volts:+.4f} V，回读 {actual:+.4f} V（output={on}）")
    ctx.state[f"{role}_bias_v"] = volts
    return dict(output_on=on, dc_v=actual)


# ---------------------------------------------------------------- 示波器


def _scope_definition(ctx: StudyContext, device_id: str) -> dict[str, Any]:
    library = load_device_definitions(ctx.root)
    defn = library.get(device_id)
    if defn is None:
        raise ValueError(f"设备库缺少示波器 {device_id}")
    if defn.instrument != "oscilloscope":
        raise ValueError(f"{device_id} 不是示波器：{defn.instrument}")
    return defn.to_config()


def _scope_snapshot(scope: Any) -> dict[str, Any]:
    channels = {}
    for ch in (1, 2, 3, 4):
        try:
            channels[ch] = dict(
                enabled=bool(scope.get_channel_state(ch)),
                scale=float(scope.get_channel_scale(ch)),
                offset=float(scope.get_channel_offset(ch)),
                coupling=scope.get_channel_coupling(ch).strip(),
                probe=float(scope.get_channel_probe(ch)),
                impedance=str(scope.get_channel_impedance(ch)),
            )
        except Exception as exc:
            channels[ch] = {"error": str(exc)}
    return dict(
        idn=scope.idn().strip(),
        timebase_scale=float(scope.get_timebase_scale()),
        timebase_delay=float(scope.get_timebase_delay()),
        trigger_mode=scope.get_trigger_mode().strip(),
        trigger_source=scope.get_trigger_source().strip(),
        trigger_slope=scope.get_trigger_slope().strip(),
        trigger_level=float(scope.get_trigger_level()),
        sampling_rate=float(scope.get_sampling_rate()),
        channels=channels,
    )


def _prepare_scope_channels(scope: Any, before: dict[str, Any], *, monitor_channel: int = 0,
                            monitor_scale: float = 0.5, monitor_offset: float = 0.0) -> dict[str, Any]:
    chosen = {}
    wanted = [PX_CHANNEL, TRIGGER_CHANNEL]
    if monitor_channel:
        wanted.append(int(monitor_channel))
    for ch in wanted:
        info = before["channels"].get(ch) or {}
        if not info.get("enabled"):
            default_scale = (0.2 if ch == PX_CHANNEL else float(monitor_scale) if ch == monitor_channel else 1.0)
            scope.set_channel_state(ch, True)
            scope.set_channel_scale(ch, default_scale)
            scope.set_channel_offset(ch, float(monitor_offset) if ch == monitor_channel else 0.0)
            scope.set_channel_coupling(ch, "DC")
            info = dict(enabled=True, scale=default_scale,
                        offset=float(monitor_offset) if ch == monitor_channel else 0.0, coupling="DC",
                        probe=1.0, impedance="ONEMeg", defaulted=True)
        if monitor_channel and ch == int(monitor_channel) and not info.get("defaulted"):
            scope.set_channel_scale(ch, float(monitor_scale))
            scope.set_channel_offset(ch, float(monitor_offset))
            info = dict(info, scale=float(monitor_scale), offset=float(monitor_offset))
        chosen[ch] = info
    return chosen


def _acquisition_config(window_s: float, rate: float, channels: dict[int, dict[str, Any]],
                        trigger_level: float, enabled: set[int] | None = None) -> AcquisitionConfig:
    enabled_channels = set(enabled or {PX_CHANNEL, TRIGGER_CHANNEL})
    return AcquisitionConfig(
        sampling_rate=rate,
        sampling_time=window_s,
        channels=[
            ChannelConfig(number=ch,
                          enabled=ch in enabled_channels,
                          scale=float(channels[ch]["scale"]) if ch in channels else 1.0,
                          offset=float(channels[ch]["offset"]) if ch in channels else 0.0,
                          coupling=str(channels[ch].get("coupling", "DC")) if ch in channels else "DC",
                          probe=float(channels[ch].get("probe", 1.0)) if ch in channels else 1.0,
                          impedance=str(channels[ch].get("impedance", "ONEMeg")) if ch in channels else "ONEMeg")
            for ch in (1, 2, 3, 4)
        ],
        trigger=TriggerConfig(mode="AUTO", source=f"C{TRIGGER_CHANNEL}", slope="RISing", level=trigger_level),
        acquire_delay=0.1,
    )


def _restore_scope(scope: Any, before: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for ch in (1, 2, 3, 4):
        info = before["channels"].get(ch) or {}
        if "error" in info:
            continue
        try:
            scope.set_channel_state(ch, bool(info["enabled"]))
        except Exception as exc:
            errors.append(f"恢复 C{ch} 开关: {exc}")
    try:
        scope.set_trigger_mode(before["trigger_mode"])
        scope.set_trigger_level(before["trigger_level"])
        scope.set_timebase_scale(before["timebase_scale"])
        scope.set_timebase_delay(before["timebase_delay"])
    except Exception as exc:
        errors.append(f"恢复时基/触发: {exc}")
    return errors


# ---------------------------------------------------------------- 采集



def _freeze_frame(scope: Any) -> None:
    """按示波器当前时基/深度冻结一帧（只布防-触发-停止，不读回）。"""
    scope.set_trigger_mode("SINGle")
    scope.trigger_run()
    if not scope.wait_for_trigger(0.02, 2.0):
        scope.set_trigger_mode("AUTO")
        scope.write(":TFORce")
        deadline = time.monotonic() + 5.0
        while scope.trigger_status() != "Stop":
            if time.monotonic() > deadline:
                raise TimeoutError("冻结帧等待 STOP 超时")
            time.sleep(0.05)
    scope.trigger_stop()


def _read_frozen(acq: Any, tdiv: float) -> tuple[Any, Any]:
    """读取已经冻结的帧；此时温控已恢复，读回不占用关温控窗口。"""
    return acq.acquire_channel(PX_CHANNEL, tdiv), acq.acquire_channel(TRIGGER_CHANNEL, tdiv)


def _capture_point(ctx: StudyContext, acq: Any, config: AcquisitionConfig | None, x_v: float, y_v: float,
                   args: argparse.Namespace, record: dict[str, Any]) -> None:
    record["readbacks"] = dict(x=_set_bias(ctx, "x", x_v), y=_set_bias(ctx, "y", y_v))
    ctx.save()
    if float(args.settle) > 0:
        sleep(float(args.settle))
    per_frame = bool(config is None and getattr(args, "temp_per_frame", True))
    if not per_frame:
        preparation.write_temperature(ctx, False)
    try:
        if not per_frame:
            sleep(float(args.temp_off_settle))
        for frame in range(int(args.frames)):
            if config is None:
                if per_frame:
                    # 只为“冻结瞬间”关温控：稳定 → 冻结 → 立即恢复，读回在温控恢复后进行。
                    preparation.write_temperature(ctx, False)
                    sleep(float(args.temp_off_settle))
                    t_off = time.monotonic()
                    try:
                        _freeze_frame(acq._inst)  # noqa: SLF001
                    finally:
                        preparation.write_temperature(ctx, True)
                    off_window = time.monotonic() - t_off
                    record.setdefault("temp_off_windows_s", []).append(off_window)
                px, trig = _read_frozen(acq, float(args.trigger_tdiv))
                if per_frame:
                    # 恢复窗口固定为 --temp-lag（默认 1 s），关闭时长另行记录。
                    recover = float(args.temp_lag)
                    if recover > 0:
                        sleep(recover)
                    record.setdefault("temp_recover_windows_s", []).append(recover)
            else:
                acq.start_acquisition(config)
                acq.stop_acquisition()
                px = acq.acquire_channel(PX_CHANNEL, config.timebase_scale)
                trig = acq.acquire_channel(TRIGGER_CHANNEL, config.timebase_scale)
            payload = dict(t_px=px.time, v_px=px.voltage, t_trigger=trig.time, v_trigger=trig.voltage,
                           x_v=float(x_v), y_v=float(y_v), frame=frame,
                           sampling_rate=1.0 / float(px.preamble_dict["xincrement"]),
                           point_num=int(px.preamble_dict["points"]))
            monitor_ch = int(getattr(args, "monitor_channel", 0) or 0)
            if monitor_ch:
                mon = acq.acquire_channel(monitor_ch,
                                          config.timebase_scale if config is not None else float(args.trigger_tdiv))
                payload.update(t_mon=mon.time, v_mon=mon.voltage, monitor_channel=monitor_ch)
            path = ctx.directory.raw / f"{record['label']}_frame{frame:02d}.npz"
            np.savez_compressed(path, **payload)
            record["frames"].append(path.relative_to(ctx.directory.root).as_posix())
            ctx.save()
    finally:
        if not per_frame:
            preparation.write_temperature(ctx, True)
            sleep(float(args.temp_lag))


def run_capture(args: argparse.Namespace) -> int:
    require_hardware_access("Px X/Y 偏置二维扫描")
    root = runtime_root()
    params = _load_params(root)
    if args.drift_control:
        x_grid = np.asarray([float(args.drift_x)])
        y_grid = np.asarray([float(args.drift_y)])
        repeats = int(args.drift_points)
        if not 2 <= repeats <= 200:
            raise ValueError(f"漂移对照点数 {repeats} 不合法（需 2--200）")
    else:
        x_grid = _grid(*args.x_range, args.x_step)
        y_grid = _grid(*args.y_range, args.y_step)
        repeats = 1
    limits = load_safety_limits(root)
    for value in (float(x_grid.min()), float(x_grid.max())):
        validate_safety_limit(X_KEY, value, limits)
    for value in (float(y_grid.min()), float(y_grid.max())):
        validate_safety_limit(Y_KEY, value, limits)
    study_dir = _create_study_directory(root, args.tag)
    ctx = StudyContext(params, root, study_dir)
    ctx.state["grid"] = dict(x_values=[float(v) for v in x_grid], y_values=[float(v) for v in y_grid], unit="V")
    ctx.state["drift_control"] = dict(enabled=bool(args.drift_control), points=repeats,
                                      x_v=float(x_grid[0]), y_v=float(y_grid[0]))
    ctx.state["scope"] = {}
    ctx.save()

    pump_dev: Any = None
    scope: Any = None
    scope_before: dict[str, Any] | None = None
    pump_initial_on: bool | None = None
    capture_failed: BaseException | None = None
    points: list[dict[str, Any]] = []
    try:
        preparation.connect_and_synchronize(ctx)
        print("[阶段] 设备连接与时钟同步完成", flush=True)
        pump_dev = _connect_or_reuse_pump(ctx)
        pump_initial_on = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
        ctx.state["pump_control"] = dict(resource=PUMP_RESOURCE, device_id=PUMP_DEVICE_ID,
                                         channel=PUMP_CHANNEL, initial_output_on=pump_initial_on, restored=False)
        ctx.save()
        if not pump_initial_on:
            raise SafetyAbort(
                f"王湾pump ({PUMP_RESOURCE}) CH{PUMP_CHANNEL} 当前为 OFF；请先开启泵浦光再启动偏置扫描"
            )

        scope_cfg = _scope_definition(ctx, args.scope_device)
        scope = create_oscilloscope(scope_cfg)
        scope.connect()
        scope_before = _scope_snapshot(scope)
        monitor_ch = int(getattr(args, "monitor_channel", 0) or 0)
        if monitor_ch in (PX_CHANNEL, TRIGGER_CHANNEL):
            raise ValueError(f"监视通道 CH{monitor_ch} 与 Px/触发通道冲突")
        channels = _prepare_scope_channels(scope, scope_before, monitor_channel=monitor_ch,
                                           monitor_scale=float(args.monitor_scale),
                                           monitor_offset=float(args.monitor_offset))
        enabled = {PX_CHANNEL, TRIGGER_CHANNEL} | ({monitor_ch} if monitor_ch else set())
        acq = create_scope_acquisition(scope, maximum_points=MAX_MEMORY_POINTS)
        if args.use_scope_config:
            # 尊重面板：时基与存储深度由操作者在示波器上设定，采样率由仪器自己推出来；不改任何设置，直接冻结读帧。
            args.trigger_tdiv = float(scope.get_timebase_scale())
            depth_text = scope.get_memory_depth().strip()
            actual_rate = float(scope.get_sampling_rate())
            if depth_text.upper() == "AUTO":
                raise ValueError("示波器存储深度仍为 AUTO；请先在面板设定固定深度（如 10k）后重跑")
            print(f"[阶段] 使用示波器现有配置：{args.trigger_tdiv*1e6:g} us/div、深度 {depth_text}、"
                  f"采样率 {actual_rate/1e6:g} MSa/s（不改设置）", flush=True)
            config = None
        else:
            config = _acquisition_config(float(args.window), float(args.rate), channels,
                                         float(args.trigger_level), enabled=enabled)
            depth_cap = int(round(float(args.window) * float(args.rate)))
            if depth_cap in DHO_DEPTHS:
                # 窗口 × 采样率落在合法深度档时走严格深度：直接写 MDEPth/SRATe，不经过 AUTO 上限协商，
                # 避免回退到 62.5 万点（线圈驱动链路实验用的就是这条 1 万点的路径）。
                print(f"[阶段] 严格深度 {depth_cap} 点（{float(args.rate)/1e6:g} MSa/s）", flush=True)
            else:
                acq.use_auto_depth(depth_cap)
            acq.apply_config(config)
        print("[阶段] 示波器采集配置完成", flush=True)
        ctx.state["scope"] = dict(
            device_id=args.scope_device, before=scope_before, channels=channels,
            window_s=(float(args.trigger_tdiv) * 10.0 if args.use_scope_config else float(args.window)),
            rate_sa_s=(float(scope.get_sampling_rate()) if args.use_scope_config else float(args.rate)),
            frames=int(args.frames),
            trigger=dict(mode="AUTO", source=f"C{TRIGGER_CHANNEL}", slope="RISing", level=float(args.trigger_level)),
            pulse_period_s=1.0 / float(params.y_rf_freq_hz),
            px_channel=PX_CHANNEL, trigger_channel=TRIGGER_CHANNEL,
            monitor=dict(channel=monitor_ch, scale=float(args.monitor_scale), offset=float(args.monitor_offset)),
            actual=(dict(points=int(scope.get_actual_points()),
                         sample_rate_sa_s=float(scope.get_sampling_rate()),
                         timebase_s_div=float(scope.get_timebase_scale()))
                    if args.use_scope_config else
                    dict(points=int(acq._snapshot["actual_points"]),  # noqa: SLF001
                         sample_rate_sa_s=float(acq._snapshot["actual_sample_rate_sa_s"]),
                         timebase_s_div=float(acq._snapshot["actual_timebase_s_div"]))),
        )
        ctx.save()

        print("[阶段] 准备输出（脉冲链布防、RF 关闭）", flush=True)
        preparation.prepare_measurement(ctx)
        dev = ctx.devices["hf2"].device_id
        if int(ctx.devices["hf2"].get_int(f"/{dev}/sigouts/0/on")) != 0:
            ctx.devices["hf2"].set_int(f"/{dev}/sigouts/0/on", 0)
            ctx.devices["hf2"].sync()
        rf_off = int(ctx.devices["hf2"].get_int(f"/{dev}/sigouts/0/on"))
        if rf_off != 0:
            raise SafetyAbort(f"MFLI Out1 未回读为 OFF：{rf_off}")
        ctx.state["rf_control"] = dict(output_on=False)
        ctx.save()
        print("[阶段] 输出准备完成（脉冲开、RF 关），开始扫描", flush=True)

        interleave = bool(getattr(args, "interleave", False)) and not bool(args.drift_control)
        ref_every = max(1, int(getattr(args, "ref_every", 2)))
        seq: list[tuple[str, int, int, int, float, float]] = []
        if interleave:
            counter = 0
            for ix, x_v in enumerate(x_grid):
                for iy, y_v in enumerate(y_grid):
                    for rep in range(repeats):
                        if counter % ref_every == 0:
                            seq.append(("ref", -1, -1, 0, 0.0, 0.0))
                        seq.append(("point", ix, iy, rep, float(x_v), float(y_v)))
                        counter += 1
            seq.append(("ref", -1, -1, 0, 0.0, 0.0))
            ctx.state["interleave"] = dict(enabled=True, ref_every=ref_every, ref_point=[0.0, 0.0],
                                           repeats=repeats)
            ctx.save()
            print(f"[阶段] 交错参考点模式：每 {ref_every} 个偏置点插一次 (0,0)，共 {len(seq)} 次采集", flush=True)
        else:
            for ix, x_v in enumerate(x_grid):
                for iy, y_v in enumerate(y_grid):
                    for rep in range(repeats):
                        seq.append(("point", ix, iy, rep, float(x_v), float(y_v)))
        total = len(seq)
        done = 0
        ref_counter = 0
        t_first: float | None = None
        for kind, ix, iy, rep, x_v, y_v in seq:
            done += 1
            if kind == "ref":
                label = f"ref{ref_counter:02d}"
                ref_counter += 1
                shown = "参考点 X=+0.000 V, Y=+0.000 V"
            else:
                label = f"d{rep:02d}" if repeats > 1 else f"x{ix:02d}_y{iy:02d}"
                shown = (f"X={x_v:+.3f} V, Y={y_v:+.3f} V"
                         f"{'' if repeats == 1 else f'（第 {rep + 1}/{repeats} 次）'}")
            t_start = time.time()
            if t_first is None:
                t_first = t_start
            record = dict(label=label, x_v=float(x_v), y_v=float(y_v), rep=rep, kind=kind,
                          t_start=t_start, elapsed_s=t_start - t_first,
                          frames=[], readbacks={})
            points.append(record)
            print(f"[{done}/{total}] {shown}", flush=True)
            _capture_point(ctx, acq, config, float(x_v), float(y_v), args, record)
            record["done"] = True
            record["t_end"] = time.time()
            ctx.state["points"] = points
            ctx.save()
        ctx.state["completion_status"] = "completed"
        ctx.save()
    except BaseException as exc:
        capture_failed = exc
        ctx.state["completion_status"] = "failed"
        ctx.state["failure_reason"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        cleanup_interrupt: KeyboardInterrupt | None = None
        pump_errors: list[str] = []
        bias_errors: list[str] = []
        scope_errors: list[str] = []
        if pump_dev is not None and pump_initial_on is not None:
            try:
                restored = _set_pump_output(pump_dev, pump_initial_on)
                ctx.state["pump_control"]["restored"] = True
                ctx.state["pump_control"]["restored_output_on"] = restored
            except (Exception, KeyboardInterrupt) as exc:
                if isinstance(exc, KeyboardInterrupt):
                    cleanup_interrupt = cleanup_interrupt or exc
                pump_errors.append(f"恢复 王湾pump CH{PUMP_CHANNEL} 失败：{exc}")
        if ctx.devices.get("signals") is not None and ctx.active:
            for role in ("x", "y"):
                try:
                    _set_bias(ctx, role, 0.0)
                    ctx.state[f"{role}_bias_restored"] = True
                except (Exception, KeyboardInterrupt) as exc:
                    if isinstance(exc, KeyboardInterrupt):
                        cleanup_interrupt = cleanup_interrupt or exc
                    bias_errors.append(f"X/Y 偏置归零失败（{role}）：{exc}")
        if scope is not None:
            try:
                scope.trigger_stop()
            except Exception as exc:
                scope_errors.append(f"停止示波器触发: {exc}")
            if scope_before is not None:
                try:
                    scope_errors.extend(_restore_scope(scope, scope_before))
                except Exception as exc:
                    scope_errors.append(f"恢复示波器原设置: {exc}")
            ctx.state.setdefault("scope", {})["restore_errors"] = scope_errors

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
            disconnect_targets=(DisconnectTarget(str(i), dev) for i, dev in enumerate(devices))
        )
        if scope is not None:
            try:
                scope.disconnect()
            except Exception as exc:
                scope_errors.append(f"断开示波器: {exc}")
        prior = ctx.state.get("safety_shutdown") or disconnect_report.to_dict()
        all_errors = [*pump_errors, *bias_errors, *scope_errors, *park_errors, *disconnect_report.errors]
        ctx.state["safety_shutdown"] = {
            **prior,
            "completed": not all_errors,
            "errors": all_errors,
            "pump_errors": pump_errors,
            "bias_errors": bias_errors,
            "scope_errors": scope_errors,
            "action_errors": park_errors,
            "disconnect_errors": list(disconnect_report.disconnect_errors),
        }
        ctx.save()
        if capture_failed is None and cleanup_interrupt is not None:
            raise cleanup_interrupt
        if capture_failed is None and all_errors:
            raise SafetyAbort("安全收尾存在未完成项：" + "；".join(all_errors))

    if capture_failed is not None:
        raise capture_failed

    summary = analyze_study(study_dir.root)
    print(f"完成采集与出图：{study_dir.root}")
    _print_summary(summary)
    return 0


# ---------------------------------------------------------------- 离线分析


def _read_config(study_dir: Path) -> dict[str, Any]:
    path = study_dir / "study_config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"找不到研究配置：{path}")
    return run_format.read_config(path) or {}


def _rising_edges(t: np.ndarray, v: np.ndarray, min_gap_s: float = 0.0) -> np.ndarray:
    if t.size < 3:
        return np.empty(0)
    v_min = float(np.percentile(v, 1.0))
    v_max = float(np.percentile(v, 99.0))
    if not math.isfinite(v_min) or not math.isfinite(v_max) or v_max - v_min < 1e-3:
        return np.empty(0)
    threshold = 0.5 * (v_min + v_max)
    above = v >= threshold
    idxs = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    times = []
    for idx in idxs:
        v0, v1 = float(v[idx - 1]), float(v[idx])
        span = v1 - v0
        frac = 0.5 if span == 0 else (threshold - v0) / span
        times.append(float(t[idx - 1] + frac * (t[idx] - t[idx - 1])))
    if not times:
        return np.empty(0)
    keep = [times[0]]
    gap = max(0.2 * (t[1] - t[0]), float(min_gap_s))
    for value in times[1:]:
        if value - keep[-1] > gap:
            keep.append(value)
    return np.asarray(keep, dtype=float)


def _folded(t: np.ndarray, v: np.ndarray, edges: np.ndarray, period_s: float, n_points: int) -> np.ndarray:
    if edges.size == 0 or not (period_s > 0):
        return np.empty(0)
    offsets = np.linspace(0.0, period_s, n_points, endpoint=False)
    acc = np.zeros_like(offsets)
    used = 0
    for edge in edges:
        mask = (t >= edge) & (t < edge + period_s)
        if int(mask.sum()) < 3:
            continue
        acc += np.interp(offsets + edge, t[mask], v[mask])
        used += 1
    return acc / used if used else np.empty(0)


def _point_files(study_dir: Path, label: str) -> list[Path]:
    return sorted((study_dir / "raw").glob(f"{label}_frame*.npz"))


def _point_metrics(study_dir: Path, label: str, period_s: float) -> dict[str, Any] | None:
    """单点的同步分量（折叠均值）与去同步残差（逐帧扣掉该帧的周期平均）。"""
    files = _point_files(study_dir, label)
    if not files:
        return None
    folds, means, residuals = [], [], []
    plateau_a, plateau_b = [], []
    mon_folds, mon_means = [], []
    x_v = y_v = None
    for path in files:
        with np.load(path, allow_pickle=False) as data:
            t = data["t_px"]
            v = data["v_px"]
            x_v = float(data["x_v"])
            y_v = float(data["y_v"])
            edges = _rising_edges(data["t_trigger"], data["v_trigger"], min_gap_s=0.5 * period_s)
            mon = (data["t_mon"], data["v_mon"]) if "v_mon" in data.files else None
        period_samples = max(int(round(period_s / float(np.median(np.diff(t))))), 8)
        folded = _folded(t, v, edges, period_s, period_samples)
        if folded.size:
            folds.append(folded)
            m = (v.size // folded.size) * folded.size
            grid = v[:m].reshape(-1, folded.size)
            seg = grid.mean(axis=0)
            residuals.append(float(np.std(grid - seg, ddof=1)))
            # 两个平台的中位电平：避开沿附近，各取半周期中部 30%
            u = np.linspace(0.0, period_s, folded.size, endpoint=False)
            seg_a = folded[(u >= 0.10 * period_s) & (u < 0.40 * period_s)]
            seg_b = folded[(u >= 0.60 * period_s) & (u < 0.90 * period_s)]
            if seg_a.size and seg_b.size:
                plateau_a.append(float(np.median(seg_a)))
                plateau_b.append(float(np.median(seg_b)))
        means.append(float(np.mean(v)))
        if mon is not None:
            mon_folded = _folded(mon[0], mon[1], edges, period_s, period_samples)
            if mon_folded.size:
                mon_folds.append(mon_folded)
            mon_means.append(float(np.mean(mon[1])))
    if not folds:
        return None
    folded_mean = np.mean(folds, axis=0)
    monitor = None
    if mon_folds:
        monitor = dict(sync_p2p_v=float(np.ptp(np.mean(mon_folds, axis=0))),
                       mean_v=float(np.mean(mon_means)),
                       folded=np.mean(mon_folds, axis=0))
    pa = float(np.mean(plateau_a)) if plateau_a else None
    pb = float(np.mean(plateau_b)) if plateau_b else None
    return dict(
        label=label, x_v=x_v, y_v=y_v, frames=len(files),
        mean_v=float(np.mean(means)), std_v=float(np.std(means, ddof=1)) if len(means) > 1 else 0.0,
        sync_p2p_v=float(np.ptp(folded_mean)),
        plateau_a_v=pa, plateau_b_v=pb,
        plateau_diff_v=(pb - pa) if (pa is not None and pb is not None) else None,
        plateau_diff_abs_v=abs(pb - pa) if (pa is not None and pb is not None) else None,
        residual_std_v=float(np.mean(residuals)) if residuals else 0.0,
        folded=folded_mean, folds=np.asarray(folds, dtype=float), monitor=monitor,
    )


def _fit_decay(t_s: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """单指数衰减 A·exp(-t/τ)+C 的网格搜索拟合；返回 τ(min)、幅度、偏置与 R²。"""
    best: tuple[float, float, np.ndarray, np.ndarray] | None = None
    for tau_s in np.geomspace(20.0, 7200.0, 600):
        basis = np.column_stack([np.exp(-t_s / tau_s), np.ones_like(t_s)])
        coef, *_ = np.linalg.lstsq(basis, y, rcond=None)
        pred = basis @ coef
        sse = float(np.sum((y - pred) ** 2))
        if best is None or sse < best[0]:
            best = (sse, float(tau_s), coef, pred)
    assert best is not None
    sse, tau_s, coef, pred = best
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return dict(tau_min=tau_s / 60.0, amplitude=float(coef[0]), offset=float(coef[1]),
                r2=float(1.0 - sse / ss_tot) if ss_tot > 0 else float("nan"), predicted=pred)


def analyze_drift(study_dir: Path, config: dict[str, Any], scope_cfg: dict[str, Any]) -> dict[str, Any]:
    drift = config.get("drift_control") or {}
    period = float(scope_cfg.get("pulse_period_s") or 200e-6)
    records = [p for p in (config.get("points") or []) if p.get("done")]
    if not records:
        raise ValueError(f"{study_dir} 下没有可用的漂移点")
    times, sync, mean, resid, folds = [], [], [], [], {}
    mon_sync, mon_means, mon_folds = [], [], {}
    for rec in records:
        metrics = _point_metrics(study_dir, rec["label"], period)
        if metrics is None:
            continue
        times.append(float(rec.get("elapsed_s", len(times) * 60.0)))
        sync.append(metrics["sync_p2p_v"] * 1e3)
        mean.append(metrics["mean_v"] * 1e3)
        resid.append(metrics["residual_std_v"] * 1e3)
        folds[rec["label"]] = (metrics["folded"], times[-1])
        if metrics.get("monitor"):
            mon_sync.append(metrics["monitor"]["sync_p2p_v"] * 1e3)
            mon_means.append(metrics["monitor"]["mean_v"] * 1e3)
            mon_folds[rec["label"]] = (metrics["monitor"]["folded"], times[-1])
    t_arr = np.asarray(times)
    sync_arr = np.asarray(sync)
    fit = _fit_decay(t_arr, sync_arr)
    mon_arr = np.asarray(mon_sync) if len(mon_sync) == len(t_arr) else None
    mon_mean_arr = np.asarray(mon_means) if len(mon_means) == len(t_arr) else None
    mon_fit = _fit_decay(t_arr, mon_arr) if mon_arr is not None and mon_arr.size > 3 else None
    results = study_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    summary = dict(
        study_dir=str(study_dir), mode="drift_control", x_v=drift.get("x_v"), y_v=drift.get("y_v"),
        points=len(t_arr), duration_min=float(t_arr[-1] / 60.0),
        sync_p2p_first_mv=float(sync_arr[0]), sync_p2p_last_mv=float(sync_arr[-1]),
        sync_p2p_min_mv=float(np.min(sync_arr)), sync_p2p_max_mv=float(np.max(sync_arr)),
        decay_tau_min=fit["tau_min"], decay_amplitude_mv=fit["amplitude"],
        decay_offset_mv=fit["offset"], decay_r2=fit["r2"],
        monitor_channel=int((scope_cfg.get("monitor") or {}).get("channel") or 0),
        monitor_p2p_first_mv=float(mon_arr[0]) if mon_arr is not None else None,
        monitor_p2p_last_mv=float(mon_arr[-1]) if mon_arr is not None else None,
        monitor_decay_tau_min=mon_fit["tau_min"] if mon_fit else None,
        monitor_decay_r2=mon_fit["r2"] if mon_fit else None,
    )
    (results / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (results / "px_drift_stats.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["label", "elapsed_min", "mean_mv", "sync_p2p_mv", "residual_std_mv", "fit_mv"])
        for idx, rec in enumerate(records):
            if idx >= len(t_arr):
                break
            writer.writerow([rec["label"], f"{t_arr[idx]/60:.3f}", f"{mean[idx]:.4f}",
                             f"{sync_arr[idx]:.4f}", f"{resid[idx]:.4f}", f"{fit['predicted'][idx]:.4f}"])

    with plot_style_context("paper"):
        ncols = 4 if mon_arr is not None else 3
        fig, axes = new_figure(figsize=(12.4 if ncols == 4 else 9.6, 3.2), nrows=1, ncols=ncols,
                               constrained_layout=True)
        ax_t, ax_s, ax_f = axes[0], axes[1], axes[-1]
        if ncols == 4:
            ax_m = axes[2]
            ax_m.plot(t_arr / 60.0, mon_arr, "o-", color="#117a65", ms=3.0, lw=1.0,
                      label=f"CH{int((scope_cfg.get('monitor') or {}).get('channel') or 2)} sync p2p")
            if mon_fit is not None:
                ax_m.plot(t_arr / 60.0, mon_fit["predicted"], "-", color="#d55e00", lw=1.2,
                          label=f"fit τ = {mon_fit['tau_min']:.1f} min (R²={mon_fit['r2']:.3f})")
            ax_m.plot(t_arr / 60.0, mon_mean_arr, "s--", color="#999999", ms=2.6, lw=0.8, label="mean")
            ax_m.set(xlabel="Elapsed time (min)", ylabel="mV", title="Monitor channel (coil current)")
            ax_m.grid(alpha=0.2)
            style_legend(ax_m, loc="upper right", fontsize=6.0, framealpha=0.9)
        ax_t.plot(t_arr / 60.0, sync_arr, "o-", color="#2a6f97", ms=3.2, lw=1.0, label="5 kHz-sync p2p")
        ax_t.plot(t_arr / 60.0, fit["predicted"], "-", color="#d55e00", lw=1.2,
                  label=f"fit τ = {fit['tau_min']:.1f} min (R²={fit['r2']:.3f})")
        ax_t.set(xlabel="Elapsed time (min)", ylabel="Px sync p2p (mV)",
                 title=f"Drift control X={drift.get('x_v', 0):+.3f} V, Y={drift.get('y_v', 0):+.3f} V")
        ax_t.grid(alpha=0.2)
        style_legend(ax_t, loc="upper right", fontsize=6.4, framealpha=0.9)
        ax_s.plot(t_arr / 60.0, mean, "s-", color="#1f77b4", ms=3.0, lw=0.9, label="Px mean")
        ax_s.plot(t_arr / 60.0, resid, "^-", color="#666666", ms=3.0, lw=0.9, label="residual std")
        ax_s.set(xlabel="Elapsed time (min)", ylabel="mV", title="DC mean and residual")
        ax_s.grid(alpha=0.2)
        style_legend(ax_s, loc="upper right", fontsize=6.4, framealpha=0.9)
        picks = sorted(folds.items(), key=lambda kv: kv[1][1])
        chosen = [picks[0], picks[len(picks) // 2], picks[-1]] if len(picks) >= 3 else picks
        for label, (folded, t_s) in chosen:
            offsets = np.linspace(0.0, period, folded.size, endpoint=False)
            ax_f.plot(offsets * 1e6, folded * 1e3, lw=1.1, label=f"{label} @ {t_s/60:.0f} min")
        ax_f.set(xlabel=f"Time since CH{TRIGGER_CHANNEL} edge (us)", ylabel="Px folded mean (mV)",
                 title="Fold shape at start / middle / end")
        ax_f.grid(alpha=0.2)
        style_legend(ax_f, loc="upper right", fontsize=6.0, framealpha=0.9)
        fig.savefig(results / "px_drift.png", dpi=240)

    readme = [
        f"# {study_dir.name}: Px 脉冲响应漂移对照（固定偏置）",
        "",
        "- 脚本：`scripts/rf_phase_sensitivity/px_bias_map_scope.py --drift-control`",
        f"- 固定偏置：X = {drift.get('x_v'):+.4f} V，Y = {drift.get('y_v'):+.4f} V；脉冲开、泵浦 ON、RF OFF",
        f"- 点数 {len(t_arr)}，总时长 {t_arr[-1]/60:.1f} min",
        f"- 5 kHz 同步 p2p：首点 {sync_arr[0]:.1f} mV → 末点 {sync_arr[-1]:.1f} mV，"
        f"最小 {np.min(sync_arr):.1f} mV、最大 {np.max(sync_arr):.1f} mV",
        f"- 单指数拟合：τ = {fit['tau_min']:.1f} min，幅度 {fit['amplitude']:.1f} mV，"
        f"偏置 {fit['offset']:.1f} mV，R² = {fit['r2']:.3f}",
        *([f"- 监视通道 CH{summary['monitor_channel']} 同步 p2p：首点 {mon_arr[0]:.1f} mV → 末点 {mon_arr[-1]:.1f} mV，"
           f"τ = {mon_fit['tau_min']:.1f} min（R² = {mon_fit['r2']:.3f}）"] if mon_fit else []),
        "",
        "产物：`results/px_drift.png`、`results/px_drift_stats.csv`、`results/summary.json`",
    ]
    (study_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return summary



def _plot_bias_time_grid(results: Path, x_values: list[float], y_values: list[float],
                         cells: dict[tuple[int, int], dict[str, Any]], period_s: float) -> Path:
    """5x5 时域网格：每格是该偏置点的折叠波形（逐帧细线 + 平均粗线）。"""
    nx, ny = len(x_values), len(y_values)
    fig, axes = new_figure(figsize=(1.95 * nx, 1.45 * ny), nrows=ny, ncols=nx,
                           sharex=True, sharey=True, constrained_layout=True)
    axes = np.atleast_2d(axes)
    sample = next(iter(cells.values()), None)
    if sample is None:
        return results / "px_bias_time_grid.png"
    n = int(np.asarray(sample["metrics"]["folded"]).size)
    u_us = np.linspace(0.0, period_s, n, endpoint=False) * 1e6
    for (ix, iy), item in cells.items():
        ax = axes[ny - 1 - iy, ix]
        m = item["metrics"]
        folds = np.asarray(m.get("folds", []), dtype=float)
        if folds.size:
            for row in folds:
                ax.plot(u_us, row * 1e3, color="#9AA0A6", lw=0.35, alpha=0.55)
            ax.fill_between(u_us, np.percentile(folds, 10, axis=0) * 1e3,
                            np.percentile(folds, 90, axis=0) * 1e3, color="#4C78A8", alpha=0.16, lw=0)
        ax.plot(u_us, np.asarray(m["folded"]) * 1e3, color="#1F4E79", lw=1.2)
        ratio = item.get("ratio")
        label = f"p2p {m['sync_p2p_v']*1e3:.0f} mV"
        if m.get("plateau_diff_abs_v"):
            label += f"\nplateau {m['plateau_diff_abs_v']*1e3:.0f} mV"
        if ratio:
            label += f"\nratio {ratio:.2f}"
        ax.text(0.04, 0.06, label,
                transform=ax.transAxes, fontsize=5.2, va="bottom", ha="left",
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#CCCCCC", lw=0.3, alpha=0.85))
        ax.set_title(f"X={x_values[ix]*1e3:+.0f}, Y={y_values[iy]*1e3:+.0f} mV", fontsize=6.2, pad=2)
        ax.tick_params(labelsize=5, length=2)
        ax.grid(alpha=0.18, lw=0.3)
        if ix == 0:
            ax.set_ylabel("Px (mV)", fontsize=6)
        if iy == 0:
            ax.set_xlabel("t since CH3 edge (us)", fontsize=6)
    fig.suptitle("Folded Px waveform per X/Y bias point (thin: frames, band: 10-90%, bold: mean)",
                 fontsize=8.4)
    path = results / "px_bias_time_grid.png"
    fig.savefig(path, dpi=220)
    return path


def analyze_interleave(study_dir: Path, config: dict[str, Any], scope_cfg: dict[str, Any]) -> dict[str, Any]:
    """交错参考点：用参考点拟合连续打脉冲的时间衰减，再把每个偏置点除以同一时刻的参考值。"""
    inter = config.get("interleave") or {}
    period = float(scope_cfg.get("pulse_period_s") or 200e-6)
    grid = config.get("grid") or {}
    x_values = [float(v) for v in grid.get("x_values") or []]
    y_values = [float(v) for v in grid.get("y_values") or []]
    records = [p for p in (config.get("points") or []) if p.get("done")]
    refs, bias = [], []
    for rec in records:
        metrics = _point_metrics(study_dir, rec["label"], period)
        if metrics is None:
            continue
        item = dict(rec=rec, metrics=metrics, t_s=float(rec.get("elapsed_s", 0.0)))
        (refs if str(rec.get("kind") or "") == "ref" or str(rec["label"]).startswith("ref") else bias).append(item)
    if len(refs) < 3:
        raise ValueError(f"{study_dir} 的参考点不足 3 个，无法拟合时间背景")
    if not bias:
        raise ValueError(f"{study_dir} 没有偏置点")

    t_ref = np.asarray([r["t_s"] for r in refs])
    def _metric(item: dict[str, Any]) -> float:
        m = item["metrics"]
        value = m.get("plateau_diff_abs_v")
        return float((value if value is not None else m["sync_p2p_v"]) * 1e3)

    y_ref = np.asarray([_metric(r) for r in refs])
    order = np.argsort(t_ref)
    fit = _fit_decay(t_ref[order], y_ref[order])

    def model(t_s: np.ndarray | float) -> np.ndarray:
        return fit["offset"] + fit["amplitude"] * np.exp(-(np.asarray(t_s, dtype=float)) / (fit["tau_min"] * 60.0))

    nx, ny = len(x_values), len(y_values)
    raw = np.full((ny, nx), np.nan)
    ratio = np.full((ny, nx), np.nan)
    resid = np.full((ny, nx), np.nan)
    detail = []
    for item in bias:
        rec = item["rec"]
        ix = int(np.argmin(np.abs(np.asarray(x_values) - float(rec["x_v"]))))
        iy = int(np.argmin(np.abs(np.asarray(y_values) - float(rec["y_v"]))))
        metric_mv = _metric(item)
        p2p_mv = item["metrics"]["sync_p2p_v"] * 1e3
        ref_now = float(model(item["t_s"]))
        raw[iy, ix] = metric_mv
        ratio[iy, ix] = metric_mv / ref_now if ref_now > 0 else np.nan
        resid[iy, ix] = item["metrics"]["residual_std_v"] * 1e3
        detail.append(dict(x_v=float(rec["x_v"]), y_v=float(rec["y_v"]), t_min=item["t_s"] / 60.0,
                           plateau_diff_mv=metric_mv, p2p_mv=p2p_mv, ref_mv=ref_now,
                           ratio=float(ratio[iy, ix]), residual_mv=float(resid[iy, ix]),
                           metrics=item["metrics"]))

    finite = np.isfinite(ratio)
    if not finite.any():
        raise ValueError(f"{study_dir} 没有可用的归一化比值")
    iy_min, ix_min = np.unravel_index(np.nanargmin(np.where(finite, ratio, np.inf)), ratio.shape)
    iy_max, ix_max = np.unravel_index(np.nanargmax(np.where(finite, ratio, -np.inf)), ratio.shape)
    results = study_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    summary = dict(
        study_dir=str(study_dir), mode="interleave", ref_every=int(inter.get("ref_every") or 2),
        ref_points=len(refs), bias_points=len(bias),
        duration_min=float(max(r["t_s"] for r in refs + bias) / 60.0),
        decay_tau_min=fit["tau_min"], decay_amplitude_mv=fit["amplitude"],
        decay_offset_mv=fit["offset"], decay_r2=fit["r2"],
        ratio_min=float(ratio[iy_min, ix_min]), ratio_min_x_v=float(x_values[ix_min]),
        ratio_min_y_v=float(y_values[iy_min]),
        ratio_max=float(ratio[iy_max, ix_max]), ratio_max_x_v=float(x_values[ix_max]),
        ratio_max_y_v=float(y_values[iy_max]), metric="plateau_diff_abs_v",
        x_values=x_values, y_values=y_values,
    )
    (results / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (results / "px_interleave_stats.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["x_v", "y_v", "elapsed_min", "plateau_diff_mv", "sync_p2p_mv",
                         "ref_model_mv", "ratio", "residual_std_mv"])
        for row in detail:
            writer.writerow([f"{row['x_v']:+.4f}", f"{row['y_v']:+.4f}", f"{row['t_min']:.3f}",
                             f"{row['plateau_diff_mv']:.5e}", f"{row['p2p_mv']:.5e}",
                             f"{row['ref_mv']:.5e}", f"{row['ratio']:.5e}",
                             f"{row['residual_mv']:.5e}"])

    with plot_style_context("paper"):
        fig, (ax_t, ax_m) = new_figure(figsize=(9.2, 3.2), nrows=1, ncols=2, constrained_layout=True)
        ax_t.plot(t_ref[order] / 60.0, y_ref[order], "o", color="#d55e00", ms=3.4, label="reference (0,0)")
        grid_t = np.linspace(0.0, float(max(r["t_s"] for r in refs + bias)), 200)
        ax_t.plot(grid_t / 60.0, model(grid_t), "-", color="#d55e00", lw=1.2,
                  label=f"decay fit τ = {fit['tau_min']:.1f} min (R²={fit['r2']:.3f})")
        ax_t.plot([i["t_s"] / 60.0 for i in bias], [_metric(i) for i in bias],
                  "o", color="#2a6f97", ms=2.6, label="bias points")
        ax_t.set(xlabel="Elapsed time (min)", ylabel="Plateau difference (mV)",
                 title="Time background from interleaved references")
        ax_t.grid(alpha=0.2)
        style_legend(ax_t, loc="upper right", fontsize=6.2, framealpha=0.9)

        extent = [x_values[0] * 1e3, x_values[-1] * 1e3, y_values[0] * 1e3, y_values[-1] * 1e3]
        im = ax_m.imshow(ratio, origin="lower", aspect="auto", extent=extent, cmap="viridis")
        cs = ax_m.contour(np.asarray(x_values) * 1e3, np.asarray(y_values) * 1e3, ratio, levels=6,
                          colors="#ffffff", linewidths=0.4, alpha=0.75)
        ax_m.clabel(cs, fontsize=5, fmt="%.2f")
        ax_m.plot(x_values[ix_min] * 1e3, y_values[iy_min] * 1e3, marker="o", ms=8, mfc="none",
                  mec="#ffffff", mew=1.4)
        ax_m.set(xlabel="X bias (mV)", ylabel="Y bias (mV)",
                 title=f"Normalized pulse response (min {ratio[iy_min, ix_min]:.2f} at "
                       f"X={x_values[ix_min]*1e3:+.0f}, Y={y_values[iy_min]*1e3:+.0f} mV)")
        fig.colorbar(im, ax=ax_m, label="plateau diff / reference", fraction=0.046, pad=0.03)
        fig.suptitle(f"{study_dir.name}: Px plateau difference vs X/Y bias, decay-corrected", fontsize=8.6)
        fig.savefig(results / "px_interleave.png", dpi=240)

        cells = {}
        for row in detail:
            ix = int(np.argmin(np.abs(np.asarray(x_values) - row["x_v"])))
            iy = int(np.argmin(np.abs(np.asarray(y_values) - row["y_v"])))
            cells[(ix, iy)] = row
        _plot_bias_time_grid(results, x_values, y_values, cells, period)

    readme = [
        f"# {study_dir.name}: Px 脉冲响应对 X/Y 偏置的依赖（交错参考点、扣除时间衰减）",
        "",
        "- 脚本：`scripts/rf_phase_sensitivity/px_bias_map_scope.py --interleave`",
        f"- 网格：X {x_values[0]*1e3:+.0f} … {x_values[-1]*1e3:+.0f} mV、Y {y_values[0]*1e3:+.0f} … "
        f"{y_values[-1]*1e3:+.0f} mV，每 {summary['ref_every']} 点插一次参考点 (0,0)",
        f"- 参考点 {len(refs)} 个、偏置点 {len(bias)} 个，总时长 {summary['duration_min']:.1f} min",
        f"- 时间背景拟合：τ = {fit['tau_min']:.1f} min（幅度 {fit['amplitude']:.1f} mV、平台 "
        f"{fit['offset']:.1f} mV、R² = {fit['r2']:.3f}）",
        f"- 归一化后的脉冲响应：最小 {summary['ratio_min']:.2f}（X={summary['ratio_min_x_v']*1e3:+.0f}、"
        f"Y={summary['ratio_min_y_v']*1e3:+.0f} mV），最大 {summary['ratio_max']:.2f}"
        f"（X={summary['ratio_max_x_v']*1e3:+.0f}、Y={summary['ratio_max_y_v']*1e3:+.0f} mV）",
        "",
        "产物：`results/px_interleave.png`、`results/px_interleave_stats.csv`、`results/summary.json`",
    ]
    (study_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return summary


def analyze_study(study_dir: Path) -> dict[str, Any]:
    study_dir = Path(study_dir).resolve()
    config = _read_config(study_dir)
    scope_cfg = config.get("scope") or {}
    if (config.get("drift_control") or {}).get("enabled"):
        return analyze_drift(study_dir, config, scope_cfg)
    if (config.get("interleave") or {}).get("enabled"):
        return analyze_interleave(study_dir, config, scope_cfg)
    grid = config.get("grid") or {}
    x_values = [float(v) for v in grid.get("x_values") or []]
    y_values = [float(v) for v in grid.get("y_values") or []]
    period = float(scope_cfg.get("pulse_period_s") or 200e-6)
    if not x_values or not y_values:
        raise ValueError(f"{study_dir} 配置缺少 X/Y 网格")
    points: dict[tuple[int, int], dict[str, Any]] = {}
    for ix in range(len(x_values)):
        for iy in range(len(y_values)):
            label = f"x{ix:02d}_y{iy:02d}"
            metrics = _point_metrics(study_dir, label, period)
            if metrics is not None:
                points[(ix, iy)] = metrics
    if not points:
        raise ValueError(f"{study_dir} 下没有可用的偏置点数据")

    nx, ny = len(x_values), len(y_values)
    sync = np.full((ny, nx), np.nan)
    mean = np.full((ny, nx), np.nan)
    resid = np.full((ny, nx), np.nan)
    for (ix, iy), item in points.items():
        sync[iy, ix] = item["sync_p2p_v"] * 1e3
        mean[iy, ix] = item["mean_v"] * 1e3
        resid[iy, ix] = item["residual_std_v"] * 1e3

    results = study_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    summary = dict(
        study_dir=str(study_dir), period_s=period, x_values=x_values, y_values=y_values,
        sync_p2p_min_v=float(np.nanmin(sync)) * 1e-3, sync_p2p_max_v=float(np.nanmax(sync)) * 1e-3,
        residual_std_min_v=float(np.nanmin(resid)) * 1e-3, residual_std_max_v=float(np.nanmax(resid)) * 1e-3,
        points=len(points),
    )
    (results / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = results / "px_bias_stats.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["x_v", "y_v", "frames", "mean_mv", "std_mv", "sync_p2p_mv", "residual_std_mv"])
        for iy, y_v in enumerate(y_values):
            for ix, x_v in enumerate(x_values):
                item = points.get((ix, iy))
                if item is None:
                    continue
                writer.writerow([f"{x_v:+.4f}", f"{y_v:+.4f}", item["frames"],
                                 f"{item['mean_v']*1e3:.5e}", f"{item['std_v']*1e3:.5e}",
                                 f"{item['sync_p2p_v']*1e3:.5e}", f"{item['residual_std_v']*1e3:.5e}"])

    with plot_style_context("paper"):
        fig, axes = new_figure(figsize=(9.0, 3.0), nrows=1, ncols=3, constrained_layout=True)
        extent = [float(x_values[0]) * 1e3, float(x_values[-1]) * 1e3, float(y_values[0]) * 1e3, float(y_values[-1]) * 1e3]
        for ax, data, title, unit in ((axes[0], sync, "5 kHz-synchronous p2p", "mV"),
                                      (axes[1], mean, "Px mean", "mV"),
                                      (axes[2], resid, "Residual std (sync removed)", "mV")):
            im = ax.imshow(data, origin="lower", aspect="auto", extent=extent, cmap="viridis")
            cs = ax.contour(np.asarray(x_values) * 1e3, np.asarray(y_values) * 1e3, data, levels=6,
                            colors="#ffffff", linewidths=0.4, alpha=0.7)
            ax.clabel(cs, fontsize=5, fmt="%.1f")
            ax.set(xlabel="X bias (mV)", ylabel="Y bias (mV)", title=title)
            fig.colorbar(im, ax=ax, label=unit, fraction=0.046, pad=0.03)
        fig.suptitle(f"{study_dir.name}: Px vs X/Y bias, Z pulse ON, RF OFF, "
                     f"{scope_cfg.get('window_s', 0) * 1e3:.0f} ms window", fontsize=8.6)
        fig.savefig(results / "px_bias_map.png", dpi=240)

        # 沿 X=0 与 Y=0 的折叠波形切片
        ix0 = int(np.argmin(np.abs(np.asarray(x_values))))
        iy0 = int(np.argmin(np.abs(np.asarray(y_values))))
        fig, (ax_x, ax_y) = new_figure(figsize=(8.4, 3.4), nrows=1, ncols=2, constrained_layout=True)
        from matplotlib import colormaps as _colormaps
        colours = _colormaps["viridis"](np.linspace(0.1, 0.9, max(nx, ny)))
        for ix, x_v in enumerate(x_values):
            item = points.get((ix, iy0))
            if item is None:
                continue
            offsets = np.linspace(0.0, period, item["folded"].size, endpoint=False)
            ax_x.plot(offsets * 1e6, item["folded"] * 1e3, color=colours[ix], lw=0.9,
                      label=f"X={x_v*1e3:+.0f} mV")
        for iy, y_v in enumerate(y_values):
            item = points.get((ix0, iy))
            if item is None:
                continue
            offsets = np.linspace(0.0, period, item["folded"].size, endpoint=False)
            ax_y.plot(offsets * 1e6, item["folded"] * 1e3, color=colours[iy], lw=0.9,
                      label=f"Y={y_v*1e3:+.0f} mV")
        for ax, title in ((ax_x, f"Fold along X (Y={y_values[iy0]*1e3:+.0f} mV)"),
                          (ax_y, f"Fold along Y (X={x_values[ix0]*1e3:+.0f} mV)")):
            ax.set(xlabel=f"Time since CH{TRIGGER_CHANNEL} edge (us)", ylabel="Px folded mean (mV)", title=title)
            ax.grid(alpha=0.2)
            style_legend(ax, loc="upper right", fontsize=5.2, ncol=2, framealpha=0.85)
        fig.suptitle(f"{study_dir.name}: pulse-synchronous Px shape vs bias", fontsize=8.6)
        fig.savefig(results / "px_bias_folded_slices.png", dpi=240)

        # 四角与中心的折叠波形对比
        picks = [(0, 0), (0, ny - 1), (nx - 1, 0), (nx - 1, ny - 1), (ix0, iy0)]
        fig, ax = new_figure(figsize=(7.0, 3.0), constrained_layout=True)
        for ix, iy in picks:
            item = points.get((ix, iy))
            if item is None:
                continue
            offsets = np.linspace(0.0, period, item["folded"].size, endpoint=False)
            ax.plot(offsets * 1e6, item["folded"] * 1e3, lw=1.2,
                    label=f"X={x_values[ix]*1e3:+.0f}, Y={y_values[iy]*1e3:+.0f} mV")
        ax.set(xlabel=f"Time since CH{TRIGGER_CHANNEL} edge (us)", ylabel="Px folded mean (mV)",
               title="Corner/centre pulse-synchronous shapes")
        ax.grid(alpha=0.2)
        style_legend(ax, loc="upper right", fontsize=6.2, framealpha=0.9)
        fig.savefig(results / "px_bias_shapes.png", dpi=240)

    readme = [
        f"# {study_dir.name}: Px 随 X/Y 静态偏置的变化（脉冲开、RF 关）",
        "",
        "- 脚本：`scripts/rf_phase_sensitivity/px_bias_map_scope.py`",
        f"- 网格：X {x_values[0]*1e3:+.0f} … {x_values[-1]*1e3:+.0f} mV × Y {y_values[0]*1e3:+.0f} … "
        f"{y_values[-1]*1e3:+.0f} mV，{nx}×{ny} = {len(points)} 点，步长 "
        f"{(x_values[1]-x_values[0])*1e3:g} / {(y_values[1]-y_values[0])*1e3:g} mV",
        f"- 采集：{float(scope_cfg.get('window_s') or 0)*1e3:g} ms 窗、"
        f"{float(scope_cfg.get('rate_sa_s') or 0)/1e6:g} MS/s、每点 {scope_cfg.get('frames')} 帧；",
        f"- 状态：脉冲控制场 ON、泵浦 ON、RF OFF；GS200 保持脉冲工作点",
        f"- 5 kHz 同步 p2p 范围：{summary['sync_p2p_min_v']*1e3:.1f} … {summary['sync_p2p_max_v']*1e3:.1f} mV",
        f"- 去同步残差 std 范围：{summary['residual_std_min_v']*1e3:.1f} … {summary['residual_std_max_v']*1e3:.1f} mV",
        "",
        "产物：",
        "  - 二维图：`results/px_bias_map.png`",
        "  - 折叠切片：`results/px_bias_folded_slices.png`",
        "  - 形状对比：`results/px_bias_shapes.png`",
        "  - 统计表：`results/px_bias_stats.csv`",
        "  - 摘要：`results/summary.json`",
    ]
    (study_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return summary


# ---------------------------------------------------------------- 命令行


def _print_summary(summary: dict[str, Any]) -> None:
    mode = summary.get("mode")
    if mode == "drift_control":
        print(f" 漂移对照：首点 {summary['sync_p2p_first_mv']:.1f} mV → 末点 "
              f"{summary['sync_p2p_last_mv']:.1f} mV，τ = {summary['decay_tau_min']:.1f} min"
              f"（R²={summary['decay_r2']:.3f}）")
    elif mode == "interleave":
        print(f" 时间背景：τ = {summary['decay_tau_min']:.1f} min（R²={summary['decay_r2']:.3f}），"
              f"参考点 {summary['ref_points']} 个")
        print(f" 归一化响应最小 {summary['ratio_min']:.2f} 于 X={summary['ratio_min_x_v'] * 1e3:+.0f} mV、"
              f"Y={summary['ratio_min_y_v'] * 1e3:+.0f} mV；最大 {summary['ratio_max']:.2f} 于 "
              f"X={summary['ratio_max_x_v'] * 1e3:+.0f} mV、Y={summary['ratio_max_y_v'] * 1e3:+.0f} mV")
    else:
        print(f" 5 kHz 同步 p2p 范围：{summary['sync_p2p_min_v']:.3e} … {summary['sync_p2p_max_v']:.3e} V")
        print(f" 去同步残差 std 范围：{summary['residual_std_min_v']:.3e} … {summary['residual_std_max_v']:.3e} V")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="脉冲控制场开启时扫描 X/Y 静态偏置，测量 Px（CH1）时域波形与 5 kHz 同步方波随偏置的变化"
    )
    parser.add_argument("--analyze", type=Path, default=None, help="对已有 _studies 目录离线重绘，不连接硬件")
    parser.add_argument("--tag", default="px_bias_map", help="研究目录名后缀（默认 px_bias_map）")
    parser.add_argument("--temp-per-frame", action="store_true", default=True,
                        help="冻结读帧模式下令温控只在冻结瞬间关闭（默认）；--no-temp-per-frame 关闭")
    parser.add_argument("--no-temp-per-frame", dest="temp_per_frame", action="store_false")
    parser.add_argument("--use-scope-config", action="store_true",
                        help="不改示波器时基/深度，按面板设置冻结读帧（采样率由仪器自行推导）")
    parser.add_argument("--scope-device", default=SCOPE_DEVICE_ID, help=f"示波器设备库 ID（默认 {SCOPE_DEVICE_ID}）")
    parser.add_argument("--x-range", type=float, nargs=2, default=list(DEFAULT_X_RANGE), metavar=("LO", "HI"),
                        help=f"X 偏置范围（V，默认 {DEFAULT_X_RANGE}）")
    parser.add_argument("--x-step", type=float, default=DEFAULT_X_STEP, help="X 偏置步长（V）")
    parser.add_argument("--y-range", type=float, nargs=2, default=list(DEFAULT_Y_RANGE), metavar=("LO", "HI"),
                        help=f"Y 偏置范围（V，默认 {DEFAULT_Y_RANGE}）")
    parser.add_argument("--y-step", type=float, default=DEFAULT_Y_STEP, help="Y 偏置步长（V）")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_S, help="单帧采集时长（s，默认 10 ms）")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_SA_S, help="目标采样率（Sa/s，默认 1 MS/s）")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="每点帧数（默认 3）")
    parser.add_argument("--trigger-level", type=float, default=DEFAULT_TRIGGER_LEVEL_V,
                        help=f"CH3 触发电平（V，默认 {DEFAULT_TRIGGER_LEVEL_V}）")
    parser.add_argument("--drift-control", action="store_true", help="漂移对照模式：固定偏置重复采点")
    parser.add_argument("--drift-points", type=int, default=25, help="漂移对照点数（默认 25）")
    parser.add_argument("--drift-x", type=float, default=0.0, help="漂移对照的 X 偏置（V，默认 0）")
    parser.add_argument("--drift-y", type=float, default=0.0, help="漂移对照的 Y 偏置（V，默认 0）")
    parser.add_argument("--interleave", action="store_true",
                        help="交错参考点模式：每 --ref-every 个偏置点插一次 (0,0) 参考点，用于扣除时间衰减")
    parser.add_argument("--ref-every", type=int, default=2, help="交错模式的参考点间隔（默认每 2 点一次）")
    parser.add_argument("--settle", type=float, default=0.3, help="切偏置后进温控窗前的稳定等待（s）")
    parser.add_argument("--temp-off-settle", type=float, default=0.3, help="关温控后到第一帧之间的等待（s）")
    parser.add_argument("--temp-lag", type=float, default=1.0,
                        help="每帧恢复温控的固定时长（s，默认 1.0）；关闭温控的实际时长记录在 temp_off_windows_s")
    parser.add_argument("--monitor-channel", type=int, default=0,
                        help="额外监视通道（如 2 = 线圈采样电阻），0 表示不启用")
    parser.add_argument("--monitor-scale", type=float, default=0.5, help="监视通道垂直档位（V/div，默认 0.5）")
    parser.add_argument("--monitor-offset", type=float, default=0.0, help="监视通道偏移（V，默认 0）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.analyze is not None:
        summary = analyze_study(args.analyze)
        print(f"离线出图完成：{Path(args.analyze).resolve() / 'results'}")
        _print_summary(summary)
        return 0
    return run_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
