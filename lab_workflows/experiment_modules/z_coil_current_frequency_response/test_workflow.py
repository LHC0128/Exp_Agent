"""假驱动配合真实对数扫描、SDS 配置、NPZ 写盘和专用收尾。"""
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

import numpy as np
import yaml

from ...common import WorkflowCancelled
from ..z_coil_inductance_frequency_response import acquisition as scope_acquisition, workflow as shared_workflow
from ..z_coil_inductance_frequency_response.test_workflow import ROOT, copy_config, fake_hardware as shared_hardware
from . import workflow
from .models import ZCoilCurrentFrequencyResponseParams
from .stages import ACQUISITION, FINISH_ID, definition

# 纯电学独立帧，既没有温控门控，也没有 OFF/ON 配对。
ACQUISITION_CASES = {
    "z-coil-current-frequency-response": {"mode": "single", "cases": {
        "order": ["CurrentWorkflowTests::test_full_staged_log_axis_last_point_actual_rates_and_repeat_isolation",
            "CurrentWorkflowTests::test_every_log_frequency_repeat_orders_real_configuration_validation_and_writer"],
        "slow_disk": ["CurrentWorkflowTests::test_cancel_after_slow_real_save_preserves_data_and_output_policy"],
        "cancel": ["CurrentWorkflowTests::test_cancel_after_slow_real_save_preserves_data_and_output_policy"],
        "acquire_failure": ["CurrentWorkflowTests::test_acquisition_and_restore_failures_keep_primary_error_and_partial_index"],
        "restore_failure": ["CurrentWorkflowTests::test_acquisition_and_restore_failures_keep_primary_error_and_partial_index"],
        "save_failure": ["CurrentWorkflowTests::test_real_writer_failure_and_failed_finish_are_not_success"],
        "partial_save": ["CurrentWorkflowTests::test_acquisition_and_restore_failures_keep_primary_error_and_partial_index"],
        "entrypoints": ["CurrentWorkflowTests::test_full_staged_log_axis_last_point_actual_rates_and_repeat_isolation"],
    }, "sources": ["lab_workflows/experiment_modules/z_coil_current_frequency_response/session.py",
        "lab_workflows/experiment_modules/z_coil_inductance_frequency_response/acquisition.py",
        "lab_workflows/experiment_modules/z_coil_inductance_frequency_response/excitation.py",
        "lab_workflows/experiment_modules/z_coil_inductance_frequency_response/preparation.py"]},
}


def scan_params(root=None, *, reference=0):
    return ZCoilCurrentFrequencyResponseParams(frequency_start_hz=1000, frequency_stop_hz=4000,
        frequency_points_per_decade=4, frequency_points=4, frequency_settle_s=0, scope_repeats=2,
        scope_sample_rate_sa_s=64000, scope_min_sample_rate_sa_s=64000, scope_capture_guard_s=0,
        scope_initial_scale_v_div=.2, response_settle_cycles=0, scope_reference_channel=reference,
        sense_resistor_ohm=10, sense_resistor_power_rating_w=1, maximum_current_a=.1, run_tag="fake-current")


@contextmanager
def fake_hardware(root):
    """沿用真实驱动边界的假设备，额外提供同帧 CH1 正弦参考。"""
    with shared_hardware(root) as hw:
        hw.state["scale"].update({1: .5, 2: .5})
        hw.state["offset"].update({1: 0, 2: 0})
        def read_channel(inst, channel, *_args, **_kwargs):
            t = np.arange(-640, 641) / hw.state["rate"]
            if channel in (1, 2, 3):
                amplitude, offset = (.36, .01) if channel == 3 else (.5, 0)
                voltage = amplitude * np.sin(2*np.pi*hw.state["frequency"]*(t-.000125)) + offset
            elif hw.no_edge_frames:
                hw.no_edge_frames -= 1
                voltage = np.zeros_like(t)
            else:
                voltage = np.where(t < 0, 5.0, 0.0)
            return SimpleNamespace(time=t, voltage=voltage, preamble_dict={"points": len(t)})
        hw.acquire.side_effect = read_channel
        yield hw


class CurrentWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="z-current-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.params = scan_params(self.root)

    def test_manifest_display_and_impacts_match_real_application(self):
        flow = definition()
        self.assertEqual({name for stage in flow["acquisition"]+flow["analysis"]
            for name in stage["display_parameters"]}, set(self.params.to_external(include_hidden=False)))
        self.assertEqual(flow["parameter_impacts"]["analysis"], {})
        with fake_hardware(self.root):
            ctx = workflow.Workflow(self.params, project_root=self.root)
            for values in ({"RUN_TAG": "new"}, {"SCOPE_CYCLES": 4},
                           {"SENSE_RESISTOR_OHM": 12, "SCOPE_CAPTURE_GUARD_S": .2},
                           {"DRIVE_AMPLITUDE_VPP": .8, "SCOPE_REPEATS": 3}):
                ctx.states = {stage.id: {"status": "completed", "error": ""} for stage in ACQUISITION}
                expected = {item for key in values for item in flow["parameter_impacts"]["acquisition"][key]}
                ctx.update_parameters(values)
                self.assertEqual({key for key,value in ctx.states.items() if value["status"]=="stale"}, expected)
            self.assertEqual(ctx.capture_guard_s, .2)
            self.assertEqual(ctx.params.sense_resistor_ohm, 12)
            ctx.finish()

    def test_dependencies_read_only_preflight_scope_mode_and_no_frontier_finish(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            with self.assertRaisesRegex(ValueError, "A05"):
                ctx.execute(["A06"])
            hw.generator.connect.assert_not_called()
            ctx.execute(["A01", "A02", "A03", "A04", "A05"])
            ctx.execute(["A01"])
            self.assertEqual(ctx.states["A05"]["status"], "completed")
            hw.state["mode"] = "AUTO"
            with self.assertRaisesRegex(ValueError, "Scope 模式已改变"):
                ctx.execute(["A06"])
            self.assertIsNone(ctx.run_dir)
            ctx.execute(["A05", "A06"])
            ctx.finish()
        with fake_hardware(self.root) as hw:
            workflow.Workflow(self.params, project_root=self.root).execute([FINISH_ID])
            hw.generator.connect.assert_not_called()

    def test_full_staged_log_axis_last_point_actual_rates_and_repeat_isolation(self):
        with fake_hardware(self.root) as hw:
            full = workflow.run(self.params, project_root=self.root)
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute([s.id for s in ACQUISITION if s.id != FINISH_ID])
            first = ctx.run_dir
            original = (first / "experiment_config.yaml").read_bytes()
            ctx.execute(["A06"])
            self.assertNotEqual(ctx.run_dir, first)
            self.assertEqual((first / "experiment_config.yaml").read_bytes(), original)
            for index,frequency in enumerate(self.params.frequency_axis()):
                for repeat in range(2):
                    name=f"raw/scope_f{index:03d}_r{repeat:02d}.npz"
                    with np.load(full/name) as a, np.load(first/name) as b:
                        np.testing.assert_array_equal(a["measured_voltage_v"], b["measured_voltage_v"])
                        self.assertAlmostEqual(float(a["frequency_hz"]),frequency)
                        self.assertAlmostEqual(float(a["actual_rate_sa_s"]),64000)
            records=json.loads((first/"raw/frequency_index.json").read_text(encoding="utf-8"))
            np.testing.assert_allclose([r["frequency_hz"] for r in records],self.params.frequency_axis())
            self.assertTrue(all(len(r["files"])==2 for r in records))
            config=yaml.safe_load((first/"experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["raw_format_version"],1)
            self.assertEqual(config["drive"]["output_protocol"],workflow.OUTPUT_PROTOCOL)
            self.assertEqual(config["drive"]["command_voltage_reference"],"50_ohm")
            self.assertNotIn("temperature_control",config)
            self.assertGreaterEqual(hw.generator.phase_init.call_count,16)
            ctx.update_parameters({"TRIGGER_FREQUENCY_HZ": 200})
            previous=hw.generator.set_frequency.call_count
            ctx.finish()
            self.assertEqual(hw.generator.set_frequency.call_count, previous)
            hw.generator.setup_dc.assert_any_call(0.0,channel=1)
            self.assertEqual([c for c in hw.generator.set_output.call_args_list if c.kwargs["channel"]==1][-1],call(False,channel=1))
            self.assertEqual([c for c in hw.generator.set_output.call_args_list if c.kwargs["channel"]==2][-1],call(True,channel=2))

    def test_reference_channel_current_validation_and_legacy_injection(self):
        self.assertIs(workflow.run_frequency_response,shared_workflow.run_frequency_response)
        with fake_hardware(self.root):
            run=workflow.run(replace(self.params,scope_reference_channel=1),project_root=self.root)
            with np.load(run/"raw/scope_f003_r01.npz") as data:
                self.assertEqual(data["reference_voltage_v"].size,data["time_s"].size)
            for limits, message in (({"MAXIMUM_CURRENT_A": .01}, "峰值电流"),
                    ({"SENSE_RESISTOR_POWER_RATING_W": .001}, "RMS 功率")):
                with self.subTest(limits=limits):
                    ctx=workflow.Workflow(self.params,project_root=self.root)
                    ctx.execute(["A01","A02","A03","A04","A05"])
                    ctx.update_parameters(limits)
                    with self.assertRaisesRegex(ValueError,message):
                        ctx.execute(["A06"])
                    self.assertFalse(list((ctx.run_dir/"raw").glob("scope_f*.npz")))
                    ctx.finish()

    def test_every_log_frequency_repeat_orders_real_configuration_validation_and_writer(self):
        events = []
        with fake_hardware(self.root) as hw:
            sine, scope_config = shared_workflow._configure_sine, shared_workflow._scope_config
            read, validate, save = hw.acquire.side_effect, workflow._validate_capture_current, shared_workflow._save_capture

            def configure_sine(inst, channel, params, frequency, **kwargs):
                events.append(("sine", frequency))
                return sine(inst, channel, params, frequency, **kwargs)

            def configure_scope(params, frequency, **kwargs):
                events.append(("scope", frequency))
                return scope_config(params, frequency, **kwargs)

            def read_channel(inst, channel, *args, **kwargs):
                self.assertEqual(hw.state["status"], "STOP")
                events.append(("read", channel))
                return read(inst, channel, *args, **kwargs)

            def validate_current(frame, params):
                events.append(("validate",))
                return validate(frame, params)

            def save_capture(directory, frequency_index, repeat_index, *args):
                relative = save(directory, frequency_index, repeat_index, *args)
                self.assertTrue((directory.root / relative).is_file())
                events.append(("save", frequency_index, repeat_index))
                return relative

            hw.acquire.side_effect = read_channel
            with patch.object(shared_workflow, "_configure_sine", side_effect=configure_sine), \
                    patch.object(shared_workflow, "_scope_config", side_effect=configure_scope), \
                    patch.object(shared_workflow, "_sleep_cancellable", side_effect=lambda _s: events.append(("settle",))), \
                    patch.object(workflow, "_validate_capture_current", side_effect=validate_current), \
                    patch.object(shared_workflow, "_save_capture", side_effect=save_capture):
                ctx = workflow.Workflow(self.params, project_root=self.root)
                ctx.execute(["A01", "A02", "A03", "A04", "A05"])
                events.clear()
                ctx.execute(["A06"])
            expected = []
            for index, frequency in enumerate(self.params.frequency_axis()):
                expected.extend([("sine", frequency), ("scope", frequency), ("settle",)])
                for repeat in range(2):
                    expected.extend([("read", 3), ("read", 4), ("validate",), ("save", index, repeat)])
            self.assertEqual(events, expected)
            np.testing.assert_allclose(np.diff(np.log(self.params.frequency_axis())), np.log(4) / 3)
            self.assertTrue((ctx.run_dir / "raw/scope_f003_r01.npz").is_file())
            ctx.finish()

    def test_acquisition_and_restore_failures_keep_primary_error_and_partial_index(self):
        with fake_hardware(self.root) as hw:
            read=hw.acquire.side_effect
            def fail(*args,**kwargs):
                if hw.acquire.call_count>2:
                    hw.generator.set_output.side_effect=lambda enabled,*,channel: (
                        (_ for _ in ()).throw(RuntimeError("模拟输出恢复失败")) if not enabled and channel==1 else None)
                    raise RuntimeError("模拟第二帧采集失败")
                return read(*args,**kwargs)
            hw.acquire.side_effect=fail
            with self.assertRaisesRegex(RuntimeError,"模拟第二帧采集失败"):
                workflow.run(self.params,project_root=self.root)
            run=next((self.root/"data"/workflow.DATA_TYPE).iterdir())
            self.assertTrue((run/"raw/scope_f000_r00.npz").is_file())
            records=json.loads((run/"raw/frequency_index.json").read_text(encoding="utf-8"))
            self.assertEqual(records[0]["files"],["raw/scope_f000_r00.npz"])
            config=yaml.safe_load((run/"experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertIn("模拟第二帧采集失败",config["failure_reason"])
            self.assertIn("模拟输出恢复失败",config["failure_reason"])
            self.assertFalse(config["safety_shutdown"]["completed"])

    def test_cancel_after_slow_real_save_preserves_data_and_output_policy(self):
        cancel=self.root/"cancel"
        original=np.savez
        saved=[]
        with fake_hardware(self.root) as hw,patch.dict(os.environ,{"LAB_CANCEL_FILE":str(cancel)}):
            def save(path,*args,**kwargs):
                if Path(path).name.startswith("scope_f"):
                    self.assertEqual(hw.state["status"],"STOP")
                    time.sleep(.01)
                    saved.append(str(path))
                original(path,*args,**kwargs)
                if Path(path).name=="scope_f000_r00.npz":
                    cancel.touch()
            with patch.object(scope_acquisition.np,"savez",side_effect=save):
                with self.assertRaises(WorkflowCancelled):
                    workflow.run(self.params,project_root=self.root)
            self.assertEqual(len(saved),1)
            run=next((self.root/"data"/workflow.DATA_TYPE).iterdir())
            config=yaml.safe_load((run/"experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"],"cancelled")
            self.assertTrue(config["safety_shutdown"]["completed"])
            hw.scope.disconnect.assert_called_once()

    def test_real_writer_failure_and_failed_finish_are_not_success(self):
        original=np.savez
        with fake_hardware(self.root):
            def save(path,*args,**kwargs):
                if Path(path).name=="scope_f000_r01.npz":
                    raise OSError("模拟写盘失败")
                return original(path,*args,**kwargs)
            with patch.object(scope_acquisition.np,"savez",side_effect=save):
                with self.assertRaisesRegex(OSError,"模拟写盘失败"):
                    workflow.run(self.params,project_root=self.root)
            run=next((self.root/"data"/workflow.DATA_TYPE).iterdir())
            self.assertTrue((run/"raw/scope_f000_r00.npz").is_file())
            config=yaml.safe_load((run/"experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"],"failed")
            self.assertIn("模拟写盘失败",config["failure_reason"])
        with fake_hardware(self.root) as hw:
            ctx=workflow.Workflow(self.params,project_root=self.root)
            ctx.execute(["A01","A02"])
            hw.scope.trigger_stop.side_effect=OSError("模拟停止失败")
            with self.assertRaisesRegex(RuntimeError,"安全收尾未全部完成"):
                ctx.finish()
            self.assertEqual(ctx.states[FINISH_ID]["status"],"failed")


def fake_session_main():
    """会话 API 使用真实子进程，子进程内部只替换仪器驱动边界。"""
    from .session import main
    root=Path(os.environ["Z_CURRENT_TEST_ROOT"])
    with fake_hardware(root) as hw,redirect_stdout(sys.__stdout__):
        if gate_name := os.environ.get("Z_CURRENT_SHUTDOWN_GATE"):
            stop = hw.scope.trigger_stop.side_effect
            def wait_before_stop():
                Path(gate_name + "-entered").touch()
                deadline = time.monotonic() + 30
                while not Path(gate_name + "-release").is_file():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("验收测试未释放假仪器收尾")
                    time.sleep(.01)
                return stop()
            hw.scope.trigger_stop.side_effect = wait_before_stop
        main()


if __name__=="__main__":
    if "Z_CURRENT_TEST_ROOT" in os.environ:
        fake_session_main()
    else:
        unittest.main()
