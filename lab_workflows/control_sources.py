"""最优控制波形与 Z 标定结果的公共只读解析。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import yaml

from .common import validate_safety_limit
from .current_feedback import CorrectedControlWaveform


_VERSION_PATTERN = re.compile(r"^v[1-9]\d*$")
_RUN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_DG4000_ARB_POINTS = 16384
THEORY_RESULTS_ROOT = Path(r"D:\Code\theory_agent\simulate\results")
CONTROL_SOURCE_SET_ROOTS = {
    "oc_sens": THEORY_RESULTS_ROOT / "oc_sens",
    "oc_broadband_v2": THEORY_RESULTS_ROOT / "oc_broadband_v2",
}


@dataclass(frozen=True, slots=True)
class TheoryControlSource:
    """理论控制文件及其周期元数据。"""

    version: str
    waveform_path: Path
    parameter_path: Path
    time_s: np.ndarray
    omega_ctrl_hz: np.ndarray
    repeat_frequency_hz: float
    theory_rf_frequency_hz: float
    waveform_sha256: str
    parameter_sha256: str


@dataclass(frozen=True, slots=True)
class ZCalibrationSource:
    """成功的 Mx Z 频率标定结果。"""

    run_name: str
    analysis_path: Path
    slope_hz_per_v: float
    intercept_hz: float
    r_squared: float
    analysis_sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AppliedControlWaveform:
    """换算为 DG4000 输出参数后的控制波形。"""

    voltage_v: np.ndarray
    normalized: np.ndarray
    amplitude_vpp: float
    offset_v: float
    minimum_v: float
    maximum_v: float
    output_minimum_v: float
    output_maximum_v: float
    max_abs_normalized: float


def applied_from_voltage(
    voltage_v: np.ndarray, *, amplitude_vpp: float, offset_v: float,
) -> AppliedControlWaveform:
    """在命令产生处检查固定幅度范围，硬件写入安全检查由输出步骤负责。"""
    voltage = np.asarray(voltage_v, dtype=float)
    if not np.all(np.isfinite(voltage)):
        raise ValueError("命令电压包含非有限值")
    lower, upper = offset_v - amplitude_vpp / 2, offset_v + amplitude_vpp / 2
    minimum, maximum = float(voltage.min()), float(voltage.max())
    if minimum < lower or maximum > upper:
        raise ValueError(
            f"命令电压 [{minimum:.9g}, {maximum:.9g}] V 超出 DG 范围 "
            f"[{lower:.9g}, {upper:.9g}] V"
        )
    return AppliedControlWaveform(
        voltage_v=voltage, normalized=(voltage - offset_v) / (amplitude_vpp / 2),
        amplitude_vpp=amplitude_vpp, offset_v=offset_v,
        minimum_v=minimum, maximum_v=maximum,
        output_minimum_v=lower, output_maximum_v=upper,
        max_abs_normalized=float(np.max(np.abs((voltage - offset_v) / (amplitude_vpp / 2)))),
    )


def resolve_control_results_root(source_set: str) -> Path:
    """将固定理论结果集名称解析为受控的结果根目录。"""
    key = str(source_set).strip()
    try:
        return CONTROL_SOURCE_SET_ROOTS[key]
    except KeyError as exc:
        choices = "、".join(CONTROL_SOURCE_SET_ROOTS)
        raise ValueError(f"CONTROL_SOURCE_SET 必须是 {choices}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_parameter_value(path: Path, key: str) -> str:
    with path.open(encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            parts = [item.strip() for item in line.split(",")]
            if len(parts) >= 2 and parts[0] == key:
                return parts[1]
            if line.startswith("#"):
                continue
    raise ValueError(f"理论参数文件缺少 {key}: {path}")


def load_theory_control(control_root: Path, version: str) -> TheoryControlSource:
    """读取一个 vN 控制版本并验证波形与理论参数一致。"""
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError("CONTROL_VERSION 必须采用 v1、v2 等 vN 格式")
    root = Path(control_root).expanduser().resolve()
    version_dir = (root / version).resolve()
    if root not in version_dir.parents:
        raise ValueError("CONTROL_VERSION 解析后超出控制结果根目录")
    waveform_path = version_dir / "waveforms" / "optimal_control_waveform.csv"
    parameter_path = version_dir / "parameters" / "optimal_control_params.csv"
    if not waveform_path.is_file():
        raise FileNotFoundError(f"未找到最优控制波形: {waveform_path}")
    if not parameter_path.is_file():
        raise FileNotFoundError(f"未找到最优控制参数: {parameter_path}")

    first_line = waveform_path.read_text(encoding="utf-8").splitlines()[0].strip().lower()
    broadband_time_ms = "time_ms" in first_line
    broadband_result = Path(control_root).name == "oc_broadband_v2"
    values = np.loadtxt(
        waveform_path,
        delimiter=",",
        comments="#",
        skiprows=1 if broadband_time_ms else 0,
        ndmin=2,
    )
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError(f"控制波形必须是 time_s,Omega_ctrl_Hz 两列: {waveform_path}")
    if values.shape[0] < 2:
        raise ValueError("控制波形至少需要 2 个采样点")
    if values.shape[0] > MAX_DG4000_ARB_POINTS:
        raise ValueError(
            f"控制波形点数 {values.shape[0]} 超过 DG4000 上限 "
            f"{MAX_DG4000_ARB_POINTS}"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError(f"控制波形包含 NaN 或无穷值: {waveform_path}")

    time_s = np.asarray(values[:, 0], dtype=float)
    if broadband_time_ms:
        time_s *= 1e-3
    omega_ctrl_hz = np.asarray(values[:, 1], dtype=float)
    steps = np.diff(time_s)
    if np.any(steps <= 0):
        raise ValueError("控制波形时间轴必须严格递增")
    step_s = float(np.median(steps))
    if not np.allclose(steps, step_s, rtol=1e-6, atol=max(1e-15, step_s * 1e-9)):
        raise ValueError("控制波形时间轴必须等间隔")
    repeat_frequency_hz = 1.0 / (time_s.size * step_s)

    try:
        theory_version = _parse_parameter_value(parameter_path, "# Output version")
    except ValueError:
        if not broadband_result:
            raise
        # oc_broadband_v2 的参数文件没有版本标记，版本由目录名确定。
        theory_version = version
    if theory_version != version:
        raise ValueError(
            f"理论参数版本 {theory_version!r} 与目录版本 {version!r} 不一致"
        )
    try:
        theory_rf_frequency_hz = float(
            _parse_parameter_value(parameter_path, "f_rf_Hz")
        )
    except ValueError:
        if not broadband_result:
            raise
        # 宽带结果用波形时间轴表达重复周期，参数文件不重复保存该字段。
        theory_rf_frequency_hz = repeat_frequency_hz
    if not np.isfinite(theory_rf_frequency_hz) or theory_rf_frequency_hz <= 0:
        raise ValueError("理论 f_rf_Hz 必须是正有限值")
    if not np.isclose(
        repeat_frequency_hz,
        theory_rf_frequency_hz,
        rtol=1e-6,
        atol=1e-6,
    ):
        raise ValueError(
            "控制波形时间轴频率与理论 f_rf_Hz 不一致: "
            f"{repeat_frequency_hz:.9g} Hz != {theory_rf_frequency_hz:.9g} Hz"
        )
    return TheoryControlSource(
        version=version,
        waveform_path=waveform_path,
        parameter_path=parameter_path,
        time_s=time_s,
        omega_ctrl_hz=omega_ctrl_hz,
        repeat_frequency_hz=repeat_frequency_hz,
        theory_rf_frequency_hz=theory_rf_frequency_hz,
        waveform_sha256=_sha256(waveform_path),
        parameter_sha256=_sha256(parameter_path),
    )


def load_z_calibration(project_root: Path, run_name: str) -> ZCalibrationSource:
    """读取并验证一个成功的 Mx Z 标定运行。"""
    if not _RUN_PATTERN.fullmatch(run_name):
        raise ValueError("Z_CALIBRATION_SOURCE_RUN 只能包含字母、数字、点、下划线和短横线")
    analysis_path = (
        Path(project_root)
        / "data"
        / "Mx_Z_Field_Calibration"
        / run_name
        / "results"
        / "analysis.yaml"
    )
    if not analysis_path.is_file():
        raise FileNotFoundError(f"未找到 Z 标定分析结果: {analysis_path}")
    with analysis_path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if payload.get("experiment_id") != "mx-z-field-calibration":
        raise ValueError(f"Z 标定 experiment_id 不匹配: {analysis_path}")
    if payload.get("success") is not True:
        raise ValueError(f"Z 标定结果未成功: {analysis_path}")
    slope = float(payload["K_Z_Hz_per_V"])
    intercept = float(payload["f_0V_Hz"])
    r_squared = float(payload["linear_fit"]["r_squared"])
    if not all(np.isfinite(value) for value in (slope, intercept, r_squared)):
        raise ValueError("Z 标定结果包含非有限值")
    if slope <= 0:
        raise ValueError("Z 标定斜率必须为正")
    return ZCalibrationSource(
        run_name=run_name,
        analysis_path=analysis_path,
        slope_hz_per_v=slope,
        intercept_hz=intercept,
        r_squared=r_squared,
        analysis_sha256=_sha256(analysis_path),
        payload=payload,
    )


def build_applied_control(
    theory: TheoryControlSource,
    calibration: ZCalibrationSource,
    scale: float,
    *,
    output_vpp: float,
    output_offset_v: float,
) -> AppliedControlWaveform:
    """按标定换算目标电压，再映射到 GUI 固定的 AW 输出范围。"""
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("CONTROL_SCALE 必须是正有限值")
    if not np.isfinite(output_vpp) or output_vpp <= 0:
        raise ValueError("Z_AW_OUTPUT_VPP 必须是正有限值")
    if not np.isfinite(output_offset_v):
        raise ValueError("Z_AW_OUTPUT_OFFSET 必须是有限值")
    voltage_v = (
        float(scale)
        * np.asarray(theory.omega_ctrl_hz, dtype=float)
        / calibration.slope_hz_per_v
    )
    minimum_v = float(np.min(voltage_v))
    maximum_v = float(np.max(voltage_v))
    validate_safety_limit("Z_magnetic_field", minimum_v)
    validate_safety_limit("Z_magnetic_field", maximum_v)

    amplitude_vpp = float(output_vpp)
    offset_v = float(output_offset_v)
    half_range = 0.5 * amplitude_vpp
    output_minimum_v = offset_v - half_range
    output_maximum_v = offset_v + half_range
    validate_safety_limit("Z_magnetic_field", output_minimum_v)
    validate_safety_limit("Z_magnetic_field", output_maximum_v)

    normalized = (voltage_v - offset_v) / half_range
    if not np.all(np.isfinite(normalized)):
        raise ValueError("控制波形归一化失败")
    max_abs_normalized = float(np.max(np.abs(normalized)))
    if max_abs_normalized > 1.0 + 1e-9:
        raise ValueError(
            f"Z 控制目标电压 [{minimum_v:+.6f}, {maximum_v:+.6f}] V "
            f"超出固定输出范围 [{output_minimum_v:+.6f}, "
            f"{output_maximum_v:+.6f}] V；请增大 Z_AW_OUTPUT_VPP、"
            "调整 Z_AW_OUTPUT_OFFSET 或降低 CONTROL_SCALE"
        )
    return AppliedControlWaveform(
        voltage_v=voltage_v,
        normalized=np.clip(normalized, -1.0, 1.0),
        amplitude_vpp=amplitude_vpp,
        offset_v=offset_v,
        minimum_v=minimum_v,
        maximum_v=maximum_v,
        output_minimum_v=output_minimum_v,
        output_maximum_v=output_maximum_v,
        max_abs_normalized=max_abs_normalized,
    )


def applied_control_from_corrected(
    control: CorrectedControlWaveform,
) -> AppliedControlWaveform:
    """把闭环冻结波形整定为 DG 任意波输出参数。

    只整理设备接口需要的字段；数组长度、有限值、归一化范围和
    电压一致性由 ``load_corrected_control_waveform`` 保证，
    Z 电压安全限值由调用方在输出前校验。
    """
    half_range = control.amplitude_vpp / 2.0
    return AppliedControlWaveform(
        voltage_v=np.asarray(control.voltage_v, dtype=float),
        normalized=np.asarray(control.normalized, dtype=float),
        amplitude_vpp=control.amplitude_vpp,
        offset_v=control.offset_v,
        minimum_v=float(np.min(control.voltage_v)),
        maximum_v=float(np.max(control.voltage_v)),
        output_minimum_v=control.offset_v - half_range,
        output_maximum_v=control.offset_v + half_range,
        max_abs_normalized=float(np.max(np.abs(control.normalized))),
    )


def corrected_control_contract(
    corrected: CorrectedControlWaveform,
) -> tuple[Any, AppliedControlWaveform]:
    """把闭环冻结波形适配为理论来源同形的 DG 任意波配置契约。

    供仍支持理论/闭环双来源的实验统一两条路径；闭环波形输出
    直接下发的实验应使用 ``applied_control_from_corrected``。
    """
    applied = applied_control_from_corrected(corrected)
    for value in (
        applied.minimum_v,
        applied.maximum_v,
        applied.output_minimum_v,
        applied.output_maximum_v,
    ):
        validate_safety_limit("Z_magnetic_field", value)
    if applied.max_abs_normalized > 1.0 + 1e-9:
        raise ValueError("校正波形归一化值超出 [-1, 1]")
    theory_contract = SimpleNamespace(
        version=f"corrected:{corrected.run_name}",
        time_s=np.asarray(corrected.time_s, dtype=float),
        omega_ctrl_hz=np.asarray(corrected.omega_ctrl_hz, dtype=float),
        repeat_frequency_hz=float(corrected.repeat_frequency_hz),
        theory_rf_frequency_hz=float(corrected.repeat_frequency_hz),
        waveform_sha256=corrected.waveform_sha256,
        parameter_sha256="",
    )
    return theory_contract, applied
