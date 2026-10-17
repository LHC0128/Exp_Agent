"""检测链频响独立验收：参数兼容、科学数值、资格与阶段产物。"""

from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
import yaml

from . import workflow
from .analysis import AnalysisWorkflow, analyze
from .models import DetectionChainFrequencyResponseParams
from .stages import ACQUISITION
from .test_analysis import make_run
from .test_workflow import ROOT, copy_config, fake_hardware, scan_params


class DetectionChainIndependentAcceptance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dcfr-independent-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)

    def change_config(self, run, **changes):
        path = run / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config.update(changes)
        path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    def test_legacy_wait_migration_current_yaml_roundtrip_and_explicit_new_key(self):
        old = {"AM_SETTLE_TIME_S": 0.2, "TEMP_SWITCH_OFF_LEAD_S": 1.3,
               "RUN_TAG": "old-run", "HF2_DEMOD_TC_S": 0.0008}
        migrated = DetectionChainFrequencyResponseParams.from_external(old, schema_version=1)
        self.assertEqual(migrated.system_settle_s, 1.3)
        self.assertEqual(migrated.hf2_demod_tc_s, 0.0008)
        explicit = DetectionChainFrequencyResponseParams.from_external(old | {"SYSTEM_SETTLE_S": 0.4})
        self.assertEqual(explicit.system_settle_s, 0.4)
        params = DetectionChainFrequencyResponseParams.from_yaml(
            ROOT / "params/experiments/detection-chain-frequency-response.yaml")
        target = self.root / "roundtrip.yaml"
        params.save_yaml(target)
        self.assertEqual(DetectionChainFrequencyResponseParams.from_yaml(target).to_external(), params.to_external())

    def test_complete_and_staged_acquisition_use_same_data_and_keep_original_snapshot(self):
        with fake_hardware(self.root):
            full = workflow.run(scan_params(), project_root=self.root)
        with fake_hardware(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            for stage in ACQUISITION[:7]:
                ctx.execute([stage.id])
            staged = ctx.run_dir
            self.assertNotEqual(full, staged)
            before = (staged / "experiment_config.yaml").read_bytes()
            hardware_calls = {key: list(device.mock_calls) for key, device in hw.devices.items()}
            ctx.update_parameters({"FIXED_PARAMS.temperature": 99.0, "HF2_OSC_FREQ_HZ": 91000.0})
            self.assertEqual({key: list(device.mock_calls) for key, device in hw.devices.items()}, hardware_calls)
            self.assertEqual((staged / "experiment_config.yaml").read_bytes(), before)
            ctx.finish()
            hw.devices["tec"].set_target_temperature.assert_called_once()
            self.assertEqual(hw.devices["tec"].set_target_temperature.call_args.args[0], 100.0)
            config = yaml.safe_load((staged / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["parameters"]["FIXED_PARAMS.temperature"], 100.0)
            self.assertEqual(config["parameters"]["HF2_OSC_FREQ_HZ"], 90000.0)
        for name in ("waveform_0000.npy", "waveform_0001.npy", "waveform_0002.npy"):
            np.testing.assert_array_equal(np.load(full / "raw" / name), np.load(staged / "raw" / name))
        with np.load(full / "raw/frequency_response.npz") as first, \
                np.load(staged / "raw/frequency_response.npz") as second:
            self.assertEqual(first.files, second.files)
            for name in first.files:
                np.testing.assert_array_equal(first[name], second[name])

    def test_exact_normalization_slopes_and_full_staged_analysis_agree(self):
        full = make_run(self.root)
        freqs = np.array([65000., 75000., 88000., 90000., 92000., 105000., 115000.])
        peaks = np.array([0.94, 0.98, 1.1, 99., 0.9, 0.97, 0.91]) * 1e-6
        np.savez(full / "raw/frequency_response.npz", am_frequencies_hz=freqs,
                 peak_amplitudes=peaks, peak_frequencies_hz=np.abs(freqs - 90000.), actual_rate=450000.)
        staged = full.with_name("0101_1201_staged")
        shutil.copytree(full, staged)
        full_summary = analyze(full)
        analyze(staged, step_ids=["B01"])
        analyze(staged, step_ids=["B02"])
        staged_summary = analyze(staged, step_ids=["B03"])
        self.assertEqual(full_summary["response"], staged_summary["response"])
        with np.load(full / "results/analysis_results.npz") as first, \
                np.load(staged / "results/analysis_results.npz") as second:
            for name in first.files:
                np.testing.assert_array_equal(first[name], second[name])
            self.assertAlmostEqual(float(first["norm_factor"]), 1e-6)
            np.testing.assert_array_equal(first["normalization_reference_am_frequencies_hz"], [88000., 92000.])
            expected = peaks / 1e-6
            expected[3] = np.nan
            np.testing.assert_allclose(first["peak_amplitudes_normalized"], expected)
        slopes = full_summary["response"]["slopes_percent_per_khz"]
        self.assertAlmostEqual(slopes["上边带 (105→115 kHz)"], (0.91 - 0.97) / 0.97 * 10)
        self.assertAlmostEqual(slopes["下边带 (75→65 kHz)"], (0.94 - 0.98) / 0.98 * 10)
        self.assertAlmostEqual(slopes["双边带平均 (15–25 kHz 基带)"], ((0.94 + 0.91) / (0.98 + 0.97) - 1) * 10)

    def test_foreign_experiment_identity_rejected_before_archiving(self):
        run = make_run(self.root)
        (run / "results/keep.txt").write_text("keep", encoding="utf-8")
        self.change_config(run, experiment_id="static-sensitivity", data_type="Static_Sensitivity")
        with self.assertRaises(ValueError):
            analyze(run, step_ids=["B01"])
        self.assertEqual((run / "results/keep.txt").read_text(encoding="utf-8"), "keep")

    def test_unknown_raw_format_rejected_before_archiving(self):
        run = make_run(self.root)
        self.change_config(run, raw_format_version=999)
        with self.assertRaises(ValueError):
            analyze(run, step_ids=["B01"])

    def test_plot_artifact_change_invalidates_only_plot_stage(self):
        run = make_run(self.root)
        analyze(run)
        (run / "results/frequency_response.png").write_bytes(b"damaged plot")
        ctx = AnalysisWorkflow(run)
        self.assertEqual(ctx.states["B01"]["status"], "completed")
        self.assertEqual(ctx.states["B02"]["status"], "completed")
        self.assertEqual(ctx.states["B03"]["status"], "stale")
        analyze(run, step_ids=["B03"])
        self.assertGreater((run / "results/frequency_response.png").stat().st_size, 1000)

    def test_analysis_artifact_change_blocks_plot_and_preserves_input_on_recompute(self):
        run = make_run(self.root)
        analyze(run)
        source = run / "raw/frequency_response.npz"
        before = source.read_bytes()
        with (run / "results/analysis_results.npz").open("ab") as stream:
            stream.write(b"changed")
        ctx = AnalysisWorkflow(run)
        self.assertEqual(ctx.states["B02"]["status"], "stale")
        self.assertEqual(ctx.states["B03"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            ctx.execute(["B03"])
        summary = analyze(run, step_ids=["B02", "B03"])
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue((run / "results" / summary["backup_directory"] / "analysis_results.npz").is_file())

    def test_quality_failure_blocks_followup_and_retains_diagnostic(self):
        run = make_run(self.root, peaks=np.full(41, np.nan))
        with self.assertRaisesRegex(ValueError, "无有效谱峰"):
            analyze(run, step_ids=["B01", "B02", "B03"])
        ctx = AnalysisWorkflow(run)
        self.assertEqual(ctx.summary["status"], "quality_failed")
        self.assertEqual(ctx.states["B02"]["status"], "quality_failed")
        self.assertNotEqual(ctx.states["B03"]["status"], "completed")
        self.assertTrue((run / "results/analysis_summary.json").is_file())
        with self.assertRaisesRegex(ValueError, "缺少前置"):
            ctx.execute(["B03"])

    def test_raw_boundary_rejects_missing_keys_shapes_and_invalid_rate_before_archiving(self):
        cases = [
            dict(am_frequencies_hz=[85000.0], peak_amplitudes=[1e-6], peak_frequencies_hz=[5000.0]),
            dict(am_frequencies_hz=[85000.0, 95000.0], peak_amplitudes=[1e-6],
                 peak_frequencies_hz=[5000.0, 5000.0], actual_rate=12000.0),
            dict(am_frequencies_hz=[85000.0], peak_amplitudes=[1e-6],
                 peak_frequencies_hz=[5000.0], actual_rate=float("nan")),
            dict(am_frequencies_hz=[85000.0], peak_amplitudes=[1e-6],
                 peak_frequencies_hz=[5000.0], actual_rate=[12000.0]),
        ]
        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                root = self.root / str(index)
                run = make_run(root)
                np.savez(run / "raw/frequency_response.npz", **payload)
                (run / "results/keep.txt").write_text("preserve", encoding="utf-8")
                with self.assertRaises(ValueError):
                    analyze(run, step_ids=["B01"])
                self.assertEqual((run / "results/keep.txt").read_text(encoding="utf-8"), "preserve")

    def test_actual_rate_gate_rejects_nonfinite_and_blocks_aom_successor(self):
        for invalid in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(rate=invalid), fake_hardware(self.root) as hw:
                ctx = workflow.Workflow(scan_params(), project_root=self.root)
                ctx.execute([stage.id for stage in ACQUISITION[:4]])
                hw.rates.side_effect = None
                hw.rates.return_value = invalid
                with self.assertRaisesRegex(RuntimeError, "无效采样率"):
                    ctx.execute(["A05", "A06"])
                self.assertEqual(ctx.states["A05"]["status"], "failed")
                self.assertNotEqual(ctx.states["A06"]["status"], "completed")
                hw.devices["aom"].setup_sine.assert_not_called()
                ctx.finish()
