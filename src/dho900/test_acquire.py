"""DHO900 假仪器采集测试：严格率、坐标、分块、取消和停止恢复。"""

from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from sds_acquisition.config import AcquisitionConfig, ChannelConfig, TriggerConfig
from .acquire import DHO900Acquisition, FrameMismatch, _Preamble
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


def test_capped_depth_accepts_scope_smaller_actual_depth(rig):
    """上限只是上限：仪器在部分时基下给不出等于上限的深度时，按回读的实际深度采帧。"""
    _, visa, acquirer = rig
    visa.auto_points = 100_000
    acquirer.use_auto_depth(10_000)
    original = visa.write

    def clamped(command):
        if command.startswith(":ACQuire:MDEPth ") and not command.endswith("AUTO"):
            original(f":ACQuire:MDEPth {min(int(command.split()[-1]), 5_000)}")
            return
        original(command)

    visa.write = clamped
    results = acquirer.acquire_all(configuration())
    assert acquirer._snapshot["depth_mode"] == "capped"
    assert acquirer._snapshot["actual_points"] == 5_000
    assert len(results[0].voltage) == 5_000


def test_cap_falls_back_to_auto_depth_when_timebase_cannot_honor_it(rig):
    """该时基下设了上限深度却回读不出整数点数时，退回 AUTO 深度并如实记录，不改请求条件。"""
    _, visa, acquirer = rig
    visa.auto_points = 100_000
    acquirer.use_auto_depth(10_000)
    original = visa.write

    def write(command):
        if command.startswith(":ACQuire:MDEPth ") and not command.endswith("AUTO"):
            visa.rate_override = 1234.5  # 显式浅深度下固件给出与窗口不对齐的采样率
        if command == ":ACQuire:MDEPth AUTO":
            visa.rate_override = None
        original(command)

    visa.write = write
    acquirer.apply_config(configuration())
    assert acquirer._snapshot["depth_mode"] == "AUTO"
    assert acquirer._snapshot["actual_points"] == 100_000


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
    # XREFerence 在 RAW 下恒为 0（手册 3.28.8），时间轴由 XORigin + 序号 × XINCrement 给出。
    result = acquirer.acquire_channel(1, timebase_scale=999)
    assert result.channel == 1
    assert result.preamble_dict["point_num"] == 300
    assert result.preamble_dict["format"] == 2
    assert result.preamble_dict["window_start_points"] == 0
    assert result.raw_data.size == 0
    np.testing.assert_allclose(result.voltage, np.arange(300) * 0.01 - 2)
    expected_time = -0.5 + np.arange(300) / 300
    np.testing.assert_allclose(result.time, expected_time)
    assert ":WAVeform:STOP 127" in visa.writes
    assert ":WAVeform:STARt 128" in visa.writes
    assert ":WAVeform:STOP 254" in visa.writes
    assert ":WAVeform:STARt 255" in visa.writes
    assert visa.queries.count(":WAVeform:DATA?") == 3
    assert ":WAVeform:POINts 300" in visa.writes
    assert not any("BYTeorder" in command or "WIDTh" in command for command in visa.writes)


def test_capped_depth_frame_is_read_from_centered_memory_window(rig):
    """深度档比屏幕窗口长时从内存中段读：触发点落在帧中心，时间轴以触发沿为 0。

    真机实测（2026-11-14，MAX_MEMORY_DEPTH=10000、20 µs 窗）：内存 10000 点、窗口
    6250 点、XORigin=-16 µs 时，旧实现从 STARt 0 读，触发点落在帧内 80% 处。
    """
    _, visa, acquirer = rig
    depth, window, increment, origin = 10_000, 6_250, 3.2e-9, -16e-6
    visa.depth = depth
    visa.preamble_override = f"2,2,{window},1,{increment},{origin},0,0.004,7,128"
    acquirer._configured = True
    acquirer._snapshot = {"actual_points": window, "actual_sample_rate_sa_s": 1.0 / increment,
                          "depth_mode": "capped", "requested": {}}
    result = acquirer.acquire_channel(1, 0.002)
    assert result.preamble_dict["window_start_points"] == 1875
    assert ":WAVeform:STARt 1876" in visa.writes
    assert len(result.time) == window
    assert result.time[0] == pytest.approx(-10e-6, rel=1e-9)
    assert result.time[window // 2] == pytest.approx(0.0, abs=2 * increment)


def test_memory_depth_shorter_than_window_keeps_first_points(rig):
    """触发点前没有半个窗口时退回 STARt 0，按实际窗口读取并标记未居中。"""
    _, visa, acquirer = rig
    visa.depth = 6_250
    visa.preamble_override = f"2,2,6250,1,3.2e-9,{-2e-6},0,0.004,7,128"
    acquirer._configured = True
    acquirer._snapshot = {"actual_points": 6_250, "actual_sample_rate_sa_s": 1.0 / 3.2e-9,
                          "depth_mode": "capped", "requested": {}}
    result = acquirer.acquire_channel(1, 0.002)
    # 触发点在第 625 点，窗口 6250 点：起点被内存深度夹到 0。
    assert result.preamble_dict["window_start_points"] == 0
    assert result.preamble_dict["window_centered"] is False
    assert result.time[0] == pytest.approx(-2e-6, rel=1e-9)


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


def test_frame_mismatch_is_redispatched_once_with_fresh_configuration(rig, monkeypatch):
    """AUTO 深度在配置回读后被固件重算时，被拒的整帧要重做一次，且新帧按重做后的回读校验。"""
    _, visa, acquirer = rig
    visa.auto_points = 10_000
    acquirer.use_auto_depth(100_000)
    original = DHO900Acquisition.apply_config
    applied = []

    def apply_then_drift(config):
        original(acquirer, config)
        applied.append(acquirer._snapshot["actual_points"])
        if len(applied) == 1:
            visa.depth = 5_000  # 模拟固件在配置回读之后改小了深度：首帧参数核对必须拒绝

    monkeypatch.setattr(acquirer, "apply_config", apply_then_drift)
    results = acquirer.acquire_all(configuration())
    assert applied == [10_000, 10_000]
    assert results[0].config_snapshot["actual_points"] == 10_000
    assert len(results[0].voltage) == 10_000


def test_persistent_frame_mismatch_is_reported_after_one_retry(rig, monkeypatch):
    """重做仍不符时如实上抛，不无限重试。"""
    _, visa, acquirer = rig
    visa.auto_points = 10_000
    acquirer.use_auto_depth(100_000)
    original = DHO900Acquisition.apply_config
    applied = []

    def apply_then_drift(config):
        original(acquirer, config)
        applied.append(acquirer._snapshot["actual_points"])
        visa.depth = 5_000

    monkeypatch.setattr(acquirer, "apply_config", apply_then_drift)
    with pytest.raises(FrameMismatch, match="整帧存储深度不符"):
        acquirer.acquire_all(configuration())
    assert len(applied) == 2
    assert ":WAVeform:DATA?" not in visa.queries


def test_auto_depth_waits_for_depth_and_rate_to_settle_together(rig):
    """固件换挡时先按旧深度给出采样率：只让采样率稳定一次不算稳定，深度变化后要重新确认。"""
    _, visa, acquirer = rig
    visa.auto_points = 10_000
    acquirer.use_auto_depth(100_000)
    queries = []
    original = visa.query

    def drifting(command):
        if command == ":ACQuire:SRATe?":
            queries.append(command)
            if len(queries) == 2:  # 第 2 次回读后固件重算出新的 AUTO 深度
                visa.depth = 25_000
                visa.auto_points = 25_000
        return original(command)

    visa.query = drifting
    acquirer.apply_config(configuration())
    assert acquirer._snapshot["actual_points"] == 25_000


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


@pytest.mark.parametrize("delay, succeeds", [(0.0, False), (1.5, True)])
def test_stop_confirmation_delay_gates_firmware_buffer_readiness(rig, delay, succeeds):
    """真机固件在 STOP 置位后波形缓冲尚不可读：累计等待不足时 DATA? 返回空串。

    空串经 split 得到 1 个字段，即「期望 N，实际 1」的用户症状（N 为分块预算）；
    acquire_delay 等待后才允许读取，整帧完整且等待值随快照保存。
    """
    _, visa, acquirer = rig
    waited = [0.0]
    base_wait = acquirer._wait

    def wait(seconds):
        base_wait(seconds)
        waited[0] += seconds

    acquirer._wait = wait
    original_query = visa.query

    def query(command):
        if command == ":WAVeform:DATA?" and waited[0] < 1.0:
            return ""
        return original_query(command)

    visa.query = query
    config = configuration(acquire_delay=delay)
    if succeeds:
        results = acquirer.acquire_all(config)
        assert len(results[0].voltage) == 1000
        assert results[0].config_snapshot["requested"]["acquire_delay"] == delay
    else:
        with pytest.raises(ValueError, match="期望 127，实际 1"):
            acquirer.acquire_all(config)


def test_raw_mode_reset_unlocks_deep_frame_points(rig):
    """DHO900 固件：已处 RAW 时重写 MODE RAW 不刷新点数，POINts 锁在屏幕默认值。

    模拟该行为：只有先切 NORMal 再切 RAW 才解除锁定。修复后深帧（>屏幕点数）
    能正确读回整帧；若缺少重置则 preamble 点数不符而拒绝读帧。
    """
    _, visa, acquirer = rig
    SCREEN_POINTS = 127  # 假仪器屏幕默认点数（小于深帧）
    state = {"mode": "NORM", "locked_points": SCREEN_POINTS}
    original_write = visa.write
    original_query = visa.query

    def write(command):
        if command.startswith(":WAVeform:MODE "):
            new_mode = command.split()[-1].upper()[:4]
            # 先 NORMal 再 RAW 才解除点数锁定；直接重写 RAW 不刷新。
            if new_mode == "NORM":
                state["locked_points"] = None
            state["mode"] = new_mode
        elif command.startswith(":WAVeform:POINts ") and state["locked_points"] is not None:
            # 锁定时 POINts 写入被忽略，回读仍是屏幕点数。
            return
        original_write(command)

    def query(command):
        if command == ":WAVeform:POINts?" and state["locked_points"] is not None:
            return str(state["locked_points"])
        if command == ":WAVeform:PREamble?" and state["locked_points"] is not None:
            # preamble 点数反映被锁定的屏幕点数。
            base = original_query(command).split(",")
            base[2] = str(state["locked_points"])
            return ",".join(base)
        return original_query(command)

    visa.write = write
    visa.query = query
    visa.depth = 1000  # 深帧 > 屏幕点数
    config = configuration()
    results = acquirer.acquire_all(config)
    assert len(results[0].voltage) == 1000
    # 确认读帧前确实做了 NORMal→RAW 重置。
    modes = [c.split()[-1] for c in visa.writes if c.startswith(":WAVeform:MODE ")]
    assert "NORMal" in modes and modes.index("NORMal") < len(modes) - 1
