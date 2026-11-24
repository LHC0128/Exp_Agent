"""共享会话骨架：一轮内四段采集同目录，中断后只能整链重测。"""
from __future__ import annotations

from pathlib import Path

from ...experiment_runtime import load_runtime_params
from ...staged_workflow import StagedWorkflow
from ...steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from ...steps.coil_drive_chain import SafetyAbort
from . import acquisition, preparation, run_format
from .models import RfPhaseSensitivityParams
from .stages import ACQUISITION, FINISH_ID, MEASUREMENT_IDS, RUN_ID

# 这些状态说明本轮的该段已经写入过原始帧；再跑必须整链换新目录，不能与旧帧拼接。
_REPEAT_STATUSES = ("failed", "cancelled", "quality_failed")


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    run_id, finish_id = RUN_ID, FINISH_ID
    run_format = run_format
    quality_errors = (SafetyAbort,)
    hardware_free = ("A01", "A02")
    inherited_state_keys = ("clock_sources", "optional_connection_errors")
    output_keys = ("功放", "Z 通道", "Y 射频", "X 偏置", "温控", "共同触发", "主磁场", "DHO924", "MFLI", "相位灵敏度")
    output_source = "功放为已发送命令；Z/X 信号源、MFLI MF-MOD、时钟和示波器有仪器回读，DC 设定不是独立物理测量"
    finished_message = "采集会话已结束；安全收尾结果见回读和错误清单"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.device_session = DeviceSession()
        self.active = False
        self.scope_original = None
        self.scope_touched = False
        self.main_field_state = None
        self.carrier = None
        self._round_marker = None
        self._completed_rounds: dict[str, str] = {}
        self._new_files: list[str] = []

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        cal = self.state.get("calibration") or {}
        noise = self.state.get("noise") or {}
        phase = self.state.get("phase_response") or {}
        return {"daq_settings": {"k0_v_per_rad": cal.get("k0_v_per_rad") if cal.get("accepted") else None,
                "carrier_center_deg": cal.get("carrier_center_deg"),
                "work_point_deg": phase.get("burst_command_deg"), "noise_accepted": noise.get("accepted"),
                "noise_reason": noise.get("reason")}, "main_field": self.state.get("main_field")}

    def validate_update(self, params):
        # 同一轮的原始帧、锁相相位和工作点共享一份参数快照；轮内改参数必须整链重做。
        if getattr(self, "directory", None) is not None and not getattr(self, "round_sealed", False):
            raise ValueError("本轮采集尚未安全结束（A07）；同一轮不能改参数，请先结束会话再新建")

    def validate(self, selected):
        selected = super().validate(selected)
        chosen = [item for item in selected if item in MEASUREMENT_IDS]
        if not chosen:
            return selected
        if self.round_sealed and chosen[0] != MEASUREMENT_IDS[0]:
            raise ValueError("新一轮测量必须从 A03 整周扫描开始；A04–A06 依赖本轮标定")
        if self._measurement_repeat(selected) and tuple(chosen) != MEASUREMENT_IDS:
            raise ValueError("本轮标定、扫频与噪声不可拼接；请整链选择 A03–A06 重跑（会新建运行目录）")
        return selected

    def _measurement_repeat(self, selected):
        chosen = [item for item in selected if item in MEASUREMENT_IDS]
        if not chosen or self.round_sealed:
            return False
        return any(self._round_marker is not None and self._completed_rounds.get(item) == self._round_marker
                   or self.states[item]["status"] in _REPEAT_STATUSES for item in chosen)

    def _needs_new_directory(self, selected):
        return super()._needs_new_directory(selected) or self._measurement_repeat(selected)

    def _new_directory(self, selected=None):
        super()._new_directory(selected)
        self._round_marker = self.directory.root.name
        self._completed_rounds = {}
        self.carrier = None

    def _guard_prepared(self, item):
        if self.carrier is None:
            raise SafetyAbort(f"{item} 缺少本轮已准备的载波会话；请从 A03 开始整链执行")

    def run_step(self, item):
        if item == "A02":
            preparation.connect_and_synchronize(self)
            return
        # 设备和映射已冻结，但重跑前仍校验现用时钟；失败发生在启动激励之前。
        preparation.connect_and_synchronize(self)
        start = len(self.state["data_files"])
        if item == "A03":
            if self.active:
                errors = preparation.park(self)
                if errors:
                    raise SafetyAbort("重跑前安全恢复未通过：" + "；".join(errors))
            self.carrier = preparation.prepare_measurement(self)
            acquisition.phase_response(self, self.carrier)
        elif item == "A04":
            self._guard_prepared(item)
            acquisition.local_calibration(self, self.carrier)
        elif item == "A05":
            self._guard_prepared(item)
            acquisition.frequency_response(self)
        else:
            self._guard_prepared(item)
            acquisition.noise_measurement(self)
        self._new_files = list(self.state["data_files"][start:])

    def after_step(self, item):
        if item in MEASUREMENT_IDS:
            self.states[item]["artifacts"] = list(self._new_files)
            self._completed_rounds[item] = self._round_marker
        if item == RUN_ID:
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
