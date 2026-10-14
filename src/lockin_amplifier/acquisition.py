"""解调时序的统一选择、离线校验与采集元数据；不连接硬件。"""

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
import math
import sys

import numpy as np

from .config import DAQConfig, DAQResult

logger = logging.getLogger(__name__)
AUTO_SCOPE_THRESHOLD = 70_000.0
ACQUISITION_MODES = frozenset({"auto", "daq", "scope"})
DEMOD_SIGNALS = frozenset({"sample.x", "sample.y", "sample.r", "sample.theta"})


def acquisition_module(model: str, requested_rate: float, mode: str = "auto") -> str:
    """只按请求值选模块，避免设备速率取整改变 70 kSa/s 边界。"""
    if mode not in ACQUISITION_MODES:
        raise ValueError("acquisition_mode 必须为 auto、daq 或 scope")
    if isinstance(requested_rate, bool) or not math.isfinite(requested_rate) or requested_rate <= 0:
        raise ValueError("请求采样率必须为有限正数")
    if model.upper() not in {"HF2", "MFLI"}:
        raise ValueError(f"不支持的锁相放大器型号: {model}")
    if mode != "auto":
        return mode
    return "scope" if model.upper() == "MFLI" and requested_rate > AUTO_SCOPE_THRESHOLD else "daq"


def nearest_scope_rate(requested_rate: float, maximum: float = 60e6) -> float:
    """绝对距离最近的硬件档位；等距时取较高档。"""
    if isinstance(requested_rate, bool) or not math.isfinite(requested_rate) or requested_rate <= 0:
        raise ValueError("请求采样率必须为有限正数")
    rates = [maximum / 2**divider for divider in range(16)]
    return min(rates, key=lambda rate: (abs(rate - requested_rate), -rate))


@dataclass(frozen=True)
class AcquisitionRequest:
    """实验显式声明的单阶段解调采集要求，用于启动输出前的预检。"""

    stage: str
    requested_rate: float
    duration: float
    demod_index: int = 0
    signals: tuple[str, ...] = ("sample.x", "sample.y")
    trigger_type: int = 0
    grid_rows: int = 1
    extra_paths: tuple[str, ...] = ()

    def config(self) -> DAQConfig:
        if (isinstance(self.requested_rate, bool) or isinstance(self.duration, bool)
                or not math.isfinite(self.requested_rate) or self.requested_rate <= 0
                or not math.isfinite(self.duration) or self.duration <= 0):
            raise ValueError(f"{self.stage}: 请求速率和时长必须为有限正数")
        return DAQConfig(duration=self.duration, grid_cols=max(1, round(self.requested_rate * self.duration)),
                         signal_paths=list(self.signals), trigger_type=self.trigger_type,
                         grid_rows=self.grid_rows, extra_paths=list(self.extra_paths))


@dataclass(frozen=True)
class AcquisitionPlan:
    module: str
    requested_rate: float
    estimated_rate: float
    length: int
    sources: tuple[str, ...] = ()
    derived_signals: dict[str, str] = field(default_factory=dict)


def select_acquisition(model: str, capabilities: dict, config: DAQConfig, requested_rate: float,
                       demod_idx: int = 0, mode: str = "auto") -> AcquisitionPlan:
    """公共能力校验；失败不回退模块、不缩短记录、不替换接线。"""
    module = acquisition_module(model, requested_rate, mode)
    if isinstance(config.duration, bool) or not math.isfinite(config.duration) or config.duration <= 0:
        raise ValueError("采集时长必须为有限正数")
    if isinstance(demod_idx, bool) or not isinstance(demod_idx, int) or not 0 <= demod_idx < capabilities["demodulators"]:
        raise ValueError(f"{model} 不支持解调器索引 {demod_idx}")
    if (isinstance(config.grid_cols, bool) or not isinstance(config.grid_cols, int)
            or isinstance(config.grid_rows, bool) or not isinstance(config.grid_rows, int)
            or config.grid_cols < 1 or config.grid_rows < 1 or not config.signal_paths):
        raise ValueError("采集网格和信号列表不能为空")
    if module == "daq":
        return AcquisitionPlan(module, requested_rate, requested_rate, config.grid_cols)
    if model.upper() != "MFLI":
        raise ValueError(f"{model} Scope 不支持直接采集解调信号")
    if not any(option.upper().removeprefix("MF-") == "DIG" for option in capabilities.get("options", [])):
        raise ValueError("MFLI 解调信号 Scope 采集需要 MF-DIG 选件")
    if config.trigger_type != 0 or config.trigger_delay != 0 or config.grid_rows != 1 or config.extra_paths:
        raise ValueError("解调 Scope 仅支持无触发、单行、无额外订阅节点的单段采集")
    if config.grid_mode not in {1, 2, 4}:
        raise ValueError("解调 Scope 不支持该网格模式")
    signals = tuple(dict.fromkeys(config.signal_paths))
    if any(signal not in DEMOD_SIGNALS for signal in signals):
        raise ValueError(f"解调 Scope 不支持信号组合 {signals}")
    derived = {}
    # 相位统一从同步 X/Y 求弧度，避免依赖原生 Scope 相位的显示单位。
    if len(signals) > 2 or "sample.theta" in signals:
        physical = ("sample.x", "sample.y")
        derived = {signal: formula for signal, formula in (
            ("sample.r", "hypot(sample.x, sample.y)"),
            ("sample.theta", "atan2(sample.y, sample.x)")) if signal in signals}
    else:
        physical = signals
    rate = nearest_scope_rate(requested_rate, capabilities["scope_max_rate"])
    length = max(16, round(config.duration * rate))
    if length * len(physical) > capabilities["scope_max_samples"]:
        raise ValueError(f"解调 Scope 单次记录需要 {length * len(physical)} 点，超出内存 {capabilities['scope_max_samples']} 点")
    sources = tuple(f"demod{demod_idx}_{signal.removeprefix('sample.')}" for signal in physical)
    return AcquisitionPlan(module, requested_rate, rate, length, sources, derived)


def restore_demod_streams(instr) -> None:
    """先恢复速率、后恢复使能；逐项恢复，保留原始采集异常。"""
    original = getattr(instr, "_acquisition_stream_settings", None)
    if not original:
        return
    errors = []
    for base, (enable, rate) in original.items():
        try:
            instr.set_int(f"{base}/enable", 0)
            instr.set_double(f"{base}/rate", rate)
            instr.set_int(f"{base}/enable", enable)
        except Exception as exc:
            errors.append(str(exc))
    try:
        instr.sync()
    except Exception as exc:
        errors.append(str(exc))
    if errors:
        raise RuntimeError("解调数据流恢复失败: " + "；".join(errors))
    instr._acquisition_stream_settings = None


def pause_demod_streams(instr) -> None:
    """关闭主机数据传输，保持内部解调滤波和 Aux 信号链运行。"""
    if getattr(instr, "_acquisition_stream_settings", None):
        return
    original = {instr.demod_path(index): (instr.get_int(f"{instr.demod_path(index)}/enable"),
                                         instr.get_double(f"{instr.demod_path(index)}/rate"))
                for index in range(instr.capabilities["demodulators"])}
    instr._acquisition_stream_settings = original
    try:
        for base in original:
            instr.set_int(f"{base}/enable", 0)
        instr.sync()
    except Exception:
        try:
            restore_demod_streams(instr)
        except Exception:
            logger.exception("准备失败后的数据流恢复失败")
        raise


@contextmanager
def suspended_demod_streams(instr):
    """覆盖采集完成、超时、取消和异常；准备时已暂停的流同样恢复。"""
    pause_demod_streams(instr)
    instr._acquisition_stream_depth = getattr(instr, "_acquisition_stream_depth", 0) + 1
    try:
        yield
    finally:
        instr._acquisition_stream_depth -= 1
        original_error = sys.exc_info()[1]
        try:
            if instr._acquisition_stream_depth == 0:
                restore_demod_streams(instr)
        except Exception:
            if original_error is None:
                raise
            logger.exception("采集异常后的数据流恢复失败")


def acquire_demod_scope(instr, config: DAQConfig, plan: AcquisitionPlan, demod_idx: int,
                         timeout: float, cancellation_check=None) -> list[DAQResult]:
    from .scope import ScopeConfig, acquire_scope

    with suspended_demod_streams(instr):
        stream_rate = instr.get_double(f"{instr.demod_path(demod_idx)}/rate")
        raw = acquire_scope(instr, ScopeConfig(list(plan.sources), plan.estimated_rate, plan.length),
                            timeout, cancellation_check=cancellation_check)
        waves = {item.signal_name.split("_", 1)[1]: item.values for item in raw}
        units = {item.signal_name.split("_", 1)[1]: item.config_snapshot["unit"] for item in raw}
        if plan.derived_signals:
            waves["r"] = np.hypot(waves["x"], waves["y"])
            waves["theta"] = np.arctan2(waves["y"], waves["x"])
            units.update(r=units["x"], theta="rad")
        snapshot = {**config.to_dict(), **raw[0].config_snapshot, "acquisition_module": "scope",
                    "requested_rate": plan.requested_rate, "demod_stream_rate": stream_rate,
                    "actual_duration": plan.length / raw[0].config_snapshot["actual_rate"],
                    "derived_signals": plan.derived_signals,
                    "scope_channels": {item.signal_name: item.config_snapshot for item in raw}}
        print(f"Scope 解调采集：请求 {plan.requested_rate:g} Sa/s，实际 {snapshot['actual_rate']:g} Sa/s，时长 {snapshot['actual_duration']:g} s", flush=True)
        return [DAQResult(signal, demod_idx, waves[signal.removeprefix("sample.")], raw[0].time.copy(),
                          {**snapshot, "unit": units[signal.removeprefix("sample.")]},
                          [(0, plan.length)]) for signal in dict.fromkeys(config.signal_paths)]


def result_rate(results: list[DAQResult], fallback: float | None = None) -> float:
    """最终波形速率；fallback 仅兼容历史结果和旧假仪器。"""
    rate = getattr(results[0], "config_snapshot", {}).get("actual_rate", fallback) if results else fallback
    if rate is None or not math.isfinite(float(rate)) or float(rate) <= 0:
        raise ValueError("采集结果缺少有效的实际采样率")
    return float(rate)


def acquisition_metadata(results: list[DAQResult]) -> dict:
    """可直接保存进现有 NPZ；完整逐路快照使用 JSON 避免 pickle。"""
    if not results or not getattr(results[0], "config_snapshot", {}).get("acquisition_module"):
        return {}
    snapshot = results[0].config_snapshot
    return {"acquisition_module": snapshot["acquisition_module"],
            "requested_rate_Sa_s": snapshot["requested_rate"], "actual_rate_Sa_s": snapshot["actual_rate"],
            "demod_stream_rate_Sa_s": snapshot["demod_stream_rate"],
            "actual_duration_s": snapshot["actual_duration"], "time_source": snapshot["time_source"],
            "acquisition_metadata_json": json.dumps({item.signal_name: item.config_snapshot for item in results},
                                                     ensure_ascii=False)}


def save_acquisition_metadata(path, results: list[DAQResult]) -> None:
    """沿用原波形文件名，旁文件保存真实时间轴和逐路采集快照。"""
    from pathlib import Path
    path = Path(path)
    metadata = acquisition_metadata(results)
    if metadata:
        np.savez(path.with_name(path.stem + "_acquisition.npz"), time_s=results[0].time, **metadata)


def load_acquisition_rate(path, fallback: float) -> float:
    """保留既有导入入口，文件读取由离线分析模块负责。"""
    from sensitivity_analysis.acquisition import load_acquisition_rate as read_rate
    return read_rate(path, fallback)
