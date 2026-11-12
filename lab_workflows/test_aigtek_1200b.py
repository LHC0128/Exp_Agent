"""1200B 的设备登记、安全校验回调及公共收尾接线，不连接硬件。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from aigtek_1200b import Aigtek1200BInstrument
from aigtek_1200b import instrument as amplifier_module
from lab_workflows.common import load_safety_limits, validate_safety_limit
from lab_workflows.instrument_config import (
    load_device_document, load_mapping_document, public_device_library,
    resolve_mapping, save_device_library,
)
from lab_workflows.steps.safety_shutdown import DisconnectTarget, ShutdownAction, run_safety_shutdown


ROOT = Path(__file__).resolve().parents[1]
DEVICE_ID = "aigtek_1200b_cp210x_0001"


@pytest.fixture
def fake_amplifier(monkeypatch):
    transport = Mock(is_open=False, in_waiting=0)
    transport.open.side_effect = lambda: setattr(transport, "is_open", True)
    transport.close.side_effect = lambda: setattr(transport, "is_open", False)
    transport.write.side_effect = len
    factory = Mock(return_value=transport)
    ports = Mock(return_value=[SimpleNamespace(device="COM3", vid=0x10C4, pid=0xEA60, serial_number="0001")])
    monkeypatch.setattr(amplifier_module.serial, "Serial", factory)
    monkeypatch.setattr(amplifier_module.list_ports, "comports", ports)
    monkeypatch.setattr("hardware_gate.denial_reason", lambda: None)
    # 每条命令的两次时钟读取间已过时限，免去真实等待。
    tick = iter(range(0, 1000, 4))
    monkeypatch.setattr(amplifier_module.time, "monotonic", lambda: next(tick))
    return transport


def test_device_entry_loads_offline_and_is_not_a_field_mapping(tmp_path):
    params = tmp_path / "params"
    params.mkdir()
    for name in ("devices.yaml", "mapping.yaml"):
        (params / name).write_bytes((ROOT / "params" / name).read_bytes())
    before = {name: (params / name).read_bytes() for name in ("devices.yaml", "mapping.yaml")}
    with patch("serial.Serial") as factory, patch("serial.tools.list_ports.comports") as ports:
        document = public_device_library(tmp_path)
        configured = document["devices"][DEVICE_ID]
        assert configured["instrument"] == "power_amplifier"
        assert configured["model"] == "1200B"
        assert configured["resource"] == "COM3"
        assert configured["reference_clock"] is None
        assert configured["connection"] == {
            "adapter_name": "Silicon Labs CP210x USB to UART Bridge",
            "usb_vid": 0x10C4, "usb_pid": 0xEA60, "serial_number": "0001",
            "baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
            "xonxoff": False, "rtscts": False, "dsrdtr": False,
        }
        assert configured["capabilities"]["supports_state_query"] is False
        assert configured["capabilities"]["supports_command_ack"] is False
        driver = Aigtek1200BInstrument(configured["resource"], serial_number=configured["connection"]["serial_number"])
        assert not driver.connected
        assert all(item.get("device_id") != DEVICE_ID for item in load_mapping_document(tmp_path)["mapping"].values())
        current = resolve_mapping(tmp_path)
        assert current["Z_magnetic_field"]["instrument"] == "signal_generator"
        assert {name: (params / name).read_bytes() for name in before} == before
        saved = save_device_library(document["devices"], base_revision=document["revision"], root=tmp_path)
        assert saved["devices"][DEVICE_ID] == configured
        assert load_device_document(tmp_path)["devices"][DEVICE_ID] == configured
        assert resolve_mapping(tmp_path) == current
        assert (params / "mapping.yaml").read_bytes() == before["mapping.yaml"]
        factory.assert_not_called()
        ports.assert_not_called()


def test_registration_does_not_create_coil_safety_limits():
    limits = load_safety_limits(ROOT)
    assert DEVICE_ID not in limits
    assert "aigtek_1200b_coil_voltage" not in limits


def test_real_validate_safety_limit_blocks_write_and_off_remains_available(fake_amplifier):
    # 临时测试规则只用于验证接口，不是现场线圈安全值。
    limits = {"test_gain": {"min": 0.0, "max": 10.0}}
    checked = []

    def check(operation, value):
        checked.append((operation, value))
        assert "test_gain" in limits
        validate_safety_limit("test_gain", value, limits)

    driver = Aigtek1200BInstrument("COM3", safety_check=check)
    driver.connect()
    with pytest.raises(ValueError, match="安全拦截"):
        driver.set_gain_db(10.5)
    fake_amplifier.write.assert_not_called()
    receipt = driver.set_gain_db(10)
    assert not receipt.execution_confirmed
    assert checked == [("gain_db", 10.5), ("gain_db", 10.0)]
    off = driver.set_output(False)
    assert not off.execution_confirmed
    assert fake_amplifier.write.call_count == 2
    driver.disconnect()


@pytest.mark.parametrize("rule", [None, {}, {"min": None, "max": 1}, {"min": 0, "max": None}])
def test_caller_rejects_missing_real_envelope_before_risky_write(fake_amplifier, rule):
    def check(operation, value):
        if not rule or rule.get("min") is None or rule.get("max") is None:
            raise ValueError("线圈电压安全范围未配置，拒绝带载开启")
        validate_safety_limit("test_coil", 0.5, {"test_coil": rule})

    driver = Aigtek1200BInstrument("COM3", safety_check=check)
    driver.connect()
    with pytest.raises(ValueError, match="未配置"):
        driver.set_output(True)
    fake_amplifier.write.assert_not_called()
    driver.set_output(False)
    assert fake_amplifier.write.call_args.args == (b"ATA,SET,AOF,0000;",)
    driver.disconnect()


@pytest.mark.parametrize("failure", [None, ValueError("调用方故障"), KeyboardInterrupt("取消")])
def test_finally_uses_real_shutdown_to_send_off_then_disconnect(fake_amplifier, failure):
    driver = Aigtek1200BInstrument("COM3")
    driver.connect()
    events = []
    receipts = []
    fake_amplifier.close.side_effect = lambda: events.append("disconnect")

    def send_off():
        receipts.append(driver.set_output(False))
        events.append("off_sent")

    def run():
        try:
            if failure is not None:
                raise failure
        finally:
            report = run_safety_shutdown(
                extra_actions=[ShutdownAction("发送放大器关闭命令", send_off)],
                disconnect_targets=[DisconnectTarget("1200B", driver)],
            )
            assert report.completed
            assert events == ["off_sent", "disconnect"]
            assert receipts[0].execution_confirmed is False

    if failure is None:
        run()
    else:
        with pytest.raises(type(failure)) as caught:
            run()
        assert caught.value is failure
    fake_amplifier.write.assert_called_once_with(b"ATA,SET,AOF,0000;")


@pytest.mark.parametrize("short_write", [True, False])
def test_write_failure_keeps_partial_frame_isolated_during_real_finally_shutdown(fake_amplifier, short_write):
    driver = Aigtek1200BInstrument("COM3", safety_check=lambda operation, value: None)
    driver.connect()
    received = bytearray()
    frames = []

    def receive_prefix(payload):
        accepted = payload[:14] if fake_amplifier.write.call_count == 1 else payload
        received.extend(accepted)
        while b";" in received:
            end = received.index(b";") + 1
            frames.append(bytes(received[:end]))
            del received[:end]
        if not short_write:
            raise OSError("可能写入半帧后掉线")
        return len(accepted)

    fake_amplifier.write.side_effect = receive_prefix
    reports = []
    with pytest.raises(OSError):
        try:
            driver.set_output(True)
        finally:
            reports.append(run_safety_shutdown(
                extra_actions=[ShutdownAction("放大器 OFF", lambda: driver.set_output(False))],
                disconnect_targets=[DisconnectTarget("1200B", driver)],
            ))
    assert not reports[0].completed
    assert "帧同步未知" in reports[0].action_errors[0]
    assert reports[0].disconnect_errors == ()
    fake_amplifier.close.assert_called_once()
    fake_amplifier.write.assert_called_once_with(b"ATA,SET,AON,0000;")
    assert received == b"ATA,SET,AON,00"
    assert frames == []


def test_failed_off_still_runs_other_actions_disconnects_and_keeps_original_error(fake_amplifier):
    driver = Aigtek1200BInstrument("COM3")
    driver.connect()
    fake_amplifier.write.side_effect = OSError("关闭命令写入失败")
    fake_amplifier.close.side_effect = OSError("串口释放失败")
    original = ValueError("实验原始故障")
    other_action = Mock()
    reports = []
    with pytest.raises(ValueError) as caught:
        try:
            raise original
        finally:
            reports.append(run_safety_shutdown(
                extra_actions=[ShutdownAction("放大器 OFF", lambda: driver.set_output(False)),
                               ShutdownAction("其他独立安全动作", other_action)],
                disconnect_targets=[DisconnectTarget("1200B", driver)],
            ))
    assert caught.value is original
    assert not reports[0].completed
    assert "关闭命令写入失败" in reports[0].action_errors[0]
    assert "串口释放失败" in reports[0].disconnect_errors[0]
    other_action.assert_called_once()
    fake_amplifier.close.assert_called_once()
    fake_amplifier.write.assert_called_once_with(b"ATA,SET,AOF,0000;")
