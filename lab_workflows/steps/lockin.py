"""实验共用的锁相输入初始化。"""

from lockin_amplifier import LockinInstrument


def initialize_lockin_inputs(instrument: LockinInstrument) -> None:
    """关闭全部主电压输入的 AC 耦合和 50 Ω 终端，保留量程与差分设置。"""
    for index in range(instrument.capabilities["voltage_inputs"]):
        path = instrument.sig_in_path(index)
        instrument.set_int(f"{path}/ac", 0)
        instrument.set_int(f"{path}/imp50", 0)
    instrument.sync()
