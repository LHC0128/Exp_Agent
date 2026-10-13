"""DirectAW 共用的取消、等待、安全电平与节点回读。"""
from __future__ import annotations
import math
import time
from lockin_amplifier import HF2Instrument
from ...common import CancellationToken, validate_safety_limit
from ...experiment_runtime import check_cancelled

class _RuntimeCancellation(CancellationToken):
    def raise_if_cancelled(self) -> None:
        super().raise_if_cancelled()
        check_cancelled()


def wait(seconds: float) -> None:
    deadline = time.monotonic() + max(0, seconds)
    check_cancelled()
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(0.1, remaining))
        check_cancelled()
def validate_levels(key: str, amplitude: float, offset: float = 0.0) -> None:
    for value in (offset, offset - amplitude / 2, offset + amplitude / 2):
        validate_safety_limit(key, float(value))


def hf2_snapshot(hf2: HF2Instrument) -> dict:
    """保存实际节点，包含响应或噪声配置的实际采样率。"""
    snapshot = {}
    for base, integers, doubles in (
        (hf2.demod_path(0), ("enable", "adcselect", "oscselect", "harmonic", "order"), ("rate", "timeconstant", "phaseshift")),
        (hf2.demod_path(3), ("enable", "adcselect", "oscselect", "harmonic", "order"), ("rate", "timeconstant", "phaseshift")),
        (hf2.sig_in_path(0), ("ac", "imp50", "diff"), ("range",)),
        (hf2.sig_in_path(1), ("ac", "imp50", "diff"), ("range",)),
        (hf2.aux_out_path(1), ("outputselect", "demodselect"), ("scale", "offset")),
        (hf2.osc_path(0), (), ("freq",)), (hf2.osc_path(1), (), ("freq",)),
    ):
        for node in integers:
            snapshot[f"{base}/{node}"] = int(hf2.get_int(f"{base}/{node}"))
        for node in doubles:
            value = float(hf2.get_double(f"{base}/{node}"))
            if not math.isfinite(value):
                raise RuntimeError(f"HF2 回读非有限：{base}/{node}")
            snapshot[f"{base}/{node}"] = value
    return snapshot
