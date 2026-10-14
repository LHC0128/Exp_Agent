"""验收关联、共享步骤影响和双 Skill 同步的无硬件回归。"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from .experiment_validation import (
    affected_experiments, changed_files, discover_acquisition_cases,
    missing_scenarios, repository_path, skill_differences,
    validation_plan,
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

    def test_skill_check_detects_missing_changed_and_extra_resources(self):
        for owner in (".agents", ".claude"):
            self.write(f"{owner}/skills/expcodegen/SKILL.md", "same")
            self.write(f"{owner}/skills/expcodegen/__pycache__/ignored.pyc", owner)
        self.assertEqual(skill_differences(self.root), [])
        self.write(".agents/skills/expcodegen/SKILL.md", "updated")
        self.write(".agents/skills/expcodegen/assets/new.tpl", "new")
        self.write(".claude/skills/expcodegen/references/extra.md", "extra")
        self.assertEqual(skill_differences(self.root), ["SKILL.md", "assets/new.tpl", "references/extra.md"])

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
