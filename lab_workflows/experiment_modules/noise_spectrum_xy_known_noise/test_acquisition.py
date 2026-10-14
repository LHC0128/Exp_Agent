"""已知噪声的连续色散、离线格式和成功恢复边界回归。"""

import ast
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

import numpy as np

from ...common import WorkflowCancelled
from ...steps.test_dispersion import FakeHF2
from ..static_sensitivity.test_dc_scan import FakeGenerator
from . import acquisition
from .definition import ADAPTER
from .dispersion import resolve_dispersion


class KnownNoiseDispersionTests(unittest.TestCase):
    def params(self, **overrides):
        values = asdict(ADAPTER.defaults()) | dict(dispersion_steps=11, hf2_demod_rate=30,
            dispersion_span_v=.2, dispersion_duration_s=.1, system_settle_s=0.1,
            dispersion_temp_switch_interval_points=5)
        return SimpleNamespace(**(values | overrides))

    def collect(self, params, hf2, root, z, temp):
        return acquisition.acquire_dispersion_scan(params, z=z, z_channel=1, temp=temp,
                                                  temp_channel=2, hf2=hf2, raw_dir=root,
                                                  k_z_hz_per_v=11703.6778)

    def test_parameter_contract_and_layout(self):
        from GUI.backend import main
        from .models import NoiseSpectrumXYKnownNoiseParams
        saved = ADAPTER.defaults()
        self.assertEqual(saved.schema_version, type(saved).schema_version)
        self.assertEqual(saved.z_calibration_k_hz_per_v, 5373.638)
        self.assertEqual(saved.hf2_demod_rate, 200000)
        schema = main._experiment_schema(main._experiment_or_404("noise-spectrum-xy-known-noise"))
        names = {field["name"] for field in schema["fields"]}
        self.assertIn("ANALYSIS_DISPERSION_SOURCE_RUN", names)
        field = next(field for field in schema["fields"] if field["name"] == "Z_CALIBRATION_K_HZ_PER_V")
        self.assertEqual((field["type"], field["unit"]), ("number", "Hz/V"))
        self.assertNotIn("options", field)
        self.assertNotIn("Z_CALIBRATION_SOURCE_RUN", names)
        self.assertFalse(any(name.startswith("DISPERSION_") for name in names))
        self.assertEqual(saved.analysis_dispersion_source_run, "")
        old = saved.to_external() | {"DISPERSION_STEPS": 41, "DISPERSION_DURATION_S": .1}
        migrated = NoiseSpectrumXYKnownNoiseParams.from_external(old)
        self.assertNotIn("DISPERSION_STEPS", migrated.to_external())
        # 来源选择仅在分析时验证，不能妨碍下一次采集参数保存和预检。
        self.assertFalse(replace(saved, analysis_dispersion_source_run="尚未选择").validate())

    def test_complete_scan_and_offline_refit(self):
        span = self.params().dispersion_span_v
        def dispersed(index, raw):
            offset = np.linspace(-span, span, 11)[index]
            center, gamma, a = 0.007, 0.025, 0.0003
            y = a * (offset - center) / ((offset - center)**2 + gamma**2) + 0.001
            raw["/devtest/demods/0/sample.y"][0]["value"][:] = y
            return raw
        for interval, groups in ((5, 3), (0, 1)):
            with self.subTest(interval=interval), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                raw = root / "raw"
                raw.mkdir()
                z, temp = FakeGenerator(), FakeGenerator()
                params = self.params(dispersion_temp_switch_interval_points=interval)
                with patch.object(acquisition, "wait_for_settle"):
                    data = self.collect(params, FakeHF2(raw_factory=dispersed), raw, z, temp)
                self.assertTrue(data["completed_mask"].all())
                self.assertEqual(data["n_points"], params.dispersion_steps)
                self.assertTrue(data["fit_mask"].all())
                self.assertAlmostEqual(data["center_v"], 0.007, places=7)
                self.assertEqual(len(list((raw / "dispersion_steps").glob("point_*.npz"))), 11)
                self.assertFalse(z.output)
                self.assertEqual(z.voltage, 0)
                self.assertTrue(temp.output)
                self.assertEqual(temp.voltage, 5)
                self.assertEqual(sum(event[:2] == ("output", False) for event in temp.events), groups)
                result = resolve_dispersion(root, {"dispersion": {}})
                self.assertEqual(result["source"], "raw_refit")
                self.assertEqual(result["n_points"], params.dispersion_steps)
                self.assertTrue(result["fit_mask"].all())
                self.assertAlmostEqual(result["slope_v_per_hz"], data["slope_v_per_hz"])
                with np.load(raw / "dispersion_scan.npz") as saved:
                    self.assertEqual(saved["schema_version"], 3)
                    self.assertNotIn("y_mean", saved.files)
                    np.testing.assert_equal(saved["sample_counts"], 3)

    def test_failed_scan_preserves_partial_waveforms_and_restores(self):
        for error in (WorkflowCancelled("取消"), TimeoutError("超时"), RuntimeError("丢样")):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                z, temp = FakeGenerator(), FakeGenerator()
                hf2 = FakeHF2(fault=error, fault_at=1)
                with patch.object(acquisition, "wait_for_settle"), self.assertRaises(type(error)):
                    self.collect(self.params(), hf2, root, z, temp)
                with np.load(root / "dispersion_scan.npz") as saved:
                    self.assertEqual(saved["completed_mask"].sum(), 1)
                    self.assertTrue(np.isnan(saved["y_median"][1:]).all())
                with np.load(root / "dispersion_steps" / "point_0001.npz") as saved:
                    self.assertFalse(saved["completed"])
                    self.assertEqual(saved["sample_count"], 3)
                self.assertFalse(z.output)
                self.assertEqual(z.voltage, 0)
                self.assertTrue(temp.output)
                self.assertEqual(temp.voltage, 5)
                self.assertTrue(all(module.clear.called for module in hf2.modules))

    def test_restoration_error_fails_scan_without_replacing_original(self):
        report = SimpleNamespace(errors=("温控恢复失败",))
        for error in (None, WorkflowCancelled("原始取消")):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(acquisition, "wait_for_settle"), \
                    patch.object(acquisition, "fit_dispersion_curve", return_value={"is_valid": True}), \
                    patch.object(acquisition, "run_safety_shutdown", return_value=report):
                with self.assertRaisesRegex(WorkflowCancelled if error else RuntimeError, "原始取消" if error else "温控恢复失败"):
                    self.collect(self.params(), FakeHF2(fault=error), Path(tmp), FakeGenerator(), FakeGenerator())

    def test_offline_requires_new_complete_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "raw").mkdir()
            with self.assertRaises(FileNotFoundError):
                resolve_dispersion(root, {"dispersion": {"slope_v_per_hz": 1}})
            path = root / "raw" / "dispersion_scan.npz"
            np.savez(path, schema_version=2)
            with self.assertRaisesRegex(ValueError, "版本 3"):
                resolve_dispersion(root, {})
            np.savez(path, schema_version=3, offsets_v=[0, 1], y_median=[0, 1], completed_mask=[True, False])
            with self.assertRaisesRegex(ValueError, "未全部完成"):
                resolve_dispersion(root, {})

    def test_new_workflow_has_no_internal_dispersion_or_import_side_effect(self):
        from . import workflow
        tree = ast.parse(Path(workflow.__file__).read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == "acquire_dispersion_scan" for node in ast.walk(tree)))
        self.assertTrue(callable(workflow.Workflow))



if __name__ == "__main__":
    unittest.main()
