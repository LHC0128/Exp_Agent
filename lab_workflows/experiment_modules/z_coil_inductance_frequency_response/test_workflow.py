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
from .stages import ACQUISITION, FINISH_ID, definition, parameter_impacts

ROOT = Path(__file__).resolve().parents[3]

# 纯电学例外采用独立帧边界，不虚构温控门控或 OFF/ON 配对。
ACQUISITION_CASES = {
    "z-coil-inductance-frequency-response": {"mode": "single", "cases": {
        "order": ["InductanceWorkflowTests::test_complete_staged_linear_axis_and_repeated_runs_share_capture",
                  "InductanceWorkflowTests::test_every_frequency_repeat_orders_real_configuration_capture_and_writer"],
        "slow_disk": ["InductanceWorkflowTests::test_cancel_with_slow_real_save_keeps_electrical_protocol"],
        "cancel": ["InductanceWorkflowTests::test_cancel_with_slow_real_save_keeps_electrical_protocol"],
        "acquire_failure": ["InductanceWorkflowTests::test_partial_capture_and_shutdown_failure_keep_primary_error"],
        "restore_failure": ["InductanceWorkflowTests::test_partial_capture_and_shutdown_failure_keep_primary_error"],
        "save_failure": ["InductanceWorkflowTests::test_save_failure_retains_first_frame_and_finish_failure_is_not_success"],
        "partial_save": ["InductanceWorkflowTests::test_partial_capture_and_shutdown_failure_keep_primary_error"],
        "entrypoints": ["InductanceWorkflowTests::test_complete_staged_linear_axis_and_repeated_runs_share_capture"],
    }, "sources": ["lab_workflows/experiment_modules/z_coil_inductance_frequency_response/session.py"]},
}


def copy_config(root):
    (root / "params").mkdir()
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)


def scan_params(root):
    return models.ZCoilInductanceFrequencyResponseParams(frequency_start_hz=1000, frequency_stop_hz=4000,
        frequency_points=4, frequency_settle_s=0, scope_repeats=2, scope_sample_rate_sa_s=64000,
        scope_initial_scale_v_div=.2, run_tag="fake")


@contextmanager
def fake_hardware(root):
    """只替换驱动构造和逐通道读取；配置、重试、保存与收尾执行真实业务。"""
    generator, scope = Mock(name="DG4000"), Mock(name="SDS")
    generator.get_ref_clock_source.return_value = "EXT"
    state = dict(scale={3: 0.2, 4: 1.0}, offset={3: 0.0, 4: -2.5},
                 mode="SINGle", source="C4", status="STOP", rate=64000.0, frequency=1000.0)
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
    scope.get_actual_points.return_value = 1281
    scope.get_memory_management.return_value = "FSRate"
    scope.get_memory_depth.return_value = "14K"
    scope.get_channel_coupling.return_value = "DC"
    scope.get_channel_impedance.return_value = "ONEMeg"
    scope.get_channel_probe.return_value = 1.0
    generator.set_frequency.side_effect = lambda value, *, channel: state.__setitem__("frequency", value)
    hw = SimpleNamespace(generator=generator, scope=scope, state=state, no_edge_frames=0)

    def read_channel(inst, channel, *_args, **_kwargs):
        t = np.arange(-640, 641) / state["rate"]
        if channel == 3:
            voltage = .36 * np.sin(2 * np.pi * state["frequency"] * (t - .000125)) + .01
        elif hw.no_edge_frames:
            hw.no_edge_frames -= 1
            voltage = np.zeros_like(t)
        else:
            voltage = np.where(t < 0, 5.0, 0.0)
        return SimpleNamespace(time=t, voltage=voltage, preamble_dict={"points": len(t)})

    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(StringIO()))
        stack.enter_context(patch.object(workflow, "find_project_root", return_value=root))
        stack.enter_context(patch.object(routed_signal_generator, "create_signal_generator", return_value=generator))
        stack.enter_context(patch.object(preparation, "SDSInstrument", return_value=scope))
        stack.enter_context(patch.object(acquisition, "_sleep_cancellable"))
        hw.acquire = stack.enter_context(patch.object(SDSAcquisition, "acquire_channel", autospec=True,
                                                      side_effect=read_channel))
        yield hw


class InductanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="z-inductance-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.params = scan_params(self.root)

    def test_visible_edit_nodes_shared_process_value_and_impacts_match_application(self):
        flow = definition()
        visible = set(self.params.to_external(include_hidden=False))
        displayed = [name for stage in flow["acquisition"] + flow["analysis"] for name in stage["display_parameters"]]
        self.assertEqual(set(displayed), visible)
        self.assertEqual(displayed.count("SCOPE_CYCLES"), 2)
        self.assertEqual(flow["parameter_impacts"]["analysis"], {})
        with fake_hardware(self.root):
            ctx = workflow.Workflow(self.params, project_root=self.root)
            for changes in ({"RUN_TAG": "changed"}, {"SCOPE_CYCLES": 4},
                            {"DRIVE_AMPLITUDE_VPP": .8, "SCOPE_REPEATS": 3}):
                ctx.states = {stage.id: {"status": "completed", "error": ""} for stage in ACQUISITION}
                expected = {item for key in changes for item in flow["parameter_impacts"]["acquisition"][key]}
                ctx.update_parameters(changes)
                self.assertEqual({key for key, value in ctx.states.items() if value["status"] == "stale"}, expected)
            revision = ctx.revision
            with self.assertRaisesRegex(ValueError, "路径分隔符"):
                ctx.update_parameters({"RUN_TAG": "../unsafe"})
            self.assertEqual(ctx.revision, revision)
            self.assertFalse(ctx.devices)
            ctx.finish()

    def test_missing_dependencies_never_connect_and_preflight_preserves_previous_work(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            with self.assertRaisesRegex(ValueError, "A05 Scope"):
                ctx.execute(["A06"])
            hw.generator.connect.assert_not_called()
            ctx.execute(["A02", "A01"])
            ctx.execute(["A01"])
            self.assertEqual(ctx.states["A02"]["status"], "completed")
            ctx.execute([FINISH_ID])
            self.assertTrue(ctx.closed)
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute([FINISH_ID])
            hw.generator.connect.assert_not_called()

    def test_complete_staged_linear_axis_and_repeated_runs_share_capture(self):
        with fake_hardware(self.root) as hw:
            full = workflow.run(self.params, project_root=self.root)
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute([stage.id for stage in ACQUISITION if stage.id != FINISH_ID])
            first, original = ctx.run_dir, (ctx.run_dir / "experiment_config.yaml").read_bytes()
            ctx.execute(["A06"])
            self.assertNotEqual(ctx.run_dir, first)
            self.assertEqual((first / "experiment_config.yaml").read_bytes(), original)
            for frequency in range(4):
                for repeat in range(2):
                    name = f"raw/scope_f{frequency:03d}_r{repeat:02d}.npz"
                    with np.load(full / name) as a, np.load(first / name) as b:
                        np.testing.assert_array_equal(a["measured_voltage_v"], b["measured_voltage_v"])
                        self.assertEqual(float(a["scale_used_v_div"]), .2)
            records = json.loads((first / "raw/frequency_index.json").read_text(encoding="utf-8"))
            self.assertEqual([r["frequency_hz"] for r in records], [1000, 2000, 3000, 4000])
            self.assertTrue(all(len(r["files"]) == 2 for r in records))
            ctx.finish()
            hw.generator.setup_dc.assert_any_call(0.0, channel=1)
            self.assertEqual([c for c in hw.generator.set_output.call_args_list if c.kwargs["channel"] == 1][-1], call(False, channel=1))
            self.assertEqual([c for c in hw.generator.set_output.call_args_list if c.kwargs["channel"] == 2][-1], call(True, channel=2))
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["raw_format_version"], 1)
            self.assertTrue(config["safety_shutdown"]["completed"])
            self.assertEqual(config["completion_status"], "completed")
            self.assertNotIn("temperature_control", config)

    def test_scope_mode_requires_preparation_and_invalid_frame_retries(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04", "A05"])
            hw.state["mode"] = "AUTO"
            with self.assertRaisesRegex(ValueError, "Scope 模式已改变"):
                ctx.execute(["A06"])
            self.assertIsNone(ctx.run_dir)
            self.assertEqual(ctx.states["A05"]["status"], "stale")
            hw.no_edge_frames = 1
            ctx.execute(["A05", "A06"])
            self.assertEqual(hw.scope.reset.call_count, 1)
            ctx.finish()

    def test_every_frequency_repeat_orders_real_configuration_capture_and_writer(self):
        events, outputs = [], {}
        with fake_hardware(self.root) as hw:
            original_sine, original_config = workflow._configure_sine, workflow._scope_config
            original_read, original_save = hw.acquire.side_effect, workflow._save_capture

            def sine(device, channel, params, frequency, **kwargs):
                events.append(("sine", frequency, kwargs.get("initialize", True)))
                return original_sine(device, channel, params, frequency, **kwargs)

            def config(params, frequency, **kwargs):
                events.append(("scope", frequency))
                return original_config(params, frequency, **kwargs)

            def output(enabled, *, channel):
                outputs[channel] = enabled
                events.append(("output", channel, enabled))

            def read(inst, channel, *args, **kwargs):
                events.append(("read", channel))
                return original_read(inst, channel, *args, **kwargs)

            def save(directory, frequency_index, repeat_index, *args):
                self.assertEqual(hw.state["status"], "STOP")
                self.assertEqual(outputs, {1: True, 2: True})
                relative = original_save(directory, frequency_index, repeat_index, *args)
                self.assertTrue((directory.root / relative).is_file())
                events.append(("save", frequency_index, repeat_index))
                return relative

            hw.generator.set_output.side_effect = output
            hw.acquire.side_effect = read
            with patch.object(workflow, "_configure_sine", side_effect=sine), \
                    patch.object(workflow, "_scope_config", side_effect=config), \
                    patch.object(workflow, "_save_capture", side_effect=save), \
                    patch.object(workflow, "_sleep_cancellable", side_effect=lambda _s: events.append(("settle",))):
                ctx = workflow.Workflow(self.params, project_root=self.root)
                ctx.execute([stage.id for stage in ACQUISITION if stage.id != FINISH_ID])
            starts = [index for index, event in enumerate(events) if event[0] == "sine" and not event[2]]
            self.assertEqual(len(starts), 4)
            for point, start in enumerate(starts):
                segment = events[start:starts[point + 1] - 1 if point + 1 < len(starts) else len(events)]
                segment = [event for index, event in enumerate(segment) if not index or event != segment[index - 1]]
                frequency = float((point + 1) * 1000)
                self.assertEqual(segment, [
                    ("sine", frequency, False), ("output", 1, False),
                    ("scope", frequency), ("settle",), ("output", 2, True), ("output", 1, True),
                    ("read", 3), ("read", 4), ("save", point, 0),
                    ("read", 3), ("read", 4), ("save", point, 1),
                ])
            captured = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            before = list(hw.generator.mock_calls)
            ctx.update_parameters({"TRIGGER_FREQUENCY_HZ": 200})
            self.assertEqual(hw.generator.mock_calls, before)
            frequency_writes = hw.generator.set_frequency.call_count
            ctx.finish()
            self.assertEqual(hw.generator.set_frequency.call_count, frequency_writes)
            self.assertEqual(captured["parameters"]["TRIGGER_FREQUENCY_HZ"], 100)

    def test_partial_capture_and_shutdown_failure_keep_primary_error(self):
        with fake_hardware(self.root) as hw:
            read = hw.acquire.side_effect
            def fail(*args, **kwargs):
                if hw.acquire.call_count > 2:
                    hw.generator.set_output.side_effect = lambda enabled, *, channel: (
                        (_ for _ in ()).throw(RuntimeError("模拟关闭失败")) if not enabled and channel == 1 else None)
                    raise RuntimeError("模拟第二帧读取失败")
                return read(*args, **kwargs)
            hw.acquire.side_effect = fail
            with self.assertRaisesRegex(RuntimeError, "模拟第二帧读取失败"):
                workflow.run(self.params, project_root=self.root)
            run = next((self.root / "data" / workflow.DATA_TYPE).iterdir())
            self.assertTrue((run / "raw/scope_f000_r00.npz").is_file())
            self.assertFalse((run / "raw/scope_f000_r01.npz").exists())
            records = json.loads((run / "raw/frequency_index.json").read_text(encoding="utf-8"))
            self.assertEqual(records[0]["files"], ["raw/scope_f000_r00.npz"])
            config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertIn("模拟第二帧读取失败", config["failure_reason"])
            self.assertIn("模拟关闭失败", config["failure_reason"])
            self.assertFalse(config["safety_shutdown"]["completed"])

    def test_cancel_with_slow_real_save_keeps_electrical_protocol(self):
        cancel = self.root / "cancel"
        original_save, seen = np.savez, []
        with fake_hardware(self.root) as hw, patch.dict(os.environ, {"LAB_CANCEL_FILE": str(cancel)}):
            def slow_save(path, *args, **kwargs):
                if Path(path).name.startswith("scope_f"):
                    self.assertEqual(hw.state["status"], "STOP")
                    time.sleep(.01)
                    seen.append(str(path))
                original_save(path, *args, **kwargs)
                if Path(path).name == "scope_f000_r00.npz":
                    cancel.touch()
            with patch.object(acquisition.np, "savez", side_effect=slow_save):
                with self.assertRaises(WorkflowCancelled):
                    workflow.run(self.params, project_root=self.root)
            self.assertEqual(len(seen), 1)
            run = next((self.root / "data" / workflow.DATA_TYPE).iterdir())
            config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "cancelled")
            self.assertTrue(config["safety_shutdown"]["completed"])
            hw.scope.disconnect.assert_called_once()

    def test_save_failure_retains_first_frame_and_finish_failure_is_not_success(self):
        original_save = np.savez
        with fake_hardware(self.root):
            def save(path, *args, **kwargs):
                if Path(path).name == "scope_f000_r01.npz":
                    raise OSError("模拟磁盘写入失败")
                return original_save(path, *args, **kwargs)
            with patch.object(acquisition.np, "savez", side_effect=save):
                with self.assertRaisesRegex(OSError, "模拟磁盘写入失败"):
                    workflow.run(self.params, project_root=self.root)
            run = next((self.root / "data" / workflow.DATA_TYPE).iterdir())
            self.assertTrue((run / "raw/scope_f000_r00.npz").is_file())
            config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "failed")
            self.assertIn("模拟磁盘写入失败", config["failure_reason"])
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02"])
            hw.scope.trigger_stop.side_effect = OSError("模拟Scope停止失败")
            with self.assertRaisesRegex(RuntimeError, "安全收尾未全部完成"):
                ctx.execute([FINISH_ID])
            self.assertTrue(ctx.closed)
            self.assertEqual(ctx.states[FINISH_ID]["status"], "failed")

    def test_connection_and_clock_failure_clean_up_and_legacy_injections_remain(self):
        with fake_hardware(self.root) as hw:
            hw.scope.connect.side_effect = OSError("模拟Scope连接失败")
            with self.assertRaisesRegex(OSError, "模拟Scope连接失败"):
                workflow.run(self.params, project_root=self.root)
            hw.generator.disconnect.assert_called_once()
            hw.generator.set_output.assert_not_called()
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            with patch.object(workflow, "synchronize_connected_clocks", side_effect=RuntimeError("时钟回读失败")):
                with self.assertRaisesRegex(RuntimeError, "时钟回读失败"):
                    ctx.execute(["A03"])
            ctx.finish(RuntimeError("时钟回读失败"))
            self.assertTrue(ctx.shutdown["completed"])
            self.assertEqual(ctx.outputs["Z 正弦"], "DC 0 V / OFF")
        from ..z_coil_current_frequency_response import workflow as current
        from ..z_coil_current_frequency_response.models import ZCoilCurrentFrequencyResponseParams
        self.assertIs(current.run_frequency_response, workflow.run_frequency_response)
        params = ZCoilCurrentFrequencyResponseParams(frequency_start_hz=1000, frequency_stop_hz=2000,
            frequency_points_per_decade=4, frequency_points=3, scope_min_sample_rate_sa_s=64000, scope_sample_rate_sa_s=64000,
            scope_samples_per_cycle=10, scope_repeats=1, response_settle_cycles=0,
            sense_resistor_ohm=10, sense_resistor_power_rating_w=1, maximum_current_a=.1)
        with fake_hardware(self.root) as hw:
            validator = Mock()
            run = workflow.run_frequency_response(params, project_root=self.root,
                experiment_id=current.EXPERIMENT_ID, data_type=current.DATA_TYPE,
                capture_validator=validator, capture_guard_s=0, drive_metadata={"protocol": "test"})
            self.assertEqual(validator.call_count, len(workflow.build_frequency_axis(params)))
            self.assertGreater(hw.generator.phase_init.call_count, 0)
            config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["experiment_id"], current.EXPERIMENT_ID)
            self.assertEqual(config["drive"]["protocol"], "test")


def fake_session_main():
    """API 回归通过真实子进程启动，驱动边界在子进程内部替换。"""
    from .session import main
    root = Path(os.environ["Z_INDUCTANCE_TEST_ROOT"])
    with fake_hardware(root), redirect_stdout(sys.__stdout__):
        main()


if __name__ == "__main__":
    if "Z_INDUCTANCE_TEST_ROOT" in os.environ:
        fake_session_main()
    else:
        unittest.main()
