"""以注册表为范围的静态边界回归；不导入实验模块，不连接硬件。

历史依赖只允许减少。冻结值经本轮修改前源码副本核对并扣除已消除的债务，
不在测试运行时从当前代码重新生成。动态工厂、容器传播和反射仍须代码审查。
"""
from __future__ import annotations

import ast
from collections import Counter
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "lab_workflows.experiment_modules."

# 与改前副本逐项核对，再扣除本轮已消除的债务；不在运行时重新生成。
_MX = "mx_z_optimal_control_rf_sensitivity.workflow.Workflow"
_MX_ANALYSIS = "mx_z_optimal_control_xy_rf_sensitivity.analysis.AnalysisWorkflow"
_DIRECT = "rf_sensitivity_direct_aw.workflow.SingleControlWorkflow"
_DIRECT_ANALYSIS = "rf_sensitivity_direct_aw.analysis.AnalysisWorkflow"
_SCOPE = "z_aw_waveform_scope_check.workflow.Workflow"


def _legacy_edge(child: str, parent: str) -> tuple[str, str]:
    return PREFIX + child, PREFIX + parent


def _legacy_profile(child: str, parent: str) -> str:
    return f"{PREFIX}{child} -> {PREFIX}{parent}"


LEGACY_EXECUTOR_BASES = frozenset({
    _legacy_edge("mx_z_optimal_control_dg4000_bias_xyz_balance.analysis.AnalysisWorkflow", _MX_ANALYSIS),
    _legacy_edge("mx_z_optimal_control_dg4000_bias_xyz_balance.workflow.Workflow", _MX),
    _legacy_edge("mx_z_optimal_control_xy_leakage_response.analysis.AnalysisWorkflow", _MX_ANALYSIS),
    _legacy_edge("mx_z_optimal_control_xy_leakage_response.workflow.Workflow", _MX),
    _legacy_edge("mx_z_optimal_control_xy_noise_spectrum.analysis.AnalysisWorkflow", _MX_ANALYSIS),
    _legacy_edge("mx_z_optimal_control_xy_noise_spectrum.workflow.Workflow", _MX),
    _legacy_edge("mx_z_optimal_control_xy_rf_phase_response.analysis.AnalysisWorkflow", _MX_ANALYSIS),
    _legacy_edge("mx_z_optimal_control_xy_rf_phase_response.workflow.Workflow", _MX),
    _legacy_edge("mx_z_optimal_control_xy_rf_sensitivity.workflow.Workflow", _MX),
    _legacy_edge("mx_z_optimal_control_xyz_balance.analysis.AnalysisWorkflow", _MX_ANALYSIS),
    _legacy_edge("mx_z_optimal_control_xyz_balance.workflow.Workflow", _MX),
    _legacy_edge("noise_spectrum_xy_known_noise.workflow.Workflow", "noise_spectrum_xy.workflow.Workflow"),
    _legacy_edge("noise_spectrum_xy_probe_am.workflow.Workflow", "noise_spectrum_xy.workflow.Workflow"),
    _legacy_edge("rf_control_scale_sensitivity.analysis.AnalysisWorkflow", _DIRECT_ANALYSIS),
    _legacy_edge("rf_control_scale_sensitivity.workflow.ControlWorkflow", _DIRECT),
    _legacy_edge("rf_control_scale_sensitivity.workflow.Workflow", _DIRECT),
    _legacy_edge("rf_sensitivity_aw_known_noise.analysis.AnalysisWorkflow", _DIRECT_ANALYSIS),
    _legacy_edge("rf_sensitivity_aw_known_noise.workflow.ControlWorkflow", _DIRECT),
    _legacy_edge("rf_sensitivity_aw_known_noise.workflow.Workflow", _DIRECT),
    _legacy_edge("z_aw_closed_loop_waveform_correction.workflow.Workflow", _SCOPE),
    _legacy_edge("z_aw_current_waveform_scope_check.analysis.AnalysisWorkflow", _MX_ANALYSIS),
    _legacy_edge("z_aw_current_waveform_scope_check.workflow.Workflow", _SCOPE),
    _legacy_edge("z_coil_current_frequency_response.workflow.Workflow",
        "z_coil_inductance_frequency_response.workflow.Workflow"),
})
_ANALYSIS_FIELDS = dict.fromkeys(
    "config config_path fingerprint options results run_dir state_path states summary".split(), 1) | {"revision": 2}
_MX_FIELDS = dict.fromkeys((
    "channels closed config_revision current_step demod_mode device_session devices directory "
    "executed_source mapping outputs params progress quality_error response_parameters revision "
    "root round_sealed run_parameters session_id shutdown_parameters state states").split(), 1)
LEGACY_CONSTRUCTOR_WRITES = {
    _legacy_profile("mx_z_optimal_control_dg4000_bias_xyz_balance.analysis.AnalysisWorkflow", _MX_ANALYSIS):
        dict(_ANALYSIS_FIELDS),
    _legacy_profile("mx_z_optimal_control_dg4000_bias_xyz_balance.workflow.Workflow", _MX): dict(_MX_FIELDS),
    _legacy_profile("mx_z_optimal_control_xy_leakage_response.analysis.AnalysisWorkflow", _MX_ANALYSIS):
        dict(_ANALYSIS_FIELDS),
    _legacy_profile("mx_z_optimal_control_xy_leakage_response.workflow.Workflow", _MX):
        _MX_FIELDS | {"main_field_state": 1},
    _legacy_profile("mx_z_optimal_control_xy_noise_spectrum.analysis.AnalysisWorkflow", _MX_ANALYSIS):
        _ANALYSIS_FIELDS | {"options": 3},
    _legacy_profile("mx_z_optimal_control_xy_rf_phase_response.analysis.AnalysisWorkflow", _MX_ANALYSIS):
        _ANALYSIS_FIELDS | {"options": 3},
    _legacy_profile("mx_z_optimal_control_xy_rf_phase_response.workflow.Workflow", _MX):
        _MX_FIELDS | {"main_field_state": 1},
    _legacy_profile("mx_z_optimal_control_xyz_balance.analysis.AnalysisWorkflow", _MX_ANALYSIS):
        dict(_ANALYSIS_FIELDS),
    _legacy_profile("mx_z_optimal_control_xyz_balance.workflow.Workflow", _MX):
        _MX_FIELDS | {"main_field_state": 1},
    _legacy_profile("noise_spectrum_xy_probe_am.workflow.Workflow", "noise_spectrum_xy.workflow.Workflow"):
        {"outputs": 1},
    _legacy_profile("rf_control_scale_sensitivity.analysis.AnalysisWorkflow", _DIRECT_ANALYSIS):
        {"config": 1, "options": 3},
    _legacy_profile("rf_control_scale_sensitivity.workflow.Workflow", _DIRECT): {"state": 1},
    _legacy_profile("z_aw_current_waveform_scope_check.analysis.AnalysisWorkflow", _MX_ANALYSIS):
        _ANALYSIS_FIELDS | {"summary": 2},
}
LEGACY_INSTANCE_WRITES = {
    PREFIX + "rf_sensitivity_aw_known_noise.workflow": {PREFIX + _DIRECT + ".directory": 1},
}


def _imports(tree: ast.Module, package: str) -> dict[str, str]:
    result = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
            for alias in node.names:
                if alias.name != "*":
                    result[alias.asname or alias.name] = f"{module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                key = alias.asname or alias.name.split(".")[0]
                result[key] = alias.name if alias.asname else key
    return result


def _name(node: ast.AST, names: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value, names)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _registered_families(root: Path) -> set[str]:
    tree = ast.parse((root / "lab_workflows/experiments/registry.py").read_text(encoding="utf-8"))
    imports = _imports(tree, "lab_workflows.experiments")
    definitions = next(node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_DEFINITIONS" for target in node.targets))
    assert isinstance(definitions, (ast.List, ast.Tuple)), "注册表达方式变化，须显式更新静态覆盖入口"
    families = set()
    for item in definitions.elts:
        assert isinstance(item, ast.Name) and item.id in imports, "存在未纳入静态覆盖的注册定义"
        qualified = imports[item.id]
        assert qualified.startswith(PREFIX) and ".definition." in qualified, qualified
        families.add(qualified[len(PREFIX):].split(".")[0])
    assert len(families) == len(definitions.elts), "重复模块注册须重新审查覆盖方式"
    return families


def _sources(root: Path) -> dict[str, ast.Module]:
    sources = {}
    for family in sorted(_registered_families(root)):
        directory = root / "lab_workflows/experiment_modules" / family
        paths = sorted(directory.glob("*.py"))
        assert paths, f"注册实验缺少源码目录：{family}"
        for path in paths:
            if path.name.startswith("test_"):
                continue
            module = f"{PREFIX}{family}.{path.stem}"
            sources[module] = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return sources


def _foreign_executor(target: str, module: str) -> bool:
    return (target.startswith(PREFIX) and target.rsplit(".", 1)[-1].endswith("Workflow")
        and target[len(PREFIX):].split(".")[0] != module[len(PREFIX):].split(".")[0])


def _self_writes(function: ast.FunctionDef | None) -> Counter[str]:
    counts: Counter[str] = Counter()
    if function is None:
        return counts
    for node in ast.walk(function):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
        for target in targets:
            for item in ast.walk(target):
                if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "self":
                    counts[item.attr] += 1
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"update", "clear", "setdefault", "pop"}
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "self"):
            counts[node.func.value.attr] += 1
    return counts


def _body_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef):
    """分开分析词法作用域，不把嵌套函数里同名变量误当成外层对象。"""
    def visit(node):
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                yield from visit(child)
    for statement in function.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from visit(statement)


def _instance_writes(tree: ast.Module, names: dict[str, str], module: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        objects: dict[str, str] = {}
        local_names = dict(names)
        for node in _body_nodes(function):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                imported = _imports(ast.Module(body=[node], type_ignores=[]), module.rsplit(".", 1)[0])
                local_names.update(imported)
                for name in imported:
                    objects.pop(name, None)
            targets = node.targets if isinstance(node, ast.Assign) else (
                [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
            value = getattr(node, "value", None)
            inferred = objects.get(value.id) if isinstance(value, ast.Name) else None
            if isinstance(value, ast.Call):
                target = _name(value.func, local_names)
                # 也识别显式类工厂；不推断任意函数返回类型。
                if not target.rsplit(".", 1)[-1].endswith("Workflow"):
                    target = target.rsplit(".", 1)[0]
                if _foreign_executor(target, module):
                    inferred = target
            for item in targets:
                if isinstance(item, ast.Name):
                    local_names.pop(item.id, None)
                    if inferred:
                        objects[item.id] = inferred
                    else:
                        objects.pop(item.id, None)
            accesses = [item for target in targets for item in ast.walk(target)]
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"update", "clear", "setdefault", "pop"}):
                accesses.extend(ast.walk(node.func.value))
            for item in accesses:
                if (isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name)
                        and item.value.id in objects):
                    counts[f"{objects[item.value.id]}.{item.attr}"] += 1
    return counts


def _snapshot(root: Path):
    sources = _sources(root)
    classes = {}
    for module, tree in sources.items():
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                constructor = next((item for item in node.body
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__"), None)
                classes[f"{module}.{node.name}"] = _self_writes(constructor)
    edges = set()
    constructors = {}
    instances = {}
    for module, tree in sources.items():
        names = _imports(tree, module.rsplit(".", 1)[0])
        constructor_names = dict(names)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                target = _name(base, names)
                if not _foreign_executor(target, module):
                    continue
                key = f"{module}.{node.name}"
                edges.add((key, target))
                constructor_names[node.name] = target
                assert target in classes, f"父执行器不存在或未纳入注册源码覆盖：{target}"
                inherited = classes[target]
                direct = classes[key]
                copied = {field: count for field, count in direct.items() if field in inherited}
                if copied:
                    constructors[f"{key} -> {target}"] = copied
        writes = _instance_writes(tree, constructor_names, module)
        if writes:
            instances[module] = dict(writes)
    return edges, constructors, instances


def _extra_writes(actual: dict[str, dict[str, int]], allowed: dict[str, dict[str, int]]):
    return {key: dict(extra) for key, value in actual.items()
        if (extra := Counter(value) - Counter(allowed.get(key, {})))}


def test_all_registered_experiments_cannot_add_executor_coupling():
    edges, constructors, instances = _snapshot(ROOT)
    assert not edges - LEGACY_EXECUTOR_BASES, f"新增跨实验执行类继承：{sorted(edges - LEGACY_EXECUTOR_BASES)}"
    assert not (extra := _extra_writes(constructors, LEGACY_CONSTRUCTOR_WRITES)), f"新增父执行器状态重建：{extra}"
    assert not (extra := _extra_writes(instances, LEGACY_INSTANCE_WRITES)), f"新增外部执行器状态写入：{extra}"


@pytest.mark.parametrize("source", [
    "from ..other.workflow import Workflow as Parent\nclass Workflow(Parent): pass",
    "from ..other import workflow as sibling\nclass Workflow(sibling.Workflow): pass",
    "import lab_workflows.experiment_modules.other.analysis as sibling\nclass AnalysisWorkflow(sibling.AnalysisWorkflow): pass",
])
def test_aliases_cannot_hide_foreign_executor_bases(source):
    tree = ast.parse(source)
    module = f"{PREFIX}sample.workflow"
    names = _imports(tree, module.rsplit(".", 1)[0])
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert _foreign_executor(_name(cls.bases[0], names), module)


def test_direct_factory_alias_and_container_writes_are_detected():
    tree = ast.parse("""
from ..other.workflow import Workflow as Parent

def build():
    run = Parent.create_control_run()
    alias = run
    alias.devices = {}
    alias.state['calibration'] = {}
    alias.options.update({'threshold': 1})
    alias.closed = True
    alias.demod_mode = 'noise'
    alias.device_session = None
    alias.shutdown_parameters = {}
    alias.execute(['A01'])
""")
    module = f"{PREFIX}sample.workflow"
    writes = _instance_writes(tree, _imports(tree, module.rsplit(".", 1)[0]), module)
    assert writes == Counter({f"{PREFIX}other.workflow.Workflow.{field}": 1
        for field in ("devices", "state", "options", "closed", "demod_mode", "device_session", "shutdown_parameters")})
    assert _extra_writes({module: dict(writes)}, {})
    assert not _extra_writes({module: dict(writes)}, {module: dict(writes)})


@pytest.mark.parametrize("source, expected_fields", [
    ("def build():\n    from ..other.workflow import Workflow as Parent\n    run = Parent()\n    run.closed = True", ("closed",)),
    ("from ..other.workflow import Workflow as Parent\nfrom ..other.analysis import fit_curve\n"
     "def build():\n    run = Parent()\n    run = fit_curve([1, 2])\n    run.params = [3]", ()),
])
def test_local_import_and_rebinding_keep_static_type_ownership(source, expected_fields):
    tree = ast.parse(source)
    module = f"{PREFIX}sample.workflow"
    writes = _instance_writes(tree, _imports(tree, module.rsplit(".", 1)[0]), module)
    assert writes == Counter({f"{PREFIX}other.workflow.Workflow.{field}": 1 for field in expected_fields})


def test_missing_parent_executor_is_not_silent_debt_reduction(tmp_path):
    registry = tmp_path / "lab_workflows/experiments/registry.py"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "from ..experiment_modules.child.definition import DEFINITION as CHILD\n"
        "from ..experiment_modules.parent.definition import DEFINITION as PARENT\n"
        "_DEFINITIONS = [CHILD, PARENT]\n", encoding="utf-8")
    modules = tmp_path / "lab_workflows/experiment_modules"
    for family in ("child", "parent"):
        directory = modules / family
        directory.mkdir(parents=True)
        (directory / "definition.py").write_text("DEFINITION = None\n", encoding="utf-8")
    (modules / "child/workflow.py").write_text(
        "from ..parent.workflow import Workflow as Parent\n"
        "class Workflow(Parent):\n    def __init__(self):\n        self.state = {}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="父执行器不存在"):
        _snapshot(tmp_path)


def test_constructor_state_rebuild_and_repeated_writes_cannot_expand():
    tree = ast.parse("""
class Child:
    def __init__(self):
        self.run_dir, self.states = None, {}
        self.options.update({'threshold': 1})
""")
    constructor = tree.body[0].body[0]
    writes = _self_writes(constructor)
    assert writes == Counter(run_dir=1, states=1, options=1)
    allowed = {"child": {"run_dir": 1, "states": 1}}
    assert _extra_writes({"child": dict(writes)}, allowed) == {"child": {"options": 1}}
    allowed["child"]["options"] = 1
    assert not _extra_writes({"child": dict(writes)}, allowed)
    writes["options"] += 1
    assert _extra_writes({"child": dict(writes)}, allowed) == {"child": {"options": 1}}


def test_numeric_reuse_and_own_state_do_not_count_as_executor_coupling():
    tree = ast.parse("""
from ..other.analysis import fit_curve

def analyze():
    result = fit_curve([1, 2])
    result.parameters = [3]
    state = {'summary': 'local'}
    state.update({'complete': True})
""")
    module = f"{PREFIX}sample.analysis"
    assert not _instance_writes(tree, _imports(tree, module.rsplit(".", 1)[0]), module)


@pytest.mark.parametrize("filename", ["workflow_stages.py", "analysis_execution.py", "steps/lockin.py"])
def test_shared_execution_capabilities_do_not_import_experiments(filename):
    path = ROOT / "lab_workflows" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = "lab_workflows.steps" if filename.startswith("steps/") else "lab_workflows"
    imports = ast.Module(body=[node for node in ast.walk(tree)
        if isinstance(node, (ast.ImportFrom, ast.Import))], type_ignores=[])
    assert not {name for name in _imports(imports, package).values() if name.startswith(PREFIX)}
