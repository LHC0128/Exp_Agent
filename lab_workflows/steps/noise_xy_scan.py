"""噪声谱（XY 正弦族）共享扫描会话引擎：生命周期、快照、发布与安全结束。

三个噪声谱实验（XY 正弦、已知可控噪声、Probe AM）共用同一套会话语义。本族与
封存型 / 模块型骨架实验的差异固定为四条，迁移时原样保留：

1. 没有轮次配置快照：会话本身不持有 ``state`` / ``directory``，也不在节点后写配置；
   运行目录在 A04 节点内由实验的 ``acquisition.initialize_run_directory`` 惰性创建，
   扫描时再按 ``for_scan=True`` 换新目录，配置由实验自己写盘。
2. ``finish()`` 保留「本轮未开始扫描则把初始配置回写为失败」的分支。
3. 质量失败由节点内部改写 ``states[item]`` 后由执行循环 ``break``；节点内写入的
   终态与产物不得被父类的模块型 ``after_step`` 覆盖。
4. 目录轮换固定不参与（``_needs_new_directory()`` 返回 False），A01–A03 失败时
   不产生运行目录。

硬件节点动作（``_execute_step`` / ``shutdown_outputs`` / ``restore_demod``）留在
实验模块；本模块只依赖 ``lab_workflows``，不得 import ``experiment_modules``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import yaml

from ..common import data_root
from ..experiment_runtime import check_cancelled, report_runtime_progress
from ..staged_workflow import StagedWorkflow
from ..workflow_stages import initial_states
from ..workflow_state import write_state
from .run_directory import write_run_config
from .safety_shutdown import disconnect_device_mapping


class NoiseXYScan(StagedWorkflow):
    """噪声谱扫描会话。子类必填：``acquisition_stages``、``finish_id``、``data_type``、
    ``output_keys``、``preflight``、``validate_selection``、``invalidate``、
    ``_execute_step``、``shutdown_outputs``、``restore_demod``。``self.outputs`` 由本类
    ``__init__`` 按 ``output_keys`` 建立，子类不再自行建立或补键。

    与 ``StagedWorkflow`` 共用配置修订、阶段表与联锁常量；本类自带执行循环与结束
    路径，不调用父类 ``__init__``（本族没有 ``state`` / ``directory`` / 轮次封存）。
    """

    acquisition_stages: ClassVar[tuple[Any, ...]] = ()
    finish_id: ClassVar[str]
    data_type: ClassVar[str]
    experiment_id: ClassVar[str]
    always_shutdown: ClassVar[bool] = False
    output_keys: ClassVar[tuple[str, ...]] = ()

    # ---- 子类实现 ----
    def preflight(self, params, root):
        """无硬件预检，返回 (params, mapping, limits, axes)。"""
        raise NotImplementedError

    def validate_selection(self, stages, states, selected):
        """选择校验与依赖排序；默认拒绝，由实验的阶段模块提供。"""
        raise NotImplementedError

    def invalidate(self, stages, states, stage_id):
        """前置重跑后的失效传播；默认拒绝，由实验的阶段模块提供。"""
        raise NotImplementedError

    def _execute_step(self, item: str) -> None:
        """执行一个采集节点（含 A01 的复检；不含结束节点）。"""
        raise NotImplementedError

    def shutdown_outputs(self):
        """安全收尾输出，返回带 ``completed`` / ``errors`` / ``to_dict()`` 的报告。"""
        raise NotImplementedError

    def restore_demod(self, params) -> None:
        """把锁相解调器恢复到已校相配置（结束时会话用校相当时的参数）。"""
        raise NotImplementedError

    # ---- 生命周期 ----
    def __init__(self, params, *, project_root=None, session_id=None):
        # 延迟导入，使测试与模块入口能按 lab_workflows.common.find_project_root 打桩。
        from ..common import find_project_root

        self.root = Path(project_root).resolve() if project_root else find_project_root(Path(__file__).parent)
        self.params, self.mapping, self.limits, self.axes = self._preflight_state(params)
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.acquisition_stages)
        self.devices = {}
        self.connection = None
        self.clock_sources = {}
        self.calibrated_phase = None
        self.phase_parameters = None
        self.xy_result = None
        self.daq_settings = None
        self.demod_mode = "unknown"
        self.run_dir = None
        self.current_step = None
        self.closed = False
        self.shutdown = None
        self.clock_failed = False
        self.outputs_started = False
        # 输出键由子类 ``output_keys`` 声明；取值由各节点按实际写入更新。
        self.outputs = {key: "未知" for key in self.output_keys}

    def _preflight_state(self, params):
        """噪声预检返回四元组；父类只取前两项，这里保留安全限值与扫描轴。"""
        current, mapping, limits, axes = self.preflight(params, self.root)
        return current, mapping, limits, axes

    @property
    def stages(self):
        """骨架用 ``stages`` 指采集清单；本族沿用既有公开名 ``acquisition_stages``。"""
        return self.acquisition_stages

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping, self.limits, self.axes = self._preflight_state(self.params)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=self.states, current_step=self.current_step,
            run_id=self.run_dir.name if self.run_dir else None, outputs=self.outputs,
            output_source=self.output_source, demod_mode=self.demod_mode,
            xy_phase_calibration=(dict(converged=self.xy_result.converged,
                final_phase_deg=self.xy_result.final_phase_deg,
                history=[item.to_dict() for item in self.xy_result.history]) if self.xy_result else None),
            safety_shutdown=self.shutdown, clock_sources=self.clock_sources,
            demod_phase_deg=self.calibrated_phase,
            daq_settings=(dict(actual_rate_sa_s=self.daq_settings[0], grid_cols=self.daq_settings[1],
                **self.daq_settings[2]) if self.daq_settings else None))

    def publish(self, message="等待选择下一模块", level="info"):
        snapshot = self.snapshot()
        write_state(data_root(self.root) / ".gui_jobs" / self.session_id / "session.json", snapshot)
        if self.run_dir:
            write_state(self.run_dir / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message,
            data={"workflow_session": snapshot, "level": level})

    def update_parameters(self, values):
        self.recheck()
        merged = self.params.to_external() | values
        unknown = set(values) - set(self.params.to_external())
        if unknown:
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        new, mapping, limits, axes = self._preflight_state(type(self.params).from_external(merged))
        changed = {key for key, value in new.to_external().items() if value != self.params.to_external()[key]}
        for stage in self.acquisition_stages:
            if changed.intersection(stage.parameters):
                self.invalidate(self.acquisition_stages, self.states, stage.id)
        self.params, self.mapping, self.limits, self.axes = new, mapping, limits, axes
        self.revision += 1
        self.publish("参数已更新，受影响模块需要重新执行")

    # ---- 骨架钩子 ----
    def _needs_new_directory(self, selected):
        """本族运行目录只在 A04 节点内惰性创建：骨架的目录轮换分支不参与。"""
        return False

    def after_step(self, item: str) -> None:
        """父类的模块型钩子会把 completion_status 置 completed；本族没有轮次快照。

        节点内声明的 ``quality_failed`` 与节点写入的 ``artifacts`` 由执行循环保留，
        这里不再写任何运行状态。
        """

    # ---- 执行与结束 ----
    def execute(self, selected):
        selected = self.validate_selection(self.acquisition_stages, self.states, selected)
        if selected != [self.finish_id]:
            self.recheck()
        if self._needs_new_directory(selected):
            self._new_directory(selected)
        self.revision += 1
        for item in selected:
            if item == self.finish_id:
                self.finish()
                break
            check_cancelled()
            if item != self.preflight_id:
                self.invalidate(self.acquisition_stages, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            try:
                self.publish(next(stage.title for stage in self.acquisition_stages if stage.id == item))
                self._execute_step(item)
                # 节点内可声明终态（如 quality_failed）或写 artifacts：只改状态字段，
                # 不重建状态字典，避免覆盖节点自己的写入。
                if self.states[item]["status"] == "running":
                    self.states[item]["status"] = "completed"
                self.after_step(item)
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

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if failure is None and self.demod_mode == "daq" and self.calibrated_phase is not None:
            try:
                self.restore_demod(self.phase_parameters or self.params)
                self.demod_mode = "phase"
            except Exception as exc:
                errors.append(str(exc))
        try:
            if self.devices and (not self.clock_failed or self.outputs_started or self.always_shutdown):
                report = self.shutdown_outputs()
                self.shutdown = report.to_dict()
                errors.extend(report.errors)
                if report.completed:
                    self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON")
                else:
                    self.outputs.update(X="未知", Y="未知", Z="未知", 温控="未知")
            if self.devices and self.clock_failed:
                disconnect_device_mapping(self.devices)
        except Exception as exc:
            errors.append(str(exc))
        finally:
            self.closed = True
            self.current_step = None
            self.revision += 1
            if self.run_dir and not getattr(self, "scan_started", False):
                try:
                    path = self.run_dir / "experiment_config.yaml"
                    config = yaml.safe_load(path.read_text(encoding="utf-8"))
                    config.update(completion_status="failed", failure_reason=str(failure or "本轮未开始扫描"))
                    write_run_config(path, config)
                except Exception as exc:
                    errors.append(f"初始配置收尾保存失败：{exc}")
            self.states[self.finish_id] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
            try:
                self.publish(self.finished_message + ("；" + "；".join(errors) if errors else ""))
            except Exception:
                if failure is None:
                    raise
        if errors and failure is None:
            raise RuntimeError("安全收尾未全部完成：" + "；".join(errors))

    @classmethod
    def run(cls, params, *, project_root=None) -> Path:
        """拒绝继承骨架的整轮入口：各实验模块级 run() 还要按本实验校相节点过质量门。"""
        raise NotImplementedError("请使用实验模块的 run()：结束时还要检查本实验的校相质量门")
