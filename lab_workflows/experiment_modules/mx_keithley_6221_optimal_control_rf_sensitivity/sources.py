"""最优控制波形与 6221 标定结果的只读解析。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...common import validate_safety_limit


_VERSION_PATTERN = re.compile(r"^v[1-9]\d*$")
_RUN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_6221_ARB_POINTS = 65535


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
class KeithleyCalibrationSource:
    """6221 主场频率标定结果。"""

    run_name: str
    analysis_path: Path
    slope_hz_per_ma: float
    intercept_hz: float
    r_squared: float
    analysis_sha256: str
    payload: dict[str, Any]


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

    values = np.loadtxt(waveform_path, delimiter=",", comments="#", ndmin=2)
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError(f"控制波形必须是 time_s,Omega_ctrl_Hz 两列: {waveform_path}")
    if values.shape[0] < 2:
        raise ValueError("控制波形至少需要 2 个采样点")
    if values.shape[0] > MAX_6221_ARB_POINTS:
        raise ValueError(
            f"控制波形点数 {values.shape[0]} 超过 Keithley 6221 上限 "
            f"{MAX_6221_ARB_POINTS}"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError(f"控制波形包含 NaN 或无穷值: {waveform_path}")

    time_s = np.asarray(values[:, 0], dtype=float)
    omega_ctrl_hz = np.asarray(values[:, 1], dtype=float)
    steps = np.diff(time_s)
    if np.any(steps <= 0):
        raise ValueError("控制波形时间轴必须严格递增")
    step_s = float(np.median(steps))
    if not np.allclose(steps, step_s, rtol=1e-6, atol=max(1e-15, step_s * 1e-9)):
        raise ValueError("控制波形时间轴必须等间隔")
    repeat_frequency_hz = 1.0 / (time_s.size * step_s)

    theory_version = _parse_parameter_value(parameter_path, "# Output version")
    if theory_version != version:
        raise ValueError(
            f"理论参数版本 {theory_version!r} 与目录版本 {version!r} 不一致"
        )
    theory_rf_frequency_hz = float(
        _parse_parameter_value(parameter_path, "f_rf_Hz")
    )
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


def load_keithley_calibration(
    project_root: Path, run_name: str
) -> KeithleyCalibrationSource:
    """读取指定的 Mx Keithley 6221 主场标定结果。"""
    if not _RUN_PATTERN.fullmatch(run_name):
        raise ValueError("标定运行名只能包含字母、数字、点、下划线和短横线")
    analysis_path = (
        Path(project_root)
        / "data"
        / "Mx_Keithley_6221_Main_Field_Calibration"
        / run_name
        / "results"
        / "analysis.yaml"
    )
    if not analysis_path.is_file():
        raise FileNotFoundError(f"未找到 6221 标定分析结果: {analysis_path}")
    with analysis_path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if payload.get("experiment_id") != "mx-keithley-6221-main-field-calibration":
        raise ValueError(f"6221 标定 experiment_id 不匹配: {analysis_path}")
    if payload.get("success") is not True:
        raise ValueError(f"6221 标定结果未成功: {analysis_path}")
    slope = float(payload["K_f_Hz_per_mA"])
    intercept = float(payload["f_0mA_Hz"])
    r_squared = float(payload["frequency_linear_fit"]["r_squared"])
    if not all(np.isfinite(value) for value in (slope, intercept, r_squared)):
        raise ValueError("6221 标定结果包含非有限值")
    if slope <= 0:
        raise ValueError("6221 标定斜率必须为正")
    return KeithleyCalibrationSource(
        run_name=run_name,
        analysis_path=analysis_path,
        slope_hz_per_ma=slope,
        intercept_hz=intercept,
        r_squared=r_squared,
        analysis_sha256=_sha256(analysis_path),
        payload=payload,
    )


@dataclass(frozen=True, slots=True)
class AppliedCurrentWaveform:
    """按 6221 标定反算的电流任意波。"""

    current_ma: np.ndarray
    normalized: np.ndarray
    amplitude_peak_ma: float
    offset_ma: float
    minimum_ma: float
    maximum_ma: float


def build_applied_current(
    theory: TheoryControlSource,
    calibration: KeithleyCalibrationSource,
    scale: float,
) -> AppliedCurrentWaveform:
    """使用完整自由斜率/截距模型把理论频率换算成电流。"""
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("CONTROL_SCALE 必须是正有限值")
    current_ma = float(scale) * (
        np.asarray(theory.omega_ctrl_hz, dtype=float) - calibration.intercept_hz
    ) / calibration.slope_hz_per_ma
    # 运行周期末点明确回到零电流；周期内部仍完整使用标定后的控制包络。
    current_ma = np.array(current_ma, dtype=float, copy=True)
    current_ma[-1] = 0.0
    minimum = float(np.min(current_ma))
    maximum = float(np.max(current_ma))
    validate_safety_limit("keithley_6221_main_field", minimum)
    validate_safety_limit("keithley_6221_main_field", maximum)
    offset = 0.0
    amplitude = max(abs(minimum), abs(maximum))
    if amplitude <= 0 or not np.isfinite(amplitude):
        raise ValueError("换算后的 6221 任意波必须有正的峰值幅度")
    normalized = np.asarray(current_ma / amplitude, dtype=float)
    if not np.all(np.isfinite(normalized)) or np.max(np.abs(normalized)) > 1.0 + 1e-9:
        raise ValueError("6221 任意波归一化失败")
    return AppliedCurrentWaveform(
        current_ma=current_ma,
        normalized=np.clip(normalized, -1.0, 1.0),
        amplitude_peak_ma=amplitude,
        offset_ma=offset,
        minimum_ma=minimum,
        maximum_ma=maximum,
    )
