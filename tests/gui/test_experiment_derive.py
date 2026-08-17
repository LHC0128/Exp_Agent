import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.main import ExperimentBody, experiment_derive


class ExperimentDeriveEndpointTests(unittest.TestCase):
    def test_keithley_rf_sensitivity_derive_follows_control_version(self):
        result = experiment_derive(
            "mx-keithley-6221-optimal-control-rf-sensitivity",
            ExperimentBody(parameters={"CONTROL_VERSION": "v4"}),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["values"]["TRIGGER_FREQUENCY_HZ"], pytest.approx(12000.0)
        )
        self.assertEqual(
            result["values"]["Y_RF_FREQUENCY_HZ"], pytest.approx(12000.0)
        )

    def test_keithley_xyz_balance_derive_returns_trigger_and_demod(self):
        result = experiment_derive(
            "mx-keithley-6221-optimal-control-xyz-balance",
            ExperimentBody(parameters={"CONTROL_VERSION": "v4"}),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["values"]["TRIGGER_FREQUENCY_HZ"], pytest.approx(12000.0)
        )
        self.assertEqual(
            result["values"]["DEMOD_FREQUENCY_HZ"], pytest.approx(12000.0)
        )

    def test_experiment_without_derive_runner_returns_empty_values(self):
        result = experiment_derive(
            "static-sensitivity",
            ExperimentBody(parameters={}),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["values"], {})

    def test_unknown_experiment_returns_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as context:
            experiment_derive("missing-experiment", ExperimentBody(parameters={}))
        self.assertEqual(context.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
