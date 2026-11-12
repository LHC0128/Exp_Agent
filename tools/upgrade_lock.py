"""Python 依赖升级助手：在 .cache 下的隔离环境里升级、生成候选锁文件，验证后再写回。

两步，均在开发目录（仓库根）执行，不触碰 agent_exp_env 与运行目录：

    # 1. 准备：新建隔离环境并升级（不带 --packages 表示按 pyproject 范围全部升到最新），
    #    pip check、生成候选锁、列出版本变化并跑固定快检
    python -m tools.upgrade_lock prepare [--packages numpy scipy]

    # 2. 人工验证通过后写回 constraints/win-py312.lock.txt，并同步 development.txt 的直接依赖
    python -m tools.upgrade_lock apply

之后按常规提交、发布；release_to_lab.ps1 发现 constraints/ 变化会停下等你授权在运行目录重装。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.metadata as metadata
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "constraints" / "win-py312.lock.txt"
DEVELOPMENT = ROOT / "constraints" / "development.txt"
WORK = ROOT / ".cache" / "upgrade-lock"
DEFAULT_ENV = WORK / "env"
CANDIDATE = WORK / "candidate.lock.txt"
PROJECT = "exp-agent"
RUNTIME_EXTRAS = ("runtime", "instruments", "analysis", "gui")
DEV_EXTRAS = ("dev",)
TARGET_PYTHON = "3.12"
TARGET_ENV = {"python_version": "3.12", "python_full_version": "3.12.4",
              "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"}
# 版本变化后需要连真实仪器确认的 SDK（单元测试只用假仪器，发现不了固件/服务端不匹配）。
HARDWARE_SDK_PREFIXES = ("zhinst", "pyvisa", "toptica", "pyserial")
SECTION_RUNTIME = "# --- runtime,instruments,analysis,gui"
SECTION_DEV = "# --- dev 增量"
LOCK_HEADER = """\
# -*- coding: utf-8 -*-
# Windows / Python 3.12 完整版本锁（含全部传递依赖），用作 pip 约束文件：
#   pip install -c constraints/win-py312.lock.txt -e ".[runtime,instruments,analysis,gui]"
# 约束只限定版本、不额外装包，同一文件也用于加装 dev。只适用于 Windows + Python 3.12；
# 3.10 / 3.11 仍用 development.txt。notebook extra（jupyter 依赖链）不在锁内，由 pip 自行解析。
# 更新方法：python -m tools.upgrade_lock prepare → 验证 → python -m tools.upgrade_lock apply（见 README）。
"""

Distribution = Callable[[str], metadata.Distribution]


def canonical(name: str) -> str:
    from packaging.utils import canonicalize_name
    return str(canonicalize_name(name))


# ---------------------------------------------------------------- 纯函数（可单测）

def dependency_closure(roots: Iterable[tuple[str, Iterable[str]]],
                       distribution: Distribution = metadata.distribution) -> dict[str, tuple[str, str]]:
    """从 (包名, extras) 出发收集已安装依赖闭包：规范名 -> (显示名, 版本)。按 TARGET_ENV 求值标记。"""
    from packaging.requirements import Requirement

    found: dict[str, tuple[str, str]] = {}
    visited: set[tuple[str, str]] = set()
    stack = [(name, tuple(extras)) for name, extras in roots]
    while stack:
        name, extras = stack.pop()
        key = canonical(name)
        dist = distribution(name)
        found.setdefault(key, (dist.metadata["Name"], dist.version))
        for extra in ("", *extras):
            if (key, extra) in visited:
                continue
            visited.add((key, extra))
            for text in dist.requires or []:
                requirement = Requirement(text)
                marker = requirement.marker
                if marker is not None and not marker.evaluate({**TARGET_ENV, "extra": extra}):
                    continue
                if marker is None and extra:
                    continue  # 无标记的依赖已在 extra="" 时加入
                stack.append((requirement.name, tuple(requirement.extras)))
    return found


def render_lock(runtime: dict[str, tuple[str, str]], full: dict[str, tuple[str, str]],
                source: str, date: str) -> str:
    """按「运行组合 / dev 增量」两段生成锁文件文本；项目自身不入锁。"""
    def lines(entries: dict[str, tuple[str, str]]) -> list[str]:
        return [f"{name}=={version}" for key, (name, version)
                in sorted(entries.items(), key=lambda item: item[1][0].lower()) if key != PROJECT]

    dev_only = {key: value for key, value in full.items() if key not in runtime}
    return "\n".join([
        LOCK_HEADER,
        f"{SECTION_RUNTIME}（来源：{source}，{date}）---",
        *lines(runtime),
        "",
        f"{SECTION_DEV}（来源：{source}，{date}；其余依赖已在上段）---",
        *lines(dev_only),
        "",
    ])


def parse_pins(text: str) -> dict[str, tuple[str, str]]:
    pins = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if "==" in line:
            name, version = (part.strip() for part in line.split("==", 1))
            pins[canonical(name)] = (name, version)
    return pins


def diff_pins(old: dict[str, tuple[str, str]], new: dict[str, tuple[str, str]]) -> dict[str, list[str]]:
    changed = [f"{new[key][0]}: {old[key][1]} -> {new[key][1]}"
               for key in sorted(old.keys() & new.keys()) if old[key][1] != new[key][1]]
    added = [f"{new[key][0]}=={new[key][1]}" for key in sorted(new.keys() - old.keys())]
    removed = [f"{old[key][0]}=={old[key][1]}" for key in sorted(old.keys() - new.keys())]
    return {"changed": changed, "added": added, "removed": removed}


def sync_development(text: str, pins: dict[str, tuple[str, str]]) -> str:
    """把 development.txt 中对目标 Python 生效的 `==` 固定改为锁内版本；范围与其他版本行保持不变。"""
    from packaging.requirements import Requirement

    out = []
    for raw in text.splitlines(keepends=True):
        body = raw.split("#", 1)[0].strip()
        if body:
            requirement = Requirement(body)
            key = canonical(requirement.name)
            specs = list(requirement.specifier)
            applies = requirement.marker is None or requirement.marker.evaluate(TARGET_ENV)
            if applies and key in pins and len(specs) == 1 and specs[0].operator == "==":
                raw = raw.replace(f"=={specs[0].version}", f"=={pins[key][1]}", 1)
        out.append(raw)
    return "".join(out)


def hardware_sdk_changes(diff: dict[str, list[str]]) -> list[str]:
    entries = diff["changed"] + diff["added"] + diff["removed"]
    return [entry for entry in entries if entry.lower().startswith(HARDWARE_SDK_PREFIXES)]


# ---------------------------------------------------------------- 子命令

def _env_python(env: Path) -> Path:
    return env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str | Path], **kwargs) -> None:
    print("==>", " ".join(str(part) for part in command), flush=True)
    result = subprocess.run([str(part) for part in command], cwd=ROOT, **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {result.returncode}），已停止；修复后重新运行 prepare。")


def _extras(*groups: tuple[str, ...]) -> str:
    return ",".join(extra for group in groups for extra in group)


def render_command(args: argparse.Namespace) -> int:
    """在升级环境内部运行：读取已安装元数据生成候选锁。"""
    runtime = dependency_closure([(PROJECT, RUNTIME_EXTRAS)])
    full = dependency_closure([(PROJECT, RUNTIME_EXTRAS + DEV_EXTRAS)])
    installed = {canonical(dist.metadata["Name"]) for dist in metadata.distributions()}
    stray = sorted(installed - set(full) - {"pip", "setuptools", "wheel"})
    if stray:
        print("警告：环境中有不属于依赖闭包的包（不会写入锁）：" + ", ".join(stray))
    text = render_lock(runtime, full, args.source, _dt.date.today().isoformat())
    Path(args.out).write_text(text, encoding="utf-8")
    return 0


def prepare_command(args: argparse.Namespace) -> int:
    env = Path(args.env).resolve()
    if WORK not in env.parents:
        raise SystemExit(f"升级环境必须位于 {WORK} 下：{env}")
    if env.exists():
        print(f"==> 删除旧的升级环境 {env}")
        shutil.rmtree(env)
    WORK.mkdir(parents=True, exist_ok=True)
    _run([args.python, "-m", "venv", env])
    python = _env_python(env)
    version = subprocess.run([str(python), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    if version != TARGET_PYTHON:
        raise SystemExit(f"锁文件针对 Python {TARGET_PYTHON}，当前解释器为 {version}；用 --python 指定 3.12。")

    spec = f".[{_extras(RUNTIME_EXTRAS, DEV_EXTRAS)}]"
    pip = [python, "-m", "pip", "--disable-pip-version-check"]
    if args.packages:
        _run(pip + ["install", "-c", LOCK, "-e", spec])
        _run(pip + ["install", "-U", *args.packages])
    else:
        _run(pip + ["install", "-U", "-e", spec])
    _run(pip + ["check"])
    _run([python, "-m", "tools.upgrade_lock", "_render", "--out", CANDIDATE,
          "--source", "tools.upgrade_lock 升级环境"])

    diff = diff_pins(parse_pins(LOCK.read_text(encoding="utf-8")),
                     parse_pins(CANDIDATE.read_text(encoding="utf-8")))
    print("\n版本变化（当前锁 -> 候选锁）：")
    for label, title in (("changed", "升级/降级"), ("added", "新增"), ("removed", "移除")):
        for entry in diff[label]:
            print(f"  [{title}] {entry}")
    if not any(diff.values()):
        print("  无变化：当前锁已是 pyproject 范围内的最新版本。")
        return 0

    print("\n==> 用升级环境跑固定快检（无硬件）", flush=True)
    check = subprocess.run([str(python), "-m", "tools.check", "--report",
                            str(WORK / "check.json")], cwd=ROOT)
    sdk = hardware_sdk_changes(diff)
    print("\n结果：")
    print(f"  候选锁：{CANDIDATE}")
    print(f"  快检：{'通过' if check.returncode == 0 else f'失败（退出码 {check.returncode}），不要 apply'}")
    if sdk:
        print("  以下仪器 SDK 有变化，apply 前需在升级环境中连真实仪器确认：")
        for entry in sdk:
            print(f"    {entry}")
    print("  验证通过后执行：python -m tools.upgrade_lock apply")
    return check.returncode


def apply_command(args: argparse.Namespace) -> int:
    if not CANDIDATE.is_file():
        raise SystemExit("没有候选锁文件，请先运行 python -m tools.upgrade_lock prepare。")
    text = CANDIDATE.read_text(encoding="utf-8")
    pins = parse_pins(text)
    LOCK.write_text(text, encoding="utf-8")
    DEVELOPMENT.write_text(sync_development(DEVELOPMENT.read_text(encoding="utf-8"), pins), encoding="utf-8")
    print(f"已写入 {LOCK.relative_to(ROOT)}，并同步 {DEVELOPMENT.relative_to(ROOT)} 的直接依赖版本。")
    print("下一步：python -m pytest tools/test_dependency_lock.py，确认后提交，再用 release_to_lab.ps1 发布。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.upgrade_lock", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="新建隔离环境、升级并生成候选锁")
    prepare.add_argument("--packages", nargs="+", default=[],
                         help="只升级这些包（其余保持锁内版本）；省略则全部升到 pyproject 范围内最新")
    prepare.add_argument("--env", default=str(DEFAULT_ENV), help="升级环境目录，须在 .cache/upgrade-lock 下")
    prepare.add_argument("--python", default=getattr(sys, "_base_executable", sys.executable),
                         help="创建环境用的 Python 3.12 解释器")
    prepare.set_defaults(handler=prepare_command)
    apply = commands.add_parser("apply", help="把候选锁写回 constraints/")
    apply.set_defaults(handler=apply_command)
    render = commands.add_parser("_render", help=argparse.SUPPRESS)
    render.add_argument("--out", required=True)
    render.add_argument("--source", required=True)
    render.set_defaults(handler=render_command)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
