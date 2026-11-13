"""代理经运行目录 GUI 后端接口做实验的命令行客户端。

只连接本机运行目录 GUI（``http://127.0.0.1:8000``），全程复用 GUI 的硬件队列/锁、
安全限值、温控时序、取消检查点与安全收尾；本工具不直接连接仪器，也不提供修改
安全限值、接线、映射、设备库或默认参数的能力。

用法（在开发目录运行）::

    python -m tools.lab_client status
    python -m tools.lab_client show <实验ID>
    python -m tools.lab_client run <实验ID> --allow <实验ID> \\
        --allow-param rf_amplitude=0.1:0.5 --param rf_amplitude=0.2 [--steps a,b] [--dry-run]
    python -m tools.lab_client cancel <任务ID>
    python -m tools.lab_client device-read <设备ID>

退出码：0 实验完成/命令成功；1 实验以失败或取消结束；2 开始前被拒绝或接口报错；
3 已请求取消但未在时限内确认安全收尾，需人工到 GUI 核查。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

BASE_URL = "http://127.0.0.1:8000"
RUN_ROOT = Path(r"D:\Code\exp_agent_run")
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
FAILED_STEP_STATUSES = frozenset({"failed", "quality_failed", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})

EXIT_OK, EXIT_RUN_FAILED, EXIT_REFUSED, EXIT_CLEANUP_UNCONFIRMED = 0, 1, 2, 3


class LabClientError(Exception):
    """开始前拒绝或接口报错；此时未启动新的硬件任务。"""


class LabApiError(LabClientError):
    def __init__(self, method: str, path: str, status: int, detail: Any):
        super().__init__(f"{method} {path} 返回 {status}：{detail}")
        self.status = status
        self.detail = detail


class CleanupUnconfirmed(Exception):
    """已请求取消，但未在时限内看到任务结束（安全收尾未确认）。"""


class WaitTimeout(Exception):
    """等待超过运行时限。"""


# ---------------------------------------------------------------- 授权与参数


def parse_value(text: str) -> Any:
    """命令行参数值按 JSON 解析（数字、布尔、列表），解析失败按字符串。"""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _split_assignment(text: str, option: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep or not name.strip():
        raise LabClientError(f"{option} 需要 名称=值 形式：{text!r}")
    return name.strip(), value


@dataclass(frozen=True)
class ParamBound:
    """用户当次授权的参数范围：数值区间或候选值。"""

    name: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] | None = None

    @classmethod
    def parse(cls, text: str) -> "ParamBound":
        name, spec = _split_assignment(text, "--allow-param")
        if "|" in spec:
            return cls(name, choices=tuple(parse_value(item) for item in spec.split("|")))
        if ":" in spec:
            low, _, high = spec.partition(":")
            try:
                minimum = float(low) if low.strip() else None
                maximum = float(high) if high.strip() else None
            except ValueError as exc:
                raise LabClientError(f"--allow-param 区间需为数值：{text!r}") from exc
            if minimum is not None and maximum is not None and minimum > maximum:
                raise LabClientError(f"--allow-param 下限大于上限：{text!r}")
            return cls(name, minimum=minimum, maximum=maximum)
        return cls(name, choices=(parse_value(spec),))

    def describe(self) -> str:
        if self.choices is not None:
            return "|".join(json.dumps(item, ensure_ascii=False) for item in self.choices)
        low = "" if self.minimum is None else f"{self.minimum:g}"
        high = "" if self.maximum is None else f"{self.maximum:g}"
        return f"{low}:{high}"

    def check(self, value: Any) -> None:
        if self.choices is not None:
            if value not in self.choices:
                raise LabClientError(f"参数 {self.name}={value!r} 不在授权候选 {self.describe()} 内")
            return
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise LabClientError(f"参数 {self.name} 授权为数值区间，收到 {value!r}")
            if (self.minimum is not None and item < self.minimum) or (
                self.maximum is not None and item > self.maximum
            ):
                raise LabClientError(f"参数 {self.name}={value!r} 超出授权区间 {self.describe()}")


def resolve_parameters(
    fields: list[dict[str, Any]],
    overrides: dict[str, Any],
    bounds: dict[str, ParamBound],
) -> dict[str, Any]:
    """以 GUI 当前默认值为底，只允许在授权范围内覆盖 schema 字段。"""
    names = {field["name"] for field in fields}
    unknown = sorted((bounds.keys() | overrides.keys()) - names)
    if unknown:
        raise LabClientError("不是该实验 schema 内的参数：" + "、".join(unknown))
    unauthorized = sorted(overrides.keys() - bounds.keys())
    if unauthorized:
        raise LabClientError("参数未经 --allow-param 授权：" + "、".join(unauthorized))
    for name, value in overrides.items():
        bounds[name].check(value)
    values = {field["name"]: field.get("default") for field in fields}
    values.update(overrides)
    return values


# ---------------------------------------------------------------- 客户端


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class LabClient:
    """GUI 接口的薄封装；http 为 httpx.Client 兼容对象（测试注入 TestClient）。"""

    def __init__(
        self,
        http: Any,
        *,
        poll_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = _stderr,
        run_root: Path = RUN_ROOT,
    ):
        self.http, self.poll_s, self.clock, self.sleep, self.log = http, poll_s, clock, sleep, log
        self.run_root = run_root

    # -- HTTP

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        response = self.http.request(method, path, json=body)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise LabApiError(method, path, response.status_code, detail)
        return response.json() if response.content else None

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: Any = None) -> Any:
        return self._request("POST", path, body if body is not None else {})

    # -- 只读查询

    def status(self) -> dict[str, Any]:
        return {"health": self.get("/api/health"), "jobs": self.get("/api/jobs")}

    def ensure_idle(self) -> dict[str, Any]:
        """确认连的是运行目录 GUI，且硬件空闲、无排队或未结束任务。"""
        state = self.status()
        health = state["health"]
        if health.get("runtime_mode") != "lab":
            raise LabClientError(f"8000 端口不是运行目录 GUI（runtime_mode={health.get('runtime_mode')!r}）")
        if health.get("hardware_busy"):
            raise LabClientError("GUI 硬件正忙，不开始")
        if health.get("queued_hardware_jobs"):
            raise LabClientError(f"GUI 有 {health['queued_hardware_jobs']} 个排队的硬件任务，不开始")
        active = [job for job in state["jobs"] if job.get("status") in ACTIVE_STATUSES]
        if active:
            listed = "、".join(f"{job['id']}({job.get('kind')})" for job in active)
            raise LabClientError(f"GUI 有未结束的任务：{listed}，不开始")
        return state

    def workflow(self, experiment_id: str) -> dict[str, Any] | None:
        """分步会话实验返回流程定义；一次性运行实验返回 None。"""
        try:
            return self.get(f"/api/experiments/{experiment_id}/workflow")
        except LabApiError as exc:
            if exc.status == 404:
                return None
            raise

    def show(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.get(f"/api/experiments/{experiment_id}")
        schema = self.get(f"/api/experiments/{experiment_id}/schema")
        flow = self.workflow(experiment_id)
        result = {
            "experiment_id": experiment_id,
            "title": experiment.get("title"),
            "data_type": experiment.get("data_type"),
            "mode": "session" if flow else "job",
            "fields": [
                {key: field.get(key) for key in ("name", "default", "label", "unit", "min", "max") if key in field}
                for field in schema.get("fields", [])
            ],
        }
        if flow:
            result["steps"] = [
                {"id": stage["id"], "title": stage.get("title"), "dependencies": stage.get("dependencies", [])}
                for stage in flow["acquisition"]
            ]
            result["finish_step_id"] = flow["finish_step_id"]
        return result

    def read_device(self, device_id: str) -> dict[str, Any]:
        """经 GUI 的“刷新设备状态”读取仪器设置；走 GUI 短时硬件锁，忙时被拒绝。"""
        self.ensure_idle()
        return self.post(f"/api/devices/{device_id}/refresh")

    # -- 等待、取消

    def _wait(self, fetch: Callable[[], Any], done: Callable[[Any], bool], deadline: float) -> Any:
        while True:
            value = fetch()
            if done(value):
                return value
            if self.clock() >= deadline:
                raise WaitTimeout()
            self.sleep(self.poll_s)

    def job(self, job_id: str) -> dict[str, Any]:
        return self.get(f"/api/jobs/{job_id}")

    def wait_job(self, job_id: str, deadline: float) -> dict[str, Any]:
        last = {"stage": None}

        def fetch():
            job = self.job(job_id)
            marker = (job.get("status"), job.get("stage"), job.get("percent"))
            if marker != last["stage"]:
                last["stage"] = marker
                self.log(f"[{job_id}] {job.get('status')} {job.get('stage')} {job.get('percent')}% {job.get('message', '')}")
            return job

        return self._wait(fetch, lambda job: job.get("status") in TERMINAL_STATUSES, deadline)

    def cancel_and_wait(self, job_id: str, cleanup_timeout: float) -> dict[str, Any]:
        """请求取消，并等待任务结束（GUI 只在安全收尾完成后才标记结束）。"""
        self.log(f"[{job_id}] 请求取消，等待安全收尾")
        try:
            self.post(f"/api/jobs/{job_id}/cancel")
        except LabApiError as exc:
            if exc.status != 400:  # 400：任务已结束
                raise
        try:
            return self.wait_job(job_id, self.clock() + cleanup_timeout)
        except WaitTimeout as exc:
            raise CleanupUnconfirmed(
                f"任务 {job_id} 在 {cleanup_timeout:g} s 内未结束，安全收尾未确认，请到 GUI 核查"
            ) from exc

    # -- 运行

    def run(
        self,
        experiment_id: str,
        *,
        allowed_experiments: list[str],
        bounds: list[ParamBound],
        overrides: dict[str, Any],
        steps: list[str] | None = None,
        timeout: float = 3600.0,
        cleanup_timeout: float = 600.0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if experiment_id not in allowed_experiments:
            raise LabClientError(f"实验 {experiment_id} 不在本次 --allow 授权清单内")
        bound_map = {bound.name: bound for bound in bounds}
        if len(bound_map) != len(bounds):
            raise LabClientError("--allow-param 重复授权同一参数")
        experiment = self.get(f"/api/experiments/{experiment_id}")
        if experiment.get("archived"):
            raise LabClientError(f"实验 {experiment_id} 已归档")
        schema = self.get(f"/api/experiments/{experiment_id}/schema")
        parameters = resolve_parameters(schema.get("fields", []), overrides, bound_map)
        flow = self.workflow(experiment_id)
        selected = self._select_steps(flow, steps)
        report: dict[str, Any] = {
            "experiment_id": experiment_id,
            "mode": "session" if flow else "job",
            "authorized_params": {name: bound.describe() for name, bound in bound_map.items()},
            "overrides": overrides,
            "parameters": parameters,
            "steps": selected,
            "run_root": str(self.run_root),
            "data_type": experiment.get("data_type"),
        }
        self.ensure_idle()
        preflight = self.post(f"/api/experiments/{experiment_id}/preflight", {"parameters": parameters})
        report["preflight"] = preflight
        if not preflight.get("ok"):
            raise LabClientError("GUI 预检未通过：" + "；".join(map(str, preflight.get("errors", []))))
        if dry_run:
            report["status"] = "dry_run"
            return report

        deadline = self.clock() + timeout
        if flow:
            return self._run_session(experiment_id, flow, parameters, selected, report, deadline, cleanup_timeout)
        job = self.post(f"/api/experiments/{experiment_id}/runs", {"parameters": parameters})
        report["job_id"] = job["id"]
        self.log(f"已开始实验 {experiment_id}，任务 {job['id']}")
        final = self._guarded(lambda: self.wait_job(job["id"], deadline), job["id"], cleanup_timeout, report)
        return self._finish_job_report(report, final)

    @staticmethod
    def _select_steps(flow: dict[str, Any] | None, steps: list[str] | None) -> list[str] | None:
        if flow is None:
            if steps:
                raise LabClientError("该实验是一次性运行，不支持 --steps")
            return None
        available = [stage["id"] for stage in flow["acquisition"] if stage["id"] != flow["finish_step_id"]]
        if not steps:
            return available
        unknown = [step for step in steps if step not in available]
        if unknown:
            raise LabClientError("未知或不可直接选择的步骤：" + "、".join(unknown) + "；可选：" + "、".join(available))
        return steps

    def _guarded(self, action: Callable[[], Any], job_id: str, cleanup_timeout: float, report: dict[str, Any]) -> Any:
        """任务开始后，超时、Ctrl+C 或接口异常都先取消任务，等安全收尾完成后再返回。"""
        try:
            return action()
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, WaitTimeout):
                reason = "timeout"
            elif isinstance(exc, KeyboardInterrupt):
                reason = "keyboard_interrupt"
            else:
                reason = f"error: {exc}"
            report["interrupted"] = reason
            self.log(f"运行中断（{reason}），取消任务 {job_id}")
            try:
                return self.cancel_and_wait(job_id, cleanup_timeout)
            except CleanupUnconfirmed:
                raise
            except Exception as cancel_exc:
                raise CleanupUnconfirmed(
                    f"任务 {job_id} 运行中断（{reason}）后取消失败：{cancel_exc}；安全收尾未确认，请到 GUI 核查"
                ) from cancel_exc

    def _finish_job_report(self, report: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        report["status"] = job.get("status")
        report["message"] = job.get("message")
        report["error"] = job.get("error")
        result = job.get("result")
        report["result"] = result
        if isinstance(result, dict):
            for key in ("run_dir", "run_directory", "output_dir"):
                if isinstance(result.get(key), str):
                    report["run_dir"] = result[key]
                    break
        return report

    def _run_session(
        self,
        experiment_id: str,
        flow: dict[str, Any],
        parameters: dict[str, Any],
        selected: list[str],
        report: dict[str, Any],
        deadline: float,
        cleanup_timeout: float,
    ) -> dict[str, Any]:
        base = f"/api/experiments/{experiment_id}/sessions"
        session = self.post(base, {"parameters": parameters})
        session_id, job_id = session["session_id"], session["job_id"]
        report.update(session_id=session_id, job_id=job_id)
        self.log(f"已建立会话 {session_id}（任务 {job_id}）")

        def fetch():
            return self.get(f"{base}/{session_id}")

        def idle(value):
            return value["status"] == "closed" or (value["status"] == "waiting" and not value.get("pending_request"))

        def body():
            state = self._wait(fetch, idle, deadline)
            if state["status"] == "waiting":
                self.log(f"运行步骤：{'、'.join(selected)}")
                self.post(f"{base}/{session_id}/steps", {"revision": state["revision"], "step_ids": selected})
                state = self._wait(fetch, idle, deadline)
            if state["status"] == "waiting":
                report["step_error"] = state.get("error")
                self.log("执行安全结束节点")
                self.post(f"{base}/{session_id}/finish", {"revision": state["revision"]})
            return self.wait_job(job_id, deadline)

        job = self._guarded(body, job_id, cleanup_timeout, report)
        self._finish_job_report(report, job)
        state = fetch()
        report["session_error"] = state.get("error")
        report["run_id"] = state.get("run_id")
        report["step_states"] = {
            step_id: {"status": value.get("status"), "error": value.get("error"), "artifacts": value.get("artifacts", [])}
            for step_id, value in state.get("steps", {}).items()
        }
        report["safety_shutdown"] = state.get("safety_shutdown")
        if state.get("run_id") and report.get("data_type"):
            report["run_dir"] = str(self.run_root / "data" / report["data_type"] / state["run_id"])
        return report


# ---------------------------------------------------------------- 命令行


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.lab_client", description="经运行目录 GUI 接口做实验")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="查看 GUI 健康状态与任务列表")
    show = sub.add_parser("show", help="查看实验参数、默认值与分步步骤")
    show.add_argument("experiment_id")
    run = sub.add_parser("run", help="在授权范围内运行实验")
    run.add_argument("experiment_id")
    run.add_argument("--allow", action="append", default=[], required=True, help="本次授权的实验 ID，可重复")
    run.add_argument("--allow-param", action="append", default=[], help="授权参数范围：名=下:上 或 名=a|b，可重复")
    run.add_argument("--param", action="append", default=[], help="覆盖参数：名=值（JSON 解析），可重复")
    run.add_argument("--steps", help="分步实验要运行的采集步骤，逗号分隔；默认全部")
    run.add_argument("--timeout", type=float, default=3600.0, help="运行时限（秒），超时即取消")
    run.add_argument("--cleanup-timeout", type=float, default=600.0, help="取消后等待安全收尾的时限（秒）")
    run.add_argument("--dry-run", action="store_true", help="只做 GUI 预检，不开始实验")
    cancel = sub.add_parser("cancel", help="取消任务并等待安全收尾")
    cancel.add_argument("job_id")
    cancel.add_argument("--cleanup-timeout", type=float, default=600.0)
    device = sub.add_parser("device-read", help="经 GUI 只读刷新设备状态")
    device.add_argument("device_id")
    return parser


def execute(argv: list[str], client: LabClient) -> tuple[int, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        return EXIT_OK, client.status()
    if args.command == "show":
        return EXIT_OK, client.show(args.experiment_id)
    if args.command == "device-read":
        return EXIT_OK, client.read_device(args.device_id)
    if args.command == "cancel":
        job = client.cancel_and_wait(args.job_id, args.cleanup_timeout)
        return EXIT_OK, {"job_id": args.job_id, "status": job.get("status"), "message": job.get("message")}
    overrides = dict(_split_assignment(item, "--param") for item in args.param)
    report = client.run(
        args.experiment_id,
        allowed_experiments=args.allow,
        bounds=[ParamBound.parse(item) for item in args.allow_param],
        overrides={name: parse_value(value) for name, value in overrides.items()},
        steps=[step.strip() for step in args.steps.split(",") if step.strip()] if args.steps else None,
        timeout=args.timeout,
        cleanup_timeout=args.cleanup_timeout,
        dry_run=args.dry_run,
    )
    failed_steps = [
        step_id for step_id, value in report.get("step_states", {}).items()
        if value.get("status") in FAILED_STEP_STATUSES
    ]
    ok = (report["status"] in ("completed", "dry_run") and not report.get("session_error")
          and not report.get("interrupted") and not failed_steps)
    return (EXIT_OK if ok else EXIT_RUN_FAILED), report


def main(argv: list[str] | None = None, client: LabClient | None = None) -> int:
    if client is None:
        # trust_env=False：不经系统代理访问本机 GUI。
        client = LabClient(httpx.Client(base_url=BASE_URL, timeout=30.0, trust_env=False))
    try:
        code, payload = execute(sys.argv[1:] if argv is None else argv, client)
    except CleanupUnconfirmed as exc:
        _stderr(f"错误：{exc}")
        return EXIT_CLEANUP_UNCONFIRMED
    except LabClientError as exc:
        _stderr(f"拒绝：{exc}")
        return EXIT_REFUSED
    except (OSError, httpx.TransportError) as exc:
        _stderr(f"无法连接运行目录 GUI（{BASE_URL}）：{exc}")
        return EXIT_REFUSED
    except KeyboardInterrupt:
        _stderr("错误：等待安全收尾时再次中断，安全收尾未确认，请到 GUI 核查")
        return EXIT_CLEANUP_UNCONFIRMED
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
