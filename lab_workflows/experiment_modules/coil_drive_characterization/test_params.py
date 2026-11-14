"""线圈驱动参数：v1 → v2 迁移与记录窗口参数。"""
import pytest

from .models import CoilDriveCharacterizationParams as Params
from .preparation import capture_config


def test_v1_params_drop_removed_sample_rate_key_and_use_new_defaults():
    p = Params.from_external({"MAX_SAMPLE_RATE_SA_S": 250e6, "ZERO_FRAMES": 3}, schema_version=1)
    assert p.zero_frames == 3 and p.max_memory_depth == 1_000_000
    with pytest.raises(ValueError, match="MAX_SAMPLE_RATE_SA_S"):
        Params.from_external({"MAX_SAMPLE_RATE_SA_S": 250e6}, schema_version=2)


def test_capture_window_is_1_2_5_ten_division_and_uses_depth_cap():
    p = Params(max_memory_depth=100_000)
    config = capture_config(p, p.pulse_window_multiple * 10e-6, 1.0)
    assert config.sampling_time == pytest.approx(50e-6) and config.timebase_scale == pytest.approx(5e-6)
    assert capture_config(p, p.zero_window_s, 1.0).sampling_time == pytest.approx(0.02)


def test_capture_config_passes_ui_acquire_delay_to_driver():
    """STOP 确认后的等待由 UI 参数给定，不再写死；0 表示不等待（A/B 对照用）。"""
    assert Params().scope_acquire_delay_s == 1.5
    assert capture_config(Params(scope_acquire_delay_s=2.5), 0.02, 1.0).acquire_delay == 2.5
    assert capture_config(Params(scope_acquire_delay_s=0.0), 0.02, 1.0).acquire_delay == 0.0


def test_legacy_snapshot_without_delay_key_fills_zero_not_current_default():
    schema = {field["name"]: field for field in Params().schema()["fields"]}
    assert schema["SCOPE_ACQUIRE_DELAY_S"]["legacy_default"] == 0.0
    assert Params.from_external({"ZERO_FRAMES": 3}).scope_acquire_delay_s == 1.5
