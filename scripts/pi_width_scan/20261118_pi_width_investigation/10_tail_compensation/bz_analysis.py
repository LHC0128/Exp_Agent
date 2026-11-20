"""提高 Bz 的三次 bperp_null（主磁场 关 / +0.40 / −0.40 mA，工作波形 8.20 µs、21.6 mA、τc 25.5）分析。

每次 5×5 网格 ±60 mV：复平面拟合 c = a + bx·x + by·y（mV，rms 读数），给出 |J1|、2f 雅可比奇异值、2f 零点
及其蒙特卡洛不确定度（按逐点重复标准误扰动 500 次）、零点处 c1、拟合残差。
输入：本目录 lab_client_out/out3_<label>.json；输出 outputs/bz_results.{txt,json}。
"""
import csv
import json
import re
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = (Path(__file__).resolve().parent / "lab_client_out")
LABELS = {"bz_off": "main field off", "bz_p040": "main field +0.40 mA", "bz_m040": "main field -0.40 mA", "bz_m040_center": "main field -0.40 mA, centred grid +-20 mV"}


def load(label):
    text = (CACHE / f"out3_{label}.json").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'"run_dir":\s*"([^"]+)"', text)
    if m is None:
        return None
    d = Path(m.group(1).replace("\\\\", "\\"))
    rows = list(csv.DictReader(open(d / "raw" / "bias_grid.csv", encoding="utf-8")))
    pts = {}
    for r in rows:
        key = (float(r["x_bias_v"]) * 1e3, float(r["y_bias_v"]) * 1e3)
        pts.setdefault(key, []).append((complex(float(r["x1_v"]), float(r["y1_v"])), complex(float(r["x2_v"]), float(r["y2_v"]))))
    xy = np.array(list(pts)); vals = [np.array(v) * 1e3 for v in pts.values()]
    mean = np.array([v.mean(0) for v in vals]); sem = np.array([v.std(0, ddof=1) / np.sqrt(len(v)) if len(v) > 1 else np.zeros(2) for v in vals])
    return d, xy, mean, sem


def solve(xy, c):
    X = np.column_stack([np.ones(len(xy)), xy]).astype(complex)
    coef, *_ = np.linalg.lstsq(X, c, rcond=None)
    return coef, X @ coef - c


def null(coef2):
    a, bx, by = coef2
    M = np.array([[bx.real, by.real], [bx.imag, by.imag]])
    return np.linalg.solve(M, -np.array([a.real, a.imag])), np.linalg.svd(M, compute_uv=False)


def main():
    rng = np.random.default_rng(1)
    res, lines = {}, [f"{'label':22s} |J1|    J2 sv (strong/weak)   2f null (mV)            sigma (mV)      c1@null (mV)      fit res c2 (mV)"]
    for lab, title in LABELS.items():
        if not (CACHE / f"out3_{lab}.json").exists():
            continue
        loaded = load(lab)
        if loaded is None:
            continue
        d, xy, mean, sem = loaded
        c1, r1 = solve(xy, mean[:, 0]); c2, r2 = solve(xy, mean[:, 1])
        nb, sv = null(c2)
        sig = np.sqrt(np.mean(np.abs(r2) ** 2))           # 残差含非线性，作为保守噪声
        mc = []
        for _ in range(500):
            noise = sig * (rng.standard_normal(len(xy)) + 1j * rng.standard_normal(len(xy))) / np.sqrt(2)
            mc.append(null(solve(xy, mean[:, 1] + noise)[0])[0])
        mc = np.array(mc)
        c1n = c1[0] + c1[1] * nb[0] + c1[2] * nb[1]
        r = dict(run_dir=str(d), title=title, J1=float(np.linalg.norm(c1[1:])), J2_sv=sv.tolist(), null_mV=nb.tolist(),
                 null_sigma_mV=mc.std(0).tolist(), c1_at_null_mV=[c1n.real, c1n.imag], fit_res_c2_mV=float(sig),
                 fit_res_c1_mV=float(np.sqrt(np.mean(np.abs(r1) ** 2))), sem_median_mV=float(np.median(np.abs(sem[:, 1]))),
                 inside_grid=bool(np.all((nb >= xy.min(0)) & (nb <= xy.max(0)))))
        res[lab] = r
        lines.append(f"{lab:22s} {r['J1']:.3f}   {sv[0]:.3f}/{sv[1]:.3f}        ({nb[0]:+8.2f},{nb[1]:+8.2f})   ({r['null_sigma_mV'][0]:.2f},{r['null_sigma_mV'][1]:.2f})   "
                     f"({c1n.real:+6.2f},{c1n.imag:+6.2f})   {sig:.3f}{'' if r['inside_grid'] else '  (outside grid)'}")
    (HERE / "outputs" / "bz_results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "outputs" / "bz_results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
