"""分阶段离线分析、指纹失效与归档的无硬件回归。"""

from pathlib import Path
import tempfile
import unittest

import numpy as np
import yaml

from . import stages
from .analysis import AnalysisWorkflow, analyze
from .models import DetectionChainFrequencyResponseParams
from .test_workflow import copy_config

DATA_TYPE = "Detection_Chain_Frequency_Response"


def make_run(root, *, completion="completed", peaks=None):
    """构造带合成频率响应数据的运行目录。"""
    params = DetectionChainFrequencyResponseParams()
    run = root / "data" / DATA_TYPE / "0101_1200_tag"
    (run / "raw").mkdir(parents=True, exist_ok=True)
    (run / "results").mkdir(exist_ok=True)
    freqs = np.linspace(50000.0, 130000.0, 41)
    if peaks is None:
        baseband = np.abs(freqs - params.hf2_osc_freq_hz)
        peaks = 1e-6 * (1.0 - 0.02 * baseband / 40000.0)
    peaks = np.asarray(peaks, float)
    peaks[np.isclose(freqs, params.hf2_osc_freq_hz)] = 5e-6  # 零基带点：应被分析排除
    np.savez(run / "raw/frequency_response.npz", am_frequencies_hz=freqs,
             peak_amplitudes=peaks, peak_frequencies_hz=np.where(np.isfinite(peaks), freqs, np.nan),
             actual_rate=450000.0)
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(dict(
        experiment_id="detection-chain-frequency-response", data_type=DATA_TYPE,
        completion_status=completion, parameters=params.to_external(),
    ), allow_unicode=True), encoding="utf-8")
    return run


class DetectionChainAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dcfr-analysis-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)

    def test_full_analysis_completes_and_normalizes_by_nearest_valid_points(self):
        run = make_run(self.root)
        summary = analyze(run)
        self.assertEqual(summary["status"], "completed")
        self.assertTrue((run / "results/analysis_results.npz").is_file())
        self.assertTrue((run / "results/frequency_response.png").is_file())
        self.assertTrue((run / "results/analysis_summary.json").is_file())
        with np.load(run / "results/analysis_results.npz") as saved:
            normalized = saved["peak_amplitudes_normalized"]
            self.assertTrue(np.isnan(normalized[20]))  # 零基带点排除
            valid = np.isfinite(normalized)
            self.assertAlmostEqual(np.nanmax(np.abs(normalized[valid] - 1.0)), 0.0, delta=0.03)
            self.assertEqual(saved["norm_factor"].size, 1)
        state = AnalysisWorkflow(run).snapshot()
        self.assertEqual([state["steps"][stage.id]["status"] for stage in stages.ANALYSIS],
                         ["completed", "completed", "completed"])
        self.assertEqual(summary["response"]["valid_points"], 40)
        self.assertEqual(summary["response"]["total_points"], 41)
        self.assertEqual(summary["response"]["zero_baseband_points"], 1)
        self.assertIn("双边带平均 (15–25 kHz 基带)", summary["response"]["slopes_percent_per_khz"])

    def test_staged_analysis_keeps_partial_outputs_then_completes_with_b03(self):
        run = make_run(self.root)
        summary = analyze(run, step_ids=["B01", "B02"])
        self.assertEqual(summary["status"], "partial")
        self.assertTrue((run / "results/analysis_results.npz").is_file())
        self.assertFalse((run / "results/frequency_response.png").exists())
        snapshot = (run / "results/analysis_results.npz").stat().st_mtime_ns
        summary = analyze(run, step_ids=["B03"])
        self.assertEqual(summary["status"], "completed")
        self.assertTrue((run / "results/frequency_response.png").is_file())
        self.assertEqual((run / "results/analysis_results.npz").stat().st_mtime_ns, snapshot)
        # B03 重算不移动仍被使用的前置产物。
        self.assertEqual(summary["backup_directory"], None)

    def test_invalid_points_quality_failure_keeps_diagnostics(self):
        run = make_run(self.root, peaks=np.full(41, np.nan))
        with self.assertRaises(ValueError):
            analyze(run, step_ids=["B01", "B02"])
        state = AnalysisWorkflow(run).snapshot()
        self.assertEqual(state["steps"]["B02"]["status"], "quality_failed")
        self.assertIn("无有效谱峰点", state["steps"]["B02"]["error"])
        self.assertEqual(state["summary"]["status"], "quality_failed")
        self.assertFalse((run / "results/analysis_results.npz").exists())

    def test_raw_change_invalidates_all_and_rerun_archives_old_results(self):
        run = make_run(self.root)
        analyze(run)
        with open(run / "raw/frequency_response.npz", "ab") as stream:
            stream.write(b"touch")
        ctx = AnalysisWorkflow(run)
        self.assertEqual([ctx.states[stage.id]["status"] for stage in stages.ANALYSIS],
                         ["stale", "stale", "stale"])
        summary = analyze(run)
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["backup_directory"])
        backups = [path for path in (run / "results").iterdir() if path.name.startswith("before_")]
        self.assertEqual({path.name for path in backups[0].iterdir()},
                         {"analysis_results.npz", "frequency_response.png",
                          "frequency_response.pdf", "analysis_summary.json"})

    def test_running_run_and_unknown_analysis_options_rejected(self):
        run = make_run(self.root, completion="running")
        with self.assertRaisesRegex(ValueError, "仍在写入"):
            AnalysisWorkflow(run)
        done = make_run(self.root)
        with self.assertRaisesRegex(ValueError, "不是分析参数"):
            analyze(done, analysis_parameters={"ANALYSIS_BIN_WIDTH_HZ": 12.0})


if __name__ == "__main__":
    unittest.main()
