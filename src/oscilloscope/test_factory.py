"""统一示波器创建、结果与 NPZ 往返的无硬件契约测试。"""

from unittest.mock import Mock

import numpy as np
import pytest

from dho900 import DHO900Instrument, DHO900Acquisition
from sds_acquisition import SDSInstrument, SDSAcquisition, AcquisitionResult
from . import create_oscilloscope, create_scope_acquisition, save_to_npz, load_npz


def test_both_models_use_identical_factories_without_connection(monkeypatch):
    transport = Mock()
    monkeypatch.setattr("pyvisa.ResourceManager", transport)
    for kind, model, driver, acquirer in [
        ("sds_acquisition", "SDS", SDSInstrument, SDSAcquisition),
        ("oscilloscope", "DHO900", DHO900Instrument, DHO900Acquisition),
        ("oscilloscope", "DHO924S", DHO900Instrument, DHO900Acquisition),
    ]:
        config = {"instrument": kind, "model": model, "resource": "TCPIP0::192.0.2.21::INSTR"}
        scope = create_oscilloscope(config)
        collector = create_scope_acquisition(scope, check_cancelled=lambda: None, maximum_points=1000)
        assert isinstance(scope, driver)
        assert isinstance(collector, acquirer)
        assert collector._maximum_points == 1000
        for method in ("apply_config", "start_acquisition", "stop_acquisition", "acquire_channel", "acquire_all"):
            assert callable(getattr(collector, method))
    transport.assert_not_called()


def test_factory_has_no_model_fallback_or_automatic_address_selection():
    for config in [
        {"instrument": "oscilloscope", "model": "DG900", "resource": "x"},
        {"instrument": "sds_acquisition", "model": "DHO900", "resource": "x"},
        {"instrument": "oscilloscope", "model": "DHO900"},
        {"instrument": "oscilloscope", "model": "DHO900", "resource": "http://192.0.2.21/"},
    ]:
        with pytest.raises(ValueError):
            create_oscilloscope(config)
    with pytest.raises(TypeError):
        create_scope_acquisition(object())


def test_factory_forwards_nested_connection_parameters():
    scope = create_oscilloscope({"instrument": "oscilloscope", "model": "DHO900",
                                "resource": "TCPIP0::192.0.2.21::INSTR",
                                "connection": {"timeout": 5000, "read_chunk_points": 321}})
    assert scope.timeout == 5000
    assert scope.read_chunk_points == 321


def result(model):
    return AcquisitionResult(channel=1, raw_data=np.array([], dtype=np.int16), voltage=np.array([1.25, -0.5]),
                             time=np.array([-0.1, -0.099]), preamble_dict={"model": model, "point_num": 2},
                             config_snapshot={"sample_rate_policy": "strict"}, timestamp=123)


def test_common_npz_default_preserves_actual_time_and_voltage_without_fake_adc(tmp_path):
    captured = result("DHO900")
    target = save_to_npz(str(tmp_path / "capture.npz"), [captured], {"sampling_rate": 1000})
    restored, config = load_npz(target)
    np.testing.assert_array_equal(restored[0].time, captured.time)
    np.testing.assert_array_equal(restored[0].voltage, captured.voltage)
    assert restored[0].raw_data.size == 0
    assert restored[0].preamble_dict == captured.preamble_dict
    assert config == {"sampling_rate": 1000, "scope_acquisition_snapshots": {
        "ch1": {"sample_rate_policy": "strict"},
    }}
    assert restored[0].config_snapshot["scope_acquisition_snapshots"]["ch1"] == captured.config_snapshot


@pytest.mark.parametrize("mode", ["raw", "voltage_only"])
def test_dho_ascii_cannot_be_saved_as_sds_adc_encoding(tmp_path, mode):
    path = tmp_path / "invalid.npz"
    with pytest.raises(ValueError, match="原始 ADC"):
        save_to_npz(str(path), [result("DHO900")], save_mode=mode)
    assert not path.exists()
