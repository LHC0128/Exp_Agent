"""在已采集的两条低场曲线上对比：有/无 gamma 下限时的拟合结果。"""
from lab_workflows.common import data_root
import pathlib

import numpy as np
from scipy.optimize import curve_fit

from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis_core import (
    fit_lorentzian_response, lorentzian_response,
)

RUN = (data_root() / "Coil_Constant_Calibration/1116_181213_coil_const_fine")

for name in ("current_003", "current_005", "current_002"):
    data = np.load(RUN / "raw" / name / "frequency_scan.npz", allow_pickle=True)
    f, r = data["frequency_hz"], data["r_mean_v"]
    official = fit_lorentzian_response(f, r, r_squared_min=None, relative_gamma_uncertainty_max=None)
    step = float(np.median(np.diff(np.sort(f))))
    base = float(np.median(np.r_[r[:2], r[-2:]]))
    p0 = (float(r.max() - base), max(float(np.ptp(f)) / 10.0, step), float(f[int(np.argmax(r))]), base)
    lower = (-np.inf, 1e-9, float(f.min()), -np.inf)
    upper = (np.inf, float(np.ptp(f)), float(f.max()), np.inf)
    popt, pcov = curve_fit(lorentzian_response, f, r, p0=p0, bounds=(lower, upper), maxfev=30000)
    perr = np.sqrt(np.diag(pcov))
    predicted = lorentzian_response(f, *popt)
    r2 = 1 - np.sum((r - predicted) ** 2) / np.sum((r - np.mean(r)) ** 2)
    print(f"=== {name}: I={float(data['current_ma']):.2f} mA  step={step:.0f} Hz")
    print(f"  官方(下限 gamma>{step:.0f}): success={official.success} "
          f"center={official.parameters[2]:.1f} gamma={abs(official.parameters[1]):.1f} "
          f"reasons={official.rejection_reasons}")
    print(f"  无下限直接拟合        : center={popt[2]:.1f}±{perr[2]:.1f} gamma={abs(popt[1]):.1f}±{perr[1]:.1f} "
          f"amp={popt[0]:.4f} base={popt[3]:.4f} R²={r2:.5f}")
