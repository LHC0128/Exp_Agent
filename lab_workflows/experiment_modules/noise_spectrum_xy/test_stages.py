"""分步执行、失效传播及真实命令子进程的无硬件回归。"""

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from . import workflow
from .models import NoiseSpectrumXYParams
from .session import serve
from .stages import ACQUISITION, definition, validate_selection
from .test_sine_mode import copy_config, fake_hardware, scan_params


class StageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xy-stages-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        copy_config(self.root)

    def prepare(self, ctx):
        ctx.execute([stage.id for stage in ACQUISITION[:11]])

    def test_display_parameters_cover_schema_and_share_only_business_inputs(self):
        flow = definition()
        visible = {name for stages in (flow["acquisition"], flow["analysis"])
                   for stage in stages for name in stage["display_parameters"]}
        self.assertEqual(visible, set(NoiseSpectrumXYParams().to_external()))
        users = lambda name: [stage["id"] for stage in flow["acquisition"]
                              if name in stage["display_parameters"]]
        self.assertEqual(users("TEMP_SWITCH_ON_SETTLE_S"), ["A08", "A10", "A12"])
        self.assertEqual(users("FIXED_PARAMS.main_magnetic_field"), ["A04"])
        self.assertEqual(users("FIXED_PARAMS.temperature"), ["A05"])
        self.assertEqual(users("TARGET_NOISE_FREQ_POINTS"), ["A12"])
        self.assertEqual(users("HF2_DAQ_DURATION"), ["A11", "A12"])
        self.assertEqual(flow["acquisition"][0]["display_parameters"], [])

    def test_parameter_impact_preview_matches_actual_updates_without_connecting(self):
        ctx = workflow.Workflow(scan_params(), project_root=self.root)
        impacts = definition()["parameter_impacts"]["acquisition"]
        for changes in ({"TEMP_SWITCH_ON_SETTLE_S": 3.0},
                        {"FIXED_PARAMS.temperature": 99.0},
                        {"RUN_TAG": "preview", "HF2_DAQ_DURATION": 2.0}):
            with self.subTest(changes=changes):
                ctx.states = {stage.id: {"status": "completed", "error": ""} for stage in ACQUISITION}
                expected = {stage_id for name in changes for stage_id in impacts[name]}
                ctx.update_parameters(changes)
                self.assertEqual({key for key, state in ctx.states.items() if state["status"] == "stale"}, expected)
                self.assertFalse(ctx.devices)
                self.assertNotIn("A14", expected)

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

    def test_quality_gate_retry_keeps_same_connections(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(replace(scan_params(), xy_phase_cal_max_iter=2), project_root=self.root)
            ctx.execute([stage.id for stage in ACQUISITION[:9]])
            hw.samples.side_effect = [dict(r=0.0, x=0.0, y=0.0, theta=0.0)] * 2
            ctx.execute(["A10", "A11", "A12"])
            self.assertEqual(ctx.states["A10"]["status"], "quality_failed")
            self.assertTrue((ctx.run_dir / "experiment_config.yaml").is_file())
            with self.assertRaisesRegex(ValueError, "A10 XY 迭代校相"):
                ctx.execute(["A11", "A12"])
            hw.samples.side_effect = [dict(r=1.0, x=1.0, y=0.0, theta=0.0)]
            ctx.execute(["A10"])
            self.assertEqual(ctx.states["A10"]["status"], "completed")
            self.assertFalse(ctx.closed)
            ctx.finish()

    def test_parameters_invalidation_mode_restore_and_unique_scan_directories(self):
        with fake_hardware(self.root) as hw:
            hw.rates.side_effect = lambda hfi, config, **kwargs: 800.0 if config.time_constant == scan_params().hf2_daq_tc else 1200.0
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            self.prepare(ctx)
            ctx.execute(["A12", "A13"])
            first = ctx.run_dir
            original = (first / "experiment_config.yaml").read_bytes()
            self.assertEqual(ctx.demod_mode, "phase")
            with self.assertRaisesRegex(ValueError, "DAQ 准备"):
                ctx.execute(["A12"])
            ctx.update_parameters({"RUN_TAG": "new-run"})
            self.assertEqual(ctx.states["A10"]["status"], "completed")
            ctx.execute(["A11", "A12"])
            self.assertNotEqual(ctx.run_dir, first)
            self.assertEqual((first / "experiment_config.yaml").read_bytes(), original)
            second = ctx.run_dir
            ctx.execute(["A12"])
            self.assertNotEqual(ctx.run_dir, second)
            ctx.update_parameters({"FIXED_PARAMS.temperature": 99.0})
            self.assertEqual(ctx.states["A04"]["status"], "completed")
            self.assertEqual(ctx.states["A05"]["status"], "stale")
            self.assertEqual(ctx.states["A12"]["status"], "stale")
            before = ctx.revision
            with self.assertRaises(ValueError):
                ctx.update_parameters({"XY_CALIB_ENVELOPE_V": 1000})
            self.assertEqual(ctx.revision, before)
            ctx.finish()
            self.assertEqual(ctx.outputs["X"], "OFF")
            self.assertEqual(ctx.outputs["共用触发"], "ON")

    def test_mode_drift_is_rejected_and_error_kept_during_cleanup(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            self.prepare(ctx)
            hw.devices["dg_comp"].get_burst_state.return_value = False
            with self.assertRaisesRegex(RuntimeError, "模式已改变") as caught:
                ctx.execute(["A12"])
            hw.devices["dg_comp"].setup_dc.side_effect = OSError("关断后归零失败")
            ctx.finish(caught.exception)
            self.assertEqual(ctx.states["A14"]["status"], "failed")
            self.assertIn("模式已改变", ctx.states["A12"]["error"])

    def test_clock_resync_failure_shuts_outputs_before_disconnect(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            self.prepare(ctx)
            events = []
            xy = hw.devices["dg_comp"]
            xy.set_output.side_effect = lambda enabled, *, channel: events.append((channel, enabled))
            xy.disconnect.side_effect = lambda: events.append("disconnect")
            error = RuntimeError("参考时钟回读失败")
            hw.clocks.side_effect = error
            with self.assertRaises(RuntimeError) as caught:
                ctx.execute(["A03"])
            self.assertIs(caught.exception, error)
            ctx.finish(caught.exception)
            self.assertEqual(events, [(1, False), (2, False), "disconnect"])
            self.assertEqual(ctx.states["A03"]["error"], str(error))
            self.assertEqual(ctx.states["A14"]["status"], "completed")
            self.assertEqual(ctx.outputs["X"], "OFF")

    def test_real_stdin_subprocess_rejects_commands_and_eof_shuts_down(self):
        env = dict(os.environ, XY_TEST_ROOT=str(self.root),
            LAB_TYPED_PARAMETERS=json.dumps(scan_params().to_external() | {"TARGET_NOISE_FREQ_POINTS": 2, "session_id": "fake-session"}))
        env.pop("LAB_CANCEL_FILE", None)
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", __name__], cwd=self.root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env)
        commands = [
            dict(request_id="bad", revision=0, action="steps", step_ids=["A03"]),
            dict(request_id="prepare", revision=0, action="steps", step_ids=[stage.id for stage in ACQUISITION[:11]]),
            dict(request_id="prepare", revision=1, action="steps", step_ids=["A12"]),
            dict(request_id="scan", revision=1, action="steps", step_ids=["A12"]),
            dict(request_id="old", revision=1, action="steps", step_ids=["A12"]),
        ]
        output, error = process.communicate("".join(json.dumps(command) + "\n" for command in commands), timeout=30)
        self.assertEqual(process.returncode, 0, output + error)
        snapshot = json.loads((self.root / "data/.gui_jobs/fake-session/session.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["status"], "closed")
        self.assertEqual(snapshot["steps"]["A14"]["status"], "completed")
        self.assertEqual(snapshot["outputs"]["X"], "OFF")
        self.assertEqual(snapshot["steps"]["A12"]["status"], "completed")
        self.assertIn("缺少前置", output)
        self.assertIn("修订已改变", output)
        self.assertIn("重复或无效的请求编号", output)
        raw = self.root / "data/Noise_Spectrum_XY_Ctrl" / snapshot["run_id"] / "raw"
        self.assertEqual(sorted(path.name for path in raw.glob("waveform_C*.npy")),
                         ["waveform_C0000.npy", "waveform_C0001.npy"])


def fake_session_main():
    root = Path(os.environ["XY_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    params = NoiseSpectrumXYParams.from_external(payload)
    with fake_hardware(root) as hw, redirect_stdout(sys.__stdout__):
        hw.rates.side_effect = lambda hfi, config, **kwargs: 800.0 if config.time_constant == params.hf2_daq_tc else 1200.0
        serve(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_session_main()
