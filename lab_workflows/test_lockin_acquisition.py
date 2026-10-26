"""自动采集选择、Scope 解调单位、真实时间轴及故障恢复的无硬件回归。"""

from dataclasses import replace
from importlib import import_module
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from scipy.signal import welch

from lockin_amplifier import (DAQConfig, DemodulatorConfig, AcquisitionRequest, daq, demod,
    acquisition_module, select_acquisition, nearest_scope_rate, lockin_capabilities,
    acquisition_metadata, result_rate, save_acquisition_metadata, load_acquisition_rate)
from lockin_amplifier.acquisition import restore_demod_streams
from .test_lockin import fake_instrument
from .common import WorkflowCancelled
from .devices import create_lockin
from .lockin_compatibility import lockin_preflight, require_lockin_compatibility
from .lockin_acquisition import ACQUISITION_STAGES, experiment_acquisition_requests


def test_stage_declarations_match_registered_defaults():
    from .experiments import get_experiment
    for experiment_id, stages in ACQUISITION_STAGES.items():
        definition = get_experiment(experiment_id)
        adapter = getattr(definition.defaults_saver, "__self__", None)
        if adapter is not None and hasattr(adapter, "defaults"):
            params = adapter.defaults()
            values = params.to_external() if hasattr(params, "to_external") else params
        else:
            values = {field["name"]: field["default"] for field in definition.schema()["fields"]}
        requests = experiment_acquisition_requests(experiment_id, values)
        expected = len(stages)
        if values.get("ACQUISITION_MODE") == "demod_rxy":
            expected = 0
        elif values.get("LINEWIDTH_MODE", "frequency_sweep") != "frequency_sweep":
            expected -= sum(stage[0] == "扫频" for stage in stages)
        assert len(requests) == expected, experiment_id


def test_partial_parameters_keep_default_acquisition_stages():
    from .experiments import get_experiment
    defaults = {field["name"]: field["default"] for field in get_experiment("rf-sensitivity-aw").schema()["fields"]}
    requests = experiment_acquisition_requests("rf-sensitivity-aw", {"NOISE_RATE": 200000})
    assert len(requests) == 2
    assert requests[1].requested_rate == 200000
    assert requests[1].duration > 0
    assert requests[1].demod_index == defaults["DEMOD3_IDX"]


@pytest.mark.parametrize("script", ["quick_test_scan.py", "optimize_scan.py", "optimize_scan_round2.py"])
def test_legacy_debug_scripts_reject_scope_before_connection(script):
    path = Path(__file__).resolve().parents[1] / "experiments" / script
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    end = next(index for index, node in enumerate(tree.body) if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "acquisition_preflight")
    prefix = ast.Module(body=tree.body[:end + 1], type_ignores=[])
    config = dict(model="HF2", acquisition_mode="scope")
    with patch("lab_workflows.common.load_mapping", return_value={"lockin_r": config}), \
         patch("lab_workflows.devices.create_lockin") as connect, pytest.raises(ValueError, match="不支持直接采集解调"):
        exec(compile(prefix, str(path), "exec"), {"__file__": str(path)})
    connect.assert_not_called()


@pytest.mark.parametrize("header", [False, True])
def test_daq_chunk_finished_flag_is_valid_and_loss_is_rejected(header):
    instr = fake_instrument()
    instr.acquisition_mode = "daq"
    cfg = DAQConfig(duration=4 / 50000, grid_cols=4, signal_paths=["sample.x"])
    burst = dict(value=[[1, 2, 3, 4]])
    flags = dict(flags=np.array([1 | 8 | 16]))
    burst.update(header=flags) if header else burst.update(flags)
    instr.daq.module.read.return_value = {f"{instr.demod_path(0)}/sample.x": [burst]}
    assert daq.acquire_data(instr, cfg, actual_rate=50000, requested_rate=50000)[0].values.size == 4
    if header:
        burst["header"]["flags"] |= 4
    else:
        burst["flags"] |= 4
    with pytest.raises(RuntimeError, match="丢样"):
        daq.acquire_data(instr, cfg, actual_rate=50000, requested_rate=50000)


@pytest.mark.parametrize("kind", ["xy", "known", "static"])
def test_analysis_uses_scope_sidecar_rate(tmp_path, kind):
    instr, cfg, _, _ = scope_fixture(("sample.x",))
    results = daq.acquire_data(instr, cfg, requested_rate=200000)
    wave = results[0].values + np.random.default_rng(13).normal(0, 1e-5, results[0].values.size)
    raw = tmp_path / "raw"
    raw.mkdir()
    config = dict(schema_version=3, hf2_daq=dict(actual_rate_Sa_s=200000, DAQ_duration_s=cfg.duration),
                  scan_params=dict(xy_envelope_voltage_V=[0, 1]))
    if kind == "static":
        from .experiment_modules.static_sensitivity.analysis_steps import _load_noise
        directory = raw / "noise_raw/gs200_range_000"
        directory.mkdir(parents=True)
        path = directory / "noise_0000.npy"
        np.save(path, wave)
        save_acquisition_metadata(path, results)
        frequency, psd, *_ = _load_noise(raw, dict(noise_duration=cfg.duration),
            dict(hf2_noise_rate_Sa_s=200000), [dict(directory=directory.name, current_range_set_A=0.01, current_range_actual_A=0.01)])
        assert frequency[np.argmax(psd[0])] == pytest.approx(234375 / 64)
    else:
        path = raw / ("waveform_C0000_on.npy" if kind == "known" else "waveform_C0000.npy")
        np.save(path, wave)
        save_acquisition_metadata(path, results)
        options = dict(bin_width_hz=234375 / 1024)
        if kind == "known":
            from .experiment_modules.noise_spectrum_xy_known_noise.analysis_core import compute_psd_matrix
            matrix, frequency, _, rows, info = compute_psd_matrix(tmp_path, config, options, "on")
        else:
            from .experiment_modules.noise_spectrum_xy.analysis import compute_psd
            matrix, frequency, _, rows, info = compute_psd(tmp_path, config, options)
        assert rows.tolist() == [0]
        assert info["actual_rate_sa_s"] == 234375
        assert frequency[np.argmax(matrix[0])] == pytest.approx(234375 / 64)


def test_prepared_streams_restore_when_request_is_rejected():
    instr, cfg, _, _ = scope_fixture()
    original = dict(instr.daq.nodes)
    demod.configure_demodulator(instr, DemodulatorConfig(rate=200000), for_acquisition=True)
    with pytest.raises(ValueError, match="超出内存"):
        daq.acquire_data(instr, replace(cfg, duration=100), requested_rate=200000)
    for key, value in original.items():
        if key.endswith(("/enable", "/rate")):
            assert instr.daq.nodes[key] == value


def test_scope_preparation_failure_restores_streams():
    instr, _, _, _ = scope_fixture()
    original = dict(instr.daq.nodes)
    setter = instr.set_double
    def fail_filter(path, value):
        if path.endswith("/timeconstant"):
            raise RuntimeError("配置失败")
        setter(path, value)
    instr.set_double = fail_filter
    with pytest.raises(RuntimeError, match="配置失败"):
        demod.configure_demodulator(instr, DemodulatorConfig(rate=200000), for_acquisition=True)
    for key, value in original.items():
        if key.endswith(("/enable", "/rate")):
            assert instr.daq.nodes[key] == value


def test_daq_rejects_discontinuous_timestamps():
    instr = fake_instrument()
    instr.acquisition_mode = "daq"
    instr.daq.module.read.return_value = {f"{instr.demod_path(0)}/sample.x": [
        dict(value=[[1, 2, 3, 4]], timestamp=np.array([[0, 60, 180, 240]], dtype=np.uint64))]}
    with pytest.raises(RuntimeError, match="丢样"):
        daq.acquire_data(instr, DAQConfig(signal_paths=["sample.x"]), requested_rate=50000)
    instr.daq.module.clear.assert_called_once()


def test_two_collector_paths_use_scope(tmp_path):
    from .steps.dispersion import acquire_dispersion_point
    from .steps.acquisition_window import DeferredAcquisitionWrites
    from .experiment_modules.noise_spectrum_xy_demod3_r.acquisition import acquire_demod_r_mean
    instr, cfg, _, waves = scope_fixture(("sample.x", "sample.y"))
    with DeferredAcquisitionWrites() as pending:
        point = acquire_dispersion_point(instr, demod_idx=0, duration_s=cfg.duration,
            requested_rate=200000, offset_v=0.0, path=tmp_path / "point.npz", cancellation_check=lambda: None,
            pending=pending)
    assert str(point["acquisition_module"]) == "scope"
    np.testing.assert_allclose(point["r_samples"], np.hypot(waves["sample.x"], waves["sample.y"]))
    assert not point["timestamp_ticks"].size
    with np.load(tmp_path / "point.npz") as saved:
        assert float(saved["actual_rate_sa_s"]) == 234375
    instr, cfg, _, waves = scope_fixture(("sample.r",))
    mean, metadata = acquire_demod_r_mean(None, "", cfg.duration, instr=instr, config=cfg,
        requested_rate=200000, return_metadata=True)
    assert mean == pytest.approx(waves["sample.r"].mean())
    assert metadata["acquisition_module"] == "scope"


@pytest.mark.parametrize("variant", ["x", "r"])
def test_direct_aw_noise_saves_final_scope_rate(tmp_path, variant):
    from .experiment_modules.rf_sensitivity_direct_aw import acquisition
    from .experiment_modules.rf_sensitivity_direct_aw.models import RFSensitivityDirectAWParams
    from .experiment_modules.rf_sensitivity_direct_aw_r.models import RFSensitivityDirectAWRParams
    instr, cfg, _, _ = scope_fixture(("sample.x",))
    params_type = RFSensitivityDirectAWParams if variant == "x" else RFSensitivityDirectAWRParams
    params = replace(params_type(), noise_rate=200000, noise_duration=cfg.duration, noise_n_avg=2)
    raw = tmp_path / "raw"
    raw.mkdir()
    ctx = SimpleNamespace(params=params, devices={"hf2": instr, "signals": Mock(), "channels": {"z": 1, "temp": 2}},
        state={"actual_rates": {}, "data_files": [], "stage_frequencies": {"noise": {"rf_frequency_Hz": 3000, "demod3_frequency_Hz": 3000}}},
        directory=SimpleNamespace(raw=raw, update_config=Mock()),
        response_config=DemodulatorConfig(demod_index=params.demod3_idx, input_source="aux_output1"), progress=Mock())
    with patch.object(acquisition, "arm_z"), patch.object(acquisition, "wait"), patch.object(acquisition, "record_stage_settings"):
        acquisition.acquire_noise(ctx)
    for path in raw.glob("noise_D3_*.npz"):
        with np.load(path) as data:
            assert str(data["acquisition_module"]) == "scope"
            assert float(data["actual_rate_Sa_s"]) == 234375
            np.testing.assert_allclose(np.diff(data["time_s"]), 1 / 234375, atol=1e-16)


@pytest.mark.parametrize("module_name, helper, run_directory, response", [
    ("mx_y_rf_sensitivity", "_acquire_valid_r_point", True, True),
    ("mx_y_rf_frequency_response", "_acquire_valid_r_point", False, True),
    ("mx_z_field_calibration", "_acquire_valid_r_point", False, False),
    ("mx_main_field_calibration", "_acquire_valid_r_point", False, False),
    ("mx_z_optimal_control_rf_sensitivity", "_acquire_valid_rxy_point", True, True),
])
def test_mx_quality_summary_and_waveform_keep_scope_rate(tmp_path, module_name, helper, run_directory, response):
    module = import_module(f"lab_workflows.experiment_modules.{module_name}.workflow")
    signals = ("sample.r", "sample.x", "sample.y") if helper.endswith("rxy_point") else ("sample.r",)
    instr, cfg, _, _ = scope_fixture(signals)
    params = SimpleNamespace(demod_idx=0, response_rate_sa_s=200000,
        frequency_duration_s=cfg.duration, r_point_max_attempts=1, r_bad_point_std_threshold_v=1,
        system_settle_s=max(0, 0), temp_switch_on_lag_s=0, )
    kwargs = dict(file_stem="point", metadata={}, actual_rate=50000, device_id=instr.device_id)
    if response:
        kwargs.update(set_y_rf=lambda: None, settle_time_s=0, duration_s=cfg.duration)
    directory = SimpleNamespace(raw=tmp_path) if run_directory else tmp_path
    with patch.object(module, "_temperature_gated_acquire", side_effect=lambda *args, acquire, **kwargs: acquire()):
        summary, attempt, filename = getattr(module, helper)(params, directory, {"hf2": instr}, {}, **kwargs)
    assert attempt == 0
    assert summary["actual_rate_sa_s"] == 234375
    with np.load(tmp_path / filename) as saved:
        assert saved["actual_rate_sa_s"] == saved["actual_rate_Sa_s"] == 234375
        assert str(saved["acquisition_module"]) == "scope"
        np.testing.assert_allclose(np.diff(saved["time_s"]), 1 / 234375, atol=1e-16)


def test_mx_frequency_aggregate_keeps_scope_rate(tmp_path):
    from .experiment_modules.mx_y_rf_frequency_response import workflow
    instr, cfg, _, _ = scope_fixture(("sample.r",))
    params = SimpleNamespace(demod_idx=0, response_rate_sa_s=200000,
        frequency_duration_s=cfg.duration, system_settle_s=0,
        r_point_max_attempts=1, r_bad_point_std_threshold_v=1)
    with patch.object(workflow, "build_rf_amplitude_axis", return_value=np.array([0.1])), \
         patch.object(workflow, "build_frequency_axis", return_value=np.array([3000, 4000])), \
         patch.object(workflow, "_temperature_gated_acquire", side_effect=lambda *args, acquire, **kwargs: acquire()):
        workflow._acquire_frequency_response(params, tmp_path, {"hf2": instr, "xy_field": Mock()}, {"y_rf": 1}, 50000, instr.device_id)
    with np.load(tmp_path / "frequency_response.npz", allow_pickle=True) as saved:
        assert saved["actual_rate_sa_s"] == 234375
        assert saved["r_mean_v"].shape == (1, 2)


def test_bell_bloom_point_summary_keeps_scope_rate(tmp_path):
    from .experiment_modules.bell_bloom_z_field_calibration import workflow
    instr, cfg, _, _ = scope_fixture(("sample.r",))
    params = SimpleNamespace(response_rate_sa_s=200000, frequency_duration_s=cfg.duration,
        system_settle_s=max(0, 0),  temp_switch_on_lag_s=0,
        r_point_max_attempts=1, r_bad_point_std_threshold_v=1)
    with patch.object(workflow, "set_temperature_switch"), patch.object(workflow, "sleep"):
        summary, filename = workflow.acquire_point(params, {"hf2": instr, "Temp_Switch": Mock()},
            {"Temp_Switch": 1}, tmp_path, {"frequency_index": 0}, 50000, instr.device_id)
    assert summary["actual_rate_sa_s"] == 234375
    with np.load(tmp_path / filename) as saved:
        assert saved["actual_rate_sa_s"] == 234375


def test_static_dispersion_stops_streams_before_outputs(tmp_path):
    from .experiment_modules.static_sensitivity import acquisition
    from .experiment_modules.static_sensitivity.models import StaticSensitivityParams
    instr, cfg, _, _ = scope_fixture(("sample.x", "sample.y"))
    params = replace(StaticSensitivityParams(), hf2_demod_rate=200000, dispersion_duration_s=cfg.duration)
    z, temp = Mock(), Mock()
    original = dict(instr.daq.nodes)
    def enable_output(*args, **kwargs):
        assert all(instr.get_int(f"{instr.demod_path(i)}/enable") == 0 for i in range(4))
    z.set_output.side_effect = enable_output
    with patch.object(acquisition, "wait_for_settle"), patch.object(acquisition, "validate_safety_limit"), patch.object(acquisition, "run_safety_shutdown", return_value=SimpleNamespace(errors=[])):
        acquisition.acquire_dispersion(params, z, 1, temp, 2, instr, tmp_path / "scan.npz")
    for key, value in original.items():
        if key.endswith(("/enable", "/rate")):
            assert instr.daq.nodes[key] == value


@pytest.mark.parametrize("model", ["HF2", "MFLI"])
@pytest.mark.parametrize("rate", [50000, 70000, 70000.01, 200000])
def test_request_boundary_and_explicit_priority(model, rate):
    expected = "scope" if model == "MFLI" and rate > 70000 else "daq"
    assert acquisition_module(model, rate) == expected
    assert acquisition_module(model, rate, "daq") == "daq"
    assert acquisition_module(model, rate, "scope") == "scope"
    cfg = DAQConfig(duration=0.01)
    caps = lockin_capabilities(model, ["MF-DIG"])
    assert select_acquisition(model, caps, cfg, rate).module == expected
    if model == "HF2":
        with pytest.raises(ValueError, match="不支持直接采集解调"):
            select_acquisition(model, caps, cfg, rate, mode="scope")


def test_nearest_rate_and_equal_distance():
    assert nearest_scope_rate(200000) == 234375
    assert nearest_scope_rate((234375 + 117187.5) / 2) == 234375
    assert nearest_scope_rate(1) == 60e6 / 2**15
    assert nearest_scope_rate(120e6) == 60e6
    for rate in [True, 0, -1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            nearest_scope_rate(rate)


@pytest.mark.parametrize("change", [dict(trigger_type=1), dict(trigger_delay=0.01),
    dict(grid_rows=2), dict(extra_paths=["/dev/auxins/0/sample"]),
    dict(signal_paths=["sample.x", "sample.frequency"]), dict(duration=100)])
def test_scope_preflight_rejects_unsupported_requests(change):
    caps = lockin_capabilities("MFLI", ["MF-DIG", "MF-MD"])
    with pytest.raises(ValueError):
        select_acquisition("MFLI", caps, replace(DAQConfig(), **change), 200000)
    with pytest.raises(ValueError, match="MF-DIG"):
        select_acquisition("MFLI", lockin_capabilities("MFLI"), DAQConfig(), 200000)


def scope_fixture(signals=("sample.x", "sample.y", "sample.r", "sample.theta")):
    instr = fake_instrument()
    base = f"/{instr.device_id}/scopes/0"
    length, rate = 1024, 234375.0
    cfg = DAQConfig(duration=length / rate, signal_paths=list(signals))
    t = np.arange(length) / rate
    x = 0.1 * np.cos(2 * np.pi * (rate / 64) * t)
    y = 0.1 * np.sin(2 * np.pi * (rate / 64) * t)
    physical = ("sample.x", "sample.y") if len(signals) > 2 or "sample.theta" in signals else signals
    waveforms = {"sample.x": x, "sample.y": y, "sample.r": np.hypot(x, y), "sample.theta": np.arctan2(y, x)}
    wave = np.stack([waveforms[signal] for signal in physical])
    for index in range(instr.capabilities["demodulators"]):
        instr.daq.nodes[f"{instr.demod_path(index)}/enable"] = 1
    for channel in range(2):
        instr.daq.nodes[f"{base}/channels/{channel}/limitlower"] = -4.0
        instr.daq.nodes[f"{base}/channels/{channel}/limitupper"] = 4.0
        instr.daq.nodes[f"{base}/channels/{channel}/inputselect"] = 8 + channel
    instr.daq.nodes[f"{base}/length"] = 2048
    timestamp = 2**62 + 500000
    record = dict(wave=wave, dt=1 / rate, timestamp=timestamp,
                  triggertimestamp=timestamp - int(length / rate * instr.clockbase), flags=0)
    instr.daq.module.read.return_value = {f"{base}/wave": [[record]]}
    return instr, cfg, record, waveforms


def test_scope_quadrature_derivation_time_axis_and_metadata(tmp_path):
    instr, cfg, record, waves = scope_fixture()
    original = dict(instr.daq.nodes)
    original_rate = instr.get_double(f"{instr.demod_path(0)}/rate")
    # 准备至稳定等待期间停流，滤波和内部信号链保留。
    stream_rate = demod.configure_demodulator(instr, DemodulatorConfig(rate=200000, time_constant=0.001), for_acquisition=True)
    assert stream_rate == original_rate
    assert not any(path.endswith("/rate") for path, _ in instr.daq.writes)
    assert all(instr.get_int(f"{instr.demod_path(i)}/enable") == 0 for i in range(4))
    results = daq.acquire_data(instr, cfg, actual_rate=stream_rate, requested_rate=200000)
    assert result_rate(results) == 234375
    assert [item.signal_name for item in results] == cfg.signal_paths
    for result in results:
        np.testing.assert_allclose(result.values, waves[result.signal_name], atol=1e-12)
        np.testing.assert_allclose(np.diff(result.time), 1 / 234375, atol=1e-16)
        assert result.record_slices == [(0, 1024)]
        assert result.config_snapshot["time_source"] == "scope_trigger_timestamp"
        assert result.config_snapshot["unit"] == ("rad" if result.signal_name.endswith("theta") else "V")
    assert "atan2" in results[0].config_snapshot["derived_signals"]["sample.theta"]
    for key, value in original.items():
        if key.endswith(("/enable", "/rate", "/inputselect", "/length")):
            assert instr.daq.nodes[key] == value
    assert not any(path.endswith(("limitlower", "limitupper")) for path, _ in instr.daq.writes)
    freq, psd = welch(results[0].values, fs=result_rate(results), nperseg=1024)
    assert freq[np.argmax(psd)] == 234375 / 64
    metadata = acquisition_metadata(results)
    assert metadata["requested_rate_Sa_s"] == 200000
    assert json.loads(metadata["acquisition_metadata_json"])["sample.x"]["demod_stream_rate"] == original_rate
    path = tmp_path / "waveform.npy"
    np.save(path, results[0].values)
    save_acquisition_metadata(path, results)
    assert load_acquisition_rate(path, 200000) == 234375
    assert load_acquisition_rate(tmp_path / "old.npy", 50000) == 50000


@pytest.mark.parametrize("model,mode,requested_rate,module", [
    ("HF2", "auto", 200000, "daq"), ("HF2", "auto", 450000, "daq"),
    ("MFLI", "auto", 200000, "scope"), ("MFLI", "auto", 450000, "scope"),
    ("MFLI", "daq", 200000, "daq"), ("MFLI", "scope", 50000, "scope"),
])
def test_xy_acquisition_rate_is_independent_of_phase_stream_rate(model, mode, requested_rate, module):
    from .experiment_modules.noise_spectrum_xy import acquisition
    from .experiment_modules.noise_spectrum_xy.models import NoiseSpectrumXYParams
    instr = fake_instrument(model)
    instr.acquisition_mode = mode
    params = replace(NoiseSpectrumXYParams(), hf2_demod_rate=10000, hf2_daq_rate=requested_rate)
    devices, base = {"hf2": instr}, instr.demod_path(0)
    with patch.object(acquisition, "wait"):
        acquisition.restore_demod(devices, params, 23.0)
        instr.daq.writes.clear()
        settings = acquisition.prepare_daq(devices, params, 23.0)
        expected_stream = 10000 if module == "scope" else requested_rate
        assert instr.get_double(f"{base}/rate") == expected_stream
        assert instr.get_int(f"{base}/enable") == int(module == "daq")
        rate_writes = [value for path, value in instr.daq.writes if path.endswith("/rate")]
        assert rate_writes == ([] if module == "scope" else [requested_rate])
        capture_rate = nearest_scope_rate(requested_rate) if module == "scope" else requested_rate
        assert settings[0] == capture_rate
        assert settings[1] == int(capture_rate * params.hf2_daq_duration)
        assert settings[2]["nperseg"] == np.ceil(capture_rate / params.analysis_bin_width_hz)
        ctx = SimpleNamespace(devices=devices, params=params, calibrated_phase=23.0, daq_settings=settings)
        from .steps.lockin import verify_lockin_acquisition_mode
        writes_before_verification = list(instr.daq.writes)
        with patch.object(instr, "get_double", wraps=instr.get_double) as readback:
            assert verify_lockin_acquisition_mode(instr, demod_index=params.hf2_demod_idx,
                requested_rate_hz=params.hf2_daq_rate, acquisition_rate_hz=settings[0],
                time_constant_s=params.hf2_daq_tc, phase_deg=23.0) is None
            explicit_reads = list(readback.call_args_list)
            readback.reset_mock()
            assert acquisition.verify_daq_mode(ctx) is None
            assert readback.call_args_list == explicit_reads
            fields = ("timeconstant", "phaseshift") if module == "scope" else ("rate", "timeconstant", "phaseshift")
            assert [item.args[0] for item in explicit_reads] == [f"{base}/{field}" for field in fields]
        assert instr.daq.writes == writes_before_verification
        acquisition.restore_demod(devices, params, 23.0)
        assert instr.get_double(f"{base}/rate") == 10000
        assert instr.get_int(f"{base}/enable") == 1


def test_xy_family_uses_explicit_lockin_verification():
    from contextlib import ExitStack

    cases = (("noise_spectrum_xy", "A12"), ("noise_spectrum_xy_known_noise", "A13"),
             ("noise_spectrum_xy_probe_am", "A14"))
    for name, step in cases:
        workflow = import_module(f"lab_workflows.experiment_modules.{name}.workflow")
        hfi = object()
        params = SimpleNamespace(hf2_demod_idx=2, hf2_daq_rate=200000.0,
                                 hf2_demod_rate=10000.0, hf2_daq_tc=0.0005, hf2_demod_tc=0.03)
        ctx = SimpleNamespace(devices={"hf2": hfi, "dg_comp": object(), "dg_sweep": object(),
                                       "dg_probe_am": object()}, params=params,
                              daq_settings=(234375.0, 16, {}), calibrated_phase=37.5,
                              demod_mode="daq", xy_result=SimpleNamespace(converged=True),
                              noise_waveform=object(), z_waveform=None, limits={},
                              mapping={"Probe_AOM_AM": {"channel": 2}})
        error = RuntimeError("只读锁相校验失败哨兵")
        with ExitStack() as stack:
            verify = stack.enter_context(patch.object(workflow, "verify_lockin_acquisition_mode", side_effect=error))
            acquire = stack.enter_context(patch.object(workflow.acquisition, "acquire_scan"))
            xy_phase = workflow if name == "noise_spectrum_xy_probe_am" else workflow.phase
            stack.enter_context(patch.object(xy_phase, "verify_xy_mode"))
            if name == "noise_spectrum_xy_known_noise":
                stack.enter_context(patch.object(workflow.excitation, "verify_noise_waveform"))
            elif name == "noise_spectrum_xy_probe_am":
                stack.enter_context(patch.object(workflow.excitation, "verify_am_noise"))
                stack.enter_context(patch.object(workflow.excitation, "verify_z_noise"))
            with pytest.raises(RuntimeError) as caught:
                workflow.Workflow._execute_step(ctx, step)
            assert caught.value is error
            verify.assert_called_once_with(hfi, demod_index=2, requested_rate_hz=200000.0,
                acquisition_rate_hz=234375.0, time_constant_s=0.0005, phase_deg=37.5)
            assert verify.call_args.args[0] is hfi
            assert all(isinstance(value, (int, float)) for value in verify.call_args.kwargs.values())
            acquire.assert_not_called()


@pytest.mark.parametrize("signal", ["sample.x", "sample.y", "sample.r", "sample.theta"])
def test_single_native_scope_component(signal):
    instr, cfg, _, waves = scope_fixture((signal,))
    results = daq.acquire_data(instr, cfg, requested_rate=200000)
    np.testing.assert_allclose(results[0].values, waves[signal])
    derived = results[0].config_snapshot["derived_signals"]
    assert derived == ({"sample.theta": "atan2(sample.y, sample.x)"} if signal == "sample.theta" else {})


@pytest.mark.parametrize("mode", ["daq", "scope"])
def test_demod_components_keep_current_and_phase_units(mode):
    instr, cfg, _, waves = scope_fixture()
    instr.acquisition_mode = mode
    instr.daq.nodes[f"{instr.demod_path(0)}/adcselect"] = 1
    if mode == "daq":
        instr.daq.module.read.return_value = {f"{instr.demod_path(0)}/{signal}": [dict(value=[wave])]
                                            for signal, wave in waves.items()}
    results = daq.acquire_data(instr, cfg, requested_rate=200000)
    for result in results:
        assert result.config_snapshot["unit"] == ("rad" if result.signal_name.endswith("theta") else "A")
        np.testing.assert_allclose(result.values, waves[result.signal_name], atol=1e-12)


@pytest.mark.parametrize("target_rate, expected", [(50000, "daq"), (200000, "scope")])
def test_device_control_selects_from_requested_grid_rate(tmp_path, target_rate, expected):
    from . import lockin_control
    instr, cfg, _, waves = scope_fixture(("sample.x", "sample.y", "sample.r"))
    instr.daq.nodes[f"{instr.demod_path(0)}/rate"] = 200000
    points = round(target_rate * cfg.duration)
    if expected == "daq":
        instr.daq.module.read.return_value = {f"{instr.demod_path(0)}/{signal}": [dict(value=[wave[:points]])]
                                            for signal, wave in waves.items()}
    directory = SimpleNamespace(root=tmp_path, raw=tmp_path, update_config=Mock())
    with patch.object(lockin_control, "create_run_directory", return_value=directory):
        lockin_control.capture(instr, "mfli", 0, dict(mode="demod", duration=cfg.duration, points=points))
    snapshot = directory.update_config.call_args.kwargs["records"][0]["config_snapshot"]
    assert snapshot["acquisition_module"] == expected
    assert snapshot["requested_rate"] == points / cfg.duration
    assert snapshot["demod_stream_rate"] == 200000


@pytest.mark.parametrize("fault", ["lost", "clipped", "size", "nan", "dt", "timeout", "cancel", "read"])
def test_scope_failure_always_restores(fault):
    instr, cfg, record, _ = scope_fixture()
    original = dict(instr.daq.nodes)
    callback = None
    if fault == "lost": record["flags"] = 1
    elif fault == "clipped": record["wave"][0, 0] = 4
    elif fault == "size": record["wave"] = record["wave"][:, :-1]
    elif fault == "nan": record["wave"][0, 0] = np.nan
    elif fault == "dt": record["dt"] = 0
    elif fault == "read": instr.daq.module.read.side_effect = RuntimeError("读取失败")
    else:
        instr.daq.module.getInt.return_value = 0
        if fault == "cancel": callback = Mock(side_effect=[None, None, WorkflowCancelled("取消")])
    with patch("lockin_amplifier.scope.time.monotonic", side_effect=[0, 1]), pytest.raises((RuntimeError, TimeoutError, WorkflowCancelled)):
        daq.acquire_data(instr, cfg, requested_rate=200000, timeout=0.1, cancellation_check=callback)
    for key, value in original.items():
        assert instr.daq.nodes[key] == value
    instr.daq.module.clear.assert_called_once()


def test_explicit_daq_and_requested_boundary_survive_rate_rounding():
    instr = fake_instrument()
    instr.acquisition_mode = "daq"
    base = instr.demod_path(0)
    instr.daq.module.read.return_value = {f"{base}/sample.x": [dict(value=[[1, 2, 3, 4]])]}
    results = daq.acquire_data(instr, DAQConfig(duration=4 / 200000, grid_cols=4, signal_paths=["sample.x"]),
                               actual_rate=201000, requested_rate=200000)
    assert result_rate(results) == pytest.approx(200000)
    assert results[0].config_snapshot["demod_stream_rate"] == 201000
    instr.acquisition_mode = "auto"
    result = daq.acquire_data(instr, DAQConfig(duration=4 / 70000, grid_cols=4, signal_paths=["sample.x"]),
                              actual_rate=72000, requested_rate=70000)[0]
    assert result.config_snapshot["acquisition_module"] == "daq"


def test_connected_options_recheck_before_outputs():
    request = AcquisitionRequest("噪声", 200000, 0.1)
    instr = create_lockin(dict(instrument="lockin_amplifier", model="MFLI", device_id="dev30661"), acquisition_requests=[request])
    server = Mock()
    server.getString.side_effect = ["MFLI", "MF-MD MF-F5M"]
    with patch("lockin_amplifier.base._zi.ziDAQServer", return_value=server), pytest.raises(ValueError, match="MF-DIG"):
        instr.connect()
    assert not instr.connected
    server.setInt.assert_not_called()
    server.setDouble.assert_not_called()
    server.disconnectDevice.assert_called_once()


def test_offline_requests_and_session_snapshot():
    config = dict(instrument="lockin_amplifier", model="MFLI", device_id="dev30661", acquisition_mode="auto",
                  capabilities=dict(options=["MF-DIG", "MF-MD", "MF-F5M"]))
    mapping = {"lockin_r": config}
    values = dict(RF_INPUT_ROUTE="internal", RF_AUX_OUTPUT_IDX=1, DEMOD3_IDX=3, DEMOD3_RATE=50000,
                  SCAN_POINT_DURATION_S=0.1, NOISE_RATE=200000, NOISE_DURATION=0.1)
    assert lockin_preflight("rf-sensitivity-aw", values, mapping=mapping) == []
    require_lockin_compatibility("rf-sensitivity-aw", values, mapping=mapping)
    instr = create_lockin(config)
    assert len(instr.acquisition_requests) == 2
    config["acquisition_mode"] = "daq"
    assert instr.acquisition_mode == "auto"
    values["NOISE_DURATION"] = 100
    assert any("超出内存" in message for message in lockin_preflight("rf-sensitivity-aw", values, mapping=mapping | {"lockin_r": {**config, "acquisition_mode": "auto"}}))
