import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from lab_workflows.experiments.catalog import (
    add_tag,
    assign_tag,
    public_catalog,
    rename_tag,
    set_experiment_description,
    set_experiment_metadata,
)


class ExperimentCatalogTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.path = root / "data" / f".test_experiment_catalog_{uuid4().hex}.yaml"
        self.categories = {"exp-a": "measurement", "exp-b": "calibration"}
        self.patcher = patch(
            "lab_workflows.experiments.catalog.catalog_path",
            return_value=self.path,
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.path.unlink(missing_ok=True)
        self.path.with_suffix(".yaml.tmp").unlink(missing_ok=True)

    def test_add_rename_and_assign_tag(self):
        tag = add_tag("自定义测量", self.categories)
        self.assertTrue(tag["id"])
        renamed = rename_tag(tag["id"], "慢速测量", self.categories)
        self.assertEqual(renamed["label"], "慢速测量")
        assign_tag("exp-a", tag["id"], self.categories)
        set_experiment_description("exp-a", "低频、长时间采集实验", self.categories)
        set_experiment_metadata(
            "exp-b", "T1 慢速标定", "用于低温条件", self.categories
        )
        catalog = public_catalog(self.categories)
        self.assertEqual(catalog["assignments"]["exp-a"], tag["id"])
        self.assertEqual(catalog["assignments"]["exp-b"], "calibration")
        self.assertEqual(
            catalog["experiment_descriptions"]["exp-a"],
            "低频、长时间采集实验",
        )
        self.assertEqual(catalog["experiment_titles"]["exp-b"], "T1 慢速标定")
        self.assertEqual(
            catalog["experiment_descriptions"]["exp-b"], "用于低温条件"
        )

    def test_rejects_duplicate_label_and_unknown_experiment(self):
        with self.assertRaises(ValueError):
            add_tag("测量", self.categories)
        with self.assertRaises(KeyError):
            assign_tag("missing", "measurement", self.categories)


if __name__ == "__main__":
    unittest.main()
