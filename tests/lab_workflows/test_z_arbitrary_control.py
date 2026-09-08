"""独立任意波模块的文件契约、顺序和故障保护测试；不连接硬件。"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import numpy as np

from lab_workflows import z_arbitrary_control as module
from lab_workflows.common import CancellationToken, WorkflowCancelled
from lab_workflows.instrument_control import ControlRevisionConflict


class ZArbitraryControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'data' / module.SOURCE_DIRECTORY / 'obbv5' / 'results' / 'corrected_control_waveform.npz'
        self.path.parent.mkdir(parents=True)
        self.data = {
            'time_s': np.arange(40) / 10000,
            'omega_ctrl_hz': np.linspace(-10, 10, 40),
            'target_current_a': np.linspace(-0.01, 0.01, 40),
            'normalized': np.linspace(-0.1, 0.1, 40),
            'voltage_v': np.linspace(-0.3, 0.3, 40),
            'repeat_frequency_hz': 250.0, 'amplitude_vpp': 6.0, 'offset_v': 0.0,
            'coupling_calibration_run': 'cal', 'coupling_calibration_sha256': 'c' * 64,
            'frequency_response_run': 'response', 'frequency_response_sha256': 'f' * 64,
        }
        self.write()
        self.service = module.ZArbitraryControlService(self.root)
        catalog = module.control.list_control_targets()
        self.request = {
            'action': 'configure', 'run_name': 'obbv5',
            'waveform_sha256': module.preview_source(self.root, 'obbv5')['waveform_sha256'],
            'device_library_revision': catalog['device_library_revision'],
            'physical_mapping_revision': catalog['physical_mapping_revision'],
            'settings': module.ZArbitrarySettings(),
        }
        self.instrument = MagicMock()
        self.instrument.get_voltage_unit.return_value = 'VPP'
        self.connect = patch.object(module.control, '_connect', return_value=self.instrument).start()
        patch.object(module.control, '_read_target_connected', return_value={'type': 'fake'}).start()
        self.addCleanup(patch.stopall)

    def write(self, **updates):
        np.savez(self.path, **{**self.data, **updates})

    def test_preview_preserves_voltage_and_time_without_hardware(self):
        preview = module.preview_source(self.root, 'obbv5')
        np.testing.assert_array_equal(preview['time_s'], self.data['time_s'])
        np.testing.assert_array_equal(preview['voltage_v'], self.data['voltage_v'])
        self.assertEqual(preview['amplitude_vpp'], 6)
        self.assertEqual(preview['maximum_v'], 0.3)
        self.assertEqual(preview['period_s'], 0.004)
        self.service.preflight(**self.request)
        self.connect.assert_not_called()

    def test_bad_files_are_independent_catalog_errors(self):
        bad = self.path.parents[2] / 'bad' / 'results' / self.path.name
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b'not a waveform')
        sources = module.list_sources(self.root)
        self.assertEqual([s['run_name'] for s in sources], ['bad', 'obbv5'])
        self.assertTrue(sources[0]['error'])
        self.assertIsNone(sources[1]['error'])

    def test_contract_rejects_corrupt_arrays_and_metadata(self):
        cases = [
            {'voltage_v': [1]}, {'normalized': np.full(40, np.nan)},
            {'normalized': np.full(40, 2.0)}, {'time_s': np.zeros(40)},
            {'repeat_frequency_hz': 200}, {'voltage_v': np.zeros(40)},
        ]
        for update in cases:
            with self.subTest(update=list(update)):
                self.write(**update)
                with self.assertRaises(ValueError):
                    module.preview_source(self.root, 'obbv5')
        self.connect.assert_not_called()

    def test_missing_field_and_path_escape(self):
        np.savez(self.path, time_s=[0, 1])
        with self.assertRaisesRegex(ValueError, '缺少字段'):
            module.preview_source(self.root, 'obbv5')
        with self.assertRaisesRegex(ValueError, '超出'):
            module.preview_source(self.root, '../../../outside')

    def test_changed_file_and_revision_fail_before_connect(self):
        self.write(omega_ctrl_hz=np.arange(40))
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.service.execute(**self.request)
        self.request['device_library_revision'] = 'stale'
        with self.assertRaises(ControlRevisionConflict):
            self.service.execute(**self.request)
        self.connect.assert_not_called()

    def test_unsafe_envelope_is_rejected_before_connect(self):
        self.write(amplitude_vpp=30, voltage_v=self.data['normalized'] * 15)
        self.request['waveform_sha256'] = module.preview_source(self.root, 'obbv5')['waveform_sha256']
        with self.assertRaises(ValueError):
            self.service.execute(**self.request)
        self.connect.assert_not_called()

    def test_configure_keeps_off_and_uploads_original_normalized_points(self):
        result = self.service.execute(**self.request)
        self.assertTrue(result['source_known'])
        self.assertEqual(result['last_applied']['state'], 'off')
        self.assertTrue(all(c.args[0] is False for c in self.instrument.set_output.call_args_list))
        np.testing.assert_allclose(self.instrument.setup_arbitrary.call_args.args[0], self.data['normalized'])
        self.instrument.set_frequency.assert_called_with(250.0, channel=1)
        self.instrument.set_amplitude.assert_called_with(6.0, channel=1)
        self.instrument.set_burst_trigger_slope.assert_called_with('NEGative', channel=1)
        self.instrument.set_burst_mode.assert_called_with('INFinity', channel=1)
        self.instrument.setup_square.assert_not_called()
        self.instrument.disconnect.assert_called_once()

    def test_linked_start_order_and_stop_ignore_edited_form_and_missing_file(self):
        self.request['settings'] = module.ZArbitrarySettings(
            link_trigger=True, trigger_frequency_hz=200, trigger_amplitude_vpp=4,
            trigger_offset_v=2, trigger_duty_percent=40, control_burst_phase_deg=390)
        self.request['action'] = 'configure_and_start'
        result = self.service.execute(**self.request)
        self.assertEqual(len(result['last_applied']['snapshots']), 2)
        self.connect.assert_called_once()
        self.instrument.setup_square.assert_called_once_with(
            freq=200, amplitude=4, offset=2, dcycle=40, phase=0.0, channel=2)
        self.instrument.set_output_load.assert_called_once_with('INFinity', channel=2)
        self.instrument.set_burst_phase.assert_called_once_with(30, channel=1)
        outputs = self.instrument.set_output.call_args_list
        self.assertEqual(outputs[:2], [call(False, channel=1), call(False, channel=2)])
        self.assertEqual(outputs[-2:], [call(True, channel=1), call(True, channel=2)])
        self.path.unlink()
        self.instrument.reset_mock()
        stopped = self.service.execute(action='stop',
            device_library_revision=self.request['device_library_revision'],
            physical_mapping_revision=self.request['physical_mapping_revision'],
            settings=module.ZArbitrarySettings(link_trigger=False))
        self.assertEqual(stopped['last_applied']['state'], 'off')
        self.assertTrue(stopped['last_applied']['link_trigger'])
        self.assertEqual(self.instrument.set_output.call_args_list,
                         [call(False, channel=1), call(False, channel=2)])
        self.instrument.setup_arbitrary.assert_not_called()

    def test_non_vpp_fails_without_writing(self):
        self.instrument.get_voltage_unit.return_value = 'VRMS'
        with self.assertRaisesRegex(ValueError, 'Vpp'):
            self.service.execute(**self.request)
        self.instrument.set_output.assert_not_called()
        self.instrument.disconnect.assert_called_once()

    def test_upload_failure_closes_both_channels(self):
        self.request['settings'] = replace(self.request['settings'], link_trigger=True)
        self.instrument.setup_arbitrary.side_effect = RuntimeError('upload failed')
        with self.assertRaisesRegex(RuntimeError, 'upload failed'):
            self.service.execute(**self.request)
        self.assertEqual(self.instrument.set_output.call_args_list[-2:],
                         [call(False, channel=1), call(False, channel=2)])
        self.assertFalse(self.service.status()['source_known'])
        self.instrument.disconnect.assert_called_once()

    def test_cancel_after_upload_closes_outputs_without_start(self):
        token = CancellationToken()
        self.request['settings'] = replace(self.request['settings'], link_trigger=True)
        self.request['action'] = 'configure_and_start'
        self.instrument.setup_arbitrary.side_effect = lambda *a, **k: token.cancel()
        with self.assertRaises(WorkflowCancelled):
            self.service.execute(**self.request, cancellation=token)
        self.assertTrue(all(c.args[0] is False for c in self.instrument.set_output.call_args_list))
        self.assertEqual(self.instrument.set_output.call_args_list[-1], call(False, channel=2))

    def test_revision_change_invalidates_record(self):
        self.service.execute(**self.request)
        catalog = module.control.list_control_targets()
        with patch.object(module.control, 'list_control_targets',
                          return_value={**catalog, 'device_library_revision': 'new'}):
            self.assertFalse(self.service.status()['source_known'])
            self.assertIsNone(self.service.status()['last_applied'])

    def test_trigger_validation(self):
        for update in ({'trigger_duty_percent': 10}, {'trigger_frequency_hz': -1},
                       {'trigger_offset_v': 20}, {'trigger_amplitude_vpp': float('nan')}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                module.ZArbitrarySettings(link_trigger=True, **update).validate()

    def test_same_channel_or_different_resource_rejects_link_before_connect(self):
        from dataclasses import replace as dc_replace
        resolve = module.control._resolve_control_target
        contexts = {key: resolve(key, self.request['device_library_revision'],
                                self.request['physical_mapping_revision'])
                    for key in ('Z_magnetic_field', 'Time_sequence_2')}
        z = contexts['Z_magnetic_field']
        trigger = contexts['Time_sequence_2']
        for invalid in ((trigger[0], trigger[1], z[2]),
                        (trigger[0], dc_replace(trigger[1], resource='OTHER'), trigger[2])):
            with self.subTest(invalid=invalid), patch.object(module.control, '_resolve_control_target',
                    side_effect=lambda key, *args: z if key == 'Z_magnetic_field' else invalid):
                with self.assertRaisesRegex(ValueError, '不同通道'):
                    self.service.preflight(**{**self.request, 'settings': module.ZArbitrarySettings(link_trigger=True)})
        self.connect.assert_not_called()

    def test_point_limit_checked_for_current_model(self):
        driver = module.control.SIGNAL_GENERATOR_DRIVERS['DG4000']
        with patch.object(driver, 'MAX_ARB_POINTS', 16):
            with self.assertRaisesRegex(ValueError, '点数'):
                self.service.execute(**self.request)
        self.connect.assert_not_called()

    def test_readback_failure_after_start_closes_both(self):
        self.request['action'] = 'configure_and_start'
        self.request['settings'] = module.ZArbitrarySettings(link_trigger=True)
        with patch.object(module.control, '_read_target_connected', side_effect=RuntimeError('read failed')):
            with self.assertRaisesRegex(RuntimeError, 'read failed'):
                self.service.execute(**self.request)
        self.assertEqual(self.instrument.set_output.call_args_list[-2:],
                         [call(False, channel=1), call(False, channel=2)])

    def test_stop_attempts_other_channel_after_failure(self):
        self.instrument.set_output.side_effect = [RuntimeError('failed'), None]
        with self.assertRaisesRegex(RuntimeError, '状态未知'):
            module.stop_outputs(self.instrument, 1, 2)
        self.assertEqual(self.instrument.set_output.call_count, 2)

    def test_existing_experiment_wrappers_delegate(self):
        from lab_workflows.steps.mx_z_optimal_control import (
            configure_z_optimal_control_output, configure_optimal_control_trigger,
        )
        with patch.object(module, 'configure_z_optimal_control_output') as output:
            configure_z_optimal_control_output('params', 'device', 1, 'theory', 'applied')
            output.assert_called_once_with('params', 'device', 1, 'theory', 'applied')
        with patch.object(module, 'configure_optimal_control_trigger') as trigger:
            configure_optimal_control_trigger('params', 'device', 2, output=False)
            trigger.assert_called_once_with('params', 'device', 2, output=False)


if __name__ == '__main__':
    unittest.main()
