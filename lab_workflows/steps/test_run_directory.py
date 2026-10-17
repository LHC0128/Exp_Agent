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
