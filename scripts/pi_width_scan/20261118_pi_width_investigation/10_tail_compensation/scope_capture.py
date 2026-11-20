"""示波器 Px 快照：经 GUI 会话驱动 Z 脉冲（bperp_null A01–A03，A04 收尾在 finally），会话等待期间
在关温控窗口内冻结 DHO924 画面，恢复温控后读出并保存。用户 2026-11-18 授权。

直连仪器只有两台：
- DHO924：读设置、STOP、读波形、RUN；不改量程和时基。
- Temp_Switch（DG900）：关闭约 0.3 s 后恢复。
功放、Z、X/Y、触发均由 GUI 会话控制，其安全收尾由 A04 完成。

用运行目录解释器在运行目录执行：
    agent_exp_env/Scripts/python.exe scripts/pi_width_scan/20261118_pi_width_investigation/10_tail_compensation/scope_capture.py（在运行目录执行）
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # 仓库根；真实仪器只在运行目录放行
sys.path.insert(0, str(ROOT))
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402
from lab_workflows.common import data_root  # noqa: E402
from lab_workflows.steps.temperature import set_temperature_switch  # noqa: E402
from oscilloscope import create_oscilloscope, create_scope_acquisition  # noqa: E402
from signal_generator import DG900Instrument  # noqa: E402
import types as _types  # noqa: E402
sys.modules.setdefault("httpx", _types.ModuleType("httpx"))  # lab_client 只在 CLI main 中用 httpx
from tools.lab_client import LabClient, ParamBound, resolve_parameters  # noqa: E402

EXP = "bperp-null"
SETTLE_S = 0.2
BASE_OVR = dict(Y_BIAS_MV=-15.1, X_SCAN_START_MV=0, X_SCAN_STOP_MV=0, X_SCAN_STEP_MV=1,
                Y_SCAN_START_MV=0, Y_SCAN_STOP_MV=0, Y_SCAN_STEP_MV=1, N_REPEAT=1)
WAVES = {
    "builtin66": dict(Z_PULSE_WIDTH_US=6.6, Z_WAVEFORM_FROM_FILE=False),
    "cand1_w8.20_tc25.5": dict(Z_PULSE_WIDTH_US=8.2, Z_WAVEFORM_FROM_FILE=True, Z_WAVEFORM_FILE="z_tail_w8.20_A0.114_tc25.5.csv"),
    "cand2_w7.50_tc35.6": dict(Z_PULSE_WIDTH_US=7.5, Z_WAVEFORM_FROM_FILE=True, Z_WAVEFORM_FILE="z_tail_w7.50_exact_sf0.594_te59.9.csv"),
}
BIASES = {"b0": 89.1, "bx40": 129.1}
BOUNDS = {b.name: b for b in [
    ParamBound("X_BIAS_MV", choices=(89.1, 129.1)), ParamBound("Y_BIAS_MV", choices=(-15.1,)),
    ParamBound("X_SCAN_START_MV", choices=(0,)), ParamBound("X_SCAN_STOP_MV", choices=(0,)), ParamBound("X_SCAN_STEP_MV", choices=(1,)),
    ParamBound("Y_SCAN_START_MV", choices=(0,)), ParamBound("Y_SCAN_STOP_MV", choices=(0,)), ParamBound("Y_SCAN_STEP_MV", choices=(1,)),
    ParamBound("N_REPEAT", choices=(1,)), ParamBound("Z_PULSE_WIDTH_US", choices=(6.6, 8.2, 7.5)),
    ParamBound("Z_WAVEFORM_FROM_FILE", choices=(True, False)),
    ParamBound("Z_WAVEFORM_FILE", choices=("z_tail_w8.20_A0.114_tc25.5.csv", "z_tail_w7.50_exact_sf0.594_te59.9.csv"))]}

dev = yaml.safe_load((ROOT / "params/devices.yaml").read_text(encoding="utf-8"))
mp = yaml.safe_load((ROOT / "params/mapping.yaml").read_text(encoding="utf-8"))
devices = dev.get("devices") or dev
mapping = mp.get("mapping", mp)
TS_RES = devices[mapping["Temp_Switch"]["device_id"]]["resource"]
TS_CH = int(mapping["Temp_Switch"]["endpoint"]["index"])
SCOPE_CFG = devices["rigol_dho900_192_168_124_21"]


class _Resp:
    def __init__(self, status, body):
        self.status_code, self.content = status, body
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class UrllibHttp:
    """httpx.Client.request 的最小替身（运行目录环境没有 httpx）。"""
    def __init__(self, base):
        self.base = base

    def request(self, method, path, json=None):
        data = None if json is None else __import__("json").dumps(json).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return _Resp(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Resp(e.code, e.read())


def capture(label, out_root):
    scope = create_oscilloscope(SCOPE_CFG); scope.connect()
    stopped = False
    try:
        info = {"idn": scope.idn().strip(), "tdiv_s": scope.get_timebase_scale(), "delay_s": scope.get_timebase_delay(),
                "trig_status": scope.trigger_status().strip()}
        chans = [c for c in (1, 2, 3, 4) if scope.get_channel_state(c)]
        info["channels"] = {c: {"scale": scope.get_channel_scale(c), "offset": scope.get_channel_offset(c)} for c in chans}
        frame_s = 10 * info["tdiv_s"]
        if frame_s > 2.0:
            raise RuntimeError(f"帧长 {frame_s:.3g} s 过长，未关温控")
        dg = DG900Instrument(TS_RES, channel=TS_CH); dg.connect()
        try:
            was_on = dg.get_output(TS_CH) and dg.get_offset(TS_CH) > 2.5
            set_temperature_switch(dg, False, channel=TS_CH)
            t_off = time.time()
            try:
                time.sleep(SETTLE_S + 2.5 * frame_s + 0.1)
                scope.trigger_stop(); stopped = True
                t_stop = time.time()
            finally:
                if was_on:
                    set_temperature_switch(dg, True, channel=TS_CH)
                t_on = time.time()
            info.update(temp_off_window_s=t_on - t_off, stop_after_off_s=t_stop - t_off, temp_restored=bool(was_on))
        finally:
            dg.disconnect()
        acq = create_scope_acquisition(scope, maximum_points=5_000_000)
        res = {c: acq.acquire_channel(c, info["tdiv_s"]) for c in chans}
    finally:
        if stopped:
            scope.trigger_run()
        scope.disconnect()
    out = out_root / label
    out.mkdir(parents=True)
    np.savez(out / "waveforms.npz", **{f"t{c}": r.time for c, r in res.items()}, **{f"v{c}": r.voltage for c, r in res.items()})
    (out / "capture_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out


def one(client, fields, label, overrides, out_root, log):
    params = resolve_parameters(fields, overrides, BOUNDS)
    base = f"/api/experiments/{EXP}/sessions"
    session = client.post(base, {"parameters": params})
    sid, jid = session["session_id"], session["job_id"]
    fetch = lambda: client.get(f"{base}/{sid}")
    idle = lambda v: v["status"] == "closed" or (v["status"] == "waiting" and not v.get("pending_request"))
    deadline = time.monotonic() + 600
    result = dict(label=label, session_id=sid, job_id=jid)
    try:
        state = client._wait(fetch, idle, deadline)
        client.post(f"{base}/{sid}/steps", {"revision": state["revision"], "step_ids": ["A01", "A02", "A03"]})
        state = client._wait(fetch, idle, deadline)
        steps = {k: v.get("status") for k, v in state["steps"].items()}
        result.update(steps=steps, run_id=state.get("run_id"), outputs=state.get("outputs"))
        if state["status"] != "waiting" or any(steps[k] != "completed" for k in ("A01", "A02", "A03")):
            raise RuntimeError(f"会话未就绪：{state['status']} {steps} {state.get('error')}")
        time.sleep(2.0)
        result["capture_dir"] = str(capture(label, out_root))
    finally:
        state = fetch()
        if state["status"] == "waiting":
            client.post(f"{base}/{sid}/finish", {"revision": state["revision"]})
        job = client.wait_job(jid, time.monotonic() + 300)
        final = fetch()
        result.update(job_status=job.get("status"), safety_shutdown=final.get("safety_shutdown"),
                      finish_step=final["steps"].get("A04", {}).get("status"))
        log(json.dumps(result, ensure_ascii=False, default=str))
    return result


def main():
    out_root = data_root() / "scope_px_snapshot" / (datetime.now().strftime("%m%d_%H%M%S") + "_tail_comp")
    out_root.mkdir(parents=True)
    log_path = out_root / "capture_log.jsonl"
    def log(line):
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    if True:
        client = LabClient(UrllibHttp("http://127.0.0.1:8000"), log=lambda m: print(m, flush=True))
        client.ensure_idle()
        fields = client.show(EXP)["fields"]
        for wname, wov in WAVES.items():
            for bname, xb in BIASES.items():
                r = one(client, fields, f"{wname}_{bname}", {**BASE_OVR, **wov, "X_BIAS_MV": xb}, out_root, log)
                if r.get("job_status") != "completed" or r.get("finish_step") != "completed" or not r.get("capture_dir"):
                    log(f"STOP after {r['label']}: job {r.get('job_status')}")
                    return 1
                time.sleep(30)
    log(f"ALL DONE {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
