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
