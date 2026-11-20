"""§4.6 π 宽度直接测量：pi-width-scan 4 相位 c−1 判据下，内置脉冲与拖尾补偿波形对照。

输入：各运行 results/scan_summary.json（B01 已运行）。输出 outputs/pi_scan_compare.{txt,json}、
figures/fig10_pi_scan_compare.png。另用 tailcomp 模型把实测 π 宽度换算到工作波形的转角。
"""
from lab_workflows.common import data_root
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tailcomp as tc  # noqa: E402

DATA = (data_root() / "pi_width_scan")
RUNS = [("built-in pulse (no tail comp)", "1118_213636_pi_width_scan", "C0"),
        ("tail comp, X bias only", "1118_205941_pi_width_scan", "C3"),
        ("tail comp, X + Y bias", "1118_220918_pi_width_scan", "C2")]


def load(name):
    path = DATA / name / "results" / "scan_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main():
    lines, out = [], {}
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    for label, name, color in RUNS:
        s = load(name)
        if s is None:
            # 运行目录名按实际生成的为准；找不到就跳过
            candidates = sorted(DATA.glob("1118_22*_pi_width_scan")) if "Y" in label else []
            s = load(candidates[-1].name) if candidates else None
            name = candidates[-1].name if candidates else name
            if s is None:
                continue
        w = np.array([p["width_us"] for p in s["points"]])
        cm = np.array([p["c_minus1"]["abs_v"] for p in s["points"]]) * 1e3
        cms = np.array([p["c_minus1"]["sem_v"] for p in s["points"]]) * 1e3
        c0 = np.array([p["c0"]["abs_v"] for p in s["points"]]) * 1e3
        ax[0].errorbar(w, cm, yerr=cms, fmt="o-", ms=3, color=color, label=f"{label}: tau_pi = {s['tau_pi_us']:.2f} us")
        ax[0].axvline(s["tau_pi_us"], color=color, ls=":", lw=0.9)
        ax[1].plot(w, c0, "s-", ms=3, color=color, label=label)
        out[name] = dict(label=label, tau_pi_us=s["tau_pi_us"], tau_pi_sem_us=s["tau_pi_sem_us"],
                         c_minus1_peak_mV=s["c_minus1_peak_v"] * 1e3, fit_quality=s["fit_quality"],
                         c0_max_mV=float(c0.max()), c0_at_tau_pi_mV=float(np.interp(s["tau_pi_us"], w, c0)))
        lines.append(f"{name}  {label:32s} tau_pi = {s['tau_pi_us']:.3f} +- {s['tau_pi_sem_us']:.3f} us  "
                     f"|c-1|peak {s['c_minus1_peak_v'] * 1e3:.2f} mV  |c0| max {c0.max():.1f} mV  ({s['fit_quality']})")
    ax[0].axvline(7.93, color="0.5", ls="--", lw=0.9, label="model pi width with exact tail, 7.93 us")
    ax[0].set(xlabel="Pulse width (us)", ylabel="|c-1| (mV)", title="(a) pulse-only component |c-1|")
    ax[1].set(xlabel="Pulse width (us)", ylabel="|c0| (mV)", title="(b) RF-independent background |c0|")
    for a in ax:
        a.grid(alpha=0.3); a.legend(fontsize=7.5)
    fig.tight_layout()
    fig.savefig(HERE / "figures" / "fig10_pi_scan_compare.png", dpi=150)

    # 模型换算：实测补偿 π 宽度相对模型 7.93 µs 的比例 → 工作波形 (8.20 µs, A 0.114) 的实际转角
    def rot_exact(w):
        a, t = tc.exact_tail(w)
        return tc.rotation(w, a, t)
    for name, r in out.items():
        if not r["label"].startswith("tail comp"):
            continue
        k = 1.0 / rot_exact(r["tau_pi_us"])
        k_hi = 1.0 / rot_exact(r["tau_pi_us"] - r["tau_pi_sem_us"])
        k_lo = 1.0 / rot_exact(r["tau_pi_us"] + r["tau_pi_sem_us"])
        work = tc.rotation(8.20, 0.114, tc.S_F * tc.TAU_E) * np.array([k, k_lo, k_hi])
        a_exact, tau_c = tc.exact_tail(r["tau_pi_us"])
        r.update(field_scale_vs_model=k, working_point_angle_pi=float(work[0]),
                 working_point_angle_range_pi=[float(work.min()), float(work.max())],
                 pi_waveform=dict(width_us=r["tau_pi_us"], tail_a=float(a_exact), tau_c_us=float(tau_c),
                                  tail_start_mA=float(-a_exact * 190.0)))
        lines.append(f"  {name}: field scale vs model {k:.4f}; working waveform (8.20 us, A 0.114) = {work[0]:.3f} pi "
                     f"[{work.min():.3f}, {work.max():.3f}]; pi waveform = {r['tau_pi_us']:.2f} us, A {a_exact:.4f} "
                     f"({-a_exact * 190:.1f} mA), tau_c {tau_c:.1f} us")
    (HERE / "outputs" / "pi_scan_compare.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "outputs" / "pi_scan_compare.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
