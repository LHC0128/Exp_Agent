import math
import unittest

import numpy as np

from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.acquisition import (
    acquire_demod_r_mean,
    extract_polled_values,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.analysis_core import (
    analyze_r_matrix,
    lorentzian_r_response,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.models import (
    NoiseSpectrumXYDemod3RParams,
)
from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.scan import (
    build_scan_axes,
    estimate_scan_duration_s,
    iter_frequency_batches,
)
from lab_workflows.experiments import get_experiment


class FakeCollector:
    def __init__(self, payload):
        self.payload = payload
        self.execute_count = 0
        self.wait_args = None

    def execute(self):
        self.execute_count += 1

    def wait(self, target, poll_interval=0.1, timeout=0):
        self.wait_args = (target, poll_interval, timeout)

    def read(self):
        return self.payload


class NoiseSpectrumXYDemod3RTests(unittest.TestCase):
    def test_default_axes_batches_and_duration(self):
        params = NoiseSpectrumXYDemod3RParams()
        control, envelope, demod3 = build_scan_axes(params)
        self.assertEqual(len(control), 200)
        self.assertEqual(len(demod3), 200)
        self.assertEqual(control[0], 200)
        self.assertEqual(control[-1], 20000)
        self.assertEqual(demod3[0], 200)
        self.assertEqual(demod3[-1], 20000)
        self.assertTrue(np.all(envelope >= 0))
        batches = list(iter_frequency_batches(200, 25))
        self.assertEqual(len(batches), 8)
        self.assertTrue(all(len(batch) == 25 for batch in batches))
        self.assertAlmostEqual(estimate_scan_duration_s(params), 15200.0)

    def test_schema_and_preflight_are_independent_from_psd_experiment(self):
        definition = get_experiment("noise-spectrum-xy-demod3-r")
        fields = {item["name"]: item for item in definition.schema()["fields"]}
        self.assertEqual(definition.execution_mode, "typed_workflow")
        self.assertEqual(fields["CONTROL_FREQ_START_HZ"]["default"], 200.0)
        self.assertEqual(fields["DEMOD3_FREQ_POINTS"]["default"], 200)
        self.assertEqual(fields["DEMOD3_ACQUISITION_DURATION_S"]["default"], 0.1)
        self.assertEqual(fields["TEMP_RECOVERY_BATCH_POINTS"]["default"], 25)
        self.assertEqual(definition.preflight({}), [])
        errors = definition.preflight({"TEMP_RECOVERY_BATCH_POINTS": 201})
        self.assertTrue(any("不能超过" in error for error in errors))

    def test_continuous_r_acquisition_returns_only_mean(self):
        path = "/dev18246/demods/3/sample.r"
        collector = FakeCollector({path: {"value": np.array([[1.0, 2.0, 3.0]])}})
        result = acquire_demod_r_mean(collector, path, 0.1)
        self.assertEqual(result, 2.0)
        self.assertEqual(collector.execute_count, 1)
        self.assertEqual(collector.wait_args[0], 1.0)
        self.assertEqual(collector.wait_args[1], 0.02)
        self.assertIsInstance(result, float)

    def test_empty_or_nonfinite_poll_data_becomes_nan(self):
        path = "/dev18246/demods/3/sample.r"
        self.assertEqual(
            extract_polled_values({path.upper(): {"value": [1.0, math.nan]}}, path).tolist(),
            [1.0],
        )
        collector = FakeCollector({})
        self.assertTrue(math.isnan(acquire_demod_r_mean(collector, path, 0.1)))

    def test_synthetic_r_matrix_calibrates_and_fits(self):
        envelope = np.linspace(0.1, 1.3, 80)
        configured_control = 15000.0 * envelope + 100.0
        demod3_frequency = np.linspace(2500.0, 18000.0, 36)
        matrix = np.empty((len(envelope), len(demod3_frequency)))
        for idx, frequency in enumerate(demod3_frequency):
            matrix[:, idx] = lorentzian_r_response(
                configured_control,
                gamma_hz=350.0,
                fit_amplitude=2.0,
                r_baseline_v=1e-5,
                frequency_offset_hz=0.0,
                fixed_frequency_hz=frequency,
            )
        result = analyze_r_matrix(matrix, envelope, demod3_frequency)
        self.assertAlmostEqual(result.calibration.slope_hz_per_v, 15000.0, delta=800.0)
        self.assertEqual(result.fit_parameters.shape, (len(demod3_frequency), 4))
        self.assertGreater(np.count_nonzero(result.fit_mask), 20)
        self.assertTrue(np.all(result.fit_parameters[result.fit_mask, 2] >= 0))


if __name__ == "__main__":
    unittest.main()
