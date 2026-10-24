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


REGRESSION_KINDS = ("analysis", "parameters")


def discover_test_cases(root: Path) -> tuple[dict, dict]:
    """一次 AST 扫描发现采集与分析/参数关联；不导入测试或实验。"""
    acquisition: dict[str, dict] = {}
    regressions: dict[str, dict] = {}
    for directory in ("lab_workflows", "experiments"):
        for path in sorted((root / directory).rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            tests = {node.name for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")}
            tests.update(f"{node.name}::{method.name}" for node in tree.body
                         if isinstance(node, ast.ClassDef) for method in node.body
                         if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"))
            relative = path.relative_to(root).as_posix()
            try:
                for variable in ("ACQUISITION_CASES", "REGRESSION_CASES"):
                    declarations = [node.value for node in tree.body if isinstance(node, ast.Assign)
                                    and any(isinstance(target, ast.Name) and target.id == variable for target in node.targets)]
                    if not declarations:
                        continue
                    if len(declarations) != 1:
                        raise ValueError(f"{variable} 必须只声明一次")
                    claims = ast.literal_eval(declarations[0])
                    if not isinstance(claims, dict):
                        raise ValueError(f"{variable} 必须是字面量映射")
                    for experiment_id, claim in claims.items():
                        if not isinstance(experiment_id, str) or not experiment_id or not isinstance(claim, dict):
                            raise ValueError("测试关联必须使用稳定实验 ID 和映射")
                        if variable == "REGRESSION_CASES":
                            if not claim or set(claim) - set(REGRESSION_KINDS):
                                raise ValueError(f"{experiment_id}: 回归类别只允许 analysis/parameters")
                            for kind, details in claim.items():
                                if not isinstance(details, dict) or set(details) != {"sources", "tests"}:
                                    raise ValueError(f"{experiment_id}.{kind}: 必须声明 sources 和 tests")
                                sources, selectors = details["sources"], details["tests"]
                                if not isinstance(sources, (list, tuple)) or not sources or any(not isinstance(p, str) for p in sources):
                                    raise ValueError(f"{experiment_id}.{kind}: sources 必须是非空源码路径列表")
                                if not isinstance(selectors, (list, tuple)) or not selectors:
                                    raise ValueError(f"{experiment_id}.{kind}: 必须关联具体测试")
                                entry = regressions.setdefault(experiment_id, {}).setdefault(kind, {"sources": [], "nodes": []})
                                for source in sources:
                                    source_path = repository_path(root, source)
                                    if not source_path.is_file():
                                        raise ValueError(f"{experiment_id}: 关联源码不存在: {source}")
                                    entry["sources"].append(source_path.relative_to(root).as_posix())
                                for selector in selectors:
                                    if not isinstance(selector, str) or selector not in tests:
                                        raise ValueError(f"{experiment_id}: 测试节点不存在: {selector}")
                                    entry["nodes"].append(f"{relative}::{selector}")
                            continue
                        mode, cases = claim.get("mode"), claim.get("cases")
                        if mode not in MODE_SCENARIOS or not isinstance(cases, dict):
                            raise ValueError(f"{experiment_id}: mode 或 cases 无效")
                        entry = acquisition.setdefault(experiment_id, {"mode": mode, "cases": {}, "sources": []})
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
    return acquisition, regressions


def discover_acquisition_cases(root: Path) -> dict[str, dict]:
    """保留原有采集声明发现入口；主验证器使用一次扫描取得两类关联。"""
    return discover_test_cases(root)[0]


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
        module_parts = list(Path(file).with_suffix("").parts)
        classnames = {".".join([*module_parts, *parts[:-1]])}
        if module_parts[0] == "src":
            classnames.add(".".join([*module_parts[1:], *parts[:-1]]))
        if not parts:
            found = any(case.get("classname") == classname or
                        case.get("classname", "").startswith(classname + ".")
                        for case in cases for classname in classnames)
        else:
            found = any(case.get("classname") in classnames and
                        (case.get("name") == parts[-1] or any(
                            case.get("name", "").startswith(parts[-1] + suffix) for suffix in ("[", " ", "(")))
                        for case in cases)
        if not found:
            raise ValueError(f"业务回归未实际执行关联节点: {nodeid}")
    return len(cases)


def deduplicate_test_targets(targets) -> list[str]:
    """整文件已纳入时不再重复收集其中的具体节点。"""
    targets = set(targets)
    files = {target for target in targets if "::" not in target}
    return sorted(target for target in targets if "::" not in target or target.split("::")[0] not in files)


def canonical_test_targets(root: Path, targets) -> tuple[list[str], dict[str, str]]:
    """仅归并已确认覆盖双入口的 expcodegen 镜像脚手架测试。"""
    source = ".agents/skills/expcodegen/scripts/test_scaffold_experiment.py"
    mirror = ".claude/skills/expcodegen/scripts/test_scaffold_experiment.py"
    result, aliases = [], {}
    for target in targets:
        file, *parts = target.split("::")
        if file == mirror:
            if not (root / source).is_file() or not (root / mirror).is_file() or (root / source).read_bytes() != (root / mirror).read_bytes():
                raise ValueError("expcodegen 镜像测试与维护源不一致，请先同步；不能跳过镜像验证")
            mapped = "::".join([source, *parts])
            aliases[target] = mapped
            target = mapped
        result.append(target)
    return deduplicate_test_targets(result), aliases


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
        # 注册表是元数据目录：保留对它本身的依赖，不经目录查询扩散到其他实验实现。
        # 实验之间的真实共享导入和测试声明 sources 仍沿各自路径追踪。
        if path.suffix != ".py" or path == root / "lab_workflows/experiments/registry.py":
            continue
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


def affected_experiments(root: Path, definitions: list, changes: list[str], claims: dict,
                         regression_cases: dict | None = None) -> dict[str, dict]:
    """分别追踪采集、分析与参数影响；显式来源补足动态依赖。"""
    regression_cases = regression_cases or {}
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
        regressions = regression_cases.get(definition.id, {})
        regression_sources = {kind: {repository_path(root, path) for path in regressions.get(kind, {}).get("sources", [])}
                              for kind in REGRESSION_KINDS}
        regression_dependencies = {kind: source_dependencies(root, list(paths), cache, changed)
                                   for kind, paths in regression_sources.items()}
        dependencies.update(source_dependencies(root,
            [repository_path(root, path) for path in claim.get("sources", [])], cache, changed))
        owned = {root / definition.acquisition_program}
        if definition.analysis_program:
            owned.add(root / definition.analysis_program)
        if acquisition and seed:
            owned.update(seed.parent.rglob("*.py"))
            owned.update(path for path in changed if path.is_relative_to(seed.parent))
        associated_tests = {repository_path(root, node.split("::")[0])
                            for nodes in claim.get("cases", {}).values() for node in nodes}
        associated_tests.update(repository_path(root, node.split("::")[0])
                                for regression in regressions.values() for node in regression["nodes"])
        matches = (dependencies | analysis_dependencies | owned | associated_tests |
                   set().union(*regression_dependencies.values())) & changed.keys()
        defaults = root / "params/experiments" / f"{definition.id}.yaml"
        if defaults in changed:
            matches.add(defaults)
        if matches:
            # 参数、说明元数据及测试自身的变化不等同于采集业务变化。
            acquisition_owned = {path for path in owned if seed and path.is_relative_to(seed.parent)
                                 and not path.name.startswith("analysis")
                                 and path not in (analysis_dependencies | regression_dependencies["analysis"]) - dependencies}
            acquisition_owned.add(root / definition.acquisition_program)
            runtime = {path for path in matches & (dependencies | acquisition_owned) if path.suffix == ".py"
                       and not path.name.startswith("test_")
                       and path.name not in {"models.py", "definition.py", "stages.py", "experiment_params.py"}}
            analysis_owned = {path for path in owned if path.name.startswith("analysis")}
            if definition.analysis_program:
                analysis_owned.add(root / definition.analysis_program)
            analysis_runtime = {path for path in matches & (analysis_dependencies | analysis_owned | regression_dependencies["analysis"])
                                if path.suffix == ".py" and not path.name.startswith("test_")
                                and path.name not in {"models.py", "definition.py", "stages.py", "experiment_params.py"}}
            regression_changes = {
                "analysis": analysis_runtime | (matches & regression_sources["analysis"]),
                "parameters": (matches & regression_dependencies["parameters"]) |
                    {path for path in matches if path == defaults or path.name in {"models.py", "experiment_params.py"}},
            }
            result[definition.id] = {"files": sorted(changed[path] for path in matches),
                                     "acquisition_changed": bool(runtime),
                                     **{f"{kind}_changed": bool(paths) for kind, paths in regression_changes.items()},
                                     "regression_changes": {kind: {
                                         "changed_sources": sorted(changed[path] for path in paths),
                                         "uncovered_sources": sorted(changed[path] for path in paths - regression_dependencies[kind]),
                                     } for kind, paths in regression_changes.items()}}
    return result


def validation_plan(root: Path, definitions: list, changes: list[str], claims: dict,
                    requested_ids=(), required_ids=(), regression_cases: dict | None = None) -> tuple[dict, list[str]]:
    """覆盖声明与执行结果分开；未修改实验缺少业务证据时仅报告缺口。"""
    from .acquisition_policy import ACQUISITION_EXCEPTIONS

    regression_cases = regression_cases or {}
    ids = {item.id for item in definitions}
    errors = [f"测试关联了未注册实验: {name}" for name in sorted((claims.keys() | regression_cases.keys()) - ids)]
    errors.extend(f"实验未注册: {name}" for name in sorted((set(requested_ids) | set(required_ids)) - ids))
    impacts = affected_experiments(root, definitions, changes, claims, regression_cases)
    selected = set(requested_ids) | set(required_ids) | impacts.keys()
    entries = []
    for definition in definitions:
        claim = claims.get(definition.id)
        impact = impacts.get(definition.id, {})
        acquisition_selected = definition.id in required_ids or impact.get("acquisition_changed", False)
        required = acquisition_selected and definition.id not in ACQUISITION_EXCEPTIONS
        missing = missing_scenarios(claim)
        if required and missing:
            errors.append(f"{definition.id}: 采集业务验收缺少场景: {', '.join(missing)}")
        nodes = sorted({node for values in claim["cases"].values() for node in values}) if claim and acquisition_selected else []
        reason = ("显式要求完整采集验收" if definition.id in required_ids else
                  "采集业务或共享依赖变化" if acquisition_selected else
                  "非采集变更或仅指定实验；不自动运行完整采集回归" if definition.id in selected else
                  "本次未选中")
        regressions = {}
        for kind in REGRESSION_KINDS:
            needed = impact.get(f"{kind}_changed", False)
            regression_nodes = sorted(set(regression_cases.get(definition.id, {}).get(kind, {}).get("nodes", []))) if needed else []
            coverage = impact.get("regression_changes", {}).get(kind, {})
            uncovered = coverage.get("uncovered_sources", [])
            missing_coverage = needed and (not regression_nodes or bool(uncovered))
            if missing_coverage:
                detail = "缺少测试关联" if not regression_nodes else "已有测试关联未覆盖修改源码"
                errors.append(f"{definition.id}: 待验证，{kind} {detail}（REGRESSION_CASES）: {', '.join(uncovered)}")
            regressions[kind] = {"required": needed, "nodes": regression_nodes,
                                 "changed_sources": coverage.get("changed_sources", []), "uncovered_sources": uncovered,
                                 "status": "not_required" if not needed else "missing_mapping" if missing_coverage else "not_run"}
        entries.append({
            "id": definition.id, "selected": definition.id in selected,
            "changed_files": impact.get("files", []), "acquisition_required": required,
            "acquisition_reason": reason, "acquisition_test_nodes": nodes, "regressions": regressions,
            "exception": definition.id in ACQUISITION_EXCEPTIONS,
            "mode": claim["mode"] if claim else None,
            "cases": claim["cases"] if claim else {}, "missing_scenarios": missing,
            "acquisition_status": "not_run" if acquisition_selected else "not_required",
        })
    matched = {path for impact in impacts.values() for path in impact["files"]}
    changed_tests = sorted(path for path in set(changes)
                           if Path(path).name.startswith("test_") and Path(path).suffix == ".py"
                           and repository_path(root, path).is_file())
    nodes = {node for entry in entries for node in entry["acquisition_test_nodes"]}
    nodes.update(node for entry in entries for regression in entry["regressions"].values() for node in regression["nodes"])
    targets, aliases = canonical_test_targets(root, [*nodes, *changed_tests])
    return {"changed_files": changes, "unmapped_files": sorted(set(changes) - matched),
            "changed_test_files": changed_tests, "test_targets": targets, "test_target_aliases": aliases,
            "required_test_nodes": sorted(nodes),
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
