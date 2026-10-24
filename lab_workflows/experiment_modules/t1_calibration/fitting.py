"""采集后与离线分析共用旧带边界的单指数拟合。"""
import numpy as np
from scipy.optimize import curve_fit


def exp_decay(t, amplitude, t1, offset):
    return amplitude*np.exp(-t/t1)+offset


def fit_decay(t, voltage, bounds):
    mask = np.asarray(t) >= 0
    t, voltage = np.asarray(t)[mask], np.asarray(voltage)[mask]
    bad = dict(T1=np.nan, T1_err=np.nan, popt=np.full(3, np.nan), pcov=np.zeros((3, 3)), reason="")
    if len(t) < 10:
        return bad | {"reason": "t≥0 少于 10 点"}
    try:
        guess = [voltage[0]-voltage[-1], np.clip(.03, bounds[0]*2, bounds[1]/2), voltage[-1]]
        guess[1] = np.clip(guess[1], bounds[0], bounds[1])
        popt, pcov = curve_fit(exp_decay, t, voltage, p0=guess,
            bounds=([-np.inf, bounds[0], -np.inf], [np.inf, bounds[1], np.inf]), maxfev=10000)
        t1 = abs(popt[1])
        error = np.sqrt(pcov[1, 1]) if pcov[1, 1] > 0 else np.inf
        if not np.isfinite(error) or error > 3*t1:
            return bad | dict(popt=popt, pcov=pcov, reason="T1_err 超过 T1 的三倍或不有限")
        return dict(T1=t1, T1_err=error, popt=popt, pcov=pcov, reason="")
    except (ValueError, RuntimeError, FloatingPointError) as exc:
        return bad | {"reason": str(exc)}
