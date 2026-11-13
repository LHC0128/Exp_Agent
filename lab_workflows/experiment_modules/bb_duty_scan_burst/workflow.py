"""完整运行与分步会话共用编排；骨架来自 StagedWorkflow，设备配置、扫描和科学算法各归职责模块。"""
from pathlib import Path

from lab_workflows.staged_workflow import StagedWorkflow

from ...experiment_runtime import load_runtime_params
from ...steps import DeviceSession, disconnect_device_mapping
from . import acquisition, excitation, preparation, run_format
from .models import BBDutyScanBurstParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID, invalidate


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    finish_id, run_id = FINISH_ID, RUN_ID
    run_format = run_format
    output_keys = ("主场", "光功率", "辅助场", "Pump", "SDS", "温控", "锁相")
    inherited_state_keys = ("clock_sources", "temperature_control", "initial_temperature_c", "scope_actual")
    quality_errors = (acquisition.AcquisitionQualityError,)
    output_source = "最近成功模块的写入结果；SDS 与 Burst 关键设置回读，其余未经额外回读"
    finished_message = "会话已结束；Time_sequence 输出、Burst 和 SYNC 保持当前状态"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.channels = {}
        self.device_session = DeviceSession()
        self.scope_config = None
        self.clock_failed = self.outputs_started = False

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        return dict(demod_mode="sds", daq_settings=self.state.get("scope_actual"))

    def run_step(self, item):
        if item == "A02":
            preparation.connect(self)
        elif item == "A03":
            try:
                self.state["clock_sources"] = preparation.clocks(self)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            self.outputs_started = True
            preparation.initial_outputs(self)
            self.outputs.update(主场="设定电流 + ON", 光功率="固定 Pump/Probe DC + ON", 辅助场="OFF", 温控="5 V DC + ON")
        elif item == "A05":
            excitation.configure(self)
        elif item == "A06":
            preparation.prepare_scope(self)
            self.outputs["SDS"] = "双通道 NORMal；实际率与时基已回读"
        elif item == RUN_ID:
            acquisition.acquire_scan(self)
            self.seal_round(self._shutdown(disconnect_tec=False))
            invalidate(self.stages, self.states, "A06")

    def after_step(self, item):
        if item == RUN_ID:
            self.states[item]["artifacts"] = self.state["data_files"]

    def _shutdown(self, *, disconnect_tec=True):
        errors = []
        if self.devices:
            report = preparation.shutdown(self, disconnect_tec=disconnect_tec)
            self.state["safety_shutdown"] = report.to_dict()
            errors.extend(report.errors)
            self.outputs.update(温控="未知" if errors else "5 V DC + ON", SDS="未知" if errors else "STOP",
                                辅助场="未知" if errors else "0 V DC + OFF")
        if disconnect_tec and (self.clock_failed or not self.outputs_started):
            errors.extend(disconnect_device_mapping({str(i): d for i, d in enumerate(self.device_session.unique_devices())}))
        return errors

    def shutdown(self, failure):
        return self._shutdown()


def run(params: BBDutyScanBurstParams, *, project_root=None) -> Path:
    return Workflow.run(params, project_root=project_root)


def main():
    run(load_runtime_params(BBDutyScanBurstParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
