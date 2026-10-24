"""投影噪声实验使用的主场标定读取。"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class MainFieldCalibration:
    """由 Mx 主场标定结果得到的线性频率模型。"""

    source_run: str
    slope_hz_per_ma: float
    intercept_hz: float
    r_squared: float
    analysis_path: Path

    def current_for_frequency(self, frequency_hz: float) -> float:
        current = (float(frequency_hz) - self.intercept_hz) / self.slope_hz_per_ma
        if not math.isfinite(current):
            raise ValueError("标定反算电流必须为有限值")
        return current


def load_main_field_calibration(
    project_root: Path,
    source_run: str,
) -> MainFieldCalibration:
    """读取并严格校验一个固定的 Mx 主场标定运行。"""
    base = (project_root / "data/Mx_Main_Field_Calibration").resolve()
    run = (base / source_run).resolve()
    path = run / "results/analysis.yaml"
    if not source_run or any(c in source_run for c in "/\\:\x00") or run.parent != base or path.resolve() != path:
        raise ValueError("主场标定来源必须是本实验目录下的运行名称，不能经过目录联接")
    if not path.is_file():
        raise FileNotFoundError(f"未找到主场标定结果: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError(f"主场标定运行未成功: {source_run}")
    try:
        slope = float(payload["K_f_Hz_per_mA"])
        intercept = float(payload["f_0mA_Hz"])
        fit = payload["frequency_linear_fit"]
        r_squared = float(fit["r_squared"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"主场标定结果字段不完整: {path}") from exc
    if not all(math.isfinite(value) for value in (slope, intercept, r_squared)) or slope <= 0.0:
        raise ValueError("主场标定系数必须有限，且斜率必须为正数")
    return MainFieldCalibration(
        source_run=source_run,
        slope_hz_per_ma=slope,
        intercept_hz=intercept,
        r_squared=r_squared,
        analysis_path=path,
    )
