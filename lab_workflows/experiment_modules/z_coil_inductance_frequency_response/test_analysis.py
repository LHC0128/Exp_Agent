"""真实原始帧、分阶段科学分析、历史资格和产物指纹回归。"""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import yaml
from . import analysis, workflow
from .test_workflow import ROOT, copy_config, fake_hardware, scan_params


REGRESSION_CASES = {
    "z-coil-inductance-frequency-response": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/z_coil_inductance_frequency_response/__init__.py",
                "lab_workflows/experiment_modules/z_coil_inductance_frequency_response/analysis.py"
            ],
            "tests": [
                "InductanceAnalysisTests::test_whole_and_staged_analysis_agree_and_export_preserves_predecessors",
                "InductanceAnalysisTests::test_quality_failure_blocks_export_and_keeps_diagnostics",
                "InductanceAnalysisTests::test_known_voltage_model_phase_wrapping_and_normalizations_have_independent_values",
                "InductanceAnalysisTests::test_partial_three_frequency_run_exports_measurements_with_model_unavailable_reason",
                "InductanceAnalysisTests::test_input_and_artifact_changes_invalidate_without_reusing_stale_fits",
                "InductanceAnalysisTests::test_historical_identity_parameter_versions_and_invalid_raw",
                "InductanceAnalysisTests::test_real_analysis_module_entry_writes_stage_outputs"
            ]
        }
    }
}


def synthetic_run(root):
    """通过真实采集步骤生成正式格式文件，只在驱动边界使用假设备。"""
    with fake_hardware(root):
        return workflow.run(scan_params(root), project_root=root)


class InductanceAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="z-inductance-analysis-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.run = synthetic_run(self.root)

    def test_whole_and_staged_analysis_agree_and_export_preserves_predecessors(self):
        other = synthetic_run(self.root)
        config_bytes = (self.run / "experiment_config.yaml").read_bytes()
        raw_bytes = (self.run / "raw/scope_f000_r00.npz").read_bytes()
        full = analysis.analyze(self.run)
        ctx = analysis.AnalysisWorkflow(other)
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            ctx.execute(["B03"])
        ctx.execute(["B01"])
        ctx = analysis.AnalysisWorkflow(other)
        ctx.execute(["B02"])
        self.assertFalse((other / "results/frequency_response.png").exists())
        ctx = analysis.AnalysisWorkflow(other)
        ctx.execute(["B03"])
        self.assertEqual(ctx.summary["status"], "completed")
        with np.load(self.run / "results/frequency_response.npz") as a, np.load(other / "results/frequency_response.npz") as b:
            self.assertEqual(a.files, b.files)
            for key in a.files:
                if a[key].dtype.kind in "fiu":
                    np.testing.assert_allclose(a[key], b[key], equal_nan=True)
                else:
                    np.testing.assert_array_equal(a[key], b[key])
        self.assertEqual(full["capture_count"], 8)
        self.assertEqual(full["frequency_count"], 4)
        self.assertEqual((self.run / "experiment_config.yaml").read_bytes(), config_bytes)
        self.assertEqual((self.run / "raw/scope_f000_r00.npz").read_bytes(), raw_bytes)
        before = (other / "results/frequency_evaluation.json").read_bytes()
        ctx.execute(["B03"])
        self.assertEqual((other / "results/frequency_evaluation.json").read_bytes(), before)
        backup = other / "results" / ctx.summary["backup_directory"]
        self.assertTrue((backup / "frequency_response.png").is_file())
        self.assertFalse((backup / "frequency_evaluation.json").exists())
        self.assertFalse((backup / "frequency_response.npz").exists())

    def test_quality_failure_blocks_export_and_keeps_diagnostics(self):
        for path in (self.run / "raw").glob("scope_f*_r*.npz"):
            with np.load(path) as data:
                payload = {key: data[key] for key in data.files}
            payload["measured_voltage_v"] = np.zeros_like(payload["measured_voltage_v"])
            np.savez(path, **payload)
        ctx = analysis.AnalysisWorkflow(self.run)
        ctx.execute(["B01"])
        with self.assertRaises(ValueError):
            ctx.execute(["B02", "B03"])
        state = json.loads((self.run / "analysis_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["B02"]["status"], "quality_failed")
        self.assertEqual(state["steps"]["B03"]["status"], "pending")
        self.assertTrue(state["summary"]["error"])
        self.assertTrue((self.run / "results/analysis_summary.json").is_file())
        self.assertFalse((self.run / "results/frequency_response.png").exists())

    def test_known_voltage_model_phase_wrapping_and_normalizations_have_independent_values(self):
        a0, ainf, fc = .45, .10, 2100.0
        expected = []
        for path in sorted((self.run / "raw").glob("scope_f*_r*.npz")):
            with np.load(path) as data:
                payload = {key: data[key] for key in data.files}
            frequency = float(payload["frequency_hz"])
            amplitude = np.sqrt((a0**2 + ainf**2 * (frequency / fc)**2) / (1 + (frequency / fc)**2))
            repeat = int(path.stem.rsplit("r", 1)[1])
            phase = 179 if repeat == 0 else -179
            t = payload["time_s"] - float(payload["trigger_edge_time_s"])
            payload["measured_voltage_v"] = .023 + amplitude * (.99 if repeat == 0 else 1.01) * np.sin(
                2 * np.pi * frequency * t + np.deg2rad(phase))
            np.savez(path, **payload)
            if repeat == 0:
                expected.append(amplitude)
        expected = np.asarray(expected)
        ctx = analysis.AnalysisWorkflow(self.run)
        ctx.execute(["B01", "B02"])
        summary = ctx.summary["summary"]
        np.testing.assert_allclose(summary["amplitude_v_mean"], expected, rtol=1e-10)
        np.testing.assert_allclose(summary["amplitude_v_std"], .01 * expected, rtol=1e-9)
        np.testing.assert_allclose(np.abs(summary["phase_deg_mean"]), 180, atol=1e-10)
        np.testing.assert_allclose(summary["phase_deg_std"],
            np.rad2deg(np.sqrt(-2 * np.log(np.cos(np.deg2rad(1))))), atol=1e-9)
        np.testing.assert_allclose(summary["offset_v_mean"], .023, atol=1e-12)
        np.testing.assert_allclose(summary["gain_mean"], expected / .5, rtol=1e-10)
        np.testing.assert_allclose(summary["amplitude_normalized_to_reference_mean"], expected / expected[0], rtol=1e-10)
        np.testing.assert_allclose(summary["amplitude_change_db_mean"], 20 * np.log10(expected / expected[0]), atol=1e-10)
        model = ctx.summary["theory_fit"]
        self.assertTrue(model["available"])
        self.assertAlmostEqual(model["amplitude_zero_frequency_v"], a0, places=6)
        self.assertAlmostEqual(model["amplitude_high_frequency_v"], ainf, places=6)
        self.assertAlmostEqual(model["corner_frequency_hz"], fc, places=3)
        self.assertGreater(model["r_squared"], .999999)
        np.testing.assert_allclose(summary["amplitude_normalized_to_fitted_zero_mean"], expected / a0, rtol=1e-6)
        np.testing.assert_allclose(summary["amplitude_drop_from_fitted_zero_percent_mean"], 100 * (1 - expected / a0), atol=1e-5)
        self.assertTrue(all(row["residual_rms_v"] < 1e-12 for row in ctx.summary["captures"]))
        with patch.object(analysis.steps, "fit_sine", side_effect=AssertionError("导出不得重新拟合")), \
                patch.object(analysis.steps, "fit_inductive_voltage_response", side_effect=AssertionError("导出不得重新拟合")):
            ctx.execute(["B03"])
        for name in ("waveform_overview", "frequency_response"):
            self.assertTrue((self.run / "results" / f"{name}.png").read_bytes().startswith(b"\x89PNG"))
            self.assertTrue((self.run / "results" / f"{name}.pdf").read_bytes().startswith(b"%PDF"))
        self.assertEqual(ctx.snapshot()["options"], {})

    def test_partial_three_frequency_run_exports_measurements_with_model_unavailable_reason(self):
        for path in (self.run / "raw").glob("scope_f003_r*.npz"):
            path.unlink()
        report = analysis.analyze(self.run)
        self.assertEqual(report["frequency_count"], 3)
        self.assertFalse(report["theory_fit"]["available"])
        self.assertEqual(report["theory_fit"]["reason"], "至少需要 4 个正频率幅值点")
        self.assertEqual(report["status"], "completed")
        self.assertNotIn("amplitude_zero_frequency_v", report["theory_fit"])
        for name in ("waveform_overview.png", "frequency_response.png"):
            self.assertTrue((self.run / "results" / name).is_file())
        for name in ("analysis_state.json", "results/analysis_summary.json", "results/frequency_evaluation.json",
                     "results/frequency_response.json"):
            json.loads((self.run / name).read_text(encoding="utf-8"),
                parse_constant=lambda value: self.fail(f"{name} 包含非标准 JSON 常量 {value}"))

    def test_input_and_artifact_changes_invalidate_without_reusing_stale_fits(self):
        ctx = analysis.AnalysisWorkflow(self.run)
        ctx.execute(["B01", "B02"])
        evaluation = self.run / "results/frequency_evaluation.json"
        evaluation.write_text(evaluation.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        ctx = analysis.AnalysisWorkflow(self.run)
        self.assertEqual(ctx.states["B01"]["status"], "completed")
        self.assertEqual(ctx.states["B02"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            ctx.execute(["B03"])
        ctx.execute(["B02"])
        source = self.run / "raw/scope_f000_r00.npz"
        with np.load(source) as data:
            payload = {key: data[key] for key in data.files}
        payload["measured_voltage_v"] = .9 * payload["measured_voltage_v"]
        np.savez(source, **payload)
        changed = analysis.AnalysisWorkflow(self.run)
        self.assertEqual(changed.states["B01"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            changed.execute(["B02"])

    def test_historical_identity_parameter_versions_and_invalid_raw(self):
        path = self.run / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config.pop("raw_format_version")
        config["schema_version"] = 999
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        analysis.AnalysisWorkflow(self.run).execute(["B01"])
        for edit in ({"experiment_id": "z-coil-current-frequency-response"},
                     {"raw_format_version": 999}, {"completion_status": "running"}):
            path.write_text(yaml.safe_dump(config | edit), encoding="utf-8")
            with self.assertRaises(ValueError):
                analysis.AnalysisWorkflow(self.run)
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        first = self.run / "raw/scope_f000_r00.npz"
        np.savez(first, time_s=np.arange(10))
        ctx = analysis.AnalysisWorkflow(self.run)
        with self.assertRaisesRegex(ValueError, "缺少字段"):
            ctx.execute(["B01", "B02"])
        self.assertEqual(ctx.states["B01"]["status"], "quality_failed")
        with self.assertRaisesRegex(ValueError, "没有可独立覆盖"):
            analysis.AnalysisWorkflow(self.run, {"SCOPE_CYCLES": 9})
        from ..z_coil_current_frequency_response import analysis as current
        self.assertIs(current.fit_sine, analysis.fit_sine)

    def test_real_analysis_module_entry_writes_stage_outputs(self):
        environment = os.environ.copy()
        environment.update(LAB_RUN_DIR=str(self.run), LAB_TYPED_PARAMETERS=json.dumps({"step_ids": ["B01", "B02"]}),
            PYTHONUTF8="1", MPLBACKEND="Agg")
        result = subprocess.run([sys.executable, "-m", analysis.__name__], cwd=ROOT,
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.run / "results/frequency_evaluation.json").is_file())
        state = json.loads((self.run / "analysis_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["B02"]["status"], "completed")
        self.assertEqual(state["steps"]["B03"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
