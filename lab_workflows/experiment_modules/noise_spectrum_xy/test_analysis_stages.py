"""分析编排回归：整次/分步一致、产物复用与归档边界。"""

from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import yaml

from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from . import analysis
from .stages import ANALYSIS, definition


class AnalysisStagesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xy-analysis-stages-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        frequency = np.arange(0.0, 1000.1, 50.0)
        voltage = np.linspace(0.0, 2.0, 50)
        control = 1000.0 * voltage
        matrix = np.column_stack([1e-12 + lorentzian_vs_control(control, 80, .005, 0, 0, freq)
            for freq in frequency])
        rows = np.arange(voltage.size)
        self.ridge = dict(k=1000.0, b=0.0, omega_ctrl=control, contrast=np.log(matrix[:, 1:]),
            spur_mask=np.zeros(frequency.size - 1, bool), V_cal=voltage, f_cal=control,
            residual_std_hz=0.0, coverage=1.0, half_slopes=np.full(2, 1000.0))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.psd = self.stack.enter_context(patch.object(analysis, "compute_psd", return_value=(
            matrix, frequency, voltage, rows, dict(actual_rate_sa_s=10000.0, nperseg=200, bin_width_hz=50.0))))
        self.locate = self.stack.enter_context(patch.object(analysis, "locate_ridge", return_value=self.ridge))
        # 科学绘图由原回归覆盖，此处只验证阶段文件的寿命，避免重复渲染。
        for name in ("plot_noise_spectrum_2d", "plot_ridge", "plot_fit", "plot_background_diagnostic"):
            self.stack.enter_context(patch.object(analysis, name))

    def directory(self, name):
        directory = self.root / name
        (directory / "raw").mkdir(parents=True)
        (directory / "experiment_config.yaml").write_text(yaml.safe_dump(dict(parameters={
            "ANALYSIS_BIN_WIDTH_HZ": 50.0, "ANALYSIS_FREQUENCY_MIN_HZ": 0.0,
            "ANALYSIS_FREQUENCY_MAX_HZ": 1000.0, "ANALYSIS_FIT_HALF_WIDTH_HZ": 400.0})), encoding="utf-8")
        return directory

    def test_full_and_separate_steps_produce_same_arrays_and_summary(self):
        full, staged = self.directory("full"), self.directory("staged")
        expected = analysis.analyze(full)
        for index, stage in enumerate(ANALYSIS):
            actual = analysis.analyze(staged, step_ids=[stage.id], expected_revision=index)
            summary = json.loads((staged / "results/analysis_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary, actual)
        self.assertEqual(expected, actual)
        self.assertEqual(self.psd.call_count, 2)
        for name in ("psd_matrix.npz", "calibration.npz", "popt_fit.npz", "noise_spectra.npz", "fit_diagnostics.npz"):
            with np.load(full / "results" / name, allow_pickle=False) as first, np.load(staged / "results" / name, allow_pickle=False) as second:
                self.assertEqual(first.files, second.files)
                for key in first.files:
                    np.testing.assert_array_equal(first[key], second[key])

    def test_parameter_impact_preview_matches_saved_analysis_invalidation(self):
        run = self.directory("preview")
        analysis.analyze(run)
        original = (run / "experiment_config.yaml").read_bytes()
        impacts = definition()["parameter_impacts"]["analysis"]
        for name, value in (("ANALYSIS_FREQUENCY_MAX_HZ", 950.0), ("ANALYSIS_FIT_HALF_WIDTH_HZ", 350.0)):
            with self.subTest(parameter=name):
                ctx = analysis.AnalysisWorkflow(run, {name: value})
                self.assertEqual([key for key, state in ctx.states.items() if state["status"] == "stale"], impacts[name])
                self.assertEqual((run / "experiment_config.yaml").read_bytes(), original)

    def test_recompute_archives_only_affected_products_and_input_drift_rejects_reuse(self):
        run = self.directory("reuse")
        analysis.analyze(run, step_ids=["B01", "B02", "B03"])
        psd_path = run / "results/psd_matrix.npz"
        signature = psd_path.stat().st_mtime_ns
        original = psd_path.read_bytes()
        analysis.analyze(run, step_ids=["B04"])
        self.assertEqual(psd_path.stat().st_mtime_ns, signature)
        self.assertFalse(list((run / "results").glob("before_*/*psd_matrix.npz")))
        analysis.analyze(run, step_ids=["B04"])
        self.assertTrue(list((run / "results").glob("before_*/noise_spectra.npz")))
        self.assertEqual(psd_path.read_bytes(), original)
        # 仅适配器写入分析状态不应使刚生成的产物失效。
        config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
        config["analysis_status"] = "completed"
        (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertEqual(analysis.AnalysisWorkflow(run).states["B04"]["status"], "completed")
        np.save(run / "raw/waveform_C0000.npy", np.ones(40))
        with self.assertRaisesRegex(ValueError, "B01"):
            analysis.analyze(run, step_ids=["B02"])
        self.assertEqual(psd_path.read_bytes(), original)

    def test_old_stage_records_artifact_changes_and_failed_calibration_keep_diagnostics(self):
        run = self.directory("old")
        with self.assertRaisesRegex(ValueError, "B01"):
            analysis.analyze(run, step_ids=["B02"])
        analysis.analyze(run, step_ids=["B01", "B02", "B03"])
        (run / "results/calibration.npz").write_bytes(b"obsolete")
        with self.assertRaisesRegex(ValueError, "B03"):
            analysis.analyze(run, step_ids=["B04"])
        self.locate.side_effect = ValueError("标定质量不足")
        with self.assertRaisesRegex(ValueError, "标定质量不足"):
            analysis.analyze(run, step_ids=["B03"])
        summary = json.loads((run / "results/analysis_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "quality_failed")
        self.assertIn("psd", summary)
        self.assertNotIn("calibration", summary)
        self.assertFalse(summary["noise_spectrum_2d"]["calibrated"])
        with self.assertRaisesRegex(ValueError, "B03"):
            analysis.analyze(run, step_ids=["B04"])


if __name__ == "__main__":
    unittest.main()
