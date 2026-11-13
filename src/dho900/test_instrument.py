"""DHO900 假 VISA 通信测试；不访问网络和真实仪器。"""

from unittest.mock import Mock

import pytest

from hardware_gate import HardwareAccessDenied
from .instrument import DHO900Instrument

RESOURCE = "TCPIP0::192.0.2.21::INSTR"


class FakeVisa:
    """按命令记录状态的小型传输替身，只服务于离线协议回归。"""

    def __init__(self):
        self.writes = []
        self.queries = []
        self.closed = False
        self.identity = "RIGOL TECHNOLOGIES,DHO924S,FAKE0001,00.01.03"
        self.depth = 1000
        self.depth_auto = False
        self.auto_points = 100_000  # AUTO 深度下假仪器自选的点数
        self.scale = 0.1
        self.delay = 0.0
        self.rate_override = None
        self.status = "STOP"
        self.statuses = []
        self.sweep = "AUTO"
        self.source = "CHAN1"
        self.slope = "POS"
        self.format = "ASC"
        self.wave_mode = "RAW"
        self.start = 1
        self.end = 1000
        self.enabled = {1: True, 2: False, 3: False, 4: False}
        self.units = "VOLT"
        self.preamble_override = None
        self.data_override = None
        self.xreference = 0.0
        self.close_error = None
        self.recording = "0"
        self.replay = "STOP"
        self.error = '0,"No error"'

    def write(self, command):
        self.writes.append(command)
        if command == ":RUN":
            self.status = "RUN"
        elif command in {":STOP", ":SINGle", ":TFORce"}:
            self.status = "STOP"
        elif command.startswith(":CHANnel") and ":DISPlay " in command:
            self.enabled[int(command[8])] = command.endswith(" ON")
        elif command.startswith(":ACQuire:MDEPth "):
            self.depth_auto = command.split()[-1] == "AUTO"
            self.depth = self.auto_points if self.depth_auto else int(command.split()[-1])
        elif command.startswith(":TIMebase:MAIN:SCALe "):
            self.scale = float(command.split()[-1])
        elif command.startswith(":TIMebase:MAIN:OFFSet "):
            self.delay = float(command.split()[-1])
        elif command.startswith(":TRIGger:SWEep "):
            self.sweep = command.split()[-1].upper()[:4]
            if self.sweep == "SING":
                self.status = "STOP"
        elif command.startswith(":TRIGger:EDGE:SOURce "):
            self.source = "CHAN" + command[-1]
        elif command.startswith(":TRIGger:EDGE:SLOPe "):
            self.slope = command.split()[-1].upper()[:3]
        elif command.startswith(":WAVeform:FORMat "):
            self.format = command.split()[-1].upper()[:3]
        elif command.startswith(":WAVeform:MODE "):
            self.wave_mode = command.split()[-1].upper()[:4]
        elif command.startswith(":WAVeform:STARt "):
            self.start = int(command.split()[-1])
        elif command.startswith(":WAVeform:STOP "):
            self.end = int(command.split()[-1])

    def query(self, command):
        self.queries.append(command)
        if command == "*IDN?":
            return self.identity
        if command == ":SYSTem:ERRor?":
            return self.error
        if command == ":RECord:WRECord:ENABle?":
            return self.recording
        if command == ":RECord:WREPlay:OPERate?":
            return self.replay
        if command == ":ACQuire:SRATe?":
            return str(self.rate_override if self.rate_override is not None else self.depth / (self.scale * 10))
        if command == ":ACQuire:MDEPth?":
            return "AUTO" if self.depth_auto else str(self.depth)
        if command == ":TIMebase:MAIN:SCALe?":
            return str(self.scale)
        if command == ":TIMebase:MAIN:OFFSet?":
            return str(self.delay)
        if command == ":TIMebase:MODE?":
            return "MAIN"
        if command == ":TIMebase:ROLL?":
            return "0"
        if command == ":TRIGger:STATus?":
            return self.statuses.pop(0) if self.statuses else self.status
        if command == ":TRIGger:SWEep?":
            return self.sweep
        if command == ":TRIGger:MODE?":
            return "EDGE"
        if command == ":TRIGger:EDGE:SOURce?":
            return self.source
        if command == ":TRIGger:EDGE:SLOPe?":
            return self.slope
        if command == ":TRIGger:EDGE:LEVel?":
            return "0.125"
        if command.startswith(":CHANnel"):
            number = int(command[8])
            if command.endswith(":DISPlay?"):
                return str(int(self.enabled[number]))
            if command.endswith(":UNITs?"):
                return self.units
            if command.endswith(":COUPling?"):
                return "DC"
            if command.endswith(":SCALe?"):
                return "0.5"
            if command.endswith(":OFFSet?"):
                return "-0.1"
            if command.endswith(":PROBe?"):
                return "10"
        if command == ":WAVeform:FORMat?":
            return self.format
        if command == ":WAVeform:PREamble?":
            if self.preamble_override is not None:
                return self.preamble_override
            increment = self.scale * 10 / self.depth
            return f"2,2,{self.depth},1,{increment},{-5 * self.scale},{self.xreference},0.004,7,128"
        if command == ":WAVeform:DATA?":
            if self.data_override is not None:
                return self.data_override
            return ",".join(str((index - 1) * 0.01 - 2) for index in range(self.start, self.end + 1))
        raise AssertionError(f"未预期的假仪器查询: {command}")

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


@pytest.fixture
def connected(monkeypatch):
    visa = FakeVisa()
    manager = Mock()
    manager.open_resource.return_value = visa
    monkeypatch.setattr("pyvisa.ResourceManager", Mock(return_value=manager))
    monkeypatch.setattr("hardware_gate.denial_reason", lambda: None)
    instrument = DHO900Instrument(RESOURCE, read_chunk_points=127)
    instrument.connect()
    yield instrument, visa, manager
    instrument.disconnect()


def test_constructor_does_not_open_transport_and_gate_blocks_lazy_query(monkeypatch):
    manager = Mock()
    monkeypatch.setattr("pyvisa.ResourceManager", manager)
    instrument = DHO900Instrument(RESOURCE)
    manager.assert_not_called()
    monkeypatch.setattr("hardware_gate.denial_reason", lambda: "离线测试拒绝")
    with pytest.raises(HardwareAccessDenied, match="离线测试拒绝"):
        instrument.idn()
    manager.assert_not_called()


def test_connect_only_identifies_and_disconnect_keeps_shared_manager(connected):
    instrument, visa, manager = connected
    manager.open_resource.assert_called_once_with(RESOURCE, open_timeout=10000)
    assert visa.queries == ["*IDN?"]
    assert visa.writes == []
    instrument.connect()
    assert manager.open_resource.call_count == 1
    instrument.disconnect()
    assert visa.closed
    manager.close.assert_not_called()


@pytest.mark.parametrize("identity", ["RIGOL,DG912,FAKE,1", "OTHER,DHO924,FAKE,1", "NOT RIGOL,DHO924,FAKE,1", "bad"])
def test_wrong_identity_closes_session_without_configuration(monkeypatch, identity):
    visa = FakeVisa()
    visa.identity = identity
    visa.close_error = RuntimeError("清理失败不覆盖身份错误")
    manager = Mock(open_resource=Mock(return_value=visa))
    monkeypatch.setattr("pyvisa.ResourceManager", Mock(return_value=manager))
    monkeypatch.setattr("hardware_gate.denial_reason", lambda: None)
    instrument = DHO900Instrument(RESOURCE)
    with pytest.raises(ValueError, match="身份"):
        instrument.connect()
    assert visa.closed and instrument._inst is None
    assert visa.writes == []


def test_same_name_controls_translate_to_rigol_commands(connected):
    instrument, visa, _ = connected
    instrument.set_channel_state(2, True)
    instrument.set_channel_scale(2, 0.5)
    instrument.set_channel_offset(2, -0.1)
    instrument.set_channel_coupling(2, "AC")
    instrument.set_channel_probe(2, 10)
    instrument.set_trigger_type("EDGE")
    instrument.set_trigger_source("C2")
    instrument.set_trigger_slope("RISing")
    instrument.set_trigger_mode("SINGle")
    instrument.set_trigger_level(0.125)
    assert visa.writes == [":CHANnel2:DISPlay ON", ":CHANnel2:SCALe 0.5", ":CHANnel2:OFFSet -0.1",
                           ":CHANnel2:COUPling AC", ":CHANnel2:PROBe 10", ":TRIGger:MODE EDGE",
                           ":TRIGger:EDGE:SOURce CHANnel2", ":TRIGger:EDGE:SLOPe POSitive",
                           ":TRIGger:SWEep SINGle", ":TRIGger:EDGE:LEVel 0.125"]
    assert instrument.get_trigger_source() == "C2"
    assert instrument.get_trigger_slope() == "RISing"
    assert instrument.get_trigger_mode() == "SINGle"
    assert instrument.get_trigger_level() == 0.125


@pytest.mark.parametrize("method,args,error", [
    ("set_channel_state", (0, True), ValueError),
    ("set_channel_scale", (1, float("nan")), ValueError),
    ("set_trigger_source", ("EXT",), ValueError),
    ("set_trigger_source", ("C1;:RUN",), ValueError),
    ("set_channel_probe", (1, 3), ValueError),
    ("set_channel_impedance", (1, "FIFTy"), NotImplementedError),
    ("set_memory_management", ("FSRate",), NotImplementedError),
    ("set_trigger_mode", ("FTRIG",), NotImplementedError),
    ("set_acquire_type", ("ERES", 12), ValueError),
    ("set_acquire_type", ("AVERage", 3), ValueError),
    ("set_waveform_width", ("WORD",), NotImplementedError),
])
def test_unsupported_or_invalid_settings_write_nothing(connected, method, args, error):
    instrument, visa, _ = connected
    with pytest.raises(error):
        getattr(instrument, method)(*args)
    assert visa.writes == []


def test_sampling_rate_is_verified_not_written(connected):
    instrument, visa, _ = connected
    instrument.set_sampling_rate(1000)
    with pytest.raises(ValueError, match="目标采样率 500.*实际 1000"):
        instrument.set_sampling_rate(500)
    assert visa.writes == []


def test_waveform_zero_based_offsets_map_to_one_based_closed_windows(connected):
    instrument, visa, _ = connected
    instrument.set_waveform_start(10)
    instrument.set_waveform_points(20)
    assert visa.writes == [":WAVeform:STARt 11", ":WAVeform:STOP 30"]
    assert instrument.get_waveform_max_points() == 127


@pytest.mark.parametrize("status,expected", [("TD", "Trig'd"), ("STOP", "Stop"),
                                             ("WAIT", "Ready"), ("RUN", "Arm"), ("AUTO", "Auto")])
def test_trigger_status_mapping(connected, status, expected):
    instrument, visa, _ = connected
    visa.status = status
    assert instrument.trigger_status() == expected


def test_single_wait_accepts_completed_stop_but_auto_stop_is_not_triggered(connected):
    instrument, visa, _ = connected
    visa.sweep = "SING"
    assert not instrument.wait_for_trigger()  # 已有 STOP 但本驱动未布防，不冒充新触发。
    instrument.set_trigger_mode("SINGle")
    visa.statuses = ["WAIT", "STOP"]
    assert instrument.wait_for_trigger(poll_interval=0.001)
    assert not instrument.wait_for_trigger()
    instrument.set_trigger_mode("SINGle")
    instrument.trigger_stop()
    assert not instrument.wait_for_trigger()  # 主动 STOP 不表示单次触发完成。
    instrument.set_trigger_mode("SINGle")
    visa.statuses = ["TD", "STOP"]
    assert instrument.wait_for_trigger()
    assert not instrument.wait_for_trigger()
    instrument.set_trigger_mode("AUTO")
    assert not instrument.wait_for_trigger()
    visa.status = "TD"
    assert instrument.wait_for_trigger()


def test_unknown_trigger_status_and_nonfinite_readback_are_rejected(connected):
    instrument, visa, _ = connected
    visa.status = "UNKNOWN"
    with pytest.raises(ValueError, match="触发状态回读"):
        instrument.trigger_status()
    visa.rate_override = float("nan")
    with pytest.raises(ValueError, match="有限"):
        instrument.get_sampling_rate()
