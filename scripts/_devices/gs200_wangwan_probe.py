"""网络 GS200（王湾主场，192.168.124.41）只读探测：连接、IDN 与当前状态。

只读，不写任何设置，不改输出状态。必须用运行目录的解释器运行（硬件门控按代码根判定）。
"""
import sys

from gs200 import GS200Instrument

RESOURCE = "TCPIP0::192.168.124.41::7655::SOCKET"


def main() -> int:
    inst = GS200Instrument(RESOURCE)
    inst.connect()
    try:
        print("idn:", inst.idn())
        print("source_function:", inst.get_source_function())
        print("output:", inst.get_output())
        print("current_A:", inst.get_current())
        print("current_range_A:", inst.get_current_range())
        print("current_limit_A:", inst.get_current_limit())
        print("voltage_limit_V:", inst.get_voltage_limit())
    finally:
        inst.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
