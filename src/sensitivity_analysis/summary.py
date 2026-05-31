"""实验结果汇总记录 — CSV 日志 + analysis.json 写入."""

import csv
import json
from pathlib import Path
from .fitting import DispersionFitResult
from .sensitivity import SensitivityResult


# CSV 列定义
_CSV_COLUMNS = [
    "timestamp", "run_tag",
    "Pump_laser_power", "Probe_laser_power",
    "X_magnetic_field", "Y_magnetic_field",
    "main_magnetic_field_mA", "PUMP_MOD_DUTY", "PUMP_MOD_AMPLITUDE",
    "temperature",
    "B0_fT", "slope_V_per_fT", "sens_flat_fT_per_sqrt_Hz",
    "gamma_fT", "gamma_nT", "f_larmor_Hz",
    "fit_r2", "fit_rmse", "fit_is_valid", "fit_rejection_reasons",
    "gamma_V", "gamma_uncertainty", "relative_gamma_uncertainty",
    "n_avg", "freq_resolution_Hz",
]


class SummaryLogger:
    """管理实验运行结果 CSV 日志文件."""

    def __init__(self, csv_path: Path):
        self._path = Path(csv_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_run(
        self,
        timestamp: str,
        run_tag: str,
        params: dict,
        fit_result: DispersionFitResult,
        sens_result: SensitivityResult,
        n_avg: int = 0,
        freq_resolution_Hz: float = 0.0,
    ):
        """追加一条运行记录."""
        row = {
            "timestamp": timestamp,
            "run_tag": run_tag,
            "Pump_laser_power": params.get("Pump_laser_power", ""),
            "Probe_laser_power": params.get("Probe_laser_power", ""),
            "X_magnetic_field": params.get("X_magnetic_field", ""),
            "Y_magnetic_field": params.get("Y_magnetic_field", ""),
            "main_magnetic_field_mA": params.get("main_magnetic_field", ""),
            "PUMP_MOD_DUTY": params.get("PUMP_MOD_DUTY", ""),
            "PUMP_MOD_AMPLITUDE": params.get("PUMP_MOD_AMPLITUDE", ""),
            "temperature": params.get("temperature", ""),
            "B0_fT": fit_result.B0_fT,
            "slope_V_per_fT": fit_result.slope_V_per_fT,
            "sens_flat_fT_per_sqrt_Hz": sens_result.sens_flat,
            "gamma_fT": fit_result.gamma_fT,
            "gamma_nT": fit_result.gamma_nT,
            "f_larmor_Hz": fit_result.f_larmor_Hz,
            "fit_r2": fit_result.r_squared,
            "fit_rmse": fit_result.rmse,
            "fit_is_valid": fit_result.is_valid,
            "fit_rejection_reasons": "; ".join(fit_result.rejection_reasons),
            "gamma_V": fit_result.gamma_V,
            "gamma_uncertainty": fit_result.gamma_uncertainty,
            "relative_gamma_uncertainty": fit_result.relative_gamma_uncertainty,
            "n_avg": n_avg,
            "freq_resolution_Hz": freq_resolution_Hz,
        }

        file_exists = self._path.exists()
        with open(self._path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def load(self):
        """读取现有 CSV 为 list[dict]."""
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))


def write_run_record(
    run_dir: Path,
    params: dict,
    fit_result: DispersionFitResult,
    sens_result: SensitivityResult,
    summary_path: Path | None = None,
    timestamp: str | None = None,
    run_tag: str = "",
    n_avg: int = 0,
    freq_resolution_Hz: float = 0.0,
):
    """同时写入 analysis.json 和 CSV 汇总.

    Parameters
    ----------
    run_dir : 本次运行目录 (如 data/.../MMDD_HHMM_tag/)
    params : 所有实验参数
    fit_result : 色散拟合结果
    sens_result : 灵敏度计算结果
    summary_path : CSV 汇总文件路径 (可选)
    timestamp : 时间戳 (可选)
    run_tag : 运行标签 (可选)
    n_avg : 噪声采集次数
    freq_resolution_Hz : PSD 频率分辨率
    """
    # 写入 analysis.json
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    analysis = {
        "B0_fT": fit_result.B0_fT,
        "slope_V_per_fT": fit_result.slope_V_per_fT,
        "sens_flat_fT_per_sqrt_Hz": sens_result.sens_flat,
        "n_avg": n_avg,
        "freq_resolution_Hz": freq_resolution_Hz,
        "f_linewidth_Hz": fit_result.f_larmor_Hz,
        "flat_fmin_Hz": sens_result.flat_fmin,
        "flat_fmax_Hz": sens_result.flat_fmax,
        "fit_is_valid": fit_result.is_valid,
        "fit_r_squared": fit_result.r_squared,
        "fit_rmse": fit_result.rmse,
        "gamma_V": fit_result.gamma_V,
        "gamma_nT": fit_result.gamma_nT,
        "gamma_fT": fit_result.gamma_fT,
        "gamma_uncertainty": fit_result.gamma_uncertainty,
        "relative_gamma_uncertainty": fit_result.relative_gamma_uncertainty,
        "residual_sign_change_ratio": fit_result.residual_sign_change_ratio,
        "rejection_reasons": fit_result.rejection_reasons,
    }
    with open(results_dir / "analysis.json", "w") as f:
        json.dump(analysis, f, indent=2, default=str)

    # 追加到 CSV 汇总
    if summary_path is not None:
        logger = SummaryLogger(summary_path)
        logger.log_run(
            timestamp=timestamp or "",
            run_tag=run_tag,
            params=params,
            fit_result=fit_result,
            sens_result=sens_result,
            n_avg=n_avg,
            freq_resolution_Hz=freq_resolution_Hz,
        )
