"""示波器完整/分步分析、科学数值、指纹、归档与历史格式的无硬件回归。"""
from pathlib import Path
import hashlib
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

from . import analysis, analysis_steps, run_format
from .models import ScopeCaptureParams
from .test_workflow import ROOT, scan_params


REGRESSION_CASES = {
    "scope-capture": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/scope_capture/analysis.py",
                "lab_workflows/experiment_modules/scope_capture/definition.py",
                "lab_workflows/experiment_modules/scope_capture/__init__.py"
            ],
            "tests": [
                "ScopeAnalysisTests::test_full_and_staged_science_match_independent_welch",
                "ScopeAnalysisTests::test_missing_prerequisite_quality_failure_and_no_coverage_options",
                "ScopeAnalysisTests::test_raw_qualification_is_independent_of_parameter_schema",
                "ScopeAnalysisTests::test_input_or_product_drift_invalidates_and_rerun_archives_only_downstream",
                "ScopeAnalysisTests::test_corrupt_raw_does_not_archive_or_overwrite_previous_scientific_results",
                "ScopeAnalysisTests::test_envelope_preserves_single_sample_pulses_and_unknown_vertical_range",
                "ScopeAnalysisTests::test_real_plot_entrypoint_creates_scientific_outputs",
                "test_real_module_requires_explicit_run_outside_repository",
                "test_status_write_failure_preserves_original_config_bytes",
                "test_package_public_exports_remain_compatible",
                "test_registered_adapter_uses_explicit_directory_and_clears_inherited_context"
            ]
        }
    }
}


def make_run(root, name="run", *, samples=500, rate=50000.0, legacy=False):
    run = root / name
    (run / "raw").mkdir(parents=True)
    t = np.arange(samples) / rate
    v = 0.8 * np.sin(2 * np.pi * 1000 * t) + 0.05
    config = dict(experiment_id="scope-capture", experiment_type="Scope_Capture",
                  schema_version=2 if legacy else 3, completion_status="completed",
                  parameters=scan_params().to_external(), scope_configuration={
                      "actual_vertical_scale_v_div": .2, "actual_vertical_offset_v": 0})
    if not legacy:
        config["raw_format_version"] = 1
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    np.savez(run / "raw/waveform.npz", time_s=t, voltage_v=v, channel=1,
             requested_rate_sa_s=rate, requested_duration_s=samples / rate)
    return run


def independent_welch(v, rate):
    n = min(10000, len(v))
    window = .5 - .5 * np.cos(2 * np.pi * np.arange(n) / n)
    powers = []
    for start in range(0, len(v) - n + 1, n - n // 2):
        segment = v[start:start + n]
        power = np.abs(np.fft.rfft((segment - segment.mean()) * window)) ** 2 / (rate * np.sum(window ** 2))
        power[1:-1 if n % 2 == 0 else None] *= 2
        powers.append(power)
    return np.fft.rfftfreq(n, 1 / rate), np.mean(powers, axis=0)


class ScopeAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scope-analysis-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_full_and_staged_science_match_independent_welch(self):
        full = make_run(self.root, "full", samples=25000)
        staged = make_run(self.root, "staged", samples=25000)
        analysis.analyze(full)
        for step in ("B01", "B02", "B03"):
            ctx = analysis.AnalysisWorkflow(staged)
            ctx.execute([step], ctx.revision)
        with np.load(full / "raw/waveform.npz") as raw:
            expected_f, expected_psd = independent_welch(raw["voltage_v"], 50000)
        with np.load(full / "results/psd.npz") as a, np.load(staged / "results/psd.npz") as b:
            np.testing.assert_allclose(a["frequency_hz"], expected_f, rtol=1e-12, atol=1e-8)
            np.testing.assert_allclose(a["psd_v2_hz"], expected_psd, rtol=1e-10, atol=1e-15)
            np.testing.assert_array_equal(a["psd_v2_hz"], b["psd_v2_hz"])
            self.assertAlmostEqual(float(np.sum(a["psd_v2_hz"]) * 5), .8 ** 2 / 2, places=10)
        display = json.loads((full / "results/display.json").read_text(encoding="utf-8"))
        self.assertTrue(display["overrange"])
        self.assertLessEqual(len(display["time_s"]), 6000)
        self.assertAlmostEqual(display["voltage_v"][0], .05)
        self.assertTrue((full / "results/waveform_psd.png").read_bytes().startswith(b"\x89PNG"))
        self.assertTrue((full / "results/waveform_psd.pdf").read_bytes().startswith(b"%PDF"))
        self.assertEqual(analysis.AnalysisWorkflow(staged).summary["status"], "completed")

    def test_missing_prerequisite_quality_failure_and_no_coverage_options(self):
        run = make_run(self.root)
        ctx = analysis.AnalysisWorkflow(run)
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            ctx.execute(["B02"])
        self.assertFalse((run / "results").exists())
        with self.assertRaisesRegex(ValueError, "没有分析覆盖参数"):
            analysis.AnalysisWorkflow(run, {"psd_window": "boxcar"})
        raw = run / "raw/waveform.npz"
        raw.unlink()
        with self.assertRaisesRegex(ValueError, "缺少原始波形"):
            ctx.execute(["B01", "B02", "B03"])
        self.assertEqual(ctx.states["B01"]["status"], "quality_failed")
        self.assertEqual(ctx.states["B02"]["status"], "pending")

    def test_raw_qualification_is_independent_of_parameter_schema(self):
        run = make_run(self.root, legacy=True)
        path = run / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["schema_version"] = 99
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertEqual(analysis_steps.load_inputs(run)["voltage_v"].size, 500)
        legacy = dict(config)
        legacy.pop("experiment_id")
        self.assertEqual(run_format.analysis_block_reason(legacy), "")
        for changed in ({"experiment_id": "other"}, {"raw_format_version": 2},
                        {"raw_format_version": True}, {"parameters": None},
                        {"experiment_type": "other"}, {"completion_status": "running"}):
            self.assertTrue(run_format.analysis_block_reason(config | changed), changed)

    def test_input_or_product_drift_invalidates_and_rerun_archives_only_downstream(self):
        run = make_run(self.root)
        analysis.analyze(run)
        inputs = run / "results/analysis_inputs.json"
        before = inputs.read_bytes()
        ctx = analysis.AnalysisWorkflow(run)
        ctx.execute(["B02", "B03"], ctx.revision)
        self.assertEqual(inputs.read_bytes(), before)
        backups = list((run / "results").glob("before_*"))
        self.assertEqual(len(backups), 1)
        self.assertFalse((backups[0] / "analysis_inputs.json").exists())
        product = run / "results/psd.json"
        stat = product.stat()
        content = bytearray(product.read_bytes())
        content[-2] = ord(" ")
        product.write_bytes(content)
        os.utime(product, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        refreshed = analysis.AnalysisWorkflow(run)
        self.assertEqual(refreshed.states["B01"]["status"], "completed")
        self.assertEqual(refreshed.states["B02"]["status"], "stale")
        self.assertEqual(refreshed.states["B03"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            refreshed.execute(["B03"])

    def test_corrupt_raw_does_not_archive_or_overwrite_previous_scientific_results(self):
        run = make_run(self.root)
        analysis.analyze(run)
        before = {p.name: p.read_bytes() for p in (run / "results").iterdir()}
        np.savez(run / "raw/waveform.npz", time_s=[0, 1], voltage_v=[np.nan, 0],
                 channel=1, requested_rate_sa_s=1, requested_duration_s=2)
        ctx = analysis.AnalysisWorkflow(run)
        with self.assertRaisesRegex(ValueError, "非有限"):
            ctx.execute(["B01", "B02", "B03"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in (run / "results").iterdir()})
        self.assertEqual(ctx.states["B01"]["status"], "quality_failed")

    def test_parameter_migration_and_explicit_new_key_precedence(self):
        old = ScopeCaptureParams.from_external({"channel": 2, "temperature_switch_off_settle_s": .7}, schema_version=1)
        self.assertEqual(old.trigger_channel, 2)
        self.assertEqual(old.system_settle_s, .7)
        current = ScopeCaptureParams.from_external({"SYSTEM_SETTLE_S": .4,
            "temperature_switch_off_settle_s": .7}, schema_version=2)
        self.assertEqual(current.system_settle_s, .4)
        self.assertEqual(ScopeCaptureParams.from_external(current.to_external()).to_external(), current.to_external())

    def test_envelope_preserves_single_sample_pulses_and_unknown_vertical_range(self):
        v = np.zeros(100000)
        v[12345], v[98765] = 7, -9
        indices = analysis.envelope_indices(v)
        self.assertTrue({0, 99999, 12345, 98765}.issubset(indices))
        self.assertLessEqual(len(indices), 6000)
        self.assertTrue(all(value is None for value in analysis.vertical_metadata([], v).values()))
        for t, values in (([0, 0], [1, 2]), ([0, 1, 3], [1, 2, 3]), ([0, 1], [1, np.inf])):
            with self.assertRaises(ValueError):
                analysis.validate_waveform(t, values)

    def test_real_plot_entrypoint_creates_scientific_outputs(self):
        run = make_run(self.root)
        result = subprocess.run([str(ROOT / "agent_exp_env/Scripts/python.exe"),
            str(ROOT / "experiments/Scope_Capture_plot.py"), str(run)], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            env=os.environ | {"PYTHONIOENCODING": "utf-8"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((run / "results/display.json").read_text(encoding="utf-8"))["sample_count"], 500)
        self.assertEqual(analysis.AnalysisWorkflow(run).states["B03"]["status"], "completed")


@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_module_requires_explicit_run_outside_repository(tmp_path, mode):
    run = make_run(tmp_path)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="p1-2-remaining-cwd-") as temporary:
        cwd = Path(temporary).resolve()
        repository = Path(__file__).resolve().parents[3]
        assert not cwd.is_relative_to(repository)
        decoy = cwd / "data" / "Scope_Capture" / "9999_latest"
        shutil.copytree(run, decoy)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LAB_") and k != "PYTHONPATH"}
        env.update(MPLBACKEND="Agg", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        missing = cwd / "not-a-run"
        if mode != "missing":
            env["LAB_RUN_DIR"] = str(run if mode == "explicit" else missing)
        command = [sys.executable, "-X", "utf8", "-m",
            "lab_workflows.experiment_modules.scope_capture.analysis"]
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
            assert (run / "results/psd.npz").is_file()
            assert json.loads((run / "results/display.json").read_text(encoding="utf-8"))["sample_count"] == 500
            assert yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == yaml.safe_load(before[Path("experiment_config.yaml")])["parameters"]
        else:
            assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
            assert result.returncode != 0
            assert ("缺少 LAB_RUN_DIR" if mode == "missing" else "运行目录不存在") in result.stderr


@pytest.mark.parametrize("missing_raw", [False, True])
@pytest.mark.parametrize("fault", ["write", "replace"])
def test_status_write_failure_preserves_original_config_bytes(tmp_path, monkeypatch, missing_raw, fault):
    run = make_run(tmp_path)
    if missing_raw:
        (run / "raw/waveform.npz").unlink()
    config_path = run / "experiment_config.yaml"
    before = config_path.read_bytes()
    dump, write_text, replace = yaml.safe_dump, Path.write_text, os.replace
    failure, attempts = OSError("模拟部分状态写入失败"), []

    def partial_dump(config, stream=None, **kwargs):
        if fault == "write" and stream is not None and "analysis_status" in config:
            attempts.append(Path(stream.name))
            stream.write("analysis_status: partial\n")
            raise failure
        return dump(config, stream, **kwargs)

    def partial_write(path, data, *args, **kwargs):
        if fault == "write" and path == config_path:
            attempts.append(path)
            write_text(path, "analysis_status: partial\n", encoding="utf-8")
            raise failure
        return write_text(path, data, *args, **kwargs)

    def failed_replace(source, destination):
        if fault == "replace" and Path(destination).resolve() == config_path.resolve():
            attempts.append(Path(source))
            assert Path(source).read_bytes()
            raise failure
        return replace(source, destination)

    monkeypatch.setattr(yaml, "safe_dump", partial_dump)
    monkeypatch.setattr(Path, "write_text", partial_write)
    monkeypatch.setattr(os, "replace", failed_replace)
    with pytest.raises(ValueError if missing_raw else OSError) as caught:
        analysis.AnalysisWorkflow(run).execute(["B01"])
    if missing_raw:
        assert "缺少原始波形" in str(caught.value)
    else:
        assert caught.value is failure
    assert len(attempts) == 1
    assert config_path.read_bytes() == before
    assert not list(run.glob(".experiment_config.yaml.*.tmp"))


def test_package_public_exports_remain_compatible():
    from . import definition
    from .models import ScopeCaptureParams

    package = sys.modules[__package__]
    assert package.__all__ == ["ADAPTER","DEFINITION","ScopeCaptureParams"]
    assert package.DEFINITION is definition.DEFINITION
    assert package.ADAPTER is definition.ADAPTER
    assert package.ScopeCaptureParams is ScopeCaptureParams
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
    adapter = get_experiment("scope-capture").analysis_runner.__self__
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
    assert command[-2:] == ["-m", "lab_workflows.experiment_modules.scope_capture.analysis"]
    assert kwargs["cwd"] == root and not root.is_relative_to(repository)
    assert kwargs["env"]["LAB_RUN_DIR"] == str(run.resolve())
    assert "LAB_TYPED_PARAMETERS" not in kwargs["env"]
    assert result["run_dir"] == str(run.resolve()) and result["artifacts"]
    assert yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before_parameters
    assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == before
    assert not list((root / "data/.gui_jobs").glob("*.cancel"))


if __name__ == "__main__":
    unittest.main()
