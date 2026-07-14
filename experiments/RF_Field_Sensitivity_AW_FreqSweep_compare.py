# %% [markdown] Cell 0
# # RF 场频率响应跨运行对比
#
# 读取两个既有运行的 `results/analysis.json`，叠加绘制与
# `freq_response_amplitude.png` 相同口径的 R 中位数幅频响应。

# %% Cell 1
from pathlib import Path
import sys

PROJECT_ROOT = Path.cwd()
while not (PROJECT_ROOT / "params").exists() and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lab_workflows.analysis import compare_frequency_response_amplitude


# %% Cell 2
EXPERIMENT_TYPE = "RF_Field_Sensitivity_AW_FreqSweep_DirectAW"
RUN_DIR_NAMES = (
    "0714_1129_freq_resp_direct_aw",
    "0714_1035_freq_resp_direct_aw",
)
OUTPUT_DIR_NAME = "0714_1129_vs_0714_1035"
OUTPUT_FILE_NAME = "freq_response_amplitude_comparison.png"
SHOW_FIGURE = True


# %% Cell 3
def main() -> Path:
    """生成两次 DirectAW 频率扫描的幅频响应对比图。"""

    data_base = PROJECT_ROOT / "data" / EXPERIMENT_TYPE
    run_dirs = [data_base / name for name in RUN_DIR_NAMES]
    output_dir = PROJECT_ROOT / "results" / EXPERIMENT_TYPE / OUTPUT_DIR_NAME

    output_path = compare_frequency_response_amplitude(
        run_dirs,
        output_dir,
        output_name=OUTPUT_FILE_NAME,
        show_figure=SHOW_FIGURE,
    )
    print(f"对比图已保存: {output_path}")
    return output_path


# %% Cell 4
if __name__ == "__main__":
    main()
