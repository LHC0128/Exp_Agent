"""漂移子运行创建、标准单轮采集与即时分析；不复制单轮科学采集。"""
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled
from ..mx_y_rf_sensitivity.models import MxYRFParams
from ..mx_y_rf_sensitivity.run_format import RAW_FORMAT_VERSION as CHILD_RAW_FORMAT_VERSION
from ..mx_y_rf_sensitivity.acquisition_steps import RPointQualityError
from .run_format import CHILD_EXPERIMENT_ID, CHILD_DATA_TYPE, EXPERIMENT_ID
from .scheduling import _child_parameters, _set_record_error


def _create_child(ctx, record, create_directory):
    params = ctx.params
    suffix = 0
    while True:
        try:
            tag = f"{params.run_tag}_cycle_{record['cycle_index']:03d}" + (f"_{suffix:02d}" if suffix else "")
            child = create_directory(CHILD_DATA_TYPE, tag, _child_parameters(params),
                schema_version=MxYRFParams.schema_version, project_root=ctx.root)
            break
        except FileExistsError:
            suffix += 1
    record["child_run_dir"] = str(child.root)
    return child


def _initialize_child(ctx, child, record):
    params = ctx.params
    child.update_config(experiment_id=CHILD_EXPERIMENT_ID, data_type=CHILD_DATA_TYPE,
        raw_format_version=CHILD_RAW_FORMAT_VERSION, execution_mode="typed_workflow", completion_status="running",
        parent_experiment_id=EXPERIMENT_ID, parent_run_dir=str(ctx.directory.root),
        drift_cycle_index=record["cycle_index"], scheduled_offset_s=record["scheduled_offset_s"],
        actual_start_utc=record["actual_start_utc"],
        geometry=dict(main_field="Z", pump="Z", probe="X", rf_field="Y", x_field_output="OFF"),
        linewidth_mode=params.linewidth_mode,
        compatibility=dict(stable_data_type=CHILD_DATA_TYPE, schema_version=MxYRFParams.schema_version),
        acquisition_signals=["R"], phase_calibration_status="not_applicable",
        r_point_quality=dict(criterion="std(R) <= threshold", std_threshold_v=params.r_bad_point_std_threshold_v,
            max_attempts=params.r_point_max_attempts),
        **{key: ctx.state[key] for key in ("clock_sources", "device_snapshot", "temperature_control") if key in ctx.state})


def run_cycles(ctx, *, acquire, analyze, refresh, write_manifest, create_directory, clock, utc_now, wait_until):
    """按绝对起点依次运行全部计划，过期不跳轮；单轮质量/拟合失败继续。"""
    start = clock()
    ctx.state.update(schedule_started_utc=utc_now(), completion_status="running")
    ctx.save()
    for record in ctx.records:
        check_cancelled()
        deadline = start + float(record["scheduled_offset_s"])
        wait_until(deadline)
        check_cancelled()
        record.update(actual_start_utc=utc_now(), start_lag_s=max(0.0, clock() - deadline), status="running")
        child = _create_child(ctx, record, create_directory)
        try:
            _initialize_child(ctx, child, record)
            write_manifest(ctx.manifest_path, ctx.records)
            refresh(ctx.directory.root)
            ctx.publish(f"长飘轮次 {record['cycle_index'] + 1}/{len(ctx.records)}")
            # 单轮内部可能切到噪声或中途失败，退出父运行前统一恢复最后成功的响应配置。
            ctx.demod_mode = "unknown"
            point = acquire(ctx.params, child.raw, ctx.devices, ctx.channels,
                ctx.state["actual_rates"]["response_sa_s"], str(ctx.mapping["lockin_r"]["device_id"]),
                results_dir=child.results)
            ctx.demod_mode = "noise"
            ctx.state["actual_rates"] = dict(response_sa_s=float(point["actual_response_rate_sa_s"]),
                noise_sa_s=float(point["actual_noise_rate_sa_s"]))
            child.update_config(completion_status="completed", actual_rates=dict(ctx.state["actual_rates"]),
                frequency_gate_fit=point["frequency_gate_fit"],
                data_files=[path.relative_to(child.root).as_posix() for path in sorted(child.raw.glob("*.npz"))])
        except BaseException as exc:
            cleanup_errors = getattr(exc, "safety_cleanup_errors", ())
            quality = isinstance(exc, RPointQualityError) and not cleanup_errors
            cancelled = isinstance(exc, (WorkflowCancelled, KeyboardInterrupt))
            _set_record_error(record, "invalid_quality" if quality else "cancelled" if cancelled else "acquisition_failed", exc)
            if cleanup_errors:
                record["safety_cleanup_errors"] = [str(error) for error in cleanup_errors]
            try:
                child.update_config(completion_status="invalid_quality" if quality else "cancelled" if cancelled else "failed",
                    failure_reason=str(exc), safety_cleanup_errors=[str(error) for error in cleanup_errors])
                write_manifest(ctx.manifest_path, ctx.records)
                refresh(ctx.directory.root)
            except Exception as save_error:
                record["persistence_error"] = str(save_error)
                if quality:
                    raise
            if quality:
                continue
            raise
        try:
            result = analyze(child.root)
        except (WorkflowCancelled, KeyboardInterrupt) as exc:
            _set_record_error(record, "cancelled", exc)
            try:
                child.update_config(analysis_status="cancelled", analysis_error=str(exc))
            except Exception as save_error:
                record["persistence_error"] = str(save_error)
            raise
        except Exception as exc:
            _set_record_error(record, "analysis_failed", exc)
            child.update_config(analysis_status="failed", analysis_error=str(exc))
        else:
            record.update(status="completed" if result.get("success") else "invalid", analysis=None)
            child.update_config(analysis_status="completed", analysis_error=None)
        write_manifest(ctx.manifest_path, ctx.records)
        refresh(ctx.directory.root)
    ctx.state.update(completion_status="acquired", completed_cycle_count=len(ctx.records))
    ctx.save()
