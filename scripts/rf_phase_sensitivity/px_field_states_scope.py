"""RF 关断条件下，用 DHO924 直接读平衡探测器 Px（CH1）在五种 Z 场 / 泵浦状态下的时域波形并对比。

回答的问题：脉冲控制场、常数 Z 场与泵浦光分别对 Px 时域信号有多大影响。

流程概要：
1. 读取并校验 ``rf-phase-sensitivity`` 运行参数（``--window`` / ``--rate`` / ``--frames`` 可覆盖），
   沿用其准备步骤：时钟同步、温控、1200B 增益与阻抗、GS200 脉冲工作点、共同触发（5 kHz 方波）
   与 Z 通道 5 kHz 突发脉冲（8.6 µs 宽、含拖尾补偿）；随后立即关闭 MFLI Y 射频输出并回读，
   本次测量全程 RF OFF。
2. 五种状态（基线为 ``z_pulse_off``，除两个常数场态外 GS200 保持脉冲工作点电流）：
   - ``z_pulse_on``: 脉冲控制场开（Z 信号源 + 1200B ON），Pump ON
   - ``z_pulse_off``: 脉冲控制场关（Z 信号源与 1200B OFF），Pump ON
   - ``pump_off``: 关闭泵浦光（``192.168.124.32`` CH1 OFF），Z 控制 OFF
   - ``const_0p3893``: GS200 常数场 0.3893 mA，Z 控制 OFF，Pump ON
   - ``const_8p08``: GS200 常数场 8.08 mA，Z 控制 OFF，Pump ON
3. 每帧沿用温控开窗：只在冻结瞬间关 ``Temp_Switch``（5 V→0 V），冻结后立即恢复、再读回，
   关窗时长必须小于恢复时长；每帧 ``--window`` 秒
   （默认 10 ms，1 ms/div）；DHO900 没有采样率写命令，存储深度取 AUTO、上限为「窗口 × ``--rate``」
   （默认 10 MS/s，约 10 万点），实际采样率与点数逐帧回读记录；CH1 = Px，CH3 = 控制场
   触发电平按 5 Vpp / 2.5 V 偏置取 2.5 V，触发方式统一 CH3 上升沿 + AUTO；每态拍完恢复温控并等待。
4. ``finally`` 中先恢复 ``王湾pump`` CH1，再停示波器、恢复示波器原设置并断开，随后执行
   ``preparation.park(ctx)`` 安全关断（1200B OFF、Z 归零、MFLI RF OFF、X 偏置归零、温控恢复、
   GS200 回运行前状态）；任一收尾项失败只记录不跳过后续项。
5. 出图（全英文）：``px_states_time.png`` 五态 Px 时域（逐帧 + 均值 + 触发边沿标记），
   ``px_states_folded.png`` 按触发边沿折叠到脉冲周期的 Px 波形与均值/起伏统计柱状图；
   连同 ``px_states_stats.csv``、``summary.json`` 与 README 一起写入
   ``data/rf_phase_sensitivity/_studies/<MMDD_HHMMSS>_px_states/``。``--analyze <dir>`` 纯离线重绘。
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
from lab_workflows.experiment_modules.z_chain_common import prep as z_prep
from lab_workflows.experiment_modules.z_chain_common.keys import MAIN_FIELD_KEY
from lab_workflows.instrument_config import load_device_definitions
from lab_workflows.plotting import (
    COLOR_BROWN,
    COLOR_GRAY,
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_ORANGE,
    COLOR_PURPLE,
    new_figure,
    plot_style_context,
    style_legend,
)
from lab_workflows.steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from lab_workflows.steps.coil_drive_chain import SafetyAbort, sleep
from lab_workflows.steps.run_directory import write_run_config


PUMP_RESOURCE = "TCPIP0::192.168.124.32::5025::SOCKET"
PUMP_DEVICE_ID = "dg9q275000733"
PUMP_CHANNEL = 1
SCOPE_DEVICE_ID = "rigol_dho900_192_168_124_21"
PX_CHANNEL = 1
TRIGGER_CHANNEL = 3
WEAK_CONST_MA = 0.3893
STRONG_CONST_MA = 8.08
DEFAULT_WINDOW_S = 10e-3
DEFAULT_RATE_SA_S = 10e6
DEFAULT_FRAMES = 5
DEFAULT_TRIGGER_LEVEL_V = 2.5
MAX_MEMORY_POINTS = 5_000_000


@dataclass(frozen=True, slots=True)
class StateSpec:
    state_id: str
    label_zh: str
    label_en: str
    pump_on: bool
    z_on: bool
    const_field_ma: float | None
    color: str
    linestyle: str


STATES: tuple[StateSpec, ...] = (
    StateSpec("z_pulse_on", "脉冲控制场开", "Z pulse ON", True, True, None, COLOR_GREEN, "-"),
    StateSpec("z_pulse_off", "脉冲控制场关（基线）", "Z pulse OFF (baseline)", True, False, None, COLOR_GRAY, "-"),
    StateSpec("pump_off", "关闭泵浦光", "Pump OFF", False, False, None, COLOR_BROWN, "--"),
    StateSpec("const_0p3893", f"常数场 {WEAK_CONST_MA:g} mA", f"Const field {WEAK_CONST_MA:g} mA",
              True, False, WEAK_CONST_MA, COLOR_ORANGE, "-"),
    StateSpec("const_8p08", f"常数场 {STRONG_CONST_MA:g} mA", f"Const field {STRONG_CONST_MA:g} mA",
              True, False, STRONG_CONST_MA, COLOR_PURPLE, "-"),
)
BASELINE_ID = "z_pulse_off"


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
        self.state["study_type"] = "rf_phase_px_field_states_scope"
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
    params = RfPhaseSensitivityParams.from_external(merged, schema_version=schema_ver)
    errors = params.validate(root)
    if errors:
        raise ValueError("参数校验失败：" + "；".join(errors))
    return params


# ---------------------------------------------------------------- 泵浦与主磁场


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


# ---------------------------------------------------------------- 状态切换


def _disable_rf(ctx: StudyContext) -> dict[str, Any]:
    """本实验全程 RF OFF：只关 MFLI Signal Output 1，保留载波与相位设置，逐项回读。"""
    lockin = ctx.devices["hf2"]
    dev = lockin.device_id
    if int(lockin.get_int(f"/{dev}/sigouts/0/on")) != 0:
        lockin.set_int(f"/{dev}/sigouts/0/on", 0)
        lockin.sync()
    actual = int(lockin.get_int(f"/{dev}/sigouts/0/on"))
    if actual != 0:
        raise SafetyAbort(f"MFLI Out1 未回读为 OFF：{actual}")
    return dict(output_on=False)


def _apply_state(ctx: StudyContext, pump_dev: Any, spec: StateSpec, *, settle_s: float) -> dict[str, Any]:
    z_dev, z_ch = preparation._route(ctx, "z")
    z_dg_on = preparation.dg_output(z_dev, z_ch)
    amp_on = bool(ctx.state.get("amp_output_on"))
    # 先关脉冲链再改 GS200，避免切场时仍有脉冲叠加。
    if not spec.z_on:
        if amp_on:
            z_prep.set_amp_output(ctx, False)
            amp_on = False
        if z_dg_on:
            z_dev.set_output(False, channel=z_ch)
            z_dev.wait_for_operation_complete()
            if preparation.dg_output(z_dev, z_ch):
                raise SafetyAbort("Z 信号源输出未回读为 OFF")

    target_ma = float(spec.const_field_ma) if spec.const_field_ma is not None else float(ctx.params.main_field_current_ma)
    gs200_readback = _set_main_field_ma(ctx, target_ma)

    if spec.z_on:
        if not z_dg_on:
            z_dev.set_output(True, channel=z_ch)
            z_dev.wait_for_operation_complete()
            if not preparation.dg_output(z_dev, z_ch):
                raise SafetyAbort("Z 信号源输出未回读为 ON，拒绝开启 1200B")
        if not amp_on:
            z_prep.set_amp_output(ctx, True)

    pump_now = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
    if pump_now != spec.pump_on:
        _set_pump_output(pump_dev, spec.pump_on)

    if settle_s > 0:
        sleep(settle_s)

    z_verified = bool(preparation.dg_output(z_dev, z_ch)) and bool(ctx.state.get("amp_output_on"))
    pump_verified = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
    gs200_verified = _set_main_field_ma(ctx, target_ma)
    if z_verified is not spec.z_on:
        raise SafetyAbort(f"{spec.state_id} 采集前 Z 控制状态回读不符：expected={spec.z_on}, actual={z_verified}")
    if pump_verified is not spec.pump_on:
        raise SafetyAbort(f"{spec.state_id} 采集前 Pump 状态回读不符：expected={spec.pump_on}, actual={pump_verified}")
    return dict(
        z_dg_on=preparation.dg_output(z_dev, z_ch),
        amp_output_on=bool(ctx.state.get("amp_output_on")),
        pump_output_on=pump_verified,
        gs200_output_on=bool(gs200_verified["output_on"]),
        gs200_current_ma=float(gs200_verified["current_ma"]),
        gs200_target_ma=float(gs200_readback["current_ma"]),
    )


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
        except Exception as exc:  # 只读快照，读不到就记下来，不改变仪器
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


def _prepare_scope_channels(scope: Any, before: dict[str, Any]) -> dict[str, Any]:
    """启用 CH1/CH3，并用仪器当前档位；关闭时按 Px 常见幅度给保守默认值。"""
    chosen = {}
    for ch in (PX_CHANNEL, TRIGGER_CHANNEL):
        info = before["channels"].get(ch) or {}
        if not info.get("enabled"):
            default_scale = 0.2 if ch == PX_CHANNEL else 1.0
            scope.set_channel_state(ch, True)
            scope.set_channel_scale(ch, default_scale)
            scope.set_channel_offset(ch, 0.0)
            scope.set_channel_coupling(ch, "DC")
            info = dict(enabled=True, scale=default_scale, offset=0.0, coupling="DC",
                        probe=1.0, impedance="ONEMeg", defaulted=True)
        chosen[ch] = info
    return chosen


def _acquisition_config(window_s: float, rate: float, channels: dict[int, dict[str, Any]],
                        trigger_level: float) -> AcquisitionConfig:
    return AcquisitionConfig(
        sampling_rate=rate,
        sampling_time=window_s,
        channels=[
            ChannelConfig(number=ch,
                          enabled=ch in {PX_CHANNEL, TRIGGER_CHANNEL},
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


def _capture_state(ctx: StudyContext, acq: Any, config: AcquisitionConfig | None, pump_dev: Any, spec: StateSpec,
                   args: argparse.Namespace, state_record: dict[str, Any]) -> None:
    readbacks = _apply_state(ctx, pump_dev, spec, settle_s=float(args.settle))
    state_record["readbacks"] = readbacks
    ctx.save()
    frozen = config is None
    for frame in range(int(args.frames)):
        preparation.write_temperature(ctx, False)
        sleep(float(args.temp_off_settle))
        t_off = time.monotonic()
        try:
            if frozen:
                _freeze_frame(acq._inst)  # noqa: SLF001
            else:
                acq.start_acquisition(config)
                acq.stop_acquisition()
        finally:
            preparation.write_temperature(ctx, True)
        off_window = time.monotonic() - t_off
        state_record.setdefault("temp_off_windows_s", []).append(off_window)
        tdiv = float(args.trigger_tdiv) if frozen else config.timebase_scale
        px = acq.acquire_channel(PX_CHANNEL, tdiv)
        trig = acq.acquire_channel(TRIGGER_CHANNEL, tdiv)
        if frame == 0:
            print(f"  [{spec.state_id}] 实际采样 {1.0 / float(px.preamble_dict['xincrement']) / 1e6:g} MSa/s，"
                  f"{int(px.preamble_dict['points'])} 点/帧", flush=True)
        path = ctx.directory.raw / f"{spec.state_id}_frame{frame:02d}.npz"
        np.savez_compressed(
            path,
            t_px=px.time,
            v_px=px.voltage,
            t_trigger=trig.time,
            v_trigger=trig.voltage,
            state_id=spec.state_id,
            frame=frame,
            sampling_rate=1.0 / float(px.preamble_dict["xincrement"]),
            point_num=int(px.preamble_dict["points"]),
            px_scale=float(state_record["scope"]["channels"][PX_CHANNEL]["scale"]),
            trigger_level=(float(config.trigger.level) if config is not None else float(args.trigger_level)),
        )
        state_record["frames"].append(dict(path=path.relative_to(ctx.directory.root).as_posix(),
                                           point_num=int(px.preamble_dict["points"])))
        ctx.save()
        print(f"  [{spec.state_id}] 帧 {frame + 1}/{int(args.frames)} 已保存（{path.name}）", flush=True)
        recover = max(off_window * 1.2, float(args.temp_lag))
        sleep(recover)
        state_record.setdefault("temp_recover_windows_s", []).append(recover)


def run_capture(args: argparse.Namespace) -> int:
    require_hardware_access("Px 五态时域对照测量")
    root = runtime_root()
    params = _load_params(root, args)
    for ma in (WEAK_CONST_MA, STRONG_CONST_MA):
        validate_safety_limit(MAIN_FIELD_KEY, ma, load_safety_limits(root))
    study_dir = _create_study_directory(root, args.tag)
    ctx = StudyContext(params, root, study_dir)
    ctx.save()
    state_records: dict[str, dict[str, Any]] = {}
    scope_device_id = args.scope_device

    pump_dev: Any = None
    scope: Any = None
    scope_before: dict[str, Any] | None = None
    pump_initial_on: bool | None = None
    capture_failed: BaseException | None = None
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
                f"王湾pump ({PUMP_RESOURCE}) CH{PUMP_CHANNEL} 当前为 OFF；请先开启泵浦光再启动 Px 对照测量"
            )

        # 示波器先连上并配置好，连不上就在改任何输出之前退出。
        scope_cfg = _scope_definition(ctx, scope_device_id)
        scope = create_oscilloscope(scope_cfg)
        scope.connect()
        scope_before = _scope_snapshot(scope)
        channels = _prepare_scope_channels(scope, scope_before)
        acq = create_scope_acquisition(scope, maximum_points=MAX_MEMORY_POINTS)
        if args.use_scope_config:
            # 尊重面板：时基与存储深度由操作者设定，采样率由仪器自行推导；不改设置，冻结读帧。
            args.trigger_tdiv = float(scope.get_timebase_scale())
            depth_text = scope.get_memory_depth().strip()
            if depth_text.upper() == "AUTO":
                raise ValueError("示波器存储深度仍为 AUTO；请先在面板设定固定深度（如 10k）后重跑")
            print(f"[阶段] 使用示波器现有配置：{args.trigger_tdiv*1e6:g} us/div、深度 {depth_text}、"
                  f"采样率 {float(scope.get_sampling_rate())/1e6:g} MSa/s（不改设置）", flush=True)
            config = None
        else:
            config = _acquisition_config(float(args.window), float(args.rate), channels, float(args.trigger_level))
            # DHO900 没有采样率写命令：深度取 AUTO，把上限设成「窗口 × 目标采样率」，
            # 由采集器按回读把深度压到该上限（与 z_chain_stability 同一套已验证路径）。
            acq.use_auto_depth(int(round(float(args.window) * float(args.rate))))
            acq.apply_config(config)
            print("[阶段] 示波器采集配置完成", flush=True)
        ctx.state["scope"] = dict(
            device_id=scope_device_id,
            before=scope_before,
            channels=channels,
            window_s=(float(args.trigger_tdiv) * 10.0 if args.use_scope_config else float(args.window)),
            rate_sa_s=(float(scope.get_sampling_rate()) if args.use_scope_config else float(args.rate)),
            frames=int(args.frames),
            trigger=dict(mode="AUTO", source=f"C{TRIGGER_CHANNEL}", slope="RISing", level=float(args.trigger_level)),
            pulse_period_s=1.0 / float(params.y_rf_freq_hz),
            px_channel=PX_CHANNEL,
            trigger_channel=TRIGGER_CHANNEL,
        )
        ctx.save()

        print("[阶段] 泵浦与示波器就绪，开始准备输出", flush=True)
        preparation.prepare_measurement(ctx)
        print("[阶段] 输出准备完成（脉冲链已布防、RF 已关闭）", flush=True)
        ctx.state["rf_control"] = _disable_rf(ctx)
        ctx.save()
        ctx.state["scope"] = dict(
            device_id=scope_device_id,
            before=scope_before,
            channels=channels,
            window_s=float(args.window),
            rate_sa_s=float(args.rate),
            frames=int(args.frames),
            trigger=dict(mode="AUTO", source=f"C{TRIGGER_CHANNEL}", slope="RISing", level=float(args.trigger_level)),
            pulse_period_s=1.0 / float(params.y_rf_freq_hz),
            px_channel=PX_CHANNEL,
            trigger_channel=TRIGGER_CHANNEL,
        )
        ctx.save()

        ctx.state["px_states"] = dict(order=[s.state_id for s in STATES], states={})
        for idx, spec in enumerate(STATES, 1):
            record = dict(label_zh=spec.label_zh, label_en=spec.label_en, pump_on=spec.pump_on,
                          z_on=spec.z_on, const_field_ma=spec.const_field_ma,
                          frames=[], scope=ctx.state["scope"])
            state_records[spec.state_id] = record
            ctx.state["px_states"]["states"][spec.state_id] = record
            print(f"[{idx}/{len(STATES)}] 切换状态 {spec.state_id}: {spec.label_zh}"
                  f"（{int(args.frames)} 帧 × {float(args.window) * 1e3:g} ms）", flush=True)
            _capture_state(ctx, acq, config, pump_dev, spec, args, record)
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
                ctx.state["pump_control"]["restore_error"] = str(exc)
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
        all_errors = [*pump_errors, *scope_errors, *park_errors, *disconnect_report.errors]
        ctx.state["safety_shutdown"] = {
            **prior,
            "completed": not all_errors,
            "errors": all_errors,
            "pump_errors": pump_errors,
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
    for state_id, item in summary["states"].items():
        print(f"  {state_id:14s} | mean={item['mean_v']:+.4e} V | std={item['std_v']:.3e} V | "
              f"p2p={item['peak_to_peak_v']:.3e} V")
    return 0


# ---------------------------------------------------------------- 离线分析


def _read_config(study_dir: Path) -> dict[str, Any]:
    path = study_dir / "study_config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"找不到研究配置：{path}")
    return run_format.read_config(path) or {}


def _state_frames(study_dir: Path, state_id: str) -> tuple[list[dict[str, Any]], float | None]:
    """按文件名顺序读回某状态的全部帧；采样率由首帧 preamble 换算。"""
    frames = []
    for path in sorted((study_dir / "raw").glob(f"{state_id}_frame*.npz")):
        with np.load(path, allow_pickle=False) as data:
            frames.append(dict(t_px=data["t_px"], v_px=data["v_px"],
                               t_trigger=data["t_trigger"], v_trigger=data["v_trigger"],
                               sampling_rate=float(data["sampling_rate"])))
    rate = frames[0]["sampling_rate"] if frames else None
    return frames, rate


def _rising_edges(t: np.ndarray, v: np.ndarray, count_hint: int | None = None) -> np.ndarray:
    """以中值为阈值的上升沿时刻（线性插值到阈值），与采样率无关。"""
    if t.size < 3:
        return np.empty(0)
    v_min = float(np.percentile(v, 1.0))
    v_max = float(np.percentile(v, 99.0))
    if not math.isfinite(v_min) or not math.isfinite(v_max) or v_max - v_min < 1e-3:
        return np.empty(0)
    threshold = 0.5 * (v_min + v_max)
    above = v >= threshold
    edges = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    times = []
    for idx in edges:
        v0, v1 = float(v[idx - 1]), float(v[idx])
        span = v1 - v0
        frac = 0.5 if span == 0 else (threshold - v0) / span
        times.append(float(t[idx - 1] + frac * (t[idx] - t[idx - 1])))
    # 去掉抖动造成的过近重复沿。
    if not times:
        return np.empty(0)
    keep = [times[0]]
    min_gap = 0.2 * (t[1] - t[0]) if t.size > 1 else 0.0
    for value in times[1:]:
        if value - keep[-1] > max(min_gap, 0.0):
            keep.append(value)
    return np.asarray(keep, dtype=float)


def _folded(t: np.ndarray, v: np.ndarray, edges: np.ndarray, period_s: float, n_points: int = 400) -> np.ndarray:
    """按触发边沿折叠到 [0, period) 的平均波形；无沿时返回空数组。"""
    if edges.size == 0 or not (period_s > 0):
        return np.empty(0)
    offsets = np.linspace(0.0, period_s, n_points, endpoint=False)
    acc = np.zeros_like(offsets)
    count = 0
    for edge in edges:
        window = v[(t >= edge) & (t < edge + period_s)]
        window_t = t[(t >= edge) & (t < edge + period_s)]
        if window.size < 3:
            continue
        acc += np.interp(offsets + edge, window_t, window)
        count += 1
    return acc / count if count else np.empty(0)


def analyze_study(study_dir: Path, *, period_s: float | None = None) -> dict[str, Any]:
    study_dir = Path(study_dir).resolve()
    config = _read_config(study_dir)
    scope_cfg = config.get("scope") or {}
    period = float(period_s if period_s is not None else scope_cfg.get("pulse_period_s") or 200e-6)
    order = (config.get("px_states") or {}).get("order") or [s.state_id for s in STATES]
    specs = {s.state_id: s for s in STATES}

    summary: dict[str, Any] = dict(
        study_dir=str(study_dir),
        states={},
        period_s=period,
        px_channel=scope_cfg.get("px_channel", PX_CHANNEL),
        trigger_channel=scope_cfg.get("trigger_channel", TRIGGER_CHANNEL),
        window_s=scope_cfg.get("window_s"),
        rate_sa_s=scope_cfg.get("rate_sa_s"),
        trigger=scope_cfg.get("trigger"),
        baseline=BASELINE_ID,
    )
    folds: dict[str, dict[str, np.ndarray]] = {}
    frames_cache: dict[str, list[dict[str, Any]]] = {}
    for state_id in order:
        frames, rate = _state_frames(study_dir, state_id)
        frames_cache[state_id] = frames
        if not frames:
            continue
        v_all = np.concatenate([f["v_px"] for f in frames])
        t0 = frames[0]["t_px"] - frames[0]["t_px"][0]
        folded_list = []
        for frame in frames:
            edges = _rising_edges(frame["t_trigger"], frame["v_trigger"])
            folded = _folded(frame["t_trigger"], frame["v_px"], edges, period)
            if folded.size:
                folded_list.append(folded)
        folded_mean = np.mean(folded_list, axis=0) if folded_list else np.empty(0)
        folded_std = np.std(folded_list, axis=0, ddof=1) if len(folded_list) > 1 else np.empty(0)
        base_time = frames[0]["t_px"]
        rel_time = base_time - float(np.mean(base_time))
        mean_curve = np.mean([np.interp(rel_time, f["t_px"] - float(np.mean(f["t_px"])), f["v_px"])
                              for f in frames], axis=0)
        edges_first = _rising_edges(frames[0]["t_trigger"], frames[0]["v_trigger"])
        stats = dict(
            label_zh=specs[state_id].label_zh if state_id in specs else state_id,
            label_en=specs[state_id].label_en if state_id in specs else state_id,
            frames=len(frames),
            mean_v=float(np.mean(v_all)),
            std_v=float(np.std(v_all, ddof=1)) if v_all.size > 1 else 0.0,
            peak_to_peak_v=float(np.ptp(v_all)),
            per_frame_mean_v=[float(np.mean(f["v_px"])) for f in frames],
            per_frame_std_v=[float(np.std(f["v_px"], ddof=1)) for f in frames],
            clipped_fraction=float(np.mean(np.abs(v_all) >= 0.98 * 4.0 * float(
                ((config.get("scope") or {}).get("channels") or {}).get(PX_CHANNEL, {}).get("scale") or 1.0)
            )) if v_all.size else None,
            folded_points=int(folded_mean.size),
            folded_peak_to_peak_v=float(np.ptp(folded_mean)) if folded_mean.size else None,
            trigger_edges_per_frame=len(edges_first),
        )
        summary["states"][state_id] = stats
        folds[state_id] = dict(rel_time=rel_time, mean_curve=mean_curve,
                               offsets=np.linspace(0.0, period, folded_mean.size, endpoint=False) if folded_mean.size else np.empty(0),
                               folded_mean=folded_mean, folded_std=folded_std)

    if not summary["states"]:
        raise ValueError(f"{study_dir} 下没有可用的 Px 帧数据")

    base = summary["states"].get(BASELINE_ID)
    for state_id, item in summary["states"].items():
        item["delta_mean_vs_baseline_v"] = (item["mean_v"] - base["mean_v"]) if base else None
        item["ratio_std_vs_baseline"] = (item["std_v"] / base["std_v"]) if base and base["std_v"] else None

    results = study_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    summary_path = results / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = results / "px_states_stats.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["state", "label_en", "frames", "mean_v", "std_v", "peak_to_peak_v",
                         "delta_mean_vs_baseline_v", "ratio_std_vs_baseline", "folded_peak_to_peak_v",
                         "trigger_edges_per_frame"])
        for state_id, item in summary["states"].items():
            writer.writerow([
                state_id, item["label_en"], item["frames"], f"{item['mean_v']:.6e}", f"{item['std_v']:.6e}",
                f"{item['peak_to_peak_v']:.6e}",
                "" if item["delta_mean_vs_baseline_v"] is None else f"{item['delta_mean_vs_baseline_v']:.6e}",
                "" if item["ratio_std_vs_baseline"] is None else f"{item['ratio_std_vs_baseline']:.4g}",
                "" if item["folded_peak_to_peak_v"] is None else f"{item['folded_peak_to_peak_v']:.6e}",
                item["trigger_edges_per_frame"],
            ])

    pxsel = summary["px_channel"]
    trigsel = summary["trigger_channel"]
    with plot_style_context("paper"):
        # 图 1：五态 Px 时域（逐帧细线 + 均值粗线 + 触发上升沿）
        nrows = len(summary["states"])
        fig, axes = new_figure(figsize=(7.6, 1.35 * nrows + 1.0), nrows=nrows, ncols=1,
                               sharex=True, constrained_layout=True)
        axes = np.atleast_1d(axes)
        for ax, state_id in zip(axes, summary["states"]):
            item = summary["states"][state_id]
            frames = frames_cache.get(state_id, [])
            color = getattr(specs[state_id], "color", COLOR_GRAY) if state_id in specs else COLOR_GRAY
            for frame in frames:
                rel = frame["t_px"] - float(np.mean(frame["t_px"]))
                ax.plot(rel * 1e3, frame["v_px"] * 1e3, color="#999999", lw=0.5, alpha=0.45)
            if frames:
                # 只用第一帧的触发沿做时间参考，避免五帧叠加把图糊满。
                edges = _rising_edges(frames[0]["t_trigger"], frames[0]["v_trigger"])
                for edge in edges[:10]:
                    ax.axvline((edge - float(np.mean(frames[0]["t_px"]))) * 1e3,
                               color="#CCCCCC", lw=0.4, alpha=0.45, zorder=0)
            ax.plot(folds[state_id]["rel_time"] * 1e3, folds[state_id]["mean_curve"] * 1e3,
                    color=color, lw=1.3, label=f"{item['label_en']} | mean {item['mean_v']*1e3:.2f} mV, "
                                               f"std {item['std_v']*1e3:.3f} mV")
            ax.set_ylabel("Px (mV)")
            ax.grid(alpha=0.2)
            style_legend(ax, loc="upper right", fontsize=6.2, framealpha=0.9)
        axes[-1].set_xlabel("Time from frame centre (ms)")
        fig.suptitle(f"{study_dir.name}: Px (CH{pxsel}) time series, {nrows} Z-field/pump states, "
                     f"trigger CH{trigsel} | RF OFF, {summary['window_s']*1e3:.0f} ms window",
                     fontsize=8.6)
        fig.savefig(results / "px_states_time.png", dpi=240)

        # 图 2：折叠到脉冲周期的波形 + 均值/起伏统计
        fig, (ax_fold, ax_bar) = new_figure(figsize=(7.6, 3.2), nrows=1, ncols=2, constrained_layout=True)
        for state_id, item in summary["states"].items():
            fold = folds[state_id]
            if not fold["folded_mean"].size:
                continue
            color = getattr(specs[state_id], "color", COLOR_GRAY) if state_id in specs else COLOR_GRAY
            ls = getattr(specs[state_id], "linestyle", "-") if state_id in specs else "-"
            ax_fold.plot(fold["offsets"] * 1e6, fold["folded_mean"] * 1e3, color=color, ls=ls, lw=1.2,
                         label=item["label_en"])
            if fold["folded_std"].size:
                ax_fold.fill_between(fold["offsets"] * 1e6,
                                     (fold["folded_mean"] - fold["folded_std"]) * 1e3,
                                     (fold["folded_mean"] + fold["folded_std"]) * 1e3,
                                     color=color, alpha=0.12, lw=0)
        ax_fold.set(xlabel=f"Time since CH{trigsel} rising edge (us)",
                    ylabel="Px folded mean (mV)",
                    title=f"Px folded on trigger, period {period*1e6:.1f} us")
        ax_fold.grid(alpha=0.2)
        style_legend(ax_fold, loc="upper right", fontsize=6.2, framealpha=0.9)

        labels = [item["label_en"] for item in summary["states"].values()]
        means = [item["mean_v"] * 1e3 for item in summary["states"].values()]
        stds = [item["std_v"] * 1e3 for item in summary["states"].values()]
        xs = np.arange(len(labels))
        ax_bar.bar(xs - 0.19, means, width=0.36, color=[getattr(specs[sid], "color", COLOR_GRAY)
                                                        for sid in summary["states"]],
                   label="mean")
        ax_bar.bar(xs + 0.19, stds, width=0.36, color="#999999", label="std")
        ax_bar.set_xticks(xs)
        ax_bar.set_xticklabels([sid for sid in summary["states"]], rotation=25, ha="right", fontsize=6.5)
        ax_bar.set(ylabel="Px (mV)", title="Per-state mean and std")
        ax_bar.grid(alpha=0.2, axis="y")
        style_legend(ax_bar, loc="upper right", fontsize=6.5, framealpha=0.9)
        fig.savefig(results / "px_states_folded.png", dpi=240)

    readme = [
        f"# {study_dir.name}: Px 五态时域对照",
        "",
        "- 脚本：`scripts/rf_phase_sensitivity/px_field_states_scope.py`",
        f"- 示波器：{scope_cfg.get('device_id')}；CH{pxsel} = 平衡探测器 Px，CH{trigsel} = 控制场触发",
        f"- 窗口：{float(scope_cfg.get('window_s') or 0) * 1e3:g} ms（1 ms/div）、"
        f"{float(scope_cfg.get('rate_sa_s') or 0) / 1e6:g} MS/s、每态 {scope_cfg.get('frames')} 帧；RF 全程 OFF",
        f"- 触发：{scope_cfg.get('trigger')}",
        "- 温控：每态拍照前 `Temp_Switch` 0 V，拍完恢复 5 V 并等待",
        "",
        "| 状态 | 帧数 | 均值 (mV) | 标准差 (mV) | p2p (mV) | 相对基线均值 (mV) | std 比基线 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for state_id, item in summary["states"].items():
        delta = item["delta_mean_vs_baseline_v"]
        ratio = item["ratio_std_vs_baseline"]
        readme.append(
            f"| `{state_id}` | {item['frames']} | {item['mean_v'] * 1e3:+.3f} | {item['std_v'] * 1e3:.3f} | "
            f"{item['peak_to_peak_v'] * 1e3:.2f} | {'' if delta is None else f'{delta * 1e3:+.3f}'} | "
            f"{'' if ratio is None else f'{ratio:.3f}'} |"
        )
    readme += ["", "产物：",
               "  - 时域图：`results/px_states_time.png`",
               "  - 折叠图与统计：`results/px_states_folded.png`",
               "  - 统计表：`results/px_states_stats.csv`",
               "  - 摘要：`results/summary.json`"]
    (study_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return summary


# ---------------------------------------------------------------- 命令行


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RF 关断时用 DHO924 读平衡探测器 Px（CH1）在五态 Z 场/泵浦下的时域波形并对比"
    )
    parser.add_argument("--analyze", type=Path, default=None, help="对已有 _studies 目录离线重绘，不连接硬件")
    parser.add_argument("--tag", default="px_states", help="研究目录名后缀（默认 px_states）")
    parser.add_argument("--use-scope-config", action="store_true",
                        help="不改示波器时基/深度，按面板设置冻结读帧（采样率由仪器自行推导）")
    parser.add_argument("--scope-device", default=SCOPE_DEVICE_ID, help=f"示波器设备库 ID（默认 {SCOPE_DEVICE_ID}）")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_S, help="单帧采集时长（s，默认 10 ms）")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_SA_S,
                        help="目标采样率（Sa/s，默认 10 MS/s）；DHO900 无写入命令，实际值由「窗口×该值」作深度上限决定并回读记录")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="每态帧数（默认 5）")
    parser.add_argument("--trigger-level", type=float, default=DEFAULT_TRIGGER_LEVEL_V,
                        help=f"CH3 触发电平（V，默认 {DEFAULT_TRIGGER_LEVEL_V}）")
    parser.add_argument("--settle", type=float, default=0.5, help="切态后进温控窗前的稳定等待（s）")
    parser.add_argument("--temp-off-settle", type=float, default=0.3, help="关温控后到第一帧之间的等待（s）")
    parser.add_argument("--temp-lag", type=float, default=1.0,
                        help="每帧恢复温控等待（s）；须大于关温控时长（默认 1.0）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.analyze is not None:
        summary = analyze_study(args.analyze)
        print(f"离线出图完成：{Path(args.analyze).resolve() / 'results'}")
        for state_id, item in summary["states"].items():
            print(f"  {state_id:14s} | mean={item['mean_v']:+.4e} V | std={item['std_v']:.3e} V | "
                  f"p2p={item['peak_to_peak_v']:.3e} V")
        return 0
    return run_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
