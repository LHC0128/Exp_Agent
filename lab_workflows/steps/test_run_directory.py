"""同秒重跑和并发运行不得复用已有原始目录。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
import yaml

from . import run_directory


@pytest.fixture
def frozen_directory(monkeypatch, tmp_path):
    class Clock:
        @staticmethod
        def now():
            return datetime(2026, 10, 14, 12, 0, 0)

    monkeypatch.setattr(run_directory, "datetime", Clock)
    monkeypatch.setattr(run_directory, "load_safety_limits", lambda root: {})
    monkeypatch.setattr(run_directory, "instrument_config_snapshot", lambda root: dict(
        resolved_mapping={}, device_library_revision="library", physical_mapping_revision="mapping",
        device_library={}, physical_mappings={},
    ))
    return tmp_path


def test_concurrent_same_second_runs_preserve_parameters_and_unique_roots(frozen_directory):
    def create(index):
        return run_directory.create_run_directory("Example", "重复", {"index": index}, project_root=frozen_directory)

    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(create, range(8)))
    assert len({run.root for run in runs}) == 8
    for index, run in enumerate(runs):
        config = yaml.safe_load(run.config_path.read_text(encoding="utf-8"))
        assert config["parameters"] == {"index": index}
        assert config["run_tag"] == "重复"
        assert run.raw.is_dir() and run.results.is_dir()


def test_existing_incomplete_run_without_raw_is_never_reused(frozen_directory):
    existing = frozen_directory / "data" / "Example" / "1014_120000_repeat"
    existing.mkdir(parents=True)
    (existing / "experiment_config.yaml").write_text("original", encoding="utf-8")
    result = run_directory.create_run_directory("Example", "repeat", {}, project_root=frozen_directory)
    assert result.root == existing.with_name(existing.name + "_2")
    assert (existing / "experiment_config.yaml").read_text(encoding="utf-8") == "original"
    assert not (existing / "raw").exists()


@pytest.mark.parametrize("tag", ["../outside", "a\\outside", "a:outside", "a\x00b"])
def test_run_tag_cannot_escape_directory(frozen_directory, tag):
    with pytest.raises(ValueError, match="路径分隔符"):
        run_directory.create_run_directory("Example", tag, {}, project_root=frozen_directory)
    assert not (frozen_directory / "data").exists()


@pytest.mark.parametrize("fault", ["serialization", "write", "replace"])
def test_atomic_update_failure_keeps_original_bytes_and_cleans_temp(frozen_directory, monkeypatch, fault):
    from pathlib import Path
    run = run_directory.create_run_directory("Example", "original", {"温度": 100}, project_root=frozen_directory)
    before = run.config_path.read_bytes()
    updates = {"completion_status": "completed"}
    if fault == "serialization":
        updates["unsupported"] = object()
    elif fault == "write":
        def fail(config, stream, **kwargs):
            stream.write("partial")
            raise OSError("磁盘写入失败")
        monkeypatch.setattr(run_directory.yaml, "safe_dump", fail)
    else:
        def fail(*args):
            raise OSError("原子替换失败")
        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises((OSError, yaml.representer.RepresenterError)):
        run.update_config(**updates)
    assert run.config_path.read_bytes() == before
    assert not list(run.root.glob("*.tmp"))
    assert not list(run.root.glob(".*.tmp"))


def test_atomic_update_retries_windows_reader_and_preserves_fields(frozen_directory, monkeypatch):
    from pathlib import Path
    run = run_directory.create_run_directory("Example", "原始", {"index": 1}, project_root=frozen_directory)
    original, attempts = Path.replace, []
    def replace(path, target):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError("文件短暂占用")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    result = run.update_config(completion_status="completed")
    assert len(attempts) == 2
    assert result["parameters"] == {"index": 1} and result["run_tag"] == "原始"
    assert yaml.safe_load(run.config_path.read_text(encoding="utf-8")) == result


def test_creation_reports_only_after_initial_config_is_saved(frozen_directory, monkeypatch):
    reports = []
    def report(path, data_type):
        assert data_type == "Example" and (path / "raw").is_dir()
        config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert config["parameters"] == {"index": 1}
        reports.append(path)
    monkeypatch.setattr(run_directory, "report_run_directory", report)
    result = run_directory.create_run_directory("Example", "current", {"index": 1}, project_root=frozen_directory)
    assert reports == [result.root]
    def fail(*args):
        raise OSError("初始配置保存失败")
    monkeypatch.setattr(run_directory, "write_run_config", fail)
    with pytest.raises(OSError, match="初始配置"):
        run_directory.create_run_directory("Example", "failed", {}, project_root=frozen_directory)
    assert reports == [result.root]


@pytest.mark.parametrize("content", ["- invalid\n", "[]\n", "false\n", "0\n"])
def test_non_mapping_config_is_not_overwritten(frozen_directory, content):
    run = run_directory.create_run_directory("Example", "invalid", {}, project_root=frozen_directory)
    run.config_path.write_text(content, encoding="utf-8")
    before = run.config_path.read_bytes()
    with pytest.raises(ValueError, match="YAML 对象"):
        run.update_config(completion_status="completed")
    assert run.config_path.read_bytes() == before


@pytest.mark.parametrize("module", ["rf_sensitivity_direct_aw_frequency", "xy_direct_aw_dc_calibration"])
def test_legacy_typed_directory_initialization_never_reuses_history(frozen_directory, module):
    """只执行真实入口的目录初始化语句，不导入会连接仪器的旧形态模块。"""
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    source = Path(__file__).resolve().parents[1] / "experiment_modules" / module / "workflow.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    data_type = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "EXPERIMENT_TYPE" for target in node.targets))
    initialize = next(node for node in tree.body if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "create_run_directory")
    previous = run_directory.create_run_directory(data_type, "repeat", {"index": 1}, project_root=frozen_directory)
    (previous.raw / "record.txt").write_text("historical", encoding="utf-8")
    before = {path: path.read_bytes() for path in previous.root.rglob("*") if path.is_file()}
    context = dict(create_run_directory=run_directory.create_run_directory, EXPERIMENT_TYPE=data_type,
        RUN_TAG="repeat", PARAMS=SimpleNamespace(schema_version=1, to_external=lambda: {"index": 2}),
        project_root=frozen_directory)
    exec(compile(ast.Module(body=[initialize], type_ignores=[]), str(source), "exec"), context)
    current = context["directory"]
    assert current.root != previous.root and current.raw.is_dir()
    assert yaml.safe_load(current.config_path.read_text(encoding="utf-8"))["parameters"] == {"index": 2}
    assert {path: path.read_bytes() for path in before} == before
