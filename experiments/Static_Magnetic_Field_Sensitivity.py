# %% [markdown] Cell 0
# # 静磁场灵敏度：Z 直流逐点色散与多量程噪声采集
# 默认参数统一来自 params/experiments/static-sensitivity.yaml。
# 本入口只采集 raw/；离线拟合与灵敏度分析使用对应 _plot.py。

# %% Cell 1
import argparse

from lab_workflows.experiment_modules.static_sensitivity.definition import ADAPTER


def main() -> int:
    argparse.ArgumentParser(description="从 params/experiments/static-sensitivity.yaml 采集直流色散与噪声，不支持旧配置参数。").parse_args()
    return ADAPTER.run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
