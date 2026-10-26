"""Probe AM 分阶段分析、暂定控制轴与归档的无硬件回归。"""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pytest
import yaml
from scipy.signal import welch

from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from . import analysis, analysis_steps, stages
from .test_workflow import copy_config, scan_params


REGRESSION_CASES = {
    "noise-spectrum-xy-uncontrolled-probe-am": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/__init__.py",
                "lab_workflows/experiment_modules/noise_spectrum_xy_probe_am/analysis.py"
            ],
            "tests": [
                "ProbeAMAnalysisTests::test_full_and_staged_outputs_are_equal",
                "ProbeAMAnalysisTests::test_provisional_axis_completes_with_quality_failed",
                "ProbeAMAnalysisTests::test_option_change_invalidates_fit_stage_and_archives",
                "ProbeAMAnalysisTests::test_invalid_options_rejected_and_fingerprint_invalidates",
                "ProbeAMRawAnalysisTests::test_real_raw_full_and_staged_science_products_match",
                "ProbeAMRawAnalysisTests::test_real_missing_pairs_keep_nan_and_block_insufficient_data",
                "test_real_module_outside_repository_uses_explicit_run"
            ]
        }
    }
}


def paired_matrix(scale_on):
    frequency = np.arange(0., 1000.1, 50.)
    voltage = np.linspace(0., 2., 50)
    control = 1000 * voltage
    base = np.column_stack([1e-6 * (1 + lorentzian_vs_control(control, 80, .005, 0, 0, f)) for f in frequency])
    return base * (1.4 if scale_on else 1), frequency, voltage


def synthetic_run(root, name):
    """生成可回读的合成原始波形；分析算法与绘图保持真实执行。"""
    directory = Path(root) / "data/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM" / name
    raw = directory / "raw"
    raw.mkdir(parents=True)
    rate, samples = 20000.0, 16384
    voltage = np.linspace(0, 1, 60)
    model_frequency = np.arange(0., rate / 2 + 1, 50.)
    model = np.column_stack([lorentzian_vs_control(10000 * voltage, 250, 2.5, 1e-6, 0, f)
                             for f in model_frequency])
    frequency = np.fft.rfftfreq(samples, 1 / rate)
    rng = np.random.default_rng(20261014)
    for index in range(voltage.size):
        target = np.interp(frequency, model_frequency, model[index])
        spectrum = np.sqrt(rate * samples * target / 2) * np.exp(2j * np.pi * rng.random(frequency.size))
        spectrum[[0, -1]] = 0
        for state, scale in (("off", np.ones(frequency.size)), ("on", np.where(frequency < 4000, .7, 1.4))):
            path = raw / f"waveform_C{index:04d}_{state}.npy"
            np.save(path, np.fft.irfft(spectrum * np.sqrt(scale), n=samples))
            np.savez(path.with_name(path.stem + "_acquisition.npz"), actual_rate_Sa_s=rate)
    noise = rng.uniform(-1, 1, 4096)
    np.save(raw / "probe_am_noise_waveform.npy", noise)
    parameters = scan_params().to_external() | {
        "ANALYSIS_BIN_WIDTH_HZ": 100., "ANALYSIS_FREQUENCY_MIN_HZ": 500.,
        "ANALYSIS_FREQUENCY_MAX_HZ": 9000., "ANALYSIS_FIT_HALF_WIDTH_HZ": 2000.,
        "TARGET_NOISE_FREQ_STOP_HZ": 9000., "XY_CTRL_K_HZ_PER_V": 10000., "XY_CTRL_B_HZ": 0.,
    }
    config = dict(experiment_id=stages.EXPERIMENT_ID, schema_version=2, completion_status="completed",
        parameters=parameters, scan_params={"xy_envelope_voltage_V": voltage.tolist()},
        hf2_daq={"actual_rate_Sa_s": 18000., "DAQ_duration_s": samples / rate},
        probe_am_noise=dict(waveform_file="raw/probe_am_noise_waveform.npy", amplitude_vpp=.01,
            sample_rate_sa_s=rate, design_peak_v=1., rms_v=float(np.std(noise)),
            bands=[dict(start_hz=1000, stop_hz=8000, design_psd_v2_per_hz=1e-6)]))
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return directory


class ProbeAMAnalysisTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="probe-am-analysis-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_config(self.root)
        matrix, frequency, voltage = paired_matrix(False)
        self.ridge = dict(k=1000., b=0., omega_ctrl=1000 * voltage, contrast=np.log(matrix[:, 1:]),
                          spur_mask=np.zeros(frequency.size - 1, bool), V_cal=voltage, f_cal=1000 * voltage,
                          residual_std_hz=0., coverage=1., half_slopes=np.full(2, 1000.))
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(redirect_stdout(StringIO()))
        self.psd = stack.enter_context(patch.object(analysis_steps, "compute_psd_matrix",
            side_effect=lambda run, cfg, opt, state: (*paired_matrix(state == "on"),
             np.arange(voltage.size), dict(actual_rate_sa_s=10000., nperseg=200, bin_width_hz=50.))))
        self.locate = stack.enter_context(patch.object(analysis_steps, "locate_ridge", return_value=self.ridge))
        for name in ("plot_two_dimensional", "plot_line_shapes", "plot_controlled_uncontrolled",
                     "save_known_noise_preview", "_plot_signed_difference"):
            stack.enter_context(patch.object(analysis_steps, name))

    def directory(self, name):
        directory = self.root / "data/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM" / name
        (directory / "raw").mkdir(parents=True)
        np.save(directory / "raw/waveform_C0000_on.npy", np.arange(20.))
        np.save(directory / "raw/waveform_C0000_off.npy", np.arange(20.))
        np.save(directory / "raw/probe_am_noise_waveform.npy", np.random.default_rng(7).normal(size=4096))
        config = dict(experiment_id="noise-spectrum-xy-uncontrolled-probe-am", schema_version=2,
            completion_status="completed",
            parameters=dict(scan_params().to_external() | {"ANALYSIS_BIN_WIDTH_HZ": 50.,
                "ANALYSIS_FREQUENCY_MIN_HZ": 50., "ANALYSIS_FREQUENCY_MAX_HZ": 1000.,
                "ANALYSIS_FIT_HALF_WIDTH_HZ": 400.}),
            probe_am_noise=dict(waveform_file="raw/probe_am_noise_waveform.npy", amplitude_vpp=.01,
                sample_rate_sa_s=10000., bands=[dict(start_hz=100, stop_hz=800)]))
        (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        return directory

    def test_full_and_staged_outputs_are_equal(self):
        full, stepped = self.directory("full"), self.directory("stepped")
        expected = analysis.analyze(full)
        self.assertEqual(expected["status"], "completed")
        for item in ("B01", "B02", "B03", "B04"):
            analysis.AnalysisWorkflow(stepped).execute([item])
        ctx = analysis.AnalysisWorkflow(stepped)
        self.assertEqual(ctx.summary["status"], "completed")
        self.assertEqual(analysis._json_safe(expected["fit_quality"]), analysis._json_safe(ctx.summary["fit_quality"]))
        for name in ("psd_matrices.npz", "calibration.npz", "popt_fit.npz", "noise_spectra.npz"):
            with np.load(full / "results" / name, allow_pickle=False) as a, \
                 np.load(stepped / "results" / name, allow_pickle=False) as b:
                self.assertEqual(a.files, b.files)
                for key in a.files:
                    np.testing.assert_array_equal(a[key], b[key])

    def test_provisional_axis_completes_with_quality_failed(self):
        run = self.directory("provisional")
        self.locate.side_effect = ValueError("无足够显著的移动峰，拒绝生成控制轴")
        summary = analysis.analyze(run)
        self.assertEqual(summary["status"], "quality_failed")
        self.assertIn("暂定控制轴", json.dumps(summary, ensure_ascii=False))
        ctx = analysis.AnalysisWorkflow(run)
        self.assertEqual(ctx.summary["calibration"]["source"], "configured_K_B")
        self.assertEqual(ctx.states["B03"]["status"], "completed")
        self.assertEqual(ctx.states["B04"]["status"], "completed")

    def test_option_change_invalidates_fit_stage_and_archives(self):
        run = self.directory("options")
        analysis.analyze(run)
        ctx = analysis.AnalysisWorkflow(run, {"ANALYSIS_FIT_HALF_WIDTH_HZ": 300.})
        self.assertEqual(ctx.states["B04"]["status"], "stale")
        self.assertEqual(ctx.states["B03"]["status"], "completed")
        ctx.execute(["B04"])
        self.assertTrue(list((run / "results").glob("before_*/noise_spectra.npz")))
        self.assertEqual(analysis.AnalysisWorkflow(run).summary["status"], "completed")

    def test_invalid_options_rejected_and_fingerprint_invalidates(self):
        run = self.directory("invalid")
        with self.assertRaisesRegex(ValueError, "分析参数"):
            analysis.AnalysisWorkflow(run, {"ANALYSIS_FIT_HALF_WIDTH_HZ": 0.0})
        analysis.analyze(run)
        np.save(run / "raw/waveform_C0000_off.npy", np.arange(21.))
        ctx = analysis.AnalysisWorkflow(run)
        self.assertEqual(ctx.states["B01"]["status"], "stale")
        self.assertEqual(ctx.states["B04"]["status"], "stale")
        with self.assertRaisesRegex(ValueError, "B01"):
            ctx.execute(["B02"])


class ProbeAMRawAnalysisTests(unittest.TestCase):
    def test_real_raw_full_and_staged_science_products_match(self):
        with tempfile.TemporaryDirectory(prefix="probe-am-science-") as tmp:
            root = Path(tmp)
            copy_config(root)
            full = synthetic_run(root, "full")
            stepped = full.with_name("stepped")
            shutil.copytree(full, stepped)
            expected = analysis.analyze(full)
            for stage in stages.ANALYSIS:
                analysis.AnalysisWorkflow(stepped).execute([stage.id])
            actual = analysis.AnalysisWorkflow(stepped)
            self.assertEqual(expected["status"], "completed")
            self.assertEqual(actual.summary["fit_quality"], expected["fit_quality"])
            self.assertEqual(expected["calibration"]["source"], "moving_peaks")
            self.assertAlmostEqual(expected["calibration"]["k"], 10000., delta=200.)
            self.assertEqual(expected["psd"]["actual_rate_sa_s"], 20000.)
            self.assertGreater(expected["fit_quality"]["valid_background_bins"], 20)
            self.assertGreater(expected["fit_quality"]["valid_controlled_bins"], 20)
            self.assertGreater(expected["fit_quality"]["negative_delta_bins"], 0)
            self.assertGreater(expected["fit_quality"]["positive_delta_bins"], 0)
            for stage in stages.ANALYSIS:
                self.assertEqual(actual.states[stage.id]["status"], "completed")
                for name in stage.artifacts:
                    first, second = full / "results" / name, stepped / "results" / name
                    self.assertTrue(first.is_file(), name)
                    self.assertTrue(second.is_file(), name)
                    if name.endswith(".npz"):
                        with np.load(first, allow_pickle=False) as a, np.load(second, allow_pickle=False) as b:
                            self.assertEqual(a.files, b.files)
                            for key in a.files:
                                np.testing.assert_array_equal(a[key], b[key])
                    if name.endswith(".png"):
                        self.assertEqual(first.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                        self.assertGreater(first.stat().st_size, 1000)
                    if name.endswith(".pdf"):
                        self.assertTrue(first.read_bytes().startswith(b"%PDF-"))
            wave = np.load(full / "raw/waveform_C0000_off.npy")
            f, reference = welch(wave, fs=20000., nperseg=200, noverlap=100,
                                 window="hann", detrend="constant", scaling="density")
            with np.load(full / "results/psd_matrices.npz") as data:
                np.testing.assert_array_equal(data["freq_axis"], f)
                np.testing.assert_array_equal(data["psd_off"][0], reference)
            with np.load(full / "results/noise_spectra.npz") as data:
                valid = data["valid_mask"]
                np.testing.assert_array_equal(data["delta_N_S1"][valid], (data["N_S1_on"] - data["N_S1_off"])[valid])
                self.assertTrue(np.isnan(data["delta_N_S1"][~valid]).all())
                csv = np.loadtxt(full / "results/noise_spectra.csv", delimiter=",", skiprows=1)
                np.testing.assert_array_equal(csv[:, 6], data["delta_N_S1"])
            retained = {name: (stepped / "results" / name).read_bytes()
                        for name in ("psd_matrices.npz", "calibration.npz")}
            changed = analysis.AnalysisWorkflow(stepped, {"ANALYSIS_FIT_HALF_WIDTH_HZ": 1800.})
            self.assertEqual(changed.states["B03"]["status"], "completed")
            self.assertEqual(changed.states["B04"]["status"], "stale")
            changed.execute(["B04"])
            backup = stepped / "results" / changed.summary["backup_directory"]
            self.assertTrue((backup / "noise_spectra.npz").is_file())
            for name, content in retained.items():
                self.assertEqual((stepped / "results" / name).read_bytes(), content)
                self.assertFalse((backup / name).exists())
            calibration = stepped / "results/calibration.npz"
            calibration.write_bytes(calibration.read_bytes() + b"changed")
            stale = analysis.AnalysisWorkflow(stepped)
            self.assertEqual(stale.states["B02"]["status"], "completed")
            self.assertEqual(stale.states["B03"]["status"], "stale")
            with self.assertRaisesRegex(ValueError, "B03"):
                stale.execute(["B04"])
            (stepped / "raw/diagnostics").mkdir()
            (stepped / "raw/diagnostics/new_record.json").write_text("{}", encoding="utf-8")
            stale = analysis.AnalysisWorkflow(stepped)
            self.assertEqual(stale.states["B01"]["status"], "stale")
            with self.assertRaisesRegex(ValueError, "B01"):
                stale.execute(["B02"])

    def test_real_missing_pairs_keep_nan_and_block_insufficient_data(self):
        with tempfile.TemporaryDirectory(prefix="probe-am-pairs-") as tmp:
            root = Path(tmp)
            copy_config(root)
            run = synthetic_run(root, "incomplete")
            (run / "raw/waveform_C0000_on.npy").unlink()
            np.save(run / "raw/waveform_C0001_off.npy", np.full(16384, np.nan))
            ctx = analysis.AnalysisWorkflow(run)
            ctx.execute(["B01", "B02"])
            with np.load(run / "results/psd_matrices.npz") as data:
                self.assertTrue(np.isnan(data["psd_on"][0]).all())
                self.assertTrue(np.isnan(data["psd_off"][1]).all())
                self.assertNotIn(0, data["paired_rows"])
                self.assertNotIn(1, data["paired_rows"])
                self.assertEqual(data["paired_rows"].size, 58)
            for path in (run / "raw").glob("waveform_C*_on.npy"):
                if int(path.stem.split("_")[1][1:]) >= 20:
                    path.unlink()
            ctx = analysis.AnalysisWorkflow(run)
            with self.assertRaisesRegex(ValueError, "成对有效控制点不足"):
                ctx.execute(["B01", "B02", "B03", "B04"])
            self.assertEqual(ctx.states["B02"]["status"], "quality_failed")
            self.assertNotEqual(ctx.states["B03"]["status"], "completed")
            saved = json.loads((run / "results/analysis_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "quality_failed")
            self.assertIn("成对有效控制点不足", saved["error"])


@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_module_outside_repository_uses_explicit_run(tmp_path, mode):
    from lab_workflows.experiments.test_analysis_imports import check_real_analysis_module

    copy_config(tmp_path)
    run = synthetic_run(tmp_path, "module")
    check_real_analysis_module(run, __package__ + ".analysis", "noise_spectra.npz", mode)


if __name__ == "__main__":
    unittest.main()
