"""公共分析状态的微型契约；不导入实验、绘图或仪器。"""

from copy import deepcopy
import hashlib
import json
import math
import os

import pytest

from . import analysis_execution
from .analysis_execution import AnalysisRunState, file_signature, input_fingerprint
from .workflow_stages import DependencyRules, Stage
from .workflow_state import read_state, write_state


STAGES = (
    Stage("B01", "输入核验", "分析", (), artifacts=("inputs.json",)),
    Stage("B02", "离线计算", "分析", ("B01",), artifacts=("report.txt",)),
)


def state_at(path, **kwargs):
    return AnalysisRunState(path, stages=STAGES, rules=DependencyRules(finish_id=None),
        fingerprint=kwargs.pop("fingerprint", {"version": 1}), **kwargs)


def test_content_fingerprint_and_explicit_extra_inputs(tmp_path):
    raw = tmp_path / "raw/nested"
    raw.mkdir(parents=True)
    source = raw / "data.bin"
    source.write_bytes(b"abc")
    results = tmp_path / "results"
    results.mkdir()
    phase = results / "phase_calibration.yaml"
    phase.write_bytes(b"phase: 1")
    config = {"parameters": {"值": 1}, "analysis_status": "running", "analysis_error": "old"}
    fingerprint = input_fingerprint(tmp_path, config, analysis_version="saved-v1",
        extra_inputs=("results/missing.npz",))
    expected_hash = hashlib.sha256(json.dumps({"parameters": {"值": 1}}, sort_keys=True,
        ensure_ascii=False, default=str).encode()).hexdigest()
    assert fingerprint == {"config_sha256": expected_hash, "analysis_version": "saved-v1",
        "raw_files": {"raw/nested/data.bin": [3, hashlib.sha256(b"abc").hexdigest()],
                      "results/missing.npz": None}}
    assert file_signature(tmp_path / "missing") is None
    phase.write_bytes(b"phase: 2")
    assert input_fingerprint(tmp_path, config | {"analysis_status": "completed"},
        analysis_version="saved-v1", extra_inputs=("results/missing.npz",)) == fingerprint
    assert "results/phase_calibration.yaml" in input_fingerprint(tmp_path, config,
        analysis_version="saved-v1", extra_inputs=("results/phase_calibration.yaml",))["raw_files"]
    old = source.stat()
    source.write_bytes(b"abd")
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert file_signature(source) == [3, hashlib.sha256(b"abd").hexdigest()]
    assert input_fingerprint(tmp_path, config, analysis_version="saved-v1") != fingerprint


def test_run_state_owns_restore_prepare_and_product_records(tmp_path):
    ctx = state_at(tmp_path, options={"FIT": 2})
    assert not ctx.restored and not ctx.state_path.exists() and not ctx.results.exists()
    assert ctx.snapshot() == dict(run_id=tmp_path.name, revision=0, options={"FIT": 2},
        steps={key: {"status": "pending", "error": ""} for key in ("B01", "B02")},
        fingerprint={"version": 1}, summary={})
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.prepare(["B02"], fingerprint={"version": 1})
    assert ctx.revision == 0
    assert ctx.prepare(["B02", "B01"], fingerprint={"version": 1}, expected_revision=0) == ["B01", "B02"]
    assert ctx.revision == 1
    ctx.results.mkdir()
    for item, name in (("B01", "inputs.json"), ("B02", "report.txt")):
        ctx.begin(item)
        assert ctx.states[item] == {"status": "running", "error": ""}
        assert ctx.summary == {"status": "running", "options": {"FIT": 2}}
        (ctx.results / name).write_bytes(b"{}")
        ctx.complete(item, [name], status="partial" if item == "B01" else "completed")
        assert ctx.states[item] == dict(status="completed", error="", artifacts=[name],
            files={name: [2, hashlib.sha256(b"{}").hexdigest()]}, input_fingerprint={"version": 1},
            parameters={"FIT": 2}, parameter_fingerprint=hashlib.sha256(b'{"FIT": 2}').hexdigest(), revision=1)
    ctx.persist()
    saved = ctx.state_path.read_bytes()
    snap = ctx.snapshot()
    snap["steps"]["B01"]["status"] = "changed"
    snap["summary"]["status"] = "changed"
    assert ctx.states["B01"]["status"] == ctx.summary["status"] == "completed"
    restored = state_at(tmp_path, options={"FIT": 2})
    assert restored.restored and restored.revision == 1 and restored.snapshot() == ctx.snapshot()
    with pytest.raises(ValueError, match="修订已改变"):
        restored.prepare(["B01"], fingerprint={"version": 2}, expected_revision=1)
    assert restored.revision == 2 and restored.fingerprint == {"version": 2}
    assert all(record["status"] == "stale" for record in restored.states.values())
    assert ctx.state_path.read_bytes() == saved
    (ctx.results / "inputs.json").unlink()
    stale = state_at(tmp_path)
    assert stale.revision == 2 and all(record["status"] == "stale" for record in stale.states.values())
    (ctx.results / "inputs.json").write_bytes(b"{}")
    interrupted = deepcopy(ctx.snapshot())
    interrupted["steps"]["B02"] = {"status": "running", "error": ""}
    write_state(ctx.state_path, interrupted)
    recovered = state_at(tmp_path)
    assert recovered.revision == 2 and recovered.states["B02"]["status"] == "stale"
    recovered.fail("B02", ValueError("质量失败"), status="quality_failed")
    assert recovered.states["B02"] == {"status": "quality_failed", "error": "质量失败"}
    recovered.fail("B02", RuntimeError("计算失败"), status="failed", files=["report.txt"])
    assert recovered.states["B02"]["files"] == {"report.txt": file_signature(ctx.results / "report.txt")}
    assert recovered.states["B02"]["revision"] == 2


def test_archive_only_declared_products(tmp_path):
    ctx = state_at(tmp_path)
    assert ctx.archive(["not_present.txt"]) is None and not ctx.results.exists()
    nested = ctx.results / "diagnostics"
    nested.mkdir(parents=True)
    (nested / "fit.txt").write_bytes(b"fit")
    for name in ("report.txt", "frozen.npz", "frozen.csv", "source.yaml"):
        (ctx.results / name).write_bytes(name.encode())
    backup = ctx.archive(["report.txt", "report.txt", "diagnostics", "not_present.txt"])
    assert backup.parent == ctx.results and backup.name.startswith("before_")
    assert (backup / "report.txt").read_bytes() == b"report.txt"
    assert (backup / "diagnostics/fit.txt").read_bytes() == b"fit"
    assert not (ctx.results / "report.txt").exists() and not nested.exists()
    for name in ("frozen.npz", "frozen.csv", "source.yaml"):
        assert (ctx.results / name).read_bytes() == name.encode()


def test_persist_encoding_and_callback_order(tmp_path, monkeypatch):
    events = []

    def encoder(snapshot):
        events.append("encode")
        assert math.isnan(snapshot["summary"]["value"])
        snapshot["summary"]["value"] = None
        return snapshot

    def after_persist(snapshot):
        assert read_state(tmp_path / "analysis_state.json") == snapshot
        events.append("after")

    ctx = state_at(tmp_path, encoder=encoder, after_persist=after_persist)
    ctx.summary["value"] = float("nan")
    ctx.persist()
    assert events == ["encode", "after"] and math.isnan(ctx.summary["value"])
    failure = OSError("保存失败")

    def broken_write(*_):
        raise failure

    with monkeypatch.context() as scoped:
        scoped.setattr(analysis_execution, "write_state", broken_write)
        with pytest.raises(OSError) as caught:
            ctx.persist()
        assert caught.value is failure
    assert events == ["encode", "after", "encode"]

    def broken_callback(snapshot):
        assert read_state(ctx.state_path) == snapshot
        raise failure

    other = state_at(tmp_path, after_persist=broken_callback)
    other.summary = {"status": "partial"}
    with pytest.raises(OSError) as caught:
        other.persist()
    assert caught.value is failure and read_state(other.state_path)["summary"] == {"status": "partial"}
