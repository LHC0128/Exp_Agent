"""把这批运行目录里所有电流点的 R(f) 找峰，用峰位反推当时的零电流中心 f0。

判据：f = |f0 + K·I|（K 取 630 Hz/mA 量级）。同一条曲线给出一个峰位；
用两个以上电流点可以同时解出 f0 与 K；只有一个点时只能给出 f0+K·I 的组合。
"""
from lab_workflows.common import data_root
import pathlib
import numpy as np
from scipy.optimize import curve_fit

BASE = (data_root() / "Coil_Constant_Calibration")


def curve(d):
    scan = d / "frequency_scan.npz"
    if scan.exists():
        s = np.load(scan, allow_pickle=True)
        return s["frequency_hz"], s["r_mean_v"], float(s["current_ma"]), float(s["predicted_center_hz"])
    files = sorted(d.glob("frequency_*_attempt_*.npz"), key=lambda p: int(p.name.split("_")[1]))
    if not files:
        return None
    f = np.array([float(np.load(p)["frequency_hz"]) for p in files])
    r = np.array([float(np.mean(np.load(p)["r_v"])) for p in files])
    sense = d / "sense_current.npz"
    current = float("nan")
    if sense.exists():
        meta = np.load(sense, allow_pickle=True)
        current = float(meta["target_ma"]) if "target_ma" in meta.files else float("nan")
    return f, r, current, float("nan")


def fit_peak(f, r):
    order = np.argsort(f)
    f, r = f[order], r[order]
    if f.size < 6:
        return None
    base = float(np.median(np.r_[r[:2], r[-2:]]))
    amp = float(r.max() - base)
    if amp <= 3 * float(np.std(np.r_[r[:2], r[-2:]])) or amp <= 0:
        return None
    x0 = float(f[int(np.argmax(r))])

    def model(x, c, a, mu, g):
        return c + a * g**2 / ((x - mu) ** 2 + g**2)

    try:
        popt, pcov = curve_fit(model, f, r, p0=[base, amp, x0, 2000.0], maxfev=40000,
                               bounds=([-np.inf, 0, f.min() - 3e4, 10], [np.inf, np.inf, f.max() + 3e4, 1e6]))
    except Exception:
        return None
    err = float(np.sqrt(np.diag(pcov))[2])
    # 峰必须在窗口内，且在边缘的点视为“峰在窗外”
    inside = f.min() < popt[2] < f.max()
    return dict(center=float(popt[2]), err=err, amp=float(popt[1]), gamma=float(popt[3]), inside=inside)


print("=" * 100)
for run in sorted(BASE.iterdir()):
    if not run.is_dir():
        continue
    points = []
    for d in sorted((run / "raw").glob("current_*")) if (run / "raw").exists() else []:
        got = curve(d)
        if got is None:
            continue
        f, r, current, predicted = got
        if f.size == 0:
            continue
        peak = fit_peak(f, r)
        points.append((d.name, current, predicted, f.min(), f.max(), peak))
    if not points:
        continue
    print(f"\n### {run.name}")
    for name, current, predicted, fmin, fmax, peak in points:
        head = f"{name}: I={current:8.2f} mA  pred={predicted:8.0f}  窗口 {fmin:7.0f}–{fmax:7.0f}"
        if peak is None:
            print(head + "  → 无峰（幅度不足）")
        else:
            flag = "" if peak["inside"] else "  ← 拟合峰在窗口外，不可信"
            print(head + f"  → 峰 {peak['center']:8.1f}±{peak['err']:.0f} Hz  "
                         f"amp={peak['amp']:.4f} γ={peak['gamma']:.0f}{flag}")
