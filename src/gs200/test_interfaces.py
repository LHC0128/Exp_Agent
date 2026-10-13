"""GS200 连接回归，使用假 VISA 资源，不访问硬件。"""

import unittest
from unittest.mock import Mock, patch

from gs200 import GS200Instrument


class GS200ConnectionTests(unittest.TestCase):
    def test_socket_reads_identity_and_current_with_terminators(self):
        resource = Mock(read_termination=None, write_termination="\r\n")
        replies = {"*IDN?": "YOKOGAWA,GS210,9026D6597,2.02\r\n",
                   ":SOURce:LEVel?": "+8.0730E-3\r\n"}

        def query(command):
            if resource.read_termination != "\n" or resource.write_termination != "\n":
                raise TimeoutError("Socket 查询缺少消息终止符")
            return replies[command]

        resource.query.side_effect = query
        manager = Mock()
        manager.open_resource.return_value = resource
        with patch("gs200.instrument.pyvisa.ResourceManager", return_value=manager):
            instrument = GS200Instrument("TCPIP0::192.168.124.41::7655::SOCKET", timeout=1500)
            try:
                instrument.connect()
                self.assertEqual(instrument.idn(), "YOKOGAWA,GS210,9026D6597,2.02")
                self.assertAlmostEqual(instrument.get_current(), 0.008073)
                self.assertEqual(resource.timeout, 1500)
                resource.write.assert_not_called()
            finally:
                instrument.disconnect()
        resource.close.assert_called_once()
        manager.close.assert_not_called()

    def test_non_socket_keeps_visa_termination_defaults(self):
        for address in ("USB0::0x0B21::0x0039::90Z631552::INSTR",
                        "TCPIP0::192.168.124.41::INSTR", "GPIB0::1::INSTR"):
            with self.subTest(address=address):
                resource = Mock(read_termination=None, write_termination="\r\n")
                resource.query.return_value = "YOKOGAWA,GS210,TEST,2.02\r\n"
                manager = Mock()
                manager.open_resource.return_value = resource
                with patch("gs200.instrument.pyvisa.ResourceManager", return_value=manager):
                    instrument = GS200Instrument(address)
                    try:
                        instrument.connect()
                        self.assertIsNone(resource.read_termination)
                        self.assertEqual(resource.write_termination, "\r\n")
                    finally:
                        instrument.disconnect()

    def test_failed_identity_query_closes_session(self):
        resource = Mock()
        error = TimeoutError("身份查询超时")
        resource.query.side_effect = error
        manager = Mock()
        manager.open_resource.return_value = resource
        with patch("gs200.instrument.pyvisa.ResourceManager", return_value=manager):
            instrument = GS200Instrument("TCPIP0::192.168.124.41::INSTR")
            with self.assertRaises(TimeoutError) as caught:
                instrument.connect()
        self.assertIs(caught.exception, error)
        self.assertFalse(instrument.connected)
        resource.close.assert_called_once()
        manager.close.assert_not_called()

    def test_failed_open_drops_manager_without_closing_shared_backend(self):
        manager = Mock()
        manager.open_resource.side_effect = OSError("打开资源失败")
        with patch("gs200.instrument.pyvisa.ResourceManager", return_value=manager):
            instrument = GS200Instrument("TCPIP0::192.168.124.41::INSTR")
            with self.assertRaises(OSError):
                instrument.connect()
        self.assertFalse(instrument.connected)
        self.assertIsNone(instrument._rm)
        manager.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
