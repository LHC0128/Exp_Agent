"""只读审计实验数据树：列出不该出现在 data/ 的代码与缺说明的研究目录。

data/ 只放数据；研究脚本放开发仓库 ``scripts/<data_type>/``（见 ``scripts/README.md``）。
本工具只遍历、不改写任何文件，默认打印清单并返回 0；``--strict`` 在有发现时返回 1。

用法：

    agent_exp_env\\Scripts\\python.exe -m tools.audit_data_tree
    agent_exp_env\\Scripts\\python.exe -m tools.audit_data_tree --root D:\\Code\\exp_agent_run\\data --json
    agent_exp_env\\Scripts\\python.exe -m tools.audit_data_tree --strict

数据根默认取 ``lab_workflows.common.data_root()``；开发目录未配置 ``LAB_DATA_ROOT`` 时读
``.lab_dev.yaml`` 的 ``data_root``。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
# 登记的例外：留在历史运行目录里的溯源脚本（把自身 SHA256 写进该运行的溯源记录，迁出会断链）。
EXCEPTIONS_FILE = ROOT / "scripts" / "data_tree_exceptions.txt"
CODE_SUFFIXES = (".py", ".ipynb")
CATEGORIES = {
    "code": "代码文件（应迁到 scripts/ 或删除）",
    "pycache": "__pycache__ 目录（有人在数据目录里直接 import 运行）",
    "study_without_readme": "数据类型下 _ 开头、缺 README.md 的研究目录",
}


@dataclass(frozen=True)
class Finding:
    category: str
    path: str  # 相对数据根，正斜杠


def load_exceptions(path: Path = EXCEPTIONS_FILE) -> frozenset[str]:
    """每行一个相对数据根的路径，``#`` 之后是原因；文件不存在时没有例外。"""
    if not path.is_file():
        return frozenset()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return frozenset(line.replace("\\", "/") for line in lines if line)


def audit(root: Path, exceptions: frozenset[str] = frozenset()) -> list[Finding]:
    """遍历数据根，按类别返回发现；不跟随目录联接，不进入 __pycache__，跳过登记的例外。"""
    findings: list[Finding] = []

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    for data_type in sorted(p for p in root.iterdir() if p.is_dir()):
        for study in sorted(p for p in data_type.iterdir() if p.is_dir() and p.name.startswith("_")
                           and p.name != "__pycache__"):
            if not (study / "README.md").is_file():
                findings.append(Finding("study_without_readme", rel(study)))
    for current, directories, files in os.walk(root):
        here = Path(current)
        if "__pycache__" in directories:
            findings.append(Finding("pycache", rel(here / "__pycache__")))
        directories[:] = sorted(d for d in directories if d != "__pycache__")
        findings.extend(Finding("code", rel(here / name)) for name in sorted(files)
                        if name.lower().endswith(CODE_SUFFIXES) and rel(here / name) not in exceptions)
    order = list(CATEGORIES)
    return sorted(findings, key=lambda f: (order.index(f.category), f.path))


def _default_root() -> Path:
    from lab_workflows.common import DataRootNotConfigured, data_root, load_yaml
    try:
        return data_root()
    except DataRootNotConfigured:
        config = ROOT / ".lab_dev.yaml"
        configured = load_yaml(config).get("data_root") if config.is_file() else None
        if not configured:
            raise
        return Path(str(configured))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, help="数据根目录；默认按 data_root() / .lab_dev.yaml")
    parser.add_argument("--json", action="store_true", help="输出 JSON 清单")
    parser.add_argument("--strict", action="store_true", help="有任何发现即返回 1")
    args = parser.parse_args(argv)
    root = args.root if args.root is not None else _default_root()
    if not root.is_dir():
        print(f"[audit] 数据根不存在: {root}", file=sys.stderr)
        return 2
    findings = audit(root, load_exceptions())
    if args.json:
        payload = {"root": str(root), "findings": [f.__dict__ for f in findings]}
        sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    else:
        lines = [f"[audit] 数据根 {root}"]
        for category, title in CATEGORIES.items():
            items = [f.path for f in findings if f.category == category]
            lines.append(f"\n## {title}: {len(items)}")
            lines.extend(f"  {path}" for path in items)
        sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    return 1 if args.strict and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
