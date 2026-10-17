"""真实原始帧、分阶段科学分析、历史资格和产物指纹回归。"""
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
import yaml
from . import analysis, analysis_steps, workflow
from ...current_feedback import load_aw_frequency_response
from ..z_aw_closed_loop_waveform_correction.response_filter import build_learning_filter
from .test_workflow import ROOT, copy_config, fake_hardware, scan_params


def synthetic_run(root, *, reference=0, repeats=2):
    """通过真实采集步骤生成正式格式文件，只在驱动边界使用假设备。"""
    with fake_hardware(root):
        return workflow.run(replace(scan_params(root), scope_reference_channel=reference,
            scope_repeats=repeats), project_root=root)


class CurrentAnalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="z-current-analysis-")
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
        self.assertFalse((other / "results/transfer_function.png").exists())
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
        self.assertTrue((backup / "transfer_function.png").is_file())
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
        self.assertFalse((self.run / "results/transfer_function.png").exists())

    def test_single_valid_repeat_exports_diagnostics_without_closed_loop_phase(self):
        full_run = synthetic_run(self.root, reference=1, repeats=1)
        staged_run = synthetic_run(self.root, reference=1, repeats=1)
        raw_before = {path.name: path.read_bytes() for path in (full_run / "raw").glob("*")}
        config_before = (full_run / "experiment_config.yaml").read_bytes()
        report = analysis.analyze(full_run)
        staged = analysis.AnalysisWorkflow(staged_run)
        for item in ("B01", "B02", "B03"):
            staged.execute([item])
            staged = analysis.AnalysisWorkflow(staged_run)
        self.assertEqual(staged.states["B03"]["status"], "completed")
        self.assertEqual(report["amplitude_reliable_point_count"], 4)
        self.assertEqual(report["phase_reliable_point_count"], 0)
        self.assertEqual(report["reliable_point_count"], 0)
        self.assertFalse(report["closed_loop_usable"])
        self.assertEqual(report["reliable_band_hz"], {"minimum": None, "maximum": None})
        self.assertEqual(report["quality_limits"]["minimum_valid_phase_repeats"], 2)
        self.assertEqual(report["valid_frame_count_rule"], "exact_frequency_v1")
        with self.assertRaisesRegex(ValueError, "至少两次有效重复"):
            load_aw_frequency_response(self.root, full_run.name)
        for point in report["points"]:
            self.assertEqual(point["frame_count"], 1)
            self.assertEqual(point["valid_frame_count"], 1)
            self.assertAlmostEqual(point["transfer_magnitude_a_per_v"], .072)
            self.assertTrue(np.isfinite(point["transfer_phase_deg"]))
            self.assertAlmostEqual(point["phase_std_deg"], 0, places=5)
        with np.load(full_run / "results/frequency_response.npz") as full, np.load(staged_run / "results/frequency_response.npz") as partial:
            self.assertEqual(full["phase_reliable"].dtype.kind, "b")
            self.assertIn(full["frame_count"].dtype.kind, "iu")
            self.assertIn(full["valid_frame_count"].dtype.kind, "iu")
            self.assertEqual(full["valid_frame_count_rule"].shape, ())
            self.assertEqual(full["valid_frame_count_rule"].dtype.kind, "U")
            self.assertEqual(str(full["valid_frame_count_rule"]), "exact_frequency_v1")
            for key in full.files:
                if full[key].dtype.kind in "fiu":
                    np.testing.assert_allclose(full[key], partial[key], equal_nan=True)
                else:
                    np.testing.assert_array_equal(full[key], partial[key])
            self.assertTrue(np.isnan(full["reliable_frequency_min_hz"]))
            self.assertTrue(np.isnan(full["reliable_frequency_max_hz"]))
            np.testing.assert_array_equal(full["amplitude_reliable"], [True] * 4)
            np.testing.assert_array_equal(full["valid_frame_count"], [1] * 4)
        for name in ("frequency_response.json", "frequency_evaluation.json", "analysis_summary.json"):
            content = (full_run / "results" / name).read_text(encoding="utf-8")
            self.assertEqual(json.loads(content, parse_constant=lambda value: self.fail(value))["closed_loop_usable"], False)
        self.assertTrue((full_run / "results/transfer_function.png").is_file())
        self.assertTrue((full_run / "results/transfer_function.pdf").is_file())
        self.assertEqual((full_run / "experiment_config.yaml").read_bytes(), config_before)
        self.assertEqual({path.name: path.read_bytes() for path in (full_run / "raw").glob("*")}, raw_before)

    def test_mixed_actual_repeats_legacy_raw_and_bad_frame_do_not_qualify_phase(self):
        reference = synthetic_run(self.root, reference=1)
        config_path = reference / "experiment_config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config.pop("raw_format_version")
        config["parameters"]["SCOPE_REPEATS"] = 3
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        for index in (0, 3):
            (reference / f"raw/scope_f{index:03d}_r01.npz").unlink()
        report = analysis.analyze(reference)
        self.assertEqual([p["frame_count"] for p in report["points"]], [1, 2, 2, 1])
        self.assertEqual([p["valid_frame_count"] for p in report["points"]], [1, 2, 2, 1])
        self.assertEqual([p["phase_reliable"] for p in report["points"]], [False, True, True, False])
        self.assertEqual(report["amplitude_reliable_point_count"], 4)
        self.assertEqual(report["reliable_point_count"], 2)
        self.assertTrue(report["closed_loop_usable"])
        response = load_aw_frequency_response(self.root, reference.name)
        np.testing.assert_array_equal(response.reliable, [False, True, True, False])
        np.testing.assert_allclose(response.frequency_hz, [p["frequency_hz"] for p in report["points"]])
        self.assertEqual(report["reliable_band_hz"], {"minimum": report["points"][1]["frequency_hz"],
            "maximum": report["points"][2]["frequency_hz"]})
        source = reference / "raw/scope_f001_r01.npz"
        with np.load(source) as data:
            payload = {key: data[key] for key in data.files}
        payload["measured_voltage_v"] = np.zeros_like(payload["measured_voltage_v"])
        np.savez(source, **payload)
        report = analysis.analyze(reference)
        bad = report["points"][1]
        self.assertEqual(bad["frame_count"], 2)
        self.assertEqual(bad["valid_frame_count"], 1)
        self.assertFalse(bad["phase_reliable"])
        self.assertFalse(bad["reliable"])
        self.assertFalse(report["closed_loop_usable"])
        with self.assertRaisesRegex(ValueError, "至少两次有效重复"):
            load_aw_frequency_response(self.root, reference.name)

    def test_effective_count_uses_individual_quality_without_changing_statistics(self):
        frame = dict(frequency_hz=1000, transfer=.072 + 0j,
            phase_reference=analysis.PHASE_REFERENCE_MEASURED, saturation_detected=False,
            fit_correlation=1.0, drive_fit_correlation=1.0,
            current_amplitude_a=.036, drive_amplitude_measured_v=.5)
        pair = analysis_steps._aggregate_points([frame, frame])[0]
        self.assertTrue(pair["phase_reliable"])
        self.assertTrue(pair["reliable"])
        for edit in ({"saturation_detected": True}, {"fit_correlation": .95},
                     {"fit_correlation": float("nan")}, {"drive_fit_correlation": .97},
                     {"transfer": 0j}, {"transfer": complex(float("nan"), 0)}):
            with self.subTest(edit=edit):
                point = analysis_steps._aggregate_points([frame, frame | edit])[0]
                self.assertEqual(point["frame_count"], 2)
                self.assertEqual(point["valid_frame_count"], 1)
                self.assertFalse(point["phase_reliable"])
                self.assertFalse(point["reliable"])
        low_fit = frame | {"fit_correlation": .94, "transfer": .036 + 0j}
        point = analysis_steps._aggregate_points([frame, low_fit])[0]
        self.assertAlmostEqual(point["fit_correlation_mean"], .97)
        self.assertAlmostEqual(point["transfer_real_a_per_v"], .054)
        self.assertAlmostEqual(point["phase_std_deg"], 0)
        self.assertTrue(point["amplitude_reliable"])
        self.assertFalse(point["phase_reliable"])

        transfers = .072 * np.exp(1j * np.deg2rad([179.0, -179.0]))
        wrapped = analysis_steps._aggregate_points([frame | {"transfer": value} for value in transfers])[0]
        self.assertAlmostEqual(wrapped["transfer_real_a_per_v"], float(np.mean(transfers).real))
        self.assertAlmostEqual(wrapped["transfer_imag_a_per_v"], float(np.mean(transfers).imag))
        self.assertAlmostEqual(wrapped["phase_std_deg"], np.rad2deg(np.sqrt(-2 * np.log(np.cos(np.deg2rad(1.0))))))
        self.assertTrue(wrapped["phase_reliable"])

    def test_real_producer_retains_internal_bad_point_and_old_results_require_raw_reanalysis(self):
        run = synthetic_run(self.root, reference=1)
        (run / "raw/scope_f001_r01.npz").unlink()
        watched = [run / "experiment_config.yaml", *(run / "raw").glob("*")]
        original = {path: path.read_bytes() for path in watched}
        report = analysis.analyze(run)
        self.assertEqual([point["valid_frame_count"] for point in report["points"]], [2, 1, 2, 2])
        self.assertTrue(report["closed_loop_usable"])
        response = load_aw_frequency_response(self.root, run.name)
        np.testing.assert_array_equal(response.reliable, [True, False, True, True])
        np.testing.assert_allclose(response.frequency_hz, [point["frequency_hz"] for point in report["points"]])
        learning = build_learning_filter(SimpleNamespace(time_s=np.arange(32) / 32000), .072, response, 4000, .1)
        np.testing.assert_array_equal(learning.excluded_frequency_hz, [2000])
        self.assertFalse(learning.learning_band[2])
        self.assertEqual(learning.response_model_a_per_v[2], 0j)
        self.assertTrue(learning.learning_band[3])
        result_path = run / "results/frequency_response.npz"
        with np.load(result_path) as data:
            response_payload = {key: data[key] for key in data.files}
        for problem, old_payload in (
                ("missing_count", {key: value for key, value in response_payload.items() if key != "valid_frame_count"}),
                ("v2_missing_rule", {key: value for key, value in response_payload.items() if key != "valid_frame_count_rule"}),
                ("old_rule", response_payload | {"valid_frame_count_rule": np.asarray("tolerant_frequency_v2")}),
                ("non_scalar_rule", response_payload | {"valid_frame_count_rule": np.asarray(["exact_frequency_v1"])}),
                ("byte_rule", response_payload | {"valid_frame_count_rule": np.asarray(b"exact_frequency_v1")})):
            with self.subTest(problem=problem):
                np.savez(result_path, **old_payload)
                with self.assertRaisesRegex(ValueError, "重新分析"):
                    load_aw_frequency_response(self.root, run.name)
        analysis.analyze(run)
        np.testing.assert_array_equal(load_aw_frequency_response(self.root, run.name).reliable, response.reliable)
        self.assertEqual({path: path.read_bytes() for path in watched}, original)

    def test_distinct_nearby_frequencies_never_count_as_repeated_frames(self):
        for repeats in (1, 2):
            with self.subTest(repeats=repeats):
                params = replace(scan_params(self.root), frequency_start_hz=1000,
                    frequency_stop_hz=1000.001, frequency_points=2,
                    scope_repeats=repeats, scope_reference_channel=1)
                with fake_hardware(self.root):
                    run = workflow.run(params, project_root=self.root)
                original = {path: path.read_bytes() for path in [
                    run / "experiment_config.yaml", *(run / "raw").glob("*")]}
                report = analysis.analyze(run)
                self.assertEqual(report["capture_count"], 2 * repeats)
                self.assertEqual([point["frame_count"] for point in report["points"]], [repeats, repeats])
                self.assertEqual([point["valid_frame_count"] for point in report["points"]], [repeats, repeats])
                self.assertEqual([point["phase_reliable"] for point in report["points"]], [repeats >= 2] * 2)
                self.assertEqual(report["closed_loop_usable"], repeats >= 2)
                if repeats == 1:
                    with self.assertRaisesRegex(ValueError, "至少两次有效重复"):
                        load_aw_frequency_response(self.root, run.name)
                else:
                    np.testing.assert_array_equal(load_aw_frequency_response(self.root, run.name).reliable, [True, True])
                self.assertEqual({path: path.read_bytes() for path in original}, original)

    def test_phase_qualification_version_invalidates_old_cache_without_rewriting_run(self):
        for previous_version in ("z-coil-current-frequency-stages-v1",
                "z-coil-current-frequency-stages-v2-valid-phase-repeats"):
            with self.subTest(previous_version=previous_version):
                reference = synthetic_run(self.root, reference=1, repeats=1)
                with patch.object(analysis, "ANALYSIS_VERSION", previous_version):
                    analysis.analyze(reference)
                watched = [reference / "experiment_config.yaml", reference / "analysis_state.json",
                    reference / "raw/scope_f000_r00.npz", reference / "results/frequency_response.npz"]
                before = {path: path.read_bytes() for path in watched}
                changed = analysis.AnalysisWorkflow(reference)
                self.assertEqual(changed.states["B01"]["status"], "stale")
                self.assertEqual(changed.states["B02"]["status"], "stale")
                self.assertEqual(changed.summary["status"], "stale")
                with self.assertRaisesRegex(ValueError, "缺少前置"):
                    changed.execute(["B03"])
                self.assertEqual({path: path.read_bytes() for path in watched}, before)
                changed.execute(["B01", "B02", "B03"])
                self.assertEqual(changed.states["B03"]["status"], "completed")
                self.assertFalse(changed.summary["closed_loop_usable"])
                self.assertEqual(changed.fingerprint["analysis_version"], analysis.ANALYSIS_VERSION)

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
        for edit in ({"experiment_id": "z-coil-inductance-frequency-response"},
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

    def test_reference_optional_missing_frame_fallback_and_reliable_band_are_preserved(self):
        from dataclasses import replace
        with fake_hardware(self.root):
            reference = workflow.run(replace(scan_params(self.root), scope_reference_channel=1), project_root=self.root)
        report = analysis.analyze(reference)
        self.assertEqual(report["phase_reference"], analysis.PHASE_REFERENCE_MEASURED)
        self.assertEqual(report["phase_reliable_point_count"], 4)
        with np.load(reference / "results/frequency_response.npz") as data:
            self.assertEqual(int(data["format_version"]), 4)
            np.testing.assert_allclose(abs(data["transfer_real_a_per_v"] + 1j*data["transfer_imag_a_per_v"]), .072)
        source = reference / "raw/scope_f003_r01.npz"
        with np.load(source) as data:
            original = {key: data[key] for key in data.files}
        for reference_problem in ("low_correlation", "missing"):
            with self.subTest(reference_problem=reference_problem):
                payload = dict(original)
                if reference_problem == "missing":
                    payload.pop("reference_voltage_v")
                else:
                    phase = 2 * np.pi * float(payload["frequency_hz"]) * payload["time_s"]
                    payload["reference_voltage_v"] = .5 * np.sin(phase) + np.cos(2 * phase)
                np.savez(source, **payload)
                report = analysis.analyze(reference)
                self.assertEqual(report["phase_reference"], analysis.PHASE_REFERENCE_COMMANDED)
                self.assertEqual(report["phase_reliable_point_count"], 0)
                self.assertEqual(report["reliable_point_count"], 4)
                self.assertAlmostEqual(report["reliable_band_hz"]["minimum"], 1000)
                self.assertAlmostEqual(report["reliable_band_hz"]["maximum"], 4000)
                with np.load(reference / "results/frequency_response.npz") as data:
                    self.assertFalse(np.any(data["phase_reliable"]))
                with self.assertRaisesRegex(ValueError, "相位参考不是实测驱动电压"):
                    load_aw_frequency_response(self.root, reference.name)
        original = (reference / "experiment_config.yaml").read_bytes()
        with self.assertRaisesRegex(ValueError, "没有可独立覆盖"):
            analysis.AnalysisWorkflow(reference, {"SENSE_RESISTOR_OHM": 100})
        self.assertEqual((reference / "experiment_config.yaml").read_bytes(), original)

    def test_zero_signal_quality_failure_retains_point_diagnostics_and_blocks_export(self):
        for path in (self.run / "raw").glob("scope_f*_r*.npz"):
            with np.load(path) as data:
                payload = {key: data[key] for key in data.files}
            payload["measured_voltage_v"] = np.zeros_like(payload["measured_voltage_v"])
            np.savez(path, **payload)
        ctx = analysis.AnalysisWorkflow(self.run)
        with self.assertRaisesRegex(ValueError, "可靠频点"):
            ctx.execute(["B01", "B02", "B03"])
        diagnostic = json.loads((self.run / "results/frequency_evaluation.json").read_text(encoding="utf-8"))
        self.assertEqual(len(diagnostic["points"]), 4)
        self.assertEqual(ctx.states["B02"]["status"], "quality_failed")
        self.assertEqual(ctx.states["B03"]["status"], "pending")
        self.assertFalse((self.run / "results/transfer_function.png").exists())

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
