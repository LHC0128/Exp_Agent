"""DirectAW 共用的取消、等待、安全电平与节点回读。"""
from __future__ import annotations
import math
import time
from contextlib import contextmanager
from ...steps.acquisition_window import DeferredAcquisitionWrites
from ...steps.temperature import set_temperature_switch
from lockin_amplifier import LockinInstrument
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


@contextmanager
def buffered_acquisition(ctx):
    """当前 RF 采集组恢复温控后落盘，激励模块共用同一个缓存。"""
    previous = getattr(ctx, "pending_writes", None)
    restore = lambda: set_temperature_switch(ctx.devices["signals"], True,
                                             channel=ctx.devices["channels"]["temp"])
    with DeferredAcquisitionWrites(restore=restore) as pending:
        ctx.pending_writes = pending
        try:
            yield pending
        finally:
            ctx.pending_writes = previous
def validate_levels(key: str, amplitude: float, offset: float = 0.0) -> None:
    for value in (offset, offset - amplitude / 2, offset + amplitude / 2):
        validate_safety_limit(key, float(value))


def hf2_snapshot(hf2: LockinInstrument, params) -> dict:
    """保存实际节点，包含响应或噪声配置的实际采样率。"""
    snapshot = {}
    nodes = [
        (hf2.demod_path(0), ("enable", "adcselect", "oscselect", "harmonic", "order"), ("rate", "timeconstant", "phaseshift")),
        (hf2.demod_path(params.demod3_idx), ("enable", "adcselect", "oscselect", "harmonic", "order"), ("rate", "timeconstant", "phaseshift")),
        (hf2.aux_out_path(params.rf_aux_output_idx), ("outputselect", "demodselect"), ("scale", "offset")),
        (hf2.osc_path(0), (), ("freq",)), (hf2.osc_path(params.demod3_osc_idx), (), ("freq",)),
    ]
    nodes.extend((hf2.sig_in_path(idx), ("ac", "imp50", "diff"), ("range",))
                 for idx in range(hf2.capabilities["voltage_inputs"]))
    for base, integers, doubles in nodes:
        for node in integers:
            snapshot[f"{base}/{node}"] = int(hf2.get_int(f"{base}/{node}"))
        for node in doubles:
            value = float(hf2.get_double(f"{base}/{node}"))
            if not math.isfinite(value):
                raise RuntimeError(f"锁相回读非有限：{base}/{node}")
            snapshot[f"{base}/{node}"] = value
    return snapshot
