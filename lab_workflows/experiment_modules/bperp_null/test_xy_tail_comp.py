"""X/Y 横向拖尾补偿的假仪器回归：形状、参数、限值预检、任意波布置与逐点只改 offset。

只用假信号源、依赖注入与临时目录，不连接任何硬件。
"""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ... import common
from ..z_chain_stability.processing import pulse_waveform
from . import acquisition, models, preparation, z_waveform
from .test_workflow import _ctx

TAU_E = 46.9


class FakeArbSignals:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.events.append((name, args, kwargs))


class ShapeTest(unittest.TestCase):
    def test_shape_is_lowpass_of_drive(self):
        wave = pulse_waveform(200.0, 6.6, 1.0, points=models.ARB_POINTS)
        shape, peak = z_waveform.slow_shape(wave, 200.0, TAU_E)
        t = np.arange(len(shape)) * 200.0 / len(shape)
        self.assertAlmostEqual(float(np.max(np.abs(shape))), 1.0)
        self.assertGreater(peak, 0.05)
        # 脉冲后驱动为零，x 按 τe 指数衰减
        i20, i50 = np.argmin(abs(t - 20)), np.argmin(abs(t - 50))
        self.assertAlmostEqual(shape[i50] / shape[i20], np.exp(-(t[i50] - t[i20]) / TAU_E), places=4)
        half = len(shape) // 2
        self.assertLess(np.max(np.abs(shape[:half] + shape[half:])), 1e-9)


class ParamsTest(unittest.TestCase):
    def test_amplitudes_and_validation(self):
        self.assertEqual(models.BperpNullParams(xy_tail_x_mv=30.0).xy_tail_amplitudes_v(), {})   # 未开启
        p = models.BperpNullParams(xy_tail_comp_enabled=True, xy_tail_x_mv=30.0)
        self.assertEqual(p.xy_tail_amplitudes_v(), {"x": 0.03})
        self.assertTrue(any("XY_TAIL" in e for e in models.BperpNullParams(xy_tail_comp_enabled=True).validate_model()))

    def test_extremes(self):
        self.assertEqual(preparation.xy_extremes(0.1, 0.0), (0.1,))
        self.assertEqual(preparation.xy_extremes(0.1, -0.03), (0.07, 0.13))

    def test_preflight_rejects_bias_plus_amplitude_over_limit(self):
        root = common.find_project_root()
        limit = common.load_safety_limits(root)["X_magnetic_field"]["max"]
        ok = models.BperpNullParams(xy_tail_comp_enabled=True, xy_tail_x_mv=30.0, x_scan_start_mv=0,
                                    x_scan_stop_mv=0, x_scan_step_mv=1, x_bias_mv=limit * 1000.0 - 50.0)
        bad = models.BperpNullParams(xy_tail_comp_enabled=True, xy_tail_x_mv=80.0, x_scan_start_mv=0,
                                     x_scan_stop_mv=0, x_scan_step_mv=1, x_bias_mv=limit * 1000.0 - 50.0)
        with self.assertRaises(ValueError):
            preparation.preflight(bad, root)
        try:
            preparation.preflight(ok, root)
        except ValueError as exc:                     # 仅允许与本改动无关的映射/能力原因
            self.assertNotIn("X_magnetic_field", str(exc))


class ArmAndScanTest(unittest.TestCase):
    def _ctx(self, tmp, **kw):
        ctx = _ctx(Path(tmp))
        ctx.params = models.BperpNullParams(xy_tail_comp_enabled=True, x_bias_mv=89.1, y_bias_mv=-15.1,
                                            x_scan_start_mv=-10, x_scan_stop_mv=10, x_scan_step_mv=10,
                                            y_scan_start_mv=0, y_scan_stop_mv=0, y_scan_step_mv=1,
                                            n_repeat=1, temp_group_size=1, **kw)
        ctx.devices["signals"] = FakeArbSignals()
        return ctx

    def test_arm_uploads_signed_shape_on_requested_axes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, xy_tail_x_mv=-30.0)
            armed = preparation.arm_xy_tail(ctx)
            self.assertEqual(armed, {"x"})
            ups = [e for e in ctx.devices["signals"].events if e[0] == "setup_arbitrary"]
            self.assertEqual(len(ups), 1)
            wave, freq, vpp, offset = ups[0][1]
            self.assertEqual(ups[0][2]["channel"], ctx.devices["channels"]["x"])
            shape, _ = preparation.xy_tail_shape(ctx.params, ctx.root)
            np.testing.assert_allclose(wave, -shape)
            self.assertAlmostEqual(vpp, 0.06); self.assertAlmostEqual(offset, 0.0891); self.assertEqual(freq, 5000.0)
            names = [e[0] for e in ctx.devices["signals"].events]
            self.assertIn("set_burst_trigger_source", names)
            trig = [e for e in ctx.devices["signals"].events if e[0] == "set_burst_trigger_source"][0]
            self.assertEqual(trig[1][0], "EXTernal")
            self.assertEqual(ctx.state["xy_tail_comp"]["armed"], ["x"])

    def test_scan_changes_only_offset_on_armed_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, xy_tail_x_mv=30.0)
            dc_calls = []

            def fake_acquire(_c, _config, _rate):
                return {"x0": 0.001, "y0": 0.0, "x1": 0.002, "y1": 0.001}

            with patch.object(acquisition, "_acquire_point", fake_acquire), \
                 patch.object(acquisition, "set_temperature_switch", lambda *a, **k: None), \
                 patch.object(acquisition, "configure_fixed_dc_field", lambda dev, ch, key, v: dc_calls.append((key, v)) or v), \
                 patch.object(acquisition.preparation, "arm_pulse", lambda c: c.state.update(z_waveform={"source": "builtin"})), \
                 patch.object(acquisition.preparation, "sleep", lambda _s: None):
                acquisition.measure_grid(ctx)
            offsets = [e for e in ctx.devices["signals"].events if e[0] == "set_offset"]
            self.assertEqual([round(e[1][0], 6) for e in offsets], [0.0791, 0.0891, 0.0991])
            self.assertTrue(all(key == "Y_magnetic_field" for key, _ in dc_calls))   # 只有未叠加补偿的 Y 走直流
            self.assertEqual(len(dc_calls), 3)
            self.assertNotIn("setup_dc", [e[0] for e in ctx.devices["signals"].events])
            with (Path(tmp) / "raw" / "bias_grid.csv").open(encoding="utf-8") as fh:
                self.assertEqual(len(list(csv.DictReader(fh))), 3)

    def test_set_offset_rejects_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp, xy_tail_x_mv=30.0)
            limit = common.load_safety_limits(ctx.root)["X_magnetic_field"]["max"]
            with self.assertRaises(ValueError):
                preparation.set_xy_offset(ctx, "x", limit - 0.01)
            self.assertNotIn("set_offset", [e[0] for e in ctx.devices["signals"].events])


if __name__ == "__main__":
    unittest.main()
