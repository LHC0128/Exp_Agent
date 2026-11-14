"""明确临时 run_dir 的科学结果、历史资格、归档与命令行回归。"""
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

from ..z_aw_waveform_scope_check import acquisition
from ..z_aw_waveform_scope_check.preparation import save_source
from . import analysis, analysis_steps, preparation
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .test_workflow import ROOT, copy_config, fake_hardware, scan_params


REGRESSION_CASES = {
    "z-aw-current-waveform-scope-check": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/__init__.py",
                "lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/analysis.py",
                "lab_workflows/experiment_modules/z_aw_waveform_scope_check/__init__.py"
            ],
            "tests": [
                "CurrentAnalysisTests::test_complete_and_staged_science_match_and_replot_keeps_prerequisite",
                "CurrentAnalysisTests::test_legacy_identity_explicit_and_raw_version_independent_from_schema",
                "CurrentAnalysisTests::test_offline_analysis_needs_no_original_sources_or_current_defaults",
                "CurrentAnalysisTests::test_content_fingerprint_detects_same_size_mtime_and_blocks_stale_successor",
                "CurrentAnalysisTests::test_product_fingerprint_invalidates_only_affected_stage",
                "CurrentAnalysisTests::test_live_context_reloads_configuration_before_reusing_scientific_products",
                "CurrentAnalysisTests::test_bad_inputs_do_not_move_previous_scientific_outputs",
                "CurrentAnalysisTests::test_flat_capture_reports_quality_failure_and_blocks_report",
                "CurrentAnalysisTests::test_invalid_resistance_target_time_or_frequency_and_running_rejected",
                "CurrentAnalysisTests::test_ch3_edge_fraction_uses_volts_and_current_in_consistent_units",
                "CurrentAnalysisTests::test_analysis_has_no_overrides_and_constructor_is_read_only",
                "CurrentAnalysisTests::test_real_cli_requires_explicit_temporary_run_directory"
            ]
        }
    }
}


def synthetic_run(root, name="0101_120000_fake", *, legacy=False, flat=False, source="theory"):
    params = scan_params(root, source=source)
    run = root / "data" / DATA_TYPE / name
    raw = run / "raw"
    raw.mkdir(parents=True)
    config = dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, measurement_mode="current",
        schema_version=1, execution_mode="typed_workflow", completion_status="completed",
        run_tag=params.run_tag, parameters=params.to_external())
    if not legacy:
        config["raw_format_version"] = 1
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    save_source(SimpleNamespace(raw=raw), preparation.prepare_control(params, root))
    t = np.arange(-96, 97) / 64000
    voltage = np.zeros_like(t) if flat else .36 * np.sin(2*np.pi*1000*(t-.000125))+.01
    for index in range(2):
        acquisition._save_frame(SimpleNamespace(root=run), index,
            dict(time_s=t, measured_voltage_v=voltage, trigger_time_s=t,
                trigger_voltage_v=np.where(t < 0, 5., 0.), scale_used_v_div=.2, offset_used_v=0,
                actual_rate_sa_s=64000, measured_preamble={}, trigger_preamble={}),
            requested_rate_sa_s=64000, requested_duration_s=.003, scope_config={})
    return run


class CurrentAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="z-current-analysis-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)
        silent = redirect_stdout(StringIO())
        silent.__enter__()
        self.addCleanup(lambda: silent.__exit__(None, None, None))

    def update_config(self, run, **values):
        path = run / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config.update(values)
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return config

    def test_complete_and_staged_science_match_and_replot_keeps_prerequisite(self):
        full = synthetic_run(self.root, "full")
        staged = synthetic_run(self.root, "staged")
        result = analysis.analyze(full)
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            analysis.AnalysisWorkflow(staged).execute(["B02"])
        for step in ("B01", "B02", "B03"):
            ctx = analysis.AnalysisWorkflow(staged)
            actual = ctx.execute([step], ctx.revision)
        self.assertEqual(actual["similarity_summary"], result["similarity_summary"])
        self.assertGreater(actual["similarity_summary"]["shape_correlation"]["mean"], .998)
        self.assertAlmostEqual(actual["similarity_summary"]["affine_gain"]["mean"], 12, delta=.05)
        self.assertAlmostEqual(actual["similarity_summary"]["affine_offset_v"]["mean"], .001, delta=2e-5)
        self.assertEqual(actual["status"], "completed")
        with np.load(full / "results/current_waveforms.npz") as a, np.load(staged / "results/current_waveforms.npz") as b:
            for key in a.files:
                np.testing.assert_array_equal(a[key], b[key])
        raw = (staged / "raw/scope_capture_000.npz").read_bytes()
        products = (staged / "results/current_waveforms.npz").read_bytes()
        ctx = analysis.AnalysisWorkflow(staged)
        ctx.execute(["B03"], ctx.revision)
        self.assertEqual((staged / "results/current_waveforms.npz").read_bytes(), products)
        self.assertEqual((staged / "raw/scope_capture_000.npz").read_bytes(), raw)
        backup = next((staged / "results").glob("before_*"))
        self.assertTrue((backup / "current_waveform_similarity.png").is_file())
        self.assertFalse((backup / "current_waveforms.npz").exists())

    def test_legacy_identity_explicit_and_raw_version_independent_from_schema(self):
        run = synthetic_run(self.root, legacy=True)
        self.update_config(run, schema_version=99)
        self.assertEqual(analysis.analyze(run)["status"], "completed")
        for updates in (dict(experiment_id="other"), dict(data_type="other"),
                        dict(measurement_mode="coil_voltage"), dict(raw_format_version=2),
                        dict(raw_format_version=True), dict(parameters=None),
                        dict(raw_format_version=1, measurement_mode="coil_voltage")):
            with self.subTest(updates=updates):
                original = (run / "experiment_config.yaml").read_bytes()
                self.update_config(run, **updates)
                with self.assertRaises(ValueError):
                    analysis.AnalysisWorkflow(run)
                (run / "experiment_config.yaml").write_bytes(original)

    def test_offline_analysis_needs_no_original_sources_or_current_defaults(self):
        run = synthetic_run(self.root, source="corrected_run")
        for base in (self.root / "control", self.root / "data/Mx_Z_Current_Coupling_Calibration",
                     self.root / "data/Z_AW_Closed_Loop_Waveform_Correction"):
            base.rename(base.with_name(base.name + "_unavailable"))
        self.assertEqual(analysis.analyze(run)["capture_count"], 2)

    def test_content_fingerprint_detects_same_size_mtime_and_blocks_stale_successor(self):
        run = synthetic_run(self.root)
        analysis.analyze(run)
        ctx = analysis.AnalysisWorkflow(run)
        raw = run / "raw/source_current_coupling_analysis.yaml"
        old = raw.stat()
        data = raw.read_bytes()
        raw.write_bytes(data.replace(b"100000", b"100001"))
        os.utime(raw, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "修订|缺少前置"):
            ctx.execute(["B03"], ctx.revision)
        refreshed = analysis.AnalysisWorkflow(run)
        self.assertEqual(refreshed.states["B01"]["status"], "stale")
        self.assertEqual(refreshed.summary["status"], "stale")

    def test_product_fingerprint_invalidates_only_affected_stage(self):
        run = synthetic_run(self.root)
        analysis.analyze(run)
        image = run / "results/current_waveform_similarity.png"
        image.write_bytes(image.read_bytes() + b"changed")
        ctx = analysis.AnalysisWorkflow(run)
        self.assertEqual(ctx.states["B02"]["status"], "completed")
        self.assertEqual(ctx.states["B03"]["status"], "stale")
        ctx.execute(["B03"], ctx.revision)
        self.assertEqual(ctx.states["B03"]["status"], "completed")

    def test_live_context_reloads_configuration_before_reusing_scientific_products(self):
        run = synthetic_run(self.root)
        analysis.analyze(run)
        ctx = analysis.AnalysisWorkflow(run)
        original = (run / "results/current_waveforms.npz").read_bytes()
        self.update_config(run, parameters=ctx.config["parameters"] | {"SENSE_RESISTOR_OHM": 20})
        with self.assertRaisesRegex(ValueError, "修订"):
            ctx.execute(["B03"], ctx.revision)
        self.assertEqual(ctx.states["B01"]["status"], "stale")
        self.assertEqual((run / "results/current_waveforms.npz").read_bytes(), original)

    def test_bad_inputs_do_not_move_previous_scientific_outputs(self):
        run = synthetic_run(self.root)
        analysis.analyze(run)
        image = run / "results/current_waveform_similarity.png"
        before = image.read_bytes()
        path = run / "raw/scope_capture_000.npz"
        with np.load(path) as data:
            values = {key: data[key] for key in data.files}
        values["trigger_voltage_v"] = np.zeros_like(values["trigger_voltage_v"])
        np.savez(path, **values)
        with self.assertRaisesRegex(ValueError, "下降沿"):
            analysis.analyze(run)
        self.assertEqual(image.read_bytes(), before)
        self.assertFalse(list((run / "results").glob("before_*")))
        state = json.loads((run / "analysis_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["B01"]["status"], "quality_failed")

    def test_flat_capture_reports_quality_failure_and_blocks_report(self):
        run = synthetic_run(self.root, flat=True)
        ctx = analysis.AnalysisWorkflow(run)
        with self.assertRaisesRegex(ValueError, "标准差"):
            ctx.execute(["B01", "B02", "B03"])
        self.assertEqual(ctx.states["B02"]["status"], "quality_failed")
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            ctx.execute(["B03"])
        self.assertFalse((run / "results/current_waveform_similarity.png").exists())

    def test_invalid_resistance_target_time_or_frequency_and_running_rejected(self):
        run = synthetic_run(self.root)
        config = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))
        for resistance in (0, -1, float("nan"), float("inf")):
            self.update_config(run, parameters=config["parameters"] | {"SENSE_RESISTOR_OHM": resistance})
            with self.assertRaisesRegex(ValueError, "有限正值"):
                analysis_steps.load_inputs(run)
        self.update_config(run, parameters=config["parameters"])
        path = run / "raw/applied_control_waveform.npz"
        with np.load(path) as data:
            original = {key: data[key] for key in data.files}
        for changes in (dict(time_s=np.zeros_like(original["time_s"])),
                        dict(target_current_a=np.full_like(original["time_s"], np.nan)),
                        dict(repeat_frequency_hz=np.float64(0))):
            np.savez(path, **(original | changes))
            with self.assertRaises(ValueError):
                analysis_steps.load_inputs(run)
        np.savez(path, **original)
        self.update_config(run, completion_status="running")
        with self.assertRaisesRegex(ValueError, "仍在写入"):
            analysis.AnalysisWorkflow(run)
        self.assertFalse((run / "analysis_state.json").exists())

    def test_ch3_edge_fraction_uses_volts_and_current_in_consistent_units(self):
        run = synthetic_run(self.root)
        for path in (run / "raw").glob("scope_capture_*.npz"):
            with np.load(path) as data:
                values = {key: data[key] for key in data.files}
            values["measured_voltage_v"] = .8 * np.sin(2*np.pi*1000*values["time_s"])
            np.savez(path, **values)
        result = analysis.analyze(run)
        expected = np.mean(np.abs(np.abs(values["measured_voltage_v"]) - .8) <= .016)
        self.assertAlmostEqual(result["similarity_summary"]["scope_edge_fraction"]["mean"], expected)
        self.assertGreater(expected, .05)

    def test_analysis_has_no_overrides_and_constructor_is_read_only(self):
        run = synthetic_run(self.root)
        before = {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob("*") if p.is_file()}
        analysis.AnalysisWorkflow(run).snapshot()
        with self.assertRaisesRegex(ValueError, "没有可调"):
            analysis.AnalysisWorkflow(run, {"CONTROL_SCALE": 2})
        after = {p.relative_to(run).as_posix(): p.read_bytes() for p in run.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_real_cli_requires_explicit_temporary_run_directory(self):
        entry = ROOT / "experiments/Z_AW_Current_Waveform_Scope_Check_plot.py"
        env = dict(os.environ, MPLBACKEND="Agg", PYTHONIOENCODING="utf-8")
        env.pop("LAB_RUN_DIR", None)
        missing = subprocess.run([sys.executable, str(entry)], cwd=self.root, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(missing.returncode, 2)
        run = synthetic_run(self.root)
        result = subprocess.run([sys.executable, str(entry), str(run)], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((run / "results/current_similarity_report.json").is_file())


if __name__ == "__main__":
    unittest.main()
