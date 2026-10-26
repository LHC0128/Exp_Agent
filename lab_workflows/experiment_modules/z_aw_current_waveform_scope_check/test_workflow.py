"""实际电流业务配假仪器；保留真实配置、触发、保存和安全收尾。"""
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import call, patch

import numpy as np
import yaml

from ...common import WorkflowCancelled
from ..z_aw_waveform_scope_check import acquisition, workflow as scope_workflow
from ..z_aw_waveform_scope_check.test_workflow import (
    ROOT, copy_config as copy_voltage_config, fake_hardware as voltage_hardware,
)
from . import models, preparation, workflow
from .stages import ACQUISITION, definition

ACQUISITION_CASES = {
    "z-aw-current-waveform-scope-check": {
        "mode": "single",
        "cases": {
            "order": ["CurrentWorkflowTests::test_full_staged_sources_and_repeat_keep_real_protocol"],
            "slow_disk": ["CurrentWorkflowTests::test_slow_disk_cancel_preserves_first_frame"],
            "cancel": ["CurrentWorkflowTests::test_slow_disk_cancel_preserves_first_frame"],
            "acquire_failure": ["CurrentWorkflowTests::test_read_failure_and_restore_failure_keep_original_error_and_partial_data"],
            "restore_failure": ["CurrentWorkflowTests::test_read_failure_and_restore_failure_keep_original_error_and_partial_data"],
            "save_failure": ["CurrentWorkflowTests::test_source_drift_copy_hash_and_save_failure_stop_capture"],
            "partial_save": ["CurrentWorkflowTests::test_read_failure_and_restore_failure_keep_original_error_and_partial_data"],
            "entrypoints": ["CurrentWorkflowTests::test_full_staged_sources_and_repeat_keep_real_protocol"],
        },
        "sources": ["lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/workflow.py",
                    "lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/preparation.py"],
    },
}


def copy_config(root):
    copy_voltage_config(root)
    directory = root / "data/Mx_Z_Current_Coupling_Calibration/fake_current/results"
    directory.mkdir(parents=True)
    (directory / "analysis.yaml").write_text(yaml.safe_dump(dict(
        experiment_id="mx-z-current-coupling-calibration", success=True,
        K_Z_Hz_per_A=100000, K_Z_Hz_per_Vsense=10000, f_0_at_zero_current_hz=0,
        sense_resistor_ohm=10, linearity=dict(r_squared=1))), encoding="utf-8")
    frozen = root / "data/Z_AW_Closed_Loop_Waveform_Correction/fake_corrected/results"
    frozen.mkdir(parents=True)
    t = np.arange(64) / 64000
    omega = 300 * np.sin(2 * np.pi * 1000 * t)
    # 冻结目标可以独立于旧 Omega/KZ；采集必须保留文件中的目标数组。
    np.savez(frozen / "corrected_control_waveform.npz", format_version=2,
        time_s=t, omega_ctrl_hz=omega, target_current_a=omega / 100000 * .9,
        normalized=omega / 1000 / 3, voltage_v=omega / 1000,
        repeat_frequency_hz=1000, amplitude_vpp=6, offset_v=0,
        coupling_calibration_run="fake_current", coupling_calibration_sha256=hashlib.sha256(
            (directory / "analysis.yaml").read_bytes()).hexdigest())


def scan_params(root, *, source="theory"):
    return models.ZAWCurrentWaveformScopeCheckParams(
        control_results_root=str(root / "control"), control_version="v1", run_tag="fake",
        command_z_calibration_source_run="fake_cal", z_calibration_source_run="unused_compatibility_key",
        current_coupling_calibration_source_run="fake_current", sense_resistor_ohm=10,
        sense_resistor_power_rating_w=1, maximum_current_a=.1, scope_repeats=2,
        scope_initial_scale_v_div=.2, control_waveform_source=source,
        corrected_control_source_run="fake_corrected")


@contextmanager
def fake_hardware(root):
    with voltage_hardware(root) as hw, patch.object(models, "find_project_root", return_value=root):
        yield hw


class CurrentWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="z-current-workflow-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.params = scan_params(self.root)

    def test_prepared_source_hook_and_failed_setup_keep_state_and_shutdown_contract(self):
        for source in ("theory", "corrected_run"):
            with self.subTest(source=source), fake_hardware(self.root) as hw:
                params = scan_params(self.root, source=source)
                ctx = workflow.Workflow(params, project_root=self.root)
                files = {path.relative_to(self.root): path.read_bytes()
                    for path in self.root.rglob("*") if path.is_file()}
                prepared = ctx.prepare_control_source()
                self.assertIsInstance(prepared, scope_workflow.ControlSource)
                self.assertIsNone(ctx.source)
                self.assertFalse(ctx.outputs_started)
                self.assertEqual(ctx.devices, {})
                expected = prepared.theory.omega_ctrl_hz / 100000 * (.9 if source == "corrected_run" else 1)
                np.testing.assert_array_equal(prepared.target_current_a, expected)
                self.assertIsNotNone(prepared.applied)
                self.assertEqual(prepared.current_calibration.sense_resistor_ohm, 10)
                self.assertTrue(callable(prepared.source_snapshot_writer))
                self.assertEqual(hw.generator.mock_calls, [])
                self.assertEqual(hw.scope.mock_calls, [])
                self.assertEqual({path.relative_to(self.root): path.read_bytes()
                    for path in self.root.rglob("*") if path.is_file()}, files)
                ctx.execute(["A01", "A02"])
                error = RuntimeError("模拟任意波设置中途失败")

                def partial_setup(actual_params, devices, channels, theory, applied):
                    self.assertIs(actual_params, ctx.params)
                    self.assertIs(ctx.source, prepared)
                    self.assertTrue(ctx.outputs_started)
                    self.assertIs(theory, prepared.theory)
                    self.assertIs(applied, prepared.applied)
                    devices["z_control"].set_output(True, channel=channels["z_field"])
                    raise error

                with patch.object(ctx, "prepare_control_source", return_value=prepared) as prepare, \
                        patch.object(scope_workflow, "_configure_outputs", side_effect=partial_setup):
                    with self.assertRaises(RuntimeError) as caught:
                        ctx.execute(["A03"])
                    self.assertIs(caught.exception, error)
                    prepare.assert_called_once_with()
                ctx.finish(error)
                self.assertEqual(ctx.states["A03"]["status"], "failed")
                self.assertTrue(ctx.shutdown["completed"])
                self.assertEqual([item for item in hw.generator.set_output.call_args_list
                    if item.kwargs["channel"] == 1][-1], call(False, channel=1))
                hw.scope.disconnect.assert_called_once()
        for error in (RuntimeError("模拟准备失败"), WorkflowCancelled("模拟准备取消")):
            with self.subTest(error=type(error).__name__), fake_hardware(self.root) as hw:
                ctx = workflow.Workflow(self.params, project_root=self.root)
                ctx.execute(["A01", "A02"])
                before = list(hw.generator.mock_calls)
                with patch.object(ctx, "prepare_control_source", side_effect=error), \
                        patch.object(scope_workflow, "_configure_outputs") as configure:
                    with self.assertRaises(type(error)) as caught:
                        ctx.execute(["A03"])
                    self.assertIs(caught.exception, error)
                    configure.assert_not_called()
                self.assertIsNone(ctx.source)
                self.assertFalse(ctx.outputs_started)
                self.assertEqual(hw.generator.mock_calls, before)
                ctx.finish(error)
                self.assertTrue(ctx.shutdown["completed"])

    def test_full_staged_sources_and_repeat_keep_real_protocol(self):
        for source in ("theory", "corrected_run"):
            with self.subTest(source=source), fake_hardware(self.root) as hw:
                params = scan_params(self.root, source=source)
                run = workflow.run(params, project_root=self.root)
                ctx = workflow.Workflow(params, project_root=self.root)
                ctx.execute(["A01", "A02", "A03", "A04", "A05"])
                first, before = ctx.run_dir, (ctx.run_dir / "experiment_config.yaml").read_bytes()
                with np.load(run / "raw/applied_control_waveform.npz") as full, np.load(
                        first / "raw/applied_control_waveform.npz") as staged:
                    np.testing.assert_array_equal(full["target_current_a"], staged["target_current_a"])
                    expected = ctx.source.theory.omega_ctrl_hz / 100000 * (.9 if source == "corrected_run" else 1)
                    np.testing.assert_array_equal(full["target_current_a"], expected)
                ctx.execute(["A05"])
                self.assertNotEqual(ctx.run_dir, first)
                self.assertEqual((first / "experiment_config.yaml").read_bytes(), before)
                self.assertTrue((run / "raw/source_current_coupling_analysis.yaml").is_file())
                config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(config["parameters"], params.to_external())
                self.assertEqual(config["measurement_mode"], "current")
                self.assertEqual(config["raw_format_version"], 1)
                self.assertEqual(config["completion_status"], "completed")
                self.assertTrue(config["safety_shutdown"]["completed"])
                self.assertEqual(hw.state["status"], "STOP")
                ctx.finish()
                self.assertEqual([c for c in hw.generator.set_output.call_args_list
                                  if c.kwargs["channel"] == 1][-1], call(False, channel=1))
                self.assertEqual([c for c in hw.generator.set_output.call_args_list
                                  if c.kwargs["channel"] == 2][-1], call(True, channel=2))

    def test_missing_dependencies_and_preflight_never_connect(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            with self.assertRaisesRegex(ValueError, "缺少前置"):
                ctx.execute(["A05"])
            hw.generator.connect.assert_not_called()
            ctx.execute(["A01", "A02"])
            ctx.execute(["A01"])
            self.assertEqual(ctx.states["A02"]["status"], "completed")
            ctx.finish()

    def test_visible_parameters_and_preview_match_real_invalidation(self):
        flow = definition()
        displayed = [key for stage in flow["acquisition"] + flow["analysis"]
                     for key in stage["display_parameters"]]
        self.assertEqual(set(displayed), set(self.params.to_external(include_hidden=False)))
        self.assertEqual(len(displayed), len(set(displayed)))
        with fake_hardware(self.root):
            ctx = workflow.Workflow(self.params, project_root=self.root)
            for changes in ({"RUN_TAG": "next"}, {"SCOPE_CYCLES": 4},
                            {"CONTROL_SCALE": .8}, {"CONTROL_WAVEFORM_SOURCE": "corrected_run"}):
                ctx.states = {stage.id: dict(status="completed", error="") for stage in ACQUISITION}
                ctx.update_parameters(changes)
                expected = set().union(*(set(flow["parameter_impacts"]["acquisition"][key]) for key in changes))
                self.assertEqual({key for key, value in ctx.states.items() if value["status"] == "stale"}, expected)
            ctx.finish()

    def test_applied_source_parameters_are_reloaded_and_hidden_compatibility_key_preserved(self):
        with fake_hardware(self.root):
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03"])
            previous = ctx.source.target_current_a.copy()
            ctx.update_parameters({"CONTROL_SCALE": .5})
            np.testing.assert_array_equal(ctx.source.target_current_a, previous)
            ctx.execute(["A03", "A04", "A05"])
            np.testing.assert_array_equal(ctx.source.target_current_a, previous * .5)
            self.assertEqual(ctx.params.z_calibration_source_run, "unused_compatibility_key")
            ctx.finish()

    def test_current_limits_coupling_hash_and_trigger_rejected_before_connection(self):
        with fake_hardware(self.root) as hw:
            for values in (dict(sense_resistor_ohm=11), dict(maximum_current_a=.0001),
                           dict(sense_resistor_power_rating_w=1e-8), dict(run_tag="../escape"),
                           dict(scope_trigger_level_v=1), dict(scope_measured_channel=2)):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    workflow.Workflow(replace(self.params, **values), project_root=self.root)
            hw.generator.connect.assert_not_called()
            path = self.root / "data/Mx_Z_Current_Coupling_Calibration/fake_current/results/analysis.yaml"
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "哈希"):
                workflow.Workflow(scan_params(self.root, source="corrected_run"), project_root=self.root)

    def test_saved_yaml_round_trip_and_frozen_source_derive_do_not_read_theory(self):
        params = scan_params(self.root, source="corrected_run")
        path = self.root / "saved.yaml"
        params.save_yaml(path)
        restored = models.ZAWCurrentWaveformScopeCheckParams.from_yaml(path)
        self.assertEqual(restored.to_external(), params.to_external())
        self.assertEqual(restored.schema_version, 1)
        (self.root / "control").rename(self.root / "unused_theory")
        with fake_hardware(self.root) as hw:
            derived = type(params).derive_external(params.to_external())
            self.assertEqual(derived["CONTROL_REPEAT_FREQUENCY_HZ"], 1000)
            self.assertAlmostEqual(derived["TOTAL_CAPTURE_DURATION_S"], .006)
            self.assertEqual(params.validate(self.root), [])
            hw.generator.connect.assert_not_called()

    def test_slow_disk_cancel_preserves_first_frame(self):
        cancel = self.root / "cancel"
        save, seen = np.savez, []
        with fake_hardware(self.root) as hw, patch.dict(os.environ, {"LAB_CANCEL_FILE": str(cancel)}):
            def slow_save(path, *args, **kwargs):
                if Path(path).name.startswith("scope_capture"):
                    self.assertEqual(hw.state["status"], "STOP")
                    time.sleep(.01)
                    seen.append(Path(path))
                save(path, *args, **kwargs)
                if Path(path).name == "scope_capture_000.npz":
                    cancel.touch()
            with patch.object(acquisition.np, "savez", side_effect=slow_save), self.assertRaises(WorkflowCancelled):
                workflow.run(self.params, project_root=self.root)
            self.assertEqual(len(seen), 1)
            self.assertTrue(seen[0].is_file())
            hw.scope.disconnect.assert_called_once()

    def test_read_failure_and_restore_failure_keep_original_error_and_partial_data(self):
        with fake_hardware(self.root) as hw:
            read = hw.acquire.side_effect
            def failing_read(*args, **kwargs):
                if hw.acquire.call_count > 2:
                    hw.generator.set_output.side_effect = lambda enabled, *, channel: (
                        (_ for _ in ()).throw(RuntimeError("模拟关闭失败")) if not enabled and channel == 1 else None)
                    raise RuntimeError("模拟第二帧失败")
                return read(*args, **kwargs)
            hw.acquire.side_effect = failing_read
            with self.assertRaisesRegex(RuntimeError, "模拟第二帧失败"):
                workflow.run(self.params, project_root=self.root)
            run = next((self.root / "data" / workflow.DATA_TYPE).iterdir())
            self.assertTrue((run / "raw/scope_capture_000.npz").is_file())
            config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "failed")
            self.assertIn("模拟第二帧失败", config["failure_reason"])
            self.assertIn("模拟关闭失败", config["failure_reason"])
            self.assertFalse(config["safety_shutdown"]["completed"])

    def test_source_drift_copy_hash_and_save_failure_stop_capture(self):
        for source in ("theory", "corrected_run"):
            with self.subTest(source=source), fake_hardware(self.root) as hw:
                ctx = workflow.Workflow(scan_params(self.root, source=source), project_root=self.root)
                ctx.execute(["A01", "A02", "A03", "A04"])
                path = self.root / "data/Mx_Z_Current_Coupling_Calibration/fake_current/results/analysis.yaml"
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                # 普通理论来源资格仍通过，A05 必须识别 A03 后的内容漂移。
                with self.assertRaisesRegex(ValueError, "改变|哈希"):
                    ctx.execute(["A05"])
                path.write_bytes(original)
                hw.acquire.assert_not_called()
                with patch.object(preparation, "sha256_file", side_effect=lambda p:
                    "bad-copy" if Path(p).name == "source_current_coupling_analysis.yaml" else hashlib.sha256(Path(p).read_bytes()).hexdigest()):
                    with self.assertRaisesRegex(ValueError, "副本哈希"):
                        ctx.execute(["A05"])
                hw.acquire.assert_not_called()
                with patch.object(acquisition, "_save_frame", side_effect=OSError("模拟慢盘失败")):
                    with self.assertRaisesRegex(OSError, "模拟慢盘失败"):
                        ctx.execute(["A05"])
                self.assertTrue((ctx.run_dir / "raw/applied_control_waveform.npz").is_file())
                ctx.finish()

    def test_scope_mode_clock_and_connection_failures_use_shared_recovery(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            hw.state["mode"] = "AUTO"
            with self.assertRaisesRegex(ValueError, "Scope 模式"):
                ctx.execute(["A05"])
            ctx.execute(["A04", "A05"])
            ctx.finish()
        with fake_hardware(self.root) as hw:
            hw.generator.get_ref_clock_source.return_value = "INT"
            with self.assertRaises(Exception):
                workflow.run(self.params, project_root=self.root)
            hw.generator.set_output.assert_not_called()
            hw.generator.disconnect.assert_called_once()
        with fake_hardware(self.root) as hw:
            hw.scope.connect.side_effect = OSError("模拟连接失败")
            with self.assertRaisesRegex(OSError, "模拟连接失败"):
                workflow.run(self.params, project_root=self.root)
            hw.generator.disconnect.assert_called_once()


def fake_session_main():
    from .session import main
    root = Path(os.environ["Z_CURRENT_TEST_ROOT"])
    with fake_hardware(root), redirect_stdout(sys.__stdout__):
        main()


if __name__ == "__main__":
    if "Z_CURRENT_TEST_ROOT" in os.environ:
        fake_session_main()
    else:
        unittest.main()
