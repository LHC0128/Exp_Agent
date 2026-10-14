"""已知噪声会话与收尾回归；真实业务步骤配合假仪器。"""
from contextlib import contextmanager, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml
from ...common import WorkflowCancelled
from ..noise_spectrum_xy.test_sine_mode import copy_config, fake_hardware, scan_params as sine_params
from ..noise_spectrum_xy.session import serve
from .models import NoiseSpectrumXYKnownNoiseParams
from . import stages, workflow, excitation, acquisition


def copy_known_config(root):
    copy_config(root)
    path = root / "data/Bell_Bloom_Z_Field_Calibration/0920_183423_bb_z_cal/results"
    path.mkdir(parents=True)
    (path / "analysis.yaml").write_text(yaml.safe_dump(dict(experiment_id="bell-bloom-z-field-calibration",
        success=True, K_Z_Hz_per_V=11703.6778, linear_fit=dict(uncertainties=[1.0]))), encoding="utf-8")


def scan_params():
    return NoiseSpectrumXYKnownNoiseParams.from_external(sine_params().to_external())


@contextmanager
def rig(root):
    with fake_hardware(root) as hw, patch.object(excitation, "upload_arbitrary"):
        hw.rates.side_effect = lambda hfi, cfg, **kwargs: 800.0 if cfg.time_constant == scan_params().hf2_daq_tc else 1200.0
        z = hw.devices["dg_sweep"]
        z.get_shape.return_value = "CUSTOM"
        z.get_arb_points.return_value = 16384
        z.get_amplitude.return_value = scan_params().noise_amplitude_vpp
        z.get_offset.return_value = 0.0
        z.get_frequency.return_value = scan_params().noise_repeat_freq_hz
        z.get_voltage_unit.return_value = "VPP"
        z.get_output_load.return_value = 9.9e37
        yield hw


class KnownWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="known-workflow-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        copy_known_config(self.root)

    def test_manifest_dag_and_quality_gate(self):
        shown = {name for key in ("acquisition", "analysis") for stage in stages.definition()[key] for name in stage["display_parameters"]}
        self.assertEqual(shown, set(scan_params().to_external()))
        self.assertEqual(stages.parameter_impacts(stages.ANALYSIS)["ANALYSIS_DISPERSION_SOURCE_RUN"], ["B05", "B06"])
        with rig(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            with self.assertRaisesRegex(ValueError, "前置"):
                ctx.execute(["A13"])
            self.assertFalse(ctx.devices)
            ctx.execute([s.id for s in stages.ACQUISITION[:9]])
            hw.samples.side_effect = [dict(r=0.0, x=0.0, y=0.0, theta=0.0)]*20
            ctx.execute(["A10", "A11", "A12", "A13"])
            self.assertEqual(ctx.states["A10"]["status"], "quality_failed")
            self.assertIsNone(ctx.run_dir)
            with self.assertRaisesRegex(ValueError, "A10"):
                ctx.execute(["A11"])
            ctx.execute(["A15"])
            self.assertTrue(ctx.closed)
            self.assertEqual(ctx.outputs["共用触发"], "OFF")

    def test_pair_save_reprepare_rerun_and_parameters(self):
        with rig(self.root) as hw:
            ctx = workflow.Workflow(scan_params(), project_root=self.root)
            ctx.execute([s.id for s in stages.ACQUISITION[:13]])
            first = ctx.run_dir
            raw_before = (first / "raw/waveform_C0000_off.npy").read_bytes()
            timing = [json.loads(line) for line in (first / "raw/pair_timing.jsonl").read_text().splitlines()]
            self.assertEqual([item["state"] for item in timing], ["off", "on", "on", "off", "off", "on"])
            self.assertFalse((first / "raw/dispersion_scan.npz").exists())
            ctx.execute(["A14"])
            with self.assertRaisesRegex(ValueError, "DAQ 准备"):
                ctx.execute(["A13"])
            ctx.execute(["A12", "A13"])
            self.assertNotEqual(ctx.run_dir, first)
            self.assertEqual((first / "raw/waveform_C0000_off.npy").read_bytes(), raw_before)
            ctx.update_parameters({"ANALYSIS_DISPERSION_SOURCE_RUN": "以后选择"})
            self.assertEqual(ctx.states["A13"]["status"], "completed")
            ctx.update_parameters({"NOISE_SEED": 2})
            self.assertEqual(ctx.states["A10"]["status"], "completed")
            self.assertEqual(ctx.states["A11"]["status"], "stale")
            ctx.finish()
            for channel in (1, 2):
                hw.devices["dg_sweep"].set_output.assert_any_call(False, channel=channel)
            hw.devices["dg_temp"].setup_dc.assert_any_call(5.0, channel=2)

    def test_failures_cancel_and_clock_failure_restore_all_outputs(self):
        for failure in (OSError("通信断开"), WorkflowCancelled("取消"), KeyboardInterrupt()):
            with self.subTest(failure=failure), rig(self.root) as hw:
                hw.acquire.side_effect = failure
                with self.assertRaises(type(failure)):
                    workflow.run(scan_params(), project_root=self.root)
                for channel in (1, 2):
                    hw.devices["dg_comp"].set_output.assert_any_call(False, channel=channel)
                    hw.devices["dg_sweep"].set_output.assert_any_call(False, channel=channel)
                hw.devices["dg_temp"].setup_dc.assert_any_call(5.0, channel=2)
        with rig(self.root) as hw:
            hw.clocks.side_effect = OSError("时钟失败")
            with self.assertRaisesRegex(OSError, "时钟失败"):
                workflow.run(scan_params(), project_root=self.root)
            hw.devices["dg_sweep"].set_output.assert_any_call(False, channel=2)

    def test_partial_connection_failure_closes_connected_outputs_before_disconnect(self):
        with rig(self.root) as hw:
            hw.devices["hf2"].connect.side_effect = OSError("锁相连接失败")
            with self.assertRaisesRegex(OSError, "锁相连接失败"):
                workflow.run(scan_params(), project_root=self.root)
            for name in ("dg_comp", "dg_sweep"):
                for channel in (1, 2):
                    hw.devices[name].set_output.assert_any_call(False, channel=channel)
                hw.devices[name].disconnect.assert_called_once()
            hw.devices["dg_temp"].setup_dc.assert_any_call(5.0, channel=2)


def fake_session_main():
    root = Path(os.environ["XY_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    with rig(root), redirect_stdout(sys.__stdout__):
        serve(workflow.Workflow(NoiseSpectrumXYKnownNoiseParams.from_external(payload), project_root=root,
                                session_id=session_id), sys.stdin, stages=stages.ACQUISITION,
              validate=stages.validate_selection, finish_id=stages.FINISH_ID)


if __name__ == "__main__":
    fake_session_main()
