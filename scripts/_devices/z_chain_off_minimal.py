"""只关 Z 脉冲链（1200B 输出 + Z 通道输出），不连接锁相/温控/主磁场。

用于锁相掉线、完整收尾脚本连不上时先把危险输出关掉；随后仍应尽快用
``z_pulse_on_hold.py --off`` 或正常 park 流程补齐主磁场、温控与 X/Y 偏置的恢复。
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

try:
    import hardware_gate as _hg  # noqa: F401
except ImportError:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from aigtek_1200b import Aigtek1200BInstrument
from hardware_gate import require_hardware_access, runtime_root
from lab_workflows.common import load_mapping
from lab_workflows.devices import create_signal_generator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只关 1200B 与 Z 通道输出")
    parser.parse_args(argv)
    require_hardware_access("Z 脉冲链最小收尾")
    root = runtime_root()
    mapping = load_mapping(root)
    amp_cfg = mapping["coil_drive_amplifier"]
    source_cfg = mapping["Z_magnetic_field"]

    amp = Aigtek1200BInstrument(amp_cfg["resource"],
                                serial_number=str((amp_cfg.get("connection") or {}).get("serial_number", "0001")))
    amp.connect()
    errors: list[str] = []
    try:
        amp.set_output(False)
        print("[amp] 1200B 输出已命令关闭（该型号无状态回读）", flush=True)
    except Exception as exc:
        errors.append(f"关闭 1200B: {exc}")
    finally:
        try:
            amp.disconnect()
        except Exception as exc:
            errors.append(f"断开 1200B: {exc}")

    source = create_signal_generator(source_cfg)
    source.connect()
    try:
        channel = int(source_cfg.get("channel") or 1)
        source.set_output(False, channel=channel)
        source.wait_for_operation_complete()
        state = source.get_output(channel=channel)
        print(f"[z] CH{channel} output={state}", flush=True)
        if state:
            errors.append("Z 通道输出未回读为 OFF")
    finally:
        try:
            source.disconnect()
        except Exception as exc:
            errors.append(f"断开 Z 通道: {exc}")

    if errors:
        raise SystemExit("；".join(errors))
    print("Z 脉冲链已关闭（主磁场/温控/偏置未处理）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
