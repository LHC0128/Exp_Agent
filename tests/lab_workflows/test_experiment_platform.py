import ast
import math
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from lab_workflows.experiments import get_experiment, list_experiments
from lab_workflows.experiments.legacy import LegacyScriptAdapter
from lab_workflows.script_runner import OverrideTransformer
from lab_workflows.steps import (
    ArbitraryWaveformSpec,
    DeviceSession,
    PhaseCalibrationConfig,
    calibrate_demod_phase,
    set_temperature_switch,
    upload_arbitrary,
)


class FakeHF2:
    clockbase = 1

    def __init__(self):
        self.samples = iter([
            {"x": [1.0], "y": [1.0]},
            {"x": [1.0], "y": [1.0]},
            {"x": [1.0], "y": [0.0]},
        ])
        self.phase = 0.0

    def demod_path(self, index):
        return f"/dev/demods/{index}"

    def get_sample(self, _path):
        return next(self.samples)

    def get_double(self, _path):
        return self.phase

    def set_double(self, _path, value):
        self.phase = value

    def sync(self):
        return None


class ExperimentPlatformTests(unittest.TestCase):
    def test_registry_contains_only_24_formal_experiments(self):
        definitions = list_experiments()
        self.assertEqual(len(definitions), 24)
        self.assertEqual(len({item.id for item in definitions}), 24)
        self.assertNotIn("quick-test-scan", {item.id for item in definitions})

    def test_all_experiments_publish_existing_program_entries(self):
        root = Path(__file__).resolve().parents[2]
        for definition in list_experiments():
            self.assertTrue(definition.acquisition_program, definition.id)
            self.assertTrue((root / definition.acquisition_program).is_file())
            if definition.analysis_program:
                self.assertTrue((root / definition.analysis_program).is_file())
        direct_aw = get_experiment("rf-sensitivity-direct-aw-frequency").public()
        self.assertEqual(
            direct_aw["acquisition_program"],
            "experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py",
        )
        self.assertEqual(
            direct_aw["analysis_program"],
            "experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py",
        )

    def test_direct_aw_scan_range_is_exposed_in_gui_schema(self):
        definition = get_experiment("xy-direct-aw-dc-calibration")
        fields = {item["name"]: item for item in definition.schema()["fields"]}
        self.assertNotIn("XY_ENV_VOLTAGE_LIST_V", fields)
        self.assertEqual(fields["XY_ENV_VOLTAGE_START_V"]["default"], -2.0)
        self.assertEqual(fields["XY_ENV_VOLTAGE_STOP_V"]["default"], 2.0)
        self.assertEqual(fields["XY_ENV_VOLTAGE_POINTS"]["default"], 17)
        self.assertEqual(fields["XY_ENV_VOLTAGE_START_V"]["group"], "basic")
        self.assertEqual(fields["XY_ENV_VOLTAGE_POINTS"]["minimum"], 2)

    def test_direct_aw_waveform_file_is_csv_dropdown(self):
        definition = get_experiment("rf-sensitivity-direct-aw-frequency")
        fields = {item["name"]: item for item in definition.schema()["fields"]}
        waveform = fields["ARB_WAVEFORM_FILE"]
        expected = sorted(
            path.name
            for path in (Path(__file__).resolve().parents[2] / "experiments").glob("*.csv")
            if path.is_file()
        )
        self.assertEqual(
            [option["value"] for option in waveform["options"]],
            expected,
        )
        self.assertEqual(waveform["group"], "basic")
        errors = definition.preflight({"ARB_WAVEFORM_FILE": "missing.txt"})
        self.assertTrue(
            any("ARB WAVEFORM FILE" in error and "必须是" in error for error in errors)
        )

    def test_directory_options_are_refreshed_when_schema_is_requested(self):
        files = []
        for name in ("first.csv", "second.csv"):
            path = MagicMock(spec=Path)
            path.name = name
            path.is_file.return_value = True
            files.append(path)
        adapter = LegacyScriptAdapter(
            "sample",
            "RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py",
            "sample",
            field_metadata={
                "ARB_WAVEFORM_FILE": {
                    "options_from_directory": "experiments",
                    "options_pattern": "*.csv",
                }
            },
        )
        with patch.object(Path, "glob", side_effect=[[files[0]], files]):
            first_options = next(
                field
                for field in adapter.schema()["fields"]
                if field["name"] == "ARB_WAVEFORM_FILE"
            )["options"]
            refreshed_options = next(
                field
                for field in adapter.schema()["fields"]
                if field["name"] == "ARB_WAVEFORM_FILE"
            )["options"]
        self.assertEqual([item["value"] for item in first_options], ["first.csv"])
        self.assertEqual(
            [item["value"] for item in refreshed_options],
            ["first.csv", "second.csv"],
        )

    def test_direct_aw_acquisition_mode_is_enum(self):
        definition = get_experiment("xy-direct-aw-dc-calibration")
        fields = {item["name"]: item for item in definition.schema()["fields"]}
        self.assertEqual(
            [option["value"] for option in fields["ACQUISITION_MODE"]["options"]],
            ["daq_y", "demod_rxy", "both"],
        )
        errors = definition.preflight({"ACQUISITION_MODE": "invalid"})
        self.assertTrue(any("采集模式必须是" in error for error in errors))

    def test_numpy_scan_override_preserves_array_type(self):
        tree = ast.parse("XY_ENV_VOLTAGE_LIST_V = np.linspace(-2, 2, 17)")
        tree = OverrideTransformer({"XY_ENV_VOLTAGE_LIST_V"}).visit(tree)
        ast.fix_missing_locations(tree)
        namespace = {
            "np": np,
            "__LAB_OVERRIDES": {"XY_ENV_VOLTAGE_LIST_V": [-1.0, 0.0, 1.0]},
            "__LAB_OVERRIDE_APPLIED": set(),
        }
        exec(compile(tree, "<test>", "exec"), namespace)
        self.assertIsInstance(namespace["XY_ENV_VOLTAGE_LIST_V"], np.ndarray)
        np.testing.assert_array_equal(
            namespace["XY_ENV_VOLTAGE_LIST_V"], [-1.0, 0.0, 1.0]
        )

    def test_gui_override_does_not_reset_runtime_phase_update(self):
        tree = ast.parse(
            "\n".join([
                "XY_CTRL_PHASE = 0.0",
                "theta_deg = -64.61",
                "XY_CTRL_PHASE = (XY_CTRL_PHASE - theta_deg) % 360.0",
                "phase_after_calibration = XY_CTRL_PHASE",
            ])
        )
        tree = OverrideTransformer({"XY_CTRL_PHASE"}).visit(tree)
        ast.fix_missing_locations(tree)
        namespace = {
            "__LAB_OVERRIDES": {"XY_CTRL_PHASE": 0.0},
            "__LAB_OVERRIDE_APPLIED": set(),
        }
        exec(compile(tree, "<test>", "exec"), namespace)
        self.assertAlmostEqual(namespace["phase_after_calibration"], 64.61)

    def test_gui_override_uses_first_executed_branch_assignment(self):
        tree = ast.parse(
            "\n".join([
                "USE_LATEST = True",
                "if USE_LATEST:",
                "    DATA_DIR = 'latest'",
                "else:",
                "    DATA_DIR = 'placeholder'",
                "selected_data_dir = DATA_DIR",
            ])
        )
        tree = OverrideTransformer({"USE_LATEST", "DATA_DIR"}).visit(tree)
        ast.fix_missing_locations(tree)
        namespace = {
            "__LAB_OVERRIDES": {
                "USE_LATEST": False,
                "DATA_DIR": "selected-run",
            },
            "__LAB_OVERRIDE_APPLIED": set(),
        }
        exec(compile(tree, "<test>", "exec"), namespace)
        self.assertEqual(namespace["selected_data_dir"], "selected-run")

    def test_device_session_reuses_resource(self):
        session = DeviceSession()
        device = MagicMock()
        factory = MagicMock(return_value=device)
        self.assertIs(session.connect("x", "USB::1", factory), device)
        self.assertIs(session.connect("y", "USB::1", factory), device)
        factory.assert_called_once()
        device.connect.assert_called_once()

    def test_temperature_switch_uses_dc_and_keeps_output_on(self):
        device = MagicMock()
        voltage = set_temperature_switch(device, False, channel=2)
        self.assertEqual(voltage, 0.0)
        device.setup_dc.assert_called_once_with(0.0, channel=2)
        device.set_output.assert_called_once_with(True, channel=2)

    def test_phase_calibration_supports_demod3_and_converges(self):
        device = FakeHF2()
        result = calibrate_demod_phase(
            device,
            PhaseCalibrationConfig(demod_idx=3, settle_time=0, max_attempts=3),
        )
        self.assertTrue(result.converged)
        self.assertEqual(result.demod_idx, 3)
        self.assertAlmostEqual(device.phase, 45.0)

    def test_arbitrary_waveform_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            ArbitraryWaveformSpec(np.array([0.0, math.nan]), 1, 1).validated_values()
        with self.assertRaises(ValueError):
            ArbitraryWaveformSpec(np.array([0.0, 1.1]), 1, 1).validated_values()

    def test_upload_clears_modulation_and_burst(self):
        device = MagicMock()
        upload_arbitrary(
            device,
            ArbitraryWaveformSpec(np.array([-1.0, 1.0]), 1000, 1.0, channel=2),
        )
        device.set_burst_state.assert_called_once_with(False, channel=2)
        device.set_mod_state.assert_called_once_with(False, channel=2)
        device.setup_arbitrary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
