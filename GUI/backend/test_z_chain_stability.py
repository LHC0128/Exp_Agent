"""Z 链路零点与脉冲角稳定性在新实验中心的注册与流程接线。"""
from . import main

ID = "z-chain-stability"


def test_registered_in_main_app():
    assert ID in main.WORKFLOW_RUN_FORMATS
    assert main.z_chain_stability_sessions in main._workflow_session_services()
