"""验收关联、共享步骤影响与 Skill 单源资源的无硬件回归。"""

import ast
from contextlib import redirect_stdout
from hashlib import sha256
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from .experiment_validation import (
    COMMON_SCENARIOS, EXPCODEGEN_RESOURCES, affected_experiments, canonical_test_targets, changed_files,
    deduplicate_test_targets, discover_acquisition_cases, discover_test_cases, missing_scenarios,
    repository_path, skill_differences, skill_resource_errors,
    split_test_targets, validation_plan,
    verify_pytest_results,
)


class ExperimentValidationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def definition(self, name="demo"):
        class Adapter:
            acquisition_module = f"lab_workflows.{name}.workflow"

            def run(self):
                raise AssertionError("验证不能启动采集")

        return SimpleNamespace(id=name, experiment_runner=Adapter().run,
                               acquisition_program=f"experiments/{name}.py", analysis_program=None)

    def test_batches_keep_every_target_and_bound_windows_command_length(self):
        targets = [f"lab_workflows/{'nested_' * 12}/test_demo.py::test_{index:03d}" for index in range(296)]
        batches = split_test_targets(targets + targets[:3])
        self.assertEqual([node for batch in batches for node in batch], sorted(targets))
        self.assertTrue(all(len(batch) <= 25 and len(subprocess.list2cmdline(batch)) <= 12000 for batch in batches))
        long_targets = [f"{'long_' * 300}/test_{index:03d}.py" for index in range(30)]
        self.assertTrue(all(len(subprocess.list2cmdline(batch)) <= 12000 for batch in split_test_targets(long_targets)))
        self.assertEqual(split_test_targets([]), [])
        self.assertEqual(split_test_targets(["test_one.py", "test_one.py::test_first"]), [["test_one.py"]])
        self.assertEqual(split_test_targets(["a.py::test_one", "b.py", "c.py"]),
                         [["a.py::test_one"], ["b.py"], ["c.py"]])

    def test_only_manifest_verified_archive_snapshots_leave_execution_targets(self):
        snapshot = "old/experiments/lab_workflows/demo/test_workflow.py"
        path = self.write(snapshot, "raise AssertionError('归档快照不得作为活动测试导入')\n")
        active = "lab_workflows/test_active.py"
        other_old = "old/other/test_active.py"
        self.write(active, "def test_active(): pass\n")
        self.write(other_old, "def test_active(): pass\n")
        self.write("old/experiments/manifest.json", json.dumps({"files": {
            "lab_workflows/demo/test_workflow.py": sha256(path.read_bytes()).hexdigest()}}))
        changes = [snapshot, active, other_old]
        report, errors = validation_plan(self.root, [], changes, {})
        self.assertFalse(errors)
        self.assertEqual(report["changed_files"], changes)
        self.assertEqual(report["changed_test_files"], sorted(changes))
        self.assertEqual(report["archived_test_files"], [snapshot])
        self.assertEqual(report["test_targets"], [active, other_old])
        path.write_text("def test_changed(): pass\n", encoding="utf-8")
        report, errors = validation_plan(self.root, [], changes, {})
        self.assertTrue(any("字节已改变" in error for error in errors))
        self.assertIn(snapshot, report["test_targets"])
        (self.root / "old/experiments/manifest.json").unlink()
        report, errors = validation_plan(self.root, [], changes, {})
        self.assertTrue(any("无法核对归档" in error for error in errors))
        self.assertIn(snapshot, report["test_targets"])

    def test_real_validator_batches_verify_all_xml_and_do_not_pass_partial_runs(self):
        script = Path(__file__).resolve().parents[1] / ".agents/skills/expcodegen/scripts/validate_experiment.py"
        spec = importlib.util.spec_from_file_location("validation_batch_test", script)
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        for failed_index in (None, 0, 25, "timeout", "missing_xml", "skipped", "missing_node"):
            with self.subTest(failed_index=failed_index):
                case_root = self.root / str(failed_index)
                prefix = case_root.relative_to(self.root).as_posix() + "/"
                source = "lab_workflows/demo/workflow.py"
                test_file = "lab_workflows/demo/test_workflow.py"
                shared = "lab_workflows/test_acquisition_order.py"
                self.write(prefix + source, "pass\n")
                for relative in EXPCODEGEN_RESOURCES:
                    self.write(prefix + ".agents/skills/expcodegen/" + relative, "resource\n")
                self.write(prefix + shared, "def test_contract(): assert True\n")
                self.write(prefix + test_file, "\n".join(
                    f"def test_{index:02d}(): assert {index != failed_index}" for index in range(26)))
                claim = {"demo": {"mode": "single", "cases": {scenario: [
                    f"{test_file}::test_{index:02d}" for index in range(26)] for scenario in COMMON_SCENARIOS}}}
                real_run = subprocess.run

                def run(command, **kwargs):
                    if isinstance(failed_index, str) and "pytest" in command:
                        if failed_index == "timeout":
                            raise subprocess.TimeoutExpired(command, 900)
                        if failed_index != "missing_xml":
                            result = Path(command[command.index("--junitxml") + 1])
                            skipped = "<skipped/>" if failed_index == "skipped" else ""
                            result.write_text(f'<testsuite><testcase classname="wrong" name="missing">{skipped}</testcase></testsuite>',
                                              encoding="utf-8")
                        return SimpleNamespace(returncode=0)
                    return real_run(command, **kwargs)

                with patch("lab_workflows.experiments.list_experiments", return_value=[self.definition()]), \
                        patch("lab_workflows.experiments.get_experiment", return_value=self.definition()), \
                        patch.object(validator.subprocess, "run", side_effect=run), \
                        patch.object(validator, "validate", return_value=[]), \
                        patch.object(validator, "metadata_snapshot", return_value={"workflow_module": None}), \
                        patch("lab_workflows.experiment_validation.discover_test_cases", return_value=(claim, {})), \
                        patch("lab_workflows.experiment_validation.changed_files", return_value=[source, shared]), \
                        patch.object(sys, "path", sys.path[:]), redirect_stdout(io.StringIO()):
                    code = validator.main(["--root", str(case_root), "--changed-file", source,
                        "--changed-file", shared, "--run-regressions", "--report", "report.json"])
                report = json.loads((case_root / "report.json").read_text(encoding="utf-8"))
                batches = report["business_batches"]
                self.assertEqual(len(batches), 3)
                self.assertEqual([node for batch in batches for node in batch["targets"]], report["test_targets"])
                self.assertEqual(code, 0 if failed_index is None else 1)
                self.assertEqual(report["business_status"], "passed" if failed_index is None else "incomplete")
                self.assertEqual(report["experiments"][0]["acquisition_status"],
                                 "passed" if failed_index is None else "batch_incomplete")
                self.assertEqual(batches[0]["status"], "incomplete" if isinstance(failed_index, str) else
                                 "failed" if failed_index == 0 else "passed")
                self.assertEqual(batches[1]["status"], "failed" if failed_index == 25 else
                                 "passed" if failed_index is None else "not_run")
                self.assertEqual(batches[-1]["status"], "passed" if failed_index is None else "not_run")
                if failed_index is None:
                    paths = [case_root / batch["results"] for batch in batches]
                    self.assertEqual(verify_pytest_results(paths, report["required_test_nodes"]), 27)
                    self.assertEqual(verify_pytest_results(case_root / report["business_test_results"],
                                                         report["required_test_nodes"]), 27)

    def test_discovers_real_nodes_without_import_and_reports_missing_scenarios(self):
        self.write("lab_workflows/test_demo.py", '''
raise AssertionError("发现阶段不能导入测试")
ACQUISITION_CASES = {"demo": {"mode": "grouped", "cases": {
    "order": ["OrderTest::test_order"], "slow_disk": ["test_disk"]}}}
class OrderTest:
    def test_order(self): pass
def test_disk(): pass
''')
        claim = discover_acquisition_cases(self.root)["demo"]
        self.assertEqual(claim["cases"]["order"], ["lab_workflows/test_demo.py::OrderTest::test_order"])
        self.assertIn("last_group", missing_scenarios(claim))
        self.assertIn("cancel", missing_scenarios(claim))
        self.assertNotIn("slow_disk", missing_scenarios(claim))
        self.assertEqual(missing_scenarios(None), ["未关联真实业务测试"])

    def test_rejects_invented_test_and_dynamic_declaration(self):
        for declaration in (
            '{"demo": {"mode": "single", "cases": {"order": ["test_missing"]}}}',
            'dict(demo={})',
            '{"demo": {"mode": "single", "cases": {"invented": ["test_real"]}}}',
        ):
            with self.subTest(declaration=declaration):
                self.write("lab_workflows/test_demo.py", f"ACQUISITION_CASES = {declaration}\ndef test_real(): pass\n")
                with self.assertRaises(ValueError):
                    discover_acquisition_cases(self.root)

    def test_shared_relative_import_and_deleted_source_affect_all_callers(self):
        for name in ("one", "two"):
            self.write(f"lab_workflows/{name}/workflow.py", "from ..shared import acquire\n")
        self.write("lab_workflows/shared.py", "def acquire(): pass\n")
        definitions = [self.definition("one"), self.definition("two")]
        for deleted in (False, True):
            if deleted:
                (self.root / "lab_workflows/shared.py").unlink()
            impacts = affected_experiments(self.root, definitions, ["lab_workflows/shared.py"], {})
            self.assertEqual(set(impacts), {"one", "two"})
            self.assertTrue(all(item["acquisition_changed"] for item in impacts.values()))

    def test_owned_dynamic_module_and_explicit_sources_are_tracked(self):
        self.write("lab_workflows/demo/workflow.py", "import importlib\n")
        self.write("lab_workflows/callback.py", "pass\n")
        claims = {"demo": {"sources": ["lab_workflows/callback.py"], "cases": {}}}
        impacts = affected_experiments(self.root, [self.definition()], ["lab_workflows/callback.py"], claims)
        self.assertTrue(impacts["demo"]["acquisition_changed"])
        impacts = affected_experiments(self.root, [self.definition()], ["lab_workflows/demo/session.py"], {})
        self.assertIn("demo", impacts)
        self.assertTrue(impacts["demo"]["acquisition_changed"])

    def test_existing_acquisition_change_requires_business_scenarios(self):
        self.write("lab_workflows/demo/workflow.py", "pass\n")
        definitions = [self.definition()]
        report, errors = validation_plan(self.root, definitions, ["lab_workflows/demo/workflow.py"], {})
        self.assertIn("未关联真实业务测试", errors[0])
        self.assertTrue(report["experiments"][0]["acquisition_required"])
        self.assertEqual(report["experiments"][0]["acquisition_status"], "not_run")
        self.assertFalse(report["hardware_verified"])
        _, errors = validation_plan(self.root, definitions, [], {})
        self.assertEqual(errors, [])
        _, errors = validation_plan(self.root, definitions, [], {}, required_ids=["demo"])
        self.assertTrue(errors)

    def test_shared_change_cannot_be_hidden_by_selecting_one_id(self):
        for name in ("one", "two"):
            self.write(f"lab_workflows/{name}/workflow.py", "from ..shared import acquire\n")
        self.write("lab_workflows/shared.py", "def acquire(): pass\n")
        report, errors = validation_plan(self.root, [self.definition("one"), self.definition("two")],
                                          ["lab_workflows/shared.py"], {}, requested_ids=["one"])
        self.assertEqual({entry["id"] for entry in report["experiments"] if entry["selected"]}, {"one", "two"})
        self.assertEqual(len(errors), 2)

    def test_parameters_and_test_changes_do_not_claim_acquisition_changes(self):
        self.write("lab_workflows/demo/workflow.py", "from .models import Params\n")
        self.write("lab_workflows/demo/models.py", "class Params: pass\n")
        impacts = affected_experiments(self.root, [self.definition()], ["lab_workflows/demo/models.py"], {})
        self.assertFalse(impacts["demo"]["acquisition_changed"])

    def test_offline_algorithm_and_legacy_plot_do_not_require_acquisition(self):
        self.write("lab_workflows/demo/workflow.py", "pass\n")
        self.write("lab_workflows/demo/analysis.py", "from .processing import fit\n")
        self.write("lab_workflows/demo/processing.py", "def fit(): pass\n")
        definition = self.definition()
        definition.experiment_runner.__self__.analysis_module = "lab_workflows.demo.analysis"
        impacts = affected_experiments(self.root, [definition], ["lab_workflows/demo/processing.py"], {})
        self.assertFalse(impacts["demo"]["acquisition_changed"])
        self.write("experiments/legacy.py", "pass\n")
        self.write("experiments/legacy_plot.py", "pass\n")
        legacy = SimpleNamespace(id="legacy", experiment_runner=None,
                                 acquisition_program="experiments/legacy.py", analysis_program="experiments/legacy_plot.py")
        impacts = affected_experiments(self.root, [legacy], ["experiments/legacy_plot.py"], {})
        self.assertFalse(impacts["legacy"]["acquisition_changed"])

    def test_non_acquisition_changes_do_not_schedule_full_acquisition(self):
        self.write("lab_workflows/demo/workflow.py", "from .models import Params\n")
        self.write("lab_workflows/demo/models.py", "class Params: pass\n")
        claims = {"demo": {"mode": "single", "cases": {
            scenario: ["lab_workflows/demo/test_workflow.py::test_order"] for scenario in COMMON_SCENARIOS}}}
        for path in ("lab_workflows/demo/models.py", "lab_workflows/demo/analysis.py",
                     "lab_workflows/demo/definition.py", "lab_workflows/demo/stages.py",
                     "params/experiments/demo.yaml"):
            with self.subTest(path=path):
                report, errors = validation_plan(self.root, [self.definition()], [path], claims)
                if path.endswith(("models.py", "analysis.py", "demo.yaml")):
                    self.assertTrue(any("REGRESSION_CASES" in error for error in errors))
                else:
                    self.assertFalse(errors)
                entry = report["experiments"][0]
                self.assertTrue(entry["selected"])
                self.assertFalse(entry["acquisition_required"])
                self.assertEqual(entry["acquisition_status"], "not_required")
                self.assertEqual(report["test_targets"], [])
        report, errors = validation_plan(self.root, [self.definition()], [], claims, requested_ids=["demo"])
        self.assertFalse(errors)
        self.assertEqual(report["test_targets"], [])
        report, errors = validation_plan(self.root, [self.definition()], [], claims, required_ids=["demo"])
        self.assertFalse(errors)
        self.assertTrue(report["experiments"][0]["selected"])
        self.assertTrue(report["experiments"][0]["acquisition_required"])
        self.assertEqual(report["test_targets"], ["lab_workflows/demo/test_workflow.py::test_order"])
        _, errors = validation_plan(self.root, [self.definition()], [], {}, required_ids=["unknown"])
        self.assertTrue(any("unknown" in error for error in errors))

    def test_changed_test_file_runs_all_its_tests_without_claiming_full_acquisition(self):
        self.write("lab_workflows/demo/workflow.py", "pass\n")
        test_file = "lab_workflows/demo/test_workflow.py"
        self.write(test_file, "def test_order(): pass\ndef test_new_regression(): pass\n")
        claims = {"demo": {"mode": "single", "cases": {"order": [test_file + "::test_order"]}}}
        report, errors = validation_plan(self.root, [self.definition()], [test_file], claims)
        self.assertFalse(errors)
        self.assertEqual(report["test_targets"], [test_file])
        self.assertFalse(report["experiments"][0]["acquisition_required"])
        self.assertEqual(report["experiments"][0]["acquisition_status"], "not_required")
        self.assertIn("cancel", report["experiments"][0]["missing_scenarios"])
        self.assertEqual(deduplicate_test_targets([test_file, test_file + "::test_order", test_file]), [test_file])

    def test_shared_acquisition_keeps_all_callers_and_deduplicates_changed_files(self):
        claims = {}
        for name in ("one", "two"):
            self.write(f"lab_workflows/{name}/workflow.py", "from ..shared import acquire\n")
            path = f"lab_workflows/{name}/test_workflow.py"
            self.write(path, "def test_order(): pass\n")
            claims[name] = {"mode": "single", "cases": {scenario: [path + "::test_order"] for scenario in COMMON_SCENARIOS}}
        self.write("lab_workflows/shared.py", "def acquire(): pass\n")
        report, errors = validation_plan(self.root, [self.definition("one"), self.definition("two")],
            ["lab_workflows/shared.py", "lab_workflows/one/test_workflow.py"], claims, requested_ids=["one"])
        self.assertFalse(errors)
        self.assertTrue(all(entry["acquisition_required"] for entry in report["experiments"]))
        self.assertEqual(report["test_targets"], ["lab_workflows/one/test_workflow.py",
                                                 "lab_workflows/two/test_workflow.py::test_order"])

    def test_shared_analysis_dependency_is_reported_without_acquisition(self):
        self.write("lab_workflows/demo/workflow.py", "pass\n")
        self.write("lab_workflows/demo/analysis.py", "from ..offline import fit\n")
        path = "lab_workflows/offline.py"
        self.write(path, "def fit(): pass\n")
        definition = self.definition()
        definition.experiment_runner.__self__.analysis_module = "lab_workflows.demo.analysis"
        report, errors = validation_plan(self.root, [definition], [path], {})
        self.assertTrue(any("analysis" in error for error in errors))
        self.assertEqual(report["experiments"][0]["regressions"]["analysis"]["status"], "missing_mapping")
        self.assertTrue(report["experiments"][0]["selected"])
        self.assertFalse(report["experiments"][0]["acquisition_required"])
        self.assertEqual(report["unmapped_files"], [])
        self.assertEqual(report["test_targets"], [])

    def test_discovery_reads_both_declarations_once_without_import(self):
        self.write("lab_workflows/offline.py", "pass\n")
        self.write("lab_workflows/test_both.py", '''
raise AssertionError("发现不能导入")
ACQUISITION_CASES = {"demo": {"mode": "single", "cases": {"order": ["test_order"]}}}
REGRESSION_CASES = {"demo": {
    "analysis": {"sources": ["lab_workflows/offline.py"], "tests": ["ScienceTest::test_fit"]},
    "parameters": {"sources": ["lab_workflows/offline.py"], "tests": ["test_order"]}}}
def test_order(): pass
class ScienceTest:
    def test_fit(self): pass
''')
        with patch("lab_workflows.experiment_validation.ast.parse", wraps=ast.parse) as parse:
            acquisition, regressions = discover_test_cases(self.root)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(acquisition["demo"]["cases"]["order"], ["lab_workflows/test_both.py::test_order"])
        self.assertEqual(regressions["demo"]["analysis"]["nodes"], ["lab_workflows/test_both.py::ScienceTest::test_fit"])

    def test_invalid_regression_declarations_are_rejected(self):
        self.write("lab_workflows/offline.py", "pass\n")
        valid = {"sources": ["lab_workflows/offline.py"], "tests": ["test_fit"]}
        declarations = [repr({"demo": {"unknown": valid}}), "dict(demo={})",
                        repr({"demo": {"analysis": valid | {"tests": ["test_missing"]}}}),
                        repr({"demo": {"analysis": valid | {"sources": []}}}),
                        repr({"demo": {"analysis": valid | {"sources": ["../escape.py"]}}}),
                        repr({"demo": {"analysis": valid | {"sources": ["lab_workflows/missing.py"]}}})]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                self.write("lab_workflows/test_offline.py", f"REGRESSION_CASES = {declaration}\ndef test_fit(): pass\n")
                with self.assertRaises(ValueError):
                    discover_test_cases(self.root)

    def test_shared_analysis_mapping_selects_both_experiments_without_acquisition(self):
        source = "lab_workflows/offline.py"
        self.write(source, "def fit(): pass\n")
        definitions = []
        for name in ("one", "two"):
            self.write(f"lab_workflows/{name}/workflow.py", "pass\n")
            self.write(f"lab_workflows/{name}/analysis.py", "from ..offline import fit\n")
            definition = self.definition(name)
            definition.experiment_runner.__self__.analysis_module = f"lab_workflows.{name}.analysis"
            definitions.append(definition)
        declaration = {name: {"analysis": {"sources": [source], "tests": ["test_fit"]}} for name in ("one", "two")}
        self.write("lab_workflows/test_offline.py", f"REGRESSION_CASES = {declaration!r}\ndef test_fit(): pass\n")
        acquisition, regressions = discover_test_cases(self.root)
        report, errors = validation_plan(self.root, definitions, [source], acquisition,
            requested_ids=["one"], regression_cases=regressions)
        self.assertFalse(errors)
        self.assertTrue(all(entry["selected"] and not entry["acquisition_required"] for entry in report["experiments"]))
        self.assertTrue(all(entry["regressions"]["analysis"]["required"] for entry in report["experiments"]))
        self.assertEqual(report["test_targets"], ["lab_workflows/test_offline.py::test_fit"])
        report, errors = validation_plan(self.root, definitions, [source, "lab_workflows/test_offline.py"], {},
            requested_ids=["one"], regression_cases={"one": regressions["one"]})
        self.assertTrue(any("two" in error and "analysis" in error for error in errors))
        self.assertEqual(report["experiments"][1]["regressions"]["analysis"]["status"], "missing_mapping")
        self.assertEqual(report["test_targets"], ["lab_workflows/test_offline.py"])
        # 分析声明不能隐藏同一源码的真实采集依赖。
        self.write("lab_workflows/one/workflow.py", "from ..offline import fit\n")
        report, errors = validation_plan(self.root, definitions, [source], {}, regression_cases=regressions)
        self.assertTrue(report["experiments"][0]["acquisition_required"])
        self.assertTrue(any("采集业务验收" in error for error in errors))

    def test_parameter_mapping_covers_models_and_defaults_without_full_acquisition(self):
        model = "lab_workflows/demo/models.py"
        defaults = "params/experiments/demo.yaml"
        self.write("lab_workflows/demo/workflow.py", "from .models import Params\n")
        self.write(model, "class Params: pass\n")
        self.write(defaults, "schema_version: 1\n")
        declaration = {"demo": {"parameters": {"sources": [model, defaults], "tests": ["test_migration", "test_limits"]}}}
        self.write("lab_workflows/test_params.py", f"REGRESSION_CASES = {declaration!r}\ndef test_migration(): pass\ndef test_limits(): pass\n")
        acquisition, regressions = discover_test_cases(self.root)
        for source in (model, defaults):
            with self.subTest(source=source):
                report, errors = validation_plan(self.root, [self.definition()], [source], acquisition, regression_cases=regressions)
                self.assertFalse(errors)
                entry = report["experiments"][0]
                self.assertFalse(entry["acquisition_required"])
                self.assertEqual(entry["regressions"]["parameters"]["status"], "not_run")
                self.assertFalse(entry["regressions"]["analysis"]["required"])
                self.assertEqual(len(report["test_targets"]), 2)
        _, errors = validation_plan(self.root, [self.definition()], [], {}, regression_cases={"unknown": {}})
        self.assertTrue(any("unknown" in error for error in errors))

    def test_unrelated_analysis_mapping_cannot_cover_changed_second_algorithm(self):
        self.write("lab_workflows/demo/workflow.py", "pass\n")
        self.write("lab_workflows/demo/analysis.py", "from .first import fit\nfrom .second import fit\n")
        first = "lab_workflows/demo/first.py"
        second = "lab_workflows/demo/second.py"
        self.write(first, "def fit(): return 1\n")
        self.write(second, "def fit(): raise RuntimeError('broken')\n")
        mapping = {"demo": {"analysis": {"sources": [first], "tests": ["test_first"]}}}
        self.write("lab_workflows/test_first.py", f"REGRESSION_CASES = {mapping!r}\ndef test_first(): pass\n")
        definition = self.definition()
        definition.experiment_runner.__self__.analysis_module = "lab_workflows.demo.analysis"
        acquisition, regressions = discover_test_cases(self.root)
        for changes in ([second], [first, second]):
            with self.subTest(changes=changes):
                report, errors = validation_plan(self.root, [definition], changes, acquisition, regression_cases=regressions)
                self.assertTrue(errors)
                regression = report["experiments"][0]["regressions"]["analysis"]
                self.assertEqual(regression["status"], "missing_mapping")
                self.assertEqual(regression["uncovered_sources"], [second])
        mapping = {"demo": {"analysis": {"sources": [second], "tests": ["test_second"]}}}
        self.write("lab_workflows/test_second.py", f"REGRESSION_CASES = {mapping!r}\ndef test_second(): pass\n")
        acquisition, regressions = discover_test_cases(self.root)
        report, errors = validation_plan(self.root, [definition], [second], acquisition, regression_cases=regressions)
        self.assertFalse(errors)
        self.assertIn("lab_workflows/test_second.py::test_second", report["test_targets"])

    def test_dynamic_acquisition_source_tracks_its_shared_safety_dependency(self):
        self.write("lab_workflows/demo/workflow.py", "import importlib\n")
        callback = "lab_workflows/callback.py"
        safety = "lab_workflows/shared_safety.py"
        self.write(callback, "from .shared_safety import acquire\n")
        self.write(safety, "def acquire(): pass\n")
        claims = {"demo": {"mode": "single", "sources": [callback], "cases": {
            scenario: ["lab_workflows/test_dynamic.py::test_order"] for scenario in COMMON_SCENARIOS}}}
        report, errors = validation_plan(self.root, [self.definition()], [safety], claims)
        self.assertFalse(errors)
        self.assertTrue(report["experiments"][0]["acquisition_required"])
        self.assertEqual(report["test_targets"], ["lab_workflows/test_dynamic.py::test_order"])
        self.assertEqual(report["unmapped_files"], [])

    def test_regression_dynamic_sources_track_transitive_helpers(self):
        self.write("lab_workflows/demo/workflow.py", "pass\n")
        callback = "lab_workflows/callback.py"
        helper = "lab_workflows/shared_algorithm.py"
        self.write(callback, "from .shared_algorithm import calculate\n")
        self.write(helper, "def calculate(): return 1\n")
        for kind in ("analysis", "parameters"):
            with self.subTest(kind=kind):
                mapping = {"demo": {kind: {"sources": [callback], "tests": ["test_calculate"]}}}
                self.write("lab_workflows/test_dynamic.py", f"REGRESSION_CASES = {mapping!r}\ndef test_calculate(): pass\n")
                acquisition, regressions = discover_test_cases(self.root)
                report, errors = validation_plan(self.root, [self.definition()], [helper], acquisition, regression_cases=regressions)
                self.assertFalse(errors)
                entry = report["experiments"][0]
                self.assertTrue(entry["selected"])
                self.assertFalse(entry["acquisition_required"])
                self.assertTrue(entry["regressions"][kind]["required"])
                self.assertEqual(entry["regressions"][kind]["uncovered_sources"], [])
                self.assertEqual(report["test_targets"], ["lab_workflows/test_dynamic.py::test_calculate"])

    def test_catalog_lookup_does_not_spread_private_models_but_keeps_shared_dependencies(self):
        definitions = [self.definition("one"), self.definition("two")]
        for name in ("one", "two"):
            self.write(f"lab_workflows/{name}/workflow.py", "from .models import Params\n")
            self.write(f"lab_workflows/{name}/models.py", "from ..experiment_params import Base\nclass Params: pass\n")
        self.write("lab_workflows/experiment_params.py", "from .experiments import list_experiments\n")
        self.write("lab_workflows/experiments/__init__.py", "from .registry import list_experiments\n")
        catalog = "lab_workflows/experiments/registry.py"
        self.write(catalog, "from ..one.models import Params\nfrom ..two.models import Params\n")
        impacts = affected_experiments(self.root, definitions, ["lab_workflows/one/models.py"], {})
        self.assertEqual(set(impacts), {"one"})
        self.assertTrue(impacts["one"]["parameters_changed"])
        impacts = affected_experiments(self.root, definitions, [catalog], {})
        self.assertEqual(set(impacts), {"one", "two"})
        impacts = affected_experiments(self.root, definitions, ["lab_workflows/experiment_params.py"], {})
        self.assertEqual(set(impacts), {"one", "two"})
        # 真正复用另一实验模型时，仍需同时验证两个调用方。
        self.write("lab_workflows/two/models.py", "from ..one.models import Params\n")
        impacts = affected_experiments(self.root, definitions, ["lab_workflows/one/models.py"], {})
        self.assertEqual(set(impacts), {"one", "two"})

    def test_only_known_scaffold_alias_is_canonicalized_and_reported(self):
        source = ".agents/skills/expcodegen/scripts/test_scaffold_experiment.py"
        legacy = ".claude/skills/expcodegen/scripts/test_scaffold_experiment.py"
        for path in (source, "lab_workflows/test_one.py", "lab_workflows/test_two.py"):
            self.write(path, "def test_entry(): pass\n")
        self.assertFalse((self.root / ".claude").exists())
        for inputs in ([legacy], [source, legacy], [source]):
            with self.subTest(inputs=inputs):
                targets, _ = canonical_test_targets(self.root, inputs)
                self.assertEqual(targets, [source])
        report, errors = validation_plan(self.root, [], [source, legacy], {})
        self.assertFalse(errors)
        self.assertEqual(report["test_targets"], [source])
        self.assertEqual(report["changed_files"], [source, legacy])
        self.assertEqual(report["changed_test_files"], [source, legacy])
        self.assertEqual(report["test_target_aliases"], {legacy: source})
        targets, aliases = canonical_test_targets(self.root, [legacy + "::test_entry", "lab_workflows/test_one.py", "lab_workflows/test_two.py"])
        self.assertEqual(len(targets), 3)
        self.assertEqual(aliases[legacy + "::test_entry"], source + "::test_entry")
        other = ".claude/skills/other/scripts/test_scaffold_experiment.py"
        self.assertEqual(canonical_test_targets(self.root, [other]), ([other], {}))

    def test_scaffold_alias_requires_source_without_reading_retired_mirror(self):
        source = ".agents/skills/expcodegen/scripts/test_scaffold_experiment.py"
        legacy = ".claude/skills/expcodegen/scripts/test_scaffold_experiment.py"
        self.write(legacy, "旧内容不再参与验证\n")
        for target in (source, legacy, legacy + "::test_entry"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "维护源缺失"):
                canonical_test_targets(self.root, [target])
        with self.assertRaisesRegex(ValueError, "维护源缺失"):
            validation_plan(self.root, [], [legacy], {})
        self.write(source, "def test_entry(): pass\n")
        real_is_file = Path.is_file

        def is_file(path):
            self.assertNotIn(".claude", path.parts, "不应检查退役目录")
            return real_is_file(path)

        with patch.object(Path, "is_file", is_file), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("alias 不能比较内容")):
            self.assertEqual(canonical_test_targets(self.root, [source, legacy]), ([source], {legacy: source}))
            report, errors = validation_plan(self.root, [], [legacy], {})
        self.assertFalse(errors)
        self.assertEqual(report["test_targets"], [source])
        self.assertEqual(report["changed_files"], [legacy])
        self.assertEqual(report["test_target_aliases"], {legacy: source})

    def test_whole_file_results_are_verified_including_src_layout(self):
        for module in ("signal_generator.test_interfaces", "src.signal_generator.test_interfaces"):
            with self.subTest(module=module):
                path = self.write("results.xml", f'<testsuite><testcase classname="{module}.ApiTests" name="test_api"/></testsuite>')
                self.assertEqual(verify_pytest_results(path, ["src/signal_generator/test_interfaces.py"]), 1)
                self.assertEqual(verify_pytest_results(path, ["src/signal_generator/test_interfaces.py::ApiTests::test_api"]), 1)
                with self.assertRaisesRegex(ValueError, "未实际执行"):
                    verify_pytest_results(path, ["src/signal_generator/test_missing.py"])

    def test_paths_cannot_escape_repository(self):
        for path in ("../outside.py", str(self.root.parent / "outside.py")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                repository_path(self.root, path)

    def test_pytest_results_reject_skips_and_missing_selected_nodes(self):
        path = self.write("results.xml", '<testsuite><testcase classname="lab_workflows.test_demo.OrderTest" name="test_order[x]"/></testsuite>')
        nodes = ["lab_workflows/test_demo.py::OrderTest::test_order"]
        self.assertEqual(verify_pytest_results(path, nodes), 1)
        with self.assertRaisesRegex(ValueError, "未实际执行"):
            verify_pytest_results(path, ["lab_workflows/test_demo.py::OrderTest::test_other"])
        path.write_text('<testsuite><testcase name="test_order"><skipped/></testcase></testsuite>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "跳过"):
            verify_pytest_results(path, nodes)

    def test_skill_check_requires_only_canonical_resources(self):
        self.assertEqual(len(EXPCODEGEN_RESOURCES), 33)
        self.assertEqual(len(set(EXPCODEGEN_RESOURCES)), 33)
        self.assertEqual(len(skill_resource_errors(self.root)), 33)
        for relative in EXPCODEGEN_RESOURCES:
            self.write(".agents/skills/expcodegen/" + relative, "resource\n")
        self.write(".agents/skills/expcodegen/__pycache__/ignored.pyc", "")
        self.write(".agents/skills/expcodegen/references/extra.md", "")
        self.write(".claude/skills/expcodegen/SKILL.md", "retired")
        self.assertEqual(skill_resource_errors(self.root), [])
        self.assertEqual(skill_differences(self.root), [])
        self.write(".agents/skills/expcodegen/SKILL.md", "")
        (self.root / ".agents/skills/expcodegen/assets/python/acquisition.py.tpl").unlink()
        unreadable = self.root / ".agents/skills/expcodegen/references/shared-steps.md"
        real_read_bytes = Path.read_bytes

        def read_bytes(path):
            self.assertNotIn(".claude", path.parts, "不应读取退役资源")
            if path == unreadable:
                raise PermissionError("模拟资源不可读")
            return real_read_bytes(path)

        with patch.object(Path, "read_bytes", read_bytes), \
                patch.object(Path, "rglob", side_effect=AssertionError("不应递归比较目录")):
            errors = skill_resource_errors(self.root)
            self.assertEqual(skill_differences(self.root), errors)
        self.assertCountEqual(errors, [
            "Skill 资源为空: SKILL.md", "Skill 资源缺失: assets/python/acquisition.py.tpl",
            "Skill 资源不可读: references/shared-steps.md: 模拟资源不可读",
        ])

    def test_git_changes_include_staged_unstaged_and_untracked(self):
        def git(*arguments):
            subprocess.run(["git", *arguments], cwd=self.root, check=True, capture_output=True)
        git("init", "-q")
        for name in ("staged.py", "unstaged.py"):
            self.write(name, "pass\n")
        git("add", ".")
        git("-c", "user.name=Validation", "-c", "user.email=validation@example.invalid", "commit", "-qm", "initial")
        self.write("staged.py", "changed\n")
        git("add", "staged.py")
        self.write("unstaged.py", "changed\n")
        self.write("new.py", "pass\n")
        self.assertEqual(changed_files(self.root), ["new.py", "staged.py", "unstaged.py"])
        (self.root / "unstaged.py").rename(self.root / "renamed.py")
        self.assertEqual(changed_files(self.root), ["new.py", "renamed.py", "staged.py", "unstaged.py"])


if __name__ == "__main__":
    unittest.main()
