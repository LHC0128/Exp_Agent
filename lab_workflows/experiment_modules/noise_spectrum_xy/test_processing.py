"""XY 噪声谱低频边界分离回归。"""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import yaml

from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from . import analysis
from .processing import fit_local_spectra


class NoiseSpectrumXYProcessingTest(TestCase):
    def test_low_frequency_boundary_uses_fixed_shape_without_extrapolation(self):
        control = np.linspace(20.0, 10000.0, 400)
        frequency = np.arange(0.0, 3000.1, 50.0)
        gamma = 80.0
        background = 1.0e-12
        matrix = np.empty((control.size, frequency.size))
        for index, fixed_frequency in enumerate(frequency):
            matrix[:, index] = background + lorentzian_vs_control(
                control,
                gamma,
                5.0e-3,
                0.0,
                0.0,
                fixed_frequency,
            )
        rng = np.random.default_rng(7)
        matrix *= np.exp(rng.normal(0.0, 0.01, matrix.shape))

        with patch("lab_workflows.experiment_modules.noise_spectrum_xy.processing.check_cancelled"):
            result = fit_local_spectra(
                matrix,
                frequency,
                control,
                np.zeros(frequency.size, dtype=bool),
                300.0,
                (500.0, 3000.0),
                full_range=True,
                allow_boundary_fits=True,
            )

        self.assertTrue(result["fit_mask"][0])
        self.assertTrue(result["background_fit_mask"][0])
        self.assertTrue(result["edge_fit_mask"][0])
        self.assertEqual(result["fit_mode"][0], "edge_fixed_shape")
        self.assertTrue(np.isfinite(result["S_beta"][0]))
        self.assertTrue(np.isfinite(result["N_S1"][0]))
        self.assertGreater(int(result["edge_fit_mask"].sum()), 1)

    def test_analysis_excludes_dc_without_edge_plot_markers(self):
        control = np.linspace(20.0, 10000.0, 400)
        frequency = np.arange(0.0, 3000.1, 50.0)
        matrix = np.column_stack([
            lorentzian_vs_control(control, 80.0, 5e-3, 1e-12, 0.0, f)
            for f in frequency
        ])
        ridge = dict(
            k=1.0, b=0.0, omega_ctrl=control, contrast=np.log(matrix[:, 1:]),
            spur_mask=np.zeros(frequency.size - 1, bool),
            V_cal=np.array([500.0, 3000.0]), f_cal=np.array([500.0, 3000.0]),
            residual_std_hz=0.0, coverage=1.0, half_slopes=np.ones(2),
        )
        figures = {}

        def capture_plot(fig, path):
            from matplotlib import pyplot as plt
            figures[path.name] = [
                (line.get_label(), line.get_marker())
                for ax in fig.axes for line in ax.lines
            ]
            plt.close(fig)

        with tempfile.TemporaryDirectory(prefix="xy-analysis-dc-") as directory:
            run_dir = Path(directory)
            (run_dir / "experiment_config.yaml").write_text(yaml.safe_dump({
                "parameters": {
                    "ANALYSIS_FREQUENCY_MIN_HZ": 0.0,
                    "ANALYSIS_FREQUENCY_MAX_HZ": 3000.0,
                    "ANALYSIS_FIT_HALF_WIDTH_HZ": 300.0,
                },
            }), encoding="utf-8")
            with patch.object(analysis, "compute_psd", return_value=(
                matrix, frequency, control, np.arange(control.size),
                dict(actual_rate_sa_s=10000.0, nperseg=200, bin_width_hz=50.0),
            )), patch.object(analysis, "locate_ridge", return_value=ridge), \
                    patch.object(analysis, "save_plot", side_effect=capture_plot), \
                    redirect_stdout(StringIO()):
                summary = analysis.analyze(run_dir)

            self.assertEqual(summary["status"], "completed")
            with np.load(run_dir / "results" / "noise_spectra.npz") as spectra:
                np.testing.assert_array_equal(spectra["freq_axis"], frequency[1:])
                self.assertTrue(spectra["edge_fit_mask"].any())
                self.assertIn("fit_mode", spectra.files)
            with np.load(run_dir / "results" / "psd_matrix.npz") as raw_psd:
                self.assertEqual(raw_psd["freq_axis"][0], 0.0)
            csv = np.loadtxt(run_dir / "results" / "noise_spectra.csv",
                             delimiter=",", skiprows=1)
            np.testing.assert_array_equal(csv[:, 0], frequency[1:])

        for name in ("noise_spectra_extracted.png", "background_identifiability.png"):
            self.assertTrue(figures[name])
            for label, marker in figures[name]:
                self.assertNotIn("edge", label.lower())
                self.assertNotIn(marker, ("^", "v"))
