"""射频相位灵敏度的薄采集入口，由统一实验队列调用。"""
from lab_workflows.experiment_modules.rf_phase_sensitivity.workflow import main

if __name__ == "__main__":
    raise SystemExit(main())
