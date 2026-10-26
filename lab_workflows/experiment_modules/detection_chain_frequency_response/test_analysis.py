"""分阶段离线分析、指纹失效与归档的无硬件回归。"""

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import pytest

import numpy as np
import yaml

from . import stages
from .analysis import AnalysisWorkflow, analyze
from .models import DetectionChainFrequencyResponseParams
from .test_workflow import copy_config

DATA_TYPE = "Detection_Chain_Frequency_Response"


REGRESSION_CASES = {
    "detection-chain-frequency-response": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/detection_chain_frequency_response/analysis.py",
                "lab_workflows/experiment_modules/detection_chain_frequency_response/definition.py",
                "lab_workflows/experiment_modules/detection_chain_frequency_response/__init__.py",
                "lab_workflows/experiment_modules/detection_chain_frequency_response/run_format.py"
            ],
            "tests": [
                "DetectionChainAnalysisTests::test_full_analysis_completes_and_normalizes_by_nearest_valid_points",
                "DetectionChainAnalysisTests::test_staged_analysis_keeps_partial_outputs_then_completes_with_b03",
                "DetectionChainAnalysisTests::test_invalid_points_quality_failure_keeps_diagnostics",
                "DetectionChainAnalysisTests::test_raw_change_invalidates_all_and_rerun_archives_old_results",
                "DetectionChainAnalysisTests::test_running_run_and_unknown_analysis_options_rejected",
                "test_real_module_requires_explicit_run_outside_repository",
                "test_foreign_identity_is_rejected_before_any_analysis_write",
                "test_package_public_exports_remain_compatible",
                "test_registered_adapter_uses_explicit_directory_and_clears_inherited_context"
            ]
        }
    }
}


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


@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_module_requires_explicit_run_outside_repository(tmp_path, mode):
    run = make_run(tmp_path)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="p1-2-remaining-cwd-") as temporary:
        cwd = Path(temporary).resolve()
        repository = Path(__file__).resolve().parents[3]
        assert not cwd.is_relative_to(repository)
        decoy = cwd / "data" / "Detection_Chain_Frequency_Response" / "9999_latest"
        shutil.copytree(run, decoy)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LAB_") and k != "PYTHONPATH"}
        env.update(MPLBACKEND="Agg", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        missing = cwd / "not-a-run"
        if mode != "missing":
            env["LAB_RUN_DIR"] = str(run if mode == "explicit" else missing)
        command = [sys.executable, "-X", "utf8", "-m",
            "lab_workflows.experiment_modules.detection_chain_frequency_response.analysis"]
        started = time.monotonic()
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
        print(json.dumps(dict(command=command, cwd=str(cwd), mode=mode,
            lab_environment={k: v for k, v in env.items() if k.startswith("LAB_")},
            returncode=result.returncode, elapsed_seconds=time.monotonic() - started), ensure_ascii=False))
        print(result.stdout, result.stderr)
        assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == before
        assert not missing.exists()
        assert {p: (run / p).read_bytes() for p in before if p.parts[0] == "raw"} == {
            p: value for p, value in before.items() if p.parts[0] == "raw"}
        if mode == "explicit":
            assert result.returncode == 0, result.stdout + result.stderr
            assert (run / "results/analysis_results.npz").is_file()
            assert json.loads((run / "results/analysis_summary.json").read_text(encoding="utf-8"))["status"] == "completed"
            assert yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == yaml.safe_load(before[Path("experiment_config.yaml")])["parameters"]
        else:
            assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
            assert result.returncode != 0
            assert ("缺少 LAB_RUN_DIR" if mode == "missing" else "运行目录不存在") in result.stderr


@pytest.mark.parametrize(("field", "value"), [("experiment_id", "other-experiment"), ("data_type", "Other_Data"), ("raw_format_version", True)])
@pytest.mark.parametrize("cached", [False, True])
def test_foreign_identity_is_rejected_before_any_analysis_write(tmp_path, field, value, cached):
    run = make_run(tmp_path)
    ctx = AnalysisWorkflow(run) if cached else None
    config_path = run / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config[field] = value
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="experiment_id|data_type|raw_format_version"):
        if ctx is None:
            analyze(run)
        else:
            ctx.execute(["B01"])
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


def test_package_public_exports_remain_compatible():
    from . import definition
    from .models import DetectionChainFrequencyResponseParams

    package = sys.modules[__package__]
    assert package.__all__ == ["DEFINITION"]
    assert package.DEFINITION is definition.DEFINITION
    with pytest.raises(AttributeError, match="unknown_public_symbol"):
        getattr(package, "unknown_public_symbol")


def test_registered_adapter_uses_explicit_directory_and_clears_inherited_context(tmp_path, monkeypatch):
    from lab_workflows.experiments import get_experiment, typed

    root = tmp_path / "project"
    root.mkdir()
    (root / "params").mkdir()
    repository = Path(__file__).resolve().parents[3]
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copy2(repository / "params" / name, root / "params" / name)
    run = make_run(root)
    before_parameters = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    decoy = run.with_name("9999_latest")
    shutil.copytree(run, decoy)
    before = {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()}
    adapter = get_experiment("detection-chain-frequency-response").analysis_runner.__self__
    monkeypatch.setattr(adapter, "root", root)
    popen, launches = typed.subprocess.Popen, []

    def launch(command, **kwargs):
        launches.append((command, kwargs))
        return popen([sys.executable, *command[1:]], **kwargs)

    monkeypatch.setattr(typed.subprocess, "Popen", launch)
    monkeypatch.setenv("LAB_RUN_DIR", str(decoy))
    monkeypatch.setenv("LAB_TYPED_PARAMETERS", "invalid old context")
    result = adapter.analyze(run, None)
    assert len(launches) == 1
    command, kwargs = launches[0]
    assert command[-2:] == ["-m", "lab_workflows.experiment_modules.detection_chain_frequency_response.analysis"]
    assert kwargs["cwd"] == root and not root.is_relative_to(repository)
    assert kwargs["env"]["LAB_RUN_DIR"] == str(run.resolve())
    assert "LAB_TYPED_PARAMETERS" not in kwargs["env"]
    assert result["run_dir"] == str(run.resolve()) and result["artifacts"]
    assert yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before_parameters
    assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == before
    assert not list((root / "data/.gui_jobs").glob("*.cancel"))


if __name__ == "__main__":
    unittest.main()
