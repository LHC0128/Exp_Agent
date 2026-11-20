"""生成 scripts/README.md 的研究脚本索引，并校验 scripts/ 的存放约定。

约定（细则写在 scripts/README.md 开头）：

- ``scripts/<分组>/``：分组名等于注册表 ``data_type``（含归档实验），即运行目录 ``data/`` 下的
  顶层目录名；跨实验对比用 ``_shared``，仪器探针用 ``_devices``。
- 分组下两种条目：单文件脚本 ``<名称>.py``（模块 docstring 首行写它回答的问题），或研究目录
  ``<YYYYMMDD>_<名称>/``（自带 README.md，开头 YAML frontmatter 写 ``title``、``conclusion``、
  ``data``——相对数据根的运行目录列表）。
- 条目里任一 .py 导入仪器驱动或 ``tools.lab_client`` 即在索引标为“需硬件”。

用法：

    agent_exp_env\\Scripts\\python.exe -m tools.gen_script_index
    agent_exp_env\\Scripts\\python.exe -m tools.gen_script_index --check

``--check`` 只校验不写入：约定不满足、文件被 git 忽略或索引落后时非零退出并列出问题。
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = "scripts"
SPECIAL_GROUPS = ("_shared", "_devices")
BEGIN_MARKER = "<!-- BEGIN GENERATED SCRIPT INDEX: 由 python -m tools.gen_script_index 生成，勿手改 -->"
END_MARKER = "<!-- END GENERATED SCRIPT INDEX -->"
STUDY_NAME = re.compile(r"^\d{8}_[A-Za-z0-9_]+$")
STUDY_FIELDS = ("title", "conclusion", "data")
# 研究目录里非代码文件（结论图、CSV、报告）的体积上限；超出只警告，提示把中间产物留在 data/。
STUDY_ASSET_BUDGET_BYTES = 3 * 1024 * 1024
HARDWARE_MODULES = frozenset({
    "aigtek_1200b", "dho900", "gs200", "keithley_6221", "lockin_amplifier", "oscilloscope",
    "sds_acquisition", "signal_generator", "tec_controller", "toptica_laser",
    "pyvisa", "zhinst", "serial", "tools.lab_client", "lab_workflows.steps.temperature",
})
# 允许被忽略的生成物：字节码，以及与 PNG 重复的 PDF（根 .gitignore 的 scripts/**/*.pdf）。
IGNORED_OK = (".pyc", ".pdf")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


@dataclass(frozen=True)
class Entry:
    group: str
    name: str  # 单文件含 .py 后缀；研究目录为目录名
    summary: str
    data: tuple[str, ...]
    hardware: bool


def registry_groups() -> set[str]:
    from lab_workflows.experiments import registry
    return {d.data_type for d in registry.list_experiments(include_archived=True)}


def _uses_hardware(paths: Iterable[Path]) -> bool:
    for path in paths:
        tree = ast.parse(path.read_bytes(), str(path))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            for name in names:
                parts = name.split(".")
                if any(".".join(parts[:i]) in HARDWARE_MODULES for i in range(1, len(parts) + 1)):
                    return True
    return False


def _python_files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def collect(root: Path, groups: set[str]) -> tuple[list[Entry], list[str], list[str]]:
    """返回 (条目, 问题, 警告)；问题会让 --check 失败。"""
    entries: list[Entry] = []
    problems: list[str] = []
    warnings: list[str] = []
    base = root / SCRIPTS
    if not base.is_dir():
        return entries, [f"{SCRIPTS}/ 不存在"], warnings
    allowed = groups | set(SPECIAL_GROUPS)
    for group_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name != "__pycache__"):
        group = group_dir.name
        if group not in allowed:
            problems.append(f"{SCRIPTS}/{group}: 分组名不是注册表 data_type，也不是 {'/'.join(SPECIAL_GROUPS)}")
            continue
        for item in sorted(group_dir.iterdir()):
            rel = f"{SCRIPTS}/{group}/{item.name}"
            if item.name in ("__pycache__", "README.md"):
                continue
            if item.is_file() and item.suffix == ".py":
                doc = ast.get_docstring(ast.parse(item.read_bytes(), str(item))) or ""
                summary = doc.strip().splitlines()[0].strip() if doc.strip() else ""
                if not summary:
                    problems.append(f"{rel}: 缺模块 docstring（首行写脚本回答的问题）")
                entries.append(Entry(group, item.name, summary, (), _uses_hardware([item])))
            elif item.is_dir():
                if not STUDY_NAME.match(item.name):
                    problems.append(f"{rel}: 研究目录名应为 YYYYMMDD_<名称>")
                readme = item / "README.md"
                meta: dict = {}
                if not readme.is_file():
                    problems.append(f"{rel}: 缺 README.md")
                else:
                    match = _FRONTMATTER.match(readme.read_text(encoding="utf-8"))
                    meta = (yaml.safe_load(match.group(1)) or {}) if match else {}
                    missing = [k for k in STUDY_FIELDS if not meta.get(k)]
                    if missing:
                        problems.append(f"{rel}/README.md: frontmatter 缺 {', '.join(missing)}")
                data = meta.get("data") or []
                data = (data,) if isinstance(data, str) else tuple(str(d) for d in data)
                entries.append(Entry(group, item.name, str(meta.get("conclusion") or "").strip(),
                                     data, _uses_hardware(_python_files(item))))
                assets = sum(p.stat().st_size for p in item.rglob("*") if p.is_file()
                             and not p.name.endswith((".py", *IGNORED_OK)) and "__pycache__" not in p.parts)
                if assets > STUDY_ASSET_BUDGET_BYTES:
                    warnings.append(f"{rel}: 非代码文件 {assets / 2**20:.1f} MB，超过 "
                                    f"{STUDY_ASSET_BUDGET_BYTES // 2**20} MB；中间产物留在 data/")
            else:
                problems.append(f"{rel}: 分组下只放单文件 .py 或研究目录")
    return entries, problems, warnings


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_block(entries: list[Entry]) -> str:
    lines = [BEGIN_MARKER, "", "| 分组 | 条目 | 结论 / 用途 | 读取数据（相对数据根） | 硬件 |",
             "|---|---|---|---|---|"]
    for e in entries:
        link = f"[{e.name}]({e.group}/{e.name}{'/README.md' if not e.name.endswith('.py') else ''})"
        data = "<br>".join(f"`{d}`" for d in e.data) or "—"
        lines.append(f"| {e.group} | {link} | {_cell(e.summary)} | {data} | {'需硬件' if e.hardware else '—'} |")
    if not entries:
        lines.append("| — | — | 暂无 | — | — |")
    lines += ["", END_MARKER]
    return "\n".join(lines)


def _git_ignored(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--others", "--ignored",
                          "--exclude-standard", "--", SCRIPTS],
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p and "__pycache__/" not in p and not p.endswith(IGNORED_OK)]


def update_readme(text: str, block: str) -> str:
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise ValueError(f"{SCRIPTS}/README.md 须各含一个索引起止标记")
    head, rest = text.split(BEGIN_MARKER)
    _, tail = rest.split(END_MARKER)
    return head + block + tail


def run(root: Path, *, check: bool, groups: set[str] | None = None,
        ignored: Callable[[Path], list[str]] = _git_ignored) -> tuple[int, list[str]]:
    entries, problems, warnings = collect(root, registry_groups() if groups is None else groups)
    if (root / SCRIPTS).is_dir():
        problems += [f"{p}: 被 .gitignore 忽略，提交时会丢失（改名，避开 data/、results/、tests/）"
                     for p in ignored(root)]
    readme = root / SCRIPTS / "README.md"
    messages = [f"[警告] {w}" for w in warnings]
    if readme.is_file():
        current = readme.read_bytes().decode("utf-8")
        newline = "\r\n" if "\r\n" in current else "\n"
        try:
            expected = update_readme(current.replace("\r\n", "\n"), render_block(entries))
        except ValueError as exc:
            problems.append(str(exc))
        else:
            expected = expected.replace("\n", newline)
            if expected != current:
                if check:
                    problems.append(f"{SCRIPTS}/README.md 索引落后，运行 python -m tools.gen_script_index")
                else:
                    readme.write_bytes(expected.encode("utf-8"))
                    messages.append(f"已更新 {SCRIPTS}/README.md（{len(entries)} 个条目）")
    elif (root / SCRIPTS).is_dir():
        problems.append(f"{SCRIPTS}/README.md 不存在")
    messages += [f"[问题] {p}" for p in problems]
    return (1 if problems else 0), messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args(argv)
    code, messages = run(ROOT, check=args.check)
    if messages:
        sys.stdout.buffer.write(("\n".join(messages) + "\n").encode("utf-8"))
    if code == 0 and args.check:
        sys.stdout.buffer.write(f"{SCRIPTS}/ 索引与约定一致\n".encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
