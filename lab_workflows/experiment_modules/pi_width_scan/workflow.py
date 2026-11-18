"""完整运行与分步会话共用的编排；骨架来自 StagedWorkflow，设备与测量各归职责模块。"""
from __future__ import annotations

from pathlib import Path

from lab_workflows.staged_workflow import StagedWorkflow

from ...experiment_runtime import load_runtime_params
from ...steps import DeviceSession, disconnect_device_mapping
from . import acquisition, preparation, run_format
from .models import PiWidthScanParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID

MEASUREMENTS = {"A03": acquisition.measure_phase_scan, "A04": acquisition.measure_scan}
ARTIFACT_PREFIX = {"A03": ("raw/phase_scan.csv", "results/phase_vs_phase.png"),
                   "A04": ("raw/scan.csv", "raw/R_")}


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    finish_id, run_id = FINISH_ID, RUN_ID
    run_format = run_format
    output_keys = ("功放", "X 偏置", "Z 通道", "Y 射频", "共同触发", "温控", "MFLI", "主磁场", "DHO900",
                   "相位粗扫")
    hardware_free = ("A01", "A02")
    seal_after_run = False
    quality_errors = (preparation.SafetyAbort,)
    output_source = "最近成功写入的命令；1200B 无状态查询，主磁场关闭与参考时钟经回读"
    finished_message = "会话已结束；功放 OFF、Z 通道 0 V、Y 射频 OFF，温控与 GS200 已恢复"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.device_session = DeviceSession()
        self.main_field_state = None

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        keys = ("scan_widths", "scan_repeats", "scan_phases_deg", "scan_points_completed")
        return {"scan_progress": {key: self.state.get(key) for key in keys},
                "phase_check": self.state.get("phase_check"),
                "main_field": self.state.get("main_field")}

    def run_step(self, item):
        if item == "A02":
            preparation.connect(self)
            self.state["clock_sources"] = preparation.clocks(self)
            # 主磁场先关，再起射频与脉冲输出。
            preparation.disable_main_field(self)
            preparation.configure_chain(self)
            preparation.configure_lockin(self)
            preparation.configure_rf(self)
            preparation.configure_trigger(self)
            preparation.configure_z_idle(self)
            preparation.configure_x_bias(self)
            preparation.enable_temperature_switch(self)
            self.state["optional_connection_errors"] = self.devices.get("optional_connection_errors", {})
            self.outputs["DHO900"] = "已连接（不采集，仅供人工核对脉冲波形）"
        else:
            MEASUREMENTS[item](self)

    def after_step(self, item):
        if item in MEASUREMENTS:
            prefixes = ARTIFACT_PREFIX[item]
            self.states[item]["artifacts"] = [f for f in self.state["data_files"] if f.startswith(prefixes)]
        super().after_step(item)

    def shutdown(self, failure):
        if not self.devices:
            return list(disconnect_device_mapping({str(i): d for i, d in enumerate(self.device_session.unique_devices())}))
        action_errors, disconnect_errors = preparation.shutdown(self)
        errors = [*action_errors, *disconnect_errors]
        self.state["safety_shutdown"] = dict(completed=not errors, errors=errors)
        return errors


def run(params: PiWidthScanParams, *, project_root=None) -> Path:
    return Workflow.run(params, project_root=project_root)


def main():
    run(load_runtime_params(PiWidthScanParams))
    return 0


if __name__ == "__main__":
    main()
