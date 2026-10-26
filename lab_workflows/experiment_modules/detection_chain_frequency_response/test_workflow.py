"""分步执行、失效传播及真实命令子进程的无硬件回归。"""

from contextlib import ExitStack, contextmanager, redirect_stdout
from io import StringIO
from itertools import count
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np
import yaml

from . import preparation, acquisition, workflow
from .models import DetectionChainFrequencyResponseParams
from .session import serve
from .stages import ACQUISITION, definition
from ...common import load_mapping
from ...steps import routed_signal_generator

ROOT = Path(__file__).resolve().parents[3]


def copy_config(root):
    """只复制本实验需要的配置，所有测试数据留在临时目录。"""
    (root / "params").mkdir()
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)


def scan_params():
    """小扫描轴：3 个频点、短采集，配合假采样率通过基带覆盖检查。"""
    return DetectionChainFrequencyResponseParams(
        am_freq_start_hz=85000.0, am_freq_stop_hz=95000.0, am_freq_points=3,
        system_settle_s=0.1, hf2_osc_freq_hz=90000.0,
        hf2_daq_duration_s=0.5, hf2_daq_rate_sa_s=12000.0,
    )


FAKE_RATE = 12000.0


@contextmanager
def fake_hardware(root):
    """在驱动构造和 HF2 I/O 边界注入 Mock，保留设备会话与路由逻辑。"""
    mapping = load_mapping(root)
    devices = {name: Mock(name=name) for name in ("tec", "aom", "laser", "temp_switch", "hf2")}
    devices["tec"].get_temperature.return_value = 100.0
    devices["hf2"].model = "HF2"
    devices["hf2"].acquisition_mode = "auto"
    devices["hf2"].capabilities = {"voltage_inputs": 2}
    devices["hf2"].sig_in_path.side_effect = lambda index: f"/dev18246/sigins/{index}"
    devices["hf2"].demod_path.side_effect = lambda index: f"/dev18246/demods/{index}"
    devices["hf2"].get_double.side_effect = lambda path: 0.0 if str(path).endswith("/phaseshift") else 30.0
    by_resource = {mapping[key]["resource"]: devices[name] for name, key in (
        ("aom", "Probe_AOM_Carrier"), ("laser", "Pump_laser_power"),
        ("temp_switch", "Temp_Switch"))}
    rng = np.random.default_rng(7)
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(StringIO()))
        for name, target in (("tec", "tec_controller.TECInstrument"),
                             ("hf2", "lab_workflows.devices.create_lockin")):
            factory = stack.enter_context(patch(target, return_value=devices[name]))
            stack.enter_context(patch.object(preparation, target.rsplit(".", 1)[1], factory))
        stack.enter_context(patch.object(routed_signal_generator, "create_signal_generator",
                                         side_effect=lambda cfg: by_resource[cfg["resource"]]))
        clocks = stack.enter_context(patch.object(preparation, "synchronize_connected_clocks",
                                                  return_value={"verified": True}))
        for name in ("configure_signal_input", "configure_oscillator"):
            stack.enter_context(patch.object(acquisition.demod, name))
        rates = stack.enter_context(patch.object(acquisition.demod, "configure_demodulator",
                                                 side_effect=lambda *args, **kwargs: FAKE_RATE))
        acquire = stack.enter_context(patch.object(acquisition.daq, "acquire_data",
            side_effect=lambda hfi, config=None, **kwargs: [SimpleNamespace(
                values=rng.normal(size=config.grid_cols), signal_name="demods/0/sample.y")]))
        ticks = count(0.0, 0.05)
        stack.enter_context(patch("time.monotonic", side_effect=lambda: next(ticks)))
        stack.enter_context(patch("time.sleep"))
        yield SimpleNamespace(devices=devices, clocks=clocks, rates=rates, acquire=acquire)


class DetectionChainWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dcfr-workflow-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)

    def prepare(self, ctx):
        ctx.execute([stage.id for stage in ACQUISITION[:6]])

    def test_display_parameters_cover_schema_with_single_edit_entry(self):
        flow = definition()
        visible = {name for stages in (flow["acquisition"], flow["analysis"])
                   for stage in stages for name in stage["display_parameters"]}
        self.assertEqual(visible, set(DetectionChainFrequencyResponseParams().to_external(include_hidden=False)))
        for stage_id in ("A01", "A02", "A03", "A08"):
            stage = next(item for item in flow["acquisition"] if item["id"] == stage_id)
            self.assertEqual(stage["display_parameters"], [], stage_id)
        users = lambda name: [stage["id"] for stage in flow["acquisition"]
                              if name in stage["display_parameters"]]
        self.assertEqual(users("FIXED_PARAMS.temperature"), ["A04"])
        self.assertEqual(users("HF2_OSC_FREQ_HZ"), ["A05"])
        self.assertEqual(users("RUN_TAG"), ["A07"])
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["FIXED_PARAMS.temperature"],
                         ["A04", "A05", "A06", "A07"])
        self.assertEqual(flow["parameter_impacts"]["acquisition"]["PROBE_AOM_AM_AMPLITUDE_VPP"], ["A07"])
        self.assertEqual(flow["parameter_impacts"]["analysis"], {})

    def test_parameter_impact_preview_matches_actual_updates_without_connecting(self):
        ctx = workflow.Workflow(scan_params(), project_root=self.root)
        impacts = definition()["parameter_impacts"]["acquisition"]
        for changes in ({"RUN_TAG": "preview"},
                        {"FIXED_PARAMS.temperature": 99.0},
                        {"AM_FREQ_POINTS": 5, "HF2_DAQ_RATE_SA_S": 24000.0}):
            with self.subTest(changes=changes):
                ctx.states = {stage.id: {"status": "completed", "error": ""} for stage in ACQUISITION}
                expected = {stage_id for name in changes for stage_id in impacts[name]}
                ctx.update_parameters(changes)
                self.assertEqual({key for key, state in ctx.states.items() if state["status"] == "stale"}, expected)
                self.assertFalse(ctx.devices)
                self.assertNotIn("A08", expected)

    def test_explicit_prerequisites_sorted_selection_and_preflight_preserves_hardware(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            with self.assertRaisesRegex(ValueError, "A02 设备连接"):
                ctx.execute(["A03"])
            self.assertFalse(ctx.devices)
            ctx.execute(["A02", "A01"])
            self.assertEqual(ctx.states["A02"]["status"], "completed")
            hw.clocks.assert_not_called()
            ctx.execute(["A01"])
            self.assertEqual(ctx.states["A02"]["status"], "completed")
            ctx.finish()

    def test_scan_creates_unique_directories_and_finish_shuts_outputs(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            self.prepare(ctx)
            ctx.execute(["A07"])
            first = ctx.run_dir
            original = (first / "experiment_config.yaml").read_bytes()
            self.assertTrue((first / "raw/frequency_response.npz").exists())
            self.assertTrue((first / "results/results_summary.yaml").exists())
            self.assertEqual(ctx.states["A07"]["status"], "completed")
            self.assertIn("raw/frequency_response.npz", ctx.states["A07"]["artifacts"])
            ctx.execute(["A07"])
            self.assertNotEqual(ctx.run_dir, first)
            self.assertEqual((first / "experiment_config.yaml").read_bytes(), original)
            ctx.update_parameters({"RUN_TAG": "new-run"})
            self.assertEqual(ctx.states["A07"]["status"], "stale")
            ctx.update_parameters({"FIXED_PARAMS.temperature": 99.0})
            self.assertEqual(ctx.states["A04"]["status"], "stale")
            self.assertEqual(ctx.states["A07"]["status"], "stale")
            before = ctx.revision
            with self.assertRaises(ValueError):
                ctx.update_parameters({"PROBE_AOM_CARRIER_AMPLITUDE_VPP": 1000.0})
            self.assertEqual(ctx.revision, before)
            ctx.finish()
            self.assertEqual(ctx.outputs["AOM 载波"], "OFF")
            self.assertEqual(ctx.outputs["AOM AM"], "OFF")
            self.assertEqual(ctx.outputs["温控"], "ON")
            aom = hw.devices["aom"]
            for channel in (ctx.channels["carrier"], ctx.channels["am"]):
                self.assertEqual([entry for entry in aom.set_output.call_args_list
                                  if entry.kwargs["channel"] == channel][-1], call(False, channel=channel))
            temp = hw.devices["temp_switch"]
            temp.setup_dc.assert_any_call(5.0, channel=ctx.channels["temp_switch"])
            self.assertEqual([entry for entry in temp.set_output.call_args_list
                              if entry.kwargs["channel"] == ctx.channels["temp_switch"]][-1],
                             call(True, channel=ctx.channels["temp_switch"]))
            hw.devices["tec"].disconnect.assert_called_once()
            hw.devices["laser"].disconnect.assert_not_called()
            # 安全收尾记录写入当前（最后一次扫描）目录，旧目录保持采集时的原始配置。
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertIn("safety_shutdown", config)

    def test_hf2_gate_rejects_scan_before_configuration(self):
        with fake_hardware(self.root):
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            # 依赖校验先行拦截缺前置的选择；内部闸门防御阶段状态与仪器漂移不一致的路径。
            with self.assertRaisesRegex(ValueError, "缺少前置模块"):
                ctx.execute(["A07"])
            ctx.execute([stage.id for stage in ACQUISITION[:3]])
            with self.assertRaisesRegex(ValueError, "HF2 尚未配置"):
                ctx._execute_step("A07")
            self.assertEqual(ctx.states["A07"]["status"], "pending")
            ctx.finish()

    def test_clock_failure_disconnects_without_touching_outputs(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            ctx.execute(["A02", "A01"])
            error = RuntimeError("参考时钟回读失败")
            hw.clocks.side_effect = error
            with self.assertRaises(RuntimeError) as caught:
                ctx.execute(["A03"])
            self.assertIs(caught.exception, error)
            ctx.finish(caught.exception)
            # 时钟失败且未开启任何输出：直接断开连接，不执行输出关闭动作。
            hw.devices["aom"].set_output.assert_not_called()
            hw.devices["temp_switch"].setup_dc.assert_not_called()
            hw.devices["aom"].disconnect.assert_called_once()
            self.assertEqual(ctx.states["A03"]["error"], str(error))
            self.assertEqual(ctx.states["A08"]["status"], "completed")

    def test_real_stdin_subprocess_rejects_commands_and_eof_shuts_down(self):
        env = dict(os.environ, DCFR_TEST_ROOT=str(self.root),
            LAB_TYPED_PARAMETERS=json.dumps(scan_params().to_external() | {"AM_FREQ_POINTS": 2, "session_id": "fake-session"}))
        env.pop("LAB_CANCEL_FILE", None)
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", __name__], cwd=self.root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env)
        commands = [
            dict(request_id="bad", revision=0, action="steps", step_ids=["A03"]),
            dict(request_id="prepare", revision=0, action="steps", step_ids=[stage.id for stage in ACQUISITION[:7]]),
            dict(request_id="scan-again", revision=1, action="steps", step_ids=["A07"]),
            dict(request_id="old", revision=1, action="steps", step_ids=["A07"]),
        ]
        output, error = process.communicate("".join(json.dumps(command) + "\n" for command in commands), timeout=60)
        self.assertEqual(process.returncode, 0, output + error)
        snapshot = json.loads((self.root / "data/.gui_jobs/fake-session/session.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["status"], "closed")
        self.assertEqual(snapshot["steps"]["A08"]["status"], "completed")
        self.assertEqual(snapshot["steps"]["A07"]["status"], "completed")
        self.assertEqual(snapshot["outputs"]["AOM 载波"], "OFF")
        self.assertIn("缺少前置", output)
        self.assertIn("修订已改变", output)
        run_dir = self.root / "data/Detection_Chain_Frequency_Response" / snapshot["run_id"]
        self.assertTrue((run_dir / "raw/frequency_response.npz").exists())
        self.assertEqual(sorted(path.name for path in (run_dir / "raw").glob("waveform_*.npy")),
                         ["waveform_0000.npy", "waveform_0001.npy"])


def fake_session_main():
    root = Path(os.environ["DCFR_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    params = DetectionChainFrequencyResponseParams.from_external(payload)
    with fake_hardware(root), redirect_stdout(sys.__stdout__):
        serve(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_session_main()
