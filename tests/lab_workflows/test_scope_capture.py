"""示波器采集无硬件回归测试。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np

from lab_workflows.common import WorkflowCancelled
from lab_workflows.experiment_modules.scope_capture import ADAPTER, DEFINITION, ScopeCaptureParams
from lab_workflows.experiment_modules.scope_capture.analysis import calculate_psd, envelope_indices, validate_waveform, analyze
from lab_workflows.experiment_modules.scope_capture import workflow
from lab_workflows.steps.run_directory import RunDirectory
from sds_acquisition import SDSAcquisition


class ScopeCaptureTests(unittest.TestCase):
    def test_schema_and_defaults(self):
        self.assertEqual(ADAPTER.defaults().to_external(), ScopeCaptureParams().to_external())
        self.assertEqual({f["name"] for f in DEFINITION.schema()["fields"]},
                         {"mode", "sampling_rate_sa_s", "duration_s", "channel", "trigger_channel", "trigger_mode", "trigger_slope", "trigger_level_v", "vertical_scale_v_div", "vertical_offset_v", "disable_temperature_control", "temperature_switch_off_settle_s", "temperature_switch_on_settle_s", "run_tag"})
        migrated = ScopeCaptureParams.from_external({"channel": 3}, schema_version=1)
        self.assertEqual((migrated.trigger_channel, migrated.vertical_scale_v_div), (3, 1.0))
        self.assertEqual(ScopeCaptureParams(channel=4).trigger_channel, 4)
        self.assertEqual(ScopeCaptureParams.from_external({"channel": 2}, schema_version=2).trigger_channel, 2)
        self.assertEqual(ScopeCaptureParams.schema_version, 2)
        self.assertEqual((migrated.trigger_mode, migrated.trigger_slope, migrated.trigger_level_v, migrated.vertical_offset_v),
                         ("AUTO", "RISing", 0.0, 0.0))
        self.assertEqual(DEFINITION.preflight({}), [])
        for values in [{"channel": 5}, {"run_tag": "../bad"}, {"duration_s": 0},
                       {"sampling_rate_sa_s": 1e9, "duration_s": 1}, {"mode": "fft"},
                       {"trigger_channel": 5}, {"trigger_mode": "FTRIG"}, {"trigger_slope": "bad"},
                       {"vertical_scale_v_div": 0}, {"vertical_offset_v": float("nan")},
                       {"trigger_level_v": float("inf")}]:
            self.assertTrue(ADAPTER.preflight(values))

    def test_temperature_switch_mapping_is_checked_when_enabled(self):
        with patch.object(workflow, "load_mapping", return_value={
            "scope_waveform": {"instrument": "sds_acquisition", "resource": "fake"},
        }), patch("lab_workflows.experiment_modules.scope_capture.definition.load_mapping", return_value={
            "scope_waveform": {"instrument": "sds_acquisition", "resource": "fake"},
        }):
            errors = DEFINITION.preflight({"disable_temperature_control": True})
        self.assertIn("Temp_Switch", "；".join(errors))

    def test_configure_applies_vertical_and_trigger_settings(self):
        scope = Mock()
        scope.get_channel_coupling.return_value = "DC"
        scope.get_channel_impedance.return_value = "ONEMeg"
        scope.get_channel_probe.return_value = 1.0
        scope.get_memory_management.return_value = "FSRate"
        scope.get_sampling_rate.return_value = 2000.0
        scope.get_actual_points.return_value = 2000
        scope.get_channel_scale.return_value = 0.5
        scope.get_channel_offset.return_value = -0.25
        scope.get_trigger_mode.return_value = "NORMal"
        scope.get_trigger_source.return_value = "C3"
        scope.get_trigger_slope.return_value = "FALLing"
        scope.get_trigger_level.return_value = 0.1
        scope.get_timebase_scale.return_value = 0.05
        scope.get_memory_depth.return_value = "2000"
        acquirer = SDSAcquisition(scope)
        params = ScopeCaptureParams(channel=2, trigger_channel=3, trigger_mode="NORMal",
                                    trigger_slope="FALLing", trigger_level_v=0.2,
                                    vertical_scale_v_div=0.4, vertical_offset_v=-0.1,
                                    sampling_rate_sa_s=2000, duration_s=1)
        with patch("sds_acquisition.acquire.time.sleep"):
            applied, snapshot = workflow.configure(scope, params, acquirer)
        self.assertEqual([c.enabled for c in applied.channels], [False, True, True, False])
        self.assertEqual((applied.channels[1].scale, applied.channels[1].offset), (0.4, -0.1))
        self.assertEqual((applied.channels[2].scale, applied.channels[2].offset), (0.5, -0.25))
        scope.set_channel_scale.assert_any_call(2, 0.4)
        scope.set_channel_offset.assert_any_call(2, -0.1)
        scope.set_channel_state.assert_any_call(3, True)
        scope.set_trigger_mode.assert_called_with("NORMal")
        scope.set_trigger_source.assert_called_with("C3")
        scope.set_trigger_slope.assert_called_with("FALLing")
        scope.set_trigger_level.assert_called_with(0.2)
        self.assertEqual((applied.trigger.mode, applied.trigger.source, applied.trigger.slope, applied.trigger.level),
                         ("NORMal", "C3", "FALLing", 0.2))
        self.assertEqual(snapshot["actual_vertical_scale_v_div"], 0.5)
        self.assertEqual(snapshot["actual_vertical_offset_v"], -0.25)
        self.assertEqual(snapshot["acquisition_channel"], 2)
        self.assertEqual(snapshot["vertical_divisions"], 8)
        self.assertEqual(snapshot["actual_trigger"], {"mode": "NORMal", "source": "C3", "slope": "FALLing", "level_v": 0.1})


    def test_psd_sine_power_and_dc(self):
        rate = 10000
        t = np.arange(50000) / rate
        v = 3 + 2 * np.sin(2 * np.pi * 123 * t)
        f, p, meta = calculate_psd(v, rate)
        self.assertEqual(f[np.argmax(p)], 123)
        self.assertAlmostEqual(float(np.sum(p) * (f[1] - f[0])), 2.0, places=8)
        self.assertLess(p[0], 1e-20)
        self.assertEqual(meta["noverlap"], 5000)
        self.assertEqual(meta["unit"], "V²/Hz")

    def test_psd_noise_normalization_and_short_record(self):
        rng = np.random.default_rng(1234)
        v = rng.normal(0, 0.2, 100000)
        f, p, _ = calculate_psd(v, 20000)
        self.assertAlmostEqual(float(np.sum(p) * (f[1] - f[0])), 0.04, delta=0.002)
        _, _, meta = calculate_psd(np.ones(11), 100)
        self.assertEqual((meta["nperseg"], meta["noverlap"]), (11, 5))

    def test_invalid_waveforms(self):
        for t, v in [([0, 1, 1], [1, 2, 3]), ([0, 1, 3], [1, 2, 3]),
                     ([0, 1], [1, np.nan]), ([0], [1])]:
            with self.assertRaises(ValueError):
                validate_waveform(t, v)

    def test_display_keeps_narrow_pulse(self):
        v = np.zeros(100000)
        v[44221], v[99332] = 99, -20
        indices = envelope_indices(v)
        self.assertLessEqual(len(indices), 6000)
        self.assertIn(44221, indices)
        self.assertIn(99332, indices)

    def test_capture_uses_actual_rate_and_retries_partial_frame(self):
        scope = Mock()
        scope.get_trigger_mode.return_value = "AUTO"
        scope.trigger_status.return_value = "STOP"
        config = SimpleNamespace(sampling_time=0.1, channels=[SimpleNamespace(number=3, enabled=True)],
                                 timebase_scale=0.01, horizontal_divisions=10, trigger=SimpleNamespace(mode="AUTO"))
        full = SimpleNamespace(time=np.arange(1001) / 10000, voltage=np.ones(1001), preamble_dict={"point_num": 1001})
        acquirer = Mock()
        acquirer.acquire_channel.side_effect = [SimpleNamespace(voltage=[], preamble_dict={"point_num": 1001}), full]
        result, _, _, rate = workflow.capture(scope, acquirer, config,
            {"readback_points": 1001, "readback_rate_sa_s": 10000, "acquisition_channel": 3}, check=lambda: None, sleep=lambda _: None)
        self.assertIs(result, full)
        self.assertAlmostEqual(rate, 10000)
        self.assertEqual(acquirer.acquire_channel.call_count, 2)
        self.assertEqual(acquirer.acquire_channel.call_args.kwargs, {"trim_points": 0})

    def test_trigger_timeout_does_not_read_stale_data(self):
        scope, acquirer = Mock(), Mock()
        scope.trigger_status.return_value = "Ready"
        with self.assertRaises(TimeoutError):
            workflow.capture(scope, acquirer, SimpleNamespace(sampling_time=1, trigger=SimpleNamespace(mode="NORMal")),
                {"readback_points": 10, "readback_rate_sa_s": 10}, check=lambda: None,
                sleep=lambda _: None, monotonic=Mock(side_effect=[0, 9]))
        acquirer.acquire_channel.assert_not_called()

    def test_capture_respects_modes_and_exports_acquisition_channel(self):
        for mode, statuses in (("AUTO", ["Stop"]), ("NORMal", ["Ready", "Trig'd", "Stop"]),
                               ("SINGle", ["Ready", "Stop", "Stop"])):
            with self.subTest(mode=mode):
                scope, acquirer, sleep = Mock(), Mock(), Mock()
                scope.trigger_status.side_effect = statuses
                scope.get_trigger_mode.return_value = "AUTO"
                config = SimpleNamespace(sampling_time=1, timebase_scale=0.1, horizontal_divisions=10,
                    channels=[SimpleNamespace(number=1, enabled=True), SimpleNamespace(number=3, enabled=True)],
                    trigger=SimpleNamespace(mode=mode))
                acquirer.acquire_channel.return_value = SimpleNamespace(time=np.arange(11) / 10,
                    voltage=np.ones(11), preamble_dict={"point_num": 11})
                workflow.capture(scope, acquirer, config,
                    {"readback_points": 11, "readback_rate_sa_s": 10, "acquisition_channel": 3},
                    check=lambda: None, sleep=sleep)
                self.assertEqual(scope.set_trigger_mode.call_args_list,
                                 [call("AUTO"), call("FTRIG")] if mode == "AUTO" else [call(mode)])
                acquirer.acquire_channel.assert_called_once_with(3, 0.1, 10, trim_points=0)
                if mode == "NORMal":
                    sleep.assert_any_call(1.1)

    def test_auto_without_edges_commits_deep_memory_before_export(self):
        """复现 AUTO 等待后 STOP 仍返回空数据，只有 FTRIG 完成才提交完整帧。"""
        state = {"mode": "AUTO", "committed": False, "stopped": True, "polls": 0}
        scope, acquirer = Mock(), Mock()

        def start():
            state.update(committed=False, stopped=False, polls=0)

        def read_mode():
            if state["mode"] == "FTRIG":
                state["polls"] += 1
                if state["polls"] >= 2:
                    state.update(committed=True, mode="AUTO")
            return state["mode"]

        def export(*args, **kwargs):
            self.assertTrue(state["stopped"])
            if not state["committed"]:
                return SimpleNamespace(voltage=[], preamble_dict={"point_num": 10000})
            return SimpleNamespace(time=np.arange(500000) / 500000, voltage=np.ones(500000),
                                   preamble_dict={"point_num": 500000})

        scope.trigger_run.side_effect = start
        scope.trigger_stop.side_effect = lambda: state.update(stopped=True)
        scope.set_trigger_mode.side_effect = lambda mode: state.update(mode=mode)
        scope.get_trigger_mode.side_effect = read_mode
        scope.trigger_status.side_effect = lambda: "Stop" if state["stopped"] else "Auto"
        acquirer.acquire_channel.side_effect = export
        config = SimpleNamespace(sampling_time=1, timebase_scale=0.1, horizontal_divisions=10,
                                 trigger=SimpleNamespace(mode="AUTO"))
        snapshot = {"acquisition_channel": 1, "readback_points": 500000, "readback_rate_sa_s": 500000}
        result, _, _, _ = workflow.capture(scope, acquirer, config, snapshot,
                                           check=lambda: None, sleep=lambda _: None)
        self.assertEqual(len(result.voltage), 500000)
        acquirer.acquire_channel.assert_called_once()
        self.assertEqual(state["mode"], "AUTO")
        self.assertTrue(snapshot["capture_attempts"][0]["force_trigger_completed"])

    def test_auto_force_trigger_timeout_and_cancel_never_export_old_frame(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                scope, acquirer = Mock(), Mock()
                scope.get_trigger_mode.return_value = "FTRIG"
                check = Mock(side_effect=[None, WorkflowCancelled("取消")]) if cancelled else lambda: None
                config = SimpleNamespace(sampling_time=1, trigger=SimpleNamespace(mode="AUTO"))
                with self.assertRaises(WorkflowCancelled if cancelled else TimeoutError):
                    workflow.capture(scope, acquirer, config,
                        {"readback_points": 500000, "readback_rate_sa_s": 500000},
                        check=check, sleep=lambda _: None, monotonic=Mock(side_effect=[0, 9]))
                acquirer.acquire_channel.assert_not_called()

    def test_cancel_while_waiting_for_trigger(self):
        scope, acquirer = Mock(), Mock()
        with self.assertRaises(WorkflowCancelled):
            workflow.capture(scope, acquirer,
                SimpleNamespace(sampling_time=1, trigger=SimpleNamespace(mode="NORMal")),
                {"readback_points": 10, "readback_rate_sa_s": 10},
                check=Mock(side_effect=[None, WorkflowCancelled("取消")]), sleep=lambda _: None)
        acquirer.acquire_channel.assert_not_called()

    def test_cancel_during_multislice_and_last_slice_size(self):
        scope = Mock()
        scope.get_waveform_data.return_value = b"x"
        checks = Mock(side_effect=[None, WorkflowCancelled("取消")])
        with self.assertRaises(WorkflowCancelled):
            SDSAcquisition(scope, check_cancelled=checks)._read_multi_slice(11, 5)
        self.assertEqual(scope.get_waveform_data.call_count, 1)
        SDSAcquisition(scope)._read_multi_slice(11, 5)
        scope.set_waveform_points.assert_called_with(1)

    def test_finally_disconnects_on_connection_and_capture_failure(self):
        for stage in ("connection", "capture", "cancel"):
            with self.subTest(stage=stage), TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "raw").mkdir()
                (root / "results").mkdir()
                directory = RunDirectory(root, root / "raw", root / "results", root / "experiment_config.yaml")
                scope = Mock()
                if stage == "connection":
                    scope.connect.side_effect = RuntimeError("连接失败")
                failure = WorkflowCancelled("取消") if stage == "cancel" else RuntimeError("采集失败")
                with patch.object(workflow, "load_mapping", return_value={"scope_waveform": {"instrument": "sds_acquisition", "resource": "fake"}}), \
                     patch.object(workflow, "create_run_directory", return_value=directory), \
                     patch.object(workflow, "configure", return_value=(Mock(), {})), \
                     patch.object(workflow, "capture", side_effect=failure):
                     with self.assertRaises((RuntimeError, WorkflowCancelled)):
                        workflow.run(ScopeCaptureParams(), instrument_factory=lambda _: scope, acquirer_factory=Mock())
                scope.disconnect.assert_called_once()
                self.assertEqual(scope.trigger_stop.call_count, 0 if stage == "connection" else 1)

    def test_analysis_preserves_raw_data_and_actual_time(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "raw").mkdir()
            t = -0.5 + np.arange(12001) / 20000
            v = np.sin(2 * np.pi * 500 * t)
            raw = root / "raw" / "waveform.npz"
            np.savez(raw, time_s=t, voltage_v=v, channel=2, requested_rate_sa_s=25000, requested_duration_s=0.6)
            original = raw.read_bytes()
            analyze(root)
            display = json.loads((root / "results" / "display.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(display["actual_rate_sa_s"], 20000)
            self.assertEqual(display["sample_count"], 12001)
            self.assertEqual(display["time_s"][0], -0.5)
            self.assertEqual(raw.read_bytes(), original)
            self.assertTrue((root / "results" / "psd.npz").exists())
            self.assertIsNone(display["overrange"])
            self.assertIsNone(display["vertical_range_min_v"])

    def test_analysis_records_vertical_range_and_overrange(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "raw").mkdir()
            (root / "experiment_config.yaml").write_text(
                "scope_configuration:\n  actual_vertical_scale_v_div: 0.5\n  actual_vertical_offset_v: 1.0\n  vertical_divisions: 8\n",
                encoding="utf-8")
            t = np.arange(100) / 1000
            v = np.zeros(100) + 1.0
            v[10] = 3.1
            np.savez(root / "raw" / "waveform.npz", time_s=t, voltage_v=v, channel=1,
                     requested_rate_sa_s=1000, requested_duration_s=0.1)
            analyze(root)
            display = json.loads((root / "results" / "display.json").read_text(encoding="utf-8"))
            self.assertEqual(display["schema_version"], 2)
            self.assertEqual((display["vertical_range_min_v"], display["vertical_range_max_v"]), (-1.0, 3.0))
            self.assertTrue(display["overrange"])
            self.assertAlmostEqual(display["overrange_fraction"], 0.01)
            v[10], v[11] = 3.0, -1.0
            np.savez(root / "raw" / "waveform.npz", time_s=t, voltage_v=v, channel=1,
                     requested_rate_sa_s=1000, requested_duration_s=0.1)
            analyze(root)
            normal = json.loads((root / "results" / "display.json").read_text(encoding="utf-8"))
            self.assertFalse(normal["overrange"])
            self.assertEqual(normal["overrange_fraction"], 0.0)

    def test_analysis_uses_raw_snapshot_for_old_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "raw").mkdir()
            t = np.arange(20) / 1000
            v = np.ones(20)
            snapshot = {"actual_vertical_scale_v_div": 0.25, "actual_vertical_offset_v": 0.5,
                        "vertical_divisions": 8}
            np.savez(root / "raw" / "waveform.npz", time_s=t, voltage_v=v, channel=1,
                     requested_rate_sa_s=1000, requested_duration_s=0.02,
                     configuration_json=np.array(json.dumps(snapshot)))
            analyze(root)
            display = json.loads((root / "results" / "display.json").read_text(encoding="utf-8"))
            self.assertEqual((display["vertical_range_min_v"], display["vertical_range_max_v"]), (-0.5, 1.5))
            self.assertFalse(display["overrange"])

    def test_temperature_gated_acquire_restores_on_cancel_and_failure(self):
        switch = Mock()
        calls = []

        def set_switch(device, enabled, **kwargs):
            calls.append((enabled, kwargs))

        with self.assertRaises(WorkflowCancelled):
            workflow.temperature_gated_acquire(
                temp_switch=switch,
                temp_channel=2,
                off_settle_s=0,
                on_settle_s=0,
                acquire=lambda: (_ for _ in ()).throw(WorkflowCancelled("取消")),
                check_cancelled=lambda: None,
                sleep=lambda _: None,
                set_temperature_switch=set_switch,
                cancellation=None,
            )
        self.assertEqual([item[0] for item in calls], [False, True])
        self.assertIsNone(calls[-1][1]["cancellation"])


if __name__ == "__main__":
    unittest.main()
