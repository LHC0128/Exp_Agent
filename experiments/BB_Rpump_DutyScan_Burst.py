# %% [markdown]
# # BB Pump 占空比 Burst 扫描：正式采集薄入口
# 参数来自 params/experiments/bb-duty-scan-burst.yaml；GUI 完整运行自动进行离线分析。

# %% Cell 1
from lab_workflows.experiment_modules.bb_duty_scan_burst.definition import ADAPTER


def main():
    return ADAPTER.run_cli()


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
