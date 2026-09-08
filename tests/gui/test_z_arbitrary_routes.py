"""任意波工具 API 的无硬件契约测试。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'GUI'))
from backend import main
from backend.schemas import ZArbitraryActionBody
from lab_workflows.instrument_control import ControlRevisionConflict


class ZArbitraryRoutesTests(unittest.TestCase):
    def body(self, **updates):
        return ZArbitraryActionBody.model_validate({
            'action': 'configure', 'device_library_revision': 'devices',
            'physical_mapping_revision': 'mapping', 'run_name': 'obbv5',
            'waveform_sha256': 'sha', **updates,
        })

    def test_routes_have_typed_preview_and_state_contracts(self):
        paths = main.app.openapi()['paths']
        for suffix in ('sources', 'preview/{run_name}', 'state'):
            self.assertIn('/api/tools/z-arbitrary-control/' + suffix, paths)
        self.assertIn('ZArbitraryStatus', str(paths['/api/tools/z-arbitrary-control/state']))

    def test_action_uses_hardware_job_and_revalidates_in_runner(self):
        with patch.object(main.z_arbitrary_service, 'preflight') as preflight, \
             patch.object(main.z_arbitrary_service, 'execute') as execute, \
             patch.object(main.manager, 'create') as create:
            main.z_arbitrary_action(self.body())
            preflight.assert_called_once()
            create.assert_called_once()
            self.assertEqual(create.call_args.args[0], 'z-arbitrary-control')
            self.assertNotIn('hardware_required', create.call_args.kwargs)
            job = MagicMock()
            create.call_args.args[1](job)
            self.assertEqual(execute.call_args.kwargs['cancellation'], job.cancellation)

    def test_revision_conflict_rejects_enqueue(self):
        with patch.object(main.z_arbitrary_service, 'preflight', side_effect=ControlRevisionConflict('已变化')), \
             patch.object(main.manager, 'create') as create:
            with self.assertRaises(HTTPException) as caught:
                main.z_arbitrary_action(self.body())
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(caught.exception.headers['X-Error-Code'], 'revision_conflict')
            create.assert_not_called()

    def test_rejects_extra_waveform_controls_and_nonfinite_numbers(self):
        for settings in ({'amplitude_vpp': 12}, {'control_burst_phase_deg': float('nan')},
                         {'trigger_duty_percent': 90}):
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                self.body(settings=settings)

    def test_preview_error_is_client_error(self):
        with patch.object(main, 'preview_source', side_effect=ValueError('文件无效')):
            with self.assertRaises(HTTPException) as caught:
                main.z_arbitrary_preview('bad')
            self.assertEqual(caught.exception.status_code, 400)


if __name__ == '__main__':
    unittest.main()
