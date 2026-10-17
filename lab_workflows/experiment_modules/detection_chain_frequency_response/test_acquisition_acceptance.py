"""检测链频响独立验收：真实采集函数、假仪器和真实文件写入。"""

from contextlib import contextmanager
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import yaml

from ...common import WorkflowCancelled
from . import acquisition, workflow
from .stages import ACQUISITION
from .test_workflow import copy_config, fake_hardware, scan_params


ACQUISITION_CASES = {
    "detection-chain-frequency-response": {
        "mode": "single",
        "cases": {
            "order": ["DetectionChainAcquisitionAcceptance::test_real_order_and_slow_writer"],
            "slow_disk": ["DetectionChainAcquisitionAcceptance::test_real_order_and_slow_writer"],
            "cancel": ["DetectionChainAcquisitionAcceptance::test_cancel_preserves_previous_point"],
            "acquire_failure": ["DetectionChainAcquisitionAcceptance::test_acquire_failure_preserves_previous_point"],
            "restore_failure": ["DetectionChainAcquisitionAcceptance::test_restore_failure_rescues_already_read_point"],
            "save_failure": ["DetectionChainAcquisitionAcceptance::test_save_failure_preserves_previous_point"],
            "partial_save": ["DetectionChainAcquisitionAcceptance::test_save_failure_preserves_previous_point"],
        },
        "sources": [
            "lab_workflows/experiment_modules/detection_chain_frequency_response/acquisition.py",
            "lab_workflows/experiment_modules/detection_chain_frequency_response/workflow.py",
        ],
    },
}

REAL_SLEEP = time.sleep


class DetectionChainAcquisitionAcceptance(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dcfr-acceptance-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_config(self.root)

    @contextmanager
    def rig(self, *, restore_failure=None, acquire_failure=None, save_failure=None, slow=False,
            sample_count=None, pump_power=0.0):
        """只替换驱动 I/O；时序、目录、数组及 YAML 写入均走生产实现。"""
        with fake_hardware(self.root) as hw:
            params = scan_params()
            params.pump_laser_power_v = pump_power
            ctx = workflow.Workflow(params, project_root=self.root)
            ctx.execute([stage.id for stage in ACQUISITION[:6]])
            events, voltage = [], {"value": 5.0}
            read_count = 0
            saved = np.save
            saved_npz = np.savez
            written = Path.write_text

            def set_dc(value, **kwargs):
                if value == 5.0:
                    events.append("restore_attempt")
                    if restore_failure is not None:
                        raise restore_failure
                voltage["value"] = value
                events.append("on" if value == 5.0 else "off")

            def configure(*args, **kwargs):
                self.assertEqual(voltage["value"], 5.0)
                events.append("configure")

            def read(*args, config=None, **kwargs):
                nonlocal read_count
                read_count += 1
                self.assertEqual(voltage["value"], 0.0)
                events.append("acquire")
                if acquire_failure is not None and read_count == 2:
                    raise acquire_failure
                length = sample_count if sample_count is not None else config.grid_cols
                return [SimpleNamespace(values=np.arange(length, dtype=float),
                    signal_name="demods/0/sample.y", time=np.arange(length) / 12000.0,
                    config_snapshot={"acquisition_module": "daq", "requested_rate": 12000.0,
                        "actual_rate": 12000.0, "demod_stream_rate": 12000.0,
                        "actual_duration": length / 12000.0, "time_source": "device_timestamp"})]

            def save(path, values, *args, **kwargs):
                events.append("save")
                if restore_failure is None:
                    self.assertEqual(voltage["value"], 5.0)
                else:
                    self.assertIn("restore_attempt", events)
                if save_failure is not None and str(path).endswith("0001.npy"):
                    raise save_failure
                if slow:
                    REAL_SLEEP(0.03)
                    self.assertEqual(voltage["value"], 5.0)
                return saved(path, values, *args, **kwargs)

            def save_npz(path, *args, **kwargs):
                if restore_failure is None:
                    self.assertEqual(voltage["value"], 5.0, str(path))
                else:
                    self.assertIn("restore_attempt", events)
                return saved_npz(path, *args, **kwargs)

            def write_text(path, *args, **kwargs):
                if restore_failure is None:
                    self.assertEqual(voltage["value"], 5.0, str(path))
                return written(path, *args, **kwargs)

            hw.devices["temp_switch"].setup_dc.side_effect = set_dc
            hw.devices["temp_switch"].set_output.side_effect = lambda enabled, **kwargs: self.assertTrue(enabled)
            hw.devices["aom"].setup_sine.side_effect = configure
            hw.devices["aom"].set_frequency.side_effect = configure
            hw.acquire.side_effect = read
            with patch.object(acquisition, "wait", side_effect=lambda seconds: events.append("settle")), \
                    patch.object(acquisition.np, "save", side_effect=save), \
                    patch.object(acquisition.np, "savez", side_effect=save_npz), \
                    patch.object(Path, "write_text", new=write_text):
                yield SimpleNamespace(ctx=ctx, hardware=hw, events=events, voltage=voltage)

    def config(self, ctx):
        return yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))

    def test_real_order_and_slow_writer(self):
        for pump in (0.0, 0.1):
            with self.subTest(pump=pump), self.rig(slow=True, pump_power=pump) as rig:
                rig.ctx.execute(["A07"])
                expected = ["configure", "off", "settle", "acquire", "restore_attempt", "on", "save"]
                self.assertEqual(rig.events, expected * 3)
                self.assertEqual(rig.hardware.acquire.call_count, 3)
                self.assertEqual(self.config(rig.ctx)["completion_status"], "completed")
                self.assertEqual(sorted(path.name for path in (rig.ctx.run_dir / "raw").glob("waveform_*.npy")),
                                 ["waveform_0000.npy", "waveform_0001.npy", "waveform_0002.npy"])
                with np.load(rig.ctx.run_dir / "raw/frequency_response.npz") as raw:
                    self.assertTrue(np.isnan(raw["peak_amplitudes"][1]))
                for index in range(3):
                    self.assertTrue((rig.ctx.run_dir / f"raw/waveform_{index:04d}_acquisition.npz").is_file())
                rig.hardware.devices["tec"].set_enable.assert_not_called()

    def assert_second_read_failure(self, error, status):
        with self.rig(acquire_failure=error) as rig:
            with self.assertRaises(type(error)) as caught:
                rig.ctx.execute(["A07"])
            self.assertIs(caught.exception, error)
            self.assertEqual(rig.voltage["value"], 5.0)
            self.assertEqual(rig.hardware.acquire.call_count, 2)
            self.assertTrue((rig.ctx.run_dir / "raw/waveform_0000.npy").is_file())
            self.assertFalse((rig.ctx.run_dir / "raw/waveform_0001.npy").exists())
            self.assertEqual(self.config(rig.ctx)["completion_status"], status)

    def test_cancel_preserves_previous_point(self):
        self.assert_second_read_failure(WorkflowCancelled("验收取消"), "cancelled")

    def test_acquire_failure_preserves_previous_point(self):
        self.assert_second_read_failure(RuntimeError("验收采集失败"), "failed")

    def test_restore_failure_rescues_already_read_point(self):
        error = RuntimeError("验收温控恢复失败")
        with self.rig(restore_failure=error) as rig:
            with self.assertRaises(RuntimeError) as caught:
                rig.ctx.execute(["A07"])
            self.assertIs(caught.exception, error)
            self.assertEqual(rig.hardware.acquire.call_count, 1)
            self.assertIn("restore_attempt", rig.events)
            self.assertTrue((rig.ctx.run_dir / "raw/waveform_0000.npy").is_file(),
                            "温控恢复失败后必须抢救已读入内存的当前频点")
            self.assertEqual(self.config(rig.ctx)["completion_status"], "failed")

    def test_save_failure_preserves_previous_point(self):
        error = OSError("验收磁盘失败")
        with self.rig(save_failure=error) as rig:
            with self.assertRaises(OSError) as caught:
                rig.ctx.execute(["A07"])
            self.assertIs(caught.exception, error)
            self.assertEqual(rig.voltage["value"], 5.0)
            self.assertEqual(rig.hardware.acquire.call_count, 2)
            self.assertTrue((rig.ctx.run_dir / "raw/waveform_0000.npy").is_file())
            self.assertTrue((rig.ctx.run_dir / "raw/waveform_0001_acquisition.npz").is_file())
            self.assertFalse((rig.ctx.run_dir / "raw/waveform_0002.npy").exists())
            self.assertEqual(self.config(rig.ctx)["completion_status"], "failed")

    def test_short_waveform_and_restore_failure_keep_original_error_and_current_data(self):
        with self.rig(restore_failure=RuntimeError("恢复的次要错误"), sample_count=7) as rig:
            with self.assertRaisesRegex(RuntimeError, "未取得足够"):
                rig.ctx.execute(["A07"])
            np.testing.assert_array_equal(np.load(rig.ctx.run_dir / "raw/waveform_0000.npy"), np.arange(7))
            self.assertEqual(rig.hardware.acquire.call_count, 1)
            self.assertTrue((rig.ctx.run_dir / "raw/waveform_0000_acquisition.npz").is_file())
            self.assertIn("未取得足够", self.config(rig.ctx)["failure_reason"])
