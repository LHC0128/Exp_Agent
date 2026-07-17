import unittest
import subprocess
import sys
import math
from pathlib import Path

from lab_workflows.clock_sync import load_clock_profile
from lab_workflows.devices import (
    create_signal_generator,
    discover_devices,
    signal_generator_max_arb_points,
)
from signal_generator import DG4000Instrument, DG900Instrument
from lab_workflows.static_sensitivity import (
    StaticSensitivityParams,
    preflight_static_sensitivity,
)
from lab_workflows.static_sensitivity.workflow import _build_subprocess_environment
from lab_workflows.steps import (
    DGChannelShutdown,
    DirectAWPhaseCalibrationConfig,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    ShutdownAction,
    TemperatureSwitchRestore,
    calibrate_direct_aw_phase,
    disconnect_device_mapping,
    run_safety_shutdown,
    synchronize_connected_clocks,
)


class RecordingSafetyDevice:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == self.fail_on:
                raise RuntimeError("模拟失败")
        return record


class SharedWorkflowTests(unittest.TestCase):
    def test_standard_preserved_state_keeps_hf2_unchanged(self):
        self.assertEqual(
            STANDARD_PRESERVED_OUTPUTS,
            (
                "main_magnetic_field",
                "Pump_laser_power",
                "Probe_laser_power",
                "Pump_modulation",
                "HF2_configuration",
            ),
        )

    def test_shared_safety_shutdown_executes_declared_policy(self):
        dg = RecordingSafetyDevice()
        temp = RecordingSafetyDevice()
        scope = RecordingSafetyDevice()
        tec = RecordingSafetyDevice()
        report = run_safety_shutdown(
            dg_channels=(
                DGChannelShutdown(
                    dg, 2, "Time_sequence", "门控", disable_sync=True
                ),
            ),
            temperature_switch=TemperatureSwitchRestore(temp, 2),
            extra_actions=(ShutdownAction("停止示波器失败", scope.trigger_stop),),
            disconnect_targets=(DisconnectTarget("TEC", tec),),
            preserved_outputs=("main_magnetic_field", "Pump_laser_power"),
        )
        self.assertTrue(report.completed)
        self.assertIn(("set_burst_state", (False,), {"channel": 2}), dg.calls)
        self.assertIn(("set_sync_state", (False,), {"channel": 2}), dg.calls)
        self.assertIn(("set_mod_state", (False,), {"channel": 2}), dg.calls)
        self.assertIn(("setup_dc", (0.0,), {"channel": 2}), dg.calls)
        self.assertIn(("set_output", (False,), {"channel": 2}), dg.calls)
        self.assertIn(("setup_dc", (5.0,), {"channel": 2}), temp.calls)
        self.assertIn(("set_output", (True,), {"channel": 2}), temp.calls)
        self.assertIn(("trigger_stop", (), {}), scope.calls)
        self.assertIn(("disconnect", (), {}), tec.calls)
        self.assertEqual(
            report.to_dict()["preserved_outputs"],
            ["main_magnetic_field", "Pump_laser_power"],
        )

    def test_shared_safety_shutdown_continues_after_one_action_fails(self):
        dg = RecordingSafetyDevice(fail_on="set_burst_state")
        report = run_safety_shutdown(
            dg_channels=(DGChannelShutdown(dg, 1, "Z_magnetic_field", "Z 场"),)
        )
        self.assertFalse(report.completed)
        self.assertTrue(any("Burst" in error for error in report.errors))
        self.assertIn(("setup_dc", (0.0,), {"channel": 1}), dg.calls)
        self.assertIn(("set_output", (False,), {"channel": 1}), dg.calls)

    def test_connection_failure_disconnect_deduplicates_devices(self):
        device = RecordingSafetyDevice()
        errors = disconnect_device_mapping({"channel_1": device, "channel_2": device})
        self.assertEqual(errors, ())
        self.assertEqual(device.calls, [("disconnect", (), {})])

    def test_all_hardware_workflows_use_shared_safety_shutdown(self):
        workflows = (
            "noise_spectrum_xy",
            "rf_sensitivity_direct_aw_frequency",
            "t2_calibration",
            "xy_direct_aw_dc_calibration",
        )
        for module_name in workflows:
            source = (
                Path("lab_workflows")
                / "experiment_modules"
                / module_name
                / "workflow.py"
            ).read_text(encoding="utf-8")
            with self.subTest(module_name=module_name):
                self.assertIn("run_safety_shutdown", source)
                self.assertIn("preserved_outputs=", source)
                self.assertNotIn(".disconnect()", source)

    def test_all_typed_workflows_use_shared_clock_sync(self):
        workflows = (
            "mx_y_rf_sensitivity",
            "noise_spectrum_xy",
            "noise_spectrum_xy_demod3_r",
            "rf_sensitivity_direct_aw_frequency",
            "t2_calibration",
            "xy_direct_aw_dc_calibration",
        )
        for module_name in workflows:
            source = (
                Path("lab_workflows")
                / "experiment_modules"
                / module_name
                / "workflow.py"
            ).read_text(encoding="utf-8")
            with self.subTest(module_name=module_name):
                self.assertIn("synchronize_connected_clocks", source)
                self.assertNotIn(".set_ref_clock_source(", source)
                self.assertNotIn(".set_extclk(", source)

    def test_direct_aw_phase_calibration_converges_with_fixed_minus_update(self):
        samples = iter(((-30.0, 1.0), (-10.0, 0.9), (0.2, 0.8)))
        measured_phases = []

        def apply_and_measure(phase_deg):
            measured_phases.append(phase_deg)
            theta_deg, r_v = next(samples)
            return {"theta": math.radians(theta_deg), "r": r_v}

        result = calibrate_direct_aw_phase(
            0.0,
            apply_and_measure,
            DirectAWPhaseCalibrationConfig(
                tolerance_deg=0.5,
                max_measurements=3,
            ),
        )
        self.assertTrue(result.converged)
        self.assertEqual(measured_phases, [0.0, 30.0, 40.0])
        self.assertAlmostEqual(result.final_phase_deg, 40.0)

    def test_direct_aw_phase_calibration_rejects_low_r_without_updating(self):
        samples = iter(((-30.0, 1.0), (-120.0, 0.01), (0.1, 0.8)))
        measured_phases = []

        def apply_and_measure(phase_deg):
            measured_phases.append(phase_deg)
            theta_deg, r_v = next(samples)
            return {"theta": math.radians(theta_deg), "r": r_v}

        result = calibrate_direct_aw_phase(
            0.0,
            apply_and_measure,
            DirectAWPhaseCalibrationConfig(
                tolerance_deg=0.5,
                max_measurements=3,
                minimum_r_ratio=0.1,
            ),
        )
        self.assertTrue(result.converged)
        self.assertEqual(measured_phases, [0.0, 30.0, 30.0])
        self.assertFalse(result.history[1].accepted)
        self.assertEqual(result.history[1].mode, "low_r_rejected")

    def test_direct_aw_phase_failure_keeps_last_measured_phase(self):
        samples = iter(((-30.0, 1.0), (-10.0, 1.0)))

        def apply_and_measure(_phase_deg):
            theta_deg, r_v = next(samples)
            return {"theta": math.radians(theta_deg), "r": r_v}

        result = calibrate_direct_aw_phase(
            0.0,
            apply_and_measure,
            DirectAWPhaseCalibrationConfig(
                tolerance_deg=0.5,
                max_measurements=2,
            ),
        )
        self.assertFalse(result.converged)
        self.assertAlmostEqual(result.final_phase_deg, 30.0)
    def test_device_discovery_groups_physical_devices(self):
        devices = discover_devices()
        self.assertEqual(len(devices), 7)
        keys = {
            channel.mapping_key
            for device in devices
            for channel in device.channels
        }
        self.assertNotIn("rf_coil", keys)
        self.assertIn("Y_magnetic_field", keys)
        heat = next(
            channel
            for device in devices
            for channel in device.channels
            if channel.mapping_key == "Heat_Control"
        )
        self.assertFalse(heat.read_only)
        labels = {device.id: device.label for device in devices}
        self.assertEqual(labels["DG9Q280100002"], "光功率")
        self.assertEqual(labels["DG4E231500376"], "XY方向AM信号")
        self.assertEqual(labels["DG4E234902522"], "XY方向磁场")

        types = {device.id: device.type for device in devices}
        self.assertEqual(types['DG9Q280100002'], 'DG900')
        self.assertEqual(types['DG4E231500376'], 'DG4000')

    def test_signal_generator_factory_uses_explicit_model(self):
        dg900 = create_signal_generator({
            'instrument': 'signal_generator',
            'model': 'DG900',
            'resource': 'RESOURCE-WITHOUT-MODEL-HINT',
            'channel': 2,
        })
        dg4000 = create_signal_generator({
            'instrument': 'signal_generator',
            'model': 'DG4000',
            'resource': 'ANOTHER-RESOURCE',
            'channel': 1,
        })
        self.assertIsInstance(dg900, DG900Instrument)
        self.assertEqual(dg900.channel, 2)
        self.assertIsInstance(dg4000, DG4000Instrument)
        self.assertEqual(
            signal_generator_max_arb_points({
                'instrument': 'signal_generator',
                'model': 'DG4000',
                'resource': 'ANOTHER-RESOURCE',
                'channel': 1,
            }),
            DG4000Instrument.MAX_ARB_POINTS,
        )

    def test_signal_generator_factory_requires_model(self):
        with self.assertRaises(ValueError):
            create_signal_generator({
                'instrument': 'signal_generator',
                'resource': 'FAKE',
                'channel': 1,
            })

    def test_static_defaults_pass_preflight(self):
        params = StaticSensitivityParams.from_yaml()
        self.assertIsInstance(params.main_magnetic_field, float)
        self.assertEqual(preflight_static_sensitivity(params), [])
        self.assertGreater(len(StaticSensitivityParams.schema(params)["fields"]), 30)

    def test_invalid_static_range_is_rejected(self):
        params = StaticSensitivityParams.from_yaml()
        params.ramp_low = 1
        params.ramp_high = -1
        self.assertIn("Z 扫场下限必须小于上限", preflight_static_sensitivity(params))

    def test_static_defaults_round_trip_yaml(self):
        params = StaticSensitivityParams.from_yaml()
        params.run_tag = "saved-default"
        path = Path.cwd() / "tests" / ".static_sensitivity_defaults_test.yaml"
        try:
            params.to_yaml(path)
            restored = StaticSensitivityParams.from_yaml(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(restored.run_tag, "saved-default")
        self.assertEqual(restored.gs200_current_ranges, params.gs200_current_ranges)

    def test_static_subprocess_uses_utf8_output(self):
        params = StaticSensitivityParams.from_yaml()
        environment = _build_subprocess_environment(
            params, Path.cwd() / "data" / ".test_cancel"
        )
        completed = subprocess.run(
            [sys.executable, "-c", 'print("实验输出\\u2705")'],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(completed.stdout.strip(), "实验输出\u2705")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(environment["PYTHONUTF8"], "1")

    def test_clock_profile_preserves_internal_pump_clock(self):
        profile = load_clock_profile()
        self.assertEqual(profile["DG4E222800868"], "INT")
        self.assertEqual(profile["DG9Q280100002"], "EXT")
        self.assertEqual(profile["DG9Q271200104"], "EXT")
        self.assertEqual(profile["dev18246"], "EXT")

    def test_connected_clock_sync_sets_dg900_and_hf2_from_profile(self):
        class FakeDG:
            def __init__(self):
                self.source = "INT"

            def get_ref_clock_source(self):
                return self.source

            def set_ref_clock_source(self, source):
                self.source = "EXT" if source.upper().startswith("EXT") else "INT"

        class FakeHF2:
            def __init__(self):
                self.external = False

            def get_extclk(self):
                return self.external

            def set_extclk(self, enabled):
                self.external = bool(enabled)

        dg900 = FakeDG()
        pump = FakeDG()
        hf2 = FakeHF2()
        mapping = {
            "Pump_laser_power": {
                "instrument": "signal_generator",
                "model": "DG900",
                "resource": "USB0::VENDOR::MODEL::LASER::INSTR",
            },
            "Pump_modulation": {
                "instrument": "signal_generator",
                "model": "DG4000",
                "resource": "USB0::VENDOR::MODEL::PUMP::INSTR",
            },
            "lockin_r": {
                "instrument": "lockin_amplifier",
                "device_id": "HF2",
            },
        }
        result = synchronize_connected_clocks(
            {"laser": dg900, "pump": pump, "hf2": hf2},
            mapping,
            {
                "laser": "Pump_laser_power",
                "pump": "Pump_modulation",
                "hf2": "lockin_r",
            },
            profile={
                "__default__": "EXT",
                "LASER": "EXT",
                "PUMP": "INT",
                "HF2": "EXT",
            },
            settle_s=0,
        )
        self.assertEqual(result["laser"]["actual"], "EXT")
        self.assertEqual(result["pump"]["actual"], "INT")
        self.assertEqual(result["hf2"]["actual"], "EXT")

    def test_connected_clock_sync_stops_on_readback_mismatch(self):
        class StuckDG:
            def get_ref_clock_source(self):
                return "INT"

            def set_ref_clock_source(self, _source):
                pass

        with self.assertRaisesRegex(RuntimeError, "clock 时钟源设置失败"):
            synchronize_connected_clocks(
                {"clock": StuckDG()},
                {
                    "field": {
                        "instrument": "signal_generator",
                        "model": "DG4000",
                        "resource": "USB0::VENDOR::MODEL::STUCK::INSTR",
                    }
                },
                {"clock": "field"},
                profile={"__default__": "EXT", "STUCK": "EXT"},
                settle_s=0,
            )


if __name__ == "__main__":
    unittest.main()
