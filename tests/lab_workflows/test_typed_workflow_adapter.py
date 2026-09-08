from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from lab_workflows.common import CancellationToken, WorkflowCancelled

from lab_workflows.experiments.typed import TypedWorkflowAdapter


def _adapter(tmp_path: Path) -> TypedWorkflowAdapter:
    adapter = TypedWorkflowAdapter.__new__(TypedWorkflowAdapter)
    adapter.root = tmp_path
    adapter.id = "test-experiment"
    adapter.data_type = "Test_Experiment"
    adapter.acquisition_module = "test.acquisition"
    adapter.analysis_module = "test.analysis"
    adapter._resolved = lambda values: SimpleNamespace(
        validate=lambda root: [],
        to_external=lambda: {},
    )
    return adapter


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "data" / "Test_Experiment" / "0824_120000_test"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "results").mkdir()
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump({"completion_status": "completed"}),
        encoding="utf-8",
    )
    return run_dir


def test_automatic_analysis_failure_fails_job_and_records_status(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    run_dir = _run_dir(tmp_path)
    def execute(module: str, *args, **kwargs) -> None:
        if module == adapter.analysis_module:
            raise ValueError("结果目录不可写")
    adapter._latest_run = lambda before: run_dir
    adapter._execute = execute

    with pytest.raises(RuntimeError, match="自动分析失败.*结果目录不可写"):
        adapter.run({}, None, None)

    config = yaml.safe_load(
        (run_dir / "experiment_config.yaml").read_text(encoding="utf-8")
    )
    assert config["analysis_status"] == "failed"
    assert config["analysis_error"] == "结果目录不可写"


def test_automatic_analysis_requires_a_run_directory(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter._execute = lambda *args, **kwargs: None
    adapter._latest_run = lambda before: None

    with pytest.raises(RuntimeError, match="未找到 Test_Experiment 运行目录"):
        adapter.run({}, None, None)


def test_automatic_analysis_records_completed_status(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    run_dir = _run_dir(tmp_path)
    adapter._execute = lambda *args, **kwargs: None
    adapter._latest_run = lambda before: run_dir

    result = adapter.run({}, None, None)

    assert result["run_dir"] == str(run_dir)
    config = yaml.safe_load(
        (run_dir / "experiment_config.yaml").read_text(encoding="utf-8")
    )
    assert config["analysis_status"] == "completed"
    assert config["analysis_error"] is None


def test_automatic_analysis_receives_cancellation_and_records_cancelled(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    run_dir = _run_dir(tmp_path)
    token = CancellationToken()

    def execute(module, **kwargs):
        if module == adapter.analysis_module:
            assert kwargs["cancellation"] is token
            raise WorkflowCancelled("取消分析")

    adapter._execute = execute
    adapter._latest_run = lambda before: run_dir
    with pytest.raises(WorkflowCancelled, match="取消分析"):
        adapter.run({}, None, token)
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["analysis_status"] == "cancelled"
    assert config["completion_status"] == "completed"
