"""完整运行与分步会话共用的编排；骨架来自 StagedWorkflow，设备与测量各归职责模块。"""
from __future__ import annotations

from pathlib import Path

from lab_workflows.staged_workflow import StagedWorkflow

from ...experiment_runtime import load_runtime_params
from ...steps import DeviceSession, disconnect_device_mapping
from . import acquisition, preparation, run_format
from .models import PiPulseCalibrationParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID

MEASUREMENTS = {"A03": acquisition.measure_scan}
ARTIFACT_PREFIX = {"A03": ("raw/scan.csv", "raw/scan_")}


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    finish_id, run_id = FINISH_ID, RUN_ID
    run_format = run_format
    output_keys = ("功放", "Z 通道", "DHO900")
    hardware_free = ("A01", "A02")
    seal_after_run = False
    quality_errors = (preparation.SafetyAbort,)
    output_source = "最近成功写入的命令；1200B 无状态查询，信号源与示波器未额外回读"
    finished_message = "会话已结束；功放 OFF、Z 通道 0 V 并关闭输出"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.device_session = DeviceSession()
        self.current_scale = None

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        keys = ("scan_widths", "scan_shots")
        return {"daq_settings": {key: self.state.get(key) for key in keys}}

    def run_step(self, item):
        if item == "A02":
            preparation.connect(self)
            self.state["clock_sources"] = preparation.clocks(self)
            self.outputs["DHO900"] = "已连接"
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
        self.outputs.update({"功放": "未知" if action_errors else "OFF（已发送 AOF）",
                             "Z 通道": "未知" if action_errors else "DC 0 V + OFF，同步 OFF",
                             "DHO900": "未知" if action_errors else "STOP，已断开"})
        return errors


def run(params: PiPulseCalibrationParams, *, project_root=None) -> Path:
    return Workflow.run(params, project_root=project_root)


def main():
    run(load_runtime_params(PiPulseCalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
