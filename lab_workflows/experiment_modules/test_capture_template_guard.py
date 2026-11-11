"""防止分析测试每例重跑完整假仪器采集：只做静态扫描，不导入或运行实验。

分析测试多数只是“篡改输入后看分析是否拒绝”，不需要各自重新采集。推荐写法见
`mx_z_optimal_control_rf_sensitivity/test_analysis.py` 的 `captured_template` + `run_dir`：
模块内只采集一次作只读模板，每例 `shutil.copytree` 到自己的 `tmp_path` 并校验模板摘要；
真实采集、安全收尾、取消路径仍由各模块 `test_workflow.py` 覆盖，不受影响。
"""

import ast
from pathlib import Path

import pytest

MODULES = Path(__file__).resolve().parent
# 新文件允许的用例体内采集次数上限（小于它的零星用法不强制迁移）。
NEW_FILE_LIMIT = 3
# 尚未迁移的存量文件及其当前次数。改动这些文件的采集调用时测试会失败，提醒顺手迁移；
# 迁移完成后把该行删除，而不是改大数字。
PENDING_MIGRATION = {
    "bell_bloom_z_field_calibration/test_analysis.py": 12,
    "mx_main_field_calibration/test_analysis.py": 11,
    "mx_y_rf_sensitivity/test_analysis_stages.py": 7,
    "mx_z_field_calibration/test_analysis.py": 13,
}


def acquisitions_in_tests(path):
    """统计 test_* 函数体内的 workflow.run(...) 调用次数（夹具中的调用不计）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "workflow"
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and function.name.startswith("test_")
        for node in ast.walk(function)
    )


def analysis_test_files():
    return {path.relative_to(MODULES).as_posix(): path for path in sorted(MODULES.glob("*/test_analysis*.py"))}


@pytest.mark.parametrize("name", sorted(analysis_test_files()))
def test_analysis_tests_reuse_one_capture_instead_of_reacquiring_per_test(name):
    count = acquisitions_in_tests(analysis_test_files()[name])
    if name in PENDING_MIGRATION:
        assert count == PENDING_MIGRATION[name], (
            f"{name} 的用例体内采集次数由 {PENDING_MIGRATION[name]} 变为 {count}。改动这个文件时请顺手迁移到"
            "模块级采集模板（写法见本文件说明），完成后从 PENDING_MIGRATION 删除该行；不要只改数字。")
    else:
        assert count < NEW_FILE_LIMIT, (
            f"{name} 在 {count} 个位置于用例体内调用 workflow.run，会让每例重跑完整采集。"
            "请改用模块级采集模板（写法见本文件说明）。")


def test_pending_migration_list_only_names_existing_files():
    files = analysis_test_files()
    assert set(PENDING_MIGRATION) <= set(files), sorted(set(PENDING_MIGRATION) - set(files))
