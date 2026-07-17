import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from lab_workflows.experiments.catalog import (
    add_tag,
    apply_parameter_layout,
    assign_tag,
    public_catalog,
    rename_tag,
    set_experiment_description,
    set_experiment_metadata,
    set_parameter_layout,
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

    def test_parameter_layout_round_trip_and_schema_merge(self):
        fields = [
            {"name": "POWER", "group": "basic", "default": 1.0},
            {"name": "RATE", "group": "advanced", "default": 10},
        ]
        set_parameter_layout(
            "exp-a",
            {"basic": ["RATE"], "advanced": ["POWER"]},
            fields,
            self.categories,
        )
        merged = apply_parameter_layout(
            "exp-a",
            {"experiment": "exp-a", "fields": fields},
            self.categories,
        )
        self.assertEqual(
            [(field["name"], field["group"]) for field in merged["fields"]],
            [("RATE", "basic"), ("POWER", "advanced")],
        )
        self.assertEqual(
            merged["parameter_layout"],
            {"basic": ["RATE"], "advanced": ["POWER"]},
        )
        self.assertTrue(merged["parameter_layout_saved"])
        merged_with_new_field = apply_parameter_layout(
            "exp-a",
            {
                "experiment": "exp-a",
                "fields": [*fields, {"name": "TIME", "group": "advanced"}],
            },
            self.categories,
        )
        self.assertEqual(
            [(field["name"], field["group"]) for field in merged_with_new_field["fields"]],
            [("RATE", "basic"), ("POWER", "advanced"), ("TIME", "advanced")],
        )
        self.assertEqual(public_catalog(self.categories)["schema_version"], 2)

    def test_schema_marks_layout_as_unsaved_for_local_storage_migration(self):
        schema = apply_parameter_layout(
            "exp-a",
            {"experiment": "exp-a", "fields": [{"name": "POWER", "group": "basic"}]},
            self.categories,
        )
        self.assertFalse(schema["parameter_layout_saved"])

    def test_parameter_layout_rejects_missing_or_duplicate_fields(self):
        fields = [
            {"name": "POWER", "group": "basic"},
            {"name": "RATE", "group": "advanced"},
        ]
        with self.assertRaisesRegex(ValueError, "未归入"):
            set_parameter_layout(
                "exp-a",
                {"basic": ["POWER"], "advanced": []},
                fields,
                self.categories,
            )
        with self.assertRaisesRegex(ValueError, "重复"):
            set_parameter_layout(
                "exp-a",
                {"basic": ["POWER", "RATE"], "advanced": ["RATE"]},
                fields,
                self.categories,
            )

    def test_defaults_endpoint_confirms_persisted_parameter_layout(self):
        from GUI.backend import main as gui_main

        fields = [
            {"name": "POWER", "group": "basic", "default": 1.0},
            {"name": "ACQUISITION_MODE", "group": "advanced", "default": "both"},
        ]
        definition = MagicMock()
        definition.id = "exp-a"
        definition.schema.side_effect = lambda: {
            "experiment": "exp-a",
            "fields": [dict(field) for field in fields],
        }
        body = gui_main.ExperimentDefaultsBody(
            parameters={"POWER": 1.0, "ACQUISITION_MODE": "both"},
            parameter_layout=gui_main.ParameterLayoutBody(
                basic=["POWER", "ACQUISITION_MODE"],
                advanced=[],
            ),
        )
        with (
            patch.object(gui_main, "_experiment_or_404", return_value=definition),
            patch.object(gui_main, "_default_categories", return_value=self.categories),
        ):
            response = gui_main.save_experiment_defaults("exp-a", body)

        definition.save_defaults.assert_called_once_with(body.parameters)
        self.assertEqual(
            response["schema"]["parameter_layout"],
            {"basic": ["POWER", "ACQUISITION_MODE"], "advanced": []},
        )
        self.assertEqual(
            [(field["name"], field["group"]) for field in response["schema"]["fields"]],
            [("POWER", "basic"), ("ACQUISITION_MODE", "basic")],
        )


if __name__ == "__main__":
    unittest.main()
