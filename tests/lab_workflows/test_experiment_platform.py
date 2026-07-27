import ast
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from lab_workflows.experiments import get_experiment, list_experiments
from lab_workflows.experiment_modules.noise_spectrum_xy.models import (
    NoiseSpectrumXYParams,
)
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_frequency.models import (
    RFDirectAWFrequencyParams,
)
from lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.models import (
    XYDirectAWDCCalibrationParams,
)
from lab_workflows.experiment_runtime import apply_runtime_params
from lab_workflows.experiments.typed import TypedWorkflowAdapter
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
    def test_typed_workflow_module_startup_has_no_circular_import(self):
        root = Path(__file__).resolve().parents[2]
        environment = os.environ.copy()
        environment.pop("LAB_TYPED_PARAMETERS", None)
        module = "lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.workflow"
        completed = subprocess.run(
            [sys.executable, "-m", module],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        output = completed.stdout + completed.stderr
        self.assertNotIn("partially initialized module", output)
        self.assertIn("LAB_TYPED_PARAMETERS", output)
        self.assertIn("RuntimeError", output)

    def test_registry_contains_only_33_formal_experiments(self):
        definitions = list_experiments()
        self.assertEqual(len(definitions), 33)
        self.assertEqual(len({item.id for item in definitions}), 33)
        self.assertNotIn("quick-test-scan", {item.id for item in definitions})

    def test_new_and_legacy_execution_modes_are_explicit(self):
        definitions = {item.id: item for item in list_experiments()}
        for experiment_id in (
            "static-sensitivity",
            "noise-spectrum-xy",
            "rf-sensitivity-direct-aw-frequency",
            "xy-direct-aw-dc-calibration",
            "t2-calibration",
            "mx-y-rf-sensitivity",
            "mx-y-rf-power-optimization",
            "mx-main-field-calibration",
            "mx-xy-residual-field-calibration",
            "mx-z-field-calibration",
            "mx-z-noise-spectrum",
            "noise-spectrum-xy-demod3-r",
        ):
            self.assertEqual(definitions[experiment_id].execution_mode, "typed_workflow")
        self.assertEqual(definitions["noise-spectrum-xy-v2"].execution_mode, "legacy_script")
        self.assertEqual(definitions["t1-calibration"].execution_mode, "legacy_script")
        self.assertEqual(definitions["noise-spectrum-xy"].public()["execution_mode"], "typed_workflow")

    def test_migrated_definitions_use_typed_adapter_not_legacy_ast(self):
        for experiment_id in (
            "noise-spectrum-xy",
            "rf-sensitivity-direct-aw-frequency",
            "xy-direct-aw-dc-calibration",
            "t2-calibration",
            "mx-y-rf-sensitivity",
            "mx-y-rf-power-optimization",
            "mx-main-field-calibration",
            "mx-xy-residual-field-calibration",
            "mx-z-field-calibration",
            "mx-z-noise-spectrum",
            "noise-spectrum-xy-demod3-r",
        ):
            definition = get_experiment(experiment_id)
            self.assertIsInstance(definition.schema_provider.__self__, TypedWorkflowAdapter)

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
        self.assertEqual(fields["XY_ENV_VOLTAGE_START_V"]["default"], -4)
        self.assertEqual(fields["XY_ENV_VOLTAGE_STOP_V"]["default"], 4)
        self.assertEqual(fields["XY_ENV_VOLTAGE_POINTS"]["default"], 17)
        self.assertEqual(fields["XY_ENV_VOLTAGE_START_V"]["group"], "basic")
        self.assertEqual(fields["XY_ENV_VOLTAGE_POINTS"]["minimum"], 2)
        self.assertLess(
            fields["Z_RF_FREQ_START_Hz"]["default"],
            fields["Z_RF_FREQ_STOP_Hz"]["default"],
        )
        self.assertGreater(fields["Z_RF_FREQ_STEP_Hz"]["default"], 0)
        for name in (
            "Z_RF_FREQ_START_Hz",
            "Z_RF_FREQ_STOP_Hz",
            "Z_RF_FREQ_STEP_Hz",
        ):
            self.assertEqual(fields[name]["group"], "basic")
            self.assertEqual(fields[name]["unit"], "Hz")

    def test_noise_spectrum_target_frequency_scan_is_exposed_and_validated(self):
        definition = get_experiment("noise-spectrum-xy")
        fields = {item["name"]: item for item in definition.schema()["fields"]}
        self.assertEqual(fields["TARGET_NOISE_FREQ_START_HZ"]["default"], 0.0)
        self.assertEqual(fields["TARGET_NOISE_FREQ_STOP_HZ"]["default"], 50000.0)
        self.assertEqual(fields["TARGET_NOISE_FREQ_POINTS"]["default"], 500)
        self.assertEqual(fields["XY_AW_OUTPUT_VPP"]["default"], 8.0)
        self.assertEqual(fields["XY_AW_OUTPUT_OFFSET_V"]["default"], 0.0)
        self.assertEqual(fields["XY_CTRL_K_HZ_PER_V"]["group"], "advanced")
        self.assertEqual(definition.preflight({}), [])

        errors = definition.preflight({"XY_AW_OUTPUT_VPP": 4.0})
        self.assertTrue(
            any("固定 AW 可表达范围" in error for error in errors)
        )
        errors = definition.preflight({"TARGET_NOISE_FREQ_STOP_HZ": 70000.0})
        self.assertTrue(
            any("超出标定有效范围" in error for error in errors)
        )

    def test_direct_aw_waveform_file_is_csv_dropdown(self):
        definition = get_experiment("rf-sensitivity-direct-aw-frequency")
        fields = {item["name"]: item for item in definition.schema()["fields"]}
        self.assertEqual(fields["Z_RF_FREQ_OFFSET_HZ"]["default"], 50)
        self.assertEqual(fields["Z_RF_FREQ_OFFSET_HZ"]["group"], "advanced")
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
            any("ARB_WAVEFORM_FILE" in error and "必须是" in error for error in errors)
        )

    def test_directory_options_are_refreshed_when_schema_is_requested(self):
        files = []
        for name in ("first.csv", "second.csv"):
            path = MagicMock(spec=Path)
            path.name = name
            path.is_file.return_value = True
            files.append(path)
        params = RFDirectAWFrequencyParams()
        with patch.object(Path, "glob", side_effect=[[files[0]], files]):
            first_options = next(
                field
                for field in params.schema()["fields"]
                if field["name"] == "ARB_WAVEFORM_FILE"
            )["options"]
            refreshed_options = next(
                field
                for field in params.schema()["fields"]
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
        self.assertTrue(any("ACQUISITION_MODE 必须是" in error for error in errors))

    def test_direct_aw_pump_modulation_frequency_is_basic_parameter(self):
        definition = get_experiment("xy-direct-aw-dc-calibration")
        fields = {item["name"]: item for item in definition.schema()["fields"]}
        frequency = fields["PUMP_MOD_FREQ_Hz"]
        self.assertEqual(frequency["default"], 10000)
        self.assertEqual(frequency["group"], "basic")
        self.assertEqual(frequency["unit"], "Hz")
        errors = definition.preflight({"PUMP_MOD_FREQ_Hz": 10250})
        self.assertTrue(any("500 Hz AW 重复频率的整数倍" in error for error in errors))

    def test_direct_aw_workflows_keep_trigger_running_before_comp_output(self):
        root = Path(__file__).resolve().parents[2]

        def assert_trigger_sequence(module_name, trigger_name, configure_name):
            workflow = (
                root
                / "lab_workflows"
                / "experiment_modules"
                / module_name
                / "workflow.py"
            )
            tree = ast.parse(
                workflow.read_text(encoding="utf-8"), filename=str(workflow)
            )
            functions = {
                node.name: node
                for node in tree.body
                if isinstance(node, ast.FunctionDef)
            }

            def device_calls(function_name, device_name, method_name, state=None):
                calls = []
                for node in ast.walk(functions[function_name]):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    owner = node.func.value
                    if not (
                        isinstance(owner, ast.Name)
                        and owner.id == device_name
                        and node.func.attr == method_name
                    ):
                        continue
                    if state is not None:
                        if not node.args or not isinstance(node.args[0], ast.Constant):
                            continue
                        if node.args[0].value is not state:
                            continue
                    calls.append(node)
                return sorted(calls, key=lambda item: item.lineno)

            upload = functions["upload_direct_aw"]
            self.assertFalse(
                any(
                    isinstance(node, ast.Name) and node.id == trigger_name
                    for node in ast.walk(upload)
                ),
                f"{module_name} 每轮 DirectAW 上传不得重启 {trigger_name}",
            )
            burst_on = device_calls(
                "upload_direct_aw", "dg_comp", "set_burst_state", True
            )
            output_calls = device_calls("upload_direct_aw", "dg_comp", "set_output")
            self.assertTrue(burst_on)
            self.assertTrue(output_calls)
            self.assertLess(burst_on[-1].lineno, output_calls[-1].lineno)

            trigger_output_on = device_calls(
                configure_name, trigger_name, "set_output", True
            )
            phase_init = device_calls(configure_name, trigger_name, "phase_init")
            self.assertEqual(len(trigger_output_on), 1)
            self.assertEqual(len(phase_init), 1)
            self.assertLess(trigger_output_on[0].lineno, phase_init[0].lineno)

        assert_trigger_sequence(
            "xy_direct_aw_dc_calibration", "dg_trigger", "configure_trigger_source"
        )
        assert_trigger_sequence(
            "noise_spectrum_xy", "dg_am", "configure_dg_am_reference_trigger"
        )

    def test_typed_direct_aw_workflows_use_shared_phase_steps(self):
        root = Path(__file__).resolve().parents[2]
        for module_name in (
            "xy_direct_aw_dc_calibration",
            "noise_spectrum_xy",
            "rf_sensitivity_direct_aw_frequency",
        ):
            workflow = (
                root
                / "lab_workflows"
                / "experiment_modules"
                / module_name
                / "workflow.py"
            )
            tree = ast.parse(
                workflow.read_text(encoding="utf-8"), filename=str(workflow)
            )
            call_names = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            attribute_calls = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            self.assertIn("calibrate_demod_phase", call_names, module_name)
            self.assertIn("calibrate_direct_aw_phase", call_names, module_name)
            self.assertNotIn("auto_calibrate_phase", attribute_calls, module_name)

    def test_phase_minimum_r_guards_are_advanced_parameters(self):
        expected = {
            "xy-direct-aw-dc-calibration": (
                "XY_PHASE_CAL_MIN_R_V",
                "XY_PHASE_CAL_MIN_R_RATIO",
            ),
            "noise-spectrum-xy": (
                "XY_PHASE_CAL_MIN_R_V",
                "XY_PHASE_CAL_MIN_R_RATIO",
            ),
            "rf-sensitivity-direct-aw-frequency": (
                "XY_CTRL_PHASE_MIN_R_V",
                "XY_CTRL_PHASE_MIN_R_RATIO",
            ),
        }
        for experiment_id, (absolute_name, ratio_name) in expected.items():
            fields = {
                item["name"]: item
                for item in get_experiment(experiment_id).schema()["fields"]
            }
            self.assertEqual(fields[absolute_name]["default"], 1e-12)
            self.assertEqual(fields[absolute_name]["group"], "advanced")
            self.assertEqual(fields[ratio_name]["default"], 0.1)
            self.assertEqual(fields[ratio_name]["group"], "advanced")
            self.assertEqual(fields[ratio_name]["minimum"], 0)
            self.assertEqual(fields[ratio_name]["maximum"], 1)

    def test_typed_params_keep_external_aliases_and_reject_unknown_keys(self):
        params = XYDirectAWDCCalibrationParams.from_external({
            "XY_ENV_VOLTAGE_START_V": -1.0,
            "PUMP_MOD_FREQ_Hz": 15000.0,
            "FIXED_PARAMS.Pump_laser_power": 0.4,
        })
        self.assertEqual(params.xy_env_voltage_start_v, -1.0)
        self.assertEqual(params.pump_mod_freq_hz, 15000.0)
        self.assertEqual(params.pump_laser_power, 0.4)
        self.assertEqual(params.to_external()["XY_ENV_VOLTAGE_START_V"], -1.0)
        with self.assertRaisesRegex(ValueError, "未知参数"):
            XYDirectAWDCCalibrationParams.from_external({"UNDECLARED": 1})

    def test_runtime_aliases_include_nested_fixed_parameters(self):
        namespace = {"FIXED_PARAMS": {"preserved": 1}}
        params = NoiseSpectrumXYParams.from_external({
            "TARGET_NOISE_FREQ_POINTS": 12,
            "FIXED_PARAMS.temperature": 95.0,
        })
        apply_runtime_params(namespace, params)
        self.assertEqual(namespace["TARGET_NOISE_FREQ_POINTS"], 12)
        self.assertEqual(namespace["FIXED_PARAMS"]["temperature"], 95.0)
        self.assertEqual(namespace["FIXED_PARAMS"]["preserved"], 1)

    def test_migrated_gui_schema_is_not_derived_from_workflow_constants(self):
        root = Path(__file__).resolve().parents[2]
        expected = {
            "noise-spectrum-xy": NoiseSpectrumXYParams.external_names(),
            "rf-sensitivity-direct-aw-frequency": RFDirectAWFrequencyParams.external_names(),
            "xy-direct-aw-dc-calibration": XYDirectAWDCCalibrationParams.external_names(),
        }
        for experiment_id, aliases in expected.items():
            schema_names = {
                field["name"] for field in get_experiment(experiment_id).schema()["fields"]
            }
            self.assertEqual(schema_names, set(aliases))
            program = root / get_experiment(experiment_id).acquisition_program
            self.assertLess(len(program.read_text(encoding="utf-8").splitlines()), 15)

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

    def test_shared_rf_plot_entry_keeps_legacy_run_dir_override(self):
        root = Path(__file__).resolve().parents[2]
        script = root / "experiments" / "RF_Field_Sensitivity_AW_FreqSweep_plot.py"
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        tree = OverrideTransformer({"USE_LATEST", "DATA_DIR"}).visit(tree)
        ast.fix_missing_locations(tree)
        selected = root / "data" / "legacy-run"
        namespace = {
            "__name__": "legacy_plot_test",
            "__file__": str(script),
            "__LAB_OVERRIDES": {"USE_LATEST": False, "DATA_DIR": selected},
            "__LAB_OVERRIDE_APPLIED": set(),
        }
        exec(compile(tree, str(script), "exec"), namespace)
        with patch.object(namespace["ADAPTER"], "analyze_cli", return_value=0) as analyze:
            self.assertEqual(namespace["main"](), 0)
        analyze.assert_called_once_with(selected)

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
