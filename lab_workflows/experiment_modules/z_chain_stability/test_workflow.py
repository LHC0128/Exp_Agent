"""Z 链路零点/脉冲角的假仪器回归：波形、分割、I–V、长记写盘、基线与取消。"""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import yaml

from ... import common
from ...experiment_runtime import WorkflowCancelled
from . import acquisition, analysis_steps, models, preparation, processing, run_format

ACQUISITION_CASES = {
    "z-chain-stability": {
        "mode": "single",
        "cases": {
            "order": ["AcquisitionTest::test_zero_log"],
            "cancel": ["AcquisitionTest::test_cancel_keeps_partial_rows"],
            "acquire_failure": ["AcquisitionTest::test_acquire_failure_propagates"],
            "entrypoints": ["AcquisitionTest::test_pulse_angles"],
        },
    },
}


class FakeSignalGenerator:
    channel = 1

    def __init__(self):
        self.voltage = 0.0
        self.arb = None
        self.output = False
        self.sync = False

    def set_output(self, value):
        self.output = value

    def set_sync_state(self, value):
        self.sync = value

    def set_burst_state(self, value):
        pass

    def set_mod_state(self, value):
        pass

    def setup_dc(self, volts):
        self.voltage, self.arb = volts, None

    def setup_arbitrary(self, values, freq, amplitude, offset, output=False):
        self.arb = (np.asarray(values, float), freq, amplitude, offset)
        self.voltage, self.output = offset, output


class FakeAmplifier:
    def set_output(self, value):
        pass


class FakeResult:
    def __init__(self, channel, time, voltage):
        self.channel, self.time, self.voltage = channel, time, voltage
        self.config_snapshot = {"channel": channel}
        self.preamble_dict = {"xorigin": 0.0, "xincrement": float(time[1] - time[0])}


class FakeAcquirer:
    """按信号源当前 DC/任意波产生采样电阻电压；通道 2 电流、通道 3 同步。"""

    def __init__(self, sg, params, *, fail_at=None):
        self.sg, self.params, self.fail_at = sg, params, fail_at
        self.calls = 0
        self.scales: list[float] = []

    def use_auto_depth(self, value):
        pass

    def acquire_all(self, config):
        self.calls += 1
        self.scales.append(config.channels[0].scale)
        if self.fail_at and self.calls == self.fail_at:
            raise RuntimeError("模拟采集失败")
        n = 4000
        duration = 400e-6 if config.trigger.mode == "NORMal" else self.params.zero_window_s
        time = np.arange(n) * duration / n
        if self.sg.arb is not None:
            values, _, amplitude, offset = self.sg.arb
            y = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, values.size), values)
            volts = offset + amplitude / 2.0 * y
        else:
            volts = np.full(n, self.sg.voltage)
        current = volts * self.params.amps_per_drive_volt
        results = [FakeResult(self.params.scope_current_channel, time, current * self.params.sense_resistor_ohm)]
        if config.trigger.mode == "NORMal":
            sync = np.where((time % (self.params.pulse_period_us * 1e-6)) < 10e-6, 3.5, 0.0)
            results.append(FakeResult(self.params.scope_sync_channel, time, sync))
        return results


class FakeContext:
    def __init__(self, root, params):
        self.root, self.params = root, params
        self.directory = SimpleNamespace(root=root / "run")
        (self.directory.root / "raw").mkdir(parents=True, exist_ok=True)
        sg = FakeSignalGenerator()
        self.devices = {"sg": sg, "amp": FakeAmplifier()}
        self.devices["acquirer"] = FakeAcquirer(sg, params)
        self.state = {"data_files": []}
        self.outputs = {}
        self.current_scale = None


def fast_params(**overrides):
    p = models.ZChainStabilityParams(
        run_tag="test", settle_s=0.0, zero_frames=1, zero_window_s=0.02,
        zero_log_interval_s=0.05, zero_log_duration_s=0.15, zero_save_all_frames=False,
        zero_raw_save_interval_s=0.0,
        pulse_duration_s=0.2, pulse_capture_interval_s=0.05, pulse_raw_save_interval_s=0.0,
        pulse_capture_periods=4)
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


class AcquisitionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(Path(common.__file__).resolve().parents[1] / "params", self.root / "params")

    def test_zero_log(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        acquisition.measure_zero_dg0(ctx)
        csv_path = ctx.directory.root / "raw" / "zero_dg0.csv"
        self.assertTrue(csv_path.is_file())
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn("current_a", rows[0])
        # A03 只写 DG 0 V 零点，不再写 I–V 电压，也不应产生 iv_check 帧。
        self.assertNotIn("iv_fit", ctx.state)
        self.assertFalse(list((ctx.directory.root / "raw").glob("iv_check_*.npz")))
        self.assertEqual(ctx.state["zero_offset"]["offset_current_a"], 0.0)

    def test_saves_every_frame_when_enabled(self):
        params = fast_params(zero_save_all_frames=True)
        ctx = FakeContext(self.root, params)
        acquisition.measure_zero_dg0(ctx)
        frames = sorted((ctx.directory.root / "raw").glob("zero_dg0_[0-9]*.npz"))
        with (ctx.directory.root / "raw" / "zero_dg0.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(len(frames), len(rows))
        with np.load(frames[0]) as data:
            meta = json.loads(str(data["meta"]))
        self.assertEqual(meta["kind"], "zero_dg0")
        self.assertIn("elapsed_s", meta)

    def test_zero_log_remembers_escalated_scale(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        ctx.devices["sg"].voltage = 0.05  # 68.5 mV 采样电阻电压：5 mV/div 削顶，需爬两档
        acquisition._log_zero(ctx, "dg0", 0.0, stage="A03", duration_s=0.15, interval_s=0.05,
                              save_frames=False, raw_interval_s=0.0, scale=0.005)
        scales = ctx.devices["acquirer"].scales
        self.assertEqual(scales[:3], [0.005, 0.01, 0.02])
        # 档位按段记忆：只有首帧从 5 mV/div 爬档，后续帧直接用 20 mV/div
        self.assertEqual(scales.count(0.005), 1)
        self.assertEqual(scales[-1], 0.02)

    def test_cancel_keeps_partial_rows(self):
        params = fast_params(zero_log_duration_s=1.0, zero_log_interval_s=0.05)
        ctx = FakeContext(self.root, params)
        calls = {"n": 0}

        def cancel_after_two():
            calls["n"] += 1
            if calls["n"] > 2:
                raise WorkflowCancelled("模拟取消")

        with patch.object(acquisition, "check_cancelled", side_effect=cancel_after_two):
            with self.assertRaises(WorkflowCancelled):
                acquisition._log_zero(ctx, "dg0", 0.0, stage="A03", duration_s=1.0,
                                      interval_s=0.05, save_frames=False, raw_interval_s=0.0, scale=0.005)
        csv_path = ctx.directory.root / "raw" / "zero_dg0.csv"
        self.assertTrue(csv_path.is_file())
        with csv_path.open(newline="", encoding="utf-8") as handle:
            self.assertGreaterEqual(len(list(csv.DictReader(handle))), 1)

    def test_acquire_failure_propagates(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        ctx.devices["acquirer"] = FakeAcquirer(ctx.devices["sg"], params, fail_at=2)
        with self.assertRaises(RuntimeError):
            acquisition.measure_zero_dg0(ctx)

    def test_baseline_current_conversion(self):
        params = fast_params(baseline_current_ma=1.0, zero_log_duration_s=0.1)
        ctx = FakeContext(self.root, params)
        acquisition.measure_baseline(ctx)
        self.assertAlmostEqual(ctx.state["baseline_target_current_a"], 1e-3, places=9)
        self.assertAlmostEqual(ctx.state["baseline_setpoint_v"],
                               1e-3 / params.conversion_amps_per_volt, places=12)
        self.assertTrue((ctx.directory.root / "raw" / "zero_baseline.csv").is_file())
        self.assertFalse(list((ctx.directory.root / "raw").glob("iv_check_*.npz")))

    def test_pulse_angles(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        acquisition.measure_pulse_angles(ctx)
        csv_path = ctx.directory.root / "raw" / "pulse_angles.csv"
        self.assertTrue(csv_path.is_file())
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(rows), 8)
        signs = {row["sign"] for row in rows}
        self.assertEqual(signs, {"+", "-"})
        theta = np.asarray([float(row["theta_pi"]) for row in rows])
        self.assertTrue(np.all(np.abs(theta) > 0.1))


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name) / "run"
        (self.run_dir / "raw").mkdir(parents=True)
        self.params = fast_params()
        config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
                      schema_version=self.params.schema_version, raw_format_version=run_format.RAW_FORMAT_VERSION,
                      completion_status="completed", parameters=self.params.to_external())
        (self.run_dir / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    def test_zero_and_pulse_reports(self):
        with (self.run_dir / "raw" / "zero_dg0.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "elapsed_s", "mean_v", "std_v", "current_a"])
            for index in range(5):
                writer.writerow(["2026-01-01T00:00:00", index, 0.001 * index, 0.0001, 0.0001 * index])
        with (self.run_dir / "raw" / "pulse_angles.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "elapsed_s", "sign", "q_a_s", "theta_pi", "i_base_a", "peak_a"])
            for index, sign in enumerate(("+", "-", "+", "-")):
                writer.writerow(["2026-01-01T00:00:00", index, sign, 7.65e-7 * (1 if sign == "+" else -1),
                                 1.0 * (1 if sign == "+" else -1), 0.0, 0.255])
        config, params = analysis_steps.load_config(self.run_dir)
        self.assertEqual(config["experiment_id"], run_format.EXPERIMENT_ID)
        zero = analysis_steps.zero_report(self.run_dir, params)
        self.assertEqual(zero["segments"]["dg0"]["rows"], 5)
        pulse = analysis_steps.pulse_report(self.run_dir, params)
        self.assertEqual(pulse["rows"], 4)
        self.assertAlmostEqual(pulse["plus"]["mean"], 1.0, places=6)
        self.assertAlmostEqual(pulse["minus"]["mean"], -1.0, places=6)
        self.assertAlmostEqual(pulse["asymmetry_pi"], 0.0, places=6)
        self.assertTrue((self.run_dir / "results" / "zero_summary.json").is_file())
        self.assertTrue((self.run_dir / "results" / "pulse_summary.json").is_file())

    def test_missing_data_still_writes_declared_artifacts(self):
        _, params = analysis_steps.load_config(self.run_dir)
        zero = analysis_steps.zero_report(self.run_dir, params)
        self.assertEqual(zero["segments"], {})
        self.assertEqual(zero["windows"], {})
        pulse = analysis_steps.pulse_report(self.run_dir, params)
        self.assertEqual(pulse["rows"], 0)
        self.assertIn("reason", pulse)
        for name in ("zero_timeseries.png", "zero_allan.png", "zero_window_noise.png",
                     "zero_window_psd.png", "pulse_timeseries.png", "pulse_hist.png"):
            self.assertTrue((self.run_dir / "results" / name).is_file(), name)

    def test_window_frames_report(self):
        rng = np.random.default_rng(0)
        for index in range(4):
            volts = 1e-3 * (1.0 + 0.01 * rng.standard_normal(2000))
            np.savez(self.run_dir / "raw" / f"zero_dg0_{index:04d}.npz", sense_v=volts,
                     meta=json.dumps({"kind": "zero_dg0", "elapsed_s": 10.0 * index,
                                      "preamble": {"xincrement": 1e-5, "points": 2000}}))
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.zero_report(self.run_dir, params)
        window = summary["windows"]["dg0"]
        self.assertEqual(window["frames"], 4)
        self.assertEqual(window["frames_analyzed"], 4)
        self.assertEqual(window["points"], 2000)
        self.assertAlmostEqual(window["sample_rate_sa_s"], 1e5, places=3)
        self.assertGreater(window["within_std_median_nt"], 0.0)
        self.assertTrue((self.run_dir / "results" / "zero_window_noise.png").is_file())
        self.assertTrue((self.run_dir / "results" / "zero_window_psd.png").is_file())


class ProcessingTest(unittest.TestCase):
    def test_waveform_has_two_opposite_pulses(self):
        wave = processing.pulse_waveform(100.0, 4.0, 1.0, points=4096)
        self.assertAlmostEqual(float(wave.min()), -1.0, places=6)
        self.assertAlmostEqual(float(wave.max()), 1.0, places=6)
        self.assertAlmostEqual(float(np.trapezoid(wave, dx=100.0 / 4096)), 0.0, places=3)

    def test_segment_and_angle(self):
        t = np.linspace(0, 400e-6, 40000)
        current = np.zeros_like(t)
        # 帧首的截断碎片不得当成完整脉冲
        current[t < 1e-6] = 0.05
        for k in range(4):
            sign = 1.0 if k % 2 == 0 else -1.0
            mask = (t >= k * 100e-6 + 5e-6) & (t < k * 100e-6 + 9e-6)
            current[mask] = sign * 0.255
        pulses = processing.segment_pulses(t, current, period_s=100e-6, width_s=4e-6, tail_s=20e-6)
        self.assertEqual(len(pulses), 4)
        angle = processing.pulse_angle_pi(pulses[0]["q_a_s"], 653.79)
        self.assertAlmostEqual(angle, 1.332, places=2)
        self.assertLess(pulses[1]["q_a_s"], 0)

    def test_preflight_rejects_over_limit_pulse(self):
        p = models.ZChainStabilityParams(pulse_peak_current_ma=500.0)
        with self.assertRaises(ValueError):
            preparation.preflight(p, Path(__file__).resolve().parents[3])


if __name__ == "__main__":
    unittest.main()
