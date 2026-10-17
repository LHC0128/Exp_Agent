"""真实业务配合假设备：流程、采集、写盘与安全策略的无硬件回归。"""
from contextlib import ExitStack, contextmanager, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np
import yaml
from sds_acquisition import SDSAcquisition

from ...common import WorkflowCancelled
from ...steps import routed_signal_generator
from . import acquisition, models, preparation, workflow
from .session import serve
from .stages import ACQUISITION, definition, parameter_impacts

ROOT = Path(__file__).resolve().parents[3]


def copy_config(root):
    (root / "params").mkdir()
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)
    directory = root / "control/v1"
    (directory / "waveforms").mkdir(parents=True)
    (directory / "parameters").mkdir()
    t = np.arange(64) / 64000
    np.savetxt(directory / "waveforms/optimal_control_waveform.csv",
        np.column_stack((t, 300 * np.sin(2 * np.pi * 1000 * t))), delimiter=",")
    (directory / "parameters/optimal_control_params.csv").write_text(
        "# Output version,v1\nf_rf_Hz,1000\n", encoding="utf-8")
    calibration = root / "data/Mx_Z_Field_Calibration/fake_cal/results"
    calibration.mkdir(parents=True)
    (calibration / "analysis.yaml").write_text(yaml.safe_dump(dict(
        experiment_id="mx-z-field-calibration", success=True, K_Z_Hz_per_V=1000,
        f_0V_Hz=0, linear_fit=dict(r_squared=1))), encoding="utf-8")


def scan_params(root):
    return models.ZAWWaveformScopeCheckParams(control_results_root=str(root / "control"),
        control_version="v1", z_calibration_source_run="fake_cal", scope_repeats=2,
        scope_initial_scale_v_div=0.2, run_tag="fake")


@contextmanager
def fake_hardware(root):
    """只替换驱动构造和逐通道读取；配置、重试、保存与收尾执行真实业务。"""
    generator, scope = Mock(name="DG4000"), Mock(name="SDS")
    generator.get_ref_clock_source.return_value = "EXT"
    state = dict(scale={3: 0.2, 4: 1.0}, offset={3: 0.0, 4: -2.5},
                 mode="SINGle", source="C4", status="STOP", rate=64000.0)
    scope.set_channel_scale.side_effect = lambda ch, value: state["scale"].__setitem__(ch, value)
    scope.set_channel_offset.side_effect = lambda ch, value: state["offset"].__setitem__(ch, value)
    scope.get_channel_scale.side_effect = lambda ch: state["scale"][ch]
    scope.get_channel_offset.side_effect = lambda ch: state["offset"][ch]
    scope.set_trigger_mode.side_effect = lambda value: state.__setitem__("mode", value)
    scope.get_trigger_mode.side_effect = lambda: state["mode"]
    scope.set_trigger_source.side_effect = lambda value: state.__setitem__("source", value)
    scope.get_trigger_source.side_effect = lambda: state["source"]
    scope.trigger_run.side_effect = lambda: state.__setitem__("status", "TRIGGERED")
    scope.trigger_stop.side_effect = lambda: state.__setitem__("status", "STOP")
    scope.trigger_status.side_effect = lambda: state["status"]
    scope.set_sampling_rate.side_effect = lambda value: state.__setitem__("rate", value)
    scope.get_sampling_rate.side_effect = lambda: state["rate"]
    scope.get_actual_points.return_value = 193
    scope.get_memory_management.return_value = "FSRate"
    scope.get_memory_depth.return_value = "14K"
    scope.get_channel_coupling.return_value = "DC"
    scope.get_channel_impedance.return_value = "ONEMeg"
    scope.get_channel_probe.return_value = 1.0
    hw = SimpleNamespace(generator=generator, scope=scope, state=state, no_edge_frames=0)

    def read_channel(inst, channel, *_args, **_kwargs):
        t = np.arange(-96, 97) / state["rate"]
        if channel == 3:
            voltage = .36 * np.sin(2 * np.pi * 1000 * (t - .000125)) + .01
        elif hw.no_edge_frames:
            hw.no_edge_frames -= 1
            voltage = np.zeros_like(t)
        else:
            voltage = np.where(t < 0, 5.0, 0.0)
        return SimpleNamespace(time=t, voltage=voltage, preamble_dict={"points": len(t)})

    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(StringIO()))
        stack.enter_context(patch.object(models, "find_project_root", return_value=root))
        stack.enter_context(patch.object(workflow, "find_project_root", return_value=root))
        stack.enter_context(patch.object(routed_signal_generator, "create_signal_generator", return_value=generator))
        stack.enter_context(patch.object(preparation, "SDSInstrument", return_value=scope))
        stack.enter_context(patch.object(acquisition, "_sleep_cancellable"))
        hw.acquire = stack.enter_context(patch.object(SDSAcquisition, "acquire_channel", autospec=True,
                                                      side_effect=read_channel))
        yield hw


class ScopeWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zaw-scope-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.params = scan_params(self.root)

    def test_all_visible_parameters_have_real_edit_nodes_and_impacts_match_application(self):
        flow = definition()
        visible = self.params.to_external(include_hidden=False)
        displayed = [name for stage in flow["acquisition"] + flow["analysis"]
                     for name in stage["display_parameters"]]
        self.assertEqual(set(displayed), set(visible))
        self.assertEqual(len(displayed), len(set(displayed)))
        self.assertEqual(flow["parameter_impacts"]["analysis"], {})
        with fake_hardware(self.root):
            ctx = workflow.Workflow(self.params, project_root=self.root)
            for changes in ({"RUN_TAG": "edited"}, {"SCOPE_CYCLES": 4},
                            {"CONTROL_SCALE": .8, "SCOPE_REPEATS": 3}):
                ctx.states = {stage.id: {"status": "completed", "error": ""} for stage in ACQUISITION}
                expected = {item for key in changes for item in flow["parameter_impacts"]["acquisition"][key]}
                ctx.update_parameters(changes)
                self.assertEqual({key for key, value in ctx.states.items() if value["status"] == "stale"}, expected)
                self.assertFalse(ctx.devices)
            before = ctx.revision
            with self.assertRaises(ValueError):
                ctx.update_parameters({"RUN_TAG": "../unsafe"})
            self.assertEqual(ctx.revision, before)
            ctx.finish()

    def test_missing_dependencies_never_connect_and_preflight_preserves_completed_steps(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            with self.assertRaisesRegex(ValueError, "A04 Scope"):
                ctx.execute(["A05"])
            hw.generator.connect.assert_not_called()
            ctx.execute(["A02", "A01"])
            ctx.execute(["A01"])
            self.assertEqual(ctx.states["A02"]["status"], "completed")
            ctx.finish()
            hw.scope.trigger_stop.assert_called()

    def test_complete_and_staged_runs_share_real_capture_and_unique_directories(self):
        with fake_hardware(self.root) as hw:
            full = workflow.run(self.params, project_root=self.root)
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04", "A05"])
            first, original = ctx.run_dir, (ctx.run_dir / "experiment_config.yaml").read_bytes()
            ctx.execute(["A05"])
            self.assertNotEqual(ctx.run_dir, first)
            self.assertEqual((first / "experiment_config.yaml").read_bytes(), original)
            for index in range(self.params.scope_repeats):
                with np.load(full / f"raw/scope_capture_{index:03d}.npz") as a, np.load(
                        first / f"raw/scope_capture_{index:03d}.npz") as b:
                    np.testing.assert_array_equal(a["measured_voltage_v"], b["measured_voltage_v"])
                    self.assertAlmostEqual(float(a["scale_used_v_div"]), .2)
            ctx.finish()
            hw.generator.setup_dc.assert_any_call(0.0, channel=1)
            self.assertEqual([entry for entry in hw.generator.set_output.call_args_list if entry.kwargs["channel"] == 1][-1],
                             call(False, channel=1))
            self.assertEqual([entry for entry in hw.generator.set_output.call_args_list if entry.kwargs["channel"] == 2][-1],
                             call(True, channel=2))
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["raw_format_version"], 1)
            self.assertEqual(config["completion_status"], "completed")
            self.assertTrue(config["safety_shutdown"]["completed"])

    def test_scope_mode_change_requires_explicit_preparation_and_bad_frame_retries(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            hw.state["mode"] = "AUTO"
            with self.assertRaisesRegex(ValueError, "Scope 模式已改变"):
                ctx.execute(["A05"])
            self.assertIsNone(ctx.run_dir)
            self.assertEqual(ctx.states["A04"]["status"], "stale")
            hw.no_edge_frames = 1
            ctx.execute(["A04", "A05"])
            self.assertEqual(hw.scope.reset.call_count, 1)
            ctx.finish()

    def test_capture_failure_preserves_saved_frames_and_original_error_with_cleanup_failure(self):
        with fake_hardware(self.root) as hw:
            read = hw.acquire.side_effect
            def failing_read(*args, **kwargs):
                if hw.acquire.call_count > 2:
                    hw.generator.set_output.side_effect = lambda enabled, *, channel: (
                        (_ for _ in ()).throw(RuntimeError("模拟关闭失败")) if not enabled and channel == 1 else None)
                    raise RuntimeError("模拟第二帧读取失败")
                return read(*args, **kwargs)
            hw.acquire.side_effect = failing_read
            with self.assertRaisesRegex(RuntimeError, "模拟第二帧读取失败"):
                workflow.run(self.params, project_root=self.root)
            runs = list((self.root / "data" / workflow.DATA_TYPE).iterdir())
            self.assertTrue((runs[0] / "raw/scope_capture_000.npz").is_file())
            self.assertFalse((runs[0] / "raw/scope_capture_001.npz").exists())
            config = yaml.safe_load((runs[0] / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "failed")
            self.assertIn("模拟第二帧读取失败", config["failure_reason"])
            self.assertIn("模拟关闭失败", config["failure_reason"])
            self.assertFalse(config["safety_shutdown"]["completed"])

    def test_cancel_between_frames_and_slow_real_disk_save_keep_electrical_protocol(self):
        cancel = self.root / "cancel"
        original_save = np.savez
        seen = []
        with fake_hardware(self.root) as hw, patch.dict(os.environ, {"LAB_CANCEL_FILE": str(cancel)}):
            def slow_save(path, *args, **kwargs):
                if Path(path).name.startswith("scope_capture"):
                    self.assertEqual(hw.state["status"], "STOP")
                    time.sleep(.01)
                    seen.append(str(path))
                original_save(path, *args, **kwargs)
                if Path(path).name == "scope_capture_000.npz":
                    cancel.touch()
            with patch.object(acquisition.np, "savez", side_effect=slow_save):
                with self.assertRaises(WorkflowCancelled):
                    workflow.run(self.params, project_root=self.root)
            self.assertEqual(len(seen), 1)
            self.assertEqual([entry for entry in hw.generator.set_output.call_args_list if entry.kwargs["channel"] == 1][-1],
                             call(False, channel=1))
            hw.scope.disconnect.assert_called_once()

    def test_save_failure_and_source_change_leave_diagnostics_and_no_overwrite(self):
        with fake_hardware(self.root):
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            source = ctx.source.theory.waveform_path
            source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "控制源或标定文件已改变"):
                ctx.execute(["A05"])
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "failed")
            ctx.execute(["A03", "A04"])
            with patch.object(acquisition, "_save_frame", side_effect=OSError("模拟磁盘写入失败")):
                with self.assertRaisesRegex(OSError, "模拟磁盘写入失败"):
                    ctx.execute(["A05"])
            self.assertTrue((ctx.run_dir / "raw/applied_control_waveform.npz").is_file())
            ctx.finish()

    def test_connection_failure_releases_partial_connection_and_legacy_exports_remain(self):
        with fake_hardware(self.root) as hw:
            hw.scope.connect.side_effect = OSError("模拟Scope连接失败")
            with self.assertRaisesRegex(OSError, "模拟Scope连接失败"):
                workflow.run(self.params, project_root=self.root)
            hw.generator.disconnect.assert_called_once()
            hw.generator.set_output.assert_not_called()
        from ..z_aw_closed_loop_waveform_correction import capture
        from ..z_aw_current_waveform_scope_check import workflow as current
        self.assertIs(current.run_scope_check, workflow.run_scope_check)
        self.assertIs(capture._wait_for_scope_trigger, acquisition._wait_for_scope_trigger)

    def test_shutdown_failure_is_not_reported_as_success_and_finish_has_no_prerequisite(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03"])
            hw.scope.trigger_stop.side_effect = OSError("模拟Scope停止失败")
            with self.assertRaisesRegex(RuntimeError, "安全收尾未全部完成"):
                ctx.execute(["A06"])
            self.assertEqual(ctx.states["A06"]["status"], "failed")
            self.assertTrue(ctx.closed)

    def test_current_override_reuses_capture_and_preserves_target_snapshot(self):
        with fake_hardware(self.root):
            params = replace(self.params, scope_repeats=1)
            source = preparation.prepare_control(params, self.root)
            calibration = SimpleNamespace(run_name="current_cal", analysis_sha256="sha",
                slope_hz_per_a=10000.0, sense_resistor_ohm=10.0)
            def snapshot(raw):
                np.savez(raw / "applied_control_waveform.npz", time_s=source.theory.time_s,
                    voltage_v=source.applied.voltage_v,
                    repeat_frequency_hz=source.theory.repeat_frequency_hz,
                    target_current_a=np.zeros_like(source.theory.time_s))
                return ["raw/applied_control_waveform.npz"]
            run = workflow.run_scope_check(params, project_root=self.root,
                experiment_id="z-aw-current-waveform-scope-check",
                data_type="Z_AW_Current_Waveform_Scope_Check", measurement_mode="current",
                current_calibration=calibration, theory_override=source.theory,
                applied_override=source.applied, source_snapshot_writer=snapshot,
                target_control_scale=1.0)
            config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["measurement_mode"], "current")
            self.assertIsNone(config["z_calibration"])
            with np.load(run / "raw/applied_control_waveform.npz") as data:
                np.testing.assert_array_equal(data["target_current_a"], source.theory.omega_ctrl_hz / 10000)
            self.assertTrue((run / "raw/scope_capture_000.npz").exists())


def fake_session_main():
    """API 回归实际启动这个模块，仪器边界在子进程内替换。"""
    from .session import main
    root = Path(os.environ["ZAW_SCOPE_TEST_ROOT"])
    with fake_hardware(root), redirect_stdout(sys.__stdout__):
        main()


if __name__ == "__main__":
    if "ZAW_SCOPE_TEST_ROOT" in os.environ:
        fake_session_main()
    else:
        unittest.main()
