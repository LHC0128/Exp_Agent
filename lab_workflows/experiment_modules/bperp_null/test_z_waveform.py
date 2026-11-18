"""Z 波形文件的假仪器回归：生成（拖尾补偿精确性）、文件边界校验、预检/上传与原样留档。

只用合成数据、临时目录和假信号源，不连接任何硬件。
"""
from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ... import common
from ..z_chain_stability.processing import pulse_waveform
from . import acquisition, models, preparation, z_waveform
from .test_workflow import _ctx

PERIOD, WIDTH, EDGE = 200.0, 7.93, 1.0
S_F, TAU_E = 0.544, 46.9


def _write(root, name="tail.csv", **overrides):
    wave, scale = z_waveform.tail_compensated_waveform(PERIOD, WIDTH, EDGE, *z_waveform.exact_tail(WIDTH, EDGE, S_F, TAU_E),
                                                       points=models.ARB_POINTS)
    header = {"period_us": PERIOD, "width_us": WIDTH, "edge_us": EDGE}
    header.update(overrides.pop("header", {}))
    values = overrides.pop("values", wave)
    return z_waveform.write_waveform_file(Path(root) / z_waveform.WAVEFORM_DIR / name, values, header)


class GeneratorTest(unittest.TestCase):
    def test_exact_tail_zeroes_field_after_pulse(self):
        A, tau_c = z_waveform.exact_tail(WIDTH, EDGE, S_F, TAU_E)
        self.assertAlmostEqual(tau_c, S_F * TAU_E)
        n = models.ARB_POINTS
        t = np.arange(n) * PERIOD / n
        k = np.fft.rfftfreq(n, PERIOD / n) * 2 * np.pi

        def field(wave):
            slow = np.fft.irfft(np.fft.rfft(wave) / (1 + 1j * k * TAU_E), n)
            return S_F * wave + (1 - S_F) * slow

        after = (t > WIDTH + 0.5) & (t < PERIOD / 2 - 0.5)
        comp, scale = z_waveform.tail_compensated_waveform(PERIOD, WIDTH, EDGE, A, tau_c, points=n)
        plain = pulse_waveform(PERIOD, WIDTH, EDGE, points=n)
        residual = np.max(np.abs(field(comp * scale)[after]))
        uncompensated = np.max(np.abs(field(plain)[after]))
        self.assertGreater(uncompensated, 0.03)
        self.assertLess(residual, 1e-3 * uncompensated * 10)
        self.assertAlmostEqual(float(np.max(np.abs(comp))), 1.0)
        self.assertLess(np.max(np.abs(comp[: n // 2] + comp[n // 2:])), 1e-12)

    def test_zero_tail_equals_builtin_pulse(self):
        wave, scale = z_waveform.tail_compensated_waveform(PERIOD, 6.6, EDGE, 0.0, 25.5, points=models.ARB_POINTS)
        self.assertEqual(scale, 1.0)
        np.testing.assert_allclose(wave, pulse_waveform(PERIOD, 6.6, EDGE, points=models.ARB_POINTS))


class LoadTest(unittest.TestCase):
    def _load(self, path, period=PERIOD):
        return z_waveform.load_z_waveform(path, points=models.ARB_POINTS, period_us=period)

    def test_valid_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp)
            loaded = self._load(path)
            self.assertEqual(loaded.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(loaded.header["width_us"], f"{WIDTH}")
            self.assertAlmostEqual(float(np.max(np.abs(loaded.values))), 1.0, places=7)
            record = loaded.record()
            self.assertEqual(record["source"], "file")
            self.assertLess(record["antisymmetry_error"], 1e-6)

    def test_rejects_bad_files(self):
        n = models.ARB_POINTS
        good = pulse_waveform(PERIOD, WIDTH, EDGE, points=n)
        cases = {
            "points": dict(values=good[:-1]),
            "nan": dict(values=np.where(np.arange(n) == 5, np.nan, good)),
            "peak": dict(values=0.9 * good),
            "over": dict(values=np.where(np.arange(n) == 5, 1.2, good)),
            "jump": dict(values=np.where(np.arange(n) == n - 1, 0.5, good)),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for label, kw in cases.items():
                with self.subTest(label):
                    with self.assertRaises(ValueError):
                        self._load(_write(tmp, f"{label}.csv", **kw))
            path = _write(tmp, "noperiod.csv", header={})
            text = path.read_text(encoding="utf-8").replace("# period_us: 200.0\n", "")
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "period_us"):
                self._load(path)
            with self.assertRaisesRegex(ValueError, "周期"):
                self._load(_write(tmp, "ok.csv"), period=100.0)

    def test_rejects_paths_outside_waveform_dir(self):
        for name in ("../escape.csv", "sub/x.csv", "x.txt", "", "C:/abs.csv"):
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    z_waveform.waveform_path("root", name)


class ParamsAndPreflightTest(unittest.TestCase):
    def test_file_required_when_enabled(self):
        params = models.BperpNullParams(z_waveform_from_file=True)
        self.assertTrue(any("Z_WAVEFORM_FILE" in item for item in params.validate_model()))
        self.assertFalse(any("Z_WAVEFORM" in item for item in models.BperpNullParams().validate_model()))

    def test_schema_lists_files_and_gates_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "a.csv")
            fields = {f["name"]: f for f in models.BperpNullParams().schema(Path(tmp))["fields"]}
            self.assertEqual([o["value"] for o in fields["Z_WAVEFORM_FILE"]["options"]], ["a.csv"])
            off = models.BperpNullParams(z_waveform_file="missing.csv")
            self.assertFalse(any("Z_WAVEFORM_FILE" in e for e in off.validate(Path(tmp))))
            on = models.BperpNullParams(z_waveform_from_file=True, z_waveform_file="missing.csv")
            self.assertTrue(any("Z_WAVEFORM_FILE" in e for e in on.validate(Path(tmp))))

    def test_z_wave_checks_width_and_reports_power(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp)
            params = models.BperpNullParams(z_waveform_from_file=True, z_waveform_file="tail.csv",
                                            z_pulse_width_us=WIDTH)
            wave, record = preparation.z_wave(params, tmp)
            self.assertEqual(record["file"], "tail.csv")
            expected = (params.z_peak_current_ma * 1e-3) ** 2 * float(np.mean(wave ** 2)) * 10.0
            self.assertAlmostEqual(preparation.resistor_power_w(params, record["mean_square"]), expected)
            with self.assertRaisesRegex(ValueError, "width_us"):
                preparation.z_wave(models.BperpNullParams(z_waveform_from_file=True, z_waveform_file="tail.csv",
                                                          z_pulse_width_us=6.6), tmp)

    def test_builtin_power_rule_unchanged(self):
        params = models.BperpNullParams()
        _wave, record = preparation.z_wave(params, "unused")
        self.assertEqual(record["source"], "builtin")
        duty = 2 * params.z_pulse_width_us / params.pulse_period_us
        self.assertGreaterEqual(duty, record["mean_square"])


class FakeArbSignals:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.events.append((name, args, kwargs))


class UploadAndRecordTest(unittest.TestCase):
    def test_arm_pulse_uploads_file_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp)
            ctx = _ctx(Path(tmp) / "run")
            ctx.root = Path(tmp)
            ctx.params = models.BperpNullParams(z_waveform_from_file=True, z_waveform_file="tail.csv",
                                                z_pulse_width_us=WIDTH)
            ctx.devices["signals"] = FakeArbSignals()
            limits = common.load_safety_limits(common.find_project_root())
            with patch.object(preparation, "load_safety_limits", lambda _root: limits), \
                 patch.object(preparation, "set_amp_output", lambda _ctx, _on: None):
                preparation.arm_pulse(ctx)
            uploads = [e for e in ctx.devices["signals"].events if e[0] == "setup_arbitrary"]
            self.assertEqual(len(uploads), 1)
            np.testing.assert_allclose(uploads[0][1][0], z_waveform.load_z_waveform(
                path, points=models.ARB_POINTS, period_us=PERIOD).values)
            self.assertAlmostEqual(uploads[0][1][2], ctx.params.pulse_vpp)
            self.assertEqual(ctx.state["z_waveform"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIn("tail.csv", ctx.outputs["Z 通道"])

    def _measure(self, tmp, tamper=False):
        root = Path(tmp)
        path = _write(root)
        ctx = _ctx(root / "run")
        ctx.root = root
        ctx.params = models.BperpNullParams(x_scan_start_mv=0, x_scan_stop_mv=0, x_scan_step_mv=1,
                                            y_scan_start_mv=0, y_scan_stop_mv=0, y_scan_step_mv=1,
                                            n_repeat=2, temp_group_size=2, z_waveform_from_file=True,
                                            z_waveform_file="tail.csv", z_pulse_width_us=WIDTH)
        events = ctx.devices["signals"].events

        def fake_arm(c):
            c.state["z_waveform"] = preparation.z_wave(c.params, c.root)[1]
            if tamper:
                path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")

        def fake_acquire(_c, _config, _rate):
            return {"x0": 0.001, "y0": 0.0, "x1": 0.002, "y1": 0.001}

        limits = common.load_safety_limits(common.find_project_root())
        with patch.object(acquisition, "_acquire_point", fake_acquire), \
             patch.object(acquisition, "load_safety_limits", lambda _root: limits), \
             patch.object(acquisition, "set_temperature_switch", lambda s, on, channel=None: events.append(("temp", on))), \
             patch.object(acquisition, "configure_fixed_dc_field", lambda *args: args[-1]), \
             patch.object(acquisition.preparation, "arm_pulse", fake_arm), \
\
             patch.object(acquisition.preparation, "sleep", lambda _s: None):
            acquisition.measure_grid(ctx)
        return ctx, path

    def test_waveform_copied_into_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx, path = self._measure(tmp)
            copy = Path(tmp) / "run" / "raw" / "z_waveform.csv"
            self.assertEqual(copy.read_bytes(), path.read_bytes())
            self.assertIn("raw/z_waveform.csv", ctx.state["data_files"])
            with (Path(tmp) / "run" / "raw" / "bias_grid.csv").open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)

    def test_file_changed_after_upload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "上传后被改动"):
                self._measure(tmp, tamper=True)
            self.assertFalse((Path(tmp) / "run" / "raw" / "z_waveform.csv").exists())


class SessionSnapshotTest(unittest.TestCase):
    def test_snapshot_fits_gui_session_model(self):
        """会话快照必须通过 GUI 的严格响应模型（多余字段会让 GET 会话返回 500）。"""
        from GUI.backend.workflow_sessions import XYSession
        from .workflow import Workflow
        flow = Workflow(models.BperpNullParams(), project_root=common.find_project_root())
        flow.state["z_waveform"] = {"source": "file", "file": "tail.csv"}
        body = flow.snapshot() | {"job_id": "job", "status": "running"}
        XYSession.model_validate(body)
        with self.assertRaises(Exception):     # 模型是严格的：任何新快照键都必须先加进 XYSession
            XYSession.model_validate(body | {"z_waveform": None})


if __name__ == "__main__":
    unittest.main()
