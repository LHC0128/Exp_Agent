"""静磁场灵敏度脚本的可复用启动、日志与取消适配层。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..common import (
    CancellationToken,
    ProgressCallback,
    WorkflowCancelled,
    emit,
    find_project_root,
    validate_safety_limit,
)
from .models import StaticSensitivityParams


def preflight_static_sensitivity(params: StaticSensitivityParams) -> list[str]:
    """进行无硬件副作用的参数预检。"""
    errors: list[str] = []
    checks = {
        "Pump_laser_power": params.pump_laser_power,
        "Probe_laser_power": params.probe_laser_power,
        "temperature": params.temperature,
        "main_magnetic_field": params.main_magnetic_field,
        "X_magnetic_field": params.x_magnetic_field,
        "Y_magnetic_field": params.y_magnetic_field,
        "Z_magnetic_field": params.ramp_low,
        "PUMP_MOD_DUTY": params.pump_mod_duty,
        "Pump_modulation": params.pump_mod_amplitude,
        "Temp_Switch": params.temp_switch,
        "Time_sequence": params.time_sequence,
        "Time_sequence_2": params.time_sequence_2,
    }
    for name, value in checks.items():
        try:
            validate_safety_limit(name, float(value))
        except ValueError as exc:
            errors.append(str(exc))
    try:
        validate_safety_limit("Z_magnetic_field", params.ramp_high)
    except ValueError as exc:
        errors.append(str(exc))
    if params.ramp_low >= params.ramp_high:
        errors.append("Z 扫场下限必须小于上限")
    if not (0 < params.ramp_symmetry < 100):
        errors.append("斜波对称度必须位于 0 到 100 之间")
    if params.noise_n_avg < 1:
        errors.append("噪声平均次数必须大于 0")
    if not params.run_tag.strip():
        errors.append("运行标签不能为空")
    return errors


def _build_subprocess_environment(
    params: StaticSensitivityParams,
    cancel_file: Path,
) -> dict[str, str]:
    """构造固定使用 UTF-8 的实验子进程环境。"""
    environment = os.environ.copy()
    environment["LAB_STATIC_CONFIG"] = json.dumps(
        params.to_dict(), ensure_ascii=False
    )
    environment["LAB_CANCEL_FILE"] = str(cancel_file)
    environment["MPLBACKEND"] = "Agg"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _stage_from_line(line: str) -> tuple[str, float | None]:
    markers = (
        ("正在连接", "connect", 5),
        ("温度", "temperature", 15),
        ("相位校准", "phase", 30),
        ("色散", "dispersion", 45),
        ("噪声", "noise", 65),
        ("灵敏度", "analysis", 85),
        ("已保存", "save", 95),
    )
    for marker, stage, percent in markers:
        if marker in line:
            return stage, percent
    return "running", None


def run_static_sensitivity(
    params: StaticSensitivityParams,
    progress: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
) -> dict[str, Any]:
    """运行现有实验入口；参数与 GUI/脚本共用同一模型。"""
    errors = preflight_static_sensitivity(params)
    if errors:
        raise ValueError("；".join(errors))
    root = find_project_root()
    cancel_file = root / "data" / ".static_sensitivity_cancel"
    if cancel_file.exists():
        cancel_file.unlink()
    environment = _build_subprocess_environment(params, cancel_file)
    command = [
        str(root / "agent_exp_env" / "Scripts" / "python.exe"),
        "-u",
        str(root / "experiments" / "Static_Magnetic_Field_Sensitivity.py"),
    ]
    emit(progress, "start", "静磁场灵敏度实验已启动", 0)
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    try:
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if cancellation and cancellation.cancelled and not cancel_file.exists():
                cancel_file.parent.mkdir(parents=True, exist_ok=True)
                cancel_file.touch()
                emit(progress, "cancel", "正在等待实验到达安全停止点", level="warning")
            stage, percent = _stage_from_line(line)
            emit(progress, stage, line, percent)
        return_code = process.wait()
    finally:
        if cancel_file.exists():
            cancel_file.unlink()
    if cancellation and cancellation.cancelled:
        raise WorkflowCancelled("实验已在安全检查点停止")
    if return_code:
        raise RuntimeError(f"静磁场灵敏度实验异常结束，退出码 {return_code}")
    run_root = root / "data" / "Static_Magnetic_Field_Sensitivity"
    runs = sorted((path for path in run_root.glob("*") if path.is_dir()), reverse=True)
    run_dir = runs[0] if runs else None
    emit(progress, "complete", "静磁场灵敏度实验完成", 100)
    return {
        "run_dir": str(run_dir) if run_dir else None,
        "artifacts": [str(path) for path in (run_dir / "results").glob("*")]
        if run_dir and (run_dir / "results").exists()
        else [],
    }
