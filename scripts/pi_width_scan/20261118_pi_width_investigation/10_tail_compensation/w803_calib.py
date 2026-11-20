"""§4.7 换成精确 π 波形（8.03 µs、A 0.1154）后重标 X/Y 横向补偿。

每次运行：复平面拟合得 2f 零点与零点处 c1（复用 xy_calib_analysis 的拟合与蒙特卡洛）。
若 |c1| > 0.1 mV，用 §4.5 的线性梯度 g、H（outputs/xy_calib_results.json 的 linear3）给出一步修正：
ΔA = −G⁻¹·c1，预测零点 = null + H·ΔA。GS200 关闭的零场按 §4.5 的斜率换算：null(0) = null(−0.4 mA) + 0.4·slope。
输入：本目录 lab_client_out/out4_w803_r<n>.json；输出 outputs/w803_calib.{txt,json}。
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import xy_calib_analysis as xa  # noqa: E402

LABELS = ("w803_r1", "w803_r2", "w803_r3", "w803_gs_off")
C1_TOL_MV = 0.1


def main():
    prev = json.loads((HERE / "outputs" / "xy_calib_results.json").read_text(encoding="utf-8"))
    lin = prev["linear3"]
    gx, gy = complex(*lin["g_x"]), complex(*lin["g_y"])
    G = np.array([[gx.real, gy.real], [gx.imag, gy.imag]])
    H = np.array(lin["H"])                                   # 行：∂null/∂A_x、∂null/∂A_y
    slope = np.array(prev["gs200"]["slope_mV_per_mA"])
    rng = np.random.default_rng(11)
    lines, out = [], {}
    for lab in LABELS:
        d = xa.run_dir(lab, None)
        if d is None or not (d / "raw" / "bias_grid.csv").exists():
            continue
        xy, mean, A, mf = xa.load(d)
        r = xa.analyse(xy, mean, rng)
        c1 = r["c1n"]
        co1, _ = xa.lsq(xy, mean[:, 0])
        centre = xy.mean(0)
        c1_centre = co1[0] + co1[1] * centre[0] + co1[2] * centre[1]
        c1_abs_grid = np.abs(mean[:, 0])
        dA = np.linalg.solve(G, -np.array([c1.real, c1.imag]))
        A_next = A + dA
        null_next = r["null"] + dA @ H
        null0 = r["null"] + (0.0 - mf) * slope
        out[lab] = dict(run_dir=str(d), A_mV=A.tolist(), gs200_mA=mf, null_mV=r["null"].tolist(),
                        null_sigma_mV=r["null_sig"].tolist(), c1_at_null_mV=[c1.real, c1.imag], c1_abs_mV=abs(c1),
                        weak_sv=float(r["sv"][1]), fit_res_c2_mV=float(r["res2"]), inside_grid=r["inside"],
                        next_A_mV=A_next.tolist(), next_null_mV=null_next.tolist(),
                        null_gs200_off_mV=null0.tolist(), converged=bool(abs(c1) <= C1_TOL_MV),
                        centre_mV=centre.tolist(), c1_at_centre_mV=[c1_centre.real, c1_centre.imag],
                        c1_abs_grid_min_max_mV=[float(c1_abs_grid.min()), float(c1_abs_grid.max())],
                        J1=float(np.linalg.norm(co1[1:])))
        lines.append(f"{lab}: A ({A[0]:+.2f}, {A[1]:+.2f}) mV, GS200 {mf:+.2f} mA -> null ({r['null'][0]:+.2f}, {r['null'][1]:+.2f}) "
                     f"+- ({r['null_sig'][0]:.3f}, {r['null_sig'][1]:.3f}) mV, c1@null {c1.real:+.3f}{c1.imag:+.3f}i "
                     f"(|c1| {abs(c1):.3f} mV), weak sv {r['sv'][1]:.3f}, res c2 {r['res2']:.3f} mV"
                     f"{'' if r['inside'] else ' (outside grid)'}")
        lines.append(f"    c1 at grid centre ({centre[0]:+.2f}, {centre[1]:+.2f}): {c1_centre.real:+.3f}{c1_centre.imag:+.3f}i "
                     f"(|c1| {abs(c1_centre):.3f} mV); |c1| over grid {c1_abs_grid.min():.2f}-{c1_abs_grid.max():.2f} mV; |J1| {np.linalg.norm(co1[1:]):.3f}")
        lines.append(f"    one-step correction: A -> ({A_next[0]:+.2f}, {A_next[1]:+.2f}) mV, predicted null ({null_next[0]:+.2f}, {null_next[1]:+.2f}); "
                     f"GS200-off null from this run ({null0[0]:+.2f}, {null0[1]:+.2f}) mV; converged (|c1| <= {C1_TOL_MV}): {abs(c1) <= C1_TOL_MV}")
    (HERE / "outputs" / "w803_calib.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "outputs" / "w803_calib.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
