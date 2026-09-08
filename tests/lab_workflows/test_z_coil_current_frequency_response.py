"""Z 线圈实际电流频响的快速采样配置测试。"""

from __future__ import annotations

import pytest

from lab_workflows.experiment_modules.z_coil_current_frequency_response.models import (
    ZCoilCurrentFrequencyResponseParams,
)
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.workflow import (
    _sample_rate_for_frequency,
    _scope_config,
)


def test_dynamic_scope_rate_reduces_low_frequency_record() -> None:
    params = ZCoilCurrentFrequencyResponseParams()

    low_rate = _sample_rate_for_frequency(params, 10.0)
    high_rate = _sample_rate_for_frequency(params, 120000.0)

    assert low_rate == pytest.approx(100000.0)
    assert high_rate == pytest.approx(7680000.0)
    assert high_rate < params.scope_sample_rate_sa_s

    low_config, low_snapshot = _scope_config(
        params,
        10.0,
        measured_scale_v_div=1.0,
        measured_offset_v=0.0,
    )
    assert low_config.sampling_rate == pytest.approx(low_rate)
    assert low_config.total_points == 30000
    assert low_snapshot["maximum_sample_rate_sa_s"] == pytest.approx(10000000.0)


def test_legacy_inductance_params_keep_fixed_scope_rate() -> None:
    from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.models import (
        ZCoilInductanceFrequencyResponseParams,
    )

    params = ZCoilInductanceFrequencyResponseParams(scope_sample_rate_sa_s=500000.0)
    assert _sample_rate_for_frequency(params, 10.0) == pytest.approx(500000.0)
    assert _sample_rate_for_frequency(params, 10000.0) == pytest.approx(500000.0)


def test_model_rejects_dynamic_minimum_above_maximum() -> None:
    params = ZCoilCurrentFrequencyResponseParams(
        scope_sample_rate_sa_s=1_000_000.0,
        scope_min_sample_rate_sa_s=2_000_000.0,
    )
    errors = params.validate_model()
    assert any("SCOPE_MIN_SAMPLE_RATE_SA_S" in error for error in errors)
