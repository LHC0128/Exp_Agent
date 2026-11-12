"""Windows / Python 3.12 锁文件与 pyproject、development.txt 的一致性契约；只读文件，不安装包。"""

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

tomllib = pytest.importorskip("tomllib")

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "constraints" / "win-py312.lock.txt"
DEVELOPMENT = ROOT / "constraints" / "development.txt"
LOCKED_EXTRAS = ("runtime", "instruments", "analysis", "gui", "dev")
# 锁定目标平台；development.txt 的版本标记按此求值。
TARGET_ENV = {"python_version": "3.12", "python_full_version": "3.12.4",
              "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"}


def _requirement_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _lock_pins() -> dict[str, str]:
    pins = {}
    for line in _requirement_lines(LOCK):
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", line)
        assert match, f"锁文件只允许无标记的 name==version：{line}"
        name = canonicalize_name(match.group(1))
        assert name not in pins, f"锁文件重复条目：{name}"
        pins[name] = match.group(2)
    return pins


def _direct_requirements() -> list[Requirement]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    texts = list(project["dependencies"])
    for extra in LOCKED_EXTRAS:
        texts.extend(project["optional-dependencies"][extra])
    return [Requirement(text) for text in texts]


def test_lock_pins_every_direct_dependency_within_pyproject_range():
    pins = _lock_pins()
    for requirement in _direct_requirements():
        name = canonicalize_name(requirement.name)
        assert name in pins, f"锁文件缺少直接依赖：{requirement}"
        assert requirement.specifier.contains(pins[name], prereleases=True), (
            f"{name}=={pins[name]} 不满足 pyproject 范围 {requirement.specifier}")


def test_lock_matches_development_pins_for_target_python():
    pins = _lock_pins()
    for line in _requirement_lines(DEVELOPMENT):
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate(TARGET_ENV):
            continue
        name = canonicalize_name(requirement.name)
        assert name in pins, f"锁文件缺少 development.txt 中的 {name}"
        assert requirement.specifier.contains(pins[name], prereleases=True), (
            f"{name}：锁文件 {pins[name]} 与 development.txt {requirement.specifier} 不一致")
