# %% [markdown]
# # 常数控制与优化控制的论文用离线对比
# 场强沿用运行记录中的 RMS 标定；读取 raw，不连接仪器。

# %% 导入
import argparse
from pathlib import Path

from paper_comparison import compare_for_paper


# %% 选择数据并生成图表
def main() -> None:
    parser = argparse.ArgumentParser(description="DirectAW 常数/优化控制对比，场强采用已确认的 RMS 标定")
    parser.add_argument("constant_run", type=Path, help="常数控制运行目录")
    parser.add_argument("optimized_run", type=Path, help="优化控制运行目录")
    parser.add_argument("--output", type=Path, required=True, help="源运行目录之外的新结果目录")
    parser.add_argument("--band", type=float, nargs=2, default=(3.0, 150.0), metavar=("LOW_HZ", "HIGH_HZ"), help="共同统计频带，默认 3–150 Hz")
    args = parser.parse_args()
    result = compare_for_paper(args.constant_run, args.optimized_run, args.output, band=tuple(args.band))
    print(f"零场斜率提高 {result['comparison']['slope_increase_percent']:.2f}%")
    print(f"共同频带灵敏度降低 {result['comparison']['sensitivity_reduction_percent']:.2f}%")
    print(f"结果：{args.output.resolve()}")


if __name__ == "__main__":
    main()
