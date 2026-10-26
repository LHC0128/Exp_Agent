"""临时运行目录中的改名、星标、删除及分析兼容回归。"""

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from . import main, run_management
from .test_rf_sensitivity_direct_aw import client, create_run
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.run_format import DATA_TYPE, EXPERIMENT_ID
from lab_workflows.workflow_state import write_state

BASE = f"/api/experiments/{EXPERIMENT_ID}"


def files(directory):
    return {path.relative_to(directory).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in directory.rglob("*") if path.is_file()}


def test_stars_persist_and_filter_before_paging_without_changing_snapshots(client, tmp_path):
    first = create_run(tmp_path, "1009_110000_first")
    create_run(tmp_path, "1009_120000_second")
    third = create_run(tmp_path, "1009_130000_third")
    before = files(first)
    assert not client.get(f"{BASE}/runs").json()["runs"][0]["starred"]
    assert not (first / "run_metadata.json").exists()
    for directory in (first, third):
        response = client.put(f"{BASE}/runs/{directory.name}/star", json={"starred": True})
        assert response.status_code == 200
        assert response.json()["starred"] is True
    assert json.loads((first / "run_metadata.json").read_text())["starred"] is True
    assert {key: value for key, value in files(first).items() if key != "run_metadata.json"} == before
    page = client.get(f"{BASE}/runs?starred_only=true&limit=1&offset=1").json()
    assert page["total"] == 2
    assert page["runs"][0]["run_id"] == first.name
    assert page["runs"][0]["name"] == "first"
    assert client.get(f"{BASE}/runs/{first.name}/summary").json()["starred"] is True
    assert client.put(f"{BASE}/runs/{first.name}/star", json={"starred": False}).status_code == 200
    assert client.get(f"{BASE}/runs?starred_only=true").json()["total"] == 1
    assert client.put(f"{BASE}/runs/{first.name}/star", json={"starred": "true"}).status_code == 422


def test_failed_star_write_preserves_existing_metadata(client, tmp_path, monkeypatch):
    assert run_management.write_state is write_state
    directory = create_run(tmp_path)
    assert client.put(f"{BASE}/runs/{directory.name}/star", json={"starred": True}).status_code == 200
    before = files(directory)
    def fail(*args):
        raise PermissionError("文件被占用")
    monkeypatch.setattr(run_management, "write_state", fail)
    response = client.put(f"{BASE}/runs/{directory.name}/star", json={"starred": False})
    assert response.status_code == 409 and "星标保存失败" in response.json()["detail"]
    assert files(directory) == before
    assert client.get(f"{BASE}/runs/{directory.name}/summary").json()["starred"] is True


@pytest.mark.parametrize("name", ["run_metadata.json", "run_metadata.json.tmp"])
def test_star_rejects_metadata_links(client, tmp_path, monkeypatch, name):
    directory = create_run(tmp_path)
    entry = directory / name
    entry.write_text("{}")
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == entry:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=run_management.stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return value
    monkeypatch.setattr(Path, "lstat", lstat)
    before = files(directory)
    assert client.put(f"{BASE}/runs/{directory.name}/star", json={"starred": True}).status_code == 400
    assert files(directory) == before


def test_rename_preserves_all_files_analysis_and_star_and_uses_new_links(client, tmp_path, monkeypatch):
    directory = create_run(tmp_path)
    (directory / "raw").mkdir()
    (directory / "raw/data.bin").write_bytes(b"original data")
    (directory / "results").mkdir()
    (directory / "results/full_analysis.png").write_bytes(b"plot")
    analysis = AnalysisWorkflow(directory)
    analysis.states["B01"]["status"] = "completed"
    write_state(directory / "analysis_state.json", analysis.snapshot())
    assert client.put(f"{BASE}/runs/{directory.name}/star", json={"starred": True}).status_code == 200
    before = files(directory)
    response = client.post(f"{BASE}/runs/{directory.name}/rename", json={"name": "最佳灵敏度"})
    assert response.status_code == 200
    new_id = "1009_120000_最佳灵敏度"
    assert response.json()["run_id"] == new_id
    assert response.json()["name"] == "最佳灵敏度"
    assert response.json()["starred"] is True
    target = directory.with_name(new_id)
    assert not directory.exists() and files(target) == before
    after = AnalysisWorkflow(target).snapshot()
    assert after["fingerprint"] == analysis.fingerprint
    assert after["revision"] == analysis.revision
    assert after["steps"] == analysis.states
    url = f"{BASE}/runs/{quote(new_id)}"
    summary = client.get(url + "/summary").json()
    assert quote(new_id) in summary["artifacts"]["full_analysis.png"]
    assert client.get(f"/api/runs/{EXPERIMENT_ID}/{quote(new_id)}/artifacts/full_analysis.png").content == b"plot"
    # 活动分析读到旧快照时，也必须返回当前目录标识，磁盘快照保持不变。
    monkeypatch.setattr(main.manager, "has_active_kind", lambda kind: True)
    assert client.get(url + "/analysis").json()["run_id"] == new_id
    assert files(target) == before
    assert client.get(f"{BASE}/runs/{directory.name}/summary").status_code == 404


def test_renamed_directory_with_url_characters_has_working_artifact_links(client, tmp_path):
    directory = create_run(tmp_path)
    (directory / "results").mkdir()
    (directory / "results/full_analysis.png").write_bytes(b"plot")
    response = client.post(f"{BASE}/runs/{directory.name}/rename", json={"name": "最佳 #1% +&"})
    assert response.status_code == 200
    new_id = response.json()["run_id"]
    summary = client.get(f"{BASE}/runs/{quote(new_id, safe='')}/summary").json()
    assert client.get(summary["artifacts"]["full_analysis.png"]).content == b"plot"


@pytest.mark.parametrize("config,prefix", [
    ({"timestamp": "1013_093027", "started_at": "2026-10-13T09:30:28"}, "1013_093027"),
    ({"started_at": "2026-10-13T09:30:28"}, "1013_093028"),
])
def test_unprefixed_run_restores_original_time(client, tmp_path, config, prefix):
    directory = create_run(tmp_path, "最优", **config)
    response = client.post(f"{BASE}/runs/{quote(directory.name)}/rename", json={"name": "更优"})
    assert response.status_code == 200
    assert response.json()["run_id"] == prefix + "_更优"


def test_missing_time_collision_and_os_failure_leave_original_directory(client, tmp_path, monkeypatch):
    unprefixed = create_run(tmp_path, "无时间")
    assert client.post(f"{BASE}/runs/{quote(unprefixed.name)}/rename", json={"name": "新名称"}).status_code == 400
    directory = create_run(tmp_path)
    target = create_run(tmp_path, "1009_120000_已有名称")
    before, target_before = files(directory), files(target)
    assert client.post(f"{BASE}/runs/{directory.name}/rename", json={"name": "已有名称"}).status_code == 409
    assert files(directory) == before and files(target) == target_before
    def fail(*args):
        raise PermissionError("目录被占用")
    monkeypatch.setattr(Path, "rename", fail)
    assert client.post(f"{BASE}/runs/{directory.name}/rename", json={"name": "新名称"}).status_code == 409
    assert files(directory) == before


@pytest.mark.parametrize("name", ["", " ", "..", "../outside", "bad\\path", "x:", "x*", "x?", "x|", "x<", 'x"', "x\x00", "x\n", "tail.", "tail ", "x" * 81])
def test_invalid_names_do_not_move_data(client, tmp_path, name):
    directory = create_run(tmp_path)
    assert client.post(f"{BASE}/runs/{directory.name}/rename", json={"name": name}).status_code in (400, 422)
    assert directory.is_dir()
    assert len(list(directory.parent.iterdir())) == 1


@pytest.mark.parametrize("kind", ["experiment", "session", "analysis", "comparison"])
@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_jobs_guard_rename_and_delete_but_allow_stars(client, tmp_path, monkeypatch, kind, status):
    directory = create_run(tmp_path)
    value = f"{kind}:{EXPERIMENT_ID}" + (f":{directory.name}" if kind == "analysis" else "")
    monkeypatch.setattr(main.manager, "jobs", {"active": SimpleNamespace(kind=value, status=status)})
    assert client.post(f"{BASE}/runs/{directory.name}/rename", json={"name": "新名称"}).status_code == 409
    assert client.delete(f"/api/runs/{EXPERIMENT_ID}/{directory.name}").status_code == 409
    assert client.put(f"{BASE}/runs/{directory.name}/star", json={"starred": True}).status_code == 200
    assert directory.is_dir()


@pytest.mark.parametrize("relative", ["data", f"data/{DATA_TYPE}", f"data/{DATA_TYPE}/1009_120000_test", f"data/{DATA_TYPE}/1009_120000_test/raw"])
def test_reparse_points_block_directory_changes(client, tmp_path, monkeypatch, relative):
    directory = create_run(tmp_path)
    (directory / "raw").mkdir()
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == tmp_path / relative:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=run_management.stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return value
    monkeypatch.setattr(Path, "lstat", lstat)
    assert client.post(f"{BASE}/runs/{directory.name}/rename", json={"name": "新名称"}).status_code == 400
    assert client.delete(f"/api/runs/{EXPERIMENT_ID}/{directory.name}").status_code == 400
    assert directory.exists()


def test_paths_wrong_experiment_and_r_mutations_are_rejected(client, tmp_path):
    directory = create_run(tmp_path, experiment_id="rf-sensitivity-aw-r")
    for action in ("rename", "star"):
        method, body = (client.post, {"name": "new"}) if action == "rename" else (client.put, {"starred": True})
        assert method(f"{BASE}/runs/..%5Coutside/{action}", json=body).status_code == 400
        assert method(f"{BASE}/runs/{directory.name}/{action}", json=body).status_code == 404
        assert method(f"/api/experiments/rf-sensitivity-aw-r/runs/{directory.name}/{action}", json=body).status_code == 410
    assert directory.exists()


def test_delete_removes_complete_run_but_preserves_other_run_and_comparison(client, tmp_path):
    directory = create_run(tmp_path)
    other = create_run(tmp_path, "1009_130000_other")
    (directory / "raw").mkdir()
    (directory / "raw/data.bin").write_bytes(b"raw")
    (directory / "results").mkdir()
    (directory / "results/plot.png").write_bytes(b"plot")
    comparison = tmp_path / "results" / EXPERIMENT_ID / "comparisons" / ("a" * 32)
    comparison.mkdir(parents=True)
    (comparison / "comparison.json").write_text(json.dumps({"run_id": directory.name}))
    before = files(comparison)
    assert client.delete(f"/api/runs/{EXPERIMENT_ID}/{directory.name}").status_code == 200
    assert not directory.exists() and other.is_dir()
    assert files(comparison) == before
    assert client.get(f"{BASE}/runs").json()["total"] == 1
