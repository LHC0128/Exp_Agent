"""离线整次/分步一致、历史资格、内容指纹、归档与错误诊断。"""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
import yaml

from . import acquisition, analysis, preparation
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .test_workflow import ROOT, copy_config, scan_params


REGRESSION_CASES = {
    "z-aw-waveform-scope-check": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/z_aw_waveform_scope_check/__init__.py",
                "lab_workflows/experiment_modules/z_aw_waveform_scope_check/analysis.py"
            ],
            "tests": [
                "ScopeAnalysisTests::test_complete_and_staged_match_and_replot_archives_only_successors",
                "ScopeAnalysisTests::test_legacy_format_is_explicit_and_schema_change_does_not_change_raw_qualification",
                "ScopeAnalysisTests::test_raw_and_artifact_content_fingerprints_reject_stale_predecessors",
                "ScopeAnalysisTests::test_running_missing_and_flat_waveform_failures_preserve_diagnostics",
                "ScopeAnalysisTests::test_unknown_analysis_overrides_rejected_and_real_subprocess_writes_results",
                "ScopeAnalysisTests::test_historical_unknown_scope_range_is_null_without_fabricated_diagnostics"
            ]
        }
    }
}


def synthetic_run(root, name="0101_120000_fake", *, legacy=False, flat=False):
    params = scan_params(root)
    directory = root / "data" / DATA_TYPE / name
    raw = directory / "raw"
    raw.mkdir(parents=True)
    config = dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, measurement_mode="coil_voltage",
        schema_version=1, execution_mode="typed_workflow", completion_status="completed",
        run_tag=params.run_tag, parameters=params.to_external())
    if not legacy:
        config["raw_format_version"] = 1
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    source = preparation.prepare_control(params, root)
    preparation.save_source(SimpleNamespace(raw=raw), source)
    t = np.arange(-96, 97) / 64000
    voltage = np.zeros_like(t) if flat else .36 * np.sin(2*np.pi*1000*(t-.000125))+.01
    for index in range(2):
        acquisition._save_frame(SimpleNamespace(root=directory), index,
            dict(time_s=t, measured_voltage_v=voltage, trigger_time_s=t,
                trigger_voltage_v=np.where(t < 0, 5., 0.), scale_used_v_div=.2, offset_used_v=0,
                actual_rate_sa_s=64000, measured_preamble={}, trigger_preamble={}),
            requested_rate_sa_s=64000, requested_duration_s=.003, scope_config={})
    return directory


class ScopeAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zaw-analysis-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        self.silent = redirect_stdout(StringIO())
        self.silent.__enter__()
        self.addCleanup(lambda: self.silent.__exit__(None, None, None))

    def test_complete_and_staged_match_and_replot_archives_only_successors(self):
        full = synthetic_run(self.root, "full")
        staged = synthetic_run(self.root, "staged")
        expected = analysis.analyze(full)
        ctx = analysis.AnalysisWorkflow(staged)
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            ctx.execute(["B02"])
        for item in ("B01", "B02", "B03"):
            ctx = analysis.AnalysisWorkflow(staged)
            actual = ctx.execute([item], ctx.revision)
        self.assertEqual(actual["similarity_summary"], expected["similarity_summary"])
        self.assertGreater(actual["similarity_summary"]["shape_correlation"]["mean"], .998)
        self.assertLessEqual(abs(actual["similarity_summary"]["best_delay_s"]["mean"] - .000125), 1/64000 + 1e-15)
        self.assertEqual(actual["status"], "completed")
        before = (staged / "results/aligned_waveforms.npz").read_bytes()
        ctx = analysis.AnalysisWorkflow(staged)
        ctx.execute(["B03"], ctx.revision)
        self.assertEqual((staged / "results/aligned_waveforms.npz").read_bytes(), before)
        backup = next((staged / "results").glob("before_*"))
        self.assertTrue((backup / "waveform_similarity.png").exists())
        self.assertFalse((backup / "aligned_waveforms.npz").exists())
        self.assertTrue((staged / "results/waveform_similarity.pdf").is_file())

    def test_legacy_format_is_explicit_and_schema_change_does_not_change_raw_qualification(self):
        run = synthetic_run(self.root, legacy=True)
        config_path = run / "experiment_config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["schema_version"] = 99
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertEqual(analysis.analyze(run)["status"], "completed")
        config["measurement_mode"] = "current"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "历史波形格式"):
            analysis.AnalysisWorkflow(run)
        config["raw_format_version"] = 999
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不支持"):
            analysis.AnalysisWorkflow(run)

    def test_raw_and_artifact_content_fingerprints_reject_stale_predecessors(self):
        run = synthetic_run(self.root)
        ctx = analysis.AnalysisWorkflow(run)
        ctx.execute(["B01", "B02"])
        cache = run / "results/aligned_waveforms.npz"
        stat = cache.stat()
        value = bytearray(cache.read_bytes()); value[-1] ^= 1
        cache.write_bytes(value); os.utime(cache, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        refreshed = analysis.AnalysisWorkflow(run)
        self.assertEqual(refreshed.states["B01"]["status"], "completed")
        self.assertEqual(refreshed.states["B02"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            refreshed.execute(["B03"])
        refreshed.execute(["B02"])
        raw = run / "raw/source_optimal_control_params.csv"
        raw.write_text(raw.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            refreshed.execute(["B02"])
        self.assertEqual(refreshed.states["B01"]["status"], "stale")

    def test_running_missing_and_flat_waveform_failures_preserve_diagnostics(self):
        run = synthetic_run(self.root, flat=True)
        ctx = analysis.AnalysisWorkflow(run)
        ctx.execute(["B01"])
        with self.assertRaisesRegex(ValueError, "标准差为零"):
            ctx.execute(["B02"])
        snapshot = json.loads((run / "analysis_state.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["steps"]["B02"]["status"], "quality_failed")
        self.assertIn("标准差为零", snapshot["summary"]["error"])
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            analysis.AnalysisWorkflow(run).execute(["B03"])
        config_path = run / "experiment_config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["completion_status"] = "running"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "仍在写入"):
            analysis.AnalysisWorkflow(run)
        config["completion_status"] = "failed"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        (run / "raw/scope_capture_000.npz").unlink()
        (run / "raw/scope_capture_001.npz").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "未找到 SDS"):
            analysis.analyze(run)

    def test_unknown_analysis_overrides_rejected_and_real_subprocess_writes_results(self):
        run = synthetic_run(self.root)
        with self.assertRaisesRegex(ValueError, "没有分析覆盖参数"):
            analysis.AnalysisWorkflow(run, {"SCOPE_CYCLES": 2})
        env = os.environ | {"LAB_RUN_DIR": str(run), "LAB_TYPED_PARAMETERS": "{}", "MPLBACKEND": "Agg"}
        env.pop("LAB_CANCEL_FILE", None)
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", analysis.__name__], cwd=ROOT,
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))
        report = json.loads((run / "results/similarity_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["capture_count"], 2)
        self.assertEqual(report["status"], "completed")

    def test_historical_unknown_scope_range_is_null_without_fabricated_diagnostics(self):
        run = synthetic_run(self.root, legacy=True)
        for path in (run / "raw").glob("scope_capture_*.npz"):
            with np.load(path) as data:
                payload = {key: data[key] for key in data.files if key not in
                           {"scale_used_v_div", "offset_used_v"}}
            np.savez(path, **payload)
        result = analysis.analyze(run, step_ids=["B01", "B02"])
        self.assertIsNone(result["similarity_summary"]["scope_edge_fraction"]["mean"])
        self.assertTrue(all(record["scope_edge_fraction"] is None for record in result["captures"]))


if __name__ == "__main__":
    unittest.main()
