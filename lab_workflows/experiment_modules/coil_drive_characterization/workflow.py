"""完整运行与分步会话共用编排；骨架来自 StagedWorkflow，设备、测量与算法各归职责模块。"""
from __future__ import annotations

import shutil
from pathlib import Path

from lab_workflows.staged_workflow import StagedWorkflow

from ...experiment_runtime import load_runtime_params
from ...steps import DeviceSession, disconnect_device_mapping
from . import acquisition, preparation, run_format
from .models import CoilDriveCharacterizationParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID

MEASUREMENTS = {"A03": acquisition.measure_zero_and_gain, "A04": acquisition.measure_transfer,
                "A05": acquisition.measure_steps}
ARTIFACT_PREFIX = {"A03": ("raw/zero_", "raw/gain_"), "A04": ("raw/sine_",), "A05": ("raw/pulse_",)}


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    finish_id, run_id = FINISH_ID, RUN_ID
    run_format = run_format
    output_keys = ("功放", "Z 通道", "DHO900")
    hardware_free = ("A01", "A02")
    seal_after_run = False
    new_directory_on_rerun = False
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
        return {"daq_settings": {key: self.state.get(key) for key in ("zero_offset", "gain_check", "transfer_preview")}}

    def run_step(self, item):
        if item == "A02":
            preparation.connect(self)
            self.state["clock_sources"] = preparation.clocks(self)
            self.outputs["DHO900"] = "已连接"
        else:
            MEASUREMENTS[item](self)

    def after_step(self, item):
        if item in MEASUREMENTS:
            self.states[item]["artifacts"] = [f for f in self.state["data_files"] if f.startswith(ARTIFACT_PREFIX[item])]
        super().after_step(item)

    def on_new_directory(self, previous, old_state):
        if previous and self.states["A03"]["status"] == "completed":
            # A03 参数未变仍有效：复制其原始帧，使新目录可独立分析；原目录不改动。
            raw = self.directory.root / "raw"
            raw.mkdir(exist_ok=True)
            for name in self.states["A03"].get("artifacts", []):
                shutil.copy2(previous.root / name, raw / Path(name).name)
            self.state.update(zero_offset=old_state.get("zero_offset"), gain_check=old_state.get("gain_check"),
                              data_files=list(self.states["A03"].get("artifacts", [])),
                              zero_inherited_from=previous.root.name)

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


def run(params: CoilDriveCharacterizationParams, *, project_root=None) -> Path:
    return Workflow.run(params, project_root=project_root)


def main():
    run(load_runtime_params(CoilDriveCharacterizationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
