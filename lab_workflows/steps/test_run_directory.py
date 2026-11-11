"""同秒重跑和并发运行不得复用已有原始目录。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
import yaml

from . import run_directory
from ..wiring import WiringGraph, read_wiring_revision
from ..setups import SETUP_ENV_VAR, bound_setup, create_setup, load_setup_registry


def write_wiring(root, targets):
    params = root / "params"
    params.mkdir(exist_ok=True)
    empty = {"nodes": [], "edges": []}
    document = {"schema_version": 1, "site": empty, "targets": {key: empty for key in targets}}
    (params / "wiring.yaml").write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")


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
        device_library={}, physical_mappings={}, setup_id="default", setup_label="默认装置",
    ))
    return tmp_path


@pytest.mark.parametrize("setup_id, setup_label", [("default", "默认装置"), ("mag_b", "磁力仪 B")])
def test_run_config_records_effective_setup(tmp_path, monkeypatch, setup_id, setup_label):
    monkeypatch.delenv(SETUP_ENV_VAR, raising=False)
    params = tmp_path / "params"
    params.mkdir()
    (params / "mapping.yaml").write_text("mapping: {}\n", encoding="utf-8")
    (params / "safety_limits.yaml").write_text("limits: {}\n", encoding="utf-8")
    if setup_id != "default":
        create_setup(setup_label, copy_from="default", setup_id=setup_id,
            base_revision=load_setup_registry(tmp_path).revision, root=tmp_path)
        with bound_setup(setup_id):
            run = run_directory.create_run_directory("Example", "bound", {}, project_root=tmp_path)
    else:
        run = run_directory.create_run_directory("Example", "default", {}, project_root=tmp_path)
        assert not (params / "setups.yaml").exists()
    config = yaml.safe_load(run.config_path.read_text(encoding="utf-8"))
    assert (config["setup_id"], config["setup_label"]) == (setup_id, setup_label)
    assert config["mapping_snapshot"] == {}
    updated = run.update_config(completion_status="completed")
    assert (updated["setup_id"], updated["setup_label"]) == (setup_id, setup_label)


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
    """只执行真实入口的目录初始化语句，不导入会连接仪器的模块。"""
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    source = Path(__file__).resolve().parents[1] / "experiment_modules" / module / "workflow.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [item for target in node.targets for item in (
            target.elts if isinstance(target, ast.Tuple) else [target])]
        values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
        for target, value in zip(targets, values):
            if isinstance(target, ast.Name):
                constants[target.id] = ast.literal_eval(value)
    data_type = constants["DATA_TYPE"]
    initialize = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "create_run_directory")
    previous = run_directory.create_run_directory(data_type, "repeat", {"index": 1}, project_root=frozen_directory)
    (previous.raw / "record.txt").write_text("historical", encoding="utf-8")
    before = {path: path.read_bytes() for path in previous.root.rglob("*") if path.is_file()}
    owner = SimpleNamespace(
        params=SimpleNamespace(schema_version=1, run_tag="repeat", to_external=lambda: {"index": 2}),
        root=frozen_directory,
    )
    context = dict(create_run_directory=run_directory.create_run_directory, DATA_TYPE=data_type, self=owner)
    exec(compile(ast.Module(body=[initialize], type_ignores=[]), str(source), "exec"), context)
    current = owner.directory
    assert current.root != previous.root and current.raw.is_dir()
    assert yaml.safe_load(current.config_path.read_text(encoding="utf-8"))["parameters"] == {"index": 2}
    assert {path: path.read_bytes() for path in before} == before


def test_run_config_records_wiring_snapshot_and_matching_revision(frozen_directory):
    write_wiring(frozen_directory, ["Example", "another-experiment"])
    expected_revision = read_wiring_revision(frozen_directory).revision
    run = run_directory.create_run_directory("Example", "wired", {}, project_root=frozen_directory)
    config = yaml.safe_load(run.config_path.read_text(encoding="utf-8"))
    assert config["wiring_revision"] == expected_revision
    assert config["wiring_snapshot_error"] is None
    assert set(config["wiring_snapshot"]) == {"schema_version", "site", "targets"}
    assert config["wiring_snapshot"]["schema_version"] == 1
    assert WiringGraph.model_validate(config["wiring_snapshot"]["site"]) == WiringGraph(nodes=[], edges=[])
    assert set(config["wiring_snapshot"]["targets"]) == {"Example", "another-experiment"}
    for graph in config["wiring_snapshot"]["targets"].values():
        assert WiringGraph.model_validate(graph) == WiringGraph(nodes=[], edges=[])


def test_wiring_changes_after_creation_do_not_rewrite_recorded_snapshot(frozen_directory):
    write_wiring(frozen_directory, ["Example", "another-experiment"])
    run = run_directory.create_run_directory("Example", "frozen", {}, project_root=frozen_directory)
    before = run.config_path.read_bytes()
    write_wiring(frozen_directory, ["Example"])
    assert run.config_path.read_bytes() == before
    config = yaml.safe_load(run.config_path.read_text(encoding="utf-8"))
    assert set(config["wiring_snapshot"]["targets"]) == {"Example", "another-experiment"}


def test_missing_wiring_is_recorded_as_empty_graph(frozen_directory):
    run = run_directory.create_run_directory("Example", "empty-wiring", {}, project_root=frozen_directory)
    config = yaml.safe_load(run.config_path.read_text(encoding="utf-8"))
    assert config["wiring_revision"] == read_wiring_revision(frozen_directory).revision
    assert config["wiring_snapshot"] == {
        "schema_version": 1, "site": {"nodes": [], "edges": []}, "targets": {},
    }
    assert config["wiring_snapshot_error"] is None


def test_corrupt_wiring_does_not_block_creation(frozen_directory):
    params = frozen_directory / "params"
    params.mkdir()
    (params / "wiring.yaml").write_text("schema_version: 1\nsite: not-a-graph\ntargets: {}\n", encoding="utf-8")
    run = run_directory.create_run_directory("Example", "corrupt-wiring", {}, project_root=frozen_directory)
    config = yaml.safe_load(run.config_path.read_text(encoding="utf-8"))
    assert config["wiring_revision"] is None
    assert config["wiring_snapshot"] is None
    assert "接线图" in config["wiring_snapshot_error"]


def test_creation_never_reads_or_rewrites_historical_run(frozen_directory):
    historical = frozen_directory / "data" / "Example" / "1001_000000_old"
    historical.mkdir(parents=True)
    (historical / "raw").mkdir()
    old_config = "experiment_type: Example\ntimestamp: 1001_000000\nparameters: {}\n"
    (historical / "experiment_config.yaml").write_text(old_config, encoding="utf-8")
    before = {path: path.read_bytes() for path in historical.rglob("*") if path.is_file()}
    run_directory.create_run_directory("Example", "new", {}, project_root=frozen_directory)
    assert {path: path.read_bytes() for path in historical.rglob("*") if path.is_file()} == before
    assert "wiring_snapshot" not in yaml.safe_load(old_config)
