"""查看、对比与提升实验参数的本机覆盖层（``params/experiments/<id>.local.yaml``）。

默认参数分两层：基线 ``<id>.yaml`` 随代码进 Git，覆盖层只存本机调参差异。
本工具只操作这两个文件，不连接仪器，也不经手运行目录。

用法：

    agent_exp_env\\Scripts\\python.exe -m tools.params list
    agent_exp_env\\Scripts\\python.exe -m tools.params diff bb-duty-scan-burst
    agent_exp_env\\Scripts\\python.exe -m tools.params promote bb-duty-scan-burst --dry-run
    agent_exp_env\\Scripts\\python.exe -m tools.params promote bb-duty-scan-burst
    agent_exp_env\\Scripts\\python.exe -m tools.params discard bb-duty-scan-burst

``promote`` 把合并后的取值写回基线并删除覆盖层；基线的改动仍是工作区里的普通 diff，
提交后随下一次代码发布带到其他机器。``--force`` 跳过模型与安全限值校验。
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable, Mapping

from lab_workflows.common import find_project_root
from lab_workflows.experiments.param_layers import OVERRIDE_SUFFIX, ParamLayers

Validator = Callable[[Mapping[str, Any]], list[str]]


def experiments_dir(root: Path) -> Path:
    return root / "params" / "experiments"


def layers_for(root: Path, experiment_id: str) -> ParamLayers:
    return ParamLayers(experiments_dir(root) / f"{experiment_id}.yaml")


def list_layers(root: Path) -> list[ParamLayers]:
    """返回当前存在覆盖文件的所有实验，按实验 ID 排序。"""
    directory = experiments_dir(root)
    if not directory.is_dir():
        return []
    return [
        layers_for(root, path.name[: -len(OVERRIDE_SUFFIX)])
        for path in sorted(directory.glob(f"*{OVERRIDE_SUFFIX}"))
    ]


def _format(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _display_width(text: str) -> int:
    """按终端显示宽度计算，中文占两列。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _table(rows: list[tuple[str, str, str]]) -> list[str]:
    """把 (参数, 基线, 本机覆盖) 对齐成文本表格。"""
    header = ("参数", "基线", "本机覆盖")
    widths = [
        max(_display_width(row[index]) for row in (header, *rows)) for index in range(3)
    ]
    def line(cells: tuple[str, ...]) -> str:
        return "  " + "  ".join(
            cell + " " * (widths[index] - _display_width(cell))
            for index, cell in enumerate(cells)
        )
    return [line(header), *(line(row) for row in rows)]


def list_lines(root: Path) -> list[str]:
    layers = list_layers(root)
    if not layers:
        return ["没有实验使用本机覆盖层。"]
    lines = [f"共 {len(layers)} 个实验有本机覆盖："]
    for item in layers:
        keys = ", ".join(item.overridden_keys())
        lines.append(f"  {item.baseline.stem:<32} {len(item.overridden_keys())} 项  {keys}")
    return lines


def diff_lines(root: Path, experiment_id: str) -> list[str]:
    layers = layers_for(root, experiment_id)
    if not layers.baseline.exists() and not layers.override.exists():
        raise FileNotFoundError(f"找不到 {layers.baseline}，也没有覆盖层")
    baseline = layers.baseline_values()
    changed = layers.overridden_keys(baseline)
    if not changed:
        return [f"{experiment_id} 没有本机覆盖层。"]
    rows = [
        (key, _format(baseline.get(key, "—")), _format(value))
        for key, value in changed.items()
    ]
    lines = [f"{experiment_id} 的本机覆盖（{len(rows)} 项）", ""]
    lines.extend(_table(rows))
    if not layers.baseline.exists():
        lines.extend([
            "",
            "基线文件不存在：兼容脚本的基线来自脚本内字面量，覆盖层叠在它上面。",
        ])
    return lines


def promote(
    root: Path,
    experiment_id: str,
    *,
    dry_run: bool = False,
    validator: Validator | None = None,
) -> list[str]:
    layers = layers_for(root, experiment_id)
    override = layers.override_values()
    if not override:
        raise FileNotFoundError(f"{experiment_id} 没有本机覆盖层")
    baseline = layers.baseline_values()
    merged = layers.merged(baseline)
    changed = layers.overridden_keys(baseline)
    if not changed:
        layers.discard()
        return [f"{experiment_id} 的覆盖层与基线一致，已直接删除。"]
    if validator is not None:
        errors = validator(merged)
        if errors:
            raise ValueError("；".join(errors))
    if dry_run:
        rows = [
            (key, _format(baseline.get(key, "—")), _format(value))
            for key, value in changed.items()
        ]
        lines = [f"将把 {len(rows)} 项并入 {layers.baseline.name} 并删除覆盖层：", ""]
        lines.extend(_table(rows))
        return lines
    layers.promote(merged)
    return [
        f"已把 {len(changed)} 项并入基线 {layers.baseline.name}，覆盖层已删除。",
        f"基线改动仍在工作区：提交 {_relative(layers.baseline, root)} 后随下一次发布生效。",
    ]


def discard(root: Path, experiment_id: str) -> list[str]:
    layers = layers_for(root, experiment_id)
    if not layers.override.exists():
        raise FileNotFoundError(f"{experiment_id} 没有本机覆盖层")
    layers.discard()
    return [f"已删除 {layers.override.name}，{experiment_id} 恢复为基线默认值。"]


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _model_validator(root: Path, experiment_id: str) -> Validator:
    """按注册表的参数模型校验合并结果；未注册或没有参数模型的实验跳过校验。"""

    def check(values: Mapping[str, Any]) -> list[str]:
        from lab_workflows.experiments.registry import get_experiment

        definition = get_experiment(experiment_id)
        adapter = getattr(definition.defaults_saver, "__self__", None)
        params_type = getattr(adapter, "params_type", None)
        if params_type is None:
            return []
        params = params_type.from_external(dict(values))
        return list(params.validate(root))

    return check


def main(argv: list[str] | None = None) -> int:
    root = find_project_root()
    parser = argparse.ArgumentParser(
        prog="python -m tools.params",
        description="查看、对比与提升实验参数的本机覆盖层。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出有本机覆盖层的实验")
    for name, help_text in (("diff", "对比基线、覆盖与生效值"), ("discard", "删除覆盖层")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("experiment_id")
    promote_parser = commands.add_parser("promote", help="把覆盖层并入基线")
    promote_parser.add_argument("experiment_id")
    promote_parser.add_argument("--dry-run", action="store_true", help="只显示将要合并的内容")
    promote_parser.add_argument("--force", action="store_true", help="跳过模型与安全限值校验")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            lines = list_lines(root)
        elif args.command == "diff":
            lines = diff_lines(root, args.experiment_id)
        elif args.command == "discard":
            lines = discard(root, args.experiment_id)
        else:
            validator = None if args.force else _model_validator(root, args.experiment_id)
            lines = promote(
                root,
                args.experiment_id,
                dry_run=args.dry_run,
                validator=validator,
            )
    except (KeyError, FileNotFoundError, TypeError, ValueError) as exc:
        message = f"未注册的实验 {args.experiment_id}" if isinstance(exc, KeyError) else str(exc)
        print(f"错误：{message}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
