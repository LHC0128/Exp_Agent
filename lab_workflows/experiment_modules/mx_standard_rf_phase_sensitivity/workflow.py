"""标准 Mx 相图与噪声路径的完整运行与分步会话编排。

骨架来自 StagedWorkflow：清单、修订、快照、目录轮换与分析指纹由基类负责，本模块只编排
A02 初始条件、A03 相图、A04 工作点、A05 噪声与安全收尾；workflow 实例自身就是采集 context。
"""
import logging
from pathlib import Path

from lab_workflows.staged_workflow import StagedWorkflow

from ...experiment_runtime import load_runtime_params
from ...steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from . import acquisition, preparation, run_format
from .models import MxStandardRfPhaseSensitivityParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    finish_id, run_id = FINISH_ID, RUN_ID
    run_format = run_format
    hardware_free = ("A01",)
    inherited_state_keys = ("clock_sources", "optional_connection_errors")
    output_keys = ("主磁场", "Y 射频", "Z 通道", "X 偏置", "温控", "共同触发", "MFLI", "相位扫描", "工作点", "噪声")
    output_source = ("载波节点、Z/X 通道、温控与 GS200 有仪器回读；相位角与斜率由本轮实测原始帧给出")
    finished_message = "本轮已安全封存；载波与 Z/X 关闭，温控与主场已恢复"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.device_session = DeviceSession()
        self.active = False
        self.main_field_state = None
        self.carrier = None

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        diagram = self.state.get("phase_diagram") or {}
        working = self.state.get("working_point") or {}
        noise = self.state.get("noise") or {}
        return {"daq_settings": {"radius_v": (diagram.get("circle_fit") or {}).get("radius_v"),
                                 "working_point_deg": working.get("command_deg"),
                                 "noise_segments": len(noise.get("labels") or ()),
                                 "time_constant_s": (self.state.get("lockin") or {}).get("time_constant_s")}}

    def run_step(self, item):
        if item == "A02":
            preparation.connect_and_synchronize(self)
            preparation.prepare(self)
        elif item == "A03":
            acquisition.acquire_phase_diagram(self)
        elif item == "A04":
            acquisition.acquire_working_point(self)
        elif item == RUN_ID:
            acquisition.acquire_noise(self)
            self.seal_round(self._shutdown(None))

    def after_step(self, item):
        if item == RUN_ID:
            self.states[item]["artifacts"] = list(self.state.get("data_files") or ())

    def _shutdown(self, failure):
        """调用本实验安全收尾并断开；收尾错误记录为列表，不覆盖原始采集错误。"""
        errors = []
        if self.devices:
            try:
                errors = preparation.park(self)
            except BaseException as exc:
                logging.getLogger(__name__).exception("安全收尾异常；保留原始采集错误")
                errors = [f"安全收尾异常：{exc}"]
        devices = reversed(self.device_session.unique_devices())
        report = run_safety_shutdown(
            disconnect_targets=(DisconnectTarget(str(index), device) for index, device in enumerate(devices)))
        prior = self.state.get("safety_shutdown") or report.to_dict()
        self.state["safety_shutdown"] = {**prior, "completed": not (errors or report.errors),
                                        "errors": [*errors, *report.errors],
                                        "action_errors": errors,
                                        "disconnect_errors": list(report.disconnect_errors)}
        self.active, self.main_field_state = False, None
        return list(self.state["safety_shutdown"]["errors"])

    def shutdown(self, failure):
        return self._shutdown(failure)


def run(params: MxStandardRfPhaseSensitivityParams, *, project_root=None) -> Path:
    return Workflow.run(params, project_root=project_root)


def main() -> None:
    """读取子进程参数并调用采集编排。"""
    run(load_runtime_params(MxStandardRfPhaseSensitivityParams))


if __name__ == "__main__":
    main()
