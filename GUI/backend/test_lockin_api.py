"""锁相放大器配置互斥、严格请求和队列采集的无硬件回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from . import main
from .schemas import LockinSelectionBody, ControlLockinCaptureBody, LockinSettings
from lab_workflows.common import CancellationToken


class LockinApiTests(unittest.TestCase):
    def test_selection_busy_does_not_write(self):
        body = LockinSelectionBody(device_id="mfli", device_library_revision="library", physical_mapping_revision="mapping")
        with patch.object(main.manager, "try_acquire_short_hardware", return_value=False), patch.object(main, "select_lockin_device") as select:
            with self.assertRaises(HTTPException) as raised:
                main.update_lockin_selection(body)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.headers["X-Error-Code"], "hardware_busy")
        select.assert_not_called()

    def test_device_library_save_with_mapping_draft_obeys_hardware_lock(self):
        body = main.DeviceLibraryBody(base_revision="library", devices={}, mapping={}, physical_mapping_revision="mapping")
        with patch.object(main.manager, "try_acquire_short_hardware", return_value=False), patch.object(main, "save_device_library") as save:
            with self.assertRaises(HTTPException) as raised:
                main.update_device_library(body)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.headers["X-Error-Code"], "hardware_busy")
        save.assert_not_called()

    def test_device_library_save_passes_both_revisions_and_draft_in_one_call(self):
        body = main.DeviceLibraryBody(base_revision="library", devices={}, mapping={"lockin_r": {"device_id": "mfli"}},
                                    network_resources=[], physical_mapping_revision="mapping")
        with patch.object(main, "_with_configuration_lock", side_effect=lambda action: action()), \
             patch.object(main, "save_device_library", return_value={"physical_mappings": {"revision": "saved"}}) as save:
            self.assertEqual(main.update_device_library(body), {"physical_mappings": {"revision": "saved"}})
        save.assert_called_once_with({}, base_revision="library", mapping=body.mapping, physical_mapping_revision="mapping",
                                     network_resources=[], root=main.ROOT)

    def test_capture_uses_queue_and_resolves_at_execution(self):
        body = ControlLockinCaptureBody(device_library_revision="library", physical_mapping_revision="mapping",
                                       settings={"mode": "demod", "duration": 2, "points": 100})
        manager = Mock()
        with patch.object(main, "manager", manager), patch.object(main, "capture_control_target_lockin", return_value={"model": "MFLI"}) as capture:
            main.acquire_control_lockin("lockin_r", body)
            capture.assert_not_called()
            kind, runner = manager.create.call_args.args
            self.assertEqual(kind, "lockin-capture")
            token = CancellationToken()
            result = runner(SimpleNamespace(cancellation=token))
            self.assertEqual(result["model"], "MFLI")
            self.assertEqual(capture.call_args.args[:3], ("lockin_r", "library", "mapping"))
            self.assertEqual(capture.call_args.kwargs["cancellation_check"], token.raise_if_cancelled)

    def test_strict_settings_reject_mixed_null_unknown_or_nonfinite(self):
        invalid = [{"demod": {"rate": float("nan")}}, {"demod": {"phase_deg": None}},
                   {"demod": {"unknown": 1}}, {"demod": {"phase_deg": 1}, "signal_output": {"index": 0, "enabled": False}},
                   {"signal_output": {"index": 0}}]
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                LockinSettings.model_validate(settings)


if __name__ == "__main__":
    unittest.main()
