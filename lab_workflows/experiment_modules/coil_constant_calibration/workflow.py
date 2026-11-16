"""完整运行与分步会话共用编排；骨架来自 StagedWorkflow，设备、测量与算法各归职责模块。"""
from __future__ import annotations

import fnmatch
import shutil

from lab_workflows.staged_workflow import StagedWorkflow

from ...experiment_runtime import load_runtime_params
from ...steps import DeviceSession
from . import acquisition, excitation, preparation, run_format
from .models import CoilConstantCalibrationParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID

MEASUREMENTS = {"A04": acquisition.measure_zero_and_dc_check,
                "A08": acquisition.measure_scan}
ARTIFACT_PATTERNS = {
    "A04": ("raw/zero_*.npz", "raw/dc_check_*.npz"),
    "A08": ("raw/current_scan_index.npz", "raw/current_*/*.npz"),
}
INHERITED_STAGE_IDS = ("A04",)
INHERITED_STATE_KEYS = ("dc_check",)


class Workflow(StagedWorkflow):
    stages = ACQUISITION
    finish_id, run_id = FINISH_ID, RUN_ID
    run_format = run_format
    output_keys = ("主场", "功放", "Z 通道", "Y RF", "DHO900", "温控", "Pump")
    hardware_free = ("A01",)
    seal_after_run = False
    new_directory_on_rerun = True
    quality_errors = (preparation.SafetyAbort, acquisition.RPointQualityError)
    inherited_state_keys = ("clock_sources",)
    output_source = "最近成功写入的命令；1200B 无状态查询，信号源、GS200 与示波器未额外回读"
    finished_message = "会话已结束；功放 OFF、Z 通道 0 V 并关闭输出"

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.device_session = DeviceSession()
        self.channels: dict[str, int] = {}

    def preflight(self, params):
        return preparation.preflight(params, self.root)

    def extra_snapshot(self):
        return {"daq_settings": {key: self.state.get(key)
                                 for key in ("dc_check", "current_scan", "main_field")}}

    def run_step(self, item):
        if item == "A02":
            preparation.connect(self)
        elif item == "A03":
            self.state["clock_sources"] = preparation.clocks(self)
        elif item in MEASUREMENTS:
            MEASUREMENTS[item](self)
        elif item == "A05":
            preparation.configure_initial_outputs(self)
        elif item == "A06":
            self.state["temperature_control"] = preparation.configure_temperature(self).to_dict()
        elif item == "A07":
            excitation.configure_pump(self)
            self.state["actual_rate_sa_s"] = excitation.configure_response(self)
        elif item == "A09":
            self._shutdown_round()
        else:
            raise ValueError(f"未知采集节点: {item}")

    def after_step(self, item):
        patterns = ARTIFACT_PATTERNS.get(item)
        if patterns:
            files = self.state.get("data_files", [])
            self.states[item]["artifacts"] = [name for name in files
                                              if any(fnmatch.fnmatch(name, pattern) for pattern in patterns)]
        super().after_step(item)

    def _shutdown_round(self):
        report = preparation.shutdown_outputs(self)
        self.seal_round(list(report["errors"]))

    def on_new_directory(self, previous, old_state):
        """A08 重跑新建目录：复制仍有效的 A04 直流核对照，原目录不改动。"""
        if previous is None:
            return
        copied: list[str] = []
        for stage_id in INHERITED_STAGE_IDS:
            if self.states[stage_id]["status"] != "completed":
                continue
            for name in self.states[stage_id].get("artifacts", []):
                source = previous.root / name
                if not source.is_file():
                    continue
                target = self.directory.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(name)
        self.state["data_files"] = list(dict.fromkeys([*self.state.get("data_files", []), *copied]))
        for key in INHERITED_STATE_KEYS:
            if key in old_state:
                self.state[key] = old_state[key]
        if copied:
            self.state["inherited_from"] = previous.root.name
            self.state["inherited_files"] = sorted(copied)

    def shutdown(self, failure):
        if not self.devices:
            return []
        if self.round_sealed:
            errors = preparation.disconnect_all(self)
            self.outputs.update({"功放": "OFF（已断开）", "Z 通道": "OFF（已断开）",
                                 "Y RF": "OFF（已断开）", "DHO900": "已断开", "温控": "5 V + ON，已断开"})
            return errors
        report = preparation.shutdown_outputs(self)
        errors = list(report["errors"])
        errors.extend(preparation.disconnect_all(self))
        return errors


def run(params: CoilConstantCalibrationParams, *, project_root=None):
    return Workflow.run(params, project_root=project_root)


def main():
    run(load_runtime_params(CoilConstantCalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
