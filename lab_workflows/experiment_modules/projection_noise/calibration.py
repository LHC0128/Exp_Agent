"""投影噪声实验使用的主场标定读取。"""

from __future__ import annotations

from dataclasses import dataclass
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
        return (float(frequency_hz) - self.intercept_hz) / self.slope_hz_per_ma


def load_main_field_calibration(
    project_root: Path,
    source_run: str,
) -> MainFieldCalibration:
    """读取并严格校验一个固定的 Mx 主场标定运行。"""
    path = (
        project_root
        / "data"
        / "Mx_Main_Field_Calibration"
        / source_run
        / "results"
        / "analysis.yaml"
    )
    if not path.is_file():
        raise FileNotFoundError(f"未找到主场标定结果: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if payload.get("success") is not True:
        raise ValueError(f"主场标定运行未成功: {source_run}")
    try:
        slope = float(payload["K_f_Hz_per_mA"])
        intercept = float(payload["f_0mA_Hz"])
        fit = payload["frequency_linear_fit"]
        r_squared = float(fit["r_squared"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"主场标定结果字段不完整: {path}") from exc
    if slope <= 0.0:
        raise ValueError(f"主场标定斜率必须为正数，实际为 {slope}")
    return MainFieldCalibration(
        source_run=source_run,
        slope_hz_per_ma=slope,
        intercept_hz=intercept,
        r_squared=r_squared,
        analysis_path=path,
    )
