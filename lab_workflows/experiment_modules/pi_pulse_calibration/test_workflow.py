"""π 脉冲面积标定的假仪器回归：扫描写盘、取消保留、π 宽度内插与缺数据占位。"""
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
from . import acquisition, analysis_steps, models, preparation, run_format

ACQUISITION_CASES = {
    "pi-pulse-calibration": {
        "mode": "single",
        "cases": {
            "order": ["AcquisitionTest::test_scan_writes_rows_and_frames"],
            "cancel": ["AcquisitionTest::test_cancel_keeps_partial_rows"],
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
    """按信号源当前任意波产生采样电阻电压：幅度归一到 ±1 再乘 vpp/2。"""

    def __init__(self, sg, params, *, fail_at=None):
        self.sg, self.params, self.fail_at = sg, params, fail_at
        self.calls = 0

    def use_auto_depth(self, value):
        pass

    def acquire_all(self, config):
        self.calls += 1
        if self.fail_at and self.calls == self.fail_at:
            raise RuntimeError("模拟采集失败")
        n = 4000
        duration = self.params.capture_window_s if config.trigger.mode == "NORMal" else 0.02
        time = np.arange(n) * duration / n
        values, freq, amplitude, offset = self.sg.arb
        phase = (time * freq) % 1.0
        y = np.interp(phase, np.linspace(0, 1, values.size, endpoint=False), values)
        volts = offset + amplitude / 2.0 * y
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
    p = models.PiPulseCalibrationParams(
        run_tag="test", settle_s=0.0, pulse_widths_us=[4.8, 5.0, 5.2], shots_per_width=2,
        capture_interval_s=0.0, pulse_peak_current_ma=190.0)
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


class AcquisitionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(Path(common.__file__).resolve().parents[1] / "params", self.root / "params")

    def test_scan_writes_rows_and_frames(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        acquisition.measure_scan(ctx)
        csv_path = ctx.directory.root / "raw" / "scan.csv"
        self.assertTrue(csv_path.is_file())
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        frames = sorted((ctx.directory.root / "raw").glob("scan_*.npz"))
        self.assertEqual(len(frames), len(params.pulse_widths_us) * params.shots_per_width)
        self.assertGreaterEqual(len(rows), len(frames) * 4)
        self.assertEqual({row["width_us"] for row in rows}, {"4.8", "5", "5.2"})
        self.assertEqual({row["sign"] for row in rows}, {"+", "-"})
        self.assertTrue(all(float(row["theta_pi"]) > 0.3 for row in rows if row["sign"] == "+"))

    def test_cancel_keeps_partial_rows(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        calls = {"n": 0}

        def cancel_after_three():
            calls["n"] += 1
            if calls["n"] > 3:
                raise WorkflowCancelled("模拟取消")

        with patch.object(acquisition, "check_cancelled", side_effect=cancel_after_three):
            with self.assertRaises(WorkflowCancelled):
                acquisition.measure_scan(ctx)
        csv_path = ctx.directory.root / "raw" / "scan.csv"
        self.assertTrue(csv_path.is_file())
        with csv_path.open(newline="", encoding="utf-8") as handle:
            self.assertGreaterEqual(len(list(csv.DictReader(handle))), 1)


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name) / "run"
        (self.run_dir / "raw").mkdir(parents=True)
        self.params = fast_params()
        config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
                      schema_version=self.params.schema_version,
                      raw_format_version=run_format.RAW_FORMAT_VERSION,
                      completion_status="completed", parameters=self.params.to_external())
        (self.run_dir / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    def test_scan_report_solves_pi_width(self):
        with (self.run_dir / "raw" / "scan.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["width_us", "shot", "sign", "q_a_s", "theta_pi", "i_base_a", "peak_a", "elapsed_s"])
            for width in (4.6, 4.8, 5.0, 5.2, 5.4):
                for shot in range(3):
                    value = 0.25 * (width - 1.0)
                    q_value = 0.18 * (width - 1.0) * 1e-6
                    writer.writerow([width, shot, "+", q_value, value, -0.002, 0.198, 1.0])
                    writer.writerow([width, shot, "-", -q_value, -value, -0.002, 0.198, 1.0])
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.scan_report(self.run_dir, params)
        self.assertEqual(summary["rows"], 30)
        plus = [item for item in summary["points"] if item["side"] == "plus"][0]
        self.assertAlmostEqual(plus["width_pi"], 5.0, places=6)
        # 合成数据里 Q = 0.18 A × (宽 − 1 µs)，故平顶电流应为 180 mA，峰值 198 mA
        self.assertAlmostEqual(plus["flat_current_ma"], 180.0, places=6)
        self.assertAlmostEqual(plus["flat_over_peak_ratio"], 180.0 / 198.0, places=6)
        self.assertAlmostEqual(plus["effective_edge_us"], 1.0, places=6)
        self.assertIn("width_pi 是在当前", summary["note"])
        self.assertTrue((self.run_dir / "results" / "scan_summary.json").is_file())
        self.assertTrue((self.run_dir / "results" / "scan_fit.png").is_file())

    def test_missing_scan_writes_placeholder(self):
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.scan_report(self.run_dir, params)
        self.assertEqual(summary["rows"], 0)
        self.assertIn("reason", summary)
        self.assertTrue((self.run_dir / "results" / "scan_fit.png").is_file())


class ParamsTest(unittest.TestCase):
    def test_predicted_width(self):
        p = models.PiPulseCalibrationParams(pulse_peak_current_ma=190.0, coil_constant_hz_per_ma=653.79,
                                            pulse_edge_us=1.0)
        self.assertAlmostEqual(p.predicted_width_us(), 1.0 + 1e6 / (2 * 653.79 * 190.0), places=6)

    def test_preflight_rejects_too_wide_pulse(self):
        p = models.PiPulseCalibrationParams(pulse_widths_us=[4.0, 60.0], pulse_period_us=100.0)
        self.assertTrue(any("两倍最大脉冲宽度" in error for error in p.validate()))


if __name__ == "__main__":
    unittest.main()
