# %% [markdown]
# # 线圈驱动链路电学特性：正式采集薄入口
# 参数来自 params/experiments/coil-drive-characterization.yaml；GUI 完整运行自动进行离线分析。

# %% Cell 1
from lab_workflows.experiment_modules.coil_drive_characterization.definition import ADAPTER


def main():
    return ADAPTER.run_cli()


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
