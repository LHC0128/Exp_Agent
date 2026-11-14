"""实验说明（`ExperimentDefinition.documentation`）与 `docs/experiments/` 的守卫测试。

覆盖五件事：注册实验都声明说明正文、声明路径按仓库根解析后存在、说明正文 frontmatter 的
`experiment_id` 与定义 id 一致、文档与实验 id 双向唯一且只取已知 id、归档卡片不声明正文。
全部用例只读文件，不依赖 git、硬件或网络；缺数据直接失败，不使用 skip。
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

from lab_workflows.experiments.archived import ARCHIVED_EXPERIMENTS
from lab_workflows.experiments.registry import list_experiments
from tools.gen_module_readme import README_NAME, plan

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs" / "experiments"
MODULES_DIR = ROOT / "lab_workflows" / "experiment_modules"

# README 指针里引用的说明正文；只认仓库内 docs/experiments 下的 .md。
DOC_REFERENCE = re.compile(r"docs/experiments/[^\s`'\"()]+\.md")

# 最小 frontmatter：文件开头的 --- 围栏内为元数据块；CRLF 与 LF 都接受。
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
# frontmatter 顶层键；缩进行属于上一键的嵌套值，不参与顶层解析。
FRONTMATTER_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")


def _read_text(path: Path) -> str:
    """仓库文档存在带 BOM 的文件，统一按 utf-8-sig 读取。"""
    return path.read_text(encoding="utf-8-sig")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """返回 frontmatter 顶层键值映射；没有 frontmatter 或不是映射时返回 None。"""
    match = FRONTMATTER.match(text)
    if match is None:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line or line[0] in " \t":
            continue
        if line.startswith("-") or line.startswith("#"):
            continue
        key_value = FRONTMATTER_KEY.match(line)
        if key_value is None:
            continue
        fields[key_value.group(1)] = _unquote(key_value.group(2).strip())
    return fields or None


def _relative(path: Path) -> str:
    """报错时统一用仓库根相对的 POSIX 路径。"""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _documents() -> list[Path]:
    return sorted(DOCS_DIR.glob("*.md"))


def test_registered_experiments_declare_documentation() -> None:
    """每个注册实验都要声明非空的 documentation。"""
    experiments = list_experiments()
    assert experiments, "实验注册表为空，无法校验实验说明"
    missing = [
        definition.id for definition in experiments
        if not definition.documentation.strip()
    ]
    assert not missing, (
        f"以下 {len(missing)} 个注册实验未声明 documentation 正文："
        + "、".join(missing)
    )


def test_declared_documentation_paths_exist() -> None:
    """声明路径必须是仓库根相对路径，解析后指向已存在的说明文件。"""
    problems: list[str] = []
    for definition in list_experiments():
        declared = definition.documentation.strip()
        if not declared:
            problems.append(f"{definition.id}: 未声明 documentation，无法校验说明文件")
            continue
        path = Path(declared)
        if path.is_absolute():
            problems.append(
                f"{definition.id}: documentation 应为仓库根相对路径，实际为 {declared}"
            )
            continue
        resolved = (ROOT / path).resolve()
        if not resolved.is_relative_to(ROOT):
            problems.append(
                f"{definition.id}: documentation 必须落在仓库内，实际解析为 {resolved}"
            )
            continue
        if not resolved.is_file():
            problems.append(
                f"{definition.id}: 声明的说明文件 {declared} 不存在"
                f"（解析为 {resolved}）"
            )
    if problems:
        pytest.fail("实验说明路径无效：\n" + "\n".join(problems))


def test_documentation_frontmatter_matches_definition() -> None:
    """说明文件 frontmatter 的 experiment_id 必须与注册实验 id 一致。"""
    problems: list[str] = []
    for definition in list_experiments():
        declared = definition.documentation.strip()
        path = ROOT / declared if declared else None
        if path is None or not path.is_file():
            problems.append(
                f"{definition.id}: 没有可校验的说明文件（documentation={declared!r}）"
            )
            continue
        fields = _parse_frontmatter(_read_text(path))
        if fields is None:
            problems.append(
                f"{definition.id}: {_relative(path)} 缺少合法 frontmatter"
                "（文件开头没有 --- 围栏，或围栏内不是键值映射）"
            )
            continue
        document_id = fields.get("experiment_id", "")
        if not document_id:
            problems.append(
                f"{definition.id}: {_relative(path)} 的 frontmatter 没有 experiment_id"
            )
            continue
        if document_id != definition.id:
            problems.append(
                f"{definition.id}: {_relative(path)} 的 experiment_id 为 "
                f"{document_id!r}，与定义 id 不一致"
            )
    if problems:
        pytest.fail("实验说明 frontmatter 与实验定义不一致：\n" + "\n".join(problems))


def test_document_experiment_ids_are_unique_and_known() -> None:
    """每篇说明的 experiment_id 只能出现一次，且必须属于注册表或归档实验。"""
    known_ids = {definition.id for definition in list_experiments()}
    known_ids |= set(ARCHIVED_EXPERIMENTS)
    documents = _documents()
    assert documents, f"{_relative(DOCS_DIR)} 下没有实验说明文件"

    declared: dict[str, list[Path]] = defaultdict(list)
    problems: list[str] = []
    for path in documents:
        fields = _parse_frontmatter(_read_text(path))
        if fields is None:
            problems.append(
                f"{_relative(path)}: 缺少合法 frontmatter"
                "（文件开头没有 --- 围栏，或围栏内不是键值映射）"
            )
            continue
        document_id = fields.get("experiment_id", "")
        if not document_id:
            problems.append(f"{_relative(path)}: frontmatter 没有 experiment_id")
            continue
        if document_id not in known_ids:
            problems.append(
                f"{_relative(path)}: experiment_id {document_id!r} 不属于注册表"
                "或归档实验，疑似写错的 id"
            )
        declared[document_id].append(path)

    for document_id, paths in declared.items():
        if len(paths) > 1:
            problems.append(
                f"{document_id} 被多篇说明同时声明："
                + "、".join(_relative(path) for path in paths)
            )

    if problems:
        pytest.fail("实验说明 experiment_id 存在重复或未知取值：\n" + "\n".join(problems))


def test_archived_experiments_declare_no_documentation() -> None:
    """归档卡片只保留卡片，documentation 必须为空串，避免生成错误的 README 链接。"""
    assert ARCHIVED_EXPERIMENTS, "归档实验清单为空，无法校验归档卡片"
    problems = [
        f"{experiment_id}: documentation 应为空串，实际为 {card.documentation!r}"
        for experiment_id, card in ARCHIVED_EXPERIMENTS.items()
        if card.documentation != ""
    ]
    assert not problems, "归档实验误声明了说明正文：\n" + "\n".join(problems)


def test_module_readme_matches_generator() -> None:
    """每个已注册实验模块目录的 README.md 必须与生成器输出逐字节一致。"""
    entries = plan(ROOT)
    experiments = list_experiments()
    assert len(entries) == len(experiments), (
        f"模块 README 覆盖 {len(entries)} 个目录，注册实验 {len(experiments)} 个；"
        "注册实验与含 definition.py 的模块目录必须一一对应"
    )
    stale = [
        path for path, text in entries
        if not path.is_file() or path.read_bytes().replace(b"\r\n", b"\n") != text.encode("utf-8")
    ]
    if stale:
        pytest.fail(
            "以下模块 README.md 与实验定义不一致，请运行 python -m tools.gen_module_readme：\n"
            + "\n".join(_relative(path) for path in stale)
        )


def test_module_readme_points_at_declared_documentation() -> None:
    """模块 README 指针只引用该实验声明的正文，且每篇声明正文都有对应指针。"""
    pointers: dict[str, list[str]] = defaultdict(list)
    problems: list[str] = []
    for readme in sorted(MODULES_DIR.glob(f"*/{README_NAME}")):
        relative = _relative(readme)
        references = set(DOC_REFERENCE.findall(_read_text(readme)))
        if len(references) != 1:
            problems.append(f"{relative}: 应引用且只引用一篇说明正文，实际 {sorted(references)}")
            continue
        target = references.pop()
        if not (ROOT / target).is_file():
            problems.append(f"{relative}: 指向的正文 {target} 不存在")
        pointers[target].append(relative)
    declared = {definition.id: definition.documentation for definition in list_experiments()}
    for experiment_id, documentation in declared.items():
        if documentation not in pointers:
            problems.append(f"{experiment_id}: 声明的正文 {documentation} 没有模块 README 指针")
    for target, paths in pointers.items():
        if len(paths) > 1:
            problems.append(f"{target} 被多个模块 README 引用：{'、'.join(paths)}")
    if problems:
        pytest.fail("模块 README 指针与实验声明不一致：\n" + "\n".join(problems))
