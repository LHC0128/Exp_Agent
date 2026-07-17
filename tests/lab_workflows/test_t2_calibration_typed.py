"""T2 标定强类型迁移契约测试。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from sds_acquisition import AcquisitionConfig

from lab_workflows.common import WorkflowCancelled
from lab_workflows.experiment_modules.t2_calibration import ADAPTER, T2CalibrationParams
from lab_workflows.experiment_modules.t2_calibration import analysis, workflow
from lab_workflows.experiments import get_experiment
from lab_workflows.experiments.typed import TypedWorkflowAdapter


class RecordingDevice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return record


class T2CalibrationTypedTests(unittest.TestCase):
    def test_definition_is_typed_and_keeps_stable_data_type(self):
        definition = get_experiment("t2-calibration")
        self.assertEqual(definition.execution_mode, "typed_workflow")
        self.assertEqual(definition.data_type, "T2_Calibration")
        self.assertIsInstance(definition.schema_provider.__self__, TypedWorkflowAdapter)
        self.assertEqual(definition.schema_version, 2)

    def test_external_keys_are_compatible_and_unknown_keys_are_rejected(self):
        params = T2CalibrationParams.from_external({
            "PUMP_POWER": 0.2,
            "MAIN_FIELD_mA": 8.5,
            "RF_GATE_FREQ": 85000,
            "HF2_OSC_FREQ": 85000,
        })
        self.assertEqual(params.pump_power, 0.2)
        self.assertEqual(params.main_field_ma, 8.5)
        self.assertEqual(params.to_external()["MAIN_FIELD_mA"], 8.5)
        with self.assertRaisesRegex(ValueError, "未知参数"):
            T2CalibrationParams.from_external({"UNDECLARED_T2_PARAMETER": 1})

    def test_fixed_params_aliases_map_to_historical_t2_keys(self):
        params = T2CalibrationParams.from_external({
            "FIXED_PARAMS.Pump_laser_power": 0.2,
            "FIXED_PARAMS.Probe_laser_power": 0.15,
            "FIXED_PARAMS.main_magnetic_field": 8.8,
            "FIXED_PARAMS.temperature": 95.0,
        })
        self.assertEqual(params.pump_power, 0.2)
        self.assertEqual(params.probe_power, 0.15)
        self.assertEqual(params.main_field_ma, 8.8)
        self.assertEqual(params.tec_temperature, 95.0)
        self.assertNotIn("FIXED_PARAMS.temperature", params.to_external())
        self.assertEqual(
            get_experiment("t2-calibration").preflight({"FIXED_PARAMS.temperature": 95.0}),
            [],
        )

    def test_gui_schema_only_contains_explicit_visible_fields(self):
        fields = {field["name"] for field in get_experiment("t2-calibration").schema()["fields"]}
        expected = {
            "RUN_TAG", "PUMP_POWER", "AOM_CARRIER_AMPLITUDE", "RF_GATE_FREQ",
            "RF_GATE_DUTY", "BURST_NCYCLES", "BURST_PERIOD", "DO_POWER_SCAN",
            "PROBE_POWER", "PROBE_POWER_START", "PROBE_POWER_STOP", "PROBE_POWER_POINTS",
            "MAIN_FIELD_mA", "SCOPE_SAMPLE_RATE",
            "SCOPE_DURATION", "ACQ_REPEATS", "HF2_SIGNAL_RANGE", "HF2_DEMOD_TC",
            "HF2_DEMOD_ORDER", "HF2_DEMOD_RATE", "TEC_TEMPERATURE",
        }
        self.assertEqual(fields, expected)
        self.assertNotIn("AOM_CARRIER_FREQ", fields)
        self.assertNotIn("SCOPE_PD_CHANNEL", fields)
        self.assertNotIn("VERT_DIVS", fields)
        source = Path(get_experiment("t2-calibration").acquisition_program).read_text(encoding="utf-8")
        self.assertLess(len(source.splitlines()), 15)

    def test_default_yaml_load_save_and_preflight(self):
        defaults = ADAPTER.defaults()
        self.assertEqual(defaults.probe_power_values[0], defaults.probe_power_start)
        self.assertAlmostEqual(defaults.probe_power_values[-1], defaults.probe_power_stop)
        self.assertEqual(len(defaults.probe_power_values), defaults.probe_power_points)
        self.assertEqual(defaults.scope_trig_slope, "FALLing")
        self.assertEqual(defaults.validate(), [])
        self.assertEqual(get_experiment("t2-calibration").preflight({}), [])
        path = Path.cwd() / "tests" / ".t2_calibration_defaults_test.yaml"
        try:
            defaults.save_yaml(path)
            restored = T2CalibrationParams.from_yaml(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(restored.to_external(), defaults.to_external())

    def test_cross_parameter_preflight_blocks_conflicts(self):
        definition = get_experiment("t2-calibration")
        defaults = ADAPTER.defaults()
        self.assertTrue(any("BURST_PERIOD" in item for item in definition.preflight({"BURST_PERIOD": 0.01})))
        self.assertEqual(definition.preflight({"RF_GATE_FREQ": 80000}), [])
        self.assertTrue(any(
            "两倍" in item
            for item in definition.preflight({"SCOPE_SAMPLE_RATE": 2 * defaults.rf_gate_freq})
        ))
        self.assertTrue(any("Probe_laser_power" in item for item in definition.preflight({"PROBE_POWER_LIST": [0.1, 2.0]})))

    def test_legacy_probe_power_list_migrates_or_preserves_exact_values(self):
        uniform = T2CalibrationParams.from_external({"PROBE_POWER_LIST": [0.01, 0.02, 0.03]})
        self.assertEqual(uniform.probe_power_start, 0.01)
        self.assertEqual(uniform.probe_power_stop, 0.03)
        self.assertEqual(uniform.probe_power_points, 3)
        self.assertEqual(uniform.probe_power_list_compat, [])
        nonuniform = T2CalibrationParams.from_external({"PROBE_POWER_LIST": [0.01, 0.025, 0.1]})
        self.assertEqual(nonuniform.probe_power_values, [0.01, 0.025, 0.1])

    def test_hf2_oscillator_frequency_is_derived_from_rf_gate(self):
        params = T2CalibrationParams.from_external({
            "RF_GATE_FREQ": 85000.0,
            "HF2_OSC_FREQ": 70000.0,
        })
        self.assertEqual(params.hf2_osc_freq, 85000.0)
        self.assertEqual(params.to_external()["HF2_OSC_FREQ"], 85000.0)

    def test_cancellation_checkpoint_raises_without_hardware(self):
        with patch.object(workflow, "check_cancelled", side_effect=WorkflowCancelled("取消")):
            with self.assertRaises(WorkflowCancelled):
                workflow._RuntimeCancellation.raise_if_cancelled()

    def test_auto_range_state_continues_across_probe_power_points(self):
        params = ADAPTER.defaults()
        params.acq_repeats = 1
        time_axis = np.linspace(-1.0e-3, 1.0e-3, 16)

        class FakeScope(RecordingDevice):
            def wait_for_trigger(self, timeout: float) -> bool:
                self.calls.append(("wait_for_trigger", (), {"timeout": timeout}))
                return True

        class FakeAcquirer:
            def acquire_channel(self, *_args, **_kwargs):
                return SimpleNamespace(
                    voltage=np.full(time_axis.shape, 0.01),
                    time=time_axis,
                )

        devices = {
            "laser": RecordingDevice(),
            "scope": FakeScope(),
            "acquirer": FakeAcquirer(),
            "temp_switch": RecordingDevice(),
        }
        channels = {"probe": 2, "temp_switch": 2}
        scope_config = AcquisitionConfig(acquire_delay=0.0)
        auto_range = workflow._AutoRangeState(scale=0.5)

        with (
            patch.object(workflow, "check_cancelled"),
            patch.object(workflow, "_sleep"),
        ):
            workflow._acquire_fid(
                params, 0.01, devices, channels, scope_config, auto_range
            )
            first_final_scale = auto_range.scale
            workflow._acquire_fid(
                params, 0.02, devices, channels, scope_config, auto_range
            )

        self.assertEqual(first_final_scale, 0.0625)
        self.assertEqual(auto_range.scale, 0.015625)

    def test_acquisition_reads_full_record_before_selecting_post_trigger_fid(self):
        params = ADAPTER.defaults()
        params.acq_repeats = 1
        time_axis = np.arange(100000, dtype=float) * 0.5e-6 - 0.025
        voltage = np.where(
            time_axis < 0,
            np.cos(2 * np.pi * params.rf_gate_freq * time_axis),
            np.exp(-time_axis / 0.003)
            * np.cos(2 * np.pi * params.rf_gate_freq * time_axis),
        )

        class FakeScope(RecordingDevice):
            def wait_for_trigger(self, timeout: float) -> bool:
                self.calls.append(("wait_for_trigger", (), {"timeout": timeout}))
                return True

        class FakeAcquirer:
            def __init__(self) -> None:
                self.trim_points: list[int] = []

            def acquire_channel(self, *_args, **kwargs):
                self.trim_points.append(kwargs["trim_points"])
                return SimpleNamespace(voltage=voltage, time=time_axis)

        acquirer = FakeAcquirer()
        devices = {
            "laser": RecordingDevice(),
            "scope": FakeScope(),
            "acquirer": acquirer,
            "temp_switch": RecordingDevice(),
        }
        channels = {"probe": 2, "temp_switch": 2}
        scope_config = AcquisitionConfig(acquire_delay=0.0)
        auto_range = workflow._AutoRangeState(scale=0.5)

        with (
            patch.object(workflow, "check_cancelled"),
            patch.object(workflow, "_sleep"),
        ):
            fid_time, fid_waveforms, average, _scales = workflow._acquire_fid(
                params, 0.01, devices, channels, scope_config, auto_range
            )

        self.assertEqual(acquirer.trim_points, [0])
        self.assertEqual(len(fid_time), 50000)
        self.assertEqual(fid_waveforms.shape, (1, 50000))
        self.assertGreaterEqual(fid_time[0], 0.0)
        self.assertAlmostEqual(fid_time[-1], 0.0249995)
        np.testing.assert_allclose(average, voltage[-50000:])

    def test_safe_shutdown_matches_reference_and_preserves_fixed_outputs(self):
        names = (
            "temp_switch", "rf_switch", "laser", "gs200", "x_field", "y_field",
            "z_field", "scope", "tec",
        )
        devices = {name: RecordingDevice() for name in names}
        channels = {
            "temp_switch": 2,
            "rf_gate": 2,
            "aom_carrier": 1,
            "pump": 1,
            "probe": 2,
            "x_field": 1,
            "y_field": 2,
            "z_field": 1,
            "time_sequence_2": 2,
        }
        shutdown_report = workflow.safe_shutdown(devices, channels, ADAPTER.defaults())
        self.assertTrue(shutdown_report.completed)
        self.assertIn(("setup_dc", (5.0,), {"channel": 2}), devices["temp_switch"].calls)
        self.assertIn(("set_burst_state", (False,), {"channel": 2}), devices["rf_switch"].calls)
        self.assertIn(("set_output", (True,), {"channel": 2}), devices["rf_switch"].calls)
        self.assertNotIn(("set_sync_state", (False,), {"channel": 2}), devices["rf_switch"].calls)
        self.assertNotIn(("set_mod_state", (False,), {"channel": 2}), devices["rf_switch"].calls)
        self.assertNotIn(("setup_dc", (0.0,), {"channel": 2}), devices["rf_switch"].calls)
        self.assertFalse(any(
            kwargs.get("channel") == 1
            for _name, _args, kwargs in devices["rf_switch"].calls
        ))
        self.assertEqual(devices["laser"].calls, [])
        self.assertEqual(devices["gs200"].calls, [])
        for name in ("x_field", "y_field", "z_field"):
            self.assertIn(("set_burst_state", (False,), {"channel": channels[name]}), devices[name].calls)
            self.assertIn(("set_mod_state", (False,), {"channel": channels[name]}), devices[name].calls)
            self.assertIn(("setup_dc", (0.0,), {"channel": channels[name]}), devices[name].calls)
            self.assertIn(("set_output", (False,), {"channel": channels[name]}), devices[name].calls)
        self.assertIn(("setup_dc", (0.0,), {"channel": 2}), devices["z_field"].calls)
        self.assertIn(("trigger_stop", (), {}), devices["scope"].calls)
        self.assertIn(("disconnect", (), {}), devices["tec"].calls)
        self.assertEqual(
            shutdown_report.preserved_outputs,
            (
                "main_magnetic_field",
                "Pump_laser_power",
                "Probe_laser_power",
                "Pump_modulation",
                "HF2_configuration",
                "Time_sequence",
            ),
        )

    def test_run_has_finally_guard_for_safe_shutdown(self):
        tree = ast.parse(Path(workflow.__file__).read_text(encoding="utf-8"))
        run_function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run")
        final_calls = {
            node.func.id
            for statement in ast.walk(run_function)
            if isinstance(statement, ast.Try)
            for node in ast.walk(ast.Module(body=statement.finalbody, type_ignores=[]))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("safe_shutdown", final_calls)
        final_attributes = {
            node.attr
            for statement in ast.walk(run_function)
            if isinstance(statement, ast.Try)
            for node in ast.walk(ast.Module(body=statement.finalbody, type_ignores=[]))
            if isinstance(node, ast.Attribute)
        }
        self.assertIn("to_dict", final_attributes)
        self.assertNotIn("cleanup_connection_failure", final_attributes)

    def test_historical_run_dir_without_new_config_is_analyzable(self):
        run_dir = Path.cwd() / "tests" / "fixtures" / "t2_historical_run"
        raw_dir = run_dir / "raw"
        with patch.object(analysis, "_analyze_single", return_value={"artifacts": []}) as analyze_single:
            result = analysis.analyze(run_dir)
        self.assertEqual(result, {"artifacts": []})
        analyze_single.assert_called_once_with(raw_dir.resolve(), (run_dir / "results").resolve())

    def test_offline_fit_recovers_synthetic_t2(self):
        time_s = np.arange(0.0, 0.012, 1.0e-6)
        waveform = analysis.damped_oscillation(time_s, 0.2, 0.003, 90000.0, 0.2, 0.01)
        t2_ms, uncertainty_ms, optimum, success = analysis.fit_t2(time_s, waveform, 90000.0)
        self.assertTrue(success)
        self.assertAlmostEqual(t2_ms, 3.0, places=2)
        self.assertLess(uncertainty_ms, 0.01)
        self.assertAlmostEqual(float(optimum[2]), 90000.0, places=1)

    def test_offline_fit_rejects_nondecaying_carrier(self):
        time_s = np.arange(0.0, 0.012, 1.0e-6)
        waveform = 0.2 * np.cos(2 * np.pi * 90000.0 * time_s + 0.2) + 0.01
        result = analysis.fit_t2_diagnostics(time_s, waveform, 90000.0)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "no_measurable_decay")
        self.assertTrue(np.isnan(result.t2_ms))
        self.assertGreater(result.envelope_tail_ratio, 0.99)


if __name__ == "__main__":
    unittest.main()
