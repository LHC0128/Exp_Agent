import unittest

import numpy as np

from lab_workflows.experiment_modules.noise_spectrum_xy.calibration import (
    fit_robust_linear_calibration,
)


class NoiseSpectrumXYCalibrationTests(unittest.TestCase):
    def test_robust_fit_rejects_vertical_peak_clusters(self):
        rng = np.random.default_rng(20260715)
        slope = 15000.0
        intercept = -200.0
        x_linear = np.linspace(0.2, 3.0, 240)
        y_linear = slope * x_linear + intercept + rng.normal(0.0, 25.0, len(x_linear))

        x_artifact = np.repeat([0.9, 1.9, 2.1], 24)
        y_artifact = (
            slope * x_artifact
            + intercept
            + np.tile(np.linspace(-8000.0, 8000.0, 24), 3)
        )
        x = np.concatenate([x_linear, x_artifact])
        y = np.concatenate([y_linear, y_artifact])

        result = fit_robust_linear_calibration(x, y)

        self.assertAlmostEqual(result.slope_hz_per_v, slope, delta=50.0)
        self.assertAlmostEqual(result.intercept_hz, intercept, delta=80.0)
        self.assertGreater(np.count_nonzero(~result.inlier_mask[-len(x_artifact):]), 50)
        self.assertGreater(np.count_nonzero(result.inlier_mask[:len(x_linear)]), 230)

    def test_fit_rejects_insufficient_calibration_points(self):
        with self.assertRaisesRegex(ValueError, "有效标定峰不足"):
            fit_robust_linear_calibration([0.1, 0.2], [1000.0, 2000.0])

    def test_fit_rejects_nonpositive_slope(self):
        with self.assertRaisesRegex(ValueError, "标定斜率必须为正"):
            fit_robust_linear_calibration(
                np.linspace(0.0, 1.0, 10),
                np.linspace(1000.0, 0.0, 10),
            )


if __name__ == "__main__":
    unittest.main()
