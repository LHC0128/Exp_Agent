"""π 脉宽扫描的 Z 拖尾补偿：波形生成、逐脉宽记录与功耗预检的假仪器回归。

所有用例只用假仪器与临时目录，不连接任何硬件。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ... import common
from ..bperp_null import z_waveform
from ..z_chain_stability import processing
from . import acquisition, preparation
from .models import ARB_POINTS
from .test_workflow import CHANNELS, FakeAcquirer, FakeContext, fast_params

ACQUISITION_CASES = {
    "pi-width-scan": {
        "mode": "grouped",
        "cases": {
            "order": ["TailScanTest::test_scan_uploads_compensated_wave_per_width"],
        },
        "sources": ["lab_workflows/experiment_modules/pi_width_scan/acquisition.py",
                    "lab_workflows/experiment_modules/pi_width_scan/preparation.py"],
    },
}
REGRESSION_CASES = {
    "pi-width-scan": {
        "parameters": {
            "sources": ["lab_workflows/experiment_modules/pi_width_scan/models.py"],
            "tests": ["TailWaveformTest::test_tail_parameters_validated"],
        },
    },
}


class TailWaveformTest(unittest.TestCase):
    def test_off_is_the_original_pulse(self):
        p = fast_params()
        wave, record = preparation.pulse_wave(p, 6.5)
        expected = processing.pulse_waveform(p.pulse_period_us, 6.5, p.z_pulse_edge_us, points=ARB_POINTS)
        np.testing.assert_array_equal(wave, expected)
        self.assertFalse(record["tail_comp"])

    def test_on_matches_bperp_generator_and_exact_tail(self):
        p = fast_params(z_tail_comp_enabled=True)
        wave, record = preparation.pulse_wave(p, 8.2)
        tail_a, tau_c = z_waveform.exact_tail(8.2, 1.0, 0.544, 46.9)
        expected, scale = z_waveform.tail_compensated_waveform(p.pulse_period_us, 8.2, 1.0, tail_a, tau_c, ARB_POINTS)
        np.testing.assert_array_equal(wave, expected)
        self.assertAlmostEqual(record["tail_a"], tail_a)
        self.assertAlmostEqual(record["tau_c_us"], 0.544 * 46.9)
        self.assertAlmostEqual(record["scale"], scale)
        self.assertAlmostEqual(float(np.max(np.abs(wave))), 1.0)
        self.assertLess(record["antisymmetry_error"], 1e-9)
        # 7.93 µs 的精确尾与 10_tail_compensation 的工作波形一致（A = 0.1139，约 −21.6 mA）。
        record_793 = preparation.pulse_wave(p, 7.93)[1]
        self.assertAlmostEqual(record_793["tail_a"], 0.1139, places=3)
        self.assertAlmostEqual(record_793["tail_start_current_ma"], -21.6, delta=0.2)
        # 尾幅随脉宽单调增大。
        amplitudes = [preparation.pulse_wave(p, w)[1]["tail_a"] for w in (4.0, 6.0, 8.0, 10.0, 12.0)]
        self.assertEqual(amplitudes, sorted(amplitudes))

    def test_tail_parameters_validated(self):
        self.assertTrue(any("Z_TAIL_S_F" in e for e in fast_params(z_tail_comp_enabled=True, z_tail_s_f=1.2).validate_model()))
        self.assertTrue(any("Z_TAIL_TAU_E_US" in e
                            for e in fast_params(z_tail_comp_enabled=True, z_tail_tau_e_us=float("nan")).validate_model()))
        # 关闭时不校验模型参数。
        self.assertFalse(any("Z_TAIL" in e for e in fast_params(z_tail_s_f=1.2).validate_model()))


class TailPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(Path(common.__file__).resolve().parents[1] / "params", self.root / "params")

    def test_power_check_uses_waveform_mean_square(self):
        # 额定 0.5 W：矩形占空比与真实波形都在限内（约 0.04 W），mean(v²) = 1 时 0.36 W 超过一半额定。
        p = fast_params(z_tail_comp_enabled=True, z_pulse_width_start_us=4.0, z_pulse_width_stop_us=12.0,
                        resistor_rated_w=0.5)
        preparation.preflight(p, self.root)
        real = preparation.pulse_wave
        seen = []

        def heavy(params, width):
            wave, record = real(params, width)
            seen.append(width)
            return wave, dict(record, mean_square=1.0)

        with patch.object(preparation, "pulse_wave", heavy):
            with self.assertRaisesRegex(ValueError, "电阻平均功耗"):
                preparation.preflight(p, self.root)
        self.assertIn(12.0, seen)
        # 关闭补偿时仍按矩形占空比，不调用波形生成。
        with patch.object(preparation, "pulse_wave", side_effect=AssertionError("不应调用")):
            preparation.preflight(fast_params(), self.root)


class TailScanTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(Path(common.__file__).resolve().parents[1] / "params", self.root / "params")

    def test_scan_uploads_compensated_wave_per_width(self):
        params = fast_params(z_tail_comp_enabled=True)
        ctx = FakeContext(self.root, params)
        signals = ctx.devices["signals"]
        uploaded = []
        original = signals.setup_arbitrary

        def capture(values, freq, amplitude, offset, phase=0.0, channel=None, output=True):
            uploaded.append((channel, np.asarray(values, dtype=float), amplitude))
            return original(values, freq, amplitude, offset, phase=phase, channel=channel, output=output)

        signals.setup_arbitrary = capture
        with patch.object(acquisition.daq, "acquire_data", FakeAcquirer(params, signals)):
            acquisition.measure_scan(ctx)
        widths = params.widths_us()
        self.assertEqual([channel for channel, _, _ in uploaded], [CHANNELS["z"]] * len(widths))
        for (_, values, amplitude), width in zip(uploaded, widths):
            np.testing.assert_array_equal(values, preparation.pulse_wave(params, width)[0])
            self.assertAlmostEqual(amplitude, params.pulse_vpp)
        record = ctx.state["z_waveform"]
        self.assertTrue(record["tail_comp"])
        self.assertAlmostEqual(record["tau_c_us"], params.z_tail_tau_c_us)
        self.assertEqual(sorted(record["widths"]), sorted(f"{w:g}" for w in widths))
        self.assertTrue(all(item["tail_a"] > 0 for item in record["widths"].values()))
        # 读数完成后温控已恢复，扫描表完整。
        self.assertTrue(ctx.directory.signals.temp_on)
        self.assertEqual(ctx.state["scan_points_completed"],
                         len(widths) * params.n_repeat * params.scan_rf_phase_count)


if __name__ == "__main__":
    unittest.main()
