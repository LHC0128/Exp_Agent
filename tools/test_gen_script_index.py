"""scripts/ 索引生成器：约定校验、硬件标记、索引同步，以及本仓库 scripts/ 当前一致。"""

from __future__ import annotations

from pathlib import Path

from tools import gen_script_index as gen

HEADER = "# 研究脚本\n\n规则……\n\n"
STUDY_README = """---
title: π 宽度调查
conclusion: π 为 6.5–6.7 µs
data:
  - pi_width_scan/1117_175856_pi_width_scan
---

# π 宽度调查
"""


def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(root: Path) -> None:
    _write(root, "scripts/README.md", f"{HEADER}{gen.BEGIN_MARKER}\n{gen.END_MARKER}\n")
    _write(root, "scripts/rf_phase_response/compare_z_fine.py",
           '"""对比 Z 细扫各轮的最佳相位。"""\nimport numpy as np\n')
    _write(root, "scripts/pi_width_scan/20261118_pi_width_investigation/README.md", STUDY_README)
    _write(root, "scripts/pi_width_scan/20261118_pi_width_investigation/10_tail/capture.py",
           '"""采集。"""\nfrom oscilloscope import create_oscilloscope\n')
    _write(root, "scripts/_devices/gs200_probe.py", '"""GS200 探针。"""\nimport tools.lab_client\n')


GROUPS = {"rf_phase_response", "pi_width_scan"}


def _run(root: Path, *, check: bool, ignored=lambda root: []):
    return gen.run(root, check=check, groups=GROUPS, ignored=ignored)


def test_generate_then_check_passes_and_marks_hardware(tmp_path):
    _repo(tmp_path)
    assert _run(tmp_path, check=True)[0] == 1  # 索引落后
    code, messages = _run(tmp_path, check=False)
    assert code == 0, messages
    text = (tmp_path / "scripts/README.md").read_text(encoding="utf-8")
    assert text.startswith(HEADER)
    assert ("| pi_width_scan | [20261118_pi_width_investigation](pi_width_scan/20261118_pi_width_investigation/README.md)"
            " | π 为 6.5–6.7 µs | `pi_width_scan/1117_175856_pi_width_scan` | 需硬件 |") in text
    assert "| rf_phase_response | [compare_z_fine.py](rf_phase_response/compare_z_fine.py) | 对比 Z 细扫各轮的最佳相位。 | — | — |" in text
    assert "| _devices | [gs200_probe.py](_devices/gs200_probe.py) | GS200 探针。 | — | 需硬件 |" in text
    assert _run(tmp_path, check=True) == (0, [])


def test_check_reports_convention_violations(tmp_path):
    _repo(tmp_path)
    _write(tmp_path, "scripts/Unknown_Type/a.py", '"""x"""\n')
    _write(tmp_path, "scripts/rf_phase_response/no_doc.py", "x = 1\n")
    _write(tmp_path, "scripts/rf_phase_response/scan_notes/README.md", "# 无 frontmatter\n")
    _write(tmp_path, "scripts/rf_phase_response/stray.png", "png")
    code, messages = _run(tmp_path, check=True, ignored=lambda root: ["scripts/x/results/a.csv"])
    text = "\n".join(messages)
    assert code == 1
    for expected in ("scripts/Unknown_Type: 分组名", "no_doc.py: 缺模块 docstring",
                     "scan_notes: 研究目录名应为", "scan_notes/README.md: frontmatter 缺 title, conclusion, data",
                     "stray.png: 分组下只放", "scripts/x/results/a.csv: 被 .gitignore 忽略", "索引落后"):
        assert expected in text


def test_large_study_assets_only_warn(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setattr(gen, "STUDY_ASSET_BUDGET_BYTES", 10)
    _write(tmp_path, "scripts/pi_width_scan/20261118_pi_width_investigation/fig.png", "x" * 20)
    _run(tmp_path, check=False)
    code, messages = _run(tmp_path, check=True)
    assert code == 0 and any(m.startswith("[警告]") and "20261118_pi_width_investigation" in m for m in messages)


def test_repository_scripts_index_is_current():
    code, messages = gen.run(gen.ROOT, check=True)
    assert code == 0, "\n".join(messages)
