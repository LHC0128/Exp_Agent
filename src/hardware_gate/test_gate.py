"""硬件门控测试：只用临时目录与打桩的通信库，不连接硬件。"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import hardware_gate
from hardware_gate import HardwareAccessDenied, MARKER_NAME, denial_reason

REPO = hardware_gate.runtime_root()


def test_missing_marker_denies(tmp_path):
    assert "缺少" in denial_reason(tmp_path)


def test_marker_with_own_path_allows(tmp_path):
    (tmp_path / MARKER_NAME).write_text(str(tmp_path.resolve()) + "\n", encoding="utf-8")
    assert denial_reason(tmp_path) is None


def test_marker_written_with_bom_allows(tmp_path):
    # PowerShell 5.1 的 Set-Content -Encoding UTF8 会写 BOM。
    (tmp_path / MARKER_NAME).write_text(str(tmp_path.resolve()), encoding="utf-8-sig")
    assert denial_reason(tmp_path) is None


def test_marker_copied_from_other_directory_denies(tmp_path):
    lab, dev = tmp_path / "lab", tmp_path / "dev"
    lab.mkdir(), dev.mkdir()
    (dev / MARKER_NAME).write_text(str(lab.resolve()), encoding="utf-8")
    assert "不符" in denial_reason(dev)


def test_development_checkout_has_no_valid_marker():
    # 开发目录（本测试所在仓库）不得带有效标记；运行目录跑测试时跳过。
    if (REPO / MARKER_NAME).exists():
        pytest.skip("运行目录")
    with pytest.raises(HardwareAccessDenied, match="开发目录禁止"):
        hardware_gate.require_hardware_access("测试目标")


def _drivers():
    from aigtek_1200b import Aigtek1200BInstrument
    from dho900 import DHO900Instrument
    from gs200 import GS200Instrument
    from keithley_6221 import Keithley6221Instrument
    from lockin_amplifier import HF2Instrument
    from sds_acquisition import SDSInstrument
    from signal_generator import DG4000Instrument, DG900Instrument
    from tec_controller import TECInstrument
    from toptica_laser import DLCProInstrument

    visa = "pyvisa.ResourceManager"
    return [
        (lambda: GS200Instrument("USB0::GS::INSTR"), visa),
        (lambda: Keithley6221Instrument("GPIB0::12::INSTR"), visa),
        (lambda: SDSInstrument("USB0::SDS::INSTR"), visa),
        (lambda: DHO900Instrument("TCPIP0::192.0.2.21::INSTR"), visa),
        (lambda: DG4000Instrument("USB0::DG4::INSTR"), visa),
        (lambda: DG900Instrument("USB0::DG9::INSTR"), visa),
        (lambda: HF2Instrument(device_id="dev1"), "lockin_amplifier.base._zi.ziDAQServer"),
        (lambda: TECInstrument(port="COM9"), "serial.Serial"),
        (lambda: Aigtek1200BInstrument(port="COM3"), "serial.Serial"),
        (lambda: DLCProInstrument("192.0.2.1", connection_factory=MagicMock(name="factory")), None),
    ]


@pytest.mark.parametrize("index", range(10))
def test_every_driver_connect_is_denied_before_transport(index):
    make, target = _drivers()[index]
    instrument = make()
    with patch("hardware_gate.denial_reason", return_value="测试拒绝"):
        if target is None:
            with pytest.raises(HardwareAccessDenied, match="测试拒绝"):
                instrument.connect()
            instrument._connection_factory.assert_not_called()
        else:
            with patch(target) as transport, pytest.raises(HardwareAccessDenied, match="测试拒绝"):
                instrument.connect()
            transport.assert_not_called()


def test_signal_generator_recovery_is_denied_before_transport():
    from signal_generator import DG4000Instrument
    from signal_generator._visa import recover_connection

    driver = DG4000Instrument("USB0::DG4::INSTR")
    with patch("hardware_gate.denial_reason", return_value="测试拒绝"), \
            patch("pyvisa.ResourceManager") as transport, \
            pytest.raises(HardwareAccessDenied):
        recover_connection(driver, 0)
    transport.assert_not_called()


def test_device_discovery_is_denied_before_transport(tmp_path):
    from lab_workflows.instrument_config import discover_visa_devices

    with patch("hardware_gate.denial_reason", return_value="测试拒绝"), \
            patch("pyvisa.ResourceManager") as visa, \
            patch("zhinst.core.ziDAQServer") as zi, \
            pytest.raises(HardwareAccessDenied):
        discover_visa_devices(tmp_path)
    visa.assert_not_called()
    zi.assert_not_called()


def test_optional_connection_does_not_swallow_denial():
    from lab_workflows.steps.session import DeviceSession

    def denied():
        device = MagicMock()
        device.connect.side_effect = HardwareAccessDenied("拒绝")
        return device

    with pytest.raises(HardwareAccessDenied):
        DeviceSession().connect_optional("tec", "COM9", denied)


# 打开真实通信的调用 → 已登记、前面有门控的文件；新增入口必须先接入门控再登记。
TRANSPORT_CALLS = {"ResourceManager", "ziDAQServer", "Serial", "NetworkConnection", "_connection_factory"}
GATED_FILES = {
    "src/aigtek_1200b/instrument.py",
    "src/dho900/instrument.py",
    "src/gs200/instrument.py",
    "src/keithley_6221/instrument.py",
    "src/sds_acquisition/instrument.py",
    "src/signal_generator/dg4000.py",
    "src/signal_generator/dg900.py",
    "src/signal_generator/_visa.py",
    "src/lockin_amplifier/base.py",
    "src/tec_controller/instrument.py",
    "src/toptica_laser/instrument.py",
    "lab_workflows/instrument_config.py",
}


def _transport_call_files() -> set[str]:
    found = set()
    for folder in ("src", "lab_workflows", "GUI/backend", "experiments"):
        for path in (REPO / folder).rglob("*.py"):
            if path.name.startswith("test_") or "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", getattr(node.func, "id", None))
                    if name in TRANSPORT_CALLS:
                        found.add(path.relative_to(REPO).as_posix())
    return found


def test_all_transport_entries_are_registered_and_gated():
    files = _transport_call_files()
    assert files == GATED_FILES
    for relative in files:
        assert "require_hardware_access(" in (REPO / relative).read_text(encoding="utf-8"), relative
