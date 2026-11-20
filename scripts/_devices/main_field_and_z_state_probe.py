"""只读核对（在运行目录执行）：王湾主磁场 GS200（192.168.124.41）与 Z 通道 DG4000（192.168.124.33）当前状态。"""
from gs200 import GS200Instrument  # noqa: E402

inst = GS200Instrument("TCPIP0::192.168.124.41::7655::SOCKET")
inst.connect()
try:
    print("GS200 idn:", inst.idn())
    print("  source_function:", inst.get_source_function())
    print("  output:", inst.get_output())
    print("  current_A:", inst.get_current())
    print("  range_A:", inst.get_current_range())
    print("  current_limit_A:", inst.get_current_limit())
    print("  voltage_limit_V:", inst.get_voltage_limit())
finally:
    inst.disconnect()

from signal_generator import create_signal_generator  # noqa: E402

cfg = {"resource": "TCPIP0::192.168.124.33::INSTR", "instrument": "signal_generator", "model": "DG4000", "channel": 1}
sg = create_signal_generator(cfg)
sg.connect()
try:
    print("Z DG4000 idn:", sg.idn())
    print("  CH1 output:", sg.get_output(channel=1))
    print("  CH1 shape:", sg.get_shape(channel=1))
    print("  CH1 dc level:", sg.get_dc_level(channel=1) if hasattr(sg, "get_dc_level") else "n/a")
finally:
    sg.disconnect()
