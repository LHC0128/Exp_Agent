"""README 正式实验清单生成块的守卫测试。

覆盖生成块与注册表一致（漂移即失败）、生成块覆盖且只覆盖全部正式实验、手工块中的
归档与 Notebook 入口未被吞掉或重复，以及文档链接取自 `definition.documentation`
而非扫描 `docs/experiments/`。声明为空、声明路径不存在、frontmatter 的
`experiment_id` 不匹配时生成器必须退出码 2 并定位到实验 id 与路径；这些用例全部
在 `tmp_path` 造最小 fixture，不读写仓库真实的 `docs/experiments/`。
"""

from __future__ import annotations

import shutil
from pathlib import Path
import re

import pytest

from lab_workflows.experiments import registry
from lab_workflows.experiments.contracts import ExperimentDefinition
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

# 临时 fixture 用的最小文档正文；`{experiment_id}` 由调用方填入。
DOC_STUB = "---\nexperiment_id: {experiment_id}\n---\n\n测试用说明正文。\n"
EMPTY_BLOCK_README = f"# 标题\n\n{gen.BEGIN_MARKER}\n\n{gen.END_MARKER}\n"


@pytest.fixture(scope="module")
def docs_tree(tmp_path_factory) -> Path:
    """完整复制正式文档目录的临时仓库根，供临时树用例复用。"""
    root = tmp_path_factory.mktemp("docs-tree")
    shutil.copytree(ROOT / "docs" / "experiments", root / "docs" / "experiments")
    return root


def readme_text() -> str:
    return gen.read_text(ROOT / "README.md")


def make_definition(
    experiment_id: str = "scope-capture",
    documentation: str = "docs/experiments/scope_capture.md",
) -> ExperimentDefinition:
    """造一个最小可渲染的假定义，字段与真实定义同构。"""
    return ExperimentDefinition(
        id=experiment_id,
        title="示波器采集",
        category="measurement",
        family="scope",
        variant="default",
        description="测试用最小定义。",
        data_type="scope_capture",
        required_devices=(),
        acquisition_program="experiments/Scope_Capture.py",
        documentation=documentation,
    )


def build_fixture_tree(root: Path, docs: dict[str, str]) -> None:
    """在临时仓库根造 README 标记块与 `docs/experiments/` 下的最小文档。"""
    docs_dir = root / "docs" / "experiments"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (docs_dir / name).write_text(text, encoding="utf-8", newline="")
    (root / "README.md").write_text(EMPTY_BLOCK_README, encoding="utf-8", newline="")


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
    expected = gen.build_block(registry.list_experiments(), ROOT)
    assert gen.extract_block(readme_text()) == expected


def test_generated_block_covers_every_registry_id_in_order():
    """生成块的文档链接须逐个对回定义的 documentation 声明，且顺序与注册表一致。"""
    covered = []
    for row in generated_rows(readme_text()):
        links = DOC_LINK.findall(row)
        assert len(links) == 1, f"行内文档链接数量不为 1: {row}"
        covered.append(links[0])
    assert covered == [
        definition.documentation for definition in registry.list_experiments()
    ]


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
    assert edited != gen.build_block(registry.list_experiments(), ROOT)


def test_declaration_is_the_link_target(tmp_path, monkeypatch):
    """正常渲染：行内链接直接取 definition.documentation，写回可重复且字节不变。"""
    definition = make_definition()
    build_fixture_tree(
        tmp_path, {"scope_capture.md": DOC_STUB.format(experiment_id="scope-capture")}
    )
    assert gen.build_rows([definition]) == [
        "| 示波器采集 | `experiments/Scope_Capture.py` | "
        "[实验说明](docs/experiments/scope_capture.md) |"
    ]
    block = gen.build_block([definition], tmp_path)
    assert block.split("\n")[0] == gen.BEGIN_MARKER
    assert block.split("\n")[-1] == gen.END_MARKER
    assert gen.extract_block(block) == block

    monkeypatch.setattr(gen.registry, "list_experiments", lambda: [definition])
    readme = tmp_path / "README.md"
    assert gen.run(tmp_path, write=True) == 0
    assert "[实验说明](docs/experiments/scope_capture.md)" in gen.extract_block(
        gen.read_text(readme)
    )
    first = readme.read_bytes()
    assert gen.run(tmp_path, write=True) == 0
    assert readme.read_bytes() == first


def test_empty_declaration_exits_nonzero_and_lists_ids(tmp_path, monkeypatch, capsys):
    """声明为空串时必须退出码 2，列出缺声明的 id，且不写入 README。"""
    declared = make_definition()
    gap = make_definition("mx-z-field-calibration", documentation="")
    build_fixture_tree(
        tmp_path, {"scope_capture.md": DOC_STUB.format(experiment_id="scope-capture")}
    )
    with pytest.raises(gen.MissingDocumentationError) as excinfo:
        gen.build_block([declared, gap], tmp_path)
    assert excinfo.value.experiment_ids == ["mx-z-field-calibration"]

    monkeypatch.setattr(gen.registry, "list_experiments", lambda: [declared, gap])
    before = (tmp_path / "README.md").read_bytes()
    assert gen.run(tmp_path, write=True) == 2
    assert (tmp_path / "README.md").read_bytes() == before, "校验失败时不得写入 README"
    stderr = capsys.readouterr().err
    assert "缺少 documentation 声明" in stderr
    assert "mx-z-field-calibration" in stderr
    assert "scope-capture" not in stderr


def test_declared_file_missing_exits_nonzero_with_id_and_path(
    tmp_path, monkeypatch, capsys
):
    """声明路径不存在时退出码 2，错误信息带实验 id 与声明路径。"""
    definition = make_definition(documentation="docs/experiments/typo.md")
    build_fixture_tree(
        tmp_path, {"scope_capture.md": DOC_STUB.format(experiment_id="scope-capture")}
    )
    with pytest.raises(gen.InvalidDocumentationError) as excinfo:
        gen.build_block([definition], tmp_path)
    message = str(excinfo.value)
    assert "scope-capture" in message
    assert "docs/experiments/typo.md" in message
    assert "文件不存在" in message

    monkeypatch.setattr(gen.registry, "list_experiments", lambda: [definition])
    assert gen.run(tmp_path, write=False) == 2
    stderr = capsys.readouterr().err
    assert "scope-capture -> docs/experiments/typo.md" in stderr


def test_frontmatter_id_mismatch_exits_nonzero_with_id_and_path(tmp_path, capsys):
    """frontmatter 的 experiment_id 与定义不一致时退出码 2，并给出双方取值。"""
    definition = make_definition()
    build_fixture_tree(
        tmp_path,
        {"scope_capture.md": DOC_STUB.format(experiment_id="other-experiment")},
    )
    with pytest.raises(gen.InvalidDocumentationError) as excinfo:
        gen.build_block([definition], tmp_path)
    message = str(excinfo.value)
    assert "scope-capture -> docs/experiments/scope_capture.md" in message
    assert "other-experiment" in message
    assert "不一致" in message


def test_missing_frontmatter_exits_nonzero_with_id_and_path(tmp_path):
    """声明文件没有 frontmatter 时同样视为声明无效，并说明 frontmatter 缺失。"""
    definition = make_definition()
    build_fixture_tree(tmp_path, {"scope_capture.md": "# 没有 frontmatter\n"})
    with pytest.raises(gen.InvalidDocumentationError) as excinfo:
        gen.build_block([definition], tmp_path)
    message = str(excinfo.value)
    assert "scope-capture -> docs/experiments/scope_capture.md" in message
    assert "缺失或为空" in message


def test_run_checks_declared_paths_in_temp_root(tmp_path, capsys):
    """临时根缺文档时退出码 2，错误信息按声明给 id -> 路径，而不是扫描 docs 的结果。"""
    (tmp_path / "docs" / "experiments").mkdir(parents=True)
    (tmp_path / "README.md").write_text(
        EMPTY_BLOCK_README, encoding="utf-8", newline=""
    )
    assert gen.run(tmp_path, write=False) == 2
    stderr = capsys.readouterr().err
    assert "文件不存在" in stderr
    first = registry.list_experiments()[0]
    assert f"{first.id} -> {first.documentation}" in stderr


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
