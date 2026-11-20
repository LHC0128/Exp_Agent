"""提高残余 Bz 的模型评估：工作波形（8.20 µs、A 0.114、τc 25.5）与内置 6.6 µs 在不同 w0 下的 2f 灵敏度与 |J1|。
GS200 约 9.643 kHz/mA（coil_constant_calibration 文档）；残余 1426 Hz 的方向未知，所以列出 ±I 两种可能。输出 outputs/bz_plan.txt。"""
import sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import tailcomp as tc
from lab_workflows.experiment_modules.bperp_null import z_waveform as zw
SC = zw.tail_compensated_waveform(200.0, 8.2, 1.0, 0.114, 25.5, points=16384)[1]
def m(w, A, t, f, ma):
    c0, jx, jy = tc.response(w, A, t, w0=f * tc.HZ, current_ma=ma)
    M = np.array([[jx[1].real, jy[1].real], [jx[1].imag, jy[1].imag]])
    return np.linalg.svd(M, compute_uv=False), np.linalg.norm([jx[0], jy[0]])
lines = []
ref_sv, ref_j1 = m(6.6, 0, 25.5, 1426, 190.0)
lines.append(f"reference (built-in 6.6 us, 1426 Hz): J2 sv {ref_sv[0]:.2e}/{ref_sv[1]:.2e}, |J1| {ref_j1:.2e}")
for f in (300, 1426, 2000, 2900, 4000, 5000, 5800, 7000):
    sv, j1 = m(8.2, 0.114, 25.5, f, 190.0 / SC)
    lines.append(f"work waveform, w0 = {f:5d} Hz: J2 sv {sv[0]:.2e}/{sv[1]:.2e} (weak/ref {sv[1]/ref_sv[1]:.2f}, cond {sv[0]/sv[1]:.1f}), |J1|/ref {j1/ref_j1:.3f}")
for ma in (0.15, 0.30, 0.45):
    df = 9643 * ma
    lines.append(f"GS200 {ma:.2f} mA -> +/-{df:.0f} Hz: |Bz| = {1426+df:.0f} Hz (same sign) or {abs(1426-df):.0f} Hz (opposite sign)")
(HERE / "outputs" / "bz_plan.txt").write_text("\n".join(lines) + "\n", encoding="utf-8"); print("\n".join(lines))
