"""XY 正弦噪声谱无硬件回归：真实工作流、共享步骤及数据契约。"""

from contextlib import ExitStack, contextmanager, redirect_stdout
from dataclasses import replace
from datetime import datetime
from io import StringIO
from itertools import count
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
import yaml

from . import workflow, preparation, acquisition, _helpers
from .models import NoiseSpectrumXYParams
from ...common import WorkflowCancelled, load_mapping, load_safety_limits
from ...steps import routed_signal_generator

ROOT = Path(__file__).resolve().parents[3]
MODULE = "lab_workflows.experiment_modules.noise_spectrum_xy.workflow"
AW_KEYS = {"XY_AW_OUTPUT_VPP", "XY_AW_OUTPUT_OFFSET_V", "XY_AW_REPEAT_FREQ_HZ", "XY_AW_POINTS"}


def copy_config(root):
    """只复制本实验需要的配置，所有测试数据留在临时目录。"""
    (root / "params").mkdir()
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copyfile(ROOT / "params" / name, root / "params" / name)


def scan_params():
    return NoiseSpectrumXYParams(
        target_noise_freq_stop_hz=500.0, target_noise_freq_points=3,
        xy_ctrl_k_hz_per_v=1000.0, xy_ctrl_b_hz=0.0,
        hf2_daq_rate=1000.0, hf2_daq_duration=0.2,
        analysis_bin_width_hz=50.0, analysis_frequency_min_hz=10.0,
        analysis_frequency_max_hz=300.0, xy_settle_time=0.2,
        temp_switch_on_settle_s=0.25,
    )


@contextmanager
def fake_hardware(root):
    """在驱动构造和 HF2 I/O 边界注入 Mock，保留设备会话、路由及校相算法。"""
    mapping = load_mapping(root)
    devices = {name: Mock(name=name) for name in (
        "gs200", "dg_comp", "dg_sweep", "dg_mod", "dg_temp", "dg_laser", "tec", "hf2")}
    devices["tec"].get_temperature.return_value = 100.0
    devices["hf2"].get_double.side_effect = lambda path: (
        scan_params().hf2_daq_tc if str(path).endswith("/timeconstant") else
        800.0 if str(path).endswith("/rate") else 30.0)
    devices["dg_comp"].get_shape.return_value = "SINusoid"
    devices["dg_comp"].get_burst_state.return_value = True
    devices["dg_comp"].get_burst_mode.return_value = "INFinity"
    devices["dg_comp"].get_burst_trigger_source.return_value = "EXTernal"
    devices["dg_comp"].get_frequency.return_value = 10000.0
    by_resource = {mapping[key]["resource"]: devices[name] for name, key in (
        ("dg_comp", "X_magnetic_field"), ("dg_sweep", "Z_magnetic_field"),
        ("dg_mod", "Pump_modulation"), ("dg_temp", "Temp_Switch"),
        ("dg_laser", "Pump_laser_power"))}
    sample = dict(r=1.0, x=1.0, y=0.0, theta=0.0)
    rng = np.random.default_rng(42)
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(StringIO()))
        for name, target in (("gs200", "gs200.GS200Instrument"),
                             ("tec", "tec_controller.TECInstrument"),
                             ("hf2", "lab_workflows.devices.create_lockin")):
            factory = stack.enter_context(patch(target, return_value=devices[name]))
            stack.enter_context(patch.object(workflow, target.rsplit(".", 1)[1], factory))
            stack.enter_context(patch.object(preparation, target.rsplit(".", 1)[1], factory))
        stack.enter_context(patch.object(routed_signal_generator, "create_signal_generator",
                                        side_effect=lambda cfg: by_resource[cfg["resource"]]))
        clocks = stack.enter_context(patch("lab_workflows.steps.synchronize_connected_clocks",
                                          return_value={"verified": True}))
        stack.enter_context(patch.object(workflow, "synchronize_connected_clocks", clocks))
        stack.enter_context(patch.object(preparation, "synchronize_connected_clocks", clocks))
        for name in ("configure_signal_input", "configure_oscillator"):
            stack.enter_context(patch.object(workflow.demod, name))
        rates = stack.enter_context(patch.object(workflow.demod, "configure_demodulator",
                                                side_effect=[1200.0, 800.0, 1200.0]))
        samples = stack.enter_context(patch.object(workflow.demod, "read_demod_sample",
            side_effect=[sample, sample, dict(sample, theta=math.radians(10)), sample]))
        acquire = stack.enter_context(patch.object(workflow.daq, "acquire_data",
            side_effect=lambda hfi, cfg, **kwargs: [SimpleNamespace(values=rng.normal(size=cfg.grid_cols))]))
        # runpy 的模块入口也使用同一时钟；不改生产代码的等待时长。
        ticks = count(0.0, 0.05)
        stack.enter_context(patch("time.monotonic", side_effect=lambda: next(ticks)))
        sleep = stack.enter_context(patch("time.sleep"))
        progress = Mock()
        progress.return_value = Mock()
        stack.enter_context(patch("tqdm.tqdm", progress))
        stack.enter_context(patch.object(workflow, "tqdm", progress))
        stack.enter_context(patch.object(acquisition, "tqdm", progress))
        shutdown = stack.enter_context(patch.object(workflow, "safe_scan_outputs_off",
                                                   wraps=workflow.safe_scan_outputs_off))
        yield SimpleNamespace(devices=devices, clocks=clocks, rates=rates, samples=samples,
                              acquire=acquire, sleep=sleep, progress=progress, shutdown=shutdown)


class NoiseSpectrumXYSineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xy-noise-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        copy_config(self.root)
        self.limits = load_safety_limits(self.root)

    def assert_shutdown(self, hw):
        hw.shutdown.assert_called_once()
        for channel in (1, 2):
            device = hw.devices["dg_comp"]
            self.assertEqual([entry for entry in device.set_output.call_args_list
                              if entry.kwargs["channel"] == channel][-1], call(False, channel=channel))
            device.setup_dc.assert_any_call(0.0, channel=channel)
        self.assertEqual([entry for entry in hw.devices["dg_sweep"].set_output.call_args_list
                          if entry.kwargs["channel"] == 1][-1], call(False, channel=1))
        hw.devices["dg_sweep"].setup_dc.assert_any_call(0.0, channel=1)
        self.assertEqual(hw.devices["dg_temp"].setup_dc.call_args, call(5.0, channel=2))
        self.assertEqual(hw.devices["dg_temp"].set_output.call_args, call(True, channel=2))
        hw.devices["tec"].disconnect.assert_called_once()
        for name, device in hw.devices.items():
            if name != "tec":
                device.disconnect.assert_not_called()
        hw.devices["dg_mod"].setup_dc.assert_not_called()
        self.assertFalse(any(entry.args == (False,)
                             for entry in hw.devices["dg_mod"].set_output.call_args_list))
        self.assertNotIn(call(False), hw.devices["gs200"].set_output.call_args_list)

    def test_scan_amplitude_rearms_calibrated_phases_with_outputs_off(self):
        device = Mock()
        setter = lambda peak: workflow.set_xy_sine_peak(device, peak, 350.0, 90.0, self.limits)
        off = [call.set_output(False, channel=1), call.set_output(False, channel=2)]
        for peak in (0.75, 0.60, 0.75):
            device.reset_mock()
            setter(peak)
            self.assertEqual(device.mock_calls, off + [
                call.set_amplitude(2 * peak, channel=1), call.set_amplitude(2 * peak, channel=2),
            ] + off + [call.set_burst_phase(350.0, channel=1), call.set_burst_phase(80.0, channel=2),
                       call.set_output(True, channel=1), call.set_output(True, channel=2)])
        device.reset_mock()
        setter(0.0)
        self.assertEqual(device.mock_calls, off)
        device.reset_mock()
        with self.assertRaises(ValueError):
            setter(self.limits["X_magnetic_field"]["max"] + 1)
        self.assertEqual(device.mock_calls, [])
        for method in (device.set_amplitude, device.set_burst_phase):
            device.reset_mock()
            method.side_effect = RuntimeError("模拟通信异常")
            with self.assertRaises(RuntimeError):
                setter(0.75)
            self.assertNotIn(call(True, channel=1), device.set_output.call_args_list)
            self.assertNotIn(call(True, channel=2), device.set_output.call_args_list)
            method.side_effect = None

    def test_temperature_output_off_restore_wait_and_cancel(self):
        device = Mock()
        params = scan_params()
        workflow.set_temp_switch(device, False, params, self.limits)
        self.assertEqual(device.mock_calls, [call.set_output(False, channel=2)])
        device.reset_mock()
        with patch.object(_helpers, "time") as clock, patch.object(_helpers, "check_cancelled") as cancel:
            clock.monotonic.side_effect = [0, 0, 0.1, 0.2, 0.25]
            workflow.set_temp_switch(device, True, params, self.limits, restore_wait=True)
            self.assertEqual(device.mock_calls, [call.setup_dc(5.0, channel=2), call.set_output(True, channel=2)])
            self.assertAlmostEqual(sum(entry.args[0] for entry in clock.sleep.call_args_list), 0.25)
            device.reset_mock()
            clock.monotonic.side_effect = [0, 0]
            cancel.side_effect = WorkflowCancelled("测试取消")
            with self.assertRaises(WorkflowCancelled):
                workflow.set_temp_switch(device, True, params, self.limits, restore_wait=True)
            device.set_output.assert_called_once_with(True, channel=2)
            clock.monotonic.side_effect = [0, 0]
            clock.sleep.reset_mock()
            workflow.set_temp_switch(device, True, replace(params, temp_switch_on_settle_s=0),
                                     self.limits, restore_wait=True)
            clock.sleep.assert_not_called()

    def test_shutdown_preserves_pump_gate_and_common_trigger(self):
        devices = {name: Mock() for name in ("dg_comp", "dg_sweep", "dg_mod", "dg_temp", "tec")}
        report = workflow.safe_scan_outputs_off(devices)
        self.assertTrue(report.completed, report.errors)
        self.assertIn("Time_sequence", report.preserved_outputs)
        self.assertEqual(devices["dg_mod"].mock_calls, [])
        devices["dg_temp"].setup_dc.assert_called_once_with(5.0, channel=2)
        devices["dg_temp"].set_output.assert_called_once_with(True, channel=2)
        for channel in (1, 2):
            devices["dg_comp"].set_output.assert_any_call(False, channel=channel)
        devices["dg_sweep"].set_output.assert_called_once_with(False, channel=1)
        self.assertIn("Time_sequence_2", report.preserved_outputs)

    def test_minimal_scan_preserves_phases_rates_files_and_shutdown_order(self):
        params = scan_params()
        original = params.to_external()
        events = []
        with fake_hardware(self.root) as hw:
            shutdown = hw.shutdown._mock_wraps
            def close(devices):
                events.append("shutdown")
                return shutdown(devices)
            hw.shutdown.side_effect = close
            dump = yaml.safe_dump
            def save_config(config, *args, **kwargs):
                if "data_files" in config:
                    events.append("complete")
                return dump(config, *args, **kwargs)
            def configure(hfi, cfg):
                if cfg.phase == 30.0 and cfg.time_constant == params.hf2_demod_tc:
                    events.append("restore_hf2")
                return 800.0
            hw.rates.side_effect = configure
            with patch.object(workflow.yaml, "safe_dump", side_effect=save_config):
                run_dir = workflow.run(params, project_root=self.root)
            self.assert_shutdown(hw)
            self.assertEqual(events, ["complete", "restore_hf2", "shutdown"])
            xy = hw.devices["dg_comp"]
            self.assertEqual(xy.setup_sine.call_count, 2)
            self.assertEqual(xy.set_burst_mode.call_count, 2)
            self.assertEqual(xy.set_amplitude.call_args_list,
                             [call(3.0, channel=1), call(3.0, channel=2),
                              call(0.5, channel=1), call(0.5, channel=2),
                              call(1.0, channel=1), call(1.0, channel=2)])
            self.assertEqual(xy.set_burst_phase.call_args_list[-4:],
                             [call(80.0, channel=1), call(170.0, channel=2)] * 2)
            hw.devices["dg_sweep"].phase_init.assert_called_once_with(channel=2)
            self.assertEqual([c for c in hw.devices["dg_sweep"].set_output.call_args_list
                              if c == call(True, channel=2)], [call(True, channel=2)])
            for entry in hw.acquire.call_args_list:
                self.assertEqual(entry.args[1].grid_cols, 160)
                self.assertEqual(entry.kwargs["actual_rate"], 800.0)
            hw.rates.assert_called()
        config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(params.to_external(), original)
        self.assertEqual(config["schema_version"], 3)
        self.assertEqual(config["parameters"], original)
        self.assertEqual(config["xy_ctrl"]["XY_CTRL_PHASE_deg"], 80.0)
        self.assertEqual(config["hf2_daq"]["actual_rate_Sa_s"], 800.0)
        self.assertEqual(config["hf2_daq"]["nperseg"], 16)
        self.assertEqual(config["clock_sources"], {"verified": True})
        for key in ("device_library_snapshot", "physical_mapping_snapshot", "mapping_snapshot"):
            self.assertTrue(config[key])
        self.assertEqual(config["data_files"], ["raw/control_scan_axes.npz"] +
                         [f"raw/waveform_C{i:04d}.npy" for i in range(3)])
        with np.load(run_dir / "raw/control_scan_axes.npz") as axes:
            np.testing.assert_allclose(axes["xy_envelope_voltage_V"], [0, .25, .5])
        for i in range(3):
            self.assertEqual(np.load(run_dir / f"raw/waveform_C{i:04d}.npy").shape, (160,))

    def test_repeated_run_does_not_mutate_or_share_calibration_state(self):
        params = scan_params()
        with patch.object(acquisition, "datetime") as clock:
            clock.now.side_effect = [datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 12, 1)]
            paths = []
            for _ in range(2):
                with fake_hardware(self.root) as hw:
                    paths.append(workflow.run(params, project_root=self.root))
                    self.assertEqual(hw.devices["dg_comp"].set_burst_phase.call_args_list[0],
                                     call(90.0, channel=1))
                    self.assert_shutdown(hw)
        self.assertNotEqual(*paths)
        self.assertEqual(params.xy_ctrl_phase, 90.0)

    def test_invalid_parameters_never_connect(self):
        for params in (replace(scan_params(), target_noise_freq_points=1),
                       replace(scan_params(), main_magnetic_field=self.limits["main_magnetic_field"]["max"] + 1),
                       replace(scan_params(), xy_ctrl_phase=float("nan")),
                       replace(scan_params(), rf_gate_offset=20.0),
                       replace(scan_params(), xy_trigger_amplitude=2 * (self.limits["Time_sequence_2"]["max"] + 4))):
            with self.subTest(params=params), patch.object(preparation, "connect_instruments") as connect:
                with self.assertRaises(ValueError):
                    workflow.run(params, project_root=self.root)
                connect.assert_not_called()
        self.assertFalse((self.root / "data").exists())

    def test_cancel_file_reaches_wait_and_shared_step_checkpoints(self):
        cancel_file = self.root / "cancel"
        cancel_file.touch()
        with patch.dict(os.environ, {"LAB_CANCEL_FILE": str(cancel_file)}):
            with self.assertRaises(WorkflowCancelled):
                workflow.wait(0.1)
            with self.assertRaises(WorkflowCancelled):
                workflow._RuntimeCancellation().raise_if_cancelled()
            with patch.object(preparation, "connect_instruments") as connect:
                with self.assertRaises(WorkflowCancelled):
                    workflow.run(scan_params(), project_root=self.root)
                connect.assert_not_called()

    def test_connection_and_clock_failures_release_connections(self):
        for stage in ("connect", "clock", "interrupt"):
            with self.subTest(stage=stage), fake_hardware(self.root) as hw:
                error = KeyboardInterrupt() if stage == "interrupt" else RuntimeError(stage)
                if stage == "connect":
                    hw.devices["hf2"].connect.side_effect = error
                else:
                    hw.clocks.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    workflow.run(scan_params(), project_root=self.root)
                self.assertIs(caught.exception, error)
                hw.shutdown.assert_not_called()
                for name, device in hw.devices.items():
                    if stage == "connect" and name == "hf2":
                        continue
                    device.disconnect.assert_called_once()
                hw.devices["gs200"].set_current.assert_not_called()

    def test_failures_cancel_and_interrupt_always_shutdown_once(self):
        stages = ("initialize", "hf2_phase", "xy_phase", "scan", "save_axes", "save_wave",
                  "cancel_daq", "interrupt_daq", "bad_actual_rate", "progress", "interrupt_progress_close")
        for stage in stages:
            with self.subTest(stage=stage), fake_hardware(self.root) as hw, ExitStack() as stack:
                error = (WorkflowCancelled(stage) if stage == "cancel_daq" else
                         KeyboardInterrupt() if stage.startswith("interrupt_") else RuntimeError(stage))
                sample = dict(r=1.0, x=1.0, y=0.0, theta=0.0)
                if stage == "initialize":
                    hw.devices["gs200"].set_current.side_effect = error
                elif stage == "hf2_phase":
                    hw.samples.side_effect = error
                elif stage == "xy_phase":
                    hw.samples.side_effect = [sample, sample, error]
                elif stage == "scan":
                    hw.devices["dg_comp"].set_amplitude.side_effect = error
                elif stage in ("save_axes", "save_wave"):
                    stack.enter_context(patch.object(workflow.np, "savez" if stage == "save_axes" else "save",
                                                    side_effect=error))
                elif stage in ("cancel_daq", "interrupt_daq"):
                    hw.acquire.side_effect = error
                elif stage == "bad_actual_rate":
                    hw.rates.side_effect = [1200.0, 1.0]
                elif stage == "progress":
                    hw.progress.side_effect = error
                elif stage == "interrupt_progress_close":
                    hw.progress.return_value.close.side_effect = error
                with self.assertRaises(ValueError if stage == "bad_actual_rate" else type(error)) as caught:
                    workflow.run(scan_params(), project_root=self.root)
                if stage != "bad_actual_rate":
                    self.assertIs(caught.exception, error)
                self.assert_shutdown(hw)
                self.assertLessEqual(hw.rates.call_count, 2)  # 异常路径不恢复 HF2 校相配置。
                if stage == "xy_phase":
                    self.assertEqual(hw.devices["dg_comp"].set_output.call_args_list[-2:],
                                     [call(False, channel=1), call(False, channel=2)])
                    # 校相测量前只开启一次，不在 finally 中重新开启。
                    self.assertEqual(sum(entry == call(True, channel=1)
                                         for entry in hw.devices["dg_comp"].set_output.call_args_list), 1)

    def test_config_write_failures_and_cleanup_errors_keep_original_exception(self):
        dump = yaml.safe_dump
        for write_number in (1, 2):
            with self.subTest(write_number=write_number), fake_hardware(self.root) as hw:
                error = OSError("模拟配置写入失败")
                writes = count(1)
                def save_config(*args, **kwargs):
                    if next(writes) == write_number:
                        raise error
                    return dump(*args, **kwargs)
                original_save = acquisition.save_config
                def write_config(*args, **kwargs):
                    if next(writes) == write_number:
                        raise error
                    return original_save(*args, **kwargs)
                with patch.object(acquisition, "save_config", side_effect=write_config):
                    with self.assertRaises(OSError) as caught:
                        workflow.run(scan_params(), project_root=self.root)
                self.assertIs(caught.exception, error)
                self.assert_shutdown(hw)
        with fake_hardware(self.root) as hw:
            error = WorkflowCancelled("原始取消")
            hw.acquire.side_effect = error
            hw.progress.return_value.close.side_effect = RuntimeError("进度条关闭失败")
            hw.devices["dg_comp"].setup_dc.side_effect = RuntimeError("归零失败")
            with self.assertRaises(WorkflowCancelled) as caught:
                workflow.run(scan_params(), project_root=self.root)
            self.assertIs(caught.exception, error)
            self.assert_shutdown(hw)
        with fake_hardware(self.root) as hw:
            error = RuntimeError("原始初始化错误")
            hw.devices["gs200"].set_current.side_effect = error
            hw.shutdown.side_effect = RuntimeError("收尾意外错误")
            with self.assertRaises(RuntimeError) as caught:
                workflow.run(scan_params(), project_root=self.root)
            self.assertIs(caught.exception, error)
            hw.shutdown.assert_called_once()

    def test_missing_tec_remains_optional(self):
        with fake_hardware(self.root) as hw:
            hw.devices["tec"].connect.side_effect = OSError("TEC 被外部软件占用")
            run_dir = workflow.run(scan_params(), project_root=self.root)
            self.assertTrue((run_dir / "raw/waveform_C0002.npy").exists())
            hw.shutdown.assert_called_once()
            hw.devices["tec"].disconnect.assert_not_called()

    def test_daq_failure_nan_rows_and_old_configs_remain_readable(self):
        from .analysis import analyze, compute_psd
        with fake_hardware(self.root) as hw:
            waveform = np.random.default_rng(9).normal(size=160)
            hw.acquire.side_effect = [[SimpleNamespace(values=waveform)], OSError("单点失败"),
                                     [SimpleNamespace(values=waveform * 2)]]
            run_dir = workflow.run(scan_params(), project_root=self.root)
            self.assert_shutdown(hw)
        config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        self.assertTrue(np.isnan(np.load(run_dir / "raw/waveform_C0001.npy")).all())
        for version in (3, 2):
            with self.subTest(schema_version=version):
                config["schema_version"] = version
                if version == 2:
                    config["scan_params"].pop("xy_envelope_voltage_V")
                    config["scan_params"].update(XY_AMP_START_V=0.0, XY_AMP_STOP_V=.5, XY_AMP_POINTS=3)
                matrix, frequency, voltage, rows, info = compute_psd(run_dir, config, {"bin_width_hz": 50.0})
                np.testing.assert_array_equal(rows, [0, 2])
                self.assertTrue(np.isnan(matrix[1]).all())
                np.testing.assert_allclose(matrix[2], 4 * matrix[0])
                np.testing.assert_allclose(voltage, [0, .25, .5])
                self.assertEqual(frequency[-1], 400.0)
                self.assertEqual(info["nperseg"], 16)
        # 少量假波形不足科学标定，但实际分析必须保存 PSD 和失败诊断。
        with redirect_stdout(StringIO()), self.assertRaisesRegex(ValueError, "至少需要 20"):
            analyze(run_dir)
        summary = json.loads((run_dir / "results/analysis_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "quality_failed")
        self.assertTrue((run_dir / "results/psd_matrix.npz").is_file())
        self.assertTrue((run_dir / "results/analysis_summary.json").is_file())

    def test_main_passes_typed_parameters(self):
        from . import ADAPTER, DEFINITION
        from .definition import ADAPTER as registered_adapter

        self.assertIs(ADAPTER, registered_adapter)
        self.assertEqual(DEFINITION.id, "noise-spectrum-xy")
        params = scan_params()
        with patch.object(workflow, "load_runtime_params", return_value=params) as load, \
             patch.object(workflow, "run") as run:
            workflow.main()
        load.assert_called_once_with(NoiseSpectrumXYParams)
        run.assert_called_once_with(params)

    def test_import_is_inert_outside_repository_without_runtime_environment(self):
        code = """
import importlib, os, sys
from pathlib import Path
from unittest.mock import patch
import matplotlib
import lab_workflows.common
import lab_workflows.experiment_runtime
import gs200, tec_controller, lockin_amplifier
before = sys.path.copy()
with patch('lab_workflows.common.load_mapping', side_effect=AssertionError('导入时读取映射')), \
     patch('lab_workflows.common.load_safety_limits', side_effect=AssertionError('导入时读取限值')), \
     patch('lab_workflows.experiment_runtime.load_runtime_params', side_effect=AssertionError('导入时读取参数')), \
     patch('gs200.GS200Instrument', side_effect=AssertionError('导入时连接设备')), \
     patch('tec_controller.TECInstrument', side_effect=AssertionError('导入时连接设备')), \
     patch('lockin_amplifier.HF2Instrument', side_effect=AssertionError('导入时连接设备')), \
     patch('matplotlib.use', side_effect=AssertionError('导入时修改绘图后端')):
    importlib.import_module('lab_workflows.experiment_modules.noise_spectrum_xy.workflow')
assert sys.path == before
assert not list(Path.cwd().iterdir())
"""
        env = {key: value for key, value in os.environ.items() if not key.startswith("LAB_")}
        with tempfile.TemporaryDirectory(prefix="xy-import-test-") as cwd:
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=cwd,
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")

    def test_actual_module_entry_in_subprocess_writes_real_files(self):
        code = """
import runpy, os
from pathlib import Path
from lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode import fake_hardware
root = Path(os.environ['XY_TEST_ROOT'])
with fake_hardware(root):
    runpy.run_module('lab_workflows.experiment_modules.noise_spectrum_xy.workflow', run_name='__main__')
"""
        # 子进程通过显式测试补丁将模块位置解析到临时根目录。
        code = code.replace("with fake_hardware(root):", "from unittest.mock import patch\nwith fake_hardware(root), patch('lab_workflows.common.find_project_root', return_value=root):")
        env = dict(os.environ, XY_TEST_ROOT=str(self.root), LAB_TYPED_PARAMETERS=json.dumps(scan_params().to_external()))
        env.pop("LAB_CANCEL_FILE", None)
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=self.root,
                                env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        runs = list((self.root / "data/Noise_Spectrum_XY_Ctrl").iterdir())
        self.assertEqual(len(runs), 1)
        config = yaml.safe_load((runs[0] / "experiment_config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["parameters"], scan_params().to_external())
        self.assertEqual(config["hf2_daq"]["actual_rate_Sa_s"], 800.0)
        self.assertEqual(config["xy_ctrl"]["XY_CTRL_PHASE_deg"], 80.0)
        for relative in config["data_files"]:
            self.assertTrue((runs[0] / relative).is_file())
        self.assertEqual(np.load(runs[0] / "raw/waveform_C0002.npy").shape, (160,))

    def test_restore_wait_default_and_validation(self) -> None:
        legacy = NoiseSpectrumXYParams.from_external({}, schema_version=2)
        self.assertEqual(legacy.temp_switch_on_settle_s, 2.0)
        params = NoiseSpectrumXYParams.from_external({"TEMP_SWITCH_ON_SETTLE_S": -0.1})
        self.assertTrue(any("TEMP_SWITCH_ON_SETTLE_S" in error for error in params.validate()))
        with self.assertRaises(ValueError):
            NoiseSpectrumXYParams.from_external({"TEMP_SWITCH_ON_SETTLE_S": float("nan")})

    def test_v1_direct_aw_parameters_are_migrated_away(self) -> None:
        values = NoiseSpectrumXYParams().to_external()
        values.update({name: 1 for name in AW_KEYS})
        migrated = NoiseSpectrumXYParams.from_external(values, schema_version=1)
        self.assertTrue(AW_KEYS.isdisjoint(migrated.to_external()))


if __name__ == "__main__":
    unittest.main()
