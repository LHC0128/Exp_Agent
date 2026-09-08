"""Z 任意波实际电流闭环波形校正采集薄入口。"""

# %% Cell 1
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction import ADAPTER


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
