"""射频相位响应在新实验中心的注册、流程接线与前端 fixture 一致性。"""
import json
from pathlib import Path

from lab_workflows.experiment_modules.rf_phase_response import stages
from lab_workflows.experiment_modules.rf_phase_response.definition import ADAPTER

from . import main

ID = "rf-phase-response"
FIXTURE = Path(__file__).resolve().parents[1] / "frontend/src/pages/RfPhaseResponsePage.fixture.json"


def test_registered_in_main_app():
    assert main.rf_phase_response_sessions in main._workflow_session_services()


def test_gui_fixture_matches_schema_and_workflow():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["fields"] == json.loads(json.dumps(ADAPTER.schema()["fields"], ensure_ascii=False))
    assert fixture["flow"] == json.loads(json.dumps(stages.definition(), ensure_ascii=False))


def test_session_snapshot_matches_strict_model():
    """会话快照只能含 XYSession 已声明键；实验专属读数必须放进 daq_settings。"""
    from lab_workflows.experiment_modules.rf_phase_response.models import RfPhaseResponseParams
    from lab_workflows.experiment_modules.rf_phase_response.workflow import Workflow

    from .workflow_sessions import XYSession

    workflow = Workflow(RfPhaseResponseParams(), project_root=Path(__file__).resolve().parents[2],
                        session_id="snapshot-test")
    XYSession.model_validate(dict(workflow.snapshot(), job_id="job-test"))
