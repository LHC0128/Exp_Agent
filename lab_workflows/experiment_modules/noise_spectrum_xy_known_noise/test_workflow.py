"""已知噪声会话与收尾回归；真实业务步骤配合假仪器。"""
from contextlib import contextmanager, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
from time import monotonic as real_monotonic, sleep as real_sleep
import unittest
from unittest.mock import patch
import yaml
from ...common import WorkflowCancelled
from ..noise_spectrum_xy.test_sine_mode import copy_config, fake_hardware, scan_params as sine_params
from ..noise_spectrum_xy.session import serve
from .models import NoiseSpectrumXYKnownNoiseParams
from . import stages, workflow, excitation, acquisition, generation


def copy_known_config(root):
    copy_config(root)


def scan_params():
    return NoiseSpectrumXYKnownNoiseParams.from_external(sine_params().to_external() | {"Z_CALIBRATION_K_HZ_PER_V": 11703.6778})


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

    def test_manual_kz_preflight_and_preview_do_not_read_calibration_results(self):
        self.assertFalse((self.root / "data/Bell_Bloom_Z_Field_Calibration").exists())
        params = scan_params()
        with patch.object(generation, "load_z_calibration", side_effect=AssertionError("不应读取标定运行")):
            workflow.preflight(params, self.root)
            preview = params.derive_external(params.to_external())
            doubled = params.derive_external(params.to_external() | {"Z_CALIBRATION_K_HZ_PER_V": 2 * params.z_calibration_k_hz_per_v})
        self.assertNotIn("error", preview)
        self.assertEqual(preview["z_calibration_k_hz_per_v"], params.z_calibration_k_hz_per_v)
        self.assertAlmostEqual(doubled["equivalent_noise_hz_per_rt_hz"], 2 * preview["equivalent_noise_hz_per_rt_hz"])
        for value in (0, -1, float("nan"), float("inf"), True, "5373.638"):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                invalid = NoiseSpectrumXYKnownNoiseParams.from_external(params.to_external() | {"Z_CALIBRATION_K_HZ_PER_V": value})
                workflow.preflight(invalid, self.root)

    def test_manifest_dag_and_quality_gate(self):
        shown = {name for key in ("acquisition", "analysis") for stage in stages.definition()[key] for name in stage["display_parameters"]}
        self.assertEqual(shown, set(scan_params().to_external(include_hidden=False)))
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
            self.assertTrue((ctx.run_dir / "experiment_config.yaml").is_file())
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
            config = yaml.safe_load((first / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["schema_version"], NoiseSpectrumXYKnownNoiseParams.schema_version)
            self.assertEqual(config["truth_chain"]["z_calibration_source"], "manual")
            self.assertEqual(config["truth_chain"]["z_calibration_run"], "")
            self.assertEqual(config["truth_chain"]["K_Z_Hz_per_V"], scan_params().z_calibration_k_hz_per_v)
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
            ctx.update_parameters({"Z_CALIBRATION_K_HZ_PER_V": 12000.0})
            self.assertEqual(ctx.states["A10"]["status"], "completed")
            self.assertEqual(ctx.states["A11"]["status"], "stale")
            ctx.execute(["A11", "A12", "A13"])
            config = yaml.safe_load((ctx.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["truth_chain"]["K_Z_Hz_per_V"], 12000.0)
            self.assertEqual((first / "raw/waveform_C0000_off.npy").read_bytes(), raw_before)
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
    with rig(root) as hw, redirect_stdout(sys.__stdout__):
        ctx = workflow.Workflow(NoiseSpectrumXYKnownNoiseParams.from_external(payload), project_root=root,
                                session_id=session_id)
        if os.environ.get("KNOWN_TEST_BLOCK_DAQ"):
            from ...experiment_runtime import check_cancelled
            def blocked_read(*args, **kwargs):
                (root / "daq_waiting").touch()
                while True:
                    check_cancelled()
                    real_sleep(.01)
            hw.acquire.side_effect = blocked_read
        if os.environ.get("KNOWN_TEST_BLOCK_SHUTDOWN"):
            original_shutdown = ctx.shutdown_outputs
            def blocked_shutdown():
                (root / "shutdown_waiting").touch()
                deadline = real_monotonic() + 30
                while not (root / "release_shutdown").exists():
                    if real_monotonic() > deadline:
                        raise TimeoutError("测试未释放安全收尾")
                    real_sleep(.01)
                return original_shutdown()
            ctx.shutdown_outputs = blocked_shutdown
        serve(ctx, sys.stdin, stages=stages.ACQUISITION,
              validate=stages.validate_selection, finish_id=stages.FINISH_ID)


if __name__ == "__main__":
    fake_session_main()
