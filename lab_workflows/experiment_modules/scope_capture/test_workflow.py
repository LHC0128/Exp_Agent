"""示波器采集真实业务无硬件回归：假仪器、温控时序、写盘与安全策略。"""

from contextlib import ExitStack, contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
import os
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import yaml
from sds_acquisition import SDSAcquisition
from sds_acquisition.acquire import AcquisitionResult

from ...common import WorkflowCancelled
from . import acquisition, models, workflow
from .session import serve
from .stages import ACQUISITION, definition, parameter_impacts

ROOT = Path(__file__).resolve().parents[3]


ACQUISITION_CASES = {
    "scope-capture": {
        "mode": "single",
        "cases": {
            "order": ["ScopeCaptureAcquisitionAcceptance::test_real_order_and_slow_writer"],
            "slow_disk": ["ScopeCaptureAcquisitionAcceptance::test_real_order_and_slow_writer"],
            "cancel": ["ScopeCaptureAcquisitionAcceptance::test_cancel_restores_switch_and_marks_cancelled"],
            "acquire_failure": ["ScopeCaptureAcquisitionAcceptance::test_acquire_failure_restores_switch"],
            "restore_failure": ["ScopeCaptureAcquisitionAcceptance::test_restore_failure_rescues_read_waveform"],
            "save_failure": ["ScopeCaptureAcquisitionAcceptance::test_save_failure_keeps_directory_marked_failed"],
            "partial_save": ["ScopeCaptureAcquisitionAcceptance::test_cancel_after_read_rescues_frame",
                             "ScopeCaptureAcquisitionAcceptance::test_partial_writer_preserves_previous_run"],
        },
        "sources": [
            "lab_workflows/experiment_modules/scope_capture/acquisition.py",
            "lab_workflows/experiment_modules/scope_capture/workflow.py",
            "lab_workflows/experiment_modules/scope_capture/preparation.py",
        ],
    },
}

REAL_SLEEP = time.sleep


def copy_config(root):
    (root / "params").mkdir()
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)


def scan_params(**overrides):
    values = dict(SYSTEM_SETTLE_S=0.05, temperature_switch_on_settle_s=0.05,
                  sampling_rate_sa_s=50000.0, duration_s=0.01, run_tag="fake")
    values.update(overrides)
    return models.ScopeCaptureParams.from_external(values)


class FakeScope:
    """支持真实 SDSAcquisition.apply_config 与 capture 触发协议的假示波器。"""

    def __init__(self, points=500, rate=50000.0):
        self.state = dict(
            scale={1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0},
            offset={1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0},
            enabled={1: True, 2: False, 3: False, 4: False},
            coupling={1: "DC", 2: "DC", 3: "DC", 4: "DC"},
            impedance={1: "ONEMeg", 2: "ONEMeg", 3: "ONEMeg", 4: "ONEMeg"},
            probe={1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0},
            memory="AUTO", rate=1e9, acquire="NORMal", timebase=1e-6, delay=0.0,
            mode="AUTO", trigger_type="EDGE", source="C1", slope="RISing", level=0.0,
            status="STOP", points=points, depth="14K")
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self):
        self.connect_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1

    def trigger_run(self):
        self.state["status"] = "RUN"

    def trigger_stop(self):
        self.state["status"] = "STOP"

    def trigger_status(self):
        return self.state["status"]

    def set_memory_management(self, value):
        self.state["memory"] = str(value)

    def get_memory_management(self):
        return self.state["memory"]

    def set_sampling_rate(self, value):
        self.state["rate"] = float(value)

    def get_sampling_rate(self):
        return self.state["rate"]

    def set_acquire_type(self, value, param=None):
        self.state["acquire"] = str(value)

    def set_timebase_scale(self, value):
        self.state["timebase"] = float(value)

    def get_timebase_scale(self):
        return self.state["timebase"]

    def set_timebase_delay(self, value):
        self.state["delay"] = float(value)

    def set_channel_state(self, channel, enabled):
        self.state["enabled"][channel] = bool(enabled)

    def set_channel_scale(self, channel, value):
        self.state["scale"][channel] = float(value)

    def get_channel_scale(self, channel):
        return self.state["scale"][channel]

    def set_channel_offset(self, channel, value):
        self.state["offset"][channel] = float(value)

    def get_channel_offset(self, channel):
        return self.state["offset"][channel]

    def set_channel_coupling(self, channel, value):
        self.state["coupling"][channel] = str(value)

    def get_channel_coupling(self, channel):
        return self.state["coupling"][channel]

    def set_channel_impedance(self, channel, value):
        self.state["impedance"][channel] = str(value)

    def get_channel_impedance(self, channel):
        return self.state["impedance"][channel]

    def set_channel_probe(self, channel, value):
        self.state["probe"][channel] = float(value)

    def get_channel_probe(self, channel):
        return self.state["probe"][channel]

    def set_trigger_mode(self, value):
        self.state["mode"] = str(value)

    def get_trigger_mode(self):
        mode = self.state["mode"]
        if mode.upper() == "FTRIG":
            # FTRIG 是一次性完整帧动作，完成后自动回到 AUTO。
            self.state["mode"] = "AUTO"
        return self.state["mode"]

    def set_trigger_type(self, value):
        self.state["trigger_type"] = str(value)

    def set_trigger_source(self, value):
        self.state["source"] = str(value)

    def get_trigger_source(self):
        return self.state["source"]

    def set_trigger_slope(self, value):
        self.state["slope"] = str(value)

    def get_trigger_slope(self):
        return self.state["slope"]

    def set_trigger_level(self, value):
        self.state["level"] = float(value)

    def get_trigger_level(self):
        return self.state["level"]

    def get_actual_points(self):
        return self.state["points"]

    def get_memory_depth(self):
        return self.state["depth"]


@contextmanager
def fake_hardware(root, *, points=500, rate=50000.0):
    """只替换驱动构造与波形读取；配置、触发协议、保存与收尾执行真实业务。"""
    scope = FakeScope(points=points, rate=rate)
    temp_switch = Mock(name="signal_generator")
    temp_switch.iter_physical_routes = None
    temp_switch.get_ref_clock_source.return_value = "EXTernal"
    hw = SimpleNamespace(scope=scope, temp_switch=temp_switch, events=[], voltage={"value": 5.0})

    def read_channel(inst, channel, *_args, **_kwargs):
        hw.events.append("acquire")
        sample_rate = scope.state["rate"]
        count = scope.state["points"]
        t = np.arange(count) / sample_rate
        v = 0.8 * np.sin(2 * np.pi * 1000.0 * t) + 0.05
        return AcquisitionResult(
            channel=channel, raw_data=np.round(v * 1000).astype(np.int16),
            voltage=v, time=t,
            preamble_dict={"point_num": count, "type": "NORMal"},
            config_snapshot={"sampling_rate": sample_rate}, timestamp=time.time())

    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(StringIO()))
        stack.enter_context(patch.object(workflow, "find_project_root", return_value=root))
        stack.enter_context(patch.object(workflow, "SDSInstrument", return_value=scope))
        stack.enter_context(patch.object(workflow, "create_signal_generator", return_value=temp_switch))
        stack.enter_context(patch.object(acquisition, "sleep_cancellable",
                                         side_effect=lambda seconds: hw.events.append("settle")))
        hw.read_waveform = read_channel
        hw.acquire = stack.enter_context(patch.object(SDSAcquisition, "acquire_channel", autospec=True,
                                                      side_effect=read_channel))
        yield hw


class ScopeWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="scope-capture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.params = scan_params()

    def test_all_visible_parameters_have_real_edit_nodes_and_impacts_match_application(self):
        flow = definition()
        visible = self.params.to_external(include_hidden=False)
        displayed = [name for stage in flow["acquisition"] + flow["analysis"]
                     for name in stage["display_parameters"]]
        # mode 在采集节点可编辑，只改变显示，不使硬件或分析阶段失效。
        self.assertEqual(set(displayed), set(visible))
        self.assertEqual(len(displayed), len(set(displayed)))
        self.assertEqual(flow["parameter_impacts"]["analysis"], {})
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["run_tag"], ["A04"])
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["sampling_rate_sa_s"], ["A03", "A04"])
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["disable_temperature_control"],
                         ["A02", "A03", "A04"])

    def test_update_parameters_invalidates_stages_without_touching_hardware(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            self.assertEqual(ctx.states["A04"]["status"], "completed")
            ctx.update_parameters({"duration_s": 0.02})
            self.assertEqual({key for key, value in ctx.states.items() if value["status"] == "stale"},
                             {"A03", "A04"})
            self.assertEqual(hw.events.count("acquire"), 1)
            self.assertEqual(hw.voltage["value"], 5.0)
            with self.assertRaises(ValueError):
                ctx.update_parameters({"not_a_parameter": 1})

    def test_rerun_acquisition_creates_new_directory_and_finish_stops_scope(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            first = ctx.run_dir
            ctx.execute(["A04"])
            second = ctx.run_dir
            self.assertNotEqual(first, second)
            self.assertTrue((first / "raw/waveform.npz").is_file())
            self.assertTrue((second / "raw/waveform.npz").is_file())
            self.assertEqual(hw.events.count("acquire"), 2)
            self.assertEqual(hw.scope.state["status"], "STOP")
            ctx.finish()
            self.assertEqual(ctx.states["A05"]["status"], "completed")
            self.assertEqual(hw.scope.disconnect_calls, 1)
            self.assertEqual(hw.voltage["value"], 5.0)
            for run, is_last in ((first, False), (second, True)):
                config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(config["completion_status"], "completed")
                self.assertEqual(config["experiment_id"], "scope-capture")
                self.assertEqual(config["raw_format_version"], 1)
                self.assertEqual(len(config["scope_configuration"]["capture_attempts"]), 1)
                # 会话保持 SDS 连接继续采集；安全收尾记录只写入会话结束时最近的运行目录。
                if is_last:
                    self.assertIn("safety_shutdown", config)
                else:
                    self.assertNotIn("safety_shutdown", config)

    def test_disable_temperature_control_false_never_connects_switch(self):
        params = scan_params(disable_temperature_control=False)
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            ctx.finish()
            # 无温控门控时直接采集；温控开关设备从不连接、从不写入。
            self.assertEqual(hw.events, ["settle", "acquire"])
            hw.temp_switch.setup_dc.assert_not_called()
            self.assertEqual(hw.scope.connect_calls, 1)
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertNotIn("temperature_switch", config)
            self.assertEqual(config["completion_status"], "completed")

    def test_full_run_reuses_staged_steps_and_returns_directory(self):
        with fake_hardware(self.root) as hw:
            run_dir = workflow.run(self.params, project_root=self.root)
            self.assertTrue((run_dir / "raw/waveform.npz").is_file())
            config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "completed")
            self.assertEqual(config["data_files"], ["raw/waveform.npz"])
            self.assertEqual(config["sample_count"], 500)
            with np.load(run_dir / "raw/waveform.npz") as data:
                self.assertEqual(int(data["channel"]), 1)
                self.assertEqual(data["voltage_v"].size, 500)
                self.assertAlmostEqual(float(data["actual_rate_sa_s"]), 50000.0)
            self.assertEqual(hw.scope.disconnect_calls, 1)
            self.assertEqual(hw.voltage["value"], 5.0)

    def test_session_eof_finishes_safety_shutdown(self):
        with fake_hardware(self.root) as hw:
            from io import StringIO as IO
            ctx = workflow.Workflow(self.params, project_root=self.root, session_id="eof-test")
            serve(ctx, IO(""))
            self.assertTrue(ctx.closed)
            self.assertEqual(ctx.states["A05"]["status"], "completed")
            self.assertEqual(hw.scope.disconnect_calls, 0)  # 未连接设备即结束
            self.assertEqual(hw.voltage["value"], 5.0)

    def test_switch_can_be_enabled_in_existing_scope_session(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(disable_temperature_control=False), project_root=self.root)
            ctx.execute(["A01", "A02", "A03"])
            self.assertIsNone(ctx.temp_switch)
            ctx.update_parameters({"disable_temperature_control": True})
            ctx.execute(["A02", "A03", "A04"])
            self.assertIs(ctx.temp_switch, hw.temp_switch)
            self.assertEqual(hw.scope.connect_calls, 1)
            self.assertTrue(ctx.clock_sources["temp_switch"]["ok"])
            ctx.finish()

    def test_missing_prerequisite_and_invalid_readback_block_capture(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            with self.assertRaisesRegex(ValueError, "缺少前置"):
                ctx.execute(["A04"])
            self.assertEqual(hw.scope.connect_calls, 0)
            ctx.execute(["A01", "A02"])
            with patch.object(hw.scope, "get_channel_scale", return_value=float("nan")):
                with self.assertRaisesRegex(ValueError, "垂直档位"):
                    ctx.execute(["A03"])
            self.assertEqual(ctx.states["A03"]["status"], "quality_failed")
            with self.assertRaisesRegex(ValueError, "缺少前置"):
                ctx.execute(["A04"])
            self.assertFalse(hw.acquire.called)
            ctx.finish()

    def test_finish_uses_existing_hardware_after_unapplied_configuration(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            run = ctx.run_dir
            config_before = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            ctx.update_parameters({"vertical_scale_v_div": 2.0, "channel": 2,
                                   "disable_temperature_control": False})
            ctx.finish()
            self.assertEqual(hw.scope.state["scale"][1], 1.0)
            self.assertEqual(hw.scope.state["scale"][2], 1.0)
            after = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(after["parameters"], config_before["parameters"])

    def test_waiting_session_cancel_keeps_completed_run_completed(self):
        with fake_hardware(self.root):
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03", "A04"])
            before = (ctx.run_dir / "raw/waveform.npz").read_bytes()
            ctx.finish(WorkflowCancelled("等待下一步时取消会话"))
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "completed")
            self.assertIsNone(config["failure_reason"])
            self.assertEqual((ctx.run_dir / "raw/waveform.npz").read_bytes(), before)


class ScopeCaptureAcquisitionAcceptance(unittest.TestCase):
    """真实采集函数 + 假仪器 + 真实文件写入的温控时序与故障保护。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="scope-capture-acceptance-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.params = scan_params()

    @contextmanager
    def rig(self, *, restore_failure=None, acquire_failure=None, save_failure=None, slow=False):
        """只替换驱动 I/O；时序、目录、数组及 YAML 写入均走生产实现。"""
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(self.params, project_root=self.root)
            ctx.execute(["A01", "A02", "A03"])
            self.assertEqual(hw.voltage["value"], 5.0)
            saved_npz = np.savez
            read = hw.read_waveform

            def setup_dc(value, **kwargs):
                if value == 5.0:
                    hw.events.append("restore_attempt")
                    # 模拟间歇性恢复故障：A04 窗口内第一次恢复失败，收尾再次恢复成功。
                    if restore_failure is not None and hw.events.count("restore_attempt") == 1:
                        raise restore_failure
                hw.voltage["value"] = value
                hw.events.append("on" if value == 5.0 else "off")

            def read_channel(inst, channel, *args, **kwargs):
                if self.params.disable_temperature_control:
                    self.assertEqual(hw.voltage["value"], 0.0)
                if acquire_failure is not None:
                    raise acquire_failure
                return read(inst, channel, *args, **kwargs)

            def save_npz(path, *args, **kwargs):
                hw.events.append("save")
                if restore_failure is None:
                    self.assertEqual(hw.voltage["value"], 5.0)
                else:
                    self.assertIn("restore_attempt", hw.events)
                if save_failure is not None:
                    raise save_failure
                if slow:
                    REAL_SLEEP(0.03)
                    self.assertEqual(hw.voltage["value"], 5.0)
                return saved_npz(path, *args, **kwargs)

            hw.temp_switch.setup_dc.side_effect = setup_dc
            hw.temp_switch.set_output.side_effect = lambda enabled, **kwargs: self.assertTrue(enabled)
            hw.acquire.side_effect = read_channel
            with patch.object(workflow.np, "savez", side_effect=save_npz):
                yield SimpleNamespace(ctx=ctx, hardware=hw)

    def config(self, ctx):
        return yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))

    def test_real_order_and_slow_writer(self):
        for gated in (True, False):
            self.params = scan_params(disable_temperature_control=gated)
            with self.subTest(gated=gated), self.rig(slow=True) as rig:
                rig.ctx.execute(["A04"])
                if gated:
                    # 配置在关温控前完成；恢复先于写盘；保存后记录实际采样信息。
                    self.assertEqual(rig.hardware.events,
                                     ["off", "settle", "acquire", "restore_attempt", "on", "save"])
                else:
                    self.assertEqual(rig.hardware.events, ["settle", "acquire", "save"])
                self.assertEqual(rig.hardware.acquire.call_count, 1)
                config = self.config(rig.ctx)
                self.assertEqual(config["completion_status"], "completed")
                self.assertEqual(config["sample_count"], 500)
                if gated:
                    self.assertEqual(config["temperature_switch"]["off_settle_s"], 0.05)
                else:
                    self.assertNotIn("temperature_switch", config)
                self.assertEqual(config["scope_configuration"]["readback_points"], 500)
                self.assertEqual(config["scope_configuration"]["capture_strategy"], "auto_force_frame")
                with np.load(rig.ctx.run_dir / "raw/waveform.npz") as data:
                    self.assertTrue(np.all(np.isfinite(data["voltage_v"])))

    def test_cancel_restores_switch_and_marks_cancelled(self):
        self.params = scan_params()
        with self.rig(acquire_failure=WorkflowCancelled("采集已取消")) as rig:
            with self.assertRaises(WorkflowCancelled):
                rig.ctx.execute(["A04"])
            self.assertEqual(rig.hardware.voltage["value"], 5.0)
            self.assertEqual(rig.hardware.events,
                             ["off", "settle", "restore_attempt", "on"])
            self.assertFalse((rig.ctx.run_dir / "raw/waveform.npz").exists())
            rig.ctx.finish()
            config = self.config(rig.ctx)
            self.assertEqual(config["completion_status"], "cancelled")
            self.assertIn("采集已取消", config["failure_reason"])

    def test_acquire_failure_restores_switch(self):
        self.params = scan_params()
        error = RuntimeError("SDS 波形导出失败")
        with self.rig(acquire_failure=error) as rig:
            with self.assertRaises(RuntimeError):
                rig.ctx.execute(["A04"])
            self.assertEqual(rig.hardware.voltage["value"], 5.0)
            rig.ctx.finish()
            config = self.config(rig.ctx)
            self.assertEqual(config["completion_status"], "failed")
            self.assertIn("SDS 波形导出失败", config["failure_reason"])
            self.assertFalse((rig.ctx.run_dir / "raw/waveform.npz").exists())

    def test_restore_failure_rescues_read_waveform(self):
        self.params = scan_params()
        with self.rig(restore_failure=RuntimeError("温控恢复失败")) as rig:
            with self.assertRaises(RuntimeError):
                rig.ctx.execute(["A04"])
            self.assertIn("温控恢复失败", str(rig.ctx.states["A04"]["error"]))
            # 已读入内存的完整帧在恢复失败后仍被抢救保存，且保留原始错误。
            self.assertTrue((rig.ctx.run_dir / "raw/waveform.npz").is_file())
            rig.ctx.finish()
            config = self.config(rig.ctx)
            self.assertEqual(config["completion_status"], "failed")
            self.assertIn("温控恢复失败", config["failure_reason"])

    def test_save_failure_keeps_directory_marked_failed(self):
        self.params = scan_params()
        with self.rig(save_failure=OSError("磁盘已满")) as rig:
            with self.assertRaises(OSError):
                rig.ctx.execute(["A04"])
            self.assertEqual(rig.hardware.voltage["value"], 5.0)
            # 保存发生在温控恢复之后；失败不留下半成品波形。
            self.assertEqual(rig.hardware.events[-1], "save")
            rig.ctx.finish()
            config = self.config(rig.ctx)
            self.assertEqual(config["completion_status"], "failed")
            self.assertIn("磁盘已满", config["failure_reason"])
            self.assertFalse((rig.ctx.run_dir / "raw/waveform.npz").exists())

    def test_cancel_after_read_rescues_frame(self):
        with self.rig() as rig:
            def cancelled():
                if rig.hardware.events.count("acquire"):
                    raise WorkflowCancelled("完整帧读完后取消")
            with patch.object(acquisition, "check_cancelled", side_effect=cancelled):
                with self.assertRaisesRegex(WorkflowCancelled, "读完后取消"):
                    rig.ctx.execute(["A04"])
            self.assertEqual(rig.hardware.events[-3:], ["restore_attempt", "on", "save"])
            with np.load(rig.ctx.run_dir / "raw/waveform.npz") as data:
                self.assertEqual(data["voltage_v"].size, 500)
            self.assertEqual(self.config(rig.ctx)["completion_status"], "cancelled")

    def test_partial_writer_preserves_previous_run(self):
        with self.rig() as rig:
            rig.ctx.execute(["A04"])
            first = rig.ctx.run_dir
            before = (first / "raw/waveform.npz").read_bytes()
            def half_write(stream, **kwargs):
                self.assertEqual(rig.hardware.voltage["value"], 5.0)
                stream.write(b"incomplete zip")
                raise OSError("真实半写故障")
            with patch.object(np, "savez", side_effect=half_write):
                with self.assertRaisesRegex(OSError, "半写故障"):
                    rig.ctx.execute(["A04"])
            self.assertEqual((first / "raw/waveform.npz").read_bytes(), before)
            self.assertFalse(list((rig.ctx.run_dir / "raw").iterdir()))
            self.assertEqual(self.config(rig.ctx)["completion_status"], "failed")

    def test_acquire_and_restore_failure_keep_original_error(self):
        with self.rig(acquire_failure=ValueError("原始读数错误"),
                      restore_failure=RuntimeError("恢复故障")) as rig:
            with self.assertRaisesRegex(ValueError, "原始读数错误"):
                rig.ctx.execute(["A04"])
            self.assertEqual(rig.ctx.states["A04"]["status"], "quality_failed")
            rig.ctx.finish()
            self.assertIn("原始读数错误", self.config(rig.ctx)["failure_reason"])


def fake_session_main():
    """API 回归实际启动这个模块，仪器边界在子进程内替换。"""
    from .session import main
    root = Path(os.environ["SCOPE_CAPTURE_TEST_ROOT"])
    with fake_hardware(root), redirect_stdout(sys.__stdout__):
        main()


if __name__ == "__main__":
    if "SCOPE_CAPTURE_TEST_ROOT" in os.environ:
        fake_session_main()
    else:
        unittest.main()
