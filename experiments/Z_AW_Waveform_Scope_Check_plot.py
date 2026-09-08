"""Z 任意波线圈波形一致性验证离线分析薄入口。"""

from lab_workflows.experiment_modules.z_aw_waveform_scope_check import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.analyze_cli())
