# %% [markdown]
# # 光子散粒噪声：正式采集薄入口
# 参数来自 params/experiments/photon-shot-noise.yaml；完整运行自动分析由实验平台负责。

# %% Cell 1
from lab_workflows.experiment_modules.photon_shot_noise.definition import ADAPTER


def main():
    return ADAPTER.run_cli()


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
