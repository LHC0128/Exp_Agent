"""Probe AM 分阶段分析、暂定控制轴与归档的无硬件回归。"""
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
from . import analysis, analysis_steps
from .test_workflow import copy_config, scan_params


def paired_matrix(scale_on):
    frequency = np.arange(0., 1000.1, 50.)
    voltage = np.linspace(0., 2., 50)
    control = 1000 * voltage
    base = np.column_stack([1e-6 * (1 + lorentzian_vs_control(control, 80, .005, 0, 0, f)) for f in frequency])
    return base * (1.4 if scale_on else 1), frequency, voltage


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


if __name__ == "__main__":
    unittest.main()
