"""示波器采集薄入口；默认参数位于 params/experiments/scope-capture.yaml。"""

# %% 运行采集
from lab_workflows.experiment_modules.scope_capture import ADAPTER

if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
