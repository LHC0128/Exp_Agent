"""按阶段逐版本激活控制，保留各自的校相与响应设置。"""
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
import shutil
from lockin_amplifier import demod
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import RunDirectory
from ..rf_sensitivity_direct_aw import excitation as shared_excitation
from ..rf_sensitivity_direct_aw._helpers import wait


def make_control_directory(parent, relative, params, state):
    version = Path(relative).parent.parent.name
    path = parent.root / "controls" / version
    raw, results = path / "raw", path / "results"
    raw.mkdir(parents=True, exist_ok=False)
    results.mkdir()
    directory = RunDirectory(path, raw, results, path / "experiment_config.yaml")
    import yaml
    config = yaml.safe_load(parent.config_path.read_text(encoding="utf-8"))
    config.update(parameters=params.to_external(), **state)
    directory.update_config(**config)
    return directory


def inherit_control(previous, current, affected):
    """只复制未失效前置的原始产物，重跑目录不带入旧噪声记录。"""
    for stage in previous.stages:
        if stage.id in affected or previous.states[stage.id]["status"] != "completed":
            continue
        for pattern in stage.artifacts:
            if not pattern.startswith(("raw/", "results/")):
                continue
            for path in previous.directory.root.glob(pattern):
                if path.is_file():
                    destination = current.directory.root / path.relative_to(previous.directory.root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
        for path in previous.directory.raw.glob(f"z_sum_{stage.id}_*.npz"):
            shutil.copy2(path, current.directory.raw / path.name)
    if "A09" not in affected:
        current.xy_phase = previous.xy_phase
        current.state["xy_phase_calibration"] = deepcopy(previous.state["xy_phase_calibration"])
    if "A10" not in affected:
        current.response_config = previous.response_config
    if "A11" not in affected:
        current.z_positive_phase = previous.z_positive_phase
        current.state["rf_phase_calibration"] = deepcopy(previous.state["rf_phase_calibration"])
    current.state["calibration"] = deepcopy(previous.state["calibration"])
    current.state["actual_rates"] = deepcopy(previous.state["actual_rates"])
    for stage_id, name in (("A13", "response"), ("A12", "calibration"), ("A14", "noise"), ("A13B", "bandwidth")):
        if stage_id in affected:
            continue
        for key in (f"hf2_{name}_snapshot", f"{name}_output_snapshot", f"{name}_acquisition"):
            if key in previous.state:
                current.state[key] = deepcopy(previous.state[key])
        if name in previous.state.get("stage_frequencies", {}):
            current.state.setdefault("stage_frequencies", {})[name] = deepcopy(previous.state["stage_frequencies"][name])
    if "A12" not in affected:
        current.state["phase_calibration_status"] = previous.state["phase_calibration_status"]
    for stage, counter in (("A13", "response_points_completed"), ("A14", "noise_records_completed"), ("A13B", "bandwidth_points_completed")):
        if stage not in affected:
            current.state[counter] = previous.state[counter]
    current.state["data_files"] = ["raw/" + path.name for path in current.directory.raw.glob("*") if path.is_file()]
    current.save()


def activate(ctx, step):
    if step == "A09":
        return
    if ctx.xy_phase is None or not ctx.xy_phase.converged:
        raise ValueError("当前控制版本 XY 校相未通过")
    shared_excitation.upload_xy(ctx.devices["signals"], ctx.devices["channels"], ctx.params,
        ctx.envelope, ctx.xy_phase.final_phase_deg)
    demod.configure_demodulator(ctx.devices["hf2"], replace(ctx.d0_config,
        phase=ctx.phase0.phase_shift_deg, time_constant=ctx.params.demod0_tc_meas))
    if ctx.response_config is not None:
        demod.configure_demodulator(ctx.devices["hf2"], ctx.response_config, for_acquisition=True)
        ctx.demod_mode = "response"
    wait(ctx.params.system_settle_s)


def execute_controls(parent, step):
    for index, (version, ctx) in enumerate(parent.controls.items()):
        check_cancelled()
        report_runtime_progress(step, f"{version}：第 {index + 1}/{len(parent.controls)} 个控制版本")
        ctx.d0_config, ctx.phase0 = parent.d0_config, parent.phase0
        if "demod0" in parent.state["calibration"]:
            ctx.state["calibration"]["demod0"] = deepcopy(parent.state["calibration"]["demod0"])
        ctx.current_step = step
        ctx.states[step] = {"status": "running", "error": ""}
        failure = None
        try:
            activate(ctx, step)
            ctx._execute_step(step)
            ctx.states[step] = {"status": "completed", "error": ""}
        except BaseException as exc:
            failure = exc
            ctx.states[step] = {"status": "failed", "error": str(exc)}
            ctx.state.update(completion_status="failed", failure_reason=str(exc))
            raise
        finally:
            parent.response_config, parent.demod_mode = ctx.response_config, ctx.demod_mode
            parent.outputs.update(ctx.outputs)
            try:
                ctx.save()
                parent.state.setdefault("controls", {})[version] = {
                    "source_file": ctx.params.arb_waveform_file,
                    "control_metrics": ctx.state["control_metrics"],
                    "steps": deepcopy(ctx.states), "xy_phase_calibration": ctx.state["xy_phase_calibration"],
                    "rf_phase_calibration": ctx.state["rf_phase_calibration"],
                }
                parent.state["xy_phase_calibration"] = ctx.state["xy_phase_calibration"]
                parent.state["rf_phase_calibration"] = ctx.state["rf_phase_calibration"]
                parent.state["phase_calibration_status"] = ctx.state["phase_calibration_status"]
                for counter in ("response_points_completed", "noise_records_completed", "bandwidth_points_completed"):
                    parent.state[counter] = sum(child.state[counter] for child in parent.controls.values())
                parent.state["data_files"] = ["raw/noise_waveform.npz", *(f"controls/{name}/{path}"
                    for name, child in parent.controls.items() for path in child.state["data_files"])]
                parent.save()
            except Exception as save_error:
                if failure is None:
                    raise
                parent.state["state_save_error"] = str(save_error)
