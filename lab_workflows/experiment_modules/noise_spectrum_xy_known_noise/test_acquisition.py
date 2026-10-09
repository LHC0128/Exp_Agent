"""已知噪声的连续色散、离线格式和成功恢复边界回归。"""

import ast
import tempfile
import unittest
from dataclasses import replace
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
        return replace(ADAPTER.defaults(), dispersion_steps=11, hf2_demod_rate=30,
                       dispersion_fit_skip_start=1, dispersion_fit_skip_end=1, **overrides)

    def collect(self, params, hf2, root, z, temp):
        return acquisition.acquire_dispersion_scan(params, z=z, z_channel=1, temp=temp,
                                                  temp_channel=2, hf2=hf2, raw_dir=root,
                                                  k_z_hz_per_v=11703.6778)

    def test_parameter_contract_and_layout(self):
        from GUI.backend import main
        saved = ADAPTER.defaults()
        self.assertEqual(saved.schema_version, 2)
        self.assertEqual(saved.hf2_demod_rate, 200000)
        self.assertEqual(saved.dispersion_duration_s, 0.1)
        schema = main._experiment_schema(main._experiment_or_404("noise-spectrum-xy-known-noise"))
        names = {field["name"] for field in schema["fields"]}
        self.assertIn("DISPERSION_DURATION_S", names)
        self.assertNotIn("DISPERSION_SAMPLES_PER_STEP", names)
        advanced = schema["parameter_layout"]["advanced"]
        self.assertEqual(advanced[advanced.index("HF2_DEMOD_RATE") + 1], "DISPERSION_DURATION_S")
        for overrides in (dict(dispersion_duration_s=0), dict(dispersion_duration_s=float("nan")),
                          dict(hf2_demod_rate=float("inf")), dict(hf2_demod_rate=1, dispersion_duration_s=0.1)):
            with self.subTest(overrides=overrides):
                self.assertTrue(replace(saved, **overrides).validate())

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
                self.assertAlmostEqual(data["center_v"], 0.007, places=7)
                self.assertEqual(len(list((raw / "dispersion_steps").glob("point_*.npz"))), 11)
                self.assertFalse(z.output)
                self.assertEqual(z.voltage, 0)
                self.assertTrue(temp.output)
                self.assertEqual(temp.voltage, 5)
                self.assertEqual(sum(event[:2] == ("output", False) for event in temp.events), groups)
                result = resolve_dispersion(root, {"parameters": params.to_external(), "dispersion": {}})
                self.assertEqual(result["source"], "raw_refit")
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

    def test_workflow_restores_arbitrary_wave_only_after_success(self):
        # 模块顶层连接仪器；只执行实际色散 try 块来检验恢复时序。
        path = Path(__file__).with_name("workflow.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                     and any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                             and item.func.id == "acquire_dispersion_scan" for item in ast.walk(ast.Module(body=node.body, type_ignores=[]))))
        code = compile(ast.Module(body=[block], type_ignores=[]), str(path), "exec")
        offsets = np.linspace(-0.2, 0.2, 11)
        result = acquisition.fit_dispersion_curve(offsets, offsets / (offsets**2 + 0.1**2),
                                                  skip_start_points=0, skip_end_points=0, k_z_hz_per_v=1)
        result.update(actual_rate_sa_s=np.array([30]), actual_duration_s=np.array([0.1]), sample_counts=np.array([3]))
        for stage, error in (("scan", None), ("scan", WorkflowCancelled("取消色散")),
                             ("scan", TimeoutError("色散超时")), ("save", OSError("配置保存失败"))):
            with self.subTest(stage=stage, error=error), patch("builtins.open", mock_open()) as writer:
                scan = Mock(return_value=result, side_effect=error if stage == "scan" else None)
                if stage == "save":
                    writer.side_effect = error
                restore, upload, shutdown = Mock(), Mock(), Mock(return_value=SimpleNamespace(errors=()))
                z, xy = Mock(), Mock()
                namespace = dict(acquire_dispersion_scan=scan, PARAMS=self.params(), dg_comp=xy,
                                 dg_sweep=z, z_channel=1, dg_temp=Mock(), dg_temp_cfg={"channel": 2},
                                 hfi=Mock(), raw_dir=Path("unused"), Z_CALIBRATION=SimpleNamespace(k_hz_per_v=1),
                                 set_xy_sine_phase=restore, XY_CTRL_PHASE=0, upload_noise_waveform=upload,
                                 run_safety_shutdown=shutdown, DGChannelShutdown=acquisition.DGChannelShutdown,
                                 TemperatureSwitchRestore=acquisition.TemperatureSwitchRestore,
                                 DisconnectTarget=lambda *args: args, devices={}, STANDARD_PRESERVED_OUTPUTS=(),
                                 config_saved={}, config_path=Path("unused"), yaml=Mock())
                namespace.update(namespace["PARAMS"].to_external())
                if error:
                    with self.assertRaises(type(error)):
                        exec(code, namespace)
                    restore.assert_not_called()
                    upload.assert_not_called()
                    z.set_output.assert_not_called()
                    shutdown.assert_called_once()
                else:
                    exec(code, namespace)
                    restore.assert_called_once_with(namespace["XY_CTRL_PHASE"], outputs_on=True)
                    upload.assert_called_once()
                    z.set_output.assert_called_once_with(True, channel=1)
                    shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
