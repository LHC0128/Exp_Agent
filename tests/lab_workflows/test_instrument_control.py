import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lab_workflows.devices import ChannelRecord, DeviceRecord
from lab_workflows.instrument_control import (
    _read_generator_channel,
    _read_gs200_state,
    _validate_generator_output,
    apply_current_source,
    apply_generator_channel,
    apply_laser_emission,
    apply_laser_settings,
    read_device,
)


class InstrumentControlTests(unittest.TestCase):
    @staticmethod
    def _gs200_record():
        return DeviceRecord(
            id="90Z631552",
            type="GS200",
            label="主磁场",
            resource="FAKE",
            short_resource="90Z631552",
            options={
                "mapping_key": "main_magnetic_field",
                "source_function": "CURRent",
            },
        )

    @staticmethod
    def _laser_record(remote_emission_control_enabled=False):
        return DeviceRecord(
            id="DLC_PRO_53043",
            type="DLC_PRO",
            label="Probe 激光",
            resource="192.168.124.68",
            short_resource="DLC PRO_53043",
            options={
                "mapping_key": "probe_laser",
                "laser_channel": 1,
                "controller_serial": "DLC PRO_53043",
                "laser_head_serial": "24105",
                "remote_emission_control_enabled": (
                    remote_emission_control_enabled
                ),
                "current_safety_key": "probe_laser_current",
                "temperature_safety_key": "probe_laser_temperature",
                "pzt_safety_key": "probe_laser_pzt_voltage",
                "scan_amplitude_safety_key": (
                    "probe_laser_scan_amplitude"
                ),
            },
        )

    @staticmethod
    def _laser_snapshot(**overrides):
        snapshot = {
            "type": "DLC_PRO",
            "controller_serial": "DLC PRO_53043",
            "system_health_code": 0,
            "laser_health_code": 0,
            "interlock_open": False,
            "laser_enabled": True,
            "emission": False,
            "laser_emission": False,
            "current_set_ma": 201.0,
            "current_actual_ma": 201.1,
            "current_clip_ma": 392.0,
            "temperature_set_c": 20.3,
            "temperature_actual_c": 20.3,
            "pzt_voltage_v": 80.0,
            "pzt_actual_v": 80.0,
            "scan_amplitude_vpp": 34.0,
        }
        snapshot.update(overrides)
        return snapshot

    def test_generator_snapshot_keeps_optional_errors(self):
        instrument = MagicMock()
        instrument.MOD_TYPES = ("AM", "FM", "PM", "FSKey", "PWM")
        instrument.get_wave_parameters.return_value = {
            "shape": "SINusoid",
            "frequency": 1000.0,
            "amplitude": 1.0,
            "offset": 0.0,
            "phase": 45.0,
        }
        instrument.get_output.return_value = False
        instrument.get_mod_type_state.side_effect = lambda kind, channel: kind == "AM"
        instrument.get_mod_source.return_value = "INTernal"
        instrument.get_mod_internal_frequency.return_value = 100.0
        instrument.get_mod_internal_function.return_value = "SINusoid"
        instrument.get_mod_am_depth.return_value = 80.0
        instrument.get_burst_state.return_value = False
        instrument.get_burst_mode.return_value = "TRIGgered"
        instrument.get_burst_ncycles.return_value = 5
        instrument.get_burst_phase.return_value = 0.0
        instrument.get_burst_period.return_value = 0.1
        instrument.get_burst_delay.side_effect = RuntimeError("当前模式不可查询")
        instrument.get_burst_trigger_source.return_value = "IMMediate"
        instrument.get_burst_trigger_slope.return_value = "POSitive"

        channel = SimpleNamespace(
            number=1, mapping_key="Pump_laser_power",
            label="Pump光功率", read_only=False,
        )
        state = _read_generator_channel(instrument, "DG900", channel)
        self.assertEqual(state["shape"], "SINusoid")
        self.assertTrue(state["mod"]["enabled"])
        self.assertEqual(state["mod"]["am_depth"], 80.0)
        self.assertIsNone(state["burst"]["delay"])
        self.assertEqual(state["readback_errors"][0]["field"], "burst.delay")

    def test_apply_mod_disables_burst_and_writes_output_last(self):
        record = DeviceRecord(
            id="DG9QTEST", type="DG900", label="测试信号源",
            resource="FAKE", short_resource="DG9QTEST",
            channels=[ChannelRecord(1, "Pump_laser_power", "Pump光功率")],
        )
        instrument = MagicMock()
        settings = {
            "shape": "SINusoid",
            "frequency": 1000.0,
            "amplitude": 0.5,
            "offset": 0.25,
            "phase": 0.0,
            "output": False,
            "mod": {
                "enabled": True,
                "type": "AM",
                "source": "EXTernal",
                "am_depth": 80.0,
            },
            "burst": {"enabled": False},
            "target_mode": "mod",
        }
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch("lab_workflows.instrument_control.read_device", return_value={"ok": True}),
        ):
            result = apply_generator_channel("DG9QTEST", 1, settings)

        self.assertEqual(result, {"ok": True})
        names = [call[0] for call in instrument.method_calls]
        self.assertLess(names.index("clear_status"), names.index("set_shape"))
        self.assertLess(names.index("set_burst_state"), names.index("set_mod_type_state"))
        self.assertLess(names.index("set_mod_type_state"), names.index("set_output"))
        self.assertLess(
            names.index("set_output"),
            names.index("wait_for_operation_complete"),
        )
        self.assertLess(
            names.index("wait_for_operation_complete"),
            names.index("raise_for_errors"),
        )
        instrument.disable_all_mod.assert_called_once_with(1)
        instrument.disconnect.assert_called_once()

    def test_heat_control_write_error_turns_output_off(self):
        record = DeviceRecord(
            id="DG9QTEST", type="DG900", label="加热信号",
            resource="FAKE", short_resource="DG9QTEST",
            channels=[ChannelRecord(1, "Heat_Control", "加热信号")],
        )
        instrument = MagicMock()
        instrument.raise_for_errors.side_effect = RuntimeError("SCPI 错误")
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
        ):
            with self.assertRaisesRegex(RuntimeError, "SCPI 错误"):
                apply_generator_channel(
                    record.id,
                    1,
                    {
                        "shape": "SINusoid",
                        "frequency": 26000.0,
                        "amplitude": 0.5,
                        "offset": 0.0,
                        "output": True,
                        "mod": {"enabled": False},
                        "burst": {"enabled": False},
                    },
                )

        self.assertEqual(instrument.set_output.call_args_list[-1].args, (False, 1))
        instrument.disconnect.assert_called_once()

    def test_read_only_channel_rejects_write_before_connect(self):
        record = DeviceRecord(
            id="DG9QTEST", type="DG900", label="受保护信号源",
            resource="FAKE", short_resource="DG9QTEST",
            channels=[ChannelRecord(1, "Protected_Test", "受保护通道", read_only=True)],
        )
        with patch("lab_workflows.instrument_control.find_device", return_value=record):
            with self.assertRaises(PermissionError):
                apply_generator_channel("DG9QTEST", 1, {"offset": 0.0})

    def test_heat_control_current_waveform_is_allowed_but_larger_peak_is_rejected(self):
        _validate_generator_output(
            "Heat_Control", {"offset": 0.0, "amplitude": 0.5}
        )
        with self.assertRaises(ValueError):
            _validate_generator_output(
                "Heat_Control", {"offset": 0.0, "amplitude": 0.6}
            )

    def test_gs200_snapshot_converts_a_to_ma(self):
        instrument = MagicMock()
        instrument.idn.return_value = "YOKOGAWA,GS210,90Z631552,2.02"
        instrument.get_source_function.return_value = "CURR"
        instrument.get_output.return_value = True
        instrument.get_current.return_value = 0.009305
        instrument.get_current_range.return_value = 0.01
        instrument.get_voltage_limit.return_value = 10.0
        instrument.get_current_limit.return_value = 0.2

        state = _read_gs200_state(self._gs200_record(), instrument)

        self.assertAlmostEqual(state["current_ma"], 9.305)
        self.assertAlmostEqual(state["current_range_ma"], 10.0)
        self.assertAlmostEqual(state["current_limit_ma"], 200.0)
        self.assertEqual(state["min_current_ma"], -10.0)
        self.assertEqual(state["max_current_ma"], 10.0)
        self.assertNotIn("channels", state)

    def test_gs200_voltage_mode_does_not_report_level_as_current(self):
        instrument = MagicMock()
        instrument.idn.return_value = "YOKOGAWA,GS210,90Z631552,2.02"
        instrument.get_source_function.return_value = "VOLT"
        instrument.get_output.return_value = False
        instrument.get_voltage_limit.return_value = 10.0
        instrument.get_current_limit.return_value = 0.2

        state = _read_gs200_state(self._gs200_record(), instrument)

        self.assertIsNone(state["current_ma"])
        self.assertIsNone(state["current_range_ma"])
        instrument.get_current.assert_not_called()
        instrument.get_current_range.assert_not_called()

    def test_gs200_enable_writes_current_before_output(self):
        record = self._gs200_record()
        instrument = MagicMock()
        instrument.get_output.return_value = False
        instrument.get_source_function.return_value = "CURR"
        instrument.get_current.return_value = 0.009305
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_gs200_state",
                return_value={"type": "GS200"},
            ),
        ):
            result = apply_current_source(
                record.id,
                {
                    "current_ma": 9.305,
                    "output": True,
                    "confirm_output_enable": True,
                },
            )

        self.assertEqual(result, {"type": "GS200"})
        names = [call[0] for call in instrument.method_calls]
        self.assertLess(names.index("set_current"), names.index("set_output"))
        instrument.set_current.assert_called_once()
        self.assertAlmostEqual(
            instrument.set_current.call_args.args[0],
            0.009305,
        )
        instrument.set_output.assert_called_once_with(True)
        instrument.disconnect.assert_called_once()

    def test_gs200_enable_requires_confirmation_and_stays_off(self):
        record = self._gs200_record()
        instrument = MagicMock()
        instrument.get_output.return_value = False
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
        ):
            with self.assertRaisesRegex(ValueError, "二次确认"):
                apply_current_source(
                    record.id,
                    {"current_ma": 9.305, "output": True},
                )

        instrument.set_current.assert_not_called()
        instrument.set_output.assert_called_once_with(False)

    def test_gs200_disable_turns_output_off_before_writing_current(self):
        record = self._gs200_record()
        instrument = MagicMock()
        instrument.get_output.return_value = True
        instrument.get_source_function.return_value = "CURR"
        instrument.get_current.return_value = 0.0093
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_gs200_state",
                return_value={"type": "GS200"},
            ),
        ):
            apply_current_source(
                record.id,
                {"current_ma": 9.3, "output": False},
            )

        names = [call[0] for call in instrument.method_calls]
        self.assertLess(names.index("set_output"), names.index("set_current"))
        instrument.set_output.assert_called_once_with(False)

    def test_gs200_out_of_range_is_rejected_before_connect(self):
        record = self._gs200_record()
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect") as connect,
        ):
            with self.assertRaises(ValueError):
                apply_current_source(record.id, {"current_ma": 10.1})
        connect.assert_not_called()

    def test_gs200_non_finite_current_is_rejected_before_connect(self):
        record = self._gs200_record()
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect") as connect,
        ):
            with self.assertRaisesRegex(ValueError, "不是有限数值"):
                apply_current_source(record.id, {"current_ma": float("nan")})
        connect.assert_not_called()

    def test_gs200_write_error_turns_output_off(self):
        record = self._gs200_record()
        instrument = MagicMock()
        instrument.get_output.return_value = True
        instrument.set_current.side_effect = RuntimeError("写入失败")
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
        ):
            with self.assertRaisesRegex(RuntimeError, "写入失败"):
                apply_current_source(record.id, {"current_ma": 9.3})
        instrument.set_output.assert_called_once_with(False)
        instrument.disconnect.assert_called_once()

    def test_gs200_readback_error_turns_output_off(self):
        record = self._gs200_record()
        instrument = MagicMock()
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_gs200_state",
                side_effect=RuntimeError("回读失败"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "回读失败"):
                read_device(record.id)

        instrument.set_output.assert_called_once_with(False)
        instrument.disconnect.assert_called_once()

    def test_gs200_shutdown_failure_keeps_both_errors(self):
        record = self._gs200_record()
        instrument = MagicMock()
        instrument.get_output.return_value = True
        instrument.set_current.side_effect = RuntimeError("写入失败")
        instrument.set_output.side_effect = RuntimeError("关断失败")
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "写入失败；GS200 输出关断失败: 关断失败",
            ):
                apply_current_source(record.id, {"current_ma": 9.3})

    def test_laser_out_of_range_rejected_before_connect(self):
        record = self._laser_record()
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect") as connect,
        ):
            with self.assertRaises(ValueError):
                apply_laser_settings(
                    record.id,
                    {"current_set_ma": 390.1},
                )
        connect.assert_not_called()

    def test_laser_envelope_rejected_before_connect(self):
        record = self._laser_record()
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect") as connect,
        ):
            with self.assertRaisesRegex(ValueError, "扫描包络越界"):
                apply_laser_settings(
                    record.id,
                    {
                        "pzt_voltage_v": 10.0,
                        "scan_amplitude_vpp": 50.0,
                    },
                )
        connect.assert_not_called()

    def test_laser_live_clip_rejected_before_any_write(self):
        record = self._laser_record()
        instrument = MagicMock()
        before = self._laser_snapshot(current_clip_ma=380.0)
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_dlc_pro_state",
                return_value=before,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "current-clip"):
                apply_laser_settings(
                    record.id,
                    {"current_set_ma": 385.0},
                )
        instrument.set_laser_current_ma.assert_not_called()
        instrument.set_laser_temperature_c.assert_not_called()
        instrument.set_pzt_voltage_v.assert_not_called()
        instrument.set_scan_amplitude_vpp.assert_not_called()
        instrument.set_scan_enabled.assert_not_called()

    def test_laser_uses_safe_scan_transition_order(self):
        record = self._laser_record()
        instrument = MagicMock()
        before = self._laser_snapshot(
            pzt_voltage_v=70.0,
            scan_amplitude_vpp=50.0,
        )
        after = self._laser_snapshot(
            pzt_voltage_v=10.0,
            scan_amplitude_vpp=20.0,
        )
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_dlc_pro_state",
                side_effect=[before, after],
            ),
        ):
            result = apply_laser_settings(
                record.id,
                {
                    "pzt_voltage_v": 10.0,
                    "scan_amplitude_vpp": 20.0,
                    "scan_enabled": True,
                },
            )

        self.assertEqual(result, after)
        names = [call[0] for call in instrument.method_calls]
        self.assertLess(
            names.index("set_scan_amplitude_vpp"),
            names.index("set_pzt_voltage_v"),
        )
        self.assertLess(
            names.index("set_pzt_voltage_v"),
            names.index("set_scan_enabled"),
        )

    def test_laser_write_error_does_not_change_emission(self):
        record = self._laser_record()
        instrument = MagicMock()
        instrument.set_laser_current_ma.side_effect = RuntimeError("写入失败")
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_dlc_pro_state",
                return_value=self._laser_snapshot(),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "设备状态可能未知"):
                apply_laser_settings(
                    record.id,
                    {"current_set_ma": 202.0},
                )
        instrument.set_emission.assert_not_called()
        instrument.disconnect.assert_called_once()

    def test_laser_emission_on_default_gate_rejects_before_connect(self):
        record = self._laser_record()
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect") as connect,
        ):
            with self.assertRaisesRegex(PermissionError, "默认禁用"):
                apply_laser_emission(
                    record.id,
                    {
                        "enabled": True,
                        "safety_acknowledged": True,
                        "confirm_emission_enable": True,
                        "confirmation_text": "DLC PRO_53043",
                    },
                )
        connect.assert_not_called()

    def test_laser_emission_on_requires_double_confirmation(self):
        record = self._laser_record(remote_emission_control_enabled=True)
        with patch(
            "lab_workflows.instrument_control.find_device",
            return_value=record,
        ):
            with self.assertRaisesRegex(ValueError, "勾选安全确认"):
                apply_laser_emission(
                    record.id,
                    {
                        "enabled": True,
                        "confirmation_text": "DLC PRO_53043",
                    },
                )

    def test_laser_emission_on_checks_and_reads_back(self):
        record = self._laser_record(remote_emission_control_enabled=True)
        instrument = MagicMock()
        before = self._laser_snapshot()
        after = self._laser_snapshot(
            emission=True,
            laser_emission=True,
        )
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_dlc_pro_state",
                side_effect=[before, after],
            ),
        ):
            result = apply_laser_emission(
                record.id,
                {
                    "enabled": True,
                    "safety_acknowledged": True,
                    "confirm_emission_enable": True,
                    "confirmation_text": "DLC PRO_53043",
                },
            )

        self.assertTrue(result["emission"])
        instrument.set_emission.assert_called_once_with(
            True,
            remote_enable_allowed=True,
        )

    def test_laser_emission_off_requires_no_confirmation(self):
        record = self._laser_record()
        instrument = MagicMock()
        before = self._laser_snapshot(emission=True)
        after = self._laser_snapshot(emission=False)
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch(
                "lab_workflows.instrument_control._read_dlc_pro_state",
                side_effect=[before, after],
            ),
        ):
            result = apply_laser_emission(
                record.id,
                {"enabled": False},
            )
        self.assertFalse(result["emission"])
        instrument.set_emission.assert_called_once_with(
            False,
            remote_enable_allowed=False,
        )


if __name__ == "__main__":
    unittest.main()
