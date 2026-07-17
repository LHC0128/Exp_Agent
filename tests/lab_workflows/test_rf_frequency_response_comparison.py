from pathlib import Path
import unittest

import matplotlib

matplotlib.use("Agg")

from lab_workflows.analysis import (
    compare_frequency_response_amplitude,
    load_frequency_response_series,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "rf_frequency_response"


class RFFrequencyResponseComparisonTests(unittest.TestCase):
    def test_loads_median_response_and_builds_label(self):
        series = load_frequency_response_series(FIXTURE_DIR / "run_a")

        self.assertEqual(series.label, "run_a (a.csv)")
        self.assertEqual(series.frequency_hz.tolist(), [100.0, 200.0, 300.0])
        self.assertEqual(series.response_v.tolist(), [1.0, 2.0, 1.5])

    def test_saves_comparison_figure(self):
        output_path = ROOT / "results" / "test_rf_frequency_response_comparison.png"
        try:
            result_path = compare_frequency_response_amplitude(
                [FIXTURE_DIR / "run_a", FIXTURE_DIR / "run_b"],
                output_path.parent,
                output_name=output_path.name,
            )

            self.assertEqual(result_path, output_path)
            self.assertTrue(output_path.is_file())
            self.assertGreater(output_path.stat().st_size, 0)
        finally:
            output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
