# %% 静磁场灵敏度局部优化：逐候选 DC 测量薄入口
"""当前默认值见 params/experiments/static-sensitivity-optimize-local.yaml。"""
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.definition import ADAPTER


def main():
    return ADAPTER.run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
