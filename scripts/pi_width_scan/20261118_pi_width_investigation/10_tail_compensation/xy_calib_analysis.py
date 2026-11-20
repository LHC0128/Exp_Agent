"""X/Y 横向拖尾补偿标定（§4.5）：bperp_null 在 (A_x, A_y) = (0,0)、(+30,0)、(0,+30) mV 及线性解出的点上各测一次。

条件：工作波形 8.20 µs、21.6 mA、τc 25.5 µs；GS200 −0.40 mA；XY_TAIL_TAU_E_US 46.9；5×5 网格 ±20 mV、步进 10，每点 2 次。
每次：复平面拟合 c = a + bx·x + by·y（mV，rms 读数），得 2f 零点、零点处 c1；按拟合残差扰动 2000 次（c1、c2 同时）给蒙特卡洛不确定度。
线性模型：c1@null(A) = c1₀ + A_x·g_x + A_y·g_y（复数 g，2 个实方程），null(A) = n₀ + H·A。由前三次解 c1@null = 0 的 A*，
预测该处的 2f 零点；有第 4 次（复测）时，用 4 点最小二乘重拟 g、H，并按复测点的剩余 c1 做一阶修正给出最终值。
输入：参照运行目录 + 本目录 lab_client_out/out4_<label>.json；输出 outputs/xy_calib_results.{txt,json}、figures/fig8_xy_tail_calib.png。
"""
from lab_workflows.common import data_root
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
CACHE = (Path(__file__).resolve().parent / "lab_client_out")
REF_RUN = (data_root() / "bperp_null/1118_191524_bperp_null")
RUNS = [("ref_00", REF_RUN), ("run1_ax30", None), ("run2_ay30", None), ("run3_verify", None),
        ("run4_stab", None), ("run5_m030", None), ("run6_m050", None)]
GS200_RUNS = ("run5_m030", "run3_verify", "run4_stab", "run6_m050")   # 同一 A*，不同 GS200 电流
N_MC = 2000
MV_TO_NT = 0.733


def run_dir(label, path):
    if path is not None:
        return path
    f = CACHE / f"out4_{label}.json"
    if not f.exists():
        return None
    m = re.search(r'"run_dir":\s*"([^"]+)"', f.read_text(encoding="utf-8", errors="replace"))
    return Path(m.group(1).replace("\\\\", "\\")) if m else None


def load(d):
    rows = list(csv.DictReader(open(d / "raw" / "bias_grid.csv", encoding="utf-8")))
    pts = {}
    for r in rows:
        key = (float(r["x_bias_v"]) * 1e3, float(r["y_bias_v"]) * 1e3)
        pts.setdefault(key, []).append((complex(float(r["x1_v"]), float(r["y1_v"])), complex(float(r["x2_v"]), float(r["y2_v"]))))
    xy = np.array(list(pts))
    mean = np.array([np.mean(v, axis=0) for v in pts.values()]) * 1e3
    cfg = (d / "experiment_config.yaml").read_text(encoding="utf-8", errors="replace")
    def par(name, default=0.0):
        m = re.search(rf"^\s*{name}:\s*(\S+)", cfg, re.M)
        return default if m is None else m.group(1)
    on = str(par("XY_TAIL_COMP_ENABLED", "false")).lower() == "true"
    A = np.array([float(par("XY_TAIL_X_MV")), float(par("XY_TAIL_Y_MV"))]) if on else np.zeros(2)
    mf = float(par("MAIN_FIELD_CURRENT_MA")) if str(par("MAIN_FIELD_ENABLED", "false")).lower() == "true" else 0.0
    return xy, mean, A, mf


def lsq(xy, c):
    X = np.column_stack([np.ones(len(xy)), xy]).astype(complex)
    coef, *_ = np.linalg.lstsq(X, c, rcond=None)
    return coef, X @ coef - c


def null_and_c1(co1, co2):
    a, bx, by = co2
    M = np.array([[bx.real, by.real], [bx.imag, by.imag]])
    nb = np.linalg.solve(M, -np.array([a.real, a.imag]))
    return nb, co1[0] + co1[1] * nb[0] + co1[2] * nb[1], np.linalg.svd(M, compute_uv=False)


def analyse(xy, mean, rng):
    co1, r1 = lsq(xy, mean[:, 0]); co2, r2 = lsq(xy, mean[:, 1])
    nb, c1n, sv = null_and_c1(co1, co2)
    s1, s2 = (np.sqrt(np.mean(np.abs(r) ** 2)) for r in (r1, r2))
    cn = lambda s: s * (rng.standard_normal(len(xy)) + 1j * rng.standard_normal(len(xy))) / np.sqrt(2)
    mc_n, mc_c = [], []
    for _ in range(N_MC):
        n_, c_, _ = null_and_c1(lsq(xy, mean[:, 0] + cn(s1))[0], lsq(xy, mean[:, 1] + cn(s2))[0])
        mc_n.append(n_); mc_c.append(c_)
    mc_n = np.array(mc_n); mc_c = np.array(mc_c)
    return dict(null=nb, c1n=c1n, sv=sv, res1=s1, res2=s2, null_mc=mc_n, c1n_mc=mc_c,
                null_sig=mc_n.std(0), c1n_sig=np.array([mc_c.real.std(), mc_c.imag.std()]),
                inside=bool(np.all((nb >= xy.min(0)) & (nb <= xy.max(0)))), center=xy.mean(0))


def fit_linear(As, c1s, nulls):
    """c1@null 与 2f 零点对 A 的线性模型（最小二乘；3 点时为精确解）。"""
    X = np.column_stack([np.ones(len(As)), As])
    gc, *_ = np.linalg.lstsq(X.astype(complex), np.array(c1s), rcond=None)       # c0, gx, gy
    gn, *_ = np.linalg.lstsq(X, np.array(nulls), rcond=None)                       # n0; H 的行
    return gc, gn


def solve_A(gc):
    c0, gx, gy = gc
    G = np.array([[gx.real, gy.real], [gx.imag, gy.imag]])
    return np.linalg.solve(G, -np.array([c0.real, c0.imag]))


def predict_null(gn, A):
    return gn[0] + A @ gn[1:]


def main():
    rng = np.random.default_rng(7)
    res = {}
    for lab, p in RUNS:
        d = run_dir(lab, p)
        if d is None or not (d / "raw" / "bias_grid.csv").exists():
            continue
        xy, mean, A, mf = load(d)
        if len(xy) < 6:                                   # 中断且几乎无数据的运行（如 1118_200954）不参与
            continue
        r = analyse(xy, mean, rng); r["A"] = A; r["dir"] = d; r["mf"] = mf
        res[lab] = r
    lines = [f"{'label':12s} A (mV)        centre (mV)        2f null (mV)          sigma (mV)     c1@null (mV)        sigma c1   |c1|   weak sv  res c1/c2 (mV)"]
    for lab, r in res.items():
        lines.append(f"{lab:12s} ({r['A'][0]:+6.1f},{r['A'][1]:+6.1f})  ({r['center'][0]:+7.2f},{r['center'][1]:+7.2f})  "
                     f"({r['null'][0]:+7.2f},{r['null'][1]:+7.2f})   ({r['null_sig'][0]:.3f},{r['null_sig'][1]:.3f})  "
                     f"({r['c1n'].real:+6.2f},{r['c1n'].imag:+6.2f})   ({r['c1n_sig'][0]:.3f},{r['c1n_sig'][1]:.3f})  "
                     f"{abs(r['c1n']):5.2f}   {r['sv'][1]:.3f}   {r['res1']:.3f}/{r['res2']:.3f}{'' if r['inside'] else '  (null outside grid)'}  {r['dir'].name}")
    out = dict(runs={k: dict(run_dir=str(v["dir"]), A_mV=v["A"].tolist(), centre_mV=v["center"].tolist(), null_mV=v["null"].tolist(),
                             null_sigma_mV=v["null_sig"].tolist(), c1_at_null_mV=[v["c1n"].real, v["c1n"].imag],
                             c1_at_null_sigma_mV=v["c1n_sig"].tolist(), J2_sv=v["sv"].tolist(), fit_res_c1_mV=v["res1"],
                             fit_res_c2_mV=v["res2"], inside_grid=v["inside"]) for k, v in res.items()})
    base = [k for k in ("ref_00", "run1_ax30", "run2_ay30") if k in res]
    if len(base) == 3:
        def model(keys, draw=None):
            As = [res[k]["A"] for k in keys]
            c1s = [res[k]["c1n"] if draw is None else res[k]["c1n_mc"][draw] for k in keys]
            ns = [res[k]["null"] if draw is None else res[k]["null_mc"][draw] for k in keys]
            gc, gn = fit_linear(As, c1s, ns)
            Astar = solve_A(gc)
            return gc, gn, Astar, predict_null(gn, Astar)
        gc, gn, Astar, nstar = model(base)
        mc = np.array([np.concatenate(model(base, i)[2:]) for i in range(N_MC)])
        lines += ["", "linear model from 3 runs (ref, +30 x, +30 y):",
                  f"  c1_0 = {gc[0].real:+.2f}{gc[0].imag:+.2f}i mV;  g_x = {gc[1].real:+.4f}{gc[1].imag:+.4f}i;  g_y = {gc[2].real:+.4f}{gc[2].imag:+.4f}i  (mV c1 per mV A)",
                  f"  null shift H (mV null per mV A): d/dA_x = ({gn[1][0]:+.4f},{gn[1][1]:+.4f}),  d/dA_y = ({gn[2][0]:+.4f},{gn[2][1]:+.4f})",
                  f"  A* (c1@null = 0) = ({Astar[0]:+.2f}, {Astar[1]:+.2f}) mV  +- ({mc[:, 0].std():.2f}, {mc[:, 1].std():.2f})",
                  f"  predicted 2f null at A* = ({nstar[0]:+.2f}, {nstar[1]:+.2f}) mV  +- ({mc[:, 2].std():.2f}, {mc[:, 3].std():.2f});"
                  f"  shift from A=0: ({nstar[0] - res['ref_00']['null'][0]:+.2f}, {nstar[1] - res['ref_00']['null'][1]:+.2f}) mV"
                  f" = ({(nstar[0] - res['ref_00']['null'][0]) * MV_TO_NT:+.1f}, {(nstar[1] - res['ref_00']['null'][1]) * MV_TO_NT:+.1f}) nT",
                  f"  run3 allowed (|A| <= 100 mV): {bool(np.all(np.abs(Astar) <= 100))}"]
        out["linear3"] = dict(c1_0=[gc[0].real, gc[0].imag], g_x=[gc[1].real, gc[1].imag], g_y=[gc[2].real, gc[2].imag],
                              H=[gn[1].tolist(), gn[2].tolist()], A_star_mV=Astar.tolist(), A_star_sigma_mV=mc[:, :2].std(0).tolist(),
                              null_pred_mV=nstar.tolist(), null_pred_sigma_mV=mc[:, 2:].std(0).tolist())
        if "run3_verify" in res:
            r3 = res["run3_verify"]
            keys = base + ["run3_verify"]
            gc4, gn4, A4, n4 = model(keys)
            # 一阶修正：从复测点出发，用 4 点梯度把剩余 c1 推到 0
            def corr(draw=None):
                g, h, _, _ = model(keys, draw)
                c3 = r3["c1n"] if draw is None else r3["c1n_mc"][draw]
                n3 = r3["null"] if draw is None else r3["null_mc"][draw]
                G = np.array([[g[1].real, g[2].real], [g[1].imag, g[2].imag]])
                dA = np.linalg.solve(G, -np.array([c3.real, c3.imag]))
                return np.concatenate([r3["A"] + dA, n3 + dA @ h[1:]])
            fin = corr(); mcf = np.array([corr(i) for i in range(N_MC)])
            lines += ["", "verification run:",
                      f"  measured null ({r3['null'][0]:+.2f}, {r3['null'][1]:+.2f}) mV vs predicted ({nstar[0]:+.2f}, {nstar[1]:+.2f});"
                      f"  c1@null = {r3['c1n'].real:+.2f}{r3['c1n'].imag:+.2f}i mV (|c1| {abs(r3['c1n']):.2f}, ref {abs(res['ref_00']['c1n']):.2f})",
                      f"  4-run refit: A* = ({A4[0]:+.2f}, {A4[1]:+.2f}) mV, null at A* = ({n4[0]:+.2f}, {n4[1]:+.2f}) mV",
                      f"  final (run3 + first-order correction): A = ({fin[0]:+.2f}, {fin[1]:+.2f}) +- ({mcf[:, 0].std():.2f}, {mcf[:, 1].std():.2f}) mV;"
                      f"  true-zero bias = ({fin[2]:+.2f}, {fin[3]:+.2f}) +- ({mcf[:, 2].std():.2f}, {mcf[:, 3].std():.2f}) mV"]
            out["verify"] = dict(A4_star_mV=A4.tolist(), null4_mV=n4.tolist(), final_A_mV=fin[:2].tolist(), final_A_sigma_mV=mcf[:, :2].std(0).tolist(),
                                 final_null_mV=fin[2:].tolist(), final_null_sigma_mV=mcf[:, 2:].std(0).tolist())
        figure(res, gc, gn, Astar, nstar)
    gs = [k for k in GS200_RUNS if k in res]
    if len({res[k]["mf"] for k in gs}) >= 2:
        I = np.array([res[k]["mf"] for k in gs]); N = np.array([res[k]["null"] for k in gs]); S = np.array([res[k]["null_sig"] for k in gs])
        X = np.column_stack([np.ones_like(I), I])
        coef, *_ = np.linalg.lstsq(X, N, rcond=None)                      # 行 0：截距（0 mA），行 1：斜率 mV/mA
        resid = N - X @ coef
        dof = max(len(I) - 2, 1)
        cov = np.linalg.inv(X.T @ X)
        # 不确定度：统计 sigma 与拟合残差取大者
        s_pt = np.maximum(S.max(0), np.sqrt((resid ** 2).sum(0) / dof) if len(I) > 2 else 0.0)
        s0 = s_pt * np.sqrt(cov[0, 0]); s1 = s_pt * np.sqrt(cov[1, 1])
        lines += ["", "GS200 dependence at fixed A (null vs main-field current):"]
        for k in gs:
            r = res[k]
            lines.append(f"  {k:12s} I = {r['mf']:+.2f} mA  null ({r['null'][0]:+7.2f},{r['null'][1]:+7.2f})  |c1| {abs(r['c1n']):.2f}  weak sv {r['sv'][1]:.3f}  res c2 {r['res2']:.3f}")
        lines += [f"  slope = ({coef[1][0]:+.2f}, {coef[1][1]:+.2f}) mV/mA  +- ({s1[0]:.2f}, {s1[1]:.2f});  residuals rms ({np.sqrt((resid[:, 0] ** 2).mean()):.3f}, {np.sqrt((resid[:, 1] ** 2).mean()):.3f}) mV",
                  f"  extrapolated null at GS200 = 0: ({coef[0][0]:+.2f}, {coef[0][1]:+.2f}) mV  +- ({s0[0]:.2f}, {s0[1]:.2f})"]
        # 两两相邻电流的斜率外推，差值作为外推的系统误差
        order = np.argsort(I); Iu, idx = np.unique(I[order], return_index=True)
        Nu = np.array([N[order][I[order] == v].mean(0) for v in Iu])
        pair0 = [Nu[i] - Iu[i] * (Nu[i + 1] - Nu[i]) / (Iu[i + 1] - Iu[i]) for i in range(len(Iu) - 1)]
        if len(pair0) > 1:
            lines.append("  pairwise extrapolations to 0 mA: " + ", ".join(f"({q[0]:+.2f}, {q[1]:+.2f})" for q in pair0))
        gs200_figure(I, N, S, coef, gs, res)
        out["gs200_pairwise_null_at_0mA"] = [q.tolist() for q in pair0]
        out["gs200"] = dict(runs=gs, I_mA=I.tolist(), null_mV=N.tolist(), slope_mV_per_mA=coef[1].tolist(), slope_sigma=s1.tolist(),
                            null_at_0mA=coef[0].tolist(), null_at_0mA_sigma=s0.tolist(), resid_mV=resid.tolist())
    (HERE / "outputs" / "xy_calib_results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "outputs" / "xy_calib_results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines))


def figure(res, gc, gn, Astar, nstar):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.6))
    cols = dict(ref_00="k", run1_ax30="C0", run2_ay30="C1", run3_verify="C3")
    names = dict(ref_00="A = (0, 0)", run1_ax30="A = (+30, 0)", run2_ay30="A = (0, +30)", run3_verify="A = ({:+.1f}, {:+.1f})")
    for lab, r in res.items():
        if lab not in cols:
            continue
        nm = names[lab].format(*r["A"]) if lab == "run3_verify" else names[lab]
        ax[0].errorbar(r["c1n"].real, r["c1n"].imag, xerr=r["c1n_sig"][0], yerr=r["c1n_sig"][1], fmt="o", color=cols[lab], label=nm, ms=7, capsize=3)
        ax[1].errorbar(r["null"][0], r["null"][1], xerr=r["null_sig"][0], yerr=r["null_sig"][1], fmt="o", color=cols[lab], label=nm, ms=7, capsize=3)
    t = np.linspace(0, 1, 50)[:, None]
    path = t * Astar                                 # A 从 0 线性走到 A*
    c_path = gc[0] + path[:, 0] * gc[1] + path[:, 1] * gc[2]
    n_path = gn[0] + path @ gn[1:]
    ax[0].plot(c_path.real, c_path.imag, "--", color="0.5", label="linear model, A: 0 -> A*")
    ax[1].plot(n_path[:, 0], n_path[:, 1], "--", color="0.5", label="linear model, A: 0 -> A*")
    ax[0].plot(0, 0, "x", color="C3", ms=12, mew=2)
    ax[1].plot(*nstar, "x", color="C3", ms=12, mew=2, label=f"predicted null at A* = ({Astar[0]:+.1f}, {Astar[1]:+.1f}) mV")
    ax[0].axhline(0, color="0.85", lw=0.8); ax[0].axvline(0, color="0.85", lw=0.8)
    ax[0].set(xlabel="Re c1 at 2f null (mV rms)", ylabel="Im c1 at 2f null (mV rms)", title="(a) 1f signal at the 2f null")
    ax[1].set(xlabel="X bias (mV)", ylabel="Y bias (mV)", title="(b) 2f null position")
    for a in ax:
        a.legend(fontsize=8); a.grid(alpha=0.3)
    ax[0].set_aspect("equal", adjustable="datalim"); ax[1].set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    fig.savefig(HERE / "figures" / "fig8_xy_tail_calib.png", dpi=150)


def gs200_figure(I, N, S, coef, keys, res):
    fig, ax = plt.subplots(1, 2, figsize=(10, 4.2))
    Ig = np.linspace(min(I.min(), 0) - 0.05, 0.02, 50)
    for k, (axis, name) in enumerate(((ax[0], "X"), (ax[1], "Y"))):
        axis.errorbar(I, N[:, k], yerr=S[:, k], fmt="o", color="C0", capsize=3, label="measured 2f null (A fixed)")
        axis.plot(Ig, coef[0][k] + coef[1][k] * Ig, "--", color="0.5", label=f"linear fit, slope {coef[1][k]:+.1f} mV/mA")
        axis.plot(0, coef[0][k], "x", color="C3", ms=11, mew=2, label=f"extrapolated to 0 mA: {coef[0][k]:+.1f} mV")
        axis.set(xlabel="GS200 main-field current (mA)", ylabel=f"{name} bias at 2f null (mV)", title=f"({'ab'[k]}) {name} null vs GS200 current")
        axis.grid(alpha=0.3); axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(HERE / "figures" / "fig9_null_vs_gs200.png", dpi=150)


if __name__ == "__main__":
    sys.exit(main())
