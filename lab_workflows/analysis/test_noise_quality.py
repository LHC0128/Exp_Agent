"""MAD 高侧规则的共享边界回归。"""
import numpy as np
import pytest
from .noise_quality import inspect_noise_spectra, select_noise_records


@pytest.mark.parametrize("scores", [[1, 1, 1, 1, 20], [1, 1.1, 0.9, 1, 1, 20]])
def test_high_outlier_is_reversible(scores):
    keep, quality = select_noise_records(scores)
    assert keep[:-1].all() and not keep[-1]
    assert quality["candidate_indices"] == [len(scores) - 1]
    assert select_noise_records(scores, "none")[0].all()


@pytest.mark.parametrize("scores", [[1, 1, 1, 20], [0, 0, 0, 0, 20], [0.01, 1, 1, 1, 1]])
def test_small_samples_zero_baseline_and_low_values_are_not_removed(scores):
    assert select_noise_records(scores)[0].all()


def test_psd_matrix_is_preserved_and_only_whole_records_are_excluded():
    frequency = np.arange(11, dtype=float)
    matrix = np.ones((5, 11))
    matrix[-1] *= 400
    original = matrix.copy()
    keep, scores, before, after, _ = inspect_noise_spectra(frequency, matrix, 1, 8)
    np.testing.assert_array_equal(matrix, original)
    np.testing.assert_allclose(after, 1)
    np.testing.assert_allclose(before, 80.8)
    np.testing.assert_allclose(scores, [1, 1, 1, 1, 20])
    assert keep.shape == (5,) and len(after) == len(frequency)
    with pytest.raises(ValueError, match="覆盖"):
        inspect_noise_spectra(frequency, matrix, 1, 20)
