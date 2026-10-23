"""逐控制 × Scale 独立校相与测量；可共用手动 RF 相位，其余校相不跨点继承。"""
from copy import deepcopy
from datetime import datetime
import json
import hashlib
from pathlib import Path
import time
import yaml
from ...steps import RunDirectory
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..noise_spectrum_xy.state_io import write_state
from ..rf_sensitivity_direct_aw import acquisition as shared, phase
from ..rf_sensitivity_direct_aw._helpers import hf2_snapshot
from .preparation import scaled_envelope
from . import run_format


def make_point(parent, control, scale):
    if hashlib.sha256(Path(control.envelope["source_waveform_path"]).read_bytes()).hexdigest() != control.state["control_source_sha256"]:
        raise ValueError("控制源在本轮初始化后已改变，请重建本轮采集")
    ctx = type(control)(control.params, project_root=parent.root, session_id=parent.session_id)
    ctx.devices = parent.devices
    ctx.state.update(raw_format_version=run_format.RAW_FORMAT_VERSION,
        phase_calibration_scope=run_format.PHASE_CALIBRATION_SCOPE, phase_calibration_steps={},
        calibration_scale=float(scale), control_scale=float(scale),
        control_source_sha256=control.state["control_source_sha256"], calibration_source=".",
        temperature_control=deepcopy(parent.state.get("temperature_control")))
    ctx.envelope = scaled_envelope(ctx.params, control.envelope, scale)
    ctx.state.update(control_scale_factor=ctx.envelope["control_scale_factor"],
                     effective_control_scale=ctx.envelope["effective_control_scale"])
    label = f"{scale:.9f}" if abs(scale) < 1e9 else repr(float(scale))
    if float(label) != scale:
        label = repr(float(scale))
    path = control.directory.root / "scales" / f"scale_{label}"
    raw, results = path / "raw", path / "results"
    raw.mkdir(parents=True, exist_ok=False)
    results.mkdir()
    ctx.directory = RunDirectory(path, raw, results, path / "experiment_config.yaml")
    ctx.directory.update_config(parameters=ctx.params.to_external(), schema_version=ctx.params.schema_version, **ctx.state)
    return ctx


def calibrate_point(ctx):
    """复用 DirectAW 校相与 RF 相位设置；A11 可只应用本实验共用的手动相位。"""
    ctx.state["phase_calibration_status"] = "running"
    for item in run_format.POINT_CALIBRATION_STAGES:
        check_cancelled()
        ctx.current_step = item
        ctx.state["point_stage"] = item
        ctx.state["phase_calibration_steps"][item] = {"status": "running", "error": ""}
        report_runtime_progress("A13", f"{Path(ctx.params.arb_waveform_file).parent.parent.name} · Scale {ctx.state['control_scale']:g}：{item} 校相/准备")
        try:
            ctx._execute_step(item)
        except BaseException as exc:
            quality = isinstance(exc, phase.CalibrationQualityError) or (item == "A11" and isinstance(exc, ValueError))
            ctx.state["phase_calibration_status"] = "failed"
            ctx.state["phase_calibration_steps"][item] = {"status": "quality_failed" if quality else "failed", "error": str(exc)}
            if quality and not isinstance(exc, phase.CalibrationQualityError):
                raise phase.CalibrationQualityError(str(exc)) from exc
            raise
        finally:
            ctx.current_step = None
        ctx.states[item] = {"status": "completed", "error": ""}
        ctx.state["phase_calibration_steps"][item] = deepcopy(ctx.states[item])
        ctx.save()
    if reason := run_format.point_calibration_block_reason(ctx.state | {"parameters": ctx.params.to_external()}):
        ctx.state["phase_calibration_status"] = "failed"
        raise phase.CalibrationQualityError(reason)


def acquire_point(ctx):
    """校相通过后使用当前点自己的相位，复用 DirectAW 分组采集和恢复。"""
    calibrate_point(ctx)
    ctx.state["point_stage"] = "response"
    shared.record_response_settings(ctx)
    ctx.started = time.monotonic()
    shared.acquire_response(ctx)
    if ctx.params.include_noise:
        ctx.state["point_stage"] = "noise"
        shared.acquire_noise(ctx)
        shared.restore_response(ctx)
    ctx.state["point_stage"] = "bandwidth"
    ctx.state["hf2_bandwidth_snapshot"] = hf2_snapshot(ctx.devices["hf2"], ctx.params)
    shared.record_stage_settings(ctx, "bandwidth")
    shared.acquire_bandwidth(ctx)
    ctx.state.update(completion_status="acquired", finished_at=datetime.now().isoformat())
    ctx.save()


def acquire_scales(parent):
    from .resume import copy_completed_points
    manifest = {"raw_format_version": run_format.RAW_FORMAT_VERSION,
                "phase_calibration_scope": run_format.PHASE_CALIBRATION_SCOPE,
                "points": copy_completed_points(parent) if parent.params.resume_run_id else []}
    reused = {(row["control"], row["scale"]) for row in manifest["points"]}
    path = parent.directory.raw / "point_manifest.json"
    total = len(parent.controls) * len(parent.params.control_scales)
    started = time.monotonic()
    write_state(path, manifest)
    parent.state["phase_calibration_status"] = "running"
    for version, control in parent.controls.items():
        for scale in parent.params.control_scales:
            check_cancelled()
            if (version, float(scale)) in reused:
                continue
            ctx = make_point(parent, control, scale)
            record = dict(control=version, scale=float(scale),
                          control_scale_factor=ctx.state["control_scale_factor"],
                          effective_control_scale=ctx.state["effective_control_scale"],
                          path=ctx.directory.root.relative_to(parent.directory.root).as_posix(), status="running")
            manifest["points"].append(record)
            write_state(path, manifest)
            failure = None
            try:
                acquire_point(ctx)
                record["status"] = "acquired"
            except BaseException as exc:
                failure = exc
                record.update(status="failed", error=str(exc))
                ctx.state.update(completion_status="failed", failure_reason=str(exc))
                try:
                    ctx.save()
                except Exception:
                    pass
                raise
            finally:
                parent.response_config, parent.demod_mode = ctx.response_config, ctx.demod_mode
                parent.outputs.update({key: value for key, value in ctx.outputs.items() if value != "未知"})
                record["phase_calibration_status"] = ctx.state["phase_calibration_status"]
                calibrated = sum(row.get("phase_calibration_status") == "completed" for row in manifest["points"])
                parent.state.update(calibration_points_completed=calibrated,
                    phase_calibration_status="completed" if calibrated == total else "failed" if failure else "running",
                    current_point=dict(control=version, scale=float(scale), phase_calibration_steps=deepcopy(ctx.state["phase_calibration_steps"])))
                for name in ("xy_phase_calibration", "rf_phase_calibration"):
                    parent.state[name] = deepcopy(ctx.state[name])
                for counter in ("response_points_completed", "noise_records_completed", "bandwidth_points_completed"):
                    record[counter] = ctx.state[counter]
                    parent.state[counter] = sum(row.get(counter, 0) for row in manifest["points"])
                try:
                    write_state(path, manifest)
                    parent.save()
                except Exception as save_error:
                    if failure is None:
                        raise
                    parent.state["state_save_error"] = str(save_error)
            done = len(manifest["points"])
            report_runtime_progress("A13", f"{version} · Scale {scale:g}，已完成 {done}/{total}", 25 + 65 * done / total,
                estimated_remaining_seconds=(time.monotonic() - started) / (done - len(reused)) * (total - done))
    parent.save()


def seal_points(parent):
    """只有父运行安全收尾成功后，才把所有子运行标为完成。"""
    manifest_path = parent.directory.raw / "point_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["points"]:
        path = parent.directory.root / record["path"] / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config.update(completion_status="completed", safety_shutdown=parent.state["safety_shutdown"])
        path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        record["status"] = "completed"
    write_state(manifest_path, manifest)
