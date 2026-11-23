"""实验前设备连通性自检：逐台连接并查询身份，只读，不设置任何输出。

用途：把“连接超时、串口被占、VXI-11 挂死”这类环境故障在开实验前暴露出来，
而不是等到 A02 才失败、白跑一次。

用法（在带 .lab_runtime 的运行目录执行）：
    python tools/check_devices.py               # 检查当前映射里的全部设备
    python tools/check_devices.py --timeout 8   # 放宽单台超时
    python tools/check_devices.py --key Z_magnetic_field   # 只查指定映射键

退出码：0 全部连通；1 有设备失败；2 不在运行目录（硬件门控拒绝）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aigtek_1200b import Aigtek1200BInstrument  # noqa: E402
from gs200 import GS200Instrument  # noqa: E402
from oscilloscope import create_oscilloscope  # noqa: E402
from tec_controller import TECInstrument  # noqa: E402

from hardware_gate import HardwareAccessDenied  # noqa: E402
from lab_workflows.common import load_mapping  # noqa: E402
from lab_workflows.devices import create_lockin, create_signal_generator  # noqa: E402


# 本实验链路不涉及、且自检未实现连接方式的类型；遇到时标记跳过而不是失败。
UNSUPPORTED_KINDS = frozenset({"keithley_6221", "toptica_dlc_pro", "sds_acquisition"})


class SkipDevice(Exception):
    """自检不支持的设备类型：跳过，不影响退出码。"""


def _factory(config: dict[str, Any]) -> Callable[[], Any]:
    """按映射里的 instrument 类型选择创建函数；不支持的型号直接报错。"""
    kind = str(config.get("instrument", "")).lower()
    if kind in UNSUPPORTED_KINDS:
        raise SkipDevice(f"自检暂不支持该设备类型: {kind}")
    if kind == "signal_generator":
        # DG 连接默认下发输出负载；连通性自检必须只查身份。
        return lambda: create_signal_generator(config, apply_load_policy=False)
    if kind == "lockin_amplifier":
        return lambda: create_lockin(config)
    if kind == "power_amplifier":
        return lambda: Aigtek1200BInstrument(
            config["resource"],
            serial_number=str((config.get("connection") or {}).get("serial_number", "0001")))
    if kind == "oscilloscope":
        return lambda: create_oscilloscope(config)
    if kind == "gs200":
        return lambda: GS200Instrument(config["resource"])
    if kind == "tec_controller":
        return lambda: TECInstrument(config["resource"])
    raise ValueError(f"自检不支持该设备类型: {kind}")


def probe(mapping: dict[str, Any], keys: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """逐台连接并断开，返回每台的结果；不读取或写入任何输出量。"""
    results = []
    for key in (keys if keys is not None else sorted(mapping)):
        config = mapping.get(key) or {}
        kind, resource = config.get("instrument"), config.get("resource")
        entry = {"key": key, "instrument": kind, "resource": resource}
        # LabOne 锁相按设备 ID 连接，不使用 VISA resource。
        if not (resource or (kind == "lockin_amplifier" and config.get("device_id"))):
            if keys is not None:
                results.append({**entry, "ok": False, "error": "指定映射不存在或缺少连接地址", "seconds": 0.})
            continue
        started = time.monotonic()
        device = None
        try:
            device = _factory(config)()
            device.connect()
            entry["ok"] = True
            entry["error"] = ""
        except HardwareAccessDenied:
            raise
        except SkipDevice as exc:
            entry["ok"] = True
            entry["skipped"] = str(exc)
            entry["error"] = ""
        except Exception as exc:  # noqa: BLE001 - 自检要把任何失败都报出来
            entry["ok"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            entry["seconds"] = round(time.monotonic() - started, 2)
            if device is not None:
                try:
                    device.disconnect()
                except Exception:  # noqa: BLE001 - 断开失败不改变自检结论
                    pass
        results.append(entry)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="实验前设备连通性自检（只连接，不设置输出）")
    parser.add_argument("--key", action="append", help="只检查指定映射键，可重复")
    parser.add_argument("--timeout", type=float, default=0.0, help="保留参数；超时由各驱动决定")
    args = parser.parse_args(argv)

    try:
        mapping = load_mapping(ROOT)
    except Exception as exc:  # noqa: BLE001
        print(f"读取映射失败：{exc}", flush=True)
        return 1

    try:
        results = probe(mapping, args.key)
    except HardwareAccessDenied as exc:
        print(f"硬件门控拒绝：{exc}", flush=True)
        print("请在带 .lab_runtime 的运行目录执行本脚本。", flush=True)
        return 2

    failed = [item for item in results if not item["ok"]]
    for item in results:
        if item.get("skipped"):
            mark, detail = "SKIP", f"  {item['skipped']}"
        else:
            mark = "OK  " if item["ok"] else "FAIL"
            detail = "" if item["ok"] else f"  {item['error']}"
        print(f"{mark} {item['key']:26s} {item['seconds']:6.1f} s{detail}", flush=True)
    print(f"共 {len(results)} 台，失败 {len(failed)} 台", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
