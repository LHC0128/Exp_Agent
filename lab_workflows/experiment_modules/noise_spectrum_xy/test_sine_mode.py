"""XY 噪声谱正弦模式回归检查。"""

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, call

from .models import NoiseSpectrumXYParams
from ...common import WorkflowCancelled, validate_safety_limit
from ...steps.temperature import set_temperature_switch
from ...steps.safety_shutdown import (
    DGChannelShutdown,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    TemperatureSwitchRestore,
    run_safety_shutdown,
)


ROOT = Path(__file__).resolve().parents[3]
AW_KEYS = {
    "XY_AW_OUTPUT_VPP",
    "XY_AW_OUTPUT_OFFSET_V",
    "XY_AW_REPEAT_FREQ_HZ",
    "XY_AW_POINTS",
}


class NoiseSpectrumXYSineTest(unittest.TestCase):
    def test_scan_amplitude_rearms_calibrated_phases_with_outputs_off(self) -> None:
        # 只执行真实函数定义，避免导入工作流时连接仪器。
        path = Path(__file__).with_name("workflow.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"validate_sine_peak", "set_xy_sine_phase", "set_xy_sine_peak"}]
        device, pump, trigger = Mock(), Mock(), Mock()
        namespace = dict(dg_comp=device, dg_mod=pump, dg_sweep=trigger,
                         XY_CTRL_PHASE=350.0, XY_CTRL_QUAD=90.0,
                         validate_safety_limit=validate_safety_limit)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
        setter = namespace["set_xy_sine_peak"]
        off = [call.set_output(False, channel=1), call.set_output(False, channel=2)]
        for peak in (0.75, 0.60, 0.75):
            device.reset_mock()
            setter(peak)
            self.assertEqual(device.mock_calls, off + [
                call.set_amplitude(2 * peak, channel=1),
                call.set_amplitude(2 * peak, channel=2),
            ] + off + [
                call.set_burst_phase(350.0, channel=1),
                call.set_burst_phase(80.0, channel=2),
                call.set_output(True, channel=1), call.set_output(True, channel=2),
            ])
        device.reset_mock()
        setter(0.0)
        self.assertEqual(device.mock_calls, off)
        device.reset_mock()
        with self.assertRaises(ValueError):
            setter(11.0)
        self.assertEqual(device.mock_calls, [])
        # 调幅或写相位失败时不能继续启用输出，由扫描 finally 统一安全收尾。
        for method in (device.set_amplitude, device.set_burst_phase):
            device.reset_mock()
            method.side_effect = RuntimeError("模拟通信异常")
            with self.assertRaises(RuntimeError):
                setter(0.75)
            self.assertEqual(device.set_output.call_args_list,
                             [call(False, channel=1), call(False, channel=2)]
                             * (2 if method is device.set_burst_phase else 1))
            method.side_effect = None
        self.assertEqual(pump.mock_calls, [])
        self.assertEqual(trigger.mock_calls, [])

    def test_temperature_output_off_restore_wait_and_cancel(self) -> None:
        path = Path(__file__).with_name("workflow.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        switch = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "set_temp_switch")
        clock = Mock()
        namespace = {
            "dg_temp": Mock(), "dg_temp_cfg": {"channel": 2},
            "FIXED_PARAMS": {"Temp_Switch": 5.0},
            "validate_safety_limit": validate_safety_limit,
            "set_temperature_switch": set_temperature_switch,
            "TEMP_SWITCH_ON_SETTLE_S": 0.25,
            "time": clock, "check_cancelled": Mock(),
        }
        exec(compile(ast.Module(body=[switch], type_ignores=[]), str(path), "exec"), namespace)
        device, setter = namespace["dg_temp"], namespace["set_temp_switch"]
        setter(False)
        self.assertEqual(device.mock_calls, [call.set_output(False, channel=2)])
        device.reset_mock()
        clock.monotonic.side_effect = [0, 0, 0.1, 0.2, 0.25]
        setter(True, wait=True)
        self.assertEqual(device.mock_calls, [call.setup_dc(5.0, channel=2), call.set_output(True, channel=2)])
        self.assertAlmostEqual(sum(item.args[0] for item in clock.sleep.call_args_list), 0.25)
        # 取消等待发生在输出恢复之后，不会把温控留在 OFF。
        clock.monotonic.side_effect = [0, 0]
        namespace["check_cancelled"].side_effect = WorkflowCancelled("test")
        device.reset_mock()
        with self.assertRaises(WorkflowCancelled):
            setter(True, wait=True)
        device.set_output.assert_called_once_with(True, channel=2)
        namespace["TEMP_SWITCH_ON_SETTLE_S"] = 0.0
        clock.monotonic.side_effect = [0, 0]
        clock.sleep.reset_mock()
        setter(True, wait=True)
        clock.sleep.assert_not_called()

    def test_restore_wait_default_and_validation(self) -> None:
        legacy = NoiseSpectrumXYParams.from_external({}, schema_version=2)
        self.assertEqual(legacy.temp_switch_on_settle_s, 2.0)
        params = NoiseSpectrumXYParams.from_external({"TEMP_SWITCH_ON_SETTLE_S": -0.1})
        self.assertTrue(any("TEMP_SWITCH_ON_SETTLE_S" in error for error in params.validate()))
        with self.assertRaises(ValueError):
            NoiseSpectrumXYParams.from_external({"TEMP_SWITCH_ON_SETTLE_S": float("nan")})

    def test_shutdown_preserves_pump_gate_and_closes_xy_trigger(self) -> None:
        # 工作流顶层会连接仪器，仅提取收尾函数，在假设备上执行真实共享收尾。
        path = Path(__file__).with_name("workflow.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        shutdown = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "safe_scan_outputs_off"
        )
        namespace = {
            "DGChannelShutdown": DGChannelShutdown,
            "DisconnectTarget": DisconnectTarget,
            "STANDARD_PRESERVED_OUTPUTS": STANDARD_PRESERVED_OUTPUTS,
            "TemperatureSwitchRestore": TemperatureSwitchRestore,
            "run_safety_shutdown": run_safety_shutdown,
            "devices": {},
            **{name: Mock() for name in ("dg_comp", "dg_sweep", "dg_mod", "dg_temp")},
        }
        exec(compile(ast.Module(body=[shutdown], type_ignores=[]), str(path), "exec"), namespace)
        report = namespace["safe_scan_outputs_off"]()
        self.assertTrue(report.completed, report.errors)
        self.assertIn("Time_sequence", report.preserved_outputs)
        self.assertEqual(namespace["dg_mod"].mock_calls, [])
        namespace["dg_temp"].setup_dc.assert_called_once_with(5.0, channel=2)
        namespace["dg_temp"].set_output.assert_called_once_with(True, channel=2)
        for channel in (1, 2):
            namespace["dg_comp"].set_output.assert_any_call(False, channel=channel)
        namespace["dg_sweep"].set_output.assert_any_call(False, channel=2)

    def test_v1_direct_aw_parameters_are_migrated_away(self) -> None:
        values = NoiseSpectrumXYParams().to_external()
        values.update({name: 1 for name in AW_KEYS})
        migrated = NoiseSpectrumXYParams.from_external(values, schema_version=1)
        self.assertTrue(AW_KEYS.isdisjoint(migrated.to_external()))

    def test_workflow_configures_sine_once_and_scans_amplitude(self) -> None:
        source = (
            ROOT
            / "lab_workflows"
            / "experiment_modules"
            / "noise_spectrum_xy"
            / "workflow.py"
        ).read_text(encoding="utf-8")
        self.assertIn("dg_comp.setup_sine(", source)
        self.assertIn("dg_comp.set_amplitude(", source)
        self.assertIn("dg_comp.set_burst_phase(", source)
        self.assertNotIn("upload_arbitrary(", source)


if __name__ == "__main__":
    unittest.main()
