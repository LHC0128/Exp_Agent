"""用假 GUI 接口（FastAPI TestClient）验证 lab_client 的授权、空闲检查、运行、取消与收尾等待。

不启动真实 GUI，不连接硬件；时钟与等待由测试控制，每次等待推进假任务一拍。
"""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tools import lab_client
from tools.lab_client import (
    EXIT_CLEANUP_UNCONFIRMED,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_RUN_FAILED,
    LabClient,
)

FIELDS = [
    {"name": "amp", "default": 0.1},
    {"name": "mode", "default": "sine"},
    {"name": "freqs", "default": [10, 20]},
]
WORKFLOW = {
    "run_step_id": "scan",
    "finish_step_id": "finish",
    "acquisition": [
        {"id": "prepare", "title": "准备", "dependencies": []},
        {"id": "scan", "title": "扫描", "dependencies": ["prepare"]},
        {"id": "finish", "title": "安全结束", "dependencies": []},
    ],
}


class FakeLab:
    """模拟 GUI 后端：任务每拍推进一次，取消后经若干拍安全收尾才进入 cancelled。"""

    def __init__(self):
        self.health = {"status": "ok", "hardware_busy": False, "queued_hardware_jobs": 0, "runtime_mode": "lab"}
        self.jobs: dict[str, dict] = {}
        self.extra_jobs: list[dict] = []
        self.calls: list[tuple] = []
        self.run_ticks: int | None = 2  # None：永不自行完成
        self.cleanup_ticks: int | None = 1  # None：取消后永不收尾
        self.session: dict | None = None
        self.step_status = "completed"

    def new_job(self, kind):
        job_id = f"job{len(self.jobs) + 1}"
        self.jobs[job_id] = {
            "id": job_id, "kind": kind, "status": "queued", "stage": "queued", "percent": 0,
            "message": "等待执行", "result": None, "error": None, "_ticks": 0, "_cancel": None,
        }
        return self.jobs[job_id]

    def tick(self):
        for job in self.jobs.values():
            if job["status"] in lab_client.TERMINAL_STATUSES:
                continue
            if job["_cancel"] is not None:
                job["_cancel"] += 1
                if self.cleanup_ticks is not None and job["_cancel"] >= self.cleanup_ticks:
                    job.update(status="cancelled", message="已安全停止")
                    if self.session and self.session["job_id"] == job["id"]:
                        self.session["status"] = "closed"
                continue
            job["status"] = "running"
            job["_ticks"] += 1
            if job["kind"].startswith("experiment:") and self.run_ticks is not None and job["_ticks"] >= self.run_ticks:
                job.update(status="completed", stage="complete", percent=100,
                           result={"run_dir": r"D:\Code\exp_agent_run\data\fake\run1"})
        session = self.session
        if session and session["status"] != "closed":
            if session["status"] == "queued":
                session["status"] = "waiting"
            elif session["pending_request"] == "steps" and self.run_ticks is not None:
                for step in session["_selected"]:
                    session["steps"][step] = {"status": self.step_status, "error": "", "artifacts": [f"{step}.npz"]}
                session.update(pending_request=None, revision=session["revision"] + 1, run_id="run42")
            elif session["pending_request"] == "finish":
                session["steps"]["finish"] = {"status": "completed", "error": "", "artifacts": []}
                session.update(status="closed", pending_request=None, safety_shutdown={"ok": True})
                self.jobs[session["job_id"]].update(status="completed", result={"run_id": "run42"})

    def public_session(self):
        return {key: value for key, value in self.session.items() if not key.startswith("_")}


def build_app(lab: FakeLab) -> FastAPI:
    app = FastAPI()
    experiments = {"one-shot": None, "session-exp": WORKFLOW}

    def experiment_or_404(experiment_id):
        if experiment_id not in experiments:
            raise HTTPException(404, "未知实验")
        return experiment_id

    @app.get("/api/health")
    def health():
        return lab.health

    @app.get("/api/jobs")
    def jobs():
        return [{"id": j["id"], "kind": j["kind"], "status": j["status"]} for j in lab.jobs.values()] + lab.extra_jobs

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return {k: v for k, v in lab.jobs[job_id].items() if not k.startswith("_")}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        job = lab.jobs[job_id]
        lab.calls.append(("cancel", job_id))
        if job["status"] in lab_client.TERMINAL_STATUSES:
            raise HTTPException(400, "任务已经结束，无法取消")
        job["_cancel"] = 0
        return job

    @app.get("/api/experiments/{experiment_id}")
    def experiment(experiment_id: str):
        experiment_or_404(experiment_id)
        return {"id": experiment_id, "title": experiment_id, "data_type": f"{experiment_id}-data", "archived": False}

    @app.get("/api/experiments/{experiment_id}/schema")
    def schema(experiment_id: str):
        experiment_or_404(experiment_id)
        return {"fields": FIELDS}

    @app.get("/api/experiments/{experiment_id}/workflow")
    def workflow(experiment_id: str):
        flow = experiments[experiment_or_404(experiment_id)]
        if flow is None:
            raise HTTPException(404, "Not Found")
        return flow

    @app.post("/api/experiments/{experiment_id}/preflight")
    def preflight(experiment_id: str, body: dict):
        lab.calls.append(("preflight", experiment_id, body["parameters"]))
        errors = ["amp 超出安全限值"] if body["parameters"]["amp"] > 1 else []
        return {"ok": not errors, "errors": errors, "acquisition": []}

    @app.post("/api/experiments/{experiment_id}/runs")
    def runs(experiment_id: str, body: dict):
        lab.calls.append(("run", experiment_id, body["parameters"]))
        return lab.new_job(f"experiment:{experiment_id}")

    @app.post("/api/experiments/session-exp/sessions")
    def create_session(body: dict):
        lab.calls.append(("session", body["parameters"]))
        job = lab.new_job("session:session-exp")
        lab.session = {"session_id": "s1", "job_id": job["id"], "revision": 0, "status": "queued",
                       "parameters": body["parameters"], "steps": {}, "run_id": None, "pending_request": None,
                       "error": None, "safety_shutdown": None, "_selected": []}
        return lab.public_session()

    @app.get("/api/experiments/session-exp/sessions/s1")
    def get_session():
        return lab.public_session()

    @app.post("/api/experiments/session-exp/sessions/s1/steps")
    def steps(body: dict):
        assert body["revision"] == lab.session["revision"]
        lab.calls.append(("steps", body["step_ids"]))
        lab.session.update(pending_request="steps", _selected=body["step_ids"])
        return lab.public_session()

    @app.post("/api/experiments/session-exp/sessions/s1/finish")
    def finish(body: dict):
        assert body["revision"] == lab.session["revision"]
        lab.calls.append(("finish",))
        lab.session["pending_request"] = "finish"
        return lab.public_session()

    @app.post("/api/devices/{device_id}/refresh")
    def refresh(device_id: str):
        lab.calls.append(("refresh", device_id))
        return {"id": device_id, "timebase": 0.001}

    return app


@pytest.fixture
def lab():
    return FakeLab()


@pytest.fixture
def cli(lab, capsys):
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds
        lab.tick()

    client = LabClient(
        TestClient(build_app(lab)), poll_s=1.0, clock=lambda: clock["now"], sleep=sleep,
        log=lambda message: None, run_root=Path(r"D:\Code\exp_agent_run"),
    )

    def run(*argv):
        code = lab_client.main(list(argv), client=client)
        out = capsys.readouterr()
        return code, (json.loads(out.out) if out.out else None), out.err

    return run


def started(lab):
    return [call for call in lab.calls if call[0] in ("run", "session")]


# ---------------------------------------------------------------- 开始前拒绝


@pytest.mark.parametrize("health, extra_jobs", [
    ({"hardware_busy": True}, []),
    ({"queued_hardware_jobs": 1}, []),
    ({"runtime_mode": "dev"}, []),
    ({}, [{"id": "other", "kind": "experiment:x", "status": "running"}]),
])
def test_refuses_when_gui_not_idle(lab, cli, health, extra_jobs):
    lab.health.update(health)
    lab.extra_jobs = extra_jobs
    code, _, err = cli("run", "one-shot", "--allow", "one-shot")
    assert code == EXIT_REFUSED and "拒绝" in err
    assert not started(lab)


@pytest.mark.parametrize("args, message", [
    (["--allow", "session-exp"], "授权清单"),
    (["--allow", "one-shot", "--param", "bogus=1", "--allow-param", "bogus=0:1"], "schema"),
    (["--allow", "one-shot", "--param", "amp=0.2"], "未经 --allow-param"),
    (["--allow", "one-shot", "--allow-param", "amp=0:0.5", "--param", "amp=0.8"], "超出授权区间"),
    (["--allow", "one-shot", "--allow-param", "freqs=0:50", "--param", "freqs=[10,60]"], "超出授权区间"),
    (["--allow", "one-shot", "--allow-param", "amp=0:0.5", "--param", "amp=\"0.2\""], "数值区间"),
    (["--allow", "one-shot", "--allow-param", "mode=sine|square", "--param", "mode=dc"], "授权候选"),
])
def test_refuses_unauthorized_requests(lab, cli, args, message):
    code, _, err = cli("run", "one-shot", *args)
    assert code == EXIT_REFUSED and message in err
    assert not lab.calls


def test_preflight_failure_does_not_start(lab, cli):
    code, _, err = cli("run", "one-shot", "--allow", "one-shot", "--allow-param", "amp=0:5", "--param", "amp=2")
    assert code == EXIT_REFUSED and "预检未通过" in err
    assert not started(lab)


def test_dry_run_only_preflights_with_defaults_merged(lab, cli):
    code, report, _ = cli("run", "one-shot", "--allow", "one-shot", "--allow-param", "mode=sine|square",
                          "--param", "mode=square", "--dry-run")
    assert code == EXIT_OK and report["status"] == "dry_run"
    assert report["parameters"] == {"amp": 0.1, "mode": "square", "freqs": [10, 20]}
    assert not started(lab)


# ---------------------------------------------------------------- 运行


def test_one_shot_run_completes_and_reports(lab, cli):
    code, report, _ = cli("run", "one-shot", "--allow", "one-shot", "--allow-param", "amp=0:0.5", "--param", "amp=0.3")
    assert code == EXIT_OK
    assert report["status"] == "completed" and report["mode"] == "job"
    assert report["parameters"]["amp"] == 0.3 and report["authorized_params"] == {"amp": "0:0.5"}
    assert report["run_dir"] == r"D:\Code\exp_agent_run\data\fake\run1"
    assert started(lab) == [("run", "one-shot", {"amp": 0.3, "mode": "sine", "freqs": [10, 20]})]
    assert not [call for call in lab.calls if call[0] == "cancel"]


def test_session_runs_selected_steps_then_safe_finish(lab, cli):
    code, report, _ = cli("run", "session-exp", "--allow", "session-exp")
    assert code == EXIT_OK and report["status"] == "completed" and report["mode"] == "session"
    names = [call[0] for call in lab.calls]
    assert names == ["preflight", "session", "steps", "finish"]
    assert ("steps", ["prepare", "scan"]) in lab.calls
    assert report["run_dir"] == str(Path(r"D:\Code\exp_agent_run") / "data" / "session-exp-data" / "run42")
    assert report["step_states"]["scan"]["artifacts"] == ["scan.npz"]


def test_session_failed_step_still_finishes_and_exits_nonzero(lab, cli):
    lab.step_status = "failed"
    code, report, _ = cli("run", "session-exp", "--allow", "session-exp", "--steps", "prepare")
    assert code == EXIT_RUN_FAILED
    assert ("finish",) in lab.calls and ("steps", ["prepare"]) in lab.calls


def test_session_rejects_finish_or_unknown_step(lab, cli):
    code, _, err = cli("run", "session-exp", "--allow", "session-exp", "--steps", "finish")
    assert code == EXIT_REFUSED and "不可直接选择" in err
    assert not lab.calls


# ---------------------------------------------------------------- 超时、取消与收尾


def test_timeout_cancels_and_waits_for_safe_cleanup(lab, cli):
    lab.run_ticks, lab.cleanup_ticks = None, 3
    code, report, _ = cli("run", "one-shot", "--allow", "one-shot", "--timeout", "5")
    assert code == EXIT_RUN_FAILED
    assert report["interrupted"] == "timeout" and report["status"] == "cancelled"
    assert ("cancel", "job1") in lab.calls


def test_session_timeout_cancels_session_job(lab, cli):
    lab.run_ticks = None
    code, report, _ = cli("run", "session-exp", "--allow", "session-exp", "--timeout", "5")
    assert code == EXIT_RUN_FAILED and report["status"] == "cancelled"
    assert ("cancel", "job1") in lab.calls and ("finish",) not in lab.calls


def test_cleanup_not_confirmed_exits_with_dedicated_code(lab, cli):
    lab.run_ticks, lab.cleanup_ticks = None, None
    code, _, err = cli("run", "one-shot", "--allow", "one-shot", "--timeout", "3", "--cleanup-timeout", "4")
    assert code == EXIT_CLEANUP_UNCONFIRMED and "安全收尾未确认" in err


def test_keyboard_interrupt_cancels_before_exit(lab, cli, monkeypatch):
    lab.run_ticks = None
    original = LabClient.wait_job
    state = {"first": True}

    def interrupted(self, job_id, deadline):
        if state.pop("first", False):
            raise KeyboardInterrupt
        return original(self, job_id, deadline)

    monkeypatch.setattr(LabClient, "wait_job", interrupted)
    code, report, _ = cli("run", "one-shot", "--allow", "one-shot")
    assert code == EXIT_RUN_FAILED
    assert report["interrupted"] == "keyboard_interrupt" and report["status"] == "cancelled"


def test_cancel_command_waits_until_terminal(lab, cli):
    lab.run_ticks = None
    lab.new_job("experiment:one-shot")
    code, report, _ = cli("cancel", "job1")
    assert code == EXIT_OK and report["status"] == "cancelled"


# ---------------------------------------------------------------- 只读查询


def test_show_lists_fields_and_steps(cli):
    code, report, _ = cli("show", "session-exp")
    assert code == EXIT_OK and report["mode"] == "session"
    assert [field["name"] for field in report["fields"]] == ["amp", "mode", "freqs"]
    assert report["finish_step_id"] == "finish"


def test_device_read_requires_idle(lab, cli):
    lab.health["hardware_busy"] = True
    code, _, _ = cli("device-read", "scope1")
    assert code == EXIT_REFUSED and not lab.calls
    lab.health["hardware_busy"] = False
    code, report, _ = cli("device-read", "scope1")
    assert code == EXIT_OK and report["timebase"] == 0.001
    assert lab.calls == [("refresh", "scope1")]


def test_tool_exposes_no_configuration_writes():
    source = Path(lab_client.__file__).read_text(encoding="utf-8")
    for forbidden in ("safety_limits", "/api/wiring", "physical-mappings", "device-library", "/defaults",
                      "control-targets", "/api/restart", "self.http.put", "\"PUT\"", "\"DELETE\""):
        assert forbidden not in source
