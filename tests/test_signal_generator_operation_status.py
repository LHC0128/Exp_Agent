"""DG4000 上传完成确认与错误队列行为。"""

import unittest
from unittest.mock import MagicMock

from signal_generator import DG4000Instrument


class DG4000OperationStatusTests(unittest.TestCase):
    def setUp(self):
        self.device = DG4000Instrument('FAKE')
        self.device.query = MagicMock()

    def test_operation_complete_accepts_scpi_positive_one(self):
        for response in ('1', '+1'):
            self.device.query.return_value = response
            self.device.wait_for_operation_complete()
        self.device.query.assert_called_with('*OPC?')

    def test_operation_not_complete_fails(self):
        self.device.query.return_value = '0'
        with self.assertRaises(RuntimeError):
            self.device.wait_for_operation_complete()

    def test_reports_all_queued_errors(self):
        self.device.query.side_effect = ['-100,"Command error"', '-200,"Execution error"', '0,"No error"']
        with self.assertRaisesRegex(RuntimeError, 'Command error.*Execution error'):
            self.device.raise_for_errors()
        self.assertEqual(self.device.query.call_count, 3)

    def test_clean_queue_succeeds(self):
        self.device.query.return_value = '+0,"No error"'
        self.device.raise_for_errors()


if __name__ == '__main__':
    unittest.main()
