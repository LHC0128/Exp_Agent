"""DHO900 假仪器采集测试：严格率、坐标、分块、取消和停止恢复。"""

from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from sds_acquisition.config import AcquisitionConfig, ChannelConfig, TriggerConfig
from .acquire import DHO900Acquisition, _Preamble
from .instrument import DHO900Instrument
from .test_instrument import FakeVisa, RESOURCE


@pytest.fixture
def rig(monkeypatch):
    visa = FakeVisa()
    manager = Mock(open_resource=Mock(return_value=visa))
    monkeypatch.setattr("pyvisa.ResourceManager", Mock(return_value=manager))
    monkeypatch.setattr("hardware_gate.denial_reason", lambda: None)
    instrument = DHO900Instrument(RESOURCE, read_chunk_points=127)
    instrument.connect()
    acquirer = DHO900Acquisition(instrument)
    # 虚拟化等待，仍在每次等待时检查取消；不改变生产默认等待时间。
    monkeypatch.setattr(acquirer, "_wait", lambda seconds: acquirer._check_cancelled())
    yield instrument, visa, acquirer
    instrument.disconnect()


def configuration(**changes):
    return replace(AcquisitionConfig(sampling_rate=1000, sampling_time=1,
                                    channels=[ChannelConfig(1)], acquire_delay=0), **changes)


def test_apply_config_coordinates_depth_timebase_and_reads_real_rate(rig):
    instrument, visa, acquirer = rig
    config = configuration()
    acquirer.apply_config(config)
    assert acquirer._snapshot["actual_sample_rate_sa_s"] == 1000
    assert acquirer._snapshot["requested"] == config.to_dict()
    assert acquirer._snapshot["sample_rate_policy"] == "strict"
    assert visa.status == "STOP"
    assert visa.depth == 1000
    assert visa.enabled == {1: True, 2: False, 3: False, 4: False}
    assert ":ACQuire:MDEPth 1000" in visa.writes
    assert not any("SRATe " in command or "MMAN" in command for command in visa.writes)
    assert ":TIMebase:ROLL 0" in visa.writes
    assert ":TIMebase:MODE?" in visa.queries and ":TIMebase:ROLL?" in visa.queries


@pytest.mark.parametrize("config,error", [
    (configuration(sampling_rate=500000, sampling_time=1), ValueError),
    (configuration(memory_management="FSRate"), NotImplementedError),
    (configuration(channels=[ChannelConfig(1, impedance="FIFTy")]), NotImplementedError),
    (configuration(channels=[ChannelConfig(1), ChannelConfig(1)]), ValueError),
    (configuration(channels=[]), ValueError),
    (configuration(trigger=TriggerConfig(source="EXT")), ValueError),
    (configuration(trigger=TriggerConfig(mode="FTRIG")), NotImplementedError),
    (configuration(acquire_type="ERES"), ValueError),
    (configuration(sampling_time=-1), ValueError),
    (configuration(sampling_rate=50_000_000, channels=[ChannelConfig(1), ChannelConfig(2)]), ValueError),
])
def test_invalid_request_is_rejected_before_any_write(rig, config, error):
    _, visa, acquirer = rig
    with pytest.raises(error):
        acquirer.apply_config(config)
    assert visa.writes == []


def test_integral_point_product_tolerates_only_float_roundoff(rig):
    _, visa, acquirer = rig
    acquirer.apply_config(configuration(sampling_rate=7000, sampling_time=1 / 7))
    assert visa.depth == 1000
    assert acquirer._snapshot["actual_points"] == 1000
    with pytest.raises(ValueError, match="整数点数"):
        acquirer.apply_config(configuration(sampling_rate=1000.25))


@pytest.mark.parametrize("recording,replay", [("1", "STOP"), ("0", "RUN"), ("UNKNOWN", "STOP")])
def test_recording_or_replay_is_rejected_before_configuration_or_arming(rig, recording, replay):
    _, visa, acquirer = rig
    visa.recording, visa.replay = recording, replay
    with pytest.raises(ValueError, match="录制或回放"):
        acquirer.apply_config(configuration())
    assert visa.writes == []
    visa.recording, visa.replay = "0", "STOP"
    acquirer.apply_config(configuration())
    visa.recording, visa.replay = recording, replay
    before = list(visa.writes)
    with pytest.raises(ValueError, match="录制或回放"):
        acquirer.start_acquisition(configuration())
    assert visa.writes == before
    assert ":WAVeform:DATA?" not in visa.queries


def test_rate_mismatch_stops_and_reports_requested_and_actual(rig):
    _, visa, acquirer = rig
    visa.rate_override = 900
    with pytest.raises(ValueError, match="目标采样率 1000.*实际 900"):
        acquirer.apply_config(configuration())
    assert visa.writes[-1] == ":STOP"
    assert not acquirer._configured


def test_auto_depth_uses_scope_choice_and_records_actual_rate(rig):
    _, visa, acquirer = rig
    visa.auto_points = 10_000
    acquirer.use_auto_depth(100_000)
    results = acquirer.acquire_all(configuration())
    assert ":ACQuire:MDEPth AUTO" in visa.writes
    snapshot = results[0].config_snapshot
    assert snapshot["sample_rate_policy"] == "auto_depth" and snapshot["depth_mode"] == "AUTO"
    assert snapshot["actual_points"] == 10_000 and snapshot["actual_sample_rate_sa_s"] == 10_000
    assert len(results[0].voltage) == 10_000


def test_auto_depth_above_cap_falls_back_to_cap_depth(rig):
    _, visa, acquirer = rig
    visa.auto_points = 100_000
    acquirer.use_auto_depth(10_000)
    acquirer.apply_config(configuration())
    assert visa.writes.count(":ACQuire:MDEPth AUTO") == 1 and ":ACQuire:MDEPth 10000" in visa.writes
    assert acquirer._snapshot["depth_mode"] == "capped" and acquirer._snapshot["actual_points"] == 10_000


def test_auto_depth_rejects_cap_outside_depth_table_before_any_write(rig):
    _, visa, acquirer = rig
    acquirer.use_auto_depth(12_345)
    with pytest.raises(ValueError, match="最大存储深度"):
        acquirer.apply_config(configuration())
    assert visa.writes == []


def test_auto_depth_rejects_non_integer_point_readback(rig):
    _, visa, acquirer = rig
    visa.rate_override = 1234.5
    acquirer.use_auto_depth(100_000)
    with pytest.raises(ValueError, match="不是整数点数"):
        acquirer.apply_config(configuration())


def test_ascii_waveform_preserves_voltage_and_real_time_axis_across_chunks(rig):
    _, visa, acquirer = rig
    visa.depth = 300
    visa.xreference = 2
    result = acquirer.acquire_channel(1, timebase_scale=999)
    assert result.channel == 1
    assert result.preamble_dict["point_num"] == 300
    assert result.preamble_dict["format"] == 2
    assert result.raw_data.size == 0
    np.testing.assert_allclose(result.voltage, np.arange(300) * 0.01 - 2)
    expected_time = -0.5 + (np.arange(300) - 2) / 300
    np.testing.assert_allclose(result.time, expected_time)
    assert ":WAVeform:STOP 127" in visa.writes
    assert ":WAVeform:STARt 128" in visa.writes
    assert ":WAVeform:STOP 254" in visa.writes
    assert ":WAVeform:STARt 255" in visa.writes
    assert visa.queries.count(":WAVeform:DATA?") == 3
    assert ":WAVeform:POINts 300" in visa.writes
    assert not any("BYTeorder" in command or "WIDTh" in command for command in visa.writes)


def test_raw_read_requires_stopped_enabled_voltage_channel(rig):
    _, visa, acquirer = rig
    visa.status = "RUN"
    with pytest.raises(ValueError, match="STOP"):
        acquirer.acquire_channel(1, 0.1)
    assert visa.writes == []
    visa.status = "STOP"
    with pytest.raises(ValueError, match="未启用"):
        acquirer.acquire_channel(2, 0.1)
    visa.units = "AMP"
    with pytest.raises(ValueError, match="VOLT"):
        acquirer.acquire_channel(1, 0.1)
    assert visa.writes == []


@pytest.mark.parametrize("response", ["2,2,1000", "2,2,0,1,1,-1,0,1,0,0",
                                      "2,2,1000,1,nan,0,0,1,0,0", "2,2,1000,1,-1,0,0,1,0,0"])
def test_preamble_invalid_metadata_is_rejected(response):
    with pytest.raises(ValueError):
        _Preamble.parse(response)


@pytest.mark.parametrize("payload", ["1,2", ",".join(["nan"] * 127), ",".join(["bad"] * 127)])
def test_truncated_or_corrupt_chunks_never_produce_partial_success(rig, payload):
    _, visa, acquirer = rig
    visa.data_override = payload
    with pytest.raises(ValueError):
        acquirer.acquire_channel(1, 0.1)


def test_scpi_failure_during_arming_stops_without_exporting_old_frame(rig):
    _, visa, acquirer = rig
    acquirer.apply_config(configuration())
    original = visa.write
    def rejected(command):
        original(command)
        if command == ":TRIGger:SWEep SINGle":
            visa.error = '-221,"Settings conflict"'
    visa.write = rejected
    with pytest.raises(RuntimeError, match="Settings conflict"):
        acquirer.start_acquisition(configuration())
    assert ":WAVeform:DATA?" not in visa.queries
    assert visa.writes[-1] == ":STOP"


def test_roll_readback_rejects_ignored_disable_command(rig):
    _, visa, acquirer = rig
    original = visa.query
    visa.query = lambda command: "1" if command == ":TIMebase:ROLL?" else original(command)
    with pytest.raises(ValueError, match="非 ROLL"):
        acquirer.apply_config(configuration())
    assert visa.writes[-1] == ":STOP"


def test_local_preamble_window_is_not_treated_as_complete_frame(rig):
    _, visa, acquirer = rig
    visa.preamble_override = "2,2,127,1,0.001,-0.5,0,1,0,128"
    with pytest.raises(ValueError, match="整帧存储深度不符"):
        acquirer.acquire_channel(1, 0.1)
    assert ":WAVeform:DATA?" not in visa.queries


def test_actual_frame_rate_is_checked_not_only_configuration_readback(rig):
    _, visa, acquirer = rig
    acquirer.apply_config(configuration())
    visa.preamble_override = "2,2,1000,1,0.002,-0.5,0,1,0,128"
    with pytest.raises(ValueError, match="严格采样请求不符"):
        acquirer.acquire_channel(1, 0.1)
    assert ":WAVeform:DATA?" not in visa.queries


def test_read_cancellation_propagates_between_chunks(rig):
    _, visa, acquirer = rig
    def cancelled():
        if ":WAVeform:DATA?" in visa.queries:
            raise InterruptedError("取消")
    acquirer._check_cancelled = cancelled
    with pytest.raises(InterruptedError, match="取消"):
        acquirer.acquire_channel(1, 0.1)
    assert visa.queries.count(":WAVeform:DATA?") == 1


@pytest.mark.parametrize("mode", ["AUTO", "SINGle", "NORMal"])
def test_acquire_all_has_same_result_contract_and_stops_in_each_mode(rig, mode):
    _, visa, acquirer = rig
    if mode == "NORMal":
        original = visa.query
        visa.query = lambda command: "TD" if command == ":TRIGger:STATus?" and visa.status == "RUN" else original(command)
    results = acquirer.acquire_all(configuration(trigger=TriggerConfig(mode=mode)))
    assert len(results) == 1
    assert len(results[0].voltage) == 1000
    assert results[0].config_snapshot["requested"]["trigger"]["mode"] == mode
    assert visa.writes[-1] == ":STOP"
    assert visa.status == "STOP"
    assert visa.writes.count(":TRIGger:SWEep SINGle") == (2 if mode == "SINGle" else 1)
    if mode == "AUTO":
        assert ":TFORce" in visa.writes
    else:
        assert ":TFORce" not in visa.writes


def test_two_channels_read_same_stopped_frame_without_rearming_between_reads(rig):
    _, visa, acquirer = rig
    results = acquirer.acquire_all(configuration(channels=[ChannelConfig(1), ChannelConfig(2)]))
    assert [result.channel for result in results] == [1, 2]
    assert visa.writes.count(":WAVeform:SOURce CHANnel1") == 1
    assert visa.writes.count(":WAVeform:SOURce CHANnel2") == 1
    reading = visa.writes[visa.writes.index(":WAVeform:SOURce CHANnel1"):]
    assert not any(command in {":RUN", ":SINGle", ":TFORce"} or command.startswith(":TRIGger:SWEep")
                   for command in reading)
    np.testing.assert_array_equal(results[0].time, results[1].time)


@pytest.mark.parametrize("save_mode", ["voltage_time", "all"])
def test_capture_to_unified_npz_roundtrip_keeps_real_coordinates_and_snapshot(rig, tmp_path, save_mode):
    from oscilloscope import save_to_npz, load_npz
    _, _, acquirer = rig
    config = configuration()
    captured = acquirer.acquire_all(config)
    path = save_to_npz(str(tmp_path / "waveform.npz"), captured, config.to_dict(), save_mode=save_mode)
    restored, metadata = load_npz(path)
    np.testing.assert_array_equal(restored[0].time, captured[0].time)
    np.testing.assert_array_equal(restored[0].voltage, captured[0].voltage)
    assert restored[0].raw_data.size == 0
    snapshot = metadata["scope_acquisition_snapshots"]["ch1"]
    assert snapshot["actual_sample_rate_sa_s"] == 1000
    assert snapshot["actual_points"] == 1000
    assert snapshot["requested"] == config.to_dict()


def test_acquire_all_failure_keeps_original_exception_and_stops(rig):
    _, visa, acquirer = rig
    visa.data_override = "1,2"
    with pytest.raises(ValueError, match="分块点数不符"):
        acquirer.acquire_all(configuration())
    assert visa.writes[-1] == ":STOP"


def test_successful_export_reports_final_stop_failure(rig):
    _, visa, acquirer = rig
    original = visa.write
    def stop_failure(command):
        if command == ":STOP" and visa.queries.count(":WAVeform:DATA?") == 8:
            raise OSError("最终停止通信失败")
        original(command)
    visa.write = stop_failure
    with pytest.raises(OSError, match="最终停止通信失败"):
        acquirer.acquire_all(configuration())


def test_frame_wait_timeout_does_not_read_old_data(rig, monkeypatch):
    _, visa, acquirer = rig
    acquirer.apply_config(configuration())
    original = visa.query
    visa.query = lambda command: "WAIT" if command == ":TRIGger:STATus?" else original(command)
    clock = iter([0, 100])
    monkeypatch.setattr("dho900.acquire.time.monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="完整帧停止超时"):
        acquirer.start_acquisition(configuration())
    assert ":WAVeform:DATA?" not in visa.queries
    assert visa.writes[-1] == ":STOP"


def test_acquire_all_cancellation_preserves_cause_and_stops(rig):
    _, visa, acquirer = rig
    def cancel():
        if ":TFORce" in visa.writes:
            raise InterruptedError("用户取消")
    acquirer._check_cancelled = cancel
    with pytest.raises(InterruptedError, match="用户取消"):
        acquirer.acquire_all(configuration())
    assert visa.writes[-1] == ":STOP"
    assert ":WAVeform:DATA?" not in visa.queries


def test_auto_depth_can_be_switched_back_to_strict_rate(rig):
    _, visa, acquirer = rig
    acquirer.use_auto_depth(100_000)
    acquirer.use_auto_depth(None)
    acquirer.apply_config(configuration())
    assert acquirer._snapshot["sample_rate_policy"] == "strict" and acquirer._snapshot["depth_mode"] == "fixed"
