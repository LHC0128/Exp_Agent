"""设备自检脚本的假仪器回归：不连接任何硬件。"""
import pytest

from tools import check_devices


class FakeDevice:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.connected = False
        self.disconnected = False

    def connect(self):
        if self.fail:
            raise TimeoutError("模拟连接超时")
        self.connected = True

    def disconnect(self):
        self.disconnected = True


def build_mapping():
    return {
        "Z_magnetic_field": {"instrument": "signal_generator", "resource": "USB::Z"},
        "lockin_r": {"instrument": "lockin_amplifier", "resource": "mfli://x"},
        "temperature": {"instrument": "tec_controller", "resource": "COM9"},
        "without_resource": {"instrument": "gs200"},
    }


def test_probe_reports_each_mapped_device(monkeypatch):
    created = {}

    def factory(config):
        def make():
            device = FakeDevice()
            created[config["instrument"]] = device
            return device
        return make

    monkeypatch.setattr(check_devices, "_factory", factory)
    results = check_devices.probe(build_mapping())

    # 没有 resource 的条目被跳过；其余每台都连过并断开。
    assert [item["key"] for item in results] == ["Z_magnetic_field", "lockin_r", "temperature"]
    assert all(item["ok"] for item in results)
    assert all(item["error"] == "" for item in results)
    assert all(device.disconnected for device in created.values())


def test_probe_marks_failure_and_keeps_going(monkeypatch):
    def factory(config):
        return lambda: FakeDevice(fail=config["resource"] == "mfli://x")

    monkeypatch.setattr(check_devices, "_factory", factory)
    results = {item["key"]: item for item in check_devices.probe(build_mapping())}

    assert results["lockin_r"]["ok"] is False
    assert "模拟连接超时" in results["lockin_r"]["error"]
    assert results["Z_magnetic_field"]["ok"] is True


def test_main_returns_failure_code_when_a_device_fails(monkeypatch, capsys):
    monkeypatch.setattr(check_devices, "load_mapping", lambda root: build_mapping())
    monkeypatch.setattr(check_devices, "_factory",
                        lambda config: (lambda: FakeDevice(fail=config["resource"] == "mfli://x")))
    code = check_devices.main([])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out and "lockin_r" in out


def test_main_exits_two_when_hardware_gate_denies(monkeypatch, capsys):
    monkeypatch.setattr(check_devices, "load_mapping", lambda root: build_mapping())

    def denied(config):
        raise check_devices.HardwareAccessDenied("不在运行目录")

    monkeypatch.setattr(check_devices, "_factory", denied)
    code = check_devices.main([])
    assert code == 2
    assert "硬件门控拒绝" in capsys.readouterr().out
