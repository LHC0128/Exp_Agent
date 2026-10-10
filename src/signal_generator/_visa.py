"""新增公共接口使用的 VISA 传输工具。"""

import math
from pathlib import Path
import time

import pyvisa
from pyvisa.constants import StatusCode


def _image_size(data: bytes, image_format: str) -> int | None:
    if image_format == "BMP":
        if len(data) < 14:
            return None
        if not data.startswith(b"BM"):
            raise ValueError("截图不是 BMP 图片")
        size = int.from_bytes(data[2:6], "little")
        if size < 14:
            raise ValueError("BMP 文件长度无效")
        return size
    if len(data) < 8:
        return None
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("截图不是 PNG 图片")
    offset = 8
    while len(data) >= offset + 12:
        length = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + length + 12
        if len(data) < end:
            return None
        if data[offset + 4:offset + 8] == b"IEND":
            if length:
                raise ValueError("PNG 结束块无效")
            return end
        offset = end
    return None


def _read_screenshot(instrument, image_format: str) -> bytes:
    data = b""

    def read_more() -> None:
        nonlocal data
        chunk = instrument.read_raw()
        if not chunk:
            raise RuntimeError("截图数据被截断")
        data += chunk

    def need(length: int) -> None:
        while len(data) < length:
            read_more()

    need(2)
    if data.startswith(b"#"):
        if data[1:2] not in b"123456789":
            raise ValueError("截图二进制块必须声明确定长度")
        digits = int(data[1:2])
        need(2 + digits)
        size_text = data[2:2 + digits]
        if not size_text.isdigit():
            raise ValueError("截图二进制块长度无效")
        size = int(size_text)
        start = 2 + digits
        need(start + size)
        payload = data[start:start + size]
        trailer = data[start + size:]
    else:
        size = _image_size(data, image_format)
        while size is None:
            read_more()
            size = _image_size(data, image_format)
        need(size)
        payload, trailer = data[:size], data[size:]
    if _image_size(payload, image_format) != len(payload):
        raise RuntimeError("截图长度与图片文件头不一致，数据不完整")
    if trailer not in {b"", b"\n", b"\r\n"}:
        raise ValueError("截图末尾包含额外数据")
    return payload


def get_screenshot(driver, image_format, save_path, timeout_ms, *, supports_png):
    image_format = str(image_format).upper()
    allowed = {"BMP", "PNG"} if supports_png else {"BMP"}
    if image_format not in allowed:
        raise ValueError("DG912 Pro 支持 BMP/PNG" if supports_png else "DG4000 仅支持 BMP 截图")
    if not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise ValueError("timeout_ms 必须是正整数")
    driver._ensure_connected()
    instrument = driver._inst
    old_timeout, old_termination = instrument.timeout, instrument.read_termination
    try:
        instrument.timeout = timeout_ms
        instrument.read_termination = None
        if supports_png:
            driver.write(f":HCOPy:SDUMp:DATA:FORMat {image_format}")
        driver.write(":HCOPy:SDUMp:DATA?")
        data = _read_screenshot(instrument, image_format)
    finally:
        instrument.timeout = old_timeout
        instrument.read_termination = old_termination
    if save_path is not None:
        Path(save_path).write_bytes(data)
    return data


def recover_connection(driver, drain_reads, *, wait_seconds=None):
    if not isinstance(drain_reads, int) or drain_reads < 0:
        raise ValueError("drain_reads 必须是非负整数")
    if wait_seconds is not None and (not math.isfinite(wait_seconds) or wait_seconds < 0):
        raise ValueError("wait_seconds 必须是有限的非负数")
    opened = driver._inst is None
    try:
        if opened:
            # 恢复入口不能依赖 connect() 的首次 IDN 查询成功。
            driver._rm = pyvisa.ResourceManager()
            driver._inst = driver._rm.open_resource(driver.resource_string)
        instrument = driver._inst
        instrument.read_termination = "\n"
        instrument.write_termination = "\n"
        try:
            instrument.clear()
        except pyvisa.errors.VisaIOError:
            pass  # 部分网络 VISA 后端不提供设备清除，继续尝试清理读取队列。
        instrument.timeout = 200
        try:
            for _ in range(drain_reads):
                try:
                    instrument.read()
                except pyvisa.errors.VisaIOError as exc:
                    if exc.error_code != StatusCode.error_timeout:
                        raise
                    break
        finally:
            instrument.timeout = driver.timeout
        if wait_seconds is not None:
            instrument.write("*RST")
            time.sleep(wait_seconds)
        instrument.write("*CLS")
        identity = str(instrument.query("*IDN?")).strip()
        if not identity:
            raise RuntimeError("通信恢复后仪器身份仍为空")
        return identity
    except Exception:
        if opened:
            driver.disconnect()
        raise
