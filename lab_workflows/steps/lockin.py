"""实验共用的锁相输入初始化与采集模式校验。"""

import math

from lockin_amplifier import LockinInstrument, acquisition_module


def initialize_lockin_inputs(instrument: LockinInstrument) -> None:
    """关闭全部主电压输入的 AC 耦合和 50 Ω 终端，保留量程与差分设置。"""
    for index in range(instrument.capabilities["voltage_inputs"]):
        path = instrument.sig_in_path(index)
        instrument.set_int(f"{path}/ac", 0)
        instrument.set_int(f"{path}/imp50", 0)
    instrument.sync()


def verify_lockin_acquisition_mode(hfi: LockinInstrument, *, demod_index: int,
                                   requested_rate_hz: float, acquisition_rate_hz: float,
                                   time_constant_s: float, phase_deg: float) -> None:
    """只读核对采集所需的解调器速率、时间常数与相位，通信异常原样传播。"""
    base = hfi.demod_path(demod_index)
    expected = {"rate": acquisition_rate_hz, "timeconstant": time_constant_s,
        "phaseshift": phase_deg}
    if acquisition_module(hfi.model, requested_rate_hz, getattr(hfi, "acquisition_mode", "auto")) == "scope":
        expected.pop("rate")  # Scope 后恢复的是原通信速率，内部滤波仍需核对。
    for field, value in expected.items():
        if not math.isclose(float(hfi.get_double(f"{base}/{field}")), value, rel_tol=1e-6, abs_tol=1e-9):
            raise RuntimeError("解调器实际模式已改变，请结束会话并重新准备")
