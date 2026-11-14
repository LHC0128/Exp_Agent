"""为正式实验模块目录生成 `README.md` 指针文件。

每个 `lab_workflows/experiment_modules/<模块>/` 的 README 记录实验标题、说明正文路径、
采集与分析入口、默认参数文件和相关仪器，让进入模块目录的读者知道行为改动必须同步哪篇正文。
模块目录名与实验 id 不总相同，因此 id、入口和设备一律取自该目录 `definition.py` 的
`DEFINITION`；只处理 id 已在实验注册表中注册的目录。

用法：

    python -m tools.gen_module_readme
    python -m tools.gen_module_readme --check

仓库内日常解释器为 `agent_exp_env\\Scripts\\python.exe`。`--check` 只比对不写入：
README 落后于实验定义时非零退出并列出待更新文件。写入为覆盖写，内容一致时不改动文件。
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys

from lab_workflows.experiments import registry
from lab_workflows.experiments.contracts import ExperimentDefinition

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = Path("lab_workflows") / "experiment_modules"
MODULES_PACKAGE = "lab_workflows.experiment_modules"
README_NAME = "README.md"
HEADER = "<!-- 由 python -m tools.gen_module_readme 生成，勿手改 -->"
NO_ANALYSIS = "未单独提供"
NO_DEVICES = "未声明"
NEWLINE = "\n"
ENCODING = "utf-8"


def render(definition: ExperimentDefinition) -> str:
    """按固定模板渲染一个模块 README 全文，以 LF 结尾。"""
    analysis = definition.analysis_program or NO_ANALYSIS
    devices = "、".join(definition.required_devices) or NO_DEVICES
    lines = [
        HEADER,
        "",
        f"# {definition.title}",
        "",
        f"实验 ID：`{definition.id}`",
        "",
        f"正文：`{definition.documentation}`",
        "",
        "本目录任何改变实验行为的修改，必须在同一轮更新上述正文；改动类型与对应章节见",
        "`.agents/skills/expcodegen/references/experiment-documentation.md`。",
        "",
        "## 入口",
        f"- 采集：`{definition.acquisition_program}`",
        f"- 分析：`{analysis}`",
        f"- 默认参数：`params/experiments/{definition.id}.yaml`",
        "",
        "## 相关仪器",
        f"设备：{devices}。型号说明与协议见 `docs/instruments/`。",
    ]
    return NEWLINE.join(lines) + NEWLINE


def load_definition(module_dir: Path) -> ExperimentDefinition:
    """导入模块目录的 `definition.py`，返回其中的 `DEFINITION`。"""
    module = importlib.import_module(f"{MODULES_PACKAGE}.{module_dir.name}.definition")
    return module.DEFINITION


def plan(root: Path = ROOT) -> list[tuple[Path, str]]:
    """返回每个已注册实验模块目录的 README 路径与应写入内容，按目录名排序。"""
    root = root.resolve()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    registered = {definition.id for definition in registry.list_experiments()}
    entries: list[tuple[Path, str]] = []
    for definition_file in sorted((root / MODULES_DIR).glob("*/definition.py")):
        definition = load_definition(definition_file.parent)
        if definition.id not in registered:
            continue
        entries.append((definition_file.parent / README_NAME, render(definition)))
    return entries


def display_path(root: Path, path: Path) -> str:
    """报错时统一用仓库根相对的 POSIX 路径。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def normalized_bytes(path: Path) -> bytes:
    """比较时忽略换行风格：Windows 检出可能按 autocrlf 写出 CRLF。"""
    return path.read_bytes().replace(b"\r\n", b"\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="为正式实验模块目录生成 README.md 指针文件。"
    )
    parser.add_argument("--check", action="store_true", help="只比对，不写入文件。")
    parser.add_argument("--root", type=Path, default=ROOT, help="仓库根目录。")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    entries = plan(root)

    stale = [
        (path, text)
        for path, text in entries
        if not path.is_file() or normalized_bytes(path) != text.encode(ENCODING)
    ]
    if args.check:
        if stale:
            print(
                f"{len(stale)} 个模块 README.md 落后于实验定义：",
                file=sys.stderr,
            )
            for path, _ in stale:
                print(f"  {display_path(root, path)}", file=sys.stderr)
            print(
                "运行 python -m tools.gen_module_readme 更新。", file=sys.stderr
            )
            return 1
        print(f"{len(entries)} 个模块 README.md 与实验定义一致。")
        return 0

    for path, text in stale:
        path.write_bytes(text.encode(ENCODING))
    if stale:
        print(f"已写入 {len(stale)} 个模块 README.md（共 {len(entries)} 个）。")
    else:
        print(f"{len(entries)} 个模块 README.md 已是最新。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
