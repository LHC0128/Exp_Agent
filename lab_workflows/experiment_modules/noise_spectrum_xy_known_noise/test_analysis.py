"""真实分离算法、增益模型和分析 DAG 的无硬件回归。"""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import yaml
from sensitivity_analysis.fitting import _dispersive
from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from . import analysis, analysis_steps, calibration_source
from .stages import ANALYSIS
from .test_workflow import copy_known_config


def static_source(root, name="source", *, completed=True, schema_version=3):
    directory = root / "data/Static_Magnetic_Field_Sensitivity" / name
    (directory / "raw").mkdir(parents=True, exist_ok=True)
    config = dict(experiment_id="static-sensitivity", experiment_type="Static_Magnetic_Field_Sensitivity",
        schema_version=schema_version, acquisition_method="dc_step", completion_status="completed",
        parameters=dict(temperature=90, pump_laser_power=.3, probe_laser_power=.1, main_magnetic_field=9.28))
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    v = np.linspace(-.2, .2, 41)
    np.savez(directory / "raw/dispersion_scan.npz", schema_version=schema_version, offsets_v=v,
        completed_mask=np.full(v.size, completed, bool), x_median=np.zeros(v.size),
        y_median=_dispersive(v, .001, .025, .005, .001), r_median=np.ones(v.size),
        sample_counts=np.full(v.size, 100), actual_rate_sa_s=np.full(v.size, 1000), actual_duration_s=np.full(v.size, .1))
    return directory


class KnownAnalysisTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="known-analysis-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_known_config(self.root)
        self.source = static_source(self.root)
        static_source(self.root, "other")
        frequency = np.arange(0., 1000.1, 50.)
        voltage = np.linspace(0., 2., 50)
        control = 1000*voltage
        matrix = np.column_stack([1e-12 + lorentzian_vs_control(control, 80, .005, 0, 0, f) for f in frequency])
        self.ridge = dict(k=1000., b=0., omega_ctrl=control, contrast=np.log(matrix[:, 1:]),
            spur_mask=np.zeros(frequency.size-1, bool), V_cal=voltage, f_cal=control,
            residual_std_hz=0., coverage=1., half_slopes=np.full(2, 1000.))
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(redirect_stdout(StringIO()))
        self.psd = stack.enter_context(patch.object(analysis_steps, "compute_psd_matrix", side_effect=lambda run, cfg, opt, state:
            (matrix*(1.4 if state == "on" else 1), frequency, voltage, np.arange(voltage.size),
             dict(actual_rate_sa_s=10000., nperseg=200, bin_width_hz=50.))))
        stack.enter_context(patch.object(analysis_steps, "locate_ridge", return_value=self.ridge))
        # 绘图在既有回归覆盖；阶段回归专注文件寿命与科学数值。
        for name in ("plot_two_dimensional", "plot_line_shapes", "plot_differential_response", "plot_controlled_uncontrolled",
                     "plot_comparison", "plot_dispersion", "save_known_noise_preview"):
            stack.enter_context(patch.object(analysis_steps, name))

    def directory(self, name, source=""):
        directory = self.root / "data/Noise_Spectrum_XY_Ctrl_Known_Noise" / name
        (directory / "raw").mkdir(parents=True)
        np.save(directory / "raw/waveform_C0000_on.npy", np.arange(20.))
        np.save(directory / "raw/known_noise_waveform.npy", np.random.default_rng(7).normal(size=4096))
        config = dict(experiment_id="noise-spectrum-xy-known-noise", schema_version=3, completion_status="completed",
            parameters={"ANALYSIS_BIN_WIDTH_HZ": 50., "ANALYSIS_FREQUENCY_MIN_HZ": 50., "ANALYSIS_FREQUENCY_MAX_HZ": 1000.,
                "ANALYSIS_FIT_HALF_WIDTH_HZ": 400., "ANALYSIS_DISPERSION_SOURCE_RUN": source, "FIXED_PARAMS.temperature": 100.},
            known_noise=dict(waveform_file="raw/known_noise_waveform.npy", amplitude_vpp=.01, sample_rate_sa_s=10000.,
                bands=[dict(start_hz=100, stop_hz=800)]),
            truth_chain=dict(z_calibration_run="0920_183423_bb_z_cal", K_Z_Hz_per_V=11703.6778))
        (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        return directory

    def test_waiting_then_gain_only_source_switch_and_archive(self):
        run = self.directory("waiting")
        self.assertEqual(analysis.analyze(run)["status"], "waiting_calibration")
        before = {name: (run / "results" / name).read_bytes() for name in ("psd_matrices.npz", "calibration.npz", "separated_spectra.npz")}
        ctx = analysis.AnalysisWorkflow(run, {"ANALYSIS_DISPERSION_SOURCE_RUN": "source"})
        self.assertEqual(ctx.states["B04"]["status"], "completed")
        ctx.execute(["B05", "B06"])
        self.assertEqual(ctx.summary["status"], "completed")
        self.assertTrue(ctx.summary["dispersion"]["condition_differences"])
        first_gain = (run / "results/dispersion_source.json").read_bytes()
        ctx = analysis.AnalysisWorkflow(run, {"ANALYSIS_DISPERSION_SOURCE_RUN": "other"})
        self.assertEqual([key for key, state in ctx.states.items() if state["status"] == "stale"], ["B05", "B06"])
        ctx.execute(["B05", "B06"])
        for name, data in before.items():
            self.assertEqual((run / "results" / name).read_bytes(), data)
            self.assertFalse(list((run / "results").glob("before_*/"+name)))
        self.assertIn(first_gain, [p.read_bytes() for p in (run / "results").glob("before_*/dispersion_source.json")])
        self.assertEqual(self.psd.call_count, 2)

    def test_full_and_staged_outputs_are_equal_and_b05_is_independent(self):
        full, stepped = self.directory("full", "source"), self.directory("stepped", "source")
        expected = analysis.analyze(full)
        for item in ("B01", "B05", "B02", "B03", "B04", "B06"):
            ctx = analysis.AnalysisWorkflow(stepped)
            ctx.execute([item])
        self.assertEqual(analysis._json_safe(expected["comparison"]), analysis._json_safe(ctx.summary["comparison"]))
        for name in ("psd_matrices.npz", "separated_spectra.npz", "noise_spectra.npz"):
            with np.load(full / "results" / name, allow_pickle=False) as a, np.load(stepped / "results" / name, allow_pickle=False) as b:
                self.assertEqual(a.files, b.files)
                for key in a.files:
                    np.testing.assert_array_equal(a[key], b[key])

    def test_invalid_sources_rejected_without_writing_source(self):
        original = (self.source / "experiment_config.yaml").read_bytes()
        static_source(self.root, "v2", schema_version=2)
        static_source(self.root, "incomplete", completed=False)
        static_source(self.root, "running")
        cfg_path = self.root / "data/Static_Magnetic_Field_Sensitivity/running/experiment_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        cfg["completion_status"] = "running"
        cfg_path.write_text(yaml.safe_dump(cfg))
        static_source(self.root, "bad_fit")
        raw = self.root / "data/Static_Magnetic_Field_Sensitivity/bad_fit/raw/dispersion_scan.npz"
        with np.load(raw) as data:
            values = {key: data[key] for key in data.files}
        values["y_median"] = np.ones(41)
        np.savez(raw, **values)
        for source in ("../source", "missing", "v2", "incomplete", "running", "bad_fit"):
            with self.subTest(source=source), self.assertRaises((ValueError, FileNotFoundError)):
                calibration_source.load_source(self.root, source, 11703.6778)
        fit = calibration_source.load_source(self.root, "source", 11703.6778)
        self.assertAlmostEqual(fit["slope_v_per_hz"], .001/.025**2/11703.6778, places=10)
        self.assertEqual((self.source / "experiment_config.yaml").read_bytes(), original)
        self.assertFalse((self.source / "results").exists())

    def test_legacy_internal_dispersion_and_fingerprints(self):
        run = self.directory("legacy")
        cfg_path = run / "experiment_config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        cfg.update(schema_version=2, dispersion=dict(slope_v_per_hz=1.))
        cfg_path.write_text(yaml.safe_dump(cfg))
        with np.load(self.source / "raw/dispersion_scan.npz") as data:
            values = {key: data[key] for key in data.files}
        np.savez(run / "raw/dispersion_scan.npz", **values, k_z_hz_per_v=11703.6778)
        self.assertEqual(analysis.analyze(run)["status"], "completed")
        raw = run / "raw/waveform_C0000_on.npy"
        np.save(raw, np.arange(21.))
        with self.assertRaisesRegex(ValueError, "B01"):
            analysis.AnalysisWorkflow(run).execute(["B02"])


if __name__ == "__main__":
    unittest.main()
