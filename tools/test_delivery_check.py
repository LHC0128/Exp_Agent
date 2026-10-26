"""交付检查只查询固定 Git 元数据；所有 Git 响应均为替身，不初始化仓库。"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import checkpoint, delivery_check


class FakeGit:
    def __init__(self, root):
        self.root, self.calls = root, []
        self.head = b"a" * 40 + b"\n"
        self.status = (b" M src/dirty name.py\0R  new name.py\0old  name.py\0"
                       b"?? loose folder/\0 M GUI/AGENTS.md\0?? .agents/\0")
        self.ignored = {"space ignored.txt", ".agents/retired-claude/20261026/manifest.json", "GUI/AGENTS.md", "docs/README.md"}
        self.tracked = {"GUI/AGENTS.md", "docs/README.md"}
        self.on_status = None

    def __call__(self, command, *, cwd, env, input, capture_output, timeout, check):
        assert command[0] == "git" and cwd == self.root
        assert env["GIT_OPTIONAL_LOCKS"] == "0" and capture_output is True and check is False
        assert timeout == 10
        args, code, output = tuple(command[1:]), 0, b""
        self.calls.append((args, input))
        if args == ("rev-parse", "--show-toplevel"):
            output = str(self.root).encode() + b"\n"
        elif args == ("rev-parse", "--verify", "--quiet", "HEAD"):
            output, code = (self.head, 0) if self.head is not None else (b"", 1)
        elif args == ("rev-parse", "--git-path", "HEAD"):
            output = b".git/HEAD\n"
        elif args == ("rev-parse", "--git-path", "index"):
            output = b".git/index\n"
        elif args == ("-c", "core.fsmonitor=false", "status", "--porcelain=v1", "-z", "--untracked-files=normal"):
            if self.on_status is not None:
                self.on_status()
            output = self.status
        elif args in (("check-ignore", "-z", "--stdin"), ("check-ignore", "--no-index", "-z", "--stdin")):
            paths = input[:-1].decode().split("\0")
            # 旧参数会把已跟踪文档也算入匹配，确保回归能够抓住原错误。
            output = b"".join(p.encode() + b"\0" for p in paths if p in self.ignored
                              and ("--no-index" in args or p not in self.tracked))
            code = 0 if output else 1
        else:
            raise AssertionError(f"禁止非 allowlist Git 命令: {command}")
        return SimpleNamespace(returncode=code, stdout=output, stderr=b"")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    metadata = tmp_path / ".git"
    metadata.mkdir()
    (metadata / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    (metadata / "index").write_bytes(b"\x00fake index\xff\r\n")
    (tmp_path / "approved.txt").write_bytes(b"original approved bytes\r\n")
    (tmp_path / "GUI").mkdir()
    (tmp_path / "GUI/AGENTS.md").write_text("仅本地", encoding="utf-8")
    monkeypatch.setattr(checkpoint.subprocess, "run", Mock(side_effect=FileNotFoundError("只允许 fake Git")))
    git = FakeGit(tmp_path)

    def cli(*args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = delivery_check.main(list(args), _root=tmp_path, _run_git=git)
        assert not err.getvalue()
        return code, json.loads(out.getvalue())

    return tmp_path, git, cli


def _files(paths):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}


def _init_task(root, *, verified=False):
    with redirect_stdout(io.StringIO()):
        assert checkpoint.main(["init", "small", "--objective", "只读交付", "--allow", "approved.txt",
                                "--allow", "GUI/AGENTS.md"], _root=root) == 0
        if verified:
            def fake(argv):
                options = dict(zip(argv[::2], argv[1::2]))
                report = {"schema_version": 1, "root": str(root), "profile": "python", "status": "passed",
                          "python": "3.10", "timeout_s": 1.0, "duration_s": 0.001,
                          "scope": {"status": "not_run", "changed_since": None, "changed_files": [], "reason": "未请求范围"},
                          "checks": [{"name": name, "status": "passed", "returncode": 0, "command": [],
                                      "duration_s": 0.001, "reason": "fake"}
                                     for name in ("python-syntax", "python-tests")],
                          "business_regressions": {"status": "not_run", "reason": "不运行实验"}}
                Path(options["--report"]).write_text(json.dumps(report), encoding="utf-8")
                return 0
            assert checkpoint.main(["check", "small"], _root=root, _check_main=fake) == 0
    directory = root / ".cache/agent-tasks/small"
    data = json.loads((directory / "checkpoint.json").read_text(encoding="utf-8"))
    paths = [directory / "checkpoint.json", *(directory / p["baseline"] for p in data["allow"])]
    paths += [directory / p["report"] for p in data["checks"]]
    return directory, paths


def test_default_classifies_nul_rename_spaces_and_explicit_ignored_without_scanning(sandbox, monkeypatch):
    root, git, cli = sandbox
    paths = [root / ".git/HEAD", root / ".git/index", root / "approved.txt"]
    original = _files(paths)
    candidates = ["space ignored.txt", ".agents/retired-claude/20261026/manifest.json", "AGENTS.md", "CLAUDE.md",
                  ".claude/settings.json", ".codex/config.toml", ".vscode/settings.json"]
    with monkeypatch.context() as guard:
        guard.setattr(Path, "rglob", Mock(side_effect=AssertionError("禁止递归扫描")))
        code, result = cli("--candidate", *candidates)
    assert code == 0 and result["status"] == "collected"
    renamed = result["staged"][0]
    assert renamed == {"path": "new name.py", "xy": "R ", "original_path": "old  name.py"}
    assert {p["path"] for p in result["changed"]} == {"src/dirty name.py", "new name.py", "GUI/AGENTS.md"}
    assert result["untracked"][0]["path"] == "loose folder/"
    assert result["ignored"] == ["space ignored.txt", ".agents/retired-claude/20261026/manifest.json"]
    assert set(result["local_only"]) == set(candidates[1:]) | {".agents/", "GUI/AGENTS.md"}
    assert set(result["ignored_query_paths"]) == set(result["candidates"])
    assert result["task"]["status"] == "not_verified"
    assert all(p["status"] == "not_verified" for p in result["task"]["verification"].values())
    assert result["business_regressions"]["status"] == "not_run"
    assert result["repository_readiness"] == "not_assessed" and result["git"]["stable"] is True
    assert _files(paths) == original and not (root / ".cache").exists()
    assert len([c for c, _ in git.calls if c[0] == "check-ignore"]) == 1


def test_tracked_ignore_rule_matches_are_not_actually_ignored_but_untracked_assets_are(sandbox):
    root, git, cli = sandbox
    readme = root / "docs/README.md"
    readme.parent.mkdir()
    readme.write_text("已跟踪文档仍可提交", encoding="utf-8")
    git.status += b" M docs/README.md\0"
    original = _files([readme, root / ".git/index"])
    asset = ".agents/retired-claude/20261026/manifest.json"
    code, result = cli("--candidate", "docs/README.md", asset)
    assert code == 0 and result["ignored"] == [asset]
    assert any(item["path"] == "docs/README.md" for item in result["changed"])
    assert result["ignored_query_paths"] == ["docs/README.md", asset]
    assert _files([readme, root / ".git/index"]) == original


@pytest.mark.parametrize("verified", [False, True])
def test_task_evidence_is_profile_scoped_read_only_and_ignored_queries_only_approved(sandbox, verified):
    root, _, cli = sandbox
    _, evidence = _init_task(root, verified=verified)
    watched = evidence + [root / ".git/HEAD", root / ".git/index"]
    original = _files(watched)
    code, result = cli("--task", "small", "--candidate", "space ignored.txt")
    assert code == 0 and _files(watched) == original
    assert result["task"]["verified_profiles"] == (["python"] if verified else [])
    assert result["task"]["verification"]["frontend"]["status"] == "not_verified"
    assert set(result["ignored_query_paths"]) == {"space ignored.txt", "approved.txt", "GUI/AGENTS.md"}
    assert "GUI/AGENTS.md" not in result["ignored"] and "GUI/AGENTS.md" in result["local_only"]
    assert result["business_regressions"]["status"] == "not_run" and result["repository_readiness"] == "not_assessed"
    (root / "approved.txt").write_bytes(b"changed after profile check")
    assert cli("--task", "small")[1]["task"]["verified_profiles"] == []
    assert _files(watched) == original


@pytest.mark.parametrize("damage", ["unknown", "baseline", "report"])
def test_unknown_task_or_corrupt_evidence_never_claims_verification(sandbox, damage):
    root, _, cli = sandbox
    directory, evidence = _init_task(root, verified=True)
    if damage == "baseline":
        (directory / "baseline/0000.bin").write_bytes(b"corrupt")
    elif damage == "report":
        evidence[-1].unlink()
    watched = [p for p in evidence if p.exists()] + [root / ".git/index"]
    original = _files(watched)
    code, result = cli("--task", "missing" if damage == "unknown" else "small")
    assert code == (0 if damage == "report" else 1)
    assert result["task"]["verified_profiles"] == [] and _files(watched) == original
    assert result["business_regressions"]["status"] == "not_run"
    if damage != "report":
        assert result["status"] == "failed" and result["errors"]


@pytest.mark.parametrize("target", ["source.py", ".git/index", ".cache/agent-tasks/small/checkpoint.json",
                                   ".cache/check/report.json", "../outside.json", "absolute", "foreign", "link"])
def test_report_output_rejects_foreign_or_escaping_paths_without_git_or_overwrite(sandbox, target, monkeypatch):
    root, git, cli = sandbox
    foreign = root / ".cache/delivery/foreign.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text('{"schema_version": 1, "checks": []}', encoding="utf-8")
    original = _files([foreign, root / ".git/index"])
    if target == "absolute":
        target = str(root / ".cache/delivery/absolute.json")
    elif target == "foreign":
        target = ".cache/delivery/foreign.json"
    elif target == "link":
        target = ".cache/delivery/linked.json"
        link, resolve = root / target, Path.resolve
        monkeypatch.setattr(Path, "resolve", lambda p, *a, **kw:
                            root.parent / "outside.json" if p == link else resolve(p, *a, **kw))
    code, result = cli("--report", target)
    assert code == 1 and result["status"] == "failed" and not git.calls
    assert _files([foreign, root / ".git/index"]) == original
    assert not (root / "source.py").exists()


def test_report_can_only_create_or_replace_owned_delivery_json(sandbox):
    root, _, cli = sandbox
    index_before = _files([root / ".git/index"])
    code, result = cli("--report", ".cache/delivery/own report.json")
    path = root / ".cache/delivery/own report.json"
    assert code == 0 and json.loads(path.read_text(encoding="utf-8")) == result
    assert cli("--report", ".cache/delivery/own report.json")[0] == 0
    assert _files([root / ".git/index"]) == index_before


@pytest.mark.parametrize("change", ["index", "head_commit", "head_branch"])
def test_git_fingerprint_changes_fail_without_attributing_the_change_to_tool(sandbox, change):
    root, git, cli = sandbox

    def during():
        if change == "index":
            (root / ".git/index").write_bytes(b"external index change")
        elif change == "head_commit":
            git.head = b"b" * 40 + b"\n"
        else:
            (root / ".git/HEAD").write_bytes(b"ref: refs/heads/another\n")
    git.on_status = during
    code, result = cli()
    assert code == 1 and result["status"] == "failed" and result["git"]["stable"] is False
    assert any("不能归因于本工具" in reason for reason in result["errors"])


def test_unborn_head_and_no_candidates_do_not_query_ignored_or_run_anything(sandbox):
    _, git, cli = sandbox
    git.head = None
    code, result = cli()
    assert code == 0 and result["git"]["before"]["head_commit"] is None
    assert result["ignored_query_paths"] == [] and not any(c[0] == "check-ignore" for c, _ in git.calls)
    with pytest.raises(SystemExit) as rejected:
        cli("--run")
    assert rejected.value.code == 2
