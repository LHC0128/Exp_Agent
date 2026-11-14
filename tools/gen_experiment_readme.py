"""由实验注册表生成 README 的正式实验清单块。

README.md 中「主要实验入口」的正式实验表格位于一对 HTML 注释标记之间，本脚本整体重写该块；
归档实验、Notebook 与未迁移入口的表格在标记之外，仍手工维护。

用法：

    agent_exp_env\\Scripts\\python.exe -m tools.gen_experiment_readme
    agent_exp_env\\Scripts\\python.exe -m tools.gen_experiment_readme --check

`--check` 只比对不写入：README 落后于注册表时非零退出。文档链接的唯一数据源是
`ExperimentDefinition.documentation`；声明为空、声明路径不存在或该文件 frontmatter 的
`experiment_id` 与定义不一致时同样非零退出并列出缺口，不生成空链接。
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from pathlib import Path
import re
import sys

from lab_workflows.experiments import registry
from lab_workflows.experiments.contracts import ExperimentDefinition

ROOT = Path(__file__).resolve().parent.parent
BEGIN_MARKER = (
    "<!-- BEGIN GENERATED EXPERIMENTS: 正式实验清单由 python -m tools.gen_experiment_readme "
    "生成，勿手改 -->"
)
END_MARKER = "<!-- END GENERATED EXPERIMENTS -->"
TABLE_HEADER = "| 方向 | 主要入口 | 相关文档 |"
TABLE_RULE = "|---|---|---|"
DOC_LINK_LABEL = "实验说明"

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


class MissingDocumentationError(RuntimeError):
    """正式实验未声明 `documentation`。"""

    def __init__(self, experiment_ids: Sequence[str]) -> None:
        self.experiment_ids = list(experiment_ids)
        super().__init__(
            "以下正式实验缺少 documentation 声明，请在 definition.py 中补文档路径: "
            + "、".join(self.experiment_ids)
        )


class InvalidDocumentationError(RuntimeError):
    """声明的文档路径不存在，或该文件 frontmatter 的 `experiment_id` 不匹配。"""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__(
            "以下实验的 documentation 声明无效，请修正 definition.py 或对应文档 frontmatter: "
            + "；".join(self.problems)
        )


def read_text(path: Path) -> str:
    """按文件自身的换行风格读取，返回以 LF 归一化的文本。"""
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n")


def newline_of(path: Path) -> str:
    """返回该文件使用的换行序列，用于原样写回。"""
    return "\r\n" if b"\r\n" in path.read_bytes() else "\n"


def write_text(path: Path, text: str) -> None:
    """按文件原有换行风格写回，避免整文件换行改写。"""
    path.write_bytes(text.replace("\n", newline_of(path)).encode("utf-8"))


def metadata_text(path: Path) -> str:
    """读取 frontmatter 用文本；容忍 UTF-8 BOM，带 BOM 的说明不会被误判为缺少 frontmatter。"""
    return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


def experiment_id_of(path: Path) -> str | None:
    """读取文档 frontmatter 的 `experiment_id`，缺失时返回 None。"""
    match = _FRONTMATTER.match(metadata_text(path))
    if match is None:
        return None
    for line in match.group(1).splitlines():
        if line.startswith("experiment_id:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            return value or None
    return None


def validate_declarations(
    definitions: Sequence[ExperimentDefinition], root: Path
) -> None:
    """校验 `documentation` 声明：非空、指向仓库根内真实文件、frontmatter 的 id 与定义一致。"""
    undeclared = [
        definition.id for definition in definitions if not definition.documentation
    ]
    if undeclared:
        raise MissingDocumentationError(undeclared)
    problems = []
    for definition in definitions:
        path = root / definition.documentation
        if not path.is_file():
            problems.append(f"{definition.id} -> {definition.documentation}：文件不存在")
            continue
        actual = experiment_id_of(path)
        if actual != definition.id:
            declared = actual or "缺失或为空"
            problems.append(
                f"{definition.id} -> {definition.documentation}："
                f"frontmatter experiment_id 为 {declared}，与定义不一致"
            )
    if problems:
        raise InvalidDocumentationError(problems)


def build_rows(definitions: Iterable[ExperimentDefinition]) -> list[str]:
    """按给定顺序渲染表格行；调用方负责先确认文档声明有效。"""
    rows = []
    for definition in definitions:
        programs = "、".join(
            f"`{program}`"
            for program in (definition.acquisition_program, definition.analysis_program)
            if program
        )
        rows.append(
            f"| {definition.title} | {programs} | "
            f"[{DOC_LINK_LABEL}]({definition.documentation}) |"
        )
    return rows


def build_block(definitions: Sequence[ExperimentDefinition], root: Path) -> str:
    """渲染生成块全文，含起止标记；声明缺失或无效时抛异常。"""
    validate_declarations(definitions, root)
    return "\n".join(
        [
            BEGIN_MARKER,
            "",
            TABLE_HEADER,
            TABLE_RULE,
            *build_rows(definitions),
            "",
            END_MARKER,
        ]
    )


def block_span(text: str) -> tuple[int, int]:
    """返回生成块起止标记的行号。"""
    lines = text.split("\n")
    try:
        start = lines.index(BEGIN_MARKER)
    except ValueError as exc:
        raise ValueError(f"README.md 缺少生成块起始标记: {BEGIN_MARKER}") from exc
    try:
        end = lines.index(END_MARKER, start)
    except ValueError as exc:
        raise ValueError(f"README.md 缺少生成块结束标记: {END_MARKER}") from exc
    return start, end


def extract_block(text: str) -> str:
    """取出 README 当前的生成块全文。"""
    start, end = block_span(text)
    return "\n".join(text.split("\n")[start : end + 1])


def replace_block(text: str, block: str) -> str:
    """用新的生成块替换 README 中的旧块，其余行保持不变。"""
    start, end = block_span(text)
    lines = text.split("\n")
    return "\n".join([*lines[:start], *block.split("\n"), *lines[end + 1 :]])


def run(root: Path, *, write: bool) -> int:
    """校验或重写 `root/README.md`；返回进程退出码。"""
    readme_path = root / "README.md"
    text = read_text(readme_path)
    definitions = registry.list_experiments()
    try:
        block = build_block(definitions, root)
        current = extract_block(text)
    except (MissingDocumentationError, InvalidDocumentationError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if current == block:
        print(f"README 正式实验清单已是最新（{len(definitions)} 个实验）。")
        return 0
    if not write:
        print(
            f"README 正式实验清单与注册表不一致（{len(definitions)} 个实验）："
            "运行 python -m tools.gen_experiment_readme 更新。",
            file=sys.stderr,
        )
        return 1
    write_text(readme_path, replace_block(text, block))
    print(f"已更新 README 正式实验清单（{len(definitions)} 个实验）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="由实验注册表生成 README 的正式实验清单块。"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验 README 是否与注册表一致，不写入文件。",
    )
    args = parser.parse_args(argv)
    return run(ROOT, write=not args.check)


if __name__ == "__main__":
    raise SystemExit(main())
