"""生成 bperp_null 用的 Z 拖尾补偿波形文件（离线，不连接仪器）。

主脉冲是与内置相同的 ±π 升余弦脉冲；每个脉冲结束后接反号指数尾 −A·exp(−(t−w)/τc)，尾按周期延拓叠加，
整体归一化到最大绝对值 1（对应 Z_PEAK_CURRENT_MA）。A、τc 可直接给出，也可由屏蔽模型 s_f、τe 精确算出：
τc = s_f·τe，A 使脉冲后原子处 z 场为零。

用法（在运行目录执行时默认写到 data/z_waveforms/）：
    python tools/make_z_waveform.py --width 7.93 --s-f 0.544 --tau-e 46.9
    python tools/make_z_waveform.py --width 8.2 --tail-a 0.12 --tau-c 25.5 --out-dir <目录>
    python tools/make_z_waveform.py --width 6.6 --tail-a 0           # 无尾，等同内置脉冲（用于对照）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab_workflows.common import find_project_root  # noqa: E402
from lab_workflows.experiment_modules.bperp_null import z_waveform  # noqa: E402
from lab_workflows.experiment_modules.bperp_null.models import ARB_POINTS  # noqa: E402


def build(args) -> tuple[Path, dict]:
    from_model = args.tail_a is None
    if from_model:
        if args.s_f is None or args.tau_e is None:
            raise SystemExit("需要给 --tail-a（可加 --tau-c），或给 --s-f 与 --tau-e 由模型计算")
        if args.tau_c is not None:
            raise SystemExit("--s-f/--tau-e 模式下 τc = s_f·τe，不能再给 --tau-c")
        tail_a, tau_c = z_waveform.exact_tail(args.width, args.edge, args.s_f, args.tau_e)
    elif args.s_f is not None or args.tau_e is not None:
        raise SystemExit("--tail-a 与 --s-f/--tau-e 二选一")
    else:
        tail_a, tau_c = args.tail_a, (args.tau_c if args.tau_c is not None else 25.5)
    wave, scale = z_waveform.tail_compensated_waveform(args.period, args.width, args.edge, tail_a, tau_c,
                                                       points=ARB_POINTS)
    header = {"period_us": f"{args.period:g}", "points": ARB_POINTS, "width_us": f"{args.width:g}",
              "edge_us": f"{args.edge:g}", "tail_a": f"{tail_a:.6f}", "tail_tau_c_us": f"{tau_c:.4f}",
              "normalization_scale": f"{scale:.6f}",
              "note": "max|v| = 1 corresponds to Z_PEAK_CURRENT_MA; main-pulse flat top = 1/normalization_scale"}
    if from_model:
        header.update(model_s_f=f"{args.s_f:g}", model_tau_e_us=f"{args.tau_e:g}")
    header["generator"] = "tools/make_z_waveform.py"
    name = args.name or f"z_tail_w{args.width:.2f}_A{tail_a:.3f}_tc{tau_c:.1f}.csv"
    out_dir = Path(args.out_dir) if args.out_dir else find_project_root() / z_waveform.WAVEFORM_DIR
    path = z_waveform.write_waveform_file(out_dir / z_waveform.waveform_path(".", name).name, wave, header)
    z_waveform.load_z_waveform(path, points=ARB_POINTS, period_us=args.period)   # 写后按实验同一规则复核
    return path, header


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--width", type=float, required=True, help="主脉冲总宽（µs，含两沿）")
    parser.add_argument("--edge", type=float, default=1.0, help="升余弦沿宽（µs）")
    parser.add_argument("--period", type=float, default=200.0, help="脉冲周期（µs），须等于 1e6/PULSE_FREQ_HZ")
    parser.add_argument("--tail-a", type=float, default=None, help="尾幅，相对主脉冲平顶")
    parser.add_argument("--tau-c", type=float, default=None, help="尾时间常数（µs），缺省 25.5")
    parser.add_argument("--s-f", type=float, default=None, help="屏蔽模型快速比例 s_f")
    parser.add_argument("--tau-e", type=float, default=None, help="屏蔽模型时间常数 τe（µs）")
    parser.add_argument("--name", default=None, help="输出文件名（.csv）")
    parser.add_argument("--out-dir", default=None, help="输出目录；缺省为 <项目根>/data/z_waveforms")
    args = parser.parse_args(argv)
    path, header = build(args)
    print(path)
    for key, value in header.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
