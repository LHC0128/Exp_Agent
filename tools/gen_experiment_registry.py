"""由实验模块生成注册表的导入块与 `_DEFINITIONS` 顺序块。

顺序的唯一来源是 `lab_workflows/experiments/registry_order.txt`（一行一个稳定 ID，
顺序即 GUI 展示顺序）；本脚本扫 `lab_workflows/experiment_modules/*/definition.py`，
导入每个模块取 `DEFINITION.id`，按顺序文件重写 `registry.py` 两个标记之间的内容：

    # <registry:imports>
    ...
    # </registry:imports>

    # <registry:definitions>
    _DEFINITIONS = [
        <NAME>,  # <稳定 ID>
    ]
    # </registry:definitions>

`_DEFINITIONS` 每行的尾注是稳定 ID，静态架构测试据此把「ID 顺序」与「模块目录」对齐，
不必导入实验模块。标记之外的正文（`_REGISTRY`、查询函数）仍手工维护。

用法：

    agent_exp_env\\Scripts\\python.exe -m tools.gen_experiment_registry
    agent_exp_env\\Scripts\\python.exe -m tools.gen_experiment_registry --check

校验或写入失败一律非零退出，不静默跳过：

- 顺序文件中的 ID 在磁盘上找不到 `definition.py`（归档或改名时不得无声丢实验）；
- 两个实验目录产出同一个稳定 ID；
- 任一 `definition.py` 导入失败或未导出 `DEFINITION`；
- 顺序文件为空、重复 ID 或缺失；
- `registry.py` 缺少生成块标记。

磁盘上新增、顺序文件里还没有的 ID 按 ID 排序追加到末尾并回写顺序文件。
连续运行两次，`registry.py` 与 `registry_order.txt` 字节不变。
"""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
MODULES_RELATIVE = Path("lab_workflows/experiment_modules")
REGISTRY_RELATIVE = Path("lab_workflows/experiments/registry.py")
ORDER_RELATIVE = Path("lab_workflows/experiments/registry_order.txt")
IMPORTS_BEGIN = "# <registry:imports>"
IMPORTS_END = "# </registry:imports>"
DEFINITIONS_BEGIN = "# <registry:definitions>"
DEFINITIONS_END = "# </registry:definitions>"
LINE_LIMIT = 100

# 这五个模块的常量名早于「大写下划线由目录名派生」的规则，保持原名以免改动既有符号。
LEGACY_ALIASES = {
    "rf_sensitivity_constxy_frequency": "RF_CONSTXY_FREQUENCY_DEFINITION",
    "rf_sensitivity_aw_known_noise": "RF_KNOWN_NOISE_DEFINITION",
    "rf_control_scale_sensitivity": "RF_CONTROL_SCALE_DEFINITION",
    "rf_sensitivity_direct_aw_frequency": "RF_DIRECT_AW_FREQUENCY_DEFINITION",
    "xy_direct_aw_dc_calibration": "XY_DIRECT_AW_DC_DEFINITION",
}


class RegistryError(RuntimeError):
    """顺序文件、模块导入或生成块标记不满足生成前提。"""


def read_text(path: Path) -> str:
    """按 UTF-8 读取，并把 CRLF 归一化为 LF。"""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def newline_of(path: Path) -> str:
    """返回该文件使用的换行序列，用于原样写回。"""
    return "\r\n" if b"\r\n" in path.read_bytes() else "\n"


def write_text(path: Path, text: str) -> None:
    """按文件原有换行风格写回。"""
    path.write_bytes(text.replace("\n", newline_of(path)).encode("utf-8"))


def verify_package_root(root: Path) -> None:
    """确认待导入的 `lab_workflows` 就是该仓库树内的包，避免拿别的检出生成注册表。"""
    package = import_module("lab_workflows")
    locations = [Path(entry).resolve() for entry in getattr(package, "__path__", ())]
    if not locations or locations[0] != (root / "lab_workflows").resolve():
        raise RegistryError(f"待导入的 lab_workflows 不在 {root} 内，请在该仓库根运行生成器")


def module_paths(root: Path) -> list[Path]:
    """返回所有带 `definition.py` 的实验模块定义文件，按目录名排序。"""
    modules = root / MODULES_RELATIVE
    if not modules.is_dir():
        raise RegistryError(f"实验模块目录不存在: {modules}")
    paths = sorted((path for path in modules.glob("*/definition.py")), key=lambda path: path.parent.name)
    if not paths:
        raise RegistryError(f"{modules} 下没有 definition.py，拒绝空生成")
    return paths


def import_definition(root: Path, path: Path):
    """导入单个模块的 `DEFINITION`；导入失败或未导出时报错，不跳过。"""
    relative = path.relative_to(root).as_posix()
    module_name = f"lab_workflows.experiment_modules.{path.parent.name}.definition"
    try:
        module = import_module(module_name)
    except Exception as exc:
        raise RegistryError(f"导入 {relative} 失败: {type(exc).__name__}: {exc}") from exc
    definition = getattr(module, "DEFINITION", None)
    if definition is None:
        raise RegistryError(f"{relative} 未导出 DEFINITION")
    return definition


def discover(root: Path) -> dict[str, str]:
    """返回 `稳定 ID -> 模块目录名`；同一 ID 出现在两个目录时报错。"""
    discovered: dict[str, str] = {}
    for path in module_paths(root):
        directory = path.parent.name
        experiment_id = getattr(import_definition(root, path), "id", None)
        if not isinstance(experiment_id, str) or not experiment_id:
            raise RegistryError(f"{path.relative_to(root).as_posix()} 的 DEFINITION.id 不是非空字符串")
        if experiment_id in discovered:
            raise RegistryError(
                f"两个实验目录产出了同一 ID {experiment_id}: {discovered[experiment_id]} 与 {directory}"
            )
        discovered[experiment_id] = directory
    return discovered


def read_order(root: Path) -> list[str]:
    """读取顺序文件的稳定 ID 列表；空行、重复 ID 与缺失文件都报错。"""
    path = root / ORDER_RELATIVE
    if not path.is_file():
        raise RegistryError(f"缺少顺序文件 {ORDER_RELATIVE.as_posix()}，请从版本库恢复")
    lines = read_text(path).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    order: list[str] = []
    for number, line in enumerate(lines, 1):
        value = line.strip()
        if not value:
            raise RegistryError(f"{ORDER_RELATIVE.as_posix()} 第 {number} 行为空；一行只能有一个稳定 ID")
        if value in order:
            raise RegistryError(f"{ORDER_RELATIVE.as_posix()} 第 {number} 行重复 ID: {value}")
        order.append(value)
    if not order:
        raise RegistryError(f"{ORDER_RELATIVE.as_posix()} 没有任何 ID")
    return order


def resolve_order(order: list[str], discovered: dict[str, str]) -> tuple[list[str], list[str]]:
    """校验顺序文件与磁盘模块的对应关系，返回最终顺序与追加到末尾的新 ID。"""
    missing = [experiment_id for experiment_id in order if experiment_id not in discovered]
    if missing:
        raise RegistryError(
            "顺序文件中的 ID 在 lab_workflows/experiment_modules/*/definition.py 中不存在: "
            + "、".join(missing)
            + "；归档或改名时须显式从顺序文件移除"
        )
    known = set(order)
    appended = sorted(experiment_id for experiment_id in discovered if experiment_id not in known)
    return [*order, *appended], appended


def alias_of(directory: str) -> str:
    """返回 `_DEFINITIONS` 中的常量名；既有特例保持原名。"""
    return LEGACY_ALIASES.get(directory, f"{directory.upper()}_DEFINITION")


def import_lines(directory: str) -> list[str]:
    """渲染一行导入；超过行长上限时按黑式括号换行。"""
    alias = alias_of(directory)
    single = f"from ..experiment_modules.{directory}.definition import DEFINITION as {alias}"
    if len(single) <= LINE_LIMIT:
        return [single]
    return [
        f"from ..experiment_modules.{directory}.definition import (",
        f"    DEFINITION as {alias},",
        ")",
    ]


def build_blocks(entries: list[tuple[str, str]]) -> tuple[str, str]:
    """按 `(稳定 ID, 模块目录名)` 顺序渲染导入块与定义块（含标记）。"""
    imports = [
        IMPORTS_BEGIN,
        *(line for _, directory in entries for line in import_lines(directory)),
        IMPORTS_END,
    ]
    definitions = [
        DEFINITIONS_BEGIN,
        "_DEFINITIONS = [",
        *(f"    {alias_of(directory)},  # {experiment_id}" for experiment_id, directory in entries),
        "]",
        DEFINITIONS_END,
    ]
    return "\n".join(imports), "\n".join(definitions)


def block_span(text: str, begin: str, end: str) -> tuple[int, int]:
    """返回生成块起止标记的行号。"""
    lines = text.split("\n")
    try:
        start = lines.index(begin)
    except ValueError as exc:
        raise RegistryError(f"registry.py 缺少生成块起始标记: {begin}") from exc
    try:
        stop = lines.index(end, start)
    except ValueError as exc:
        raise RegistryError(f"registry.py 缺少生成块结束标记: {end}") from exc
    return start, stop


def replace_block(text: str, begin: str, end: str, block: str) -> str:
    """用新的生成块替换旧块（含标记行），其余行保持不变。"""
    start, stop = block_span(text, begin, end)
    lines = text.split("\n")
    return "\n".join([*lines[:start], *block.split("\n"), *lines[stop + 1 :]])


def render(root: Path) -> tuple[list[str], list[str], str, str]:
    """返回 `(最终顺序, 追加的新 ID, 新 registry.py 正文, 新顺序文件正文)`。"""
    verify_package_root(root)
    discovered = discover(root)
    order = read_order(root)
    ordered, appended = resolve_order(order, discovered)
    imports_block, definitions_block = build_blocks(
        [(experiment_id, discovered[experiment_id]) for experiment_id in ordered]
    )
    text = read_text(root / REGISTRY_RELATIVE)
    updated = replace_block(
        replace_block(text, IMPORTS_BEGIN, IMPORTS_END, imports_block),
        DEFINITIONS_BEGIN,
        DEFINITIONS_END,
        definitions_block,
    )
    return ordered, appended, updated, "\n".join(ordered) + "\n"


def run(root: Path, *, write: bool) -> int:
    """校验或重写注册表与顺序文件；返回进程退出码。0 一致，1 漂移，2 生成前提不成立。"""
    try:
        ordered, appended, updated, order_text = render(root)
        registry_path = root / REGISTRY_RELATIVE
        order_path = root / ORDER_RELATIVE
        registry_drift = read_text(registry_path) != updated
        order_drift = read_text(order_path) != order_text
    except (RegistryError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if not (registry_drift or order_drift):
        print(f"注册表与顺序文件已是最新（{len(ordered)} 个实验）。")
        return 0
    if not write:
        if appended:
            print(
                f"{ORDER_RELATIVE.as_posix()} 落后于磁盘模块，需追加: " + "、".join(appended),
                file=sys.stderr,
            )
        print(
            f"注册表与顺序文件不一致（{len(ordered)} 个实验）："
            "运行 python -m tools.gen_experiment_registry 更新。",
            file=sys.stderr,
        )
        return 1
    if order_drift:
        write_text(order_path, order_text)
    if registry_drift:
        write_text(registry_path, updated)
    if appended:
        print(f"已追加 {len(appended)} 个实验到顺序文件末尾: " + "、".join(appended))
    print(f"已更新注册表与顺序文件（{len(ordered)} 个实验）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="由实验模块生成注册表的导入块与 _DEFINITIONS 顺序块。")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验注册表与顺序文件是否落后于实验模块，不写入文件。",
    )
    args = parser.parse_args(argv)
    return run(ROOT, write=not args.check)


if __name__ == "__main__":
    raise SystemExit(main())
