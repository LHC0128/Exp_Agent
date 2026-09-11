"""Z 任意波电流闭环实验专属 summary 端点的契约测试。"""

import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'GUI'))
from backend import main


class ClosedLoopSummaryTests(unittest.TestCase):
    def test_summary_endpoint_rejects_other_experiments(self):
        with self.assertRaises(HTTPException) as context:
            main.experiment_run_summary("static-sensitivity", "anything")
        self.assertEqual(context.exception.status_code, 404)

    def test_summary_endpoint_rejects_path_traversal(self):
        with self.assertRaises(HTTPException) as context:
            main.experiment_run_summary(main.ZAW_CLOSED_LOOP_ID, "../etc")
        self.assertEqual(context.exception.status_code, 400)

    def test_summary_endpoint_404_for_missing_run(self):
        with self.assertRaises(HTTPException) as context:
            main.experiment_run_summary(main.ZAW_CLOSED_LOOP_ID, "does_not_exist")
        self.assertEqual(context.exception.status_code, 404)

    def test_summary_uses_existing_run(self):
        data_root = main.ROOT / "data" / "Z_AW_Closed_Loop_Waveform_Correction"
        target = None
        for candidate in data_root.iterdir():
            if not candidate.is_dir():
                continue
            if (candidate / "experiment_config.yaml").is_file():
                target = candidate
                break
        if target is None:
            self.skipTest("缺少 Z_AW_Closed_Loop_Waveform_Correction 真实运行目录")
        summary = main.experiment_run_summary(main.ZAW_CLOSED_LOOP_ID, target.name)
        self.assertEqual(summary.run_id, target.name)
        self.assertGreater(len(summary.parameters), 0)
        self.assertTrue(summary.iteration_count >= 0)
        self.assertIn("convergence.png", summary.artifacts)
        self.assertIn("waveform_comparison.png", summary.artifacts)


if __name__ == "__main__":
    unittest.main()
