"""README 正式实验清单生成块的守卫测试。

覆盖三件事：生成块与注册表一致（漂移即失败）、生成块覆盖且只覆盖全部正式实验、
手工块中的归档与 Notebook 入口未被吞掉或重复。缺文档时生成器必须非零退出并列出缺口。
"""

from __future__ import annotations

import shutil
from pathlib import Path
import re

import pytest

from lab_workflows.experiments import registry
from tools import gen_experiment_readme as gen

ROOT = Path(__file__).resolve().parents[1]
DOC_LINK = re.compile(r"\[实验说明\]\(([^)]+)\)")

# 手工块必须保留的入口；有意增删归档实验或 Notebook 行时同步更新这两组路径。
REQUIRED_NOTEBOOKS = (
    "experiments/XY_Compensation_Calibration.ipynb",
    "experiments/XY_Channel_Calibration.ipynb",
    "experiments/XY_AM_Transfer.ipynb",
    "experiments/XY_MOD_ZeroOffset.ipynb",
    "experiments/XY_Output_Verification.ipynb",
    "experiments/MORS_feasibility_test.ipynb",
    "experiments/MORS_feasibility_test_v2.ipynb",
    "experiments/MORS_polarization_pulsed.ipynb",
    "experiments/RF_Field_Sensitivity.ipynb",
    "experiments/RF_Field_Sensitivity_AW.ipynb",
)
REQUIRED_ARCHIVED = (
    "old/experiments/experiments/Mx_Keithley_6221_Main_Field_Calibration.py",
    "old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py",
    "old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py",
    "old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity.py",
    "old/experiments/experiments/Mx_Main_Field_Noise_Spectrum.py",
    "old/experiments/experiments/Mx_Z_Noise_Spectrum.py",
    "old/experiments/experiments/RF_Field_Sensitivity_AW_R.py",
    "old/experiments/experiments/RF_Field_Sensitivity_AW_FreqSweep.py",
    "old/experiments/experiments/XY_DC_Voltage_Calibration.py",
)


@pytest.fixture(scope="module")
def docs_tree(tmp_path_factory) -> Path:
    """完整复制正式文档目录的临时仓库根，供临时树用例复用。"""
    root = tmp_path_factory.mktemp("docs-tree")
    shutil.copytree(ROOT / "docs" / "experiments", root / "docs" / "experiments")
    return root


def readme_text() -> str:
    return gen.read_text(ROOT / "README.md")


def doc_index() -> dict[str, str]:
    return gen.read_doc_index(ROOT / "docs" / "experiments")


def manual_rows(text: str) -> list[str]:
    """返回手工块中的表格数据行（去掉表头与分隔行）。"""
    lines = text.split("\n")
    end = lines.index(gen.END_MARKER)
    stop = next(
        (index for index in range(end + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    rows = [line for line in lines[end + 1 : stop] if line.startswith("|")]
    return rows[2:]


def generated_rows(text: str) -> list[str]:
    rows = gen.extract_block(text).split("\n")
    return [line for line in rows if line.startswith("|")][2:]


def test_generated_block_matches_registry():
    """README 生成块必须与注册表当前内容逐字一致。"""
    expected = gen.build_block(registry.list_experiments(), doc_index())
    assert gen.extract_block(readme_text()) == expected


def test_generated_block_covers_every_registry_id_in_order():
    """生成块的文档链接须逐个对回 registry 的稳定 ID，且顺序与注册表一致。"""
    path_to_id = {path: experiment_id for experiment_id, path in doc_index().items()}
    covered = []
    for row in generated_rows(readme_text()):
        links = DOC_LINK.findall(row)
        assert len(links) == 1, f"行内文档链接数量不为 1: {row}"
        assert links[0] in path_to_id, f"文档链接未归属任何 experiment_id: {links[0]}"
        covered.append(path_to_id[links[0]])
    assert covered == [definition.id for definition in registry.list_experiments()]


def test_generated_rows_reference_existing_files():
    """生成行提到的程序与文档都必须在仓库内真实存在，不允许空链接或陈旧路径。"""
    for row in generated_rows(readme_text()):
        for program in re.findall(r"`([^`]+)`", row):
            assert (ROOT / program).is_file(), f"程序路径不存在: {program}"
        for doc in DOC_LINK.findall(row):
            assert (ROOT / doc).is_file(), f"文档路径不存在: {doc}"


def test_manual_block_keeps_archived_and_notebook_entries():
    """手工块保留全部归档与 Notebook 入口，且不重复、不混入正式实验行。"""
    rows = manual_rows(readme_text())
    assert len(rows) == 12, "手工块行数变化，请确认归档/Notebook 入口未被丢失"
    assert len(rows) == len(set(rows)), "手工块存在重复行"
    joined = "\n".join(rows)
    for entry in (*REQUIRED_NOTEBOOKS, *REQUIRED_ARCHIVED):
        assert f"`{entry}`" in joined, f"手工块丢失入口: {entry}"
    formal = {
        definition.acquisition_program
        for definition in registry.list_experiments()
        if definition.acquisition_program
    }
    for row in rows:
        for program in re.findall(r"`([^`]+)`", row):
            assert program not in formal, f"正式实验入口不得留在手工块: {program}"


def test_block_comparison_detects_hand_edit():
    """手工删改生成块内的行必须能被比对发现，否则守卫形同虚设。"""
    text = readme_text()
    edited = "\n".join(
        line
        for line in gen.extract_block(text).split("\n")
        if "bell_bloom_z_field_calibration.md" not in line
    )
    assert edited != gen.build_block(registry.list_experiments(), doc_index())


def test_missing_marker_is_reported(tmp_path):
    with pytest.raises(ValueError, match="生成块起始标记"):
        gen.extract_block("# 只有标题\n")
    docs = tmp_path / "docs" / "experiments"
    docs.mkdir(parents=True)
    (tmp_path / "README.md").write_text("# 只有标题\n", encoding="utf-8")
    assert gen.run(tmp_path, write=False) == 2


def test_check_mode_reports_drift(docs_tree):
    """README 落后于注册表时 --check 必须非零退出。"""
    readme = docs_tree / "README.md"
    readme.write_text(readme_text(), encoding="utf-8", newline="")
    assert gen.run(docs_tree, write=False) == 0
    readme.write_text(
        gen.replace_block(readme_text(), "\n".join([gen.BEGIN_MARKER, "", gen.END_MARKER])),
        encoding="utf-8",
        newline="",
    )
    assert gen.run(docs_tree, write=False) == 1


def test_missing_documentation_exits_nonzero_and_lists_gaps(tmp_path, capsys):
    """正式实验缺 docs frontmatter 时必须非零退出并列出缺口，而不是生成空链接。"""
    docs = tmp_path / "docs" / "experiments"
    docs.mkdir(parents=True)
    (docs / "scope_capture.md").write_text(
        "---\nexperiment_id: scope-capture\n---\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        f"# 标题\n\n{gen.BEGIN_MARKER}\n\n{gen.END_MARKER}\n", encoding="utf-8"
    )
    assert gen.run(tmp_path, write=True) == 2
    stderr = capsys.readouterr().err
    assert "缺少 experiment_id 归属" in stderr
    assert "bell-bloom-z-field-calibration" in stderr
    assert "scope-capture" not in stderr


def test_generation_is_idempotent_in_temp_tree(tmp_path, docs_tree):
    """在临时树中补全生成块后，再跑一次必须字节不变且与仓库 README 一致。"""
    shutil.copytree(docs_tree / "docs" / "experiments", tmp_path / "docs" / "experiments")
    stale = gen.replace_block(
        readme_text(), "\n".join([gen.BEGIN_MARKER, "", gen.END_MARKER])
    )
    readme = tmp_path / "README.md"
    readme.write_text(stale, encoding="utf-8", newline="")
    assert gen.run(tmp_path, write=True) == 0
    first = readme.read_bytes()
    assert gen.run(tmp_path, write=True) == 0
    assert readme.read_bytes() == first
    assert gen.extract_block(first.decode("utf-8")) == gen.extract_block(readme_text())
