"""Probe AM 会话、配对采集与收尾的无硬件回归。"""
from contextlib import ExitStack, contextmanager, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import yaml

from ...common import WorkflowCancelled, load_mapping
from ...steps import routed_signal_generator
from ..noise_spectrum_xy.test_sine_mode import copy_config, fake_hardware, scan_params as sine_params
from ..noise_spectrum_xy_known_noise.models import NOISE_POINTS
from ..noise_spectrum_xy.session import serve
from . import excitation, preparation, stages, workflow
from .models import NoiseSpectrumXYProbeAMParams


def scan_params():
    return NoiseSpectrumXYProbeAMParams.from_external(sine_params().to_external())


@contextmanager
def rig(root, *, inject=False):
    """在 XY 假仪器上增加 Probe AM 信号源；保留会话、路由与校相算法。"""
    mapping = load_mapping(root)
    probe_resources = {mapping["Probe_AOM_Carrier"]["resource"], mapping["Probe_AOM_AM"]["resource"]}
    params = scan_params()
    with fake_hardware(root) as hw, patch.object(excitation, "upload_arbitrary"):
        hw.rates.side_effect = lambda hfi, cfg, **kwargs: 800.0 if cfg.time_constant == params.hf2_daq_tc else 1200.0
        probe = Mock(name="dg_probe_am")
        for device, amplitude in ((probe, params.noise_amplitude_vpp),
                                  (hw.devices["dg_sweep"], params.z_noise_amplitude_vpp)):
            device.get_shape.return_value = "CUSTOM"
            device.get_arb_points.return_value = NOISE_POINTS
            device.get_amplitude.return_value = amplitude
            device.get_offset.return_value = 0.0
            device.get_frequency.return_value = 10.0
            device.get_voltage_unit.return_value = "VPP"
            device.get_output_load.return_value = 9.9e37
        hw.devices["dg_probe_am"] = probe
        z = hw.devices["dg_sweep"]
        z.get_output.side_effect = lambda **kw: next(
            entry.args[0] for entry in reversed(z.set_output.call_args_list)
            if entry.kwargs.get("channel") == kw["channel"])
        factory = routed_signal_generator.create_signal_generator
        original = factory.side_effect
        factory.side_effect = lambda cfg: probe if cfg["resource"] in probe_resources else original(cfg)
        with ExitStack() as stack:
            for name, attr in (("gs200", "GS200Instrument"), ("tec", "TECInstrument"),
                               ("hf2", "create_lockin"), ("dg_clock", "synchronize_connected_clocks")):
                if name == "dg_clock":
                    stack.enter_context(patch.object(preparation, "synchronize_connected_clocks", hw.clocks))
                else:
                    stack.enter_context(patch.object(preparation, attr, return_value=hw.devices[name]))
            yield hw


class ProbeAMWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="probe-am-workflow-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_config(self.root)

    def test_import_does_not_construct_instruments(self):
        import importlib
        with patch("lab_workflows.devices.create_lockin") as lockin, \
             patch("pyvisa.ResourceManager") as visa, patch("serial.Serial") as serial:
            importlib.reload(importlib.import_module(__package__ + ".workflow"))
        lockin.assert_not_called()
        visa.assert_not_called()
        serial.assert_not_called()

    def test_manifest_dag_and_quality_gate(self):
        shown = {name for key in ("acquisition", "analysis") for stage in stages.definition()[key] for name in stage["display_parameters"]}
        self.assertEqual(shown, set(scan_params().to_external()))
        self.assertEqual(stages.parameter_impacts(stages.ACQUISITION)["INJECT_CONTROLLED_NOISE"],
                         ["A12", "A13", "A14", "A15"])
        self.assertEqual(stages.parameter_impacts(stages.ACQUISITION)["PROBE_AOM_CARRIER_VPP"][0], "A08")
        with rig(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            with self.assertRaisesRegex(ValueError, "前置"):
                ctx.execute(["A14"])
            self.assertFalse(ctx.devices)
            ctx.execute([stage.id for stage in stages.ACQUISITION[:10]])
            hw.samples.side_effect = None
            hw.samples.return_value = dict(r=0.0, x=0.0, y=0.0, theta=0.0)
            ctx.execute(["A11"])
            self.assertEqual(ctx.states["A11"]["status"], "quality_failed")
            self.assertIsNone(ctx.run_dir)
            with self.assertRaisesRegex(ValueError, "A11"):
                ctx.execute(["A12"])
            ctx.execute(["A16"])
            self.assertTrue(ctx.closed)
            self.assertEqual(ctx.outputs["Probe AM"], "OFF")

    def test_pair_save_reprepare_rerun_and_parameters(self):
        for inject in (False, True):
            with self.subTest(inject=inject), rig(self.root) as hw:
                params = NoiseSpectrumXYProbeAMParams.from_external(scan_params().to_external()
                    | {"RUN_TAG": "probe_am", "INJECT_CONTROLLED_NOISE": inject})
                ctx = workflow.Workflow(params, project_root=self.root)
                ctx.execute([stage.id for stage in stages.ACQUISITION[:14]])
                first = ctx.run_dir
                raw_before = (first / "raw/waveform_C0000_off.npy").read_bytes()
                timing = [json.loads(line) for line in (first / "raw/pair_timing.jsonl").read_text().splitlines()]
                self.assertEqual([item["state"] for item in timing], ["off", "on", "on", "off", "off", "on"])
                self.assertFalse((first / "raw/dispersion_scan.npz").exists())
                self.assertEqual((first / "raw/controlled_z_noise_waveform.npy").exists(), inject)
                z = hw.devices["dg_sweep"]
                z.set_output.assert_any_call(inject, channel=1)
                self.assertEqual((first / "raw/probe_am_noise_waveform.npy").is_file(), True)
                ctx.execute(["A15"])
                with self.assertRaisesRegex(ValueError, "DAQ 准备"):
                    ctx.execute(["A14"])
                ctx.execute(["A13", "A14"])
                self.assertNotEqual(ctx.run_dir, first)
                self.assertEqual((first / "raw/waveform_C0000_off.npy").read_bytes(), raw_before)
                ctx.update_parameters({"NOISE_SEED": 2})
                self.assertEqual(ctx.states["A11"]["status"], "completed")
                self.assertEqual(ctx.states["A12"]["status"], "stale")
                ctx.finish()
                for channel in (1, 2):
                    hw.devices["dg_comp"].set_output.assert_any_call(False, channel=channel)
                    hw.devices["dg_sweep"].set_output.assert_any_call(False, channel=channel)
                    hw.devices["dg_probe_am"].set_output.assert_any_call(False, channel=channel)
                hw.devices["dg_probe_am"].set_mod_state.assert_any_call(False, channel=1)
                hw.devices["dg_temp"].setup_dc.assert_any_call(5.0, channel=2)

    def test_z_noise_readback_mismatch_rejected(self):
        with rig(self.root, inject=True) as hw:
            z = hw.devices["dg_sweep"]
            z.get_output.side_effect = lambda **kw: not next(
                entry.args[0] for entry in reversed(z.set_output.call_args_list)
                if entry.kwargs.get("channel") == kw["channel"])
            params = NoiseSpectrumXYProbeAMParams.from_external(
                scan_params().to_external() | {"INJECT_CONTROLLED_NOISE": True})
            with self.assertRaisesRegex(RuntimeError, "Z 可控噪声输出状态回读"):
                workflow.run(params, project_root=self.root)
            for channel in (1, 2):
                hw.devices["dg_probe_am"].set_output.assert_any_call(False, channel=channel)
                hw.devices["dg_comp"].set_output.assert_any_call(False, channel=channel)

    def test_failures_cancel_and_clock_failure_restore_all_outputs(self):
        for failure in (OSError("通信断开"), WorkflowCancelled("取消"), KeyboardInterrupt()):
            with self.subTest(failure=failure), rig(self.root) as hw:
                hw.acquire.side_effect = failure
                with self.assertRaises(type(failure)):
                    workflow.run(scan_params(), project_root=self.root)
                for name in ("dg_comp", "dg_sweep", "dg_probe_am"):
                    for channel in (1, 2):
                        hw.devices[name].set_output.assert_any_call(False, channel=channel)
                hw.devices["dg_temp"].setup_dc.assert_any_call(5.0, channel=2)
        with rig(self.root) as hw:
            hw.clocks.side_effect = OSError("时钟失败")
            with self.assertRaisesRegex(OSError, "时钟失败"):
                workflow.run(scan_params(), project_root=self.root)
            for name, device in hw.devices.items():
                device.disconnect.assert_called_once()

    def test_partial_connection_failure_closes_connected_outputs_before_disconnect(self):
        with rig(self.root) as hw:
            hw.devices["hf2"].connect.side_effect = OSError("锁相连接失败")
            with self.assertRaisesRegex(OSError, "锁相连接失败"):
                workflow.run(scan_params(), project_root=self.root)
            for name in ("dg_comp", "dg_sweep", "dg_probe_am"):
                for channel in (1, 2):
                    hw.devices[name].set_output.assert_any_call(False, channel=channel)
                hw.devices[name].disconnect.assert_called_once()
            hw.devices["dg_temp"].setup_dc.assert_any_call(5.0, channel=2)


def fake_session_main():
    root = Path(os.environ["XY_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    with rig(root), redirect_stdout(sys.__stdout__):
        serve(workflow.Workflow(NoiseSpectrumXYProbeAMParams.from_external(payload), project_root=root,
                                session_id=session_id), sys.stdin, stages=stages.ACQUISITION,
              validate=stages.validate_selection, finish_id=stages.FINISH_ID)


if __name__ == "__main__":
    fake_session_main()
