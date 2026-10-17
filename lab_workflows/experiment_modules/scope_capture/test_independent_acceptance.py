"""独立验收：真实步骤与 writer 的边界，固定分析输入/产物的完整性。"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import yaml

from . import acquisition, analysis, stages, workflow
from .models import ScopeCaptureParams
from .test_analysis import make_run
from .test_workflow import ROOT, copy_config, fake_hardware, scan_params


class ScopeIndependentAcceptance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="scope-independent-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)

    def test_clock_readback_mismatch_blocks_capture_and_cleans_connected_devices(self):
        with fake_hardware(self.root) as hw:
            hw.temp_switch.get_ref_clock_source.return_value = "INTernal"
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            ctx.execute(["A01"])
            with self.assertRaisesRegex(RuntimeError, "时钟源设置失败"):
                ctx.execute(["A02"])
            self.assertEqual(ctx.states["A02"]["status"], "failed")
            with self.assertRaisesRegex(ValueError, "缺少前置"):
                ctx.execute(["A03", "A04"])
            hw.acquire.assert_not_called()
            self.assertIsNone(ctx.run_dir)
            self.assertEqual(hw.scope.disconnect_calls, 1)
            hw.temp_switch.disconnect.assert_called_once()
            self.assertTrue(ctx.shutdown["completed"])

    def test_gate_window_writes_nothing_and_restore_cannot_mutate_frozen_frame(self):
        with fake_hardware(self.root) as hw, ExitStack() as stack:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            ctx.execute(["A01", "A02", "A03"])
            voltage = {"value": 5.0}
            frame = {}
            events = []
            original_write, original_save = Path.write_text, np.savez

            def setup_dc(value, **kwargs):
                voltage["value"] = value
                events.append("restore" if value else "off")
                if value and frame:
                    frame["result"].voltage[:] = -123.0
                    frame["result"].time[:] = -1.0

            def read(inst, channel, *args, **kwargs):
                self.assertEqual(voltage["value"], 0.0)
                result = hw.read_waveform(inst, channel, *args, **kwargs)
                frame["result"] = result
                frame["expected_v"] = result.voltage.copy()
                frame["expected_t"] = result.time.copy()
                events.append("read")
                return result

            def write_text(path, *args, **kwargs):
                self.assertEqual(voltage["value"], 5.0, str(path))
                return original_write(path, *args, **kwargs)

            def save_npz(stream, **values):
                self.assertEqual(voltage["value"], 5.0)
                self.assertEqual(events[-1], "restore")
                events.append("write")
                return original_save(stream, **values)

            hw.temp_switch.setup_dc.side_effect = setup_dc
            hw.acquire.side_effect = read
            stack.enter_context(patch.object(Path, "write_text", new=write_text))
            stack.enter_context(patch.object(np, "savez", side_effect=save_npz))
            ctx.execute(["A04"])
            self.assertEqual(events, ["off", "read", "restore", "write"])
            with np.load(ctx.run_dir / "raw/waveform.npz", allow_pickle=False) as raw:
                np.testing.assert_array_equal(raw["voltage_v"], frame["expected_v"])
                np.testing.assert_array_equal(raw["time_s"], frame["expected_t"])
            self.assertFalse(list((ctx.run_dir / "raw").glob("*.tmp")))
            ctx.finish()

    def test_gate_disabled_keeps_exact_system_wait_and_later_enable_reuses_scope(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(disable_temperature_control=False,
                SYSTEM_SETTLE_S=.137), project_root=self.root)
            ctx.execute(["A01", "A02", "A03"])
            with patch.object(acquisition, "sleep_cancellable") as sleep:
                ctx.execute(["A04"])
            sleep.assert_called_once_with(.137)
            hw.temp_switch.setup_dc.assert_not_called()
            before = (ctx.run_dir / "raw/waveform.npz").read_bytes()
            first = ctx.run_dir
            ctx.update_parameters({"disable_temperature_control": True})
            self.assertIsNone(ctx.temp_switch)
            with self.assertRaisesRegex(ValueError, "缺少前置"):
                ctx.execute(["A04"])
            ctx.execute(["A02", "A03", "A04"])
            self.assertIs(ctx.temp_switch, hw.temp_switch)
            self.assertEqual(hw.scope.connect_calls, 1)
            self.assertNotEqual(first, ctx.run_dir)
            self.assertEqual((first / "raw/waveform.npz").read_bytes(), before)
            self.assertTrue(ctx.clock_sources["temp_switch"]["ok"])
            ctx.finish()

    def test_short_frame_retry_only_saves_complete_frame_and_resets_diagnostics(self):
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(disable_temperature_control=False), project_root=self.root)
            ctx.execute(["A01", "A02", "A03"])
            calls = []

            def read(inst, channel, *args, **kwargs):
                result = hw.read_waveform(inst, channel, *args, **kwargs)
                calls.append(result)
                if len(calls) == 1:
                    result.time = result.time[:-1]
                    result.voltage = result.voltage[:-1]
                return result

            hw.acquire.side_effect = read
            ctx.execute(["A04"])
            first = ctx.run_dir
            with np.load(first / "raw/waveform.npz", allow_pickle=False) as raw:
                self.assertEqual(raw["voltage_v"].size, 500)
                attempts = json.loads(str(raw["configuration_json"].item()))["capture_attempts"]
                self.assertEqual([item["exported_points"] for item in attempts], [499, 500])
                self.assertIn("failure", attempts[0])
            ctx.execute(["A04"])
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(len(config["scope_configuration"]["capture_attempts"]), 1)
            self.assertTrue((first / "raw/waveform.npz").is_file())
            ctx.finish()

    def test_raw_sha_change_with_same_size_mtime_invalidates_all_analysis(self):
        run = make_run(self.root / "data/Scope_Capture", "fingerprint", samples=20001)
        analysis.analyze(run)
        old = analysis.AnalysisWorkflow(run)
        raw_path = run / "raw/waveform.npz"
        stat = raw_path.stat()
        with np.load(raw_path, allow_pickle=False) as raw:
            values = {key: raw[key].copy() for key in raw.files}
        values["voltage_v"][-1] += .125
        np.savez(raw_path, **values)
        self.assertEqual(raw_path.stat().st_size, stat.st_size)
        os.utime(raw_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        current = analysis.AnalysisWorkflow(run)
        self.assertEqual({record["status"] for record in current.states.values()}, {"stale"})
        self.assertNotEqual(old.fingerprint, current.fingerprint)
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            current.execute(["B02"])
        self.assertFalse(list((run / "results").glob("before_*")))
        current.execute(["B01", "B02", "B03"])
        backups = list((run / "results").glob("before_*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual({path.name for path in backups[0].iterdir()},
            {name for stage in stages.ANALYSIS for name in stage.artifacts})

    def test_auto_normal_single_trigger_timeout_never_reads_a_stale_frame(self):
        for mode in ("AUTO", "NORMal", "SINGle"):
            with self.subTest(mode=mode), fake_hardware(self.root) as hw:
                ctx = workflow.Workflow(scan_params(trigger_mode=mode,
                    disable_temperature_control=False), project_root=self.root)
                ctx.execute(["A01", "A02", "A03"])
                elapsed = [0.0]

                def sleep(seconds):
                    elapsed[0] += seconds

                with patch.object(hw.scope, "get_trigger_mode", return_value="FTRIG"), \
                        patch.object(hw.scope, "trigger_status", return_value="WAIT"):
                    with self.assertRaises(TimeoutError):
                        acquisition.capture(hw.scope, ctx.acquirer, ctx.scope_config,
                            ctx.scope_snapshot, sleep=sleep, monotonic=lambda: elapsed[0])
                hw.acquire.assert_not_called()
                self.assertIsNone(ctx.run_dir)
                ctx.finish()

    def test_manifest_fixture_and_editable_diagram_preserve_the_real_contract(self):
        fixture = json.loads((ROOT / "GUI/frontend/src/pages/ScopeCapturePage.fixture.json").read_text(encoding="utf-8"))
        manifest = json.loads(json.dumps(stages.definition()))
        self.assertEqual(fixture["flow"], manifest)
        actual_fields = json.loads(json.dumps(ScopeCaptureParams().schema()["fields"]))
        self.assertEqual(fixture["schema"]["fields"], actual_fields)
        self.assertEqual(len(actual_fields), 14)
        self.assertEqual(len(ScopeCaptureParams().to_external()), 17)
        self.assertEqual(stages.ACQUISITION[-1].dependencies, ())
        diagram = json.loads((ROOT / "lab_workflows/experiment_modules/scope_capture/示波器采集.excalidraw").read_text(encoding="utf-8"))
        labels = "\n".join(element.get("text", "") for element in diagram["elements"])
        for stage in (*stages.ACQUISITION, *stages.ANALYSIS):
            self.assertIn(stage.id, labels)
            for gate in stage.quality_gates:
                self.assertIn(gate, labels)


if __name__ == "__main__":
    unittest.main()
