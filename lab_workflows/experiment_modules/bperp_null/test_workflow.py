"""横向静态场补平的假仪器回归：参数、网格写盘顺序、取消保留与复平面零点回收。

所有用例只用假仪器与临时目录，不连接任何硬件。
"""
from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import yaml

from ... import common
from . import acquisition, analysis_steps, models, run_format, stages

ACQUISITION_CASES = {
    "bperp-null": {
        "mode": "grouped",
        "cases": {
            "order": ["AcquisitionTest::test_grid_writes_after_temperature_restore"],
            "slow_disk": ["AcquisitionTest::test_grid_writes_after_temperature_restore"],
            "group_change": ["AcquisitionTest::test_grid_writes_after_temperature_restore"],
            "last_group": ["AcquisitionTest::test_grid_writes_after_temperature_restore"],
            "cancel": ["AcquisitionTest::test_cancel_keeps_completed_points"],
        },
        "sources": ["lab_workflows/experiment_modules/bperp_null/acquisition.py",
                    "lab_workflows/experiment_modules/bperp_null/preparation.py",
                    "lab_workflows/experiment_modules/bperp_null/workflow.py"],
    },
}
REGRESSION_CASES = {
    "bperp-null": {
        "analysis": {
            "sources": ["lab_workflows/experiment_modules/bperp_null/analysis_steps.py"],
            "tests": ["AnalysisTest::test_plane_fit_recovers_null",
                      "AnalysisTest::test_next_run_yaml_written"],
        },
        "parameters": {
            "sources": ["lab_workflows/experiment_modules/bperp_null/models.py",
                        "params/experiments/bperp-null.yaml"],
            "tests": ["ParamsTest::test_grid_from_start_stop_step",
                      "ParamsTest::test_width_over_half_period_rejected",
                      "ParamsTest::test_empty_grid_rejected"],
        },
    },
}


class FakeSignals:
    def __init__(self):
        self.events = []

    def set_output(self, state, channel=None):
        self.events.append(("output", channel, state))

    def setup_dc(self, value, channel=None):
        self.events.append(("dc", channel, value))


class FakeDirectory:
    def __init__(self, root):
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.results = self.root / "results"
        self.updates = []

    def update_config(self, **kwargs):
        self.updates.append(dict(kwargs))


def _ctx(tmp):
    params = models.BperpNullParams(x_scan_start_mv=-20, x_scan_stop_mv=20, x_scan_step_mv=20,
                                    y_scan_start_mv=-20, y_scan_stop_mv=20, y_scan_step_mv=20,
                                    n_repeat=2, temp_group_size=2)
    signals = FakeSignals()
    return SimpleNamespace(params=params, directory=FakeDirectory(tmp), root=common.find_project_root(),
                           devices={"signals": signals, "hf2": SimpleNamespace(device_id="dev_fake"), "channels": {"z": 1, "x": 2, "y": 3, "trigger": 4, "temp": 5}},
                           state={"actual_rates": {"demod0_Sa_s": 10000.0}, "data_files": []}, outputs={}, mapping={})


class AcquisitionTest(unittest.TestCase):
    def _run(self, tmp, fail_at=None):
        ctx = _ctx(tmp)
        calls = {"reads": 0}

        def fake_acquire(_ctx, _config, _rate):
            calls["reads"] += 1
            if fail_at is not None and calls["reads"] == fail_at:
                raise RuntimeError("模拟采集失败")
            return {"x0": 0.001, "y0": 0.0, "x0_sem": 1e-5, "y0_sem": 1e-5,
                    "x1": 0.002, "y1": 0.001, "x1_sem": 2e-5, "y1_sem": 2e-5}

        def fake_temp(signals, enabled, channel=None):
            ctx.devices["signals"].events.append(("temp", enabled))

        with patch.object(acquisition, "_acquire_point", fake_acquire), \
             patch.object(acquisition, "set_temperature_switch", fake_temp), \
             patch.object(acquisition, "configure_fixed_dc_field", lambda *args: args[-1]), \
             patch.object(acquisition.preparation, "arm_pulse", lambda _ctx: None), \
             patch.object(acquisition.preparation, "sleep", lambda _s: None):
            acquisition.measure_grid(ctx)
        return ctx, calls

    def test_grid_writes_after_temperature_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx, calls = self._run(Path(tmp))
            rows = list(csv.DictReader((Path(tmp) / "raw" / "bias_grid.csv").open(encoding="utf-8")))
            self.assertEqual(len(rows), 9 * 2)
            self.assertEqual(calls["reads"], 18)
            # 最后一次读数前温控必须已恢复，文件在恢复后落盘。
            self.assertEqual(ctx.devices["signals"].events[-1], ("temp", True))
            self.assertEqual(ctx.state["scan_reads_completed"], 18)

    def test_cancel_keeps_completed_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self._run(Path(tmp), fail_at=3)
            rows = list(csv.DictReader((Path(tmp) / "raw" / "bias_grid.csv").open(encoding="utf-8")))
            self.assertGreaterEqual(len(rows), 2)
            self.assertLess(len(rows), 18)


class AnalysisTest(unittest.TestCase):
    def _make_run(self, tmp, null=(-12.0, 5.0), bias=(0.0, 0.0), noise=0.0):
        root = Path(tmp)
        (root / "raw").mkdir(parents=True)
        params = models.BperpNullParams(x_scan_start_mv=-40, x_scan_stop_mv=40, x_scan_step_mv=10,
                                        y_scan_start_mv=-40, y_scan_stop_mv=40, y_scan_step_mv=10,
                                        n_repeat=3, x_bias_mv=bias[0] * 1000.0, y_bias_mv=bias[1] * 1000.0)
        config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
                      raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="completed",
                      schema_version=1, parameters=params.to_external())
        (root / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        rng = np.random.default_rng(3)
        # 模型零点在 null（mV），斜率复数为 (b, c)。
        b, c = 2e-3 + 1e-3j, -1e-3 + 0.5e-3j
        with (root / "raw" / "bias_grid.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(run_format.GRID_FIELDS))
            writer.writeheader()
            for x in params.x_offsets_mv():
                for y in params.y_offsets_mv():
                    value2 = b * (x - null[0]) + c * (y - null[1])
                    value1 = 0.5 * value2 + 0.02
                    for repeat in range(params.n_repeat):
                        noise2 = noise * (rng.standard_normal() + 1j * rng.standard_normal())
                        v2, v1 = value2 + noise2, value1 + noise2 * 0.5
                        writer.writerow({"width_us": "6.6", "x_offset_mv": f"{x:g}", "y_offset_mv": f"{y:g}",
                                         "x_bias_v": f"{bias[0]}", "y_bias_v": f"{bias[1]}", "repeat": repeat,
                                         "x2_v": f"{v2.real:.9g}", "y2_v": f"{v2.imag:.9g}",
                                         "x1_v": f"{v1.real:.9g}", "y1_v": f"{v1.imag:.9g}",
                                         "temp_c": "40", "timestamp": "2026-11-18T12:00:00+08:00"})
        return root, params

    def test_plane_fit_recovers_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, params = self._make_run(tmp, noise=0.0)
            summary = analysis_steps.scan_report(root, params)
            offset = summary["c2"]["null"]["offset_mv"]
            self.assertAlmostEqual(offset[0], -12.0, places=6)
            self.assertAlmostEqual(offset[1], 5.0, places=6)
            self.assertTrue(summary["c2"]["signal_ok"])

    def test_next_run_yaml_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, params = self._make_run(tmp, noise=1e-5)
            analysis_steps.scan_report(root, params)
            payload = yaml.safe_load((root / "results" / "next_run.yaml").read_text(encoding="utf-8"))
            self.assertIn("X_BIAS_MV", payload)
            self.assertGreater(payload["X_SCAN_STOP_MV"], 0)
            summary = json.loads((root / "results" / "null_summary.json").read_text(encoding="utf-8"))
            self.assertIn("c1", summary)


class ParamsTest(unittest.TestCase):
    def test_grid_from_start_stop_step(self):
        params = models.BperpNullParams()
        self.assertEqual(params.x_offsets_mv(), [-40.0, -20.0, 0.0, 20.0, 40.0])
        self.assertEqual(len(params.scan_voltages()), 25)

    def test_width_over_half_period_rejected(self):
        params = models.BperpNullParams(z_pulse_width_us=150.0)
        self.assertTrue(any("半个脉冲周期" in item for item in params.validate_model()))

    def test_empty_grid_rejected(self):
        params = models.BperpNullParams(x_scan_start_mv=10.0, x_scan_stop_mv=-10.0)
        self.assertTrue(any("X 扫描网格为空" in item for item in params.validate_model()))
