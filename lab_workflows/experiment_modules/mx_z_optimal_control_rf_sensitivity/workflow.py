"""完整运行与分步会话共用的 Mx Z 灵敏度编排，兼容既有共享导入。"""
from __future__ import annotations
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
import shutil
import hashlib
import yaml
from uuid import uuid4
from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import (DeviceSession, create_run_directory, save_corrected_control_source_snapshot,
    snapshot_main_field_state, restore_main_field_state, disconnect_device_mapping)
from lab_workflows.workflow_state import write_state
from ..mx_y_rf_sensitivity.acquisition import acquire_r, acquire_rxy, summarize_r, summarize_rxy
from ..mx_y_rf_sensitivity.workflow import RPointQualityError, _acquire_noise, _acquire_valid_r_point, _temperature_gated_acquire
from ..mx_y_rf_sensitivity.excitation import _configure_response_demodulator
from .models import MxZOptimalControlRFParams
from . import preparation, acquisition, run_format
from .preparation import _connect_control_devices, _configure_common_outputs, safe_shutdown
from .excitation import (_configure_y_rf_output, _validate_y_rf_envelope, _restore_y_rf_burst_sine,
    _rearm_y_rf, _rearm_control)
from .acquisition import _save_rxy_point, _rxy_summary_arrays, _reacquire_phase_outliers_once
from .stages import ACQUISITION, FINISH_ID, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


def _acquire_valid_rxy_point(*args, **kwargs):
    return acquisition._acquire_valid_rxy_point(*args, **kwargs, reader=acquire_rxy, gated_acquire=_temperature_gated_acquire)


def _acquire_phase_scan(*args, **kwargs):
    return acquisition._acquire_phase_scan(*args, **kwargs,
        r_point_acquirer=_acquire_valid_r_point, rxy_point_acquirer=_acquire_valid_rxy_point)


def _acquire_amplitude_scan(*args, **kwargs):
    return acquisition._acquire_amplitude_scan(*args, **kwargs, point_acquirer=_acquire_valid_r_point)


def _acquire_control_noise(*args, **kwargs):
    return acquisition._acquire_control_noise(*args, **kwargs, noise_acquirer=_acquire_noise)


@dataclass(frozen=True, slots=True)
class StageModePolicy:
    """只声明模式恢复、重跑与失败显示所关联的阶段，不携带运行状态。"""
    response_prepare_id: str
    response_required_ids: frozenset[str]
    new_round_on_repeat_ids: frozenset[str]
    uncertain_y_output_ids: frozenset[str]

    def __post_init__(self):
        for name in ("response_required_ids", "new_round_on_repeat_ids", "uncertain_y_output_ids"):
            object.__setattr__(self, name, frozenset(getattr(self, name)))


class Workflow:
    stages = ACQUISITION
    # 尚未调用父构造的同家族子类也沿用这些不可变默认规则。
    mode_policy = StageModePolicy(
        response_prepare_id="A03",
        response_required_ids=frozenset(("A04", "A05")),
        new_round_on_repeat_ids=frozenset(("A04", "A05", "A06")),
        uncertain_y_output_ids=frozenset(("A04", "A05", "A06")),
    )
    initial_metadata = None

    def __init__(self, params, *, project_root=None, session_id=None, preflight=None,
                 initial_metadata: Mapping[str, str | int] | None = None,
                 mode_policy: StageModePolicy | None = None):
        """完整初始化；注入预检仅用于构造，后续重检由具体实验实现。"""
        if initial_metadata is not None:
            metadata = dict(initial_metadata)
            required = {"experiment_id", "data_type", "execution_mode", "raw_format_version"}
            if not required <= metadata.keys() or metadata.keys() - (required | {"measurement_mode"}):
                raise ValueError("初始元数据只允许完整实验身份、原始格式版本与可选测量模式")
            if (type(metadata["raw_format_version"]) is not int or metadata["raw_format_version"] < 1
                    or any(not isinstance(value, str) or not value
                           for key, value in metadata.items() if key != "raw_format_version")):
                raise ValueError("初始元数据的身份与模式必须是非空字符串，原始格式版本必须是正整数")
            self.initial_metadata = MappingProxyType(metadata)
        if mode_policy is not None:
            self.mode_policy = mode_policy
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        check = preparation.preflight if preflight is None else preflight
        self.params, self.mapping = check(params, self.root)
        self.shutdown_parameters = self.params
        self.corrected = self.applied = self.executed_source = self.main_field_state = None
        self.response_parameters = self.params
        self.demod_mode = "unknown"
        self.selected_phase = None
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = False
        self.quality_error = self.progress = None
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("X", "Y RF", "Z", "温控", "Pump", "共同触发")}


    def _initial_state(self):
        if self.initial_metadata is not None:
            return dict(self.initial_metadata, completion_status="running")
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running")


    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"], (self.root / "params/safety_limits.yaml").read_bytes())


    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请安全结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)
        if self.corrected is not None:
            current, _ = preparation.load_source(self.params, self.root)
            if current.run_name == self.corrected.run_name and current.waveform_sha256 != self.corrected.waveform_sha256:
                invalidate(self.stages, self.states, "A01")
                self.corrected = None
                self.revision += 1
                raise ValueError("闭环冻结文件已改变，请从A01重新预检来源")


    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功模块的写入结果；未经额外回读", demod_mode=self.demod_mode,
            safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sources", {}),
            daq_settings={"actual_response_rate_sa_s": self.state.get("actual_rates", {}).get("response_sa_s")})


    def publish(self, message="等待选择下一模块"):
        value = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", value)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", value)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": value})


    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)


    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(MxZOptimalControlRFParams.from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块")


    def _new_directory(self, selected):
        previous, inherited = self.directory, deepcopy(self.state)
        if previous and not self.round_sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已采集数据")
            self.save()
        suffix = 0
        while True:
            try:
                tag = self.params.run_tag if not suffix else f"{self.params.run_tag}_{suffix:02d}"
                self.directory = create_run_directory(DATA_TYPE, tag, self.params.to_external(), schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.round_sealed = False
        self.run_parameters = self.params.to_external()
        self.state = self._initial_state()
        affected = {stage.id for item in selected if item != "A01" for stage in invalidated_stages(self.stages, item)}
        if previous:
            self.state["source_run_id"] = previous.root.name
            for key in ("actual_rates", "clock_sources", "hf2_configuration", "temperature_control", "device_snapshot", "phase_calibration", "residual_control_phase_scan", "selected_y_rf_phase_deg", "selected_control_phase_deg", "noise_rf"):
                if key in inherited:
                    self.state[key] = inherited[key]
            for path in previous.raw.glob("*"):
                owner = "A04" if path.name.startswith("phase") else "A05" if path.name.startswith("amplitude") else "A06" if path.name.startswith("noise") else None
                if path.is_file() and (owner is None or (owner not in affected and self.states[owner]["status"] == "completed")):
                    shutil.copy2(path, self.directory.raw / path.name)
            if "A04" not in affected and self.states["A04"]["status"] == "completed":
                for name in ("phase_calibration.yaml", "phase_calibration.png"):
                    source = previous.results / name
                    if source.is_file():
                        shutil.copy2(source, self.directory.results / name)
        if self.corrected is not None:
            self.state.update(preparation.initial_config(self.params, self.corrected, self.applied))
        self.state.update(raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running")
        self.save()

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        policy = self.mode_policy
        if (self.demod_mode == "noise" and set(selected).intersection(policy.response_required_ids)
                and policy.response_prepare_id not in selected):
            raise ValueError(f"当前为噪声模式，请显式选择 {policy.response_prepare_id} 重新准备响应解调")
        hardware = [item for item in selected if item not in ("A01", FINISH_ID)]
        if hardware and (self.directory is None or self.round_sealed or self.run_parameters != self.params.to_external()
                or any(item in policy.new_round_on_repeat_ids and self.states[item]["status"] != "pending" for item in hardware)):
            self._new_directory(selected)
        self.revision += 1
        self.quality_error = None
        for item in selected:
            if item == FINISH_ID:
                self.finish()
                break
            check_cancelled()
            invalidate(self.stages, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            failure = None
            try:
                if item != "A01":
                    self.recheck()
                self.publish(next(stage.title for stage in self.stages if stage.id == item))
                self._execute_step(item)
                stage = next(stage for stage in self.stages if stage.id == item)
                artifacts = [path.relative_to(self.directory.root).as_posix() for pattern in stage.artifacts
                    for path in self.directory.root.glob(pattern) if path.is_file()] if self.directory else []
                self.states[item] = {"status": "completed", "error": "", "artifacts": artifacts}
            except BaseException as exc:
                failure = exc
                if notes := getattr(exc, "acquisition_cleanup_notes", None) or getattr(exc, "__notes__", None):
                    self.state["acquisition_cleanup_notes"] = list(notes)
                if item in policy.uncertain_y_output_ids:
                    self.outputs["Y RF"] = "未知（本模块未完成）"
                self.states[item] = {"status": "quality_failed" if isinstance(exc, RPointQualityError) else "failed", "error": str(exc)}
                self.state["failure_reason"] = str(exc)
                try:
                    self.save()
                except Exception as error:
                    self.state["state_save_error"] = str(error)
                if isinstance(exc, RPointQualityError):
                    self.quality_error = exc
                    break
                raise
            finally:
                self.current_step = None
                try:
                    self.publish(f"{item} {self.states[item]['status']}")
                except Exception:
                    if failure is None:
                        raise
        return self.snapshot()


    def _execute_step(self, item):
        params, devices, channels = self.params, self.devices, self.channels
        residual = params.phase_cal_rf_amplitude_vpp == 0.0
        if item == "A01":
            self.corrected, self.applied = preparation.load_source(params, self.root)
        elif item == "A02":
            self.state.update(preparation.initial_config(params, self.corrected, self.applied))
            if not devices:
                try:
                    self.devices, self.channels = _connect_control_devices(self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
                self.main_field_state = snapshot_main_field_state(self.devices["gs200"])
                self.state["device_snapshot"] = preparation._initial_state_snapshot(self.devices, self.channels)
                self.state["device_snapshot"]["main_field_before_configuration"] = asdict(self.main_field_state)
            save_corrected_control_source_snapshot(self.directory.raw, self.corrected, self.applied)
            if hashlib.sha256((self.directory.raw / "source_corrected_control_waveform.npz").read_bytes()).hexdigest() != self.corrected.waveform_sha256:
                raise ValueError("复制的闭环冻结文件与 A01 来源 SHA 不一致，拒绝配置输出")
        elif item == "A03":
            rate, temperature, clocks, snapshot = _configure_common_outputs(
                params, devices, channels, self.mapping, self.corrected, self.applied)
            # 只在工作点成功后更换恢复基线，失败的重跑不能采用尚未执行的草稿值。
            self.shutdown_parameters = self.response_parameters = params
            self.executed_source = (params, self.corrected, self.applied)
            self.demod_mode = "response"
            self.state.update(actual_rates={"response_sa_s": rate}, clock_sources=clocks,
                hf2_configuration=snapshot, initial_temperature_c=temperature,
                temperature_control={"target_temperature_c": params.temperature_c, "actual_temperature_c": temperature,
                    "controlled_by_experiment": devices.get("tec") is not None,
                    "control_source": "tec103" if devices.get("tec") is not None else "external_software"})
            self.outputs.update(X="配置值", Z="最优控制ON", **{"Y RF": "等待触发", "温控": "ON", "Pump": "ON", "共同触发": "ON"})
        elif item == "A04":
            self.selected_phase, payload = _acquire_phase_scan(params, self.directory, devices, channels,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]))
            if residual:
                self.state.update(residual_control_phase_scan=payload,
                    selected_control_phase_deg=payload.get("selected_control_phase_deg"),
                    restored_control_phase_deg=params.control_burst_phase_deg,
                    y_rf_output_during_scan="DC_BIAS_ON" if params.y_rf_offset_v != 0 else "DC_ZERO_OUTPUT_OFF")
            else:
                self.state.update(phase_calibration=payload, selected_y_rf_phase_deg=self.selected_phase)
        elif item == "A05" and not residual:
            _acquire_amplitude_scan(params, self.directory, devices, channels,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]), self.selected_phase)
        elif item == "A06" and not residual:
            self.demod_mode = "noise"
            self.state["actual_rates"]["noise_sa_s"] = _acquire_control_noise(params, self.directory, devices, channels,
                str(self.mapping["lockin_r"]["device_id"]), self.selected_phase)
            self.state["noise_rf"] = yaml.safe_load(self.directory.config_path.read_text(encoding="utf-8"))["noise_rf"]
        elif item == "A07":
            if errors := self._shutdown(disconnect_tec=False):
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat(),
                data_files=[path.relative_to(self.directory.root).as_posix() for path in sorted(self.directory.raw.glob("*")) if path.is_file()])
            self.save()
            self.round_sealed = True
            invalidate(self.stages, self.states, "A03")
        self.save()

    def _shutdown(self, *, disconnect_tec=True):
        errors = []
        if self.devices:
            if self.executed_source and self.shutdown_parameters.phase_cal_rf_amplitude_vpp == 0.0:
                try:
                    _rearm_control(self.devices["z_field"], self.channels["z_field"],
                        phase_deg=self.shutdown_parameters.control_burst_phase_deg)
                except Exception as exc:
                    errors.append("恢复 Z 控制相位失败：" + str(exc))
            if self.demod_mode == "noise":
                try:
                    rate = _configure_response_demodulator(self.response_parameters, self.devices["hf2"])
                    self.state.setdefault("actual_rates", {})["restored_response_sa_s"] = rate
                    self.demod_mode = "response"
                except Exception as exc:
                    errors.append("恢复响应解调失败：" + str(exc))
            try:
                report = preparation.safe_shutdown(self.devices, self.channels, self.shutdown_parameters, disconnect_tec=disconnect_tec)
                self.state["safety_shutdown"] = report.to_dict()
                errors.extend(report.errors)
            except Exception as exc:
                errors.append(str(exc))
            main_errors = []
            if self.main_field_state is not None:
                try:
                    restore_main_field_state(self.devices["gs200"], self.main_field_state)
                except Exception as exc:
                    main_errors.append(str(exc))
            self.state["main_field_restore"] = dict(attempted=self.main_field_state is not None, success=not main_errors, errors=main_errors)
            errors.extend(main_errors)
            self.outputs.update(X="OFF" if not errors else "未知", **{"Y RF": "OFF" if not errors else "未知",
                "Z": "保持当前最优控制" if self.executed_source else "未知", "温控": "ON" if not errors else "未知",
                "共同触发": "保持当前状态"})
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        return errors

    def finish(self, failure=None):
        if self.closed:
            return
        errors = list(disconnect_device_mapping({"tec": self.devices.get("tec")})) if self.round_sealed else self._shutdown()
        if self.round_sealed and errors:
            self.state["cleanup_errors"] = list(errors)
        if not self.round_sealed:
            self.state.update(completion_status="cancelled" if isinstance(failure, WorkflowCancelled) else "failed",
                failure_reason=str(failure) if failure else self.state.get("failure_reason") or "本轮采集未完整完成",
                finished_at=datetime.now().isoformat())
            if errors:
                self.state["failure_reason"] += "；安全恢复失败：" + "；".join(errors)
            try:
                self.save()
            except Exception as exc:
                self.state["state_save_error"] = str(exc)
                if failure is None and not errors:
                    errors.append(str(exc))
        self.closed = True
        self.current_step = None
        self.revision += 1
        self.states[FINISH_ID] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
        try:
            self.publish("会话已安全结束")
            report_runtime_progress("finished", "采集会话结束", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))



def run(params, *, project_root=None, workflow_type=Workflow):
    ctx = workflow_type(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ctx.stages if stage.id != FINISH_ID])
        if ctx.quality_error:
            raise ctx.quality_error
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(MxZOptimalControlRFParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
