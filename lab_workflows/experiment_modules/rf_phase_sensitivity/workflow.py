"""共享会话骨架：分步时不会把静态斜率与其他工况拼接。"""
from __future__ import annotations

from pathlib import Path

from ...experiment_runtime import load_runtime_params
from ...staged_workflow import StagedWorkflow
from ...steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from ...steps.coil_drive_chain import SafetyAbort
from . import acquisition, preparation, run_format
from .models import RfPhaseSensitivityParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    run_id, finish_id = RUN_ID, FINISH_ID
    run_format = run_format
    quality_errors = (SafetyAbort,)
    hardware_free = ("A01", "A02")
    inherited_state_keys = ("clock_sources", "optional_connection_errors")
    output_keys = ("功放", "Z 通道", "Y 射频", "X 偏置", "温控", "共同触发", "主磁场", "DHO924", "MFLI", "相位灵敏度")
    output_source = "功放为已发送命令；Y/X/Z、时钟和示波器有仪器回读，DC 设定不是独立物理测量"
    finished_message = "采集会话已结束；安全收尾结果见回读和错误清单"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.device_session = DeviceSession()
        self.active = False
        self.scope_original = None
        self.scope_touched = False
        self.main_field_state = None

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        cal = self.state.get("calibration") or {}
        noise = self.state.get("noise") or {}
        return {"daq_settings": {"k0_v_per_rad": cal.get("k0_v_per_rad") if cal.get("accepted") else None,
                "carrier_center_deg": cal.get("carrier_center_deg"), "noise_accepted": noise.get("accepted"),
                "noise_reason": noise.get("reason")}, "main_field": self.state.get("main_field")}

    def run_step(self, item):
        if item == "A02":
            preparation.connect_and_synchronize(self)
        else:
            # 设备和映射已冻结，但重跑前仍校验现用时钟；失败发生在启动激励之前。
            preparation.connect_and_synchronize(self)
            acquisition.measure(self, preparation.prepare_measurement(self))

    def after_step(self, item):
        if item == RUN_ID:
            self.states[item]["artifacts"] = list(self.state["data_files"])
            self.seal_round(preparation.park(self))

    def shutdown(self, failure):
        try:
            errors = preparation.park(self)
        except Exception as exc:
            errors = [f"执行安全恢复失败：{exc}"]
        devices = reversed(self.device_session.unique_devices())
        report = run_safety_shutdown(disconnect_targets=(DisconnectTarget(str(i), device) for i, device in enumerate(devices)))
        prior = self.state.get("safety_shutdown") or report.to_dict()
        self.state["safety_shutdown"] = {**prior, "completed": not (errors or report.errors),
                                        "errors": [*errors, *report.errors],
                                        "action_errors": errors,
                                        "disconnect_errors": list(report.disconnect_errors)}
        errors = self.state["safety_shutdown"]["errors"]
        return errors


def run(params: RfPhaseSensitivityParams, *, project_root=None) -> Path:
    return Workflow.run(params, project_root=project_root)


def main():
    run(load_runtime_params(RfPhaseSensitivityParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
