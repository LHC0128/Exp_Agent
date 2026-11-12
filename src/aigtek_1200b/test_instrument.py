"""1200B 无硬件回归：假串口、假端口枚举及虚拟时钟。"""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import serial

from hardware_gate import HardwareAccessDenied
from . import Aigtek1200BInstrument, AmplifierCommandError, CommandReceipt
from . import instrument as module


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class FakeSerial:
    def __init__(self, clock, **kwargs):
        self.clock = clock
        self.options = kwargs
        self.timeout = kwargs["timeout"]
        self.is_open = False
        self.writes = []
        self.responses = []
        self.open_calls = 0
        self.close_calls = 0
        self.short_write = None
        self.write_error = None
        self.read_error = None
        self.open_error = None
        self.close_error = None

    def open(self):
        self.open_calls += 1
        assert self.port == "COM3"
        assert self.dtr is False and self.rts is False
        if self.open_error:
            raise self.open_error
        self.is_open = True

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error
        self.is_open = False

    @property
    def in_waiting(self):
        return len(self.responses[0]) if self.responses else 0

    def write(self, payload):
        self.writes.append(payload)
        if self.write_error:
            raise self.write_error
        return self.short_write if self.short_write is not None else len(payload)

    def read(self, size):
        if self.read_error:
            raise self.read_error
        self.clock.now += self.timeout
        if not self.responses:
            return b""
        chunk = self.responses.pop(0)
        if len(chunk) > size:
            self.responses.insert(0, chunk[size:])
        return chunk[:size]


def cp210x(device="COM3", **changes):
    return SimpleNamespace(device=device, vid=0x10C4, pid=0xEA60, serial_number="0001", **changes)


@pytest.fixture
def rig(monkeypatch):
    clock = FakeClock()
    transport = FakeSerial(clock, timeout=0.1)
    factory = Mock(return_value=transport)
    ports = Mock(return_value=[cp210x()])
    gate = Mock()
    check = Mock()
    monkeypatch.setattr(module, "require_hardware_access", gate)
    monkeypatch.setattr(module.serial, "Serial", factory)
    monkeypatch.setattr(module.list_ports, "comports", ports)
    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    driver = Aigtek1200BInstrument("COM3", safety_check=check)
    return SimpleNamespace(driver=driver, clock=clock, transport=transport,
                           factory=factory, ports=ports, gate=gate, check=check)


def test_construct_is_offline_and_connect_does_not_configure_or_enable(rig):
    rig.factory.assert_not_called()
    rig.ports.assert_not_called()
    rig.gate.assert_not_called()
    rig.driver.connect()
    rig.factory.assert_called_once_with(
        port=None, baudrate=9600, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=0.1, write_timeout=1.0,
        xonxoff=False, rtscts=False, dsrdtr=False,
    )
    assert rig.driver.connected
    assert rig.transport.writes == []
    rig.driver.connect()
    assert rig.transport.open_calls == 1
    rig.driver.disconnect()
    rig.driver.disconnect()
    assert rig.transport.close_calls == 1
    assert not rig.driver.connected
    assert rig.transport.writes == []


def test_gate_runs_before_port_enumeration_or_serial_factory(rig):
    rig.gate.side_effect = HardwareAccessDenied("开发目录禁止")
    with pytest.raises(HardwareAccessDenied, match="开发目录禁止"):
        rig.driver.connect()
    rig.ports.assert_not_called()
    rig.factory.assert_not_called()
    assert not rig.driver.connected


@pytest.mark.parametrize("entries", [
    [], [cp210x("COM4")], [cp210x(), cp210x("COM4")],
    [SimpleNamespace(device="COM3", vid=1, pid=0xEA60, serial_number="0001")],
    [SimpleNamespace(device="COM3", vid=0x10C4, pid=1, serial_number="0001")],
    [SimpleNamespace(device="COM3", vid=0x10C4, pid=0xEA60, serial_number="0002")],
])
def test_wrong_missing_or_ambiguous_adapter_never_opens_or_falls_back(rig, entries):
    rig.ports.return_value = entries
    with pytest.raises(RuntimeError, match="不自动换端口"):
        rig.driver.connect()
    rig.factory.assert_not_called()
    assert rig.transport.writes == []


def test_port_enumeration_failure_propagates_without_open(rig):
    error = OSError("枚举失败")
    rig.ports.side_effect = error
    with pytest.raises(OSError) as caught:
        rig.driver.connect()
    assert caught.value is error
    rig.factory.assert_not_called()


def test_open_failure_closes_partial_transport_and_preserves_original_error(rig, caplog):
    original = serial.SerialException("端口占用")
    rig.transport.open_error = original
    rig.transport.close_error = OSError("清理失败")
    with pytest.raises(serial.SerialException) as caught:
        rig.driver.connect()
    assert caught.value is original
    assert rig.transport.close_calls == 1
    assert not rig.driver.connected
    assert rig.transport.writes == []
    assert "释放失败" in caplog.text


@pytest.mark.parametrize("gain", [index / 2 for index in range(41)])
def test_every_half_db_value_has_exact_encoding(rig, gain):
    rig.driver.connect()
    receipt = rig.driver.set_gain_db(gain)
    expected = f"ATA,SET,AMP,{int(gain * 10):04d};"
    assert rig.transport.writes == [expected.encode("ascii")]
    assert receipt == CommandReceipt(expected, b"")
    assert receipt.execution_confirmed is False
    rig.check.assert_called_once_with("gain_db", gain)


@pytest.mark.parametrize("method,value,payload,operation", [
    ("set_output", False, b"ATA,SET,AOF,0000;", "output"),
    ("set_output", True, b"ATA,SET,AON,0000;", "output"),
    ("set_input_impedance", 50, b"ATA,SET,RIN,0050;", "input_impedance_ohms"),
    ("set_input_impedance", 1_000_000, b"ATA,SET,RIN,1M00;", "input_impedance_ohms"),
    ("set_output_impedance", 1, b"ATA,SET,ROT,0001;", "output_impedance_ohms"),
    ("set_output_impedance", 50, b"ATA,SET,ROT,0050;", "output_impedance_ohms"),
])
def test_exact_switch_and_impedance_commands(rig, method, value, payload, operation):
    rig.driver.connect()
    receipt = getattr(rig.driver, method)(value)
    assert rig.transport.writes == [payload]
    assert receipt.command.encode("ascii") == payload
    assert receipt.execution_confirmed is False
    if value is False:
        rig.check.assert_not_called()
    else:
        rig.check.assert_called_once_with(operation, value)


def test_save_is_explicit_and_never_automatic(rig):
    with rig.driver:
        rig.driver.set_output(False)
        assert not any(b"SAV" in payload for payload in rig.transport.writes)
        receipt = rig.driver.save_settings()
    assert receipt.command == "ATA,SET,SAV,0000;"
    assert rig.transport.writes == [b"ATA,SET,AOF,0000;", b"ATA,SET,SAV,0000;"]
    rig.check.assert_called_once_with("save_settings", True)


@pytest.mark.parametrize("value", [-0.5, 20.5, 0.1, 1.25, float("nan"),
                                   float("inf"), -float("inf"), True, None, "bad"])
def test_invalid_gain_never_writes(rig, value):
    rig.driver.connect()
    with pytest.raises(ValueError):
        rig.driver.set_gain_db(value)
    assert rig.transport.writes == []
    rig.check.assert_not_called()


@pytest.mark.parametrize("method,value", [
    ("set_input_impedance", 1), ("set_input_impedance", "1M00"),
    ("set_input_impedance", float("nan")), ("set_input_impedance", False),
    ("set_output_impedance", 1_000_000), ("set_output_impedance", float("inf")),
    ("set_output_impedance", True), ("set_output", 0), ("set_output", 1),
    ("set_output", "OFF"), ("set_output", None),
])
def test_invalid_impedance_and_switch_never_write(rig, method, value):
    rig.driver.connect()
    with pytest.raises(ValueError):
        getattr(rig.driver, method)(value)
    assert rig.transport.writes == []
    rig.check.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"port": " "}, {"serial_number": ""}, {"response_timeout": 0},
    {"response_timeout": float("nan")}, {"write_timeout": float("inf")},
    {"write_timeout": -1}, {"response_timeout": False}, {"safety_check": True},
])
def test_invalid_constructor_arguments_are_offline(rig, kwargs):
    with pytest.raises(ValueError):
        Aigtek1200BInstrument(**{"port": "COM3", **kwargs})
    rig.ports.assert_not_called()
    rig.factory.assert_not_called()


@pytest.mark.parametrize("method,args", [
    ("set_gain_db", (10,)), ("set_input_impedance", (50,)),
    ("set_output_impedance", (1,)), ("set_output", (True,)), ("save_settings", ()),
])
def test_missing_safety_check_denies_risky_commands_but_allows_off(rig, method, args):
    driver = Aigtek1200BInstrument("COM3")
    driver.connect()
    with pytest.raises(RuntimeError, match="安全校验"):
        getattr(driver, method)(*args)
    assert rig.transport.writes == []
    assert driver.set_output(False).execution_confirmed is False
    assert rig.transport.writes == [b"ATA,SET,AOF,0000;"]


def test_safety_failure_is_propagated_before_write_and_does_not_block_off(rig):
    rig.driver.connect()
    error = ValueError("[安全拦截] 线圈包络越界")
    rig.check.side_effect = error
    with pytest.raises(ValueError) as caught:
        rig.driver.set_gain_db(10)
    assert caught.value is error
    assert rig.transport.writes == []
    rig.driver.set_output(False)
    assert rig.transport.writes == [b"ATA,SET,AOF,0000;"]


def test_not_connected_rejects_without_automatic_connection(rig):
    with pytest.raises(RuntimeError, match="尚未连接"):
        rig.driver.set_output(False)
    rig.factory.assert_not_called()
    rig.check.assert_not_called()


@pytest.mark.parametrize("written", [0, 1, 14])
def test_short_write_stops_without_resend(rig, written):
    rig.driver.connect()
    rig.transport.short_write = written
    with pytest.raises(OSError, match="未完整写入"):
        rig.driver.set_output(False)
    assert rig.transport.writes == [b"ATA,SET,AOF,0000;"]
    assert rig.clock.now == 0
    with pytest.raises(RuntimeError, match="帧同步未知"):
        rig.driver.set_output(False)
    assert rig.transport.writes == [b"ATA,SET,AOF,0000;"]


@pytest.mark.parametrize("attribute,error", [
    ("write_error", serial.SerialTimeoutException("写超时")),
    ("write_error", OSError("USB 掉线")),
    ("read_error", OSError("读取失败，设备执行结果未知")),
])
def test_transport_errors_propagate_without_retry_or_port_change(rig, attribute, error):
    rig.driver.connect()
    setattr(rig.transport, attribute, error)
    with pytest.raises(type(error)) as caught:
        rig.driver.set_output(False)
    assert caught.value is error
    assert rig.transport.writes == [b"ATA,SET,AOF,0000;"]
    assert rig.ports.call_count == 1
    assert rig.factory.call_count == 1


@pytest.mark.parametrize("failure", ["short_write", "timeout", "interrupt"])
def test_failed_write_latches_across_reconnect_without_emitting_followup_off(rig, failure):
    rig.driver.connect()
    if failure == "short_write":
        rig.transport.short_write = 14
        error_type = OSError
    elif failure == "timeout":
        rig.transport.write_error = serial.SerialTimeoutException("写入部分字节后超时")
        error_type = serial.SerialTimeoutException
    else:
        rig.transport.write_error = KeyboardInterrupt("写入期间取消")
        error_type = KeyboardInterrupt
    with pytest.raises(error_type):
        rig.driver.set_output(True)
    rig.transport.short_write = None
    rig.transport.write_error = None
    with pytest.raises(RuntimeError, match="帧同步未知"):
        rig.driver.set_output(False)
    rig.driver.disconnect()
    rig.driver.connect()
    with pytest.raises(RuntimeError, match="重连不能确认恢复"):
        rig.driver.set_output(False)
    assert rig.transport.writes == [b"ATA,SET,AON,0000;"]
    assert rig.transport.open_calls == 2
    rig.driver.disconnect()


def test_empty_response_and_read_error_do_not_poison_complete_write_stream(rig):
    rig.driver.connect()
    assert rig.driver.set_output(True).response == b""
    rig.transport.read_error = OSError("读取失败")
    with pytest.raises(OSError, match="读取失败"):
        rig.driver.set_gain_db(10)
    rig.transport.read_error = None
    receipt = rig.driver.set_output(False)
    assert not receipt.execution_confirmed
    assert rig.transport.writes == [b"ATA,SET,AON,0000;", b"ATA,SET,AMP,0100;", b"ATA,SET,AOF,0000;"]


def test_silent_device_returns_unconfirmed_receipt_after_bounded_collection(rig):
    rig.driver.connect()
    receipt = rig.driver.set_output(False)
    assert receipt == CommandReceipt("ATA,SET,AOF,0000;", b"")
    assert receipt.execution_confirmed is False
    assert rig.clock.now == pytest.approx(3.0)
    with pytest.raises(FrozenInstanceError):
        receipt.execution_confirmed = True
    assert not hasattr(rig.driver, "get_output")
    assert not hasattr(rig.driver, "get_gain_db")
    assert not hasattr(rig.driver, "output_enabled")


def test_unknown_or_non_ascii_response_does_not_become_an_ack(rig):
    rig.driver.connect()
    rig.transport.responses = [b"OK", b"\xff\x00"]
    receipt = rig.driver.set_output(False)
    assert receipt.response == b"OK\xff\x00"
    assert not receipt.execution_confirmed


def test_fragmented_over_range_error_preserves_raw_bytes_and_does_not_retry(rig):
    rig.driver.connect()
    rig.transport.responses = [b"OVER ", b"RANGE ", b"ERR!"]
    with pytest.raises(AmplifierCommandError, match="OVER RANGE") as caught:
        rig.driver.set_gain_db(10)
    assert caught.value.receipt.response == b"OVER RANGE ERR!"
    assert caught.value.receipt.command == "ATA,SET,AMP,0100;"
    assert not caught.value.receipt.execution_confirmed
    assert len(rig.transport.writes) == 1


def test_response_limit_fails_closed_instead_of_collecting_forever(rig):
    rig.driver.connect()
    rig.transport.responses = [b"x" * 5000]
    with pytest.raises(AmplifierCommandError, match="收集上限") as caught:
        rig.driver.set_output(False)
    assert len(caught.value.receipt.response) == 4096
    assert not caught.value.receipt.execution_confirmed
    assert len(rig.transport.writes) == 1


def test_short_response_budget_also_limits_each_read(rig):
    driver = Aigtek1200BInstrument("COM3", response_timeout=0.02)
    driver.connect()
    assert driver.set_output(False).response == b""
    assert rig.clock.now == pytest.approx(0.02)
    assert rig.factory.call_args.kwargs["timeout"] == 0.02


def test_reconnect_does_not_restore_or_invent_output_state(rig):
    rig.driver.connect()
    rig.driver.set_output(True)
    rig.driver.disconnect()
    rig.driver.connect()
    assert rig.transport.writes == [b"ATA,SET,AON,0000;"]
    assert rig.transport.open_calls == 2
    assert not hasattr(rig.driver, "output_enabled")


def test_context_exit_only_closes_and_preserves_original_exception(rig, caplog):
    original = ValueError("调用方故障")
    rig.transport.close_error = OSError("关闭串口失败")
    with pytest.raises(ValueError) as caught:
        with rig.driver:
            raise original
    assert caught.value is original
    assert rig.transport.writes == []
    assert rig.transport.close_calls == 1
    assert "保留原始异常" in caplog.text


def test_disconnect_failure_is_reported_and_can_be_retried_explicitly(rig):
    rig.driver.connect()
    rig.transport.close_error = OSError("串口释放失败")
    with pytest.raises(OSError, match="串口释放失败"):
        rig.driver.disconnect()
    assert rig.driver.connected
    rig.transport.close_error = None
    rig.driver.disconnect()
    assert not rig.driver.connected
    assert rig.transport.writes == []
