"""按稳定实验 ID 发现采集回归与变更影响；不导入采集脚本或连接仪器。"""

from __future__ import annotations

import ast
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


COMMON_SCENARIOS = frozenset({
    "order", "slow_disk", "cancel", "acquire_failure", "restore_failure",
    "save_failure", "partial_save",
})
MODE_SCENARIOS = {
    "single": frozenset(),
    "grouped": frozenset({"group_change", "last_group"}),
    "paired": frozenset({"pair_second_failure"}),
    "mixed": frozenset({"group_change", "last_group", "pair_second_failure"}),
}


def repository_path(root: Path, relative: str) -> Path:
    """所有输入路径限制在仓库内，允许变更列表中的已删除文件。"""
    path = (root / relative).resolve()
    if path == root.resolve() or not path.is_relative_to(root.resolve()):
        raise ValueError(f"路径必须位于仓库内: {relative}")
    return path


def changed_files(root: Path, since: str = "HEAD") -> list[str]:
    """包括暂存、未暂存及未忽略的新文件；不依赖 pytest 的历史失败缓存。"""
    result = []
    for arguments in (
        ["diff", "--no-renames", "--name-only", "-z", since, "--"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ):
        process = subprocess.run(
            ["git", *arguments], cwd=root, capture_output=True, timeout=30, check=False,
        )
        if process.returncode:
            raise ValueError("无法读取 Git 变更；请提供 --changed-file 明确指定变更文件")
        result.extend(process.stdout.decode("utf-8").split("\0"))
    return sorted(set(result) - {""})


def discover_acquisition_cases(root: Path) -> dict[str, dict]:
    """从测试的字面量声明发现关联，不以任意测试文件存在作为业务证据。"""
    result: dict[str, dict] = {}
    for directory in ("lab_workflows", "experiments"):
        for path in sorted((root / directory).rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            declarations = [node.value for node in tree.body if isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == "ACQUISITION_CASES"
                                    for target in node.targets)]
            if not declarations:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                if len(declarations) != 1:
                    raise ValueError("ACQUISITION_CASES 必须只声明一次")
                claims = ast.literal_eval(declarations[0])
                if not isinstance(claims, dict):
                    raise ValueError("ACQUISITION_CASES 必须是字面量映射")
                tests = {node.name for node in tree.body
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name.startswith("test_")}
                tests.update(f"{node.name}::{method.name}" for node in tree.body
                             if isinstance(node, ast.ClassDef) for method in node.body
                             if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and method.name.startswith("test_"))
                for experiment_id, claim in claims.items():
                    if not isinstance(experiment_id, str) or not experiment_id or not isinstance(claim, dict):
                        raise ValueError("采集关联必须使用稳定实验 ID 和场景映射")
                    mode = claim.get("mode")
                    cases = claim.get("cases")
                    if mode not in MODE_SCENARIOS or not isinstance(cases, dict):
                        raise ValueError(f"{experiment_id}: mode 或 cases 无效")
                    entry = result.setdefault(experiment_id, {"mode": mode, "cases": {}, "sources": []})
                    if entry["mode"] != mode:
                        raise ValueError(f"{experiment_id}: 多份测试声明的采集模式不一致")
                    for scenario, selectors in cases.items():
                        if scenario not in COMMON_SCENARIOS | MODE_SCENARIOS["mixed"] | {"entrypoints"}:
                            raise ValueError(f"{experiment_id}: 未知场景 {scenario}")
                        if not isinstance(selectors, (list, tuple)) or not selectors:
                            raise ValueError(f"{experiment_id}.{scenario}: 必须关联具体测试")
                        for selector in selectors:
                            if not isinstance(selector, str) or selector not in tests:
                                raise ValueError(f"{experiment_id}: 测试节点不存在: {selector}")
                            entry["cases"].setdefault(scenario, []).append(f"{relative}::{selector}")
                    sources = claim.get("sources", [])
                    if not isinstance(sources, (list, tuple)) or any(not isinstance(p, str) for p in sources):
                        raise ValueError(f"{experiment_id}: sources 必须是源码路径列表")
                    for source in sources:
                        if not repository_path(root, source).is_file():
                            raise ValueError(f"{experiment_id}: 关联源码不存在: {source}")
                        entry["sources"].append(source)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{relative}: {exc}") from exc
    return result


def missing_scenarios(claim: dict | None) -> list[str]:
    if claim is None:
        return ["未关联真实业务测试"]
    required = COMMON_SCENARIOS | MODE_SCENARIOS[claim["mode"]]
    return sorted(required - claim["cases"].keys())


def verify_pytest_results(path: Path, nodeids: list[str]) -> int:
    """退出码为零也可能只执行了 skip/xfail，必须核对所选节点的实际结果。"""
    try:
        cases = ET.parse(path).findall(".//testcase")
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"业务回归缺少有效的 pytest 结果: {exc}") from exc
    if not cases or any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")):
        raise ValueError("业务回归含跳过、预期失败或未通过的测试，不能记为验收通过")
    for nodeid in nodeids:
        file, *parts = nodeid.split("::")
        classname = ".".join([*Path(file).with_suffix("").parts, *parts[:-1]])
        if not any(case.get("classname") == classname and
                   (case.get("name") == parts[-1] or any(
                       case.get("name", "").startswith(parts[-1] + suffix) for suffix in ("[", " ", "(")))
                   for case in cases):
            raise ValueError(f"业务回归未实际执行关联节点: {nodeid}")
    return len(cases)


def local_module_path(root: Path, module: str, known_files=()) -> Path | None:
    for base in (root, root / "src"):
        path = base.joinpath(*module.split("."))
        for candidate in (path.with_suffix(".py"), path / "__init__.py"):
            if candidate.is_file() or candidate in known_files:
                return candidate
    return None


def source_dependencies(root: Path, seeds: list[Path], cache: dict[Path, set[Path]], known_files=()) -> set[Path]:
    """保守追踪仓库内静态导入；动态入口用测试声明 sources 补充。"""
    seen: set[Path] = set()
    pending = list(seeds)
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if path not in cache:
            cache[path] = set()
            if not path.is_file():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            base = root / "src" if path.is_relative_to(root / "src") else root
            package = list(path.relative_to(base).parent.parts)
            modules = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    prefix = package[:len(package) - node.level + 1] if node.level else []
                    if node.module:
                        prefix += node.module.split(".")
                    modules.append(".".join(prefix))
                    modules.extend(".".join([*prefix, alias.name]) for alias in node.names if alias.name != "*")
            cache[path].update(candidate for module in modules
                               if module and (candidate := local_module_path(root, module, known_files)) is not None)
        pending.extend(cache[path] - seen)
    return seen


def affected_experiments(root: Path, definitions: list, changes: list[str], claims: dict) -> dict[str, dict]:
    """同时报告配置、分析与采集影响，只对采集业务变化要求完整时序覆盖。"""
    changed = {repository_path(root, path): path for path in changes}
    cache: dict[Path, set[Path]] = {}
    result = {}
    for definition in definitions:
        adapter = getattr(definition.experiment_runner, "__self__", None)
        acquisition = getattr(adapter, "acquisition_module", None)
        seed = local_module_path(root, acquisition, changed) if acquisition else root / definition.acquisition_program
        dependencies = source_dependencies(root, [seed] if seed else [], cache, changed)
        analysis_module = getattr(adapter, "analysis_module", None)
        analysis_seed = (local_module_path(root, analysis_module, changed) if analysis_module else
                         root / definition.analysis_program if definition.analysis_program else None)
        analysis_dependencies = source_dependencies(root, [analysis_seed] if analysis_seed else [], cache, changed)
        claim = claims.get(definition.id, {})
        dependencies.update(repository_path(root, path) for path in claim.get("sources", []))
        owned = {root / definition.acquisition_program}
        if definition.analysis_program:
            owned.add(root / definition.analysis_program)
        if acquisition and seed:
            owned.update(seed.parent.rglob("*.py"))
            owned.update(path for path in changed if path.is_relative_to(seed.parent))
        associated_tests = {repository_path(root, node.split("::")[0])
                            for nodes in claim.get("cases", {}).values() for node in nodes}
        matches = (dependencies | owned | associated_tests) & changed.keys()
        defaults = root / "params/experiments" / f"{definition.id}.yaml"
        if defaults in changed:
            matches.add(defaults)
        if matches:
            # 参数、说明元数据及测试自身的变化不等同于采集业务变化。
            acquisition_owned = {path for path in owned if seed and path.is_relative_to(seed.parent)
                                 and not path.name.startswith("analysis")
                                 and path not in analysis_dependencies - dependencies}
            acquisition_owned.add(root / definition.acquisition_program)
            runtime = {path for path in matches & (dependencies | acquisition_owned) if path.suffix == ".py"
                       and not path.name.startswith("test_")
                       and path.name not in {"models.py", "definition.py", "stages.py", "experiment_params.py"}}
            result[definition.id] = {"files": sorted(changed[path] for path in matches),
                                     "acquisition_changed": bool(runtime)}
    return result


def validation_plan(root: Path, definitions: list, changes: list[str], claims: dict,
                    requested_ids=(), required_ids=()) -> tuple[dict, list[str]]:
    """覆盖声明与执行结果分开；未修改实验缺少业务证据时仅报告缺口。"""
    from .acquisition_policy import ACQUISITION_EXCEPTIONS

    ids = {item.id for item in definitions}
    errors = [f"测试关联了未注册实验: {name}" for name in sorted(claims.keys() - ids)]
    errors.extend(f"实验未注册: {name}" for name in sorted(set(requested_ids) - ids))
    impacts = affected_experiments(root, definitions, changes, claims)
    selected = set(requested_ids) | impacts.keys()
    entries = []
    for definition in definitions:
        claim = claims.get(definition.id)
        impact = impacts.get(definition.id, {})
        required = (definition.id in required_ids or impact.get("acquisition_changed", False))
        required = required and definition.id not in ACQUISITION_EXCEPTIONS
        missing = missing_scenarios(claim)
        if required and missing:
            errors.append(f"{definition.id}: 采集业务验收缺少场景: {', '.join(missing)}")
        entries.append({
            "id": definition.id, "selected": definition.id in selected,
            "changed_files": impact.get("files", []), "acquisition_required": required,
            "exception": definition.id in ACQUISITION_EXCEPTIONS,
            "mode": claim["mode"] if claim else None,
            "cases": claim["cases"] if claim else {}, "missing_scenarios": missing,
            "acquisition_status": "not_run",
        })
    matched = {path for impact in impacts.values() for path in impact["files"]}
    return {"changed_files": changes, "unmapped_files": sorted(set(changes) - matched),
            "experiments": entries, "checks": [], "hardware_verified": False}, errors


def skill_differences(root: Path) -> list[str]:
    """.agents 是维护源；忽略 Python 运行缓存，比较全部受管理资源。"""
    directories = [root / owner / "skills/expcodegen" for owner in (".agents", ".claude")]
    files = []
    for directory in directories:
        files.append({path.relative_to(directory).as_posix(): path for path in directory.rglob("*")
                      if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"})
    return sorted(relative for relative in files[0].keys() | files[1].keys()
                  if relative not in files[0] or relative not in files[1]
                  or files[0][relative].read_bytes() != files[1][relative].read_bytes())
