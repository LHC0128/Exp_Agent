"""直流色散、取消、安全恢复和离线入口的无硬件回归。"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import yaml
from lockin_amplifier import daq, lockin_capabilities

from ...common import WorkflowCancelled
from ...steps.run_directory import RunDirectory
from ...steps.dispersion import acquire_dispersion_point
from ...steps import dispersion
from ...steps.test_dispersion import FakeHF2
from . import acquisition, analysis, analysis_steps, workflow, preparation, excitation, phase
from .definition import ADAPTER
from .models import StaticSensitivityParams
from .test_support import fake_rig


class FakeGenerator:
    """记录开关时序，setup_dc 与真实驱动一样隐式打开输出。"""

    def __init__(self):
        self.events = []
        self.output = False
        self.voltage = 0.0

    def setup_dc(self, voltage, channel=None):
        self.voltage, self.output = voltage, True
        self.events.append(("dc", voltage, channel))

    def set_output(self, enabled, channel=None):
        self.output = enabled
        self.events.append(("output", enabled, channel))

    def set_burst_state(self, enabled, channel=None):
        self.events.append(("burst", enabled, channel))

    def set_mod_state(self, enabled, channel=None):
        self.events.append(("mod", enabled, channel))

    def set_sync_state(self, enabled, channel=None):
        self.events.append(("sync", enabled, channel))


def make_run(root):
    """合成色散和原始噪声；只写临时目录。"""
    raw = root / "raw"
    raw.mkdir()
    offsets = np.linspace(-0.2, 0.2, 81)
    popt = (0.0003, 0.025, 0.007, 0.001)
    y = analysis_steps._dispersive(offsets, *popt)
    acquisition.save_scan(raw / "dispersion_scan.npz", dict(
        schema_version=np.array(3), offsets_v=offsets, x_median=np.ones_like(y), y_median=y,
        r_median=np.hypot(1, y), completed_mask=np.ones(y.size, bool),
    ))
    noise = raw / "noise_raw" / "gs200_range_10mA"
    noise.mkdir(parents=True)
    rng = np.random.default_rng(42)
    np.save(noise / "noise_0000.npy", rng.normal(0, 1e-4, 1000))
    config = dict(experiment_id="static-sensitivity", schema_version=3, acquisition_method="dc_step",
                  timestamp="test", run_tag="test", parameters=StaticSensitivityParams(z_v_to_nt=1950, dispersion_steps=81, dispersion_start_v=-0.2, dispersion_stop_v=0.2).to_external(),
                  actual_rates={"hf2_noise_rate_Sa_s": 1000},
                  gs200_range_measurements=[dict(directory=noise.name, current_range_set_A=0.01,
                                                 current_range_actual_A=0.01)])
    (root / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return popt


ACQUISITION_CASES = {
    "static-sensitivity": {
        "mode": "grouped",
        "cases": {
            "order": ["DCScanTests::test_real_grouped_order_slow_disk_and_last_group", "DCScanTests::test_noise_order_real_writers_and_ui_temperature"],
            "slow_disk": ["DCScanTests::test_real_grouped_order_slow_disk_and_last_group", "DCScanTests::test_noise_order_real_writers_and_ui_temperature"],
            "save_failure": ["DCScanTests::test_write_failure_preserves_other_group_files"],
            "group_change": ["DCScanTests::test_median_axis_groups_and_no_sync_trigger"],
            "last_group": ["DCScanTests::test_median_axis_groups_and_no_sync_trigger"],
            "cancel": ["DCScanTests::test_partial_read_and_cancel_preserve_data", "DCScanTests::test_noise_failures_restore_before_partial_save"],
            "acquire_failure": ["DCScanTests::test_partial_read_and_cancel_preserve_data", "DCScanTests::test_noise_failures_restore_before_partial_save"],
            "partial_save": ["DCScanTests::test_partial_read_and_cancel_preserve_data", "DCScanTests::test_noise_failures_restore_before_partial_save"],
            "restore_failure": ["DCScanTests::test_cancel_during_wait_and_restoration_failure", "DCScanTests::test_restore_and_save_failures_keep_original_read_error", "DCScanTests::test_noise_failures_restore_before_partial_save"],
            "entrypoints": ["DCScanTests::test_full_workflow_uses_selected_demod_and_preserves_settings"],
        },
    },
}


class DCScanTests(unittest.TestCase):
    def test_parameters_and_saved_defaults(self):
        saved = ADAPTER.defaults()
        self.assertEqual(saved.validate(), [])
        self.assertEqual(saved.schema_version, StaticSensitivityParams.schema_version)
        fields = {item["name"] for item in saved.schema()["fields"]}
        self.assertIn("dispersion_steps", fields)
        self.assertIn("dispersion_duration_s", fields)
        self.assertFalse(fields & {"dispersion_samples_per_step", "dispersion_sample_interval_s"})
        self.assertFalse(fields & {"dispersion_fit_skip_start", "dispersion_fit_skip_end"})
        self.assertFalse(fields & {"ramp_freq", "ramp_symmetry", "daq_duration", "daq_trigger_channel", "time_sequence"})
        for overrides in (dict(dispersion_steps=6), dict(system_settle_s=-1,),
                          dict(dispersion_start_v=11), dict(dispersion_stop_v=-1),
                          dict(dispersion_duration_s=0),
                          dict(dispersion_duration_s=float("nan")), dict(hf2_demod_rate=float("inf")),
                          dict(hf2_demod_rate=1, dispersion_duration_s=0.1), dict(temp_switch=0), dict(hf2_demod_idx=6),
                          dict(run_tag="../越界"), dict(gs200_current_ranges=[0.01, 0.01]),
                          dict(gs200_current_ranges=[0.0100000001, 0.0100000002])):
            with self.subTest(overrides=overrides):
                self.assertTrue(replace(saved, **overrides).validate())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "params.yaml"
            updated = replace(saved, dispersion_steps=61)
            updated.save_yaml(path)
            self.assertEqual(StaticSensitivityParams.from_yaml(path).to_external(), updated.to_external())
            for key in ("pump_laser_power", "probe_laser_power", "main_magnetic_field", "z_v_to_nt", "hf2_ac_coupling", "noise_n_avg"):
                self.assertEqual(updated.to_external()[key], saved.to_external()[key])

    def test_median_axis_groups_and_no_sync_trigger(self):
        for interval, group_starts, recovers in ((5, 3, 2), (0, 1, 0)):
            with self.subTest(interval=interval), tempfile.TemporaryDirectory() as tmp:
                z, temp = FakeGenerator(), FakeGenerator()
                params = StaticSensitivityParams(dispersion_start_v=-0.1, dispersion_stop_v=0.2,
                                                 dispersion_steps=11, hf2_demod_rate=30, dispersion_duration_s=0.1,
                                                 dispersion_temp_switch_interval_points=interval, hf2_demod_idx=2)
                path = Path(tmp) / "dispersion.npz"
                hf2 = FakeHF2()
                with patch.object(acquisition, "wait_for_settle") as wait:
                    acquisition.acquire_dispersion(params, z, 1, temp, 2, hf2, path)
                with np.load(path, allow_pickle=False) as data:
                    np.testing.assert_allclose(data["offsets_v"], np.linspace(-0.1, 0.2, 11))
                    np.testing.assert_equal(data["y_median"], 2)
                    self.assertTrue(data["completed_mask"].all())
                    np.testing.assert_equal(data["sample_counts"], 3)
                    self.assertTrue(np.isfinite(data["point_finished_unix_s"]).all())
                with np.load(path.parent / "dispersion_steps" / "point_0000.npz") as point:
                    np.testing.assert_equal(point["y_samples"], [1, 100, 2])
                self.assertEqual(sum(event[:2] == ("dc", 0.0) for event in temp.events), group_starts)
                self.assertFalse(any(event[:2] == ("output", False) for event in temp.events))
                self.assertEqual(sum(call.args == (params.dispersion_temp_switch_on_settle_s,) for call in wait.call_args_list), recovers)
                self.assertEqual(len(hf2.modules), 11)
                self.assertTrue(all(module.subscribed[0] == "/devtest/demods/2/sample.x" for module in hf2.modules))
                self.assertEqual(z.voltage, 0)
                self.assertFalse(z.output)
                self.assertTrue(temp.output)
                self.assertEqual(temp.voltage, 5)
                self.assertFalse(any(event[:2] == ("sync", True) for event in z.events))

    def test_partial_read_and_cancel_preserve_data(self):
        for fault in (WorkflowCancelled("测试取消"), ValueError("测试读数故障"), None):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "scan.npz"
                params = StaticSensitivityParams(dispersion_steps=7, hf2_demod_rate=30)
                z, temp = FakeGenerator(), FakeGenerator()
                def raw_factory(index, raw):
                    if index == 1 and fault is None:
                        raw["/devtest/demods/0/sample.y"][0]["value"][0, 1] = np.nan
                    return raw
                hf2 = FakeHF2(raw_factory=raw_factory, fault=fault, fault_at=1)
                with patch.object(acquisition, "wait_for_settle"):
                    with self.assertRaises((WorkflowCancelled, ValueError)):
                        acquisition.acquire_dispersion(params, z, 1, temp, 2, hf2, path)
                with np.load(path) as data:
                    self.assertEqual(data["completed_mask"].sum(), 1)
                    self.assertTrue(np.isnan(data["y_median"][1:]).all())
                with np.load(path.parent / "dispersion_steps" / "point_0001.npz") as point:
                    self.assertEqual(point["sample_count"], 3)
                    self.assertFalse(point["completed"])
                self.assertFalse(z.output)
                self.assertEqual(z.voltage, 0)
                self.assertTrue(temp.output)
                self.assertEqual(temp.voltage, 5)

    def test_real_grouped_order_slow_disk_and_last_group(self):
        for delay in (0, 0.002):
            with self.subTest(delay=delay), tempfile.TemporaryDirectory() as tmp:
                events = []
                z, temp = FakeGenerator(), FakeGenerator()
                real_temp_dc, real_z_dc = temp.setup_dc, z.setup_dc
                def temp_dc(voltage, channel=None):
                    events.append(("gate", voltage))
                    real_temp_dc(voltage, channel=channel)
                def z_dc(voltage, channel=None):
                    events.append(("configure", voltage))
                    real_z_dc(voltage, channel=channel)
                temp.setup_dc, z.setup_dc = temp_dc, z_dc
                def raw_factory(index, raw):
                    self.assertTrue(temp.output)
                    self.assertEqual(temp.voltage, 0)
                    self.assertTrue(z.output)
                    events.append(("read", index))
                    return raw
                real_save = dispersion.save_scan
                def save(path, data):
                    self.assertEqual((temp.voltage, temp.output), (5, True))
                    events.append(("write", path.name))
                    time.sleep(delay)
                    real_save(path, data)
                    self.assertEqual((temp.voltage, temp.output), (5, True))
                params = StaticSensitivityParams(dispersion_steps=7, dispersion_temp_switch_interval_points=3,
                                                 hf2_demod_rate=30, system_settle_s=0.25)
                path = Path(tmp) / "scan.npz"
                with patch.object(acquisition, "wait_for_settle", side_effect=lambda seconds: events.append(("wait", seconds))), \
                     patch.object(dispersion, "save_scan", side_effect=save), patch.object(acquisition, "save_scan", side_effect=save):
                    acquisition.acquire_dispersion(params, z, 1, temp, 2, FakeHF2(raw_factory=raw_factory), path)
                starts = [index for index, event in enumerate(events) if event == ("gate", 0)]
                ends = [index for index, event in enumerate(events) if event == ("gate", 5)]
                self.assertEqual(len(starts), 3)
                self.assertEqual(len(ends), 3)
                for group, (start, end) in enumerate(zip(starts, ends)):
                    expected = list(range(group * 3, min(group * 3 + 3, 7)))
                    self.assertEqual([event[1] for event in events[start:end] if event[0] == "read"], expected)
                    self.assertFalse(any(event[0] == "write" for event in events[start:end]))
                    self.assertEqual(events[start - 1][0], "configure")
                    for index in range(start, end):
                        if events[index][0] == "read":
                            self.assertEqual(events[index - 1], ("wait", params.system_settle_s))
                for index in range(7):
                    with np.load(path.parent / "dispersion_steps" / f"point_{index:04d}.npz") as point:
                        self.assertTrue(point["completed"])
                with np.load(path) as scan:
                    self.assertTrue(scan["completed_mask"].all())

    def test_write_failure_preserves_other_group_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.npz"
            z, temp, reads = FakeGenerator(), FakeGenerator(), []
            real_save = dispersion.save_scan
            def save(target, data):
                self.assertEqual((temp.voltage, temp.output), (5, True))
                if target.name == "point_0001.npz":
                    raise OSError("本组磁盘失败")
                real_save(target, data)
            def raw_factory(index, raw):
                reads.append(index)
                return raw
            params = StaticSensitivityParams(dispersion_steps=7, dispersion_temp_switch_interval_points=3, hf2_demod_rate=30)
            with patch.object(acquisition, "wait_for_settle"), patch.object(dispersion, "save_scan", side_effect=save), \
                 patch.object(acquisition, "save_scan", side_effect=save), self.assertRaisesRegex(OSError, "本组磁盘失败"):
                acquisition.acquire_dispersion(params, z, 1, temp, 2, FakeHF2(raw_factory=raw_factory), path)
            self.assertEqual(reads, [0, 1, 2])
            self.assertTrue((path.parent / "dispersion_steps/point_0000.npz").is_file())
            self.assertTrue((path.parent / "dispersion_steps/point_0002.npz").is_file())
            with np.load(path) as scan:
                self.assertEqual(scan["completed_mask"].sum(), 3)
            self.assertEqual((z.voltage, z.output, temp.voltage, temp.output), (0, False, 5, True))

    def test_restore_and_save_failures_keep_original_read_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.npz"
            z, temp, attempts = FakeGenerator(), FakeGenerator(), []
            real_temp_dc, real_save = temp.setup_dc, dispersion.save_scan
            def temp_dc(voltage, channel=None):
                if voltage == 5:
                    attempts.append("restore")
                    raise RuntimeError("温控恢复失败")
                real_temp_dc(voltage, channel=channel)
            temp.setup_dc = temp_dc
            def save(target, data):
                self.assertTrue(attempts)
                if target.name == "point_0000.npz":
                    raise OSError("部分磁盘失败")
                real_save(target, data)
            with patch.object(acquisition, "wait_for_settle"), patch.object(dispersion, "save_scan", side_effect=save), \
                 patch.object(acquisition, "save_scan", side_effect=save), self.assertRaisesRegex(ValueError, "原始读数故障"):
                acquisition.acquire_dispersion(StaticSensitivityParams(dispersion_steps=7, hf2_demod_rate=30),
                    z, 1, temp, 2, FakeHF2(fault=ValueError("原始读数故障"), fault_at=1), path)
            self.assertFalse(z.output)
            with np.load(path) as scan:
                self.assertEqual(scan["completed_mask"].sum(), 1)
            with np.load(path.parent / "dispersion_steps/point_0001.npz") as point:
                self.assertFalse(point["completed"])
                self.assertIn("原始读数故障", str(point["error"]))

    def test_cancel_during_wait_and_restoration_failure(self):
        with patch.object(acquisition, "check_cancelled", side_effect=WorkflowCancelled("等待取消")):
            with self.assertRaises(WorkflowCancelled):
                acquisition.wait_for_settle(30)
        with tempfile.TemporaryDirectory() as tmp:
            z, temp = FakeGenerator(), FakeGenerator()
            def fail_recovery(device, channel, enabled):
                acquisition.set_temperature_switch(device, enabled, channel=channel)
                if enabled:
                    raise RuntimeError("分组恢复失败")
            with patch.object(acquisition, "wait_for_settle"), patch.object(acquisition, "set_temp_switch", side_effect=fail_recovery):
                with self.assertRaisesRegex(RuntimeError, "分组恢复失败"):
                    acquisition.acquire_dispersion(StaticSensitivityParams(), z, 1, temp, 2, FakeHF2(), Path(tmp) / "scan.npz")
            self.assertFalse(z.output)
            self.assertTrue(temp.output)
            with np.load(Path(tmp) / "scan.npz") as data:
                self.assertEqual(data["completed_mask"].sum(), 5)

    def test_noise_order_real_writers_and_ui_temperature(self):
        params = StaticSensitivityParams(dispersion_steps=7, noise_n_avg=2, noise_duration=0.1,
                                         hf2_noise_rate=10000, gs200_current_ranges=[0.01, 0.1])
        with tempfile.TemporaryDirectory() as tmp, fake_rig(Path(tmp), params) as rig:
            ctx = workflow.Workflow(params)
            from .stages import ACQUISITION
            ctx.execute([stage.id for stage in ACQUISITION if stage.id <= "A10"])
            target = rig.mapping["Temp_Switch"]
            temp = rig.generators[target["resource"]]
            channel, events = target["channel"], []
            original_dc = temp.setup_dc
            def gate(voltage, channel=1):
                events.append(("gate", voltage))
                original_dc(voltage, channel=channel)
            temp.setup_dc = gate
            original_read = daq.acquire_data.side_effect
            def read(*args, **kwargs):
                self.assertEqual((temp.dc[channel], temp.output[channel]), (0, True))
                events.append(("read", None))
                return original_read(*args, **kwargs)
            original_save, original_metadata, original_update = np.save, np.savez, RunDirectory.update_config
            def save(path, values):
                self.assertEqual((temp.dc[channel], temp.output[channel]), (5, True))
                events.append(("write", Path(path).name))
                time.sleep(0.002)
                original_save(path, values)
            def metadata(path, **values):
                self.assertEqual((temp.dc[channel], temp.output[channel]), (5, True))
                events.append(("metadata", Path(path).name))
                original_metadata(path, **values)
            def update(directory, **values):
                self.assertEqual((temp.dc[channel], temp.output[channel]), (5, True))
                events.append(("config", None))
                return original_update(directory, **values)
            try:
                with patch.object(daq, "acquire_data", side_effect=read), patch.object(np, "save", side_effect=save), \
                     patch.object(np, "savez", side_effect=metadata), \
                     patch.object(RunDirectory, "update_config", autospec=True, side_effect=update), \
                     patch.object(acquisition, "wait_for_settle", side_effect=lambda seconds: events.append(("wait", seconds))):
                    ctx.execute(["A11"])
                self.assertEqual(sum(event[0] == "read" for event in events), 4)
                for index, event in enumerate(events):
                    if event[0] == "read":
                        self.assertEqual(events[index - 2:index], [("gate", 0), ("wait", params.system_settle_s)])
                        self.assertEqual(events[index + 1], ("gate", 5))
                        self.assertEqual(events[index + 2][0], "write")
                files = sorted(ctx.directory.raw.rglob("noise_*.npy"))
                self.assertEqual(len(files), 4)
                self.assertTrue(all(path.with_name(path.stem + "_acquisition.npz").is_file() for path in files))
                self.assertTrue(all(np.isfinite(np.load(path)).all() for path in files))
                rig.tec.set_target_temperature.assert_called_once_with(params.temperature, channel=int(rig.mapping["temperature"]["channel"]))
                rig.tec.set_enable.assert_not_called()
                rig.tec.get_temperature.assert_not_called()
                self.assertEqual(ctx.state["temperature_control"]["target_temperature_c"], params.temperature)
                self.assertIsNone(ctx.state["temperature_control"]["actual_temperature_c"])
                self.assertEqual(ctx.state["temperature_control"]["control_source"], "tec103")
            finally:
                ctx.finish()

    def test_noise_failures_restore_before_partial_save(self):
        for fault in ("cancel", "acquire", "restore", "save"):
            params = StaticSensitivityParams(dispersion_steps=7, noise_n_avg=3, noise_duration=0.1, hf2_noise_rate=10000)
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp, fake_rig(Path(tmp), params) as rig:
                ctx = workflow.Workflow(params)
                from .stages import ACQUISITION
                ctx.execute([stage.id for stage in ACQUISITION if stage.id <= "A10"])
                route = rig.mapping["Temp_Switch"]
                temp, channel = rig.generators[route["resource"]], route["channel"]
                original_read, original_save, original_dc = daq.acquire_data.side_effect, np.save, temp.setup_dc
                reads, restores = [], []
                def gate(voltage, channel=1):
                    if voltage == 5:
                        restores.append(len(reads))
                        if fault == "restore" and len(reads) == 2:
                            raise RuntimeError("噪声温控恢复失败")
                    original_dc(voltage, channel=channel)
                temp.setup_dc = gate
                def read(*args, **kwargs):
                    reads.append(len(reads))
                    if len(reads) == 2 and fault in ("cancel", "acquire"):
                        raise WorkflowCancelled("噪声取消") if fault == "cancel" else ValueError("噪声读数失败")
                    return original_read(*args, **kwargs)
                def save(path, values):
                    self.assertEqual(restores[-1], len(reads))
                    if fault != "restore":
                        self.assertEqual((temp.dc[channel], temp.output[channel]), (5, True))
                    if fault == "save" and Path(path).name == "noise_0001.npy":
                        raise OSError("噪声写盘失败")
                    original_save(path, values)
                error = WorkflowCancelled if fault == "cancel" else ValueError if fault == "acquire" else RuntimeError if fault == "restore" else OSError
                try:
                    with patch.object(daq, "acquire_data", side_effect=read), patch.object(np, "save", side_effect=save), self.assertRaises(error):
                        ctx.execute(["A11"])
                    self.assertEqual(len(reads), 2)
                    files = sorted(ctx.directory.raw.rglob("noise_*.npy"))
                    self.assertEqual(len(files), 2 if fault == "restore" else 1)
                    self.assertTrue(np.isfinite(np.load(files[0])).all())
                    self.assertNotEqual(ctx.states["A11"]["status"], "completed")
                finally:
                    temp.setup_dc = original_dc
                    ctx.finish()

    def test_clock_and_phase_settle_can_cancel_without_changing_default_wait(self):
        from ...steps import clock, phase
        cfg = dict(instrument="signal_generator", resource="USB0::123::456::test-clock::INSTR", model="DG4000")
        dg = SimpleNamespace(get_ref_clock_source=Mock(return_value="INT"), set_ref_clock_source=Mock())
        with patch.object(acquisition, "check_cancelled", side_effect=WorkflowCancelled("稳定等待取消")):
            with self.assertRaises(WorkflowCancelled):
                clock.synchronize_connected_clocks({"dg": dg}, {"z": cfg}, {"dg": "z"}, profile={"__default__": "EXT"}, settle_wait=acquisition.wait_for_settle)
            hf2 = Mock()
            with patch.object(phase.demod, "read_demod_sample", return_value=dict(x=1, y=0, r=1, theta=0)) as read:
                with self.assertRaises(WorkflowCancelled):
                    phase.calibrate_demod_phase(hf2, phase.PhaseCalibrationConfig(), settle_wait=acquisition.wait_for_settle)
                read.assert_called_once()
                hf2.set_double.assert_not_called()
        dg.get_ref_clock_source.side_effect = ["INT", "EXT"]
        with patch.object(clock.time, "sleep") as sleep:
            record = clock.synchronize_clock_device(dg, cfg, profile={"__default__": "EXT"})
            self.assertTrue(record.ok)
            sleep.assert_called_once_with(0.2)
        hf2.get_double.return_value = 17.0
        with patch.object(phase.demod, "read_demod_sample", return_value=dict(x=1, y=0, r=1, theta=0)), patch.object(phase.time, "sleep") as sleep:
            result = phase.calibrate_demod_phase(hf2, phase.PhaseCalibrationConfig())
            self.assertEqual(result.phase_shift_deg, 17.0)
            sleep.assert_called_once_with(0.2)

    def test_phase_rejects_nonfinite_readings_before_writing_phase(self):
        from ...steps import phase
        valid = dict(x=1, y=0, r=1, theta=0)
        invalid = dict(valid, r=float("nan"))
        for readings, angle in (([invalid], 17.0), ([valid, invalid], 17.0), ([valid, valid], float("nan"))):
            with self.subTest(readings=readings):
                hf2 = Mock()
                hf2.get_double.return_value = angle
                with patch.object(phase.demod, "read_demod_sample", side_effect=readings), patch.object(phase.time, "sleep"):
                    with self.assertRaisesRegex(ValueError, "非有限"):
                        phase.calibrate_demod_phase(hf2, phase.PhaseCalibrationConfig())
                hf2.set_double.assert_not_called()

    def test_daq_cancel_closes_module_and_default_unchanged(self):
        module = Mock()
        instr = Mock()
        instr.device_id = "dev"
        instr.model = "HF2"
        instr.acquisition_mode = "auto"
        instr.capabilities = lockin_capabilities("HF2")
        instr._acquisition_stream_settings = None
        instr.get_double.return_value = 100.0
        instr.demod_path.side_effect = lambda idx: f"/dev/demods/{idx}"
        instr.daq.dataAcquisitionModule.return_value = module
        module.progress.return_value = [0]
        cancel = Mock(side_effect=[None, WorkflowCancelled("DAQ取消")])
        with self.assertRaises(WorkflowCancelled):
            daq.acquire_data(instr, cancellation_check=cancel)
        self.assertEqual(cancel.call_count, 2)
        module.execute.assert_called_once()
        module.clear.assert_called_once()
        module.reset_mock()
        module.progress.return_value = [1]
        module.read.return_value = {"/dev/demods/0/sample.y": {"value": [[1, 2, 3]]}}
        result = daq.acquire_data(instr, actual_rate=100)
        np.testing.assert_equal(result[0].values, [1, 2, 3])
        module.clear.assert_called_once()
        module.clear.side_effect = RuntimeError("DAQ清理失败")
        with self.assertRaisesRegex(RuntimeError, "DAQ清理失败"):
            daq.acquire_data(instr, actual_rate=100, strict_cleanup=True)
        # 清理失败不能覆盖原始取消；默认模式仍保留旧行为。
        module.progress.return_value = [0]
        cancel = Mock(side_effect=[None, WorkflowCancelled("DAQ取消")])
        with self.assertRaises(WorkflowCancelled):
            daq.acquire_data(instr, strict_cleanup=True, cancellation_check=cancel)
        module.progress.return_value = [1]
        self.assertEqual(len(daq.acquire_data(instr, actual_rate=100)), 1)

    def test_offline_fit_units_actual_rate_and_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, gamma, center, _ = make_run(root)
            summary = analysis.analyze(root)
            self.assertTrue(summary["fit_valid"])
            self.assertAlmostEqual(summary["fit_center_v"], center, places=7)
            self.assertAlmostEqual(summary["fit_slope_V_per_fT"], abs(a) / gamma**2 / (1950e6), places=14)
            self.assertAlmostEqual(summary["fit_gamma_fT"], gamma * 1950e6, places=2)
            with np.load(root / "results" / "gs200_current_range_sensitivity.npz") as data:
                self.assertEqual(data["freq"][-1], 500)
            self.assertTrue((root / "results" / "full_analysiswithoutpump.png").is_file())
            self.assertTrue((root / "results" / "run_summary.csv").is_file())
            self.assertFalse((root / "run_summary.csv").exists())
            env = dict(os.environ, LAB_RUN_DIR=str(root), MPLBACKEND="Agg", PYTHONUTF8="1")
            process = subprocess.run([sys.executable, "-m", analysis.__name__], env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertTrue(json.loads((root / "results" / "analysis.json").read_text(encoding="utf-8"))["success"])
            self.assertTrue((root / "results" / "full_analysiswithoutpump.pdf").is_file())
            process = subprocess.run([sys.executable, "experiments/Static_Magnetic_Field_Sensitivity_plot.py", str(root)], capture_output=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1", MPLBACKEND="Agg"), timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_shared_reference_fit_does_not_freeze_center(self):
        from ..noise_spectrum_xy_known_noise.dispersion import fit_dispersion_curve
        offsets = np.linspace(-0.2, 0.2, 81)
        y = analysis_steps._dispersive(offsets, 0.0003, 0.025, 0.007, 0.001)
        fit = fit_dispersion_curve(offsets, y, k_z_hz_per_v=11703.6778)
        self.assertAlmostEqual(fit["center_v"], 0.007, places=7)
        self.assertAlmostEqual(fit["slope_v_per_hz"], 0.0003 / 0.025**2 / 11703.6778, places=10)

    def test_full_workflow_uses_selected_demod_and_preserves_settings(self):
        params = StaticSensitivityParams(dispersion_steps=7, noise_n_avg=1, hf2_demod_idx=2,
                                         x_magnetic_field=0.1, y_magnetic_field=-0.2)
        with tempfile.TemporaryDirectory() as tmp, fake_rig(Path(tmp), params) as rig:
            with patch.object(phase, "calibrate_demod_phase", return_value=SimpleNamespace(
                    phase_shift_deg=17., to_dict=lambda: {"demod_idx": 2, "phase_shift_deg": 17.})) as calibrate:
                directory = workflow.run(params)
            self.assertEqual(calibrate.call_args.args[1].demod_idx, 2)
            self.assertEqual(rig.hf2.get_double(rig.hf2.demod_path(2) + "/phaseshift"), 17.)
            config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["completion_status"], "completed")
            self.assertEqual(config["noise_z_condition"], "zero_dc_output_off")
            self.assertEqual(config["actual_rates"]["hf2_noise_rate_Sa_s"], params.hf2_noise_rate * 0.9)
            self.assertEqual(config["temperature_switch_off_mode"], "zero_dc_output_on")
            for key, voltage in (("X_magnetic_field", 0.1), ("Y_magnetic_field", -0.2)):
                target = rig.mapping[key]
                self.assertEqual(rig.generators[target["resource"]].dc[target["channel"]], voltage)
            timing = rig.mapping["Time_sequence_2"]
            self.assertFalse(any(resource == timing["resource"] and channel == timing["channel"]
                                 for resource, _, channel, _ in rig.events))

    def test_cleanup_failure_keeps_original_exception(self):
        for original in (None, ValueError("原始异常")):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp, fake_rig(Path(tmp)) as rig:
                rig.shutdown_error = True
                with patch.object(acquisition, "acquire_noise", side_effect=original):
                    with self.assertRaisesRegex(ValueError if original else RuntimeError,
                                                "原始异常" if original else "安全结束失败"):
                        workflow.run(rig.params)
                config_path = next((Path(tmp) / "data" / workflow.DATA_TYPE).glob("*/experiment_config.yaml"))
                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                self.assertEqual(config["completion_status"], "failed")
                self.assertFalse(config["safety_shutdown"]["completed"])

    def test_api_schema_and_preflight_have_no_hardware_effect(self):
        from fastapi.testclient import TestClient
        from GUI.backend.main import app
        with TestClient(app) as client, patch.object(preparation, "connect_devices", side_effect=AssertionError("不得连接")):
            public = client.get("/api/experiments/static-sensitivity").json()
            self.assertIn("直流", public["description"])
            schema = client.get("/api/experiments/static-sensitivity/schema").json()
            self.assertEqual(schema["schema_version"], StaticSensitivityParams.schema_version)
            self.assertIn("dispersion_steps", {field["name"] for field in schema["fields"]})
            preflight = client.post("/api/experiments/static-sensitivity/preflight", json={"parameters": {"dispersion_steps": 2}}).json()
            self.assertFalse(preflight["ok"])

    def test_history_keeps_old_results_and_api_blocks_reanalysis(self):
        from fastapi.testclient import TestClient
        from GUI.backend import main as backend
        with tempfile.TemporaryDirectory() as tmp, patch.object(backend, "ROOT", Path(tmp)), patch.object(backend.manager, "create") as create, TestClient(backend.app) as client:
            base = Path(tmp) / "data" / workflow.DATA_TYPE
            old = base / "test_old"
            (old / "results").mkdir(parents=True)
            (old / "experiment_config.yaml").write_text(yaml.safe_dump({"params": {"ramp_low": -0.1}, "schema_version": 1}), encoding="utf-8")
            saved = old / "results" / "analysis.json"
            saved.write_text(json.dumps({"fit_is_valid": True, "sens_flat_fT_per_sqrt_Hz": 100}), encoding="utf-8")
            before = saved.read_bytes()
            response = client.get("/api/experiments/static-sensitivity/runs")
            self.assertEqual(response.json()["runs"][0]["run_id"], "test_old")
            summary = client.get("/api/experiments/static-sensitivity/runs/test_old/summary").json()
            self.assertEqual(summary["parameters"]["ramp_low"], -0.1)
            self.assertTrue(summary["analysis"]["fit_valid"])
            self.assertEqual(summary["analysis"]["sensitivity_flat_fT_per_sqrt_Hz"], 100)
            response = client.post("/api/runs/test_old/analyze", json={"experiment_id": "static-sensitivity"})
            self.assertEqual(response.status_code, 400)
            create.assert_not_called()
            self.assertEqual(saved.read_bytes(), before)
            new = base / "test_dc"
            new.mkdir()
            make_run(new)
            create.return_value.public.return_value = {"id": "analysis-test", "status": "queued"}
            response = client.post("/api/runs/test_dc/analyze", json={"experiment_id": "static-sensitivity"})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(create.call_args.kwargs["hardware_required"])
            runs = client.get("/api/runs?experiment_id=static-sensitivity").json()["runs"]
            can_analyze = {run["id"]: run["can_analyze"] for run in runs}
            self.assertFalse(can_analyze["test_old"])
            self.assertTrue(can_analyze["test_dc"])

    def test_legacy_config_is_not_silently_ignored(self):
        with patch.dict(os.environ, {"LAB_STATIC_CONFIG": "params/static_sensitivity.yaml"}), patch.object(preparation, "connect_devices") as connect:
            with self.assertRaisesRegex(ValueError, "桥接已移除"):
                workflow.run(StaticSensitivityParams())
            connect.assert_not_called()
        process = subprocess.run([sys.executable, "experiments/Static_Magnetic_Field_Sensitivity.py", "--ramp-freq", "1"], capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 2)

    def test_partial_scan_and_unusable_noise_are_diagnostic_failures(self):
        for completed in (6, 81):
            with self.subTest(completed=completed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                make_run(root)
                path = root / "raw" / "dispersion_scan.npz"
                with np.load(path) as source:
                    data = {key: source[key] for key in source.files}
                data["completed_mask"][completed:] = False
                data["y_median"][completed:] = np.nan
                acquisition.save_scan(path, data)
                invalid_noise = SimpleNamespace(sens_flat=float("nan"))
                with patch.object(analysis_steps, "compute_sensitivity", return_value=invalid_noise), self.assertRaises(ValueError):
                    analysis.analyze(root)
                summary = json.loads((root / "results" / "analysis.json").read_text(encoding="utf-8"))
                self.assertFalse(summary["success"])
                self.assertNotIn("sensitivity_flat_fT_per_sqrt_Hz", summary)
                self.assertEqual(summary["fit_valid"], completed >= 7)
                self.assertTrue((root / "results" / "dispersion_fit.npz").is_file())

    def test_all_valid_points_and_old_ramp_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_run(root)
            path = root / "experiment_config.yaml"
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            summary = analysis.analyze(root)
            self.assertEqual(summary["fit_points"], 81)
            with np.load(root / "results" / "dispersion_fit.npz") as data:
                self.assertTrue(data["fit_mask"].all())
            config["schema_version"] = 1
            config.pop("acquisition_method")
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "旧格式"):
                analysis.analyze(root)

    def test_invalid_fit_is_not_reported_as_sensitivity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_run(root)
            failure = SimpleNamespace(popt=np.array([0, 0, 0, 0]), is_valid=False,
                                      rejection_reasons=["拟合失败"], slope_V_per_fT=0, f_larmor_Hz=0)
            with patch.object(analysis_steps, "fit_dispersive", return_value=failure):
                with self.assertRaisesRegex(ValueError, "不能计算灵敏度"):
                    analysis.analyze(root)
            summary = json.loads((root / "results" / "analysis.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["success"])
            self.assertNotIn("sensitivity_flat_fT_per_sqrt_Hz", summary)
            self.assertTrue((root / "results" / "dispersion_fit.npz").is_file())

    def test_workflow_noise_failure_cleanup_and_import_safe(self):
        for fault in (WorkflowCancelled("噪声取消"), RuntimeError("噪声故障")):
            with tempfile.TemporaryDirectory() as tmp, fake_rig(Path(tmp), StaticSensitivityParams(dispersion_steps=7, noise_n_avg=2)) as rig:
                waveform = SimpleNamespace(signal_name="sample.y", values=np.arange(100, dtype=float))
                with patch.object(daq, "acquire_data", side_effect=[[waveform], fault]):
                    with self.assertRaises(type(fault)):
                        workflow.run(rig.params)
                directory = next((Path(tmp) / "data" / workflow.DATA_TYPE).iterdir())
                config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
                self.assertEqual(config["completion_status"], "cancelled" if isinstance(fault, WorkflowCancelled) else "failed")
                self.assertTrue(config["safety_shutdown"]["completed"])
                self.assertIn("X_magnetic_field", config["safety_shutdown"]["preserved_outputs"])
                self.assertTrue((directory / "raw/noise_raw/gs200_range_10mA/noise_0000.npy").is_file())
                rig.tec.disconnect.assert_called()
        process = subprocess.run([sys.executable, "-c", "from unittest.mock import patch; from lockin_amplifier import HF2Instrument;\nwith patch.object(HF2Instrument, 'connect', side_effect=AssertionError('不得连接')):\n import lab_workflows.experiment_modules.static_sensitivity.workflow\n import experiments.Static_Magnetic_Field_Sensitivity"], capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
