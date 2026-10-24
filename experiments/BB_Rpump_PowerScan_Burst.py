# %% [markdown]
# # BB Burst Pump 功率扫描：正式采集薄入口
# 参数来自 params/experiments/bb-power-scan-burst.yaml，完整运行共用分步业务。

# %% Cell 1
from lab_workflows.experiment_modules.bb_power_scan_burst.definition import ADAPTER


def main():
    return ADAPTER.run_cli()


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
