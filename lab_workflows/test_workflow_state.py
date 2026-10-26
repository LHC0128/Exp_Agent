"""工作流状态 JSON 的小文件契约；不连接仪器、不执行实验。"""

import json
from pathlib import Path

import pytest

from . import workflow_state


def test_utf8_round_trip_and_missing_file(tmp_path):
    path = tmp_path / "子目录" / "state.json"
    assert workflow_state.read_state(str(path)) is None
    payload = {"说明": "状态快照", "revision": 2, "values": [0.25, None, True]}
    assert workflow_state.write_state(str(path), payload) is None
    assert workflow_state.read_state(path) == payload
    assert path.read_text(encoding="utf-8") == json.dumps(payload, ensure_ascii=False, indent=2)
    assert "状态快照".encode("utf-8") in path.read_bytes()
    assert not path.with_suffix(".json.tmp").exists()

    workflow_state.write_state(path, {"revision": 3})
    assert workflow_state.read_state(path) == {"revision": 3}
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("value,error_type", [
    (float("nan"), ValueError),
    (float("inf"), ValueError),
    (object(), TypeError),
], ids=["nan", "infinity", "non-json"])
def test_invalid_payload_preserves_original_and_existing_temporary(tmp_path, value, error_type):
    path = tmp_path / "state.json"
    temporary = path.with_suffix(".json.tmp")
    path.write_bytes(b'{"revision": 1}')
    temporary.write_bytes(b"previous temporary")

    with pytest.raises(error_type):
        workflow_state.write_state(path, {"value": value})

    assert path.read_bytes() == b'{"revision": 1}'
    assert temporary.read_bytes() == b"previous temporary"


@pytest.mark.parametrize("content,error_type", [
    (b"{", json.JSONDecodeError),
    (b"\xff", UnicodeDecodeError),
], ids=["invalid-json", "invalid-utf8"])
def test_invalid_file_read_propagates(tmp_path, content, error_type):
    path = tmp_path / "state.json"
    path.write_bytes(content)
    with pytest.raises(error_type):
        workflow_state.read_state(path)
    assert path.read_bytes() == content


def test_read_permission_error_propagates(tmp_path, monkeypatch):
    error = PermissionError("模拟读取失败")

    def fail(path, **kwargs):
        raise error

    monkeypatch.setattr(Path, "read_text", fail)
    with pytest.raises(PermissionError) as caught:
        workflow_state.read_state(tmp_path / "state.json")
    assert caught.value is error


def test_partial_temporary_write_keeps_original_and_partial_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    temporary = path.with_suffix(".json.tmp")
    path.write_bytes(b'{"revision": 1}')
    temporary.write_bytes(b"previous temporary")
    original_write = Path.write_text
    error = OSError("模拟临时文件写入中断")

    def fail(source, text, **kwargs):
        assert source == temporary
        original_write(source, '{"partial":', **kwargs)
        raise error

    monkeypatch.setattr(Path, "write_text", fail)
    with pytest.raises(OSError) as caught:
        workflow_state.write_state(path, {"revision": 2})
    assert caught.value is error
    assert path.read_bytes() == b'{"revision": 1}'
    assert temporary.read_bytes() == b'{"partial":'


@pytest.mark.parametrize("error_type,attempt_count", [
    (OSError, 1),
    (PermissionError, 6),
], ids=["replace-error", "windows-retry-exhausted"])
def test_replace_failure_keeps_original_and_complete_temporary(
    tmp_path, monkeypatch, error_type, attempt_count,
):
    path = tmp_path / "state.json"
    temporary = path.with_suffix(".json.tmp")
    path.write_bytes(b'{"revision": 1}')
    temporary.write_bytes(b"previous temporary")
    error = error_type("模拟原子替换失败")
    attempts, waits = [], []

    def fail(source, target):
        assert source == temporary and target == path
        assert path.read_bytes() == b'{"revision": 1}'
        assert json.loads(source.read_text(encoding="utf-8")) == {"revision": 2}
        attempts.append(source)
        raise error

    monkeypatch.setattr(Path, "replace", fail)
    monkeypatch.setattr(workflow_state.time, "sleep", waits.append)
    with pytest.raises(error_type) as caught:
        workflow_state.write_state(path, {"revision": 2})
    assert caught.value is error
    assert len(attempts) == attempt_count
    assert waits == [0.02] * (attempt_count - 1)
    assert path.read_bytes() == b'{"revision": 1}'
    assert workflow_state.read_state(temporary) == {"revision": 2}
