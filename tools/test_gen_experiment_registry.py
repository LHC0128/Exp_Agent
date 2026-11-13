"""注册表生成器的守卫测试。

在临时仓库树里跑真实的 `tools/gen_experiment_registry.py`（子进程，`tools/` 与
`lab_workflows/` 都是副本或假模块），覆盖幂等、新模块追加、顺序文件多出 ID、
重复 ID、导入失败与 `--check` 漂移；另有一条用例确认真实仓库与生成结果一致。

假模块只用 `SimpleNamespace`，不导入真实实验模块，也不连接硬件。
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import gen_experiment_registry as gen

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SKELETON = '''"""临时注册表骨架，只用于生成器测试。"""

from __future__ import annotations

# <registry:imports>
# </registry:imports>


# <registry:definitions>
_DEFINITIONS = [
]
# </registry:definitions>

_REGISTRY = {definition.id: definition for definition in _DEFINITIONS}
'''


def make_tree(root: Path, order: list[str], modules: dict[str, str]) -> Path:
    """建最小仓库树：生成器副本、注册表骨架、顺序文件与假实验模块。"""
    (root / "tools").mkdir(parents=True)
    (root / "tools/__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(ROOT / "tools/gen_experiment_registry.py", root / "tools/gen_experiment_registry.py")
    experiments = root / "lab_workflows/experiments"
    experiments.mkdir(parents=True)
    (root / "lab_workflows/__init__.py").write_text("", encoding="utf-8")
    (experiments / "__init__.py").write_text("", encoding="utf-8")
    (experiments / "registry.py").write_text(REGISTRY_SKELETON, encoding="utf-8", newline="\n")
    if order:
        (experiments / "registry_order.txt").write_text(
            "\n".join(order) + "\n", encoding="utf-8", newline="\n"
        )
    modules_root = root / "lab_workflows/experiment_modules"
    modules_root.mkdir(parents=True)
    (modules_root / "__init__.py").write_text("", encoding="utf-8")
    for directory, experiment_id in modules.items():
        package = modules_root / directory
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "definition.py").write_text(
            "from types import SimpleNamespace\n\n"
            f"DEFINITION = SimpleNamespace(id={experiment_id!r})\n",
            encoding="utf-8",
        )
    return root


def run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    """在临时树里跑生成器；去掉 PYTHONPATH，确保 `lab_workflows` 解析到该树。"""
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "tools.gen_experiment_registry", *arguments],
        cwd=root, capture_output=True, env=environment, check=False,
    )


def stderr_of(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode("utf-8", "replace")


def registry_text(root: Path) -> str:
    return (root / "lab_workflows/experiments/registry.py").read_text(encoding="utf-8")


def order_text(root: Path) -> str:
    return (root / "lab_workflows/experiments/registry_order.txt").read_text(encoding="utf-8")


def test_generation_is_idempotent_and_orders_by_order_file(tmp_path):
    """连续跑两次字节不变，且导入块与 `_DEFINITIONS` 都按顺序文件排列。"""
    root = make_tree(tmp_path, ["beta", "alpha"], {"a": "alpha", "b": "beta", "c": "gamma"})
    assert run_cli(root).returncode == 0
    registry = (root / "lab_workflows/experiments/registry.py").read_bytes()
    order = (root / "lab_workflows/experiments/registry_order.txt").read_bytes()
    assert order_text(root) == "beta\nalpha\ngamma\n"
    text = registry_text(root)
    assert text.index("b.definition") < text.index("a.definition") < text.index("c.definition")
    assert text.index("B_DEFINITION,  # beta") < text.index("A_DEFINITION,  # alpha") < \
        text.index("C_DEFINITION,  # gamma")
    assert run_cli(root).returncode == 0
    assert (root / "lab_workflows/experiments/registry.py").read_bytes() == registry
    assert (root / "lab_workflows/experiments/registry_order.txt").read_bytes() == order
    assert run_cli(root, "--check").returncode == 0


def test_new_module_is_appended_to_end(tmp_path):
    """磁盘上新增、顺序文件没有的 ID 按 ID 排序追加到末尾，不改动既有顺序。"""
    root = make_tree(tmp_path, ["zulu", "alpha"], {"a": "alpha", "z": "zulu"})
    assert run_cli(root).returncode == 0
    assert order_text(root) == "zulu\nalpha\n"
    make_tree_module = root / "lab_workflows/experiment_modules"
    for directory, experiment_id in (("m", "mike"), ("k", "kilo")):
        package = make_tree_module / directory
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "definition.py").write_text(
            "from types import SimpleNamespace\n\n"
            f"DEFINITION = SimpleNamespace(id={experiment_id!r})\n",
            encoding="utf-8",
        )
    assert run_cli(root, "--check").returncode == 1
    assert run_cli(root).returncode == 0
    assert order_text(root) == "zulu\nalpha\nkilo\nmike\n"
    text = registry_text(root)
    assert text.index("Z_DEFINITION,  # zulu") < text.index("A_DEFINITION,  # alpha") < \
        text.index("K_DEFINITION,  # kilo") < text.index("M_DEFINITION,  # mike")


def test_order_file_with_unknown_id_is_rejected(tmp_path):
    """顺序文件多出的 ID 必须报错退出，不静默丢弃，也不改写任何文件。"""
    root = make_tree(tmp_path, ["alpha", "ghost"], {"a": "alpha"})
    before = (root / "lab_workflows/experiments/registry.py").read_bytes(), order_text(root)
    result = run_cli(root)
    assert result.returncode == 2
    assert "ghost" in stderr_of(result) and "不存在" in stderr_of(result)
    assert ((root / "lab_workflows/experiments/registry.py").read_bytes(), order_text(root)) == before


def test_duplicate_ids_are_rejected(tmp_path):
    """两个目录产出同一 ID 时必须报错退出，不覆盖前一个模块。"""
    root = make_tree(tmp_path, ["dup"], {"one": "dup", "two": "dup"})
    result = run_cli(root)
    assert result.returncode == 2
    assert "同一 ID" in stderr_of(result) and "one" in stderr_of(result) and "two" in stderr_of(result)


def test_broken_module_import_is_rejected(tmp_path):
    """导入失败（半成品或语法错）必须报错退出，不静默跳过该模块。"""
    root = make_tree(tmp_path, ["alpha"], {"a": "alpha", "broken": "broken"})
    (root / "lab_workflows/experiment_modules/broken/definition.py").write_text(
        "DEFINITION = (\n", encoding="utf-8"
    )
    result = run_cli(root)
    assert result.returncode == 2
    assert "broken/definition.py" in stderr_of(result)


def test_check_reports_drift_without_writing(tmp_path):
    """`--check` 在漂移时非 0 且不写入；写入后恢复一致。"""
    root = make_tree(tmp_path, ["alpha", "beta"], {"a": "alpha", "b": "beta"})
    assert run_cli(root).returncode == 0
    registry = root / "lab_workflows/experiments/registry.py"
    drifted = registry_text(root).replace("    A_DEFINITION,  # alpha\n", "")
    registry.write_text(drifted, encoding="utf-8", newline="\n")
    result = run_cli(root, "--check")
    assert result.returncode == 1
    assert registry_text(root) == drifted
    assert run_cli(root).returncode == 0
    assert run_cli(root, "--check").returncode == 0


def test_missing_marker_is_reported():
    """`registry.py` 缺少生成块标记时报错，而不是把文件写坏。"""
    with pytest.raises(gen.RegistryError, match="起始标记"):
        gen.block_span('"""没有标记。"""\n', gen.IMPORTS_BEGIN, gen.IMPORTS_END)
    with pytest.raises(gen.RegistryError, match="结束标记"):
        gen.block_span(f"{gen.IMPORTS_BEGIN}\n", gen.IMPORTS_BEGIN, gen.IMPORTS_END)


def test_repository_registry_is_in_sync():
    """真实仓库必须与生成结果一致：顺序文件、条目尾注与导入的 DEFINITION.id 三处不漂移。"""
    assert gen.run(ROOT, write=False) == 0
