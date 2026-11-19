"""射频相位响应的离线回归：参数默认、相位网格、预检拒绝与三分量分解。

只用临时目录与合成数据，不连接硬件、不写运行目录。
"""
from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

from . import analysis_steps, models, preparation, run_format, stages

ROOT = Path(__file__).resolve().parents[3]

REGRESSION_CASES = {
    "rf-phase-response": {
        "analysis": {
            "sources": ["lab_workflows/experiment_modules/rf_phase_response/analysis_steps.py"],
            "tests": ["AnalysisTest::test_synthetic_three_component_response_recovers_best_phase",
                      "AnalysisTest::test_v1_rows_without_xy_still_analyze",
                      "AnalysisTest::test_staged_analysis_runs_on_minimal_run",
                      "AnalysisTest::test_missing_raw_writes_unavailable_summary"],
        },
        "parameters": {
            "sources": ["lab_workflows/experiment_modules/rf_phase_response/models.py",
                        "params/experiments/rf-phase-response.yaml"],
            "tests": ["ParamsTest::test_defaults_match_pi_width_run",
                      "ParamsTest::test_phase_grid_is_full_circle",
                      "PreparationTest::test_preflight_rejects_width_over_half_period",
                      "PreparationTest::test_preflight_accepts_defaults"],
        },
    },
}


class ParamsTest(unittest.TestCase):
    def test_defaults_match_pi_width_run(self):
        p = models.RfPhaseResponseParams()
        self.assertEqual(p.y_rf_freq_hz, 5000.0)
        self.assertEqual(p.y_rf_vpp, 0.05)
        self.assertEqual(p.z_peak_current_ma, 190.0)
        self.assertEqual(p.z_pulse_width_us, 8.03)
        self.assertTrue(p.z_tail_comp_enabled)
        self.assertAlmostEqual(p.z_tail_tau_c_us, 25.5136, places=3)
        wave, record = preparation.pulse_wave(p, p.z_pulse_width_us)
        self.assertTrue(record["tail_comp"])
        self.assertAlmostEqual(record["tail_a"], 0.115446, places=5)
        self.assertLess(abs(record["tail_start_current_ma"]), 22.0)
        self.assertGreater(abs(record["tail_start_current_ma"]), 21.5)
        self.assertEqual(len(wave), preparation.ARB_POINTS)

    def test_phase_grid_is_full_circle(self):
        p = models.RfPhaseResponseParams()
        phases = p.phase_angles_deg()
        self.assertEqual(len(phases), 36)
        self.assertEqual(phases[0], 0.0)
        self.assertEqual(phases[-1], 350.0)
        self.assertEqual(len(set(phases)), 36)


class PreparationTest(unittest.TestCase):
    def test_preflight_accepts_defaults(self):
        params, mapping = preparation.preflight(models.RfPhaseResponseParams(), ROOT)
        self.assertEqual(params, models.RfPhaseResponseParams())
        self.assertIn("Y_magnetic_field", mapping)

    def test_preflight_rejects_width_over_half_period(self):
        params = models.RfPhaseResponseParams(z_pulse_width_us=150.0)
        with self.assertRaisesRegex(ValueError, "半个射频周期"):
            preparation.preflight(params, ROOT)


class _FakeGS200:
    def __init__(self):
        self.source_function = "CURR"
        self.current_a = 0.0
        self.output = False
        self.current_range_a = 0.01
        self.current_limit_a = 0.2

    def get_source_function(self):
        return self.source_function

    def get_current(self):
        return self.current_a

    def get_output(self):
        return self.output

    def get_current_range(self):
        return self.current_range_a

    def get_current_limit(self):
        return self.current_limit_a

    def set_output(self, value):
        self.output = bool(value)

    def set_source_function(self, value):
        self.source_function = value

    def set_current_limit(self, value):
        self.current_limit_a = float(value)

    def set_current_range(self, value):
        self.current_range_a = float(value)

    def set_current(self, value):
        self.current_a = float(value)


class MainFieldTest(unittest.TestCase):
    def _context(self):
        from types import SimpleNamespace
        gs = _FakeGS200()
        ctx = SimpleNamespace(root=ROOT, devices={"gs200": gs}, state={}, outputs={},
                              main_field_state=None,
                              mapping={"main_magnetic_field": {"resource": "ASRL1::INSTR",
                                                               "source_function": "CURR"}})
        return ctx, gs

    def test_zero_current_keeps_output_off(self):
        ctx, gs = self._context()
        preparation.configure_main_field(ctx, 0.0)
        self.assertFalse(gs.output)
        self.assertFalse(ctx.state["main_field"]["output_after"])

    def test_nonzero_current_sets_and_reads_back(self):
        ctx, gs = self._context()
        preparation.configure_main_field(ctx, 0.2)
        self.assertTrue(gs.output)
        self.assertAlmostEqual(gs.current_a, 0.0002, places=12)
        self.assertTrue(ctx.state["main_field"]["output_after"])
        self.assertAlmostEqual(ctx.state["main_field"]["current_after_ma"], 0.2)

    def test_current_over_safety_limit_is_rejected(self):
        ctx, gs = self._context()
        with self.assertRaisesRegex(ValueError, "安全"):
            preparation.configure_main_field(ctx, 21.0)


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write_rows(self, c0, c_plus1, c_minus1, repeats=3, with_xy=True):
        phases = models.RfPhaseResponseParams().phase_angles_deg()
        path = self.root / run_format.PHASE_RESPONSE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["phase_deg", "repeat", "R_v", "theta_deg", "timestamp"]
        if with_xy:
            fields[2:2] = ["x_v", "y_v"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for repeat in range(repeats):
                for phase in phases:
                    phi = math.radians(phase)
                    value = c0 + c_plus1 * np.exp(1j * phi) + c_minus1 * np.exp(-1j * phi)
                    row = {"phase_deg": f"{phase:g}", "repeat": repeat,
                           "R_v": f"{abs(value):.12g}",
                           "theta_deg": f"{math.degrees(np.angle(value)):.12g}",
                           "timestamp": "2026-11-19T00:00:00+08:00"}
                    if with_xy:
                        row.update(x_v=f"{value.real:.12g}", y_v=f"{value.imag:.12g}")
                    writer.writerow(row)
        return phases

    def test_synthetic_three_component_response_recovers_best_phase(self):
        c0 = 0.010 + 0.005j
        c_plus1 = 0.003 - 0.002j
        c_minus1 = 0.004 + 0.001j
        self._write_rows(c0, c_plus1, c_minus1)
        params = models.RfPhaseResponseParams()
        summary = analysis_steps.scan_report(self.root, params)
        self.assertEqual(summary["mode"], "phase_response")
        self.assertAlmostEqual(summary["residual_rms_v"], 0.0, places=9)
        self.assertAlmostEqual(summary["components"]["c0"]["re_v"], c0.real, places=9)
        self.assertAlmostEqual(summary["components"]["c_minus1"]["im_v"], c_minus1.imag, places=9)
        # 最佳点定义为 |dZ/dφ| 最大，不是 |Z| 最大；幅值以 180° 为周期，两个点等价。
        best = summary["best_phase_deg"]
        phi = np.radians(np.arange(0.0, 360.0, 0.1))
        derivative = 1j * (c_plus1 * np.exp(1j * phi) - c_minus1 * np.exp(-1j * phi))
        expected = float(np.degrees(phi[int(np.argmax(np.abs(derivative)))]))
        distance = min(abs(best - expected), 180.0 - abs(best - expected))
        self.assertLess(distance, 0.3)
        self.assertAlmostEqual((summary["best_phase_alt_deg"] - best) % 360.0, 180.0, places=6)
        self.assertGreater(summary["sensitivity_max_v_per_deg"], 0.0)
        self.assertIsNotNone(summary["measured_best_phase_deg"])
        self.assertTrue((self.root / run_format.SUMMARY_FILE).is_file())
        self.assertTrue((self.root / run_format.PHASE_RESPONSE_PLOT).is_file())
        self.assertTrue((self.root / run_format.PHASOR_PLOT).is_file())
        self.assertIsNotNone(summary["points"][0]["x_mean_v"])
        self.assertEqual(json.loads((self.root / run_format.SUMMARY_FILE).read_text(encoding="utf-8"))["rows"], 108)

    def test_v1_rows_without_xy_still_analyze(self):
        """版本 1 的旧数据没有 X/Y 列，分析仍按 R/θ 完成分解。"""
        self._write_rows(0.010 + 0.005j, 0.003 - 0.002j, 0.004 + 0.001j, with_xy=False)
        summary = analysis_steps.scan_report(self.root, models.RfPhaseResponseParams())
        self.assertEqual(summary["mode"], "phase_response")
        self.assertIsNone(summary["points"][0]["x_mean_v"])
        self.assertTrue((self.root / run_format.PHASOR_PLOT).is_file())

    def test_staged_analysis_runs_on_minimal_run(self):
        self._write_rows(0.010 + 0.005j, 0.003 - 0.002j, 0.004 + 0.001j)
        params = models.RfPhaseResponseParams()
        config = dict(run_format.initial_metadata(params), schema_version=params.schema_version,
                      parameters=params.to_external(), completion_status="completed")
        (self.root / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True),
                                                          encoding="utf-8")
        from . import analysis

        analysis.AnalysisWorkflow.analyze(self.root)
        summary = json.loads((self.root / run_format.SUMMARY_FILE).read_text(encoding="utf-8"))
        self.assertEqual(summary["mode"], "phase_response")
        self.assertTrue((self.root / run_format.PHASE_RESPONSE_PLOT).is_file())

    def test_missing_raw_writes_unavailable_summary(self):
        summary = analysis_steps.scan_report(self.root, models.RfPhaseResponseParams())
        self.assertIn("缺少", summary["error"])
        self.assertTrue((self.root / "results/phase_response.png").is_file())
        self.assertTrue((self.root / "results/phase_response_phasor.png").is_file())


class StagesTest(unittest.TestCase):
    def test_stage_list_starts_with_preflight(self):
        self.assertEqual([stage.id for stage in stages.ACQUISITION], ["A01", "A02", "A03", "A04"])


if __name__ == "__main__":
    unittest.main()
