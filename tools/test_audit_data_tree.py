"""数据树审计：只读、分类正确、--strict 退出码。"""

from __future__ import annotations

import json
from pathlib import Path

from tools import audit_data_tree as audit_tool


def _tree(root: Path) -> dict[str, bytes]:
    files = {
        "pi_width_scan/1117_run/raw/scan.npz": b"npz",
        "pi_width_scan/1117_run/results/diag.py": b"print(1)\n",
        "pi_width_scan/_study_ok/README.md": b"# ok\n",
        "pi_width_scan/_study_ok/scripts/fit.py": b"x = 1\n",
        "pi_width_scan/_study_missing/fig.png": b"png",
        "pi_width_scan/__pycache__/x.cpython-310.pyc": b"pyc",
        "rf_phase_response/notebook.ipynb": b"{}",
    }
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    return {name: (root / name).read_bytes() for name in files}


def test_audit_classifies_code_pycache_and_studies_without_changing_files(tmp_path):
    before = _tree(tmp_path)
    findings = audit_tool.audit(tmp_path)
    assert [(f.category, f.path) for f in findings] == [
        ("code", "pi_width_scan/1117_run/results/diag.py"),
        ("code", "pi_width_scan/_study_ok/scripts/fit.py"),
        ("code", "rf_phase_response/notebook.ipynb"),
        ("pycache", "pi_width_scan/__pycache__"),
        ("study_without_readme", "pi_width_scan/_study_missing"),
    ]
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


def test_cli_reports_by_default_and_strict_fails_on_findings(tmp_path, capsysbinary):
    _tree(tmp_path)
    assert audit_tool.main(["--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert len(payload["findings"]) == 5
    assert audit_tool.main(["--root", str(tmp_path), "--strict"]) == 1
    assert "pi_width_scan/_study_missing" in capsysbinary.readouterr().out.decode("utf-8")


def test_registered_exceptions_are_skipped(tmp_path):
    _tree(tmp_path)
    listing = tmp_path / "exceptions.txt"
    listing.write_text("# 溯源脚本\npi_width_scan\\1117_run\\results\\diag.py  # 写入自身 SHA256\n\n", encoding="utf-8")
    exceptions = audit_tool.load_exceptions(listing)
    assert exceptions == {"pi_width_scan/1117_run/results/diag.py"}
    paths = [f.path for f in audit_tool.audit(tmp_path, exceptions)]
    assert "pi_width_scan/1117_run/results/diag.py" not in paths and len(paths) == 4
    assert audit_tool.load_exceptions(tmp_path / "absent.txt") == frozenset()


def test_cli_strict_passes_on_clean_tree_and_rejects_missing_root(tmp_path):
    (tmp_path / "pi_width_scan" / "1117_run" / "raw").mkdir(parents=True)
    assert audit_tool.main(["--root", str(tmp_path), "--strict"]) == 0
    assert audit_tool.main(["--root", str(tmp_path / "absent")]) == 2
