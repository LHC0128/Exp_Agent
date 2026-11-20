"""生成 Z 拖尾补偿波形文件（提案格式样例，尚未接入实验）。

格式：`#` 注释头（生成参数）+ 16384 行单列归一化电流；一个周期，从 +脉冲上升沿起算，最大绝对值 1
对应 Z_PEAK_CURRENT_MA。X/Y 补偿形状 x(t) 另存一列文件（第二阶段用）。
用法：python make_waveform.py <w_us> [A] [tau_c_us]   缺省 A、τc 取精确补偿值。
"""
import hashlib
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tailcomp as tc  # noqa: E402

POINTS = 16384


def make(width, A=None, tau_c=None):
    A_ex, tc_ex = tc.exact_tail(width)
    A = A_ex if A is None else A
    tau_c = tc_ex if tau_c is None else tau_c
    t = np.arange(POINTS) * tc.PERIOD / POINTS
    z = tc.drive(width, A, tau_c, t=t)
    z = z / np.max(np.abs(z))
    x = tc.lowpass(tc.drive(width, A, tau_c), tc.TAU_E)          # X/Y 形状（慢分量 x，未归一）
    x = np.interp(t, tc.TU, x, period=tc.PERIOD)
    head = [f"period_us: {tc.PERIOD}", f"points: {POINTS}", f"width_us: {width}", f"edge_us: {tc.EDGE}",
            f"tail_A: {A:.6f}", f"tail_tau_c_us: {tau_c:.4f}", f"model_s_f: {tc.S_F:.4f}", f"model_tau_e_us: {tc.TAU_E:.3f}",
            f"rotation_pi_at_190mA: {tc.rotation(width, A, tau_c):.4f}", "generator: 10_tail_compensation/make_waveform.py"]
    out = HERE / "waveforms"; out.mkdir(exist_ok=True)
    name = f"z_tail_w{width:.2f}_A{A:.3f}_tc{tau_c:.1f}"
    body = "\n".join(f"{v:.8f}" for v in z) + "\n"
    (out / f"{name}.csv").write_text("".join(f"# {h}\n" for h in head) + body, encoding="utf-8")
    (out / f"{name}_xy_shape.csv").write_text("".join(f"# {h}\n" for h in head + ["column: slow state x(t), same time base"])
                                             + "\n".join(f"{v:.8f}" for v in x) + "\n", encoding="utf-8")
    print(name, "sha256", hashlib.sha256(body.encode()).hexdigest()[:16], "min", z.min(), "max", z.max(),
          "antisym err", np.abs(z[:POINTS // 2] + z[POINTS // 2:]).max())


if __name__ == "__main__":
    args = [float(a) for a in sys.argv[1:]]
    make(*args) if args else make(7.93)
