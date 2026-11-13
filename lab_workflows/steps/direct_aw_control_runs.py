"""DirectAW 多控制子运行：子目录、有效前置继承、逐控制执行与封存。

子运行只借用设备，不接管安全收尾；校准和阶段状态各自独立。硬件激活由实验侧
注入 ``activate_control``，本模块只依赖 ``lab_workflows``。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar
import shutil

import yaml

from ..common import WorkflowCancelled
from ..experiment_runtime import check_cancelled, report_runtime_progress
from .run_directory import RunDirectory


_ControlWorkflow = TypeVar("_ControlWorkflow")


def make_control_directory(parent, relative, params, state):
    """在父运行目录下建立单个控制的子目录，并写入继承后的父配置。"""
    version = Path(relative).parent.parent.name
    path = parent.root / "controls" / version
    raw, results = path / "raw", path / "results"
    raw.mkdir(parents=True, exist_ok=False)
    results.mkdir()
    directory = RunDirectory(path, raw, results, path / "experiment_config.yaml")
    config = yaml.safe_load(parent.config_path.read_text(encoding="utf-8"))
    for key in ("controls", "controls_sealed"):
        config.pop(key, None)
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


def create_control_run(cls: type[_ControlWorkflow], params, *, parent_directory, project_root,
                       session_id, devices, shared_calibration=None, stage_states=None, previous=None,
                       affected_stages: Iterable[str] = frozenset()) -> tuple[_ControlWorkflow, Path]:
    """创建已选定一个控制的子运行，返回 ``(ctx, run_dir)``。

    校准和阶段状态独立复制；有效前置仍按 affected_stages 从内存状态继承，
    不额外要求 completed，也不修改旧运行。实验侧按需保留只返回 ctx 的包装。
    """
    ctx = cls(params, project_root=project_root, session_id=session_id)
    ctx.devices = devices
    if shared_calibration is not None:
        ctx.state["calibration"] = deepcopy(shared_calibration)
    ctx.state.update(ctx._control_run_metadata())
    relative = params.control_waveform_files[0]
    ctx.directory = make_control_directory(parent_directory, relative, params, ctx.state)
    if stage_states is not None:
        ctx.states = deepcopy(stage_states)
    if previous is not None:
        inherit_control(previous, ctx, affected_stages)
    return ctx, ctx.directory.root


def execute_controls(parent, step, *, activate_control: Callable[[Any, str], None],
                     check_cancelled: Callable[[], None] = check_cancelled):
    """按控制顺序执行同一节点；每个控制前检查取消，硬件激活由调用方注入。"""
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
            activate_control(ctx, step)
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
                parent.state["data_files"] = [*(path for path in parent.state.get("data_files", []) if path.startswith("raw/")), *(f"controls/{name}/{path}"
                    for name, child in parent.controls.items() for path in child.state["data_files"])]
                parent.save()
            except Exception as save_error:
                if failure is None:
                    raise
                parent.state["state_save_error"] = str(save_error)


def seal_controls(parent, *, check_cancelled: Callable[[], None] = check_cancelled):
    """全部子快照落盘后才提交父容器封存标记；写失败不得伪装完整运行。"""
    try:
        for ctx in parent.controls.values():
            check_cancelled()
            ctx.state.update(completion_status="completed", finished_at=datetime.now().isoformat(),
                             safety_shutdown=parent.state["safety_shutdown"])
            ctx.save()
        parent.state["controls_sealed"] = True
        parent.save()
    except BaseException as error:
        parent.state.update(controls_sealed=False,
            completion_status="cancelled" if isinstance(error, WorkflowCancelled) else "failed",
            failure_reason=str(error))
        try:
            parent.save()
        except Exception as save_error:
            parent.state["state_save_error"] = str(save_error)
        raise
