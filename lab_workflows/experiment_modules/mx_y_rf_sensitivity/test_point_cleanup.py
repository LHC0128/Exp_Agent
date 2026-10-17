"""共享单工作点与网格调用方的收尾故障回归。"""

from importlib import import_module
from types import SimpleNamespace

import pytest
import yaml

from . import acquisition_steps


@pytest.mark.parametrize("quality_failure", [False, True])
def test_point_keeps_original_error_and_structured_cleanup_failure(
    tmp_path, monkeypatch, quality_failure
):
    error = (
        acquisition_steps.RPointQualityError("R 点不合格")
        if quality_failure else OSError("读数失败")
    )
    cleanup = OSError("RF 归零失败")

    def fail_read(*args, **kwargs):
        raise error

    def fail_cleanup(*args, **kwargs):
        raise cleanup

    monkeypatch.setattr(acquisition_steps, "_acquire_amplitude_scan", fail_read)
    monkeypatch.setattr(
        acquisition_steps, "_set_y_rf_zero_off_with_temperature_sequence", fail_cleanup
    )
    with pytest.raises(type(error)) as caught:
        acquisition_steps.acquire_mx_y_rf_point(
            SimpleNamespace(linewidth_mode="amplitude_equivalent"),
            tmp_path / "raw", {}, {}, 100.0, "fake-device"
        )
    assert caught.value is error
    assert error.safety_cleanup_errors == (cleanup,)


@pytest.mark.parametrize("module_name", [
    "mx_y_rf_power_optimization", "mx_y_rf_probe_detuning_optimization"
])
@pytest.mark.parametrize("cleanup_failed", [False, True])
def test_grid_continues_only_after_quality_failure_with_successful_cleanup(
    tmp_path, monkeypatch, module_name, cleanup_failed
):
    workflow = import_module(f"lab_workflows.experiment_modules.{module_name}.workflow")
    probe = "probe_detuning" in module_name
    params = SimpleNamespace(
        pump_power_start_v=0.1, pump_power_stop_v=0.1, pump_power_points=1,
        probe_power_start_v=0.1, probe_power_stop_v=0.2, probe_power_points=2,
        pzt_voltage_start_v=0.0, pzt_voltage_stop_v=0.0, pzt_voltage_points=1,
        pzt_settle_time_s=0.0,
    )
    error = acquisition_steps.RPointQualityError("R 点不合格")
    if cleanup_failed:
        error.safety_cleanup_errors = (OSError("RF 归零失败"),)
    calls = []

    def acquire(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            raise error
        return {"actual_response_rate_sa_s": 100.0, "actual_noise_rate_sa_s": 100.0}

    monkeypatch.setattr(workflow, "acquire_mx_y_rf_point", acquire)
    if probe:
        monkeypatch.setattr(workflow, "_set_probe_power", lambda *args: None)
        monkeypatch.setattr(workflow, "_set_pzt_row", lambda *args: {})
        monkeypatch.setattr(workflow, "_read_pzt_point", lambda *args: {})
    else:
        monkeypatch.setattr(workflow, "_set_power_point", lambda *args: None)
    raw = tmp_path / "raw"
    raw.mkdir()
    args = (params, SimpleNamespace(raw=raw), {"probe_laser_controller": object()}, {}, 100.0, "fake")
    if cleanup_failed:
        with pytest.raises(acquisition_steps.RPointQualityError) as caught:
            workflow._acquire_grid(*args)
        assert caught.value is error
        assert len(calls) == 1
    else:
        result = workflow._acquire_grid(*args)
        assert result["invalid_quality_point_count"] == 1
        assert result["completed_point_count"] == 1
        assert len(calls) == 2
    manifest = yaml.safe_load((raw / "point_manifest.yaml").read_text(encoding="utf-8"))
    records = list(manifest["points"].values())
    assert records[1]["status"] == ("pending" if cleanup_failed else "completed")
