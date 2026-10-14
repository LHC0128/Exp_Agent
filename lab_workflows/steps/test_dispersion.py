"""共享色散采集的无硬件回归；假 HF2 保留真实 DAQ 字典形状。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from lockin_amplifier import DAQConfig, daq, lockin_capabilities

from ..common import WorkflowCancelled
from . import dispersion


def waveforms(count=3, rate=30.0, clockbase=30000.0, demod_idx=2):
    y = np.resize([1.0, 100.0, 2.0], count)
    ticks = np.uint64(2**53 + 123) + np.arange(count, dtype=np.uint64) * np.uint64(clockbase / rate)
    return {f"/devtest/demods/{demod_idx}/sample.{key}": [
        {"value": values[None, :], "timestamp": ticks[None, :].copy()}
    ] for key, values in (("x", 2 * y), ("y", y))}


class FakeHF2:
    """仅模拟本次共享步骤实际使用的接口，不导入或连接仪器。"""

    device_id = "devtest"
    model = "HF2"
    capabilities = lockin_capabilities("HF2")
    acquisition_mode = "auto"
    clockbase = 30000.0

    def __init__(self, rate=30.0, raw_factory=None, fault=None, fault_at=0):
        self.rate, self.raw_factory = rate, raw_factory
        self.fault, self.fault_at = fault, fault_at
        self.modules = []
        self.sync = Mock()
        self.daq = SimpleNamespace(dataAcquisitionModule=self.new_module)

    def demod_path(self, index):
        return f"/{self.device_id}/demods/{index}"

    def get_double(self, path):
        assert path.endswith("/rate")
        return self.rate

    def new_module(self):
        settings, subscribed = {}, []
        index = len(self.modules)
        module = Mock()
        module.set.side_effect = lambda key, value: settings.update({key: value})
        module.subscribe.side_effect = subscribed.append
        module.settings, module.subscribed = settings, subscribed
        module.progress.return_value = [1.0]
        if self.fault is not None and index == self.fault_at:
            module.progress.side_effect = self.fault

        def read(flat):
            count = settings["dataAcquisitionModule/grid/cols"]
            demod_idx = int(subscribed[0].split("/")[3])
            raw = waveforms(count, self.rate, self.clockbase, demod_idx)
            return self.raw_factory(index, raw) if self.raw_factory else raw

        module.read.side_effect = read
        self.modules.append(module)
        return module


class DispersionTests(unittest.TestCase):
    def collect(self, hf2, path, **overrides):
        options = dict(demod_idx=2, duration_s=0.1, requested_rate=31.0, offset_v=0.02,
                       path=path, cancellation_check=lambda: None)
        options.update(overrides)
        return dispersion.acquire_dispersion_point(hf2, **options)

    def test_actual_rate_ceil_medians_and_atomic_waveforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispersion_steps" / "point_0000.npz"
            hf2 = FakeHF2()
            point = self.collect(hf2, path, duration_s=0.1001)
            self.assertEqual(point["expected_sample_count"], 4)
            self.assertEqual(point["sample_count"], 4)
            self.assertEqual(point["actual_rate_sa_s"], 30)
            self.assertAlmostEqual(point["actual_duration_s"], 4 / 30)
            self.assertEqual(point["y_median"], 1.5)
            self.assertEqual(point["x_median"], 3)
            self.assertAlmostEqual(float(point["r_median"]), 1.5 * np.sqrt(5))
            module = hf2.modules[0]
            for key, value in (("type", 0), ("count", 1), ("endless", 0), ("grid/mode", 4),
                               ("grid/rows", 1), ("grid/cols", 4), ("flags", 4)):
                self.assertEqual(module.settings[f"dataAcquisitionModule/{key}"], value)
            self.assertEqual(module.subscribed, ["/devtest/demods/2/sample.x", "/devtest/demods/2/sample.y"])
            hf2.sync.assert_called_once()
            module.clear.assert_called_once()
            with np.load(path, allow_pickle=False) as saved:
                self.assertTrue(saved["completed"])
                np.testing.assert_array_equal(saved["y_samples"], [1, 100, 2, 1])
                np.testing.assert_allclose(saved["r_samples"], np.hypot(saved["x_samples"], saved["y_samples"]))
                np.testing.assert_allclose(saved["time_s"], np.arange(4) / 30)
                self.assertEqual(saved["timestamp_ticks"][0], 2**53 + 123)
                self.assertEqual(saved["schema_version"], 3)
            self.assertFalse(path.with_suffix(".npz.tmp").exists())

    def test_finite_positive_and_minimum_sample_count(self):
        for rate, duration in ((0, 1), (-1, 1), (float("nan"), 1), (float("inf"), 1),
                               (100, 0), (100, -1), (100, float("inf")), (10, 0.01), (1e308, 1e308)):
            with self.subTest(rate=rate, duration=duration), self.assertRaises(ValueError):
                dispersion.dispersion_sample_count(rate, duration)
        self.assertEqual(dispersion.dispersion_sample_count(10, 0.11), 2)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            self.collect(FakeHF2(rate=1), Path(tmp) / "point.npz")

    def test_invalid_records_stop_and_keep_readable_data(self):
        def invalid(kind, raw):
            x, y = raw["/devtest/demods/2/sample.x"][0], raw["/devtest/demods/2/sample.y"][0]
            if kind == "missing":
                del raw["/devtest/demods/2/sample.y"]
            elif kind == "length":
                y["value"] = y["value"][:, :2]
            elif kind == "nonfinite":
                y["value"][0, 1] = np.nan
            elif kind == "unaligned":
                y["timestamp"][0, 1] += np.uint64(1)
            elif kind == "duplicate":
                for block in (x, y):
                    block["timestamp"][0, 1] = block["timestamp"][0, 0]
            elif kind == "gap":
                for block in (x, y):
                    block["timestamp"][0, 2] += np.uint64(1000)
            elif kind == "frames":
                raw["/devtest/demods/2/sample.y"].append(y)
            elif kind == "dataloss":
                y["header"] = {"flags": np.array([4])}
            return raw

        for kind in ("missing", "length", "nonfinite", "unaligned", "duplicate", "gap", "frames", "dataloss"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "point.npz"
                hf2 = FakeHF2(raw_factory=lambda index, raw: invalid(kind, raw))
                with self.assertRaises(ValueError):
                    self.collect(hf2, path)
                hf2.modules[0].clear.assert_called_once()
                with np.load(path, allow_pickle=False) as saved:
                    self.assertFalse(saved["completed"])
                    self.assertTrue(str(saved["error"]))
                    np.testing.assert_array_equal(saved["x_samples"], [2, 200, 4])
                    if kind == "nonfinite":
                        self.assertTrue(np.isnan(saved["y_samples"][1]))
                        np.testing.assert_allclose(saved["time_s"], np.arange(3) / 30)

    def test_daq_finished_flag_remains_valid(self):
        def finished(index, raw):
            for blocks in raw.values():
                blocks[0]["header"] = {"flags": np.array([1 | 8 | 16])}
            return raw
        with tempfile.TemporaryDirectory() as tmp:
            point = self.collect(FakeHF2(raw_factory=finished), Path(tmp) / "point.npz")
            self.assertTrue(point["completed"])

    def test_wait_cancellation_saves_partial_and_cleans_module(self):
        hf2 = FakeHF2()
        create = hf2.new_module
        def waiting():
            module = create()
            module.progress.return_value = [0.5]
            return module
        hf2.daq.dataAcquisitionModule = waiting
        def cancel():
            if hf2.modules and hf2.modules[0].execute.called:
                raise WorkflowCancelled("DAQ 等待取消")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "point.npz"
            with self.assertRaisesRegex(WorkflowCancelled, "等待取消"):
                self.collect(hf2, path, cancellation_check=cancel)
            hf2.modules[0].clear.assert_called_once()
            with np.load(path) as saved:
                self.assertFalse(saved["completed"])
                self.assertEqual(saved["sample_count"], 3)

    def test_timeout_uses_actual_record_duration_plus_five(self):
        hf2 = FakeHF2()
        create = hf2.new_module
        def waiting():
            module = create()
            module.progress.return_value = [0]
            return module
        hf2.daq.dataAcquisitionModule = waiting
        with tempfile.TemporaryDirectory() as tmp, patch.object(daq.time, "sleep"), \
                patch.object(daq.time, "monotonic", side_effect=[0, 5.11]):
            path = Path(tmp) / "point.npz"
            with self.assertRaises(TimeoutError):
                self.collect(hf2, path)
            hf2.modules[0].clear.assert_called_once()
            with np.load(path) as saved:
                self.assertEqual(saved["actual_duration_s"], 0.1)
                self.assertEqual(saved["sample_count"], 3)
                self.assertFalse(saved["completed"])

    def test_sample_loss_and_cleanup_do_not_hide_original_failure(self):
        for original in (None, RuntimeError("HF2 丢样")):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                hf2 = FakeHF2(fault=original)
                create = hf2.new_module
                def bad_cleanup():
                    module = create()
                    module.clear.side_effect = RuntimeError("清理失败")
                    return module
                hf2.daq.dataAcquisitionModule = bad_cleanup
                path = Path(tmp) / "point.npz"
                with self.assertRaisesRegex(RuntimeError, "丢样" if original else "清理失败"):
                    self.collect(hf2, path)
                with np.load(path) as saved:
                    self.assertFalse(saved["completed"])
                    self.assertEqual(saved["sample_count"], 3)

    def test_save_failure_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.npz"
            dispersion.save_scan(path, {"completed_mask": [True, False]})
            before = path.read_bytes()
            with patch.object(dispersion.np, "savez", side_effect=OSError("写入失败")), self.assertRaises(OSError):
                dispersion.save_scan(path, {"completed_mask": [True, True]})
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(path.with_suffix(".npz.tmp").exists())

    def test_other_daq_callers_keep_default_flags(self):
        hf2 = FakeHF2()
        collector = daq.DAQCollector(hf2)
        collector.configure(DAQConfig())
        self.assertEqual(hf2.modules[0].settings["dataAcquisitionModule/flags"], 0)
        collector.close(strict=True)


if __name__ == "__main__":
    unittest.main()
