# %% [markdown]
# # 探测链路频率响应标定：共享模块编排与分步会话
# 导入只声明接口；硬件连接只发生在执行模块时。

# %% 导入
from pathlib import Path
from uuid import uuid4

from ...common import find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import (
    DGChannelShutdown,
    DisconnectTarget,
    TemperatureSwitchRestore,
    run_safety_shutdown,
)
from .models import DetectionChainFrequencyResponseParams
from . import preparation, acquisition
from .stages import ACQUISITION, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states
from lab_workflows.workflow_state import write_state


class Workflow:
    """一个子进程持有一个设备会话；阶段状态不等同于历史数据。"""

    acquisition_stages = ACQUISITION
    finish_id = "A08"
    preflight = staticmethod(preparation.preflight)
    invalidate = staticmethod(invalidate)
    validate_selection = staticmethod(validate_selection)
    always_shutdown = False

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root(Path(__file__).parent)
        self.params, self.mapping, self.limits = self.preflight(params, self.root)
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.acquisition_stages)
        self.devices = {}
        self.channels = {}
        self.connection = None
        self.clock_sources = {}
        self.temperature_control = None
        self.tec_connection_error = None
        self.hf2_configuration = None
        self.actual_rate = None
        self.run_dir = None
        self.run_directory = None
        self.current_step = None
        self.closed = False
        self.outputs = {key: "未知" for key in ("AOM 载波", "AOM AM", "温控", "Pump 光", "Probe 光")}
        self.shutdown = None
        self.clock_failed = False
        self.outputs_started = False

    def _configuration_revision(self):
        snapshot = instrument_config_snapshot(self.root)
        return (snapshot["device_library_revision"], snapshot["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=self.states, current_step=self.current_step,
            run_id=self.run_dir.name if self.run_dir else None, outputs=self.outputs,
            output_source="最近成功模块的写入结果；未经额外回读",
            demod_mode="acquisition" if self.actual_rate else "unknown",
            safety_shutdown=self.shutdown, clock_sources=self.clock_sources,
            daq_settings=dict(actual_rate_sa_s=self.actual_rate) if self.actual_rate else None)

    def publish(self, message="等待选择下一模块", level="info"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.run_dir:
            write_state(self.run_dir / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message,
            data={"workflow_session": snapshot, "level": level})

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping, self.limits = self.preflight(self.params, self.root)

    def update_parameters(self, values):
        self.recheck()
        merged = self.params.to_external() | values
        unknown = set(values) - set(self.params.to_external())
        if unknown:
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        new, mapping, limits = self.preflight(type(self.params).from_external(merged), self.root)
        changed = {key for key, value in new.to_external().items() if value != self.params.to_external()[key]}
        for stage in self.acquisition_stages:
            if changed.intersection(stage.parameters):
                self.invalidate(self.acquisition_stages, self.states, stage.id)
        self.params, self.mapping, self.limits = new, mapping, limits
        self.revision += 1
        self.publish("参数已更新，受影响模块需要重新执行")

    def execute(self, selected):
        selected = self.validate_selection(self.acquisition_stages, self.states, selected)
        if selected != [self.finish_id]:
            self.recheck()
        self.revision += 1
        for item in selected:
            if item == self.finish_id:
                self.finish()
                break
            check_cancelled()
            if item != "A01":
                self.invalidate(self.acquisition_stages, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            try:
                self.publish(next(stage.title for stage in self.acquisition_stages if stage.id == item))
                self._execute_step(item)
                if self.states[item]["status"] == "running":
                    self.states[item]["status"] = "completed"
            except BaseException as exc:
                self.states[item] = {"status": "failed", "error": str(exc)}
                self.outputs = {key: "未知" for key in self.outputs}
                try:
                    self.publish(str(exc), "error")
                except Exception:
                    pass
                raise
            finally:
                self.current_step = None
            self.publish(f"{item} {self.states[item]['status']}")
            if self.states[item]["status"] != "completed":
                break
        return self.snapshot()

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not self.devices:
                self.devices, self.connection, self.channels = preparation.connect_instruments(self.mapping)
        elif item == "A03":
            self.clock_failed = False
            try:
                self.clock_sources = preparation.synchronize_devices(self.devices, self.mapping)
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            self.outputs_started = True
            self.temperature_control, self.tec_connection_error = preparation.configure_initial_outputs(
                self.devices, self.channels, self.params, self.mapping, self.connection)
            self.outputs.update({"温控": "ON", "Pump 光": "ON", "Probe 光": "ON"})
        elif item == "A05":
            self.actual_rate, self.hf2_configuration = acquisition.configure_hf2(self.devices, self.params)
        elif item == "A06":
            acquisition.configure_aom_carrier(self.devices, self.channels, self.params)
            self.outputs.update({"AOM 载波": "AM·EXT", "AOM AM": "OFF"})
        elif item == "A07":
            if self.actual_rate is None:
                raise ValueError("HF2 尚未配置，请先执行 A05 HF2 解调配置")
            acquisition.acquire_scan(self)
            self.states[item]["artifacts"] = [
                "experiment_config.yaml", "raw/frequency_response.npz", "results/results_summary.yaml",
                *[str(path.relative_to(self.run_dir)).replace("\\", "/")
                    for path in sorted((self.run_dir / "raw").glob("waveform_*.npy"))]]

    def shutdown_outputs(self):
        aom = self.devices.get("aom")
        return run_safety_shutdown(
            dg_channels=(
                DGChannelShutdown(aom, self.channels["carrier"], "Probe_AOM_Carrier", "Probe AOM 载波"),
                DGChannelShutdown(aom, self.channels["am"], "Probe_AOM_AM", "Probe AOM AM"),
            ) if aom is not None else (),
            temperature_switch=TemperatureSwitchRestore(
                self.devices["temp_switch"], self.channels["temp_switch"]),
            disconnect_targets=(DisconnectTarget("TEC", self.devices["tec"]),),
        )

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        try:
            if self.devices and (not self.clock_failed or self.outputs_started or self.always_shutdown):
                report = self.shutdown_outputs()
                self.shutdown = report.to_dict()
                errors.extend(report.errors)
                if report.completed:
                    # 温度开关恢复为 5 V ON；Pump/Probe 直流输出保持既有状态。
                    self.outputs.update({"AOM 载波": "OFF", "AOM AM": "OFF", "温控": "ON"})
                else:
                    self.outputs.update({key: "未知" for key in self.outputs})
            if self.devices and self.clock_failed:
                self.connection.cleanup_connection_failure()
        except Exception as exc:
            errors.append(str(exc))
        finally:
            self.closed = True
            self.current_step = None
            self.revision += 1
            if self.run_directory is not None:
                try:
                    self.run_directory.update_config(safety_shutdown=self.shutdown)
                except OSError as exc:
                    errors.append(f"写入安全收尾记录失败：{exc}")
            self.states[self.finish_id] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
            try:
                self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
            except Exception:
                if failure is None:
                    raise
        if errors and failure is None:
            raise RuntimeError("安全收尾未全部完成：" + "；".join(errors))


# %% 运行入口
def run(params: DetectionChainFrequencyResponseParams, *, project_root: Path | None = None) -> Path:
    """完整采集和分步会话使用相同模块；直接调用会自动结束。"""
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != "A08"])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(DetectionChainFrequencyResponseParams))


if __name__ == "__main__":
    main()
