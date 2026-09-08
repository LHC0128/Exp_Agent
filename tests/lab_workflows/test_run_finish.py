"""安全关断与状态恢复失败处理的测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lab_workflows.common import WorkflowCancelled
from lab_workflows.steps.run_finish import finalize_run_safety
from lab_workflows.steps.safety_shutdown import SafetyShutdownReport


def _report(errors: tuple[str, ...] = ()) -> SafetyShutdownReport:
    return SafetyShutdownReport(
        action_errors=errors,
        disconnect_errors=(),
        preserved_outputs=(),
    )


class TestFinalizeRunSafety:
    def test_completed_with_clean_shutdown_stays_completed(self) -> None:
        finish = finalize_run_safety(
            shutdown=lambda: _report(),
            completion_status="completed",
            failure_reason=None,
        )
        assert finish.completion_status == "completed"
        assert finish.failure_reason is None
        assert finish.cleanup_errors == []
        assert finish.original_exception_pending is False

    def test_completed_with_shutdown_failure_becomes_failed(self) -> None:
        finish = finalize_run_safety(
            shutdown=lambda: _report(("关闭 X 磁场失败",)),
            completion_status="completed",
            failure_reason=None,
        )
        assert finish.completion_status == "failed"
        assert "关闭 X 磁场失败" in finish.failure_reason

    def test_failed_keeps_status_and_merges_cleanup_errors(self) -> None:
        finish = finalize_run_safety(
            shutdown=lambda: _report(("归零 Y RF 失败",)),
            completion_status="failed",
            failure_reason="采集失败: 设备通信错误",
        )
        assert finish.completion_status == "failed"
        assert "采集失败: 设备通信错误" in finish.failure_reason
        assert "归零 Y RF 失败" in finish.failure_reason

    def test_cancelled_keeps_status_and_merges_cleanup_errors(self) -> None:
        finish = finalize_run_safety(
            shutdown=lambda: _report(("恢复温度开关失败",)),
            completion_status="cancelled",
            failure_reason="取消",
        )
        assert finish.completion_status == "cancelled"
        assert "恢复温度开关失败" in finish.failure_reason

    def test_restore_action_errors_are_merged(self) -> None:
        def restore() -> list[str]:
            return ["PZT 写入超时"]

        finish = finalize_run_safety(
            shutdown=lambda: _report(),
            completion_status="completed",
            failure_reason=None,
            extra_restores=(("恢复 DLC pro PZT/扫描状态", restore),),
        )
        assert finish.completion_status == "failed"
        assert "恢复 DLC pro PZT/扫描状态: PZT 写入超时" in finish.failure_reason

    def test_restore_action_exception_is_merged(self) -> None:
        def restore() -> None:
            raise RuntimeError("通信失败")

        finish = finalize_run_safety(
            shutdown=lambda: _report(),
            completion_status="completed",
            failure_reason=None,
            extra_restores=(("光功率基准恢复", restore),),
        )
        assert finish.completion_status == "failed"
        assert "光功率基准恢复: 通信失败" in finish.failure_reason

    def test_shutdown_exception_is_caught_not_propagated(self) -> None:
        def shutdown() -> SafetyShutdownReport:
            raise RuntimeError("关断入口崩溃")

        finish = finalize_run_safety(
            shutdown=shutdown,
            completion_status="failed",
            failure_reason="原始采集异常",
        )
        assert finish.completion_status == "failed"
        assert "原始采集异常" in finish.failure_reason
        assert "安全关断执行失败" in finish.failure_reason

    def test_pending_exception_is_detected(self) -> None:
        try:
            raise RuntimeError("原始异常")
        except RuntimeError:
            finish = finalize_run_safety(
                shutdown=lambda: _report(("清理失败",)),
                completion_status="failed",
                failure_reason="原始异常",
            )
        assert finish.original_exception_pending is True
        assert finish.completion_status == "failed"
        assert "清理失败" in finish.failure_reason


class _FakeRunDir:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw = root / "raw"
        self.results = root / "results"
        self.raw.mkdir(parents=True)
        self.results.mkdir()
        self.config_path = root / "experiment_config.yaml"
        self.config_path.write_text("{}", encoding="utf-8")
        self.updates: list[dict] = []

    def update_config(self, **kwargs) -> None:
        self.updates.append(kwargs)


def _patch_sensitivity_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shutdown_report: SafetyShutdownReport,
):
    import lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow as workflow

    run_dir = _FakeRunDir(tmp_path / "run")
    monkeypatch.setattr(workflow, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        workflow,
        "load_mapping",
        lambda _root: {"lockin_r": {"device_id": "dev"}},
    )
    monkeypatch.setattr(
        workflow,
        "create_run_directory",
        lambda *args, **kwargs: run_dir,
    )
    monkeypatch.setattr(
        workflow,
        "DeviceSession",
        lambda: SimpleNamespace(cleanup_connection_failure=lambda: None),
    )
    monkeypatch.setattr(workflow, "_connect_devices", lambda *args: ({}, {}))
    monkeypatch.setattr(workflow, "_initial_state_snapshot", lambda *args: {})
    monkeypatch.setattr(
        workflow,
        "_configure_outputs",
        lambda *args: (1000.0, {}),
    )
    monkeypatch.setattr(
        workflow,
        "acquire_mx_y_rf_point",
        lambda *args, **kwargs: {
            "actual_response_rate_sa_s": 1000.0,
            "actual_noise_rate_sa_s": 50000.0,
            "frequency_gate_fit": None,
        },
    )
    monkeypatch.setattr(
        workflow,
        "safe_shutdown",
        lambda *args: shutdown_report,
    )
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    return workflow, run_dir


def _params():
    from lab_workflows.experiment_modules.mx_y_rf_sensitivity.models import (
        MxYRFParams,
    )

    return MxYRFParams()


class TestSensitivityRunFinish:
    def test_completed_with_shutdown_failure_raises_and_records_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        workflow, run_dir = _patch_sensitivity_run(
            monkeypatch,
            tmp_path,
            _report(("归零 Y RF 失败",)),
        )
        with pytest.raises(RuntimeError, match="安全恢复失败"):
            workflow.run(_params())
        assert run_dir.updates[-1]["completion_status"] == "failed"
        assert "归零 Y RF 失败" in run_dir.updates[-1]["failure_reason"]
        assert run_dir.updates[-1]["safety_shutdown"]["completed"] is False

    def test_completed_with_clean_shutdown_keeps_completed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        workflow, run_dir = _patch_sensitivity_run(
            monkeypatch,
            tmp_path,
            _report(),
        )
        assert workflow.run(_params()) == run_dir.root
        assert run_dir.updates[-1]["completion_status"] == "completed"
        assert run_dir.updates[-1]["failure_reason"] is None
        assert run_dir.updates[-1]["safety_shutdown"]["completed"] is True

    def test_acquisition_error_keeps_original_and_merges_cleanup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        workflow, run_dir = _patch_sensitivity_run(
            monkeypatch,
            tmp_path,
            _report(("关闭 X 磁场失败",)),
        )
        monkeypatch.setattr(
            workflow,
            "acquire_mx_y_rf_point",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("采集设备通信失败")
            ),
        )
        with pytest.raises(RuntimeError, match="采集设备通信失败"):
            workflow.run(_params())
        last = run_dir.updates[-1]
        assert last["completion_status"] == "failed"
        assert "采集设备通信失败" in last["failure_reason"]
        assert "关闭 X 磁场失败" in last["failure_reason"]

    def test_cancel_keeps_original_and_merges_cleanup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        workflow, run_dir = _patch_sensitivity_run(
            monkeypatch,
            tmp_path,
            _report(("恢复温度开关失败",)),
        )
        monkeypatch.setattr(
            workflow,
            "acquire_mx_y_rf_point",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                WorkflowCancelled("用户取消")
            ),
        )
        with pytest.raises(WorkflowCancelled):
            workflow.run(_params())
        last = run_dir.updates[-1]
        assert last["completion_status"] == "cancelled"
        assert "用户取消" in last["failure_reason"]
        assert "恢复温度开关失败" in last["failure_reason"]


def _patch_frequency_response_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shutdown_report: SafetyShutdownReport,
):
    import lab_workflows.experiment_modules.mx_y_rf_frequency_response.workflow as workflow

    run_dir = _FakeRunDir(tmp_path / "run")
    monkeypatch.setattr(workflow, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        workflow,
        "load_mapping",
        lambda _root: {"lockin_r": {"device_id": "dev"}},
    )
    monkeypatch.setattr(
        workflow,
        "create_run_directory",
        lambda *args, **kwargs: run_dir,
    )
    monkeypatch.setattr(
        workflow,
        "DeviceSession",
        lambda: SimpleNamespace(cleanup_connection_failure=lambda: None),
    )
    monkeypatch.setattr(
        workflow,
        "connect_mx_y_rf_devices",
        lambda *args, **kwargs: ({}, {}),
    )
    monkeypatch.setattr(workflow, "snapshot_mx_y_rf_state", lambda *args: {})
    monkeypatch.setattr(
        workflow,
        "configure_mx_y_rf_outputs",
        lambda *args, **kwargs: (1000.0, {}),
    )
    monkeypatch.setattr(workflow, "_acquire_frequency_response", lambda *args: None)
    monkeypatch.setattr(
        workflow,
        "safe_shutdown",
        lambda *args: shutdown_report,
    )
    monkeypatch.setattr(workflow, "check_cancelled", lambda: None)
    return workflow, run_dir


class TestFrequencyResponseRunFinish:
    def test_completed_with_shutdown_failure_raises_and_records_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from lab_workflows.experiment_modules.mx_y_rf_frequency_response.models import (
            MxYRFFrequencyResponseParams,
        )

        workflow, run_dir = _patch_frequency_response_run(
            monkeypatch,
            tmp_path,
            _report(("归零 Z 辅助场失败",)),
        )
        with pytest.raises(RuntimeError, match="安全恢复失败"):
            workflow.run(MxYRFFrequencyResponseParams())
        last = run_dir.updates[-1]
        assert last["completion_status"] == "failed"
        assert "归零 Z 辅助场失败" in last["failure_reason"]
        assert last["safety_shutdown"]["completed"] is False
