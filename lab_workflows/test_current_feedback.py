"""闭环频响加载必须核对有效重复证据，不能相信旧的合格标记。"""
from types import SimpleNamespace

import numpy as np
import pytest

from .current_feedback import load_aw_frequency_response, sha256_file
from .experiment_modules.z_aw_closed_loop_waveform_correction.response_filter import build_learning_filter


def write_response(root, **overrides):
    path = root / "data/Z_Coil_Current_Frequency_Response/1014_1200_response/results/frequency_response.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(format_version=np.int64(4), output_protocol="coherent_swept_sine_current_response",
        command_voltage_reference="50_ohm", phase_reference="measured_drive_voltage",
        frequency_hz=np.array([10., 20., 30., 40.]), transfer_real_a_per_v=np.full(4, .01),
        transfer_imag_a_per_v=np.zeros(4), reliable=np.ones(4, dtype=bool),
        phase_reliable=np.ones(4, dtype=bool), frame_count=np.full(4, 3, dtype=int),
        valid_frame_count=np.full(4, 3, dtype=int), valid_frame_count_rule="exact_frequency_v1",
        sense_resistor_ohm=10.)
    payload.update(overrides)
    for name in [name for name, value in payload.items() if value is None]:
        del payload[name]
    np.savez(path, **payload)
    return path


def test_phase_qualification_reaches_real_closed_loop_without_crossing_invalid_point(tmp_path):
    path = write_response(tmp_path, valid_frame_count=np.array([2, 2, 1, 3]))
    before = sha256_file(path)
    response = load_aw_frequency_response(tmp_path, "1014_1200_response")
    np.testing.assert_array_equal(response.reliable, [True, True, False, True])
    np.testing.assert_array_equal(response.frequency_hz, [10, 20, 30, 40])
    learning = build_learning_filter(SimpleNamespace(time_s=np.arange(128) / 128), .01,
        response, cutoff_hz=40., regularization=.01)
    assert learning.learning_band[10] and learning.learning_band[15] and learning.learning_band[40]
    assert not np.any(learning.learning_band[[25, 30, 35]])
    assert 30 in learning.excluded_frequency_hz
    assert sha256_file(path) == before


def test_false_phase_mask_is_respected_despite_sufficient_total_and_valid_repeats(tmp_path):
    write_response(tmp_path, phase_reliable=np.array([True, True, False, False]))
    response = load_aw_frequency_response(tmp_path, "1014_1200_response")
    np.testing.assert_array_equal(response.reliable, [True, True, False, False])


@pytest.mark.parametrize("missing", ["phase_reliable", "frame_count", "valid_frame_count", "valid_frame_count_rule"])
def test_older_results_without_qualification_evidence_request_reanalysis(tmp_path, missing):
    path = write_response(tmp_path, **{missing: None})
    before = sha256_file(path)
    with pytest.raises(ValueError, match="已有原始数据重新分析"):
        load_aw_frequency_response(tmp_path, "1014_1200_response")
    assert sha256_file(path) == before


@pytest.mark.parametrize("rule", ["tolerance_v2", np.array(["exact_frequency_v1"]), 2, True])
def test_untrusted_repeat_grouping_rule_requests_reanalysis(tmp_path, rule):
    path = write_response(tmp_path, valid_frame_count_rule=rule)
    before = sha256_file(path)
    with pytest.raises(ValueError, match="已有原始数据重新分析"):
        load_aw_frequency_response(tmp_path, "1014_1200_response")
    assert sha256_file(path) == before


@pytest.mark.parametrize("valid_counts", [np.ones(4, dtype=int), np.array([2, 1, 1, 1])])
def test_old_mislabelled_single_repeats_cannot_be_used_for_closed_loop(tmp_path, valid_counts):
    write_response(tmp_path, valid_frame_count=valid_counts)
    with pytest.raises(ValueError, match="闭环要求相位合格且至少两次有效重复"):
        load_aw_frequency_response(tmp_path, "1014_1200_response")


@pytest.mark.parametrize("overrides", [
    {"valid_frame_count": np.array([2, 2, 2])},
    {"valid_frame_count": np.array([-1, 2, 2, 2])},
    {"valid_frame_count": np.array([4, 2, 2, 2])},
    {"valid_frame_count": np.array([2., 2., 2., 2.])},
    {"valid_frame_count": np.ones(4, dtype=bool)},
    {"phase_reliable": np.array(["true"] * 4)},
])
def test_inconsistent_qualification_metadata_is_rejected(tmp_path, overrides):
    write_response(tmp_path, **overrides)
    with pytest.raises(ValueError):
        load_aw_frequency_response(tmp_path, "1014_1200_response")
