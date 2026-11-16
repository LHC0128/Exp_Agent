"""Z 链路零点与脉冲角稳定性在新实验中心的注册、流程接线与前端 fixture 一致性。"""
import json
from pathlib import Path

from lab_workflows.experiment_modules.z_chain_stability import stages
from lab_workflows.experiment_modules.z_chain_stability.definition import ADAPTER

from . import main

ID = "z-chain-stability"
FIXTURE = Path(__file__).resolve().parents[1] / "frontend/src/pages/ZChainStabilityPage.fixture.json"


def test_registered_in_main_app():
    assert ID in main.WORKFLOW_RUN_FORMATS
    assert main.z_chain_stability_sessions in main._workflow_session_services()


def test_gui_fixture_matches_schema_and_workflow():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["fields"] == json.loads(json.dumps(ADAPTER.schema()["fields"], ensure_ascii=False))
    assert fixture["flow"] == json.loads(json.dumps(stages.definition(), ensure_ascii=False))
