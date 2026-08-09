"""Mx Y RF 灵敏度长飘采集工作流。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ...common import WorkflowCancelled, find_project_root, load_mapping
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import DeviceSession, create_run_directory
from ..mx_y_rf_sensitivity.analysis import analyze as analyze_single
from ..mx_y_rf_sensitivity.models import MxYRFParams
from ..mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
    acquire_mx_y_rf_point,
    configure_mx_y_rf_outputs,
    connect_mx_y_rf_devices,
    safe_shutdown,
    snapshot_mx_y_rf_state,
)
from .analysis import refresh_trend
from .models import MxYRFDriftParams


EXPERIMENT_ID = "mx-y-rf-sensitivity-drift"
DATA_TYPE = "Mx_Y_RF_Sensitivity_Drift"
CHILD_DATA_TYPE = "Mx_Y_RF_Sensitivity"
EXECUTION_MODE = "typed_workflow"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_manifest(path: Path, records: list[dict[str, Any]]) -> None:
    payload = {"schema_version": 1, "records": records}
    content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(content, encoding="utf-8")
        temporary.unlink(missing_ok=True)


def _planned_records(params: MxYRFDriftParams) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    first_offset = 0.0 if params.drift_start_immediately else params.drift_interval_s
    for index in range(params.planned_cycle_count):
        records.append(
            {
                "cycle_index": index,
                "scheduled_offset_s": first_offset + index * params.drift_interval_s,
                "status": "pending",
                "child_run_dir": None,
                "actual_start_utc": None,
                "start_lag_s": None,
                "analysis": None,
                "error": None,
            }
        )
    return records


def _child_parameters(params: MxYRFDriftParams) -> dict[str, Any]:
    names = set(MxYRFParams.external_names())
    return {key: value for key, value in params.to_external().items() if key in names}


def _set_record_error(record: dict[str, Any], status: str, error: Exception) -> None:
    record["status"] = status
    record["error"] = str(error)


def _sleep_until(deadline: float) -> None:
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _refresh_safely(parent_root: Path) -> None:
    try:
        refresh_trend(parent_root)
    except Exception as exc:
        print(f"长飘趋势更新失败，后续轮次继续: {exc}")


def run(params: MxYRFDriftParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
    parent = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    manifest_path = parent.raw / "drift_manifest.yaml"
    records = _planned_records(params)
    _write_manifest(manifest_path, records)
    parent.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        scan_mode="repeated_measurement",
        interval_s=params.drift_interval_s,
        duration_s=params.drift_duration_s,
        planned_cycle_count=len(records),
        child_experiment_id="mx-y-rf-sensitivity",
        manifest="raw/drift_manifest.yaml",
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = connect_mx_y_rf_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        parent.update_config(device_snapshot=snapshot_mx_y_rf_state(devices, channels))
        actual_response_rate, clock_sources = configure_mx_y_rf_outputs(
            params, devices, channels, mapping
        )
        parent.update_config(clock_sources=clock_sources)
        device_id = str(mapping["lockin_r"]["device_id"])
        start_mono = time.monotonic()

        for record in records:
            check_cancelled()
            deadline = start_mono + float(record["scheduled_offset_s"])
            _sleep_until(deadline)
            check_cancelled()
            actual_start = time.monotonic()
            record["actual_start_utc"] = _utc_now()
            record["start_lag_s"] = max(0.0, actual_start - deadline)
            record["status"] = "running"
            child = create_run_directory(
                CHILD_DATA_TYPE,
                f"{params.run_tag}_cycle_{record['cycle_index']:03d}",
                _child_parameters(params),
                schema_version=2,
                project_root=root,
            )
            record["child_run_dir"] = str(child.root)
            child.update_config(
                experiment_id="mx-y-rf-sensitivity",
                data_type=CHILD_DATA_TYPE,
                execution_mode=EXECUTION_MODE,
                parent_experiment_id=EXPERIMENT_ID,
                parent_run_dir=str(parent.root),
                drift_cycle_index=record["cycle_index"],
                scheduled_offset_s=record["scheduled_offset_s"],
                actual_start_utc=record["actual_start_utc"],
                geometry={
                    "main_field": "Z",
                    "pump": "Z",
                    "probe": "X",
                    "rf_field": "Y",
                    "x_field_output": "OFF",
                },
                linewidth_mode=params.linewidth_mode,
                compatibility={
                    "stable_data_type": CHILD_DATA_TYPE,
                    "schema_version": 2,
                },
                acquisition_signals=["R"],
                r_point_quality={
                    "criterion": "std(R) <= threshold",
                    "std_threshold_v": params.r_bad_point_std_threshold_v,
                    "max_attempts": params.r_point_max_attempts,
                },
            )
            _write_manifest(manifest_path, records)
            _refresh_safely(parent.root)
            try:
                point = acquire_mx_y_rf_point(
                    params,
                    child.raw,
                    devices,
                    channels,
                    actual_response_rate,
                    device_id,
                    results_dir=child.results,
                )
                actual_response_rate = float(point["actual_response_rate_sa_s"])
                child.update_config(
                    completion_status="completed",
                    actual_rates={
                        "response_sa_s": actual_response_rate,
                        "noise_sa_s": float(point["actual_noise_rate_sa_s"]),
                    },
                    frequency_gate_fit=point["frequency_gate_fit"],
                    data_files=[
                        "raw/amplitude_scan.npz",
                        *[
                            f"raw/noise_{index:03d}.npz"
                            for index in range(params.noise_n_avg)
                        ],
                    ],
                )
            except RPointQualityError as exc:
                _set_record_error(record, "invalid_quality", exc)
                child.update_config(
                    completion_status="invalid_quality",
                    failure_reason=str(exc),
                )
                _write_manifest(manifest_path, records)
                _refresh_safely(parent.root)
                continue
            except Exception as exc:
                _set_record_error(record, "acquisition_failed", exc)
                child.update_config(
                    completion_status="failed",
                    failure_reason=str(exc),
                )
                _write_manifest(manifest_path, records)
                _refresh_safely(parent.root)
                raise

            try:
                result = analyze_single(child.root)
            except Exception as exc:
                _set_record_error(record, "analysis_failed", exc)
                child.update_config(analysis_status="failed", analysis_error=str(exc))
            else:
                record["status"] = "completed" if result.get("success") else "invalid"
                record["analysis"] = None
                child.update_config(analysis_status="completed")
            _write_manifest(manifest_path, records)
            _refresh_safely(parent.root)

        completion_status = "completed"
        parent.update_config(completion_status=completion_status, completed_utc=_utc_now())
        return parent.root
    except WorkflowCancelled as exc:
        failure_reason = str(exc)
        completion_status = "cancelled"
        if parent.config_path.exists():
            parent.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
            )
        raise
    except Exception as exc:
        failure_reason = str(exc)
        if parent.config_path.exists():
            parent.update_config(completion_status="failed", failure_reason=failure_reason)
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        if shutdown_report.errors:
            print("安全关闭警告: " + "; ".join(shutdown_report.errors))
        if parent.config_path.exists():
            parent.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
            )
        _write_manifest(manifest_path, records)
        _refresh_safely(parent.root)


def main() -> int:
    run(load_runtime_params(MxYRFDriftParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
