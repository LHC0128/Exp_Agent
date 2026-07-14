"""在隔离子进程中执行带参数覆盖和取消检查点的历史实验脚本。"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import Any


class OverrideTransformer(ast.NodeTransformer):
    """把 GUI 参数注入脚本中第一次实际执行的同名赋值。

    历史实验脚本会在运行过程中更新部分大写参数（例如相位校准后的
    ``XY_CTRL_PHASE``）。GUI 只应覆盖参数初值，不能把后续运行时更新再次
    改回表单值。条件分支中的参数则以第一个实际执行到的赋值作为初值。
    """

    def __init__(self, override_names: set[str]) -> None:
        self.override_names = override_names

    @staticmethod
    def _override_expr(name: str, original: ast.expr | None = None) -> ast.expr:
        override: ast.expr = ast.Subscript(
            value=ast.Name(id="__LAB_OVERRIDES", ctx=ast.Load()),
            slice=ast.Constant(value=name),
            ctx=ast.Load(),
        )
        if (
            isinstance(original, ast.Call)
            and isinstance(original.func, ast.Attribute)
            and isinstance(original.func.value, ast.Name)
            and original.func.attr in {"array", "arange", "linspace"}
        ):
            return ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id=original.func.value.id, ctx=ast.Load()),
                    attr="asarray",
                    ctx=ast.Load(),
                ),
                args=[override],
                keywords=[],
            )
        return override

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        node = self.generic_visit(node)
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in self.override_names:
                return self._override_once(node, name, node.value)
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        node = self.generic_visit(node)
        if (
            isinstance(node.target, ast.Name)
            and node.target.id in self.override_names
            and node.value is not None
        ):
            return self._override_once(node, node.target.id, node.value)
        return node

    def _override_once(
        self,
        node: ast.Assign | ast.AnnAssign,
        name: str,
        original: ast.expr,
    ) -> list[ast.stmt]:
        """首次执行同名赋值时使用 GUI 值，之后保留脚本原始赋值。"""
        node.value = ast.IfExp(
            test=ast.Compare(
                left=ast.Constant(value=name),
                ops=[ast.NotIn()],
                comparators=[
                    ast.Name(id="__LAB_OVERRIDE_APPLIED", ctx=ast.Load())
                ],
            ),
            body=self._override_expr(name, original),
            orelse=original,
        )
        mark_applied = ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(
                        id="__LAB_OVERRIDE_APPLIED", ctx=ast.Load()
                    ),
                    attr="add",
                    ctx=ast.Load(),
                ),
                args=[ast.Constant(value=name)],
                keywords=[],
            )
        )
        ast.copy_location(mark_applied, node)
        return [node, mark_applied]

    @staticmethod
    def _with_cancel_check(node: ast.stmt) -> ast.stmt:
        check = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="__lab_check_cancel", ctx=ast.Load()),
                args=[],
                keywords=[],
            )
        )
        node.body.insert(0, check)
        return node

    def visit_For(self, node: ast.For) -> ast.AST:
        node = self.generic_visit(node)
        return self._with_cancel_check(node)

    def visit_While(self, node: ast.While) -> ast.AST:
        node = self.generic_visit(node)
        return self._with_cancel_check(node)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--overrides", required=True)
    parser.add_argument("--cancel-file")
    args = parser.parse_args()

    script = Path(args.script).resolve()
    overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
    for name in ("DATA_DIR", "RUN_DIR"):
        if name in overrides and overrides[name] is not None:
            overrides[name] = Path(overrides[name]).resolve()
    cancel_file = Path(args.cancel_file).resolve() if args.cancel_file else None

    def check_cancel() -> None:
        if cancel_file and cancel_file.exists():
            raise KeyboardInterrupt("收到 GUI 安全停止请求")

    source = script.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(script))
    tree = OverrideTransformer(set(overrides)).visit(tree)
    ast.fix_missing_locations(tree)
    namespace = {
        "__name__": "__main__",
        "__file__": str(script),
        "__package__": None,
        "__LAB_OVERRIDES": overrides,
        "__LAB_OVERRIDE_APPLIED": set(),
        "__lab_check_cancel": check_cancel,
    }
    os.environ.setdefault("MPLBACKEND", "Agg")
    exec(compile(tree, str(script), "exec"), namespace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
