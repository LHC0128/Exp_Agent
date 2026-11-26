"""完整运行与分步会话共用的编排；骨架来自 StagedWorkflow，设备与测量各归职责模块。"""
from __future__ import annotations

from pathlib import Path

from lab_workflows.staged_workflow import StagedWorkflow

from ...experiment_runtime import load_runtime_params
from ...steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from . import acquisition, preparation, run_format
from .models import RfPhaseResponseParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID

MEASUREMENTS = {RUN_ID: acquisition.measure_phase_response}
ARTIFACT_PREFIX = {RUN_ID: ("raw/coarse_",)}


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    finish_id, run_id = FINISH_ID, RUN_ID
    run_format = run_format
    output_keys = ("Z 通道", "X 偏置", "Y 射频", "共同触发", "温控", "MFLI", "主磁场", "相位响应")
    hardware_free = ("A01", "A02")
    seal_after_run = False
    quality_errors = (preparation.SafetyAbort,)
    output_source = ("最近成功写入的命令；DG 直驱无 1200B 可查询，Z 脉冲幅度、MFLI Out1/MF-MOD 节点、"
                     "主磁场关闭与参考时钟经回读")
    finished_message = "会话已结束；Z/X 关闭归零、MFLI Out1 与 MF-MOD 已关闭，温控与 GS200 已恢复"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.device_session = DeviceSession()
        self.active = False
        self.main_field_state = None
        self.carrier = None

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        # 会话模型只接受已声明键；实验专属读数放进 daq_settings 字典。
        return {"daq_settings": {"phase_response": self.state.get("phase_response"),
                                 "phase_response_acquisition": self.state.get("phase_response_acquisition")},
                "main_field": self.state.get("main_field")}

    def validate_update(self, params):
        # 同一轮采集共用一份参数快照；设备已连接时改参数必须整轮重做。
        if self.device_session.unique_devices() and not self.closed:
            raise ValueError("采集会话尚未安全结束；同一轮不能改参数，请先结束会话再新建")

    def run_step(self, item):
        if item == "A02":
            preparation.connect_and_synchronize(self)
            self.carrier = preparation.prepare_measurement(self)
            return
        if self.carrier is None:
            raise preparation.SafetyAbort("缺少本轮已准备的 MFLI 载波会话；请从 A02 开始")
        preparation.arm_z(self)
        MEASUREMENTS[item](self)

    def after_step(self, item):
        if item in MEASUREMENTS:
            prefixes = ARTIFACT_PREFIX[item]
            self.states[item]["artifacts"] = [f for f in self.state["data_files"] if f.startswith(prefixes)]
        super().after_step(item)

    def shutdown(self, failure):
        try:
            errors = preparation.park(self)
        except Exception as exc:
            errors = [f"执行安全恢复失败：{exc}"]
        devices = list(self.device_session.unique_devices())
        report = run_safety_shutdown(
            disconnect_targets=(DisconnectTarget(str(index), device)
                                for index, device in enumerate(devices)))
        prior = self.state.get("safety_shutdown") or report.to_dict()
        self.state["safety_shutdown"] = {**prior, "completed": not (errors or report.errors),
                                        "errors": [*errors, *report.errors],
                                        "action_errors": errors,
                                        "disconnect_errors": list(report.disconnect_errors)}
        return self.state["safety_shutdown"]["errors"]


def run(params: RfPhaseResponseParams, *, project_root=None) -> Path:
    return Workflow.run(params, project_root=project_root)


def main():
    run(load_runtime_params(RfPhaseResponseParams))
    return 0


if __name__ == "__main__":
    main()
