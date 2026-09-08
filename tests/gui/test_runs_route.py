import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.main import runs


class FakePath:
    """模拟运行目录树，可注入并发删除时的 OSError。"""

    def __init__(
        self,
        name,
        *,
        is_dir=True,
        is_file=False,
        children=(),
        exists=True,
        stat_value=1.0,
        stat_error=None,
        iterdir_error=None,
    ):
        self.name = name
        self._is_dir = is_dir
        self._is_file = is_file
        self._children = list(children)
        self._exists = exists
        self._stat_value = stat_value
        self._stat_error = stat_error
        self._iterdir_error = iterdir_error

    def __truediv__(self, other):
        for child in self._children:
            if child.name == str(other):
                return child
        return FakePath(str(other), exists=False, is_dir=False, is_file=False)

    def __str__(self) -> str:
        return self.name

    def exists(self) -> bool:
        return self._exists

    def is_dir(self) -> bool:
        return self._is_dir

    def is_file(self) -> bool:
        return self._is_file

    def iterdir(self):
        if self._iterdir_error is not None:
            raise self._iterdir_error
        return iter(self._children)

    def stat(self):
        if self._stat_error is not None:
            raise self._stat_error
        return SimpleNamespace(st_mtime=self._stat_value)


class RunsRouteRaceTests(unittest.TestCase):
    def _call(self, base) -> dict:
        definition = SimpleNamespace(
            id="demo",
            title="演示实验",
            data_type="Demo_Type",
            analysis_runner=None,
        )
        with patch("backend.main.list_experiments", return_value=[definition]), \
                patch("backend.main.ROOT", FakePath("root", children=[
                    FakePath("data", children=[base]),
                ])):
            return runs()

    def test_deleted_run_dir_is_skipped_instead_of_500(self):
        base = FakePath("Demo_Type", children=[
            FakePath("run_a", stat_value=2.0, children=[
                FakePath("results", children=[FakePath("a.png", is_dir=False, is_file=True)]),
            ]),
            # 并发删除：列目录时还在，stat 时已消失。
            FakePath("run_b", stat_value=3.0, stat_error=FileNotFoundError("并发删除")),
        ])
        result = self._call(base)
        self.assertEqual(result["total"], 1)
        self.assertEqual([item["id"] for item in result["runs"]], ["run_a"])
        self.assertEqual(result["runs"][0]["artifacts"], ["a.png"])

    def test_base_removed_during_iterdir_returns_empty_list(self):
        base = FakePath(
            "Demo_Type",
            iterdir_error=FileNotFoundError("数据目录被并发删除"),
        )
        result = self._call(base)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["runs"], [])

    def test_results_dir_removed_during_listing_keeps_run_without_artifacts(self):
        base = FakePath("Demo_Type", children=[
            FakePath("run_a", stat_value=2.0, children=[
                FakePath(
                    "results",
                    iterdir_error=FileNotFoundError("结果目录被并发删除"),
                ),
            ]),
        ])
        result = self._call(base)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["runs"][0]["id"], "run_a")
        self.assertEqual(result["runs"][0]["artifacts"], [])

    def test_missing_base_is_skipped(self):
        base = FakePath("Demo_Type", exists=False, is_dir=False)
        result = self._call(base)
        self.assertEqual(result["total"], 0)


if __name__ == "__main__":
    unittest.main()
