"""探索档：关温控窗口内冻结 DHO900 当前画面，恢复温控后再读出并保存。不改示波器量程/时基。"""
import json, sys, time
from datetime import datetime
from pathlib import Path
import numpy as np, yaml

ROOT = Path(__file__).resolve().parents[4]  # 仓库根；真实仪器只在运行目录放行
from lab_workflows.common import data_root
from lab_workflows.steps.temperature import set_temperature_switch
from oscilloscope import create_oscilloscope, create_scope_acquisition
from signal_generator import DG900Instrument

SETTLE_S = 0.2
dev = yaml.safe_load((ROOT / "params/devices.yaml").read_text(encoding="utf-8"))
mp = yaml.safe_load((ROOT / "params/mapping.yaml").read_text(encoding="utf-8"))
def find_dev(did):
    for k, v in (dev.get("devices") or dev).items():
        if k == did or (isinstance(v, dict) and v.get("id") == did):
            return v
    raise KeyError(did)
ts_map = mp.get("mapping", mp)["Temp_Switch"]
ts_res = find_dev(ts_map["device_id"])["resource"]; ts_ch = int(ts_map["endpoint"]["index"])
SCOPE = find_dev("rigol_dho900_192_168_124_21")
print("Temp_Switch", ts_res, "ch", ts_ch)

scope = create_oscilloscope(SCOPE); scope.connect()
info = {"idn": scope.idn().strip(), "trig_mode": scope.get_trigger_mode().strip(),
        "tdiv_s": scope.get_timebase_scale(), "delay_s": scope.get_timebase_delay(), "depth": scope.get_memory_depth(), "points": scope.get_actual_points(),
        "srate": scope.get_sampling_rate(), "trig_status": scope.trigger_status().strip()}
chans = [c for c in (1, 2, 3, 4) if scope.get_channel_state(c)]
info["channels"] = {c: {"scale": scope.get_channel_scale(c), "offset": scope.get_channel_offset(c),
                        "coupling": scope.get_channel_coupling(c).strip()} for c in chans}
print(json.dumps(info, ensure_ascii=False))
frame_s = 10 * info["tdiv_s"]
if frame_s > 2.0:
    scope.disconnect(); sys.exit(f"帧长 {frame_s:.3g} s 过长，未关温控，退出")

dg = DG900Instrument(ts_res, channel=ts_ch); dg.connect()
apply0 = dg.query_apply(ts_ch).strip(); on0 = dg.get_output(ts_ch); off0 = dg.get_offset(ts_ch)
print("Temp_Switch 原状态:", apply0, "output", on0, "offset", off0)
was_on = on0 and off0 > 2.5
stopped = False
try:
    set_temperature_switch(dg, False, channel=ts_ch)
    t_off = time.time()
    time.sleep(SETTLE_S + 2.5 * frame_s + 0.1)
    scope.trigger_stop(); stopped = True
    t_stop = time.time()
finally:
    if was_on:
        set_temperature_switch(dg, True, channel=ts_ch)
    t_on = time.time()
    print("Temp_Switch 已恢复:", dg.query_apply(ts_ch).strip(), "output", dg.get_output(ts_ch))
    dg.disconnect()
try:
    acq = create_scope_acquisition(scope, maximum_points=5_000_000)
    res = {c: acq.acquire_channel(c, info["tdiv_s"]) for c in chans}
finally:
    if stopped and info["trig_status"] != "Stop":
        scope.trigger_run()
    scope.disconnect()

out = data_root() / "scope_px_snapshot" / datetime.now().strftime("%m%d_%H%M%S")
out.mkdir(parents=True)
np.savez(out / "waveforms.npz", **{f"t{c}": r.time for c, r in res.items()}, **{f"v{c}": r.voltage for c, r in res.items()})
info.update(temp_off_window_s=t_on - t_off, stop_after_off_s=t_stop - t_off, temp_switch_before=apply0,
            preambles={c: r.preamble_dict for c, r in res.items()})
(out / "capture_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("saved", out, {c: len(r.voltage) for c, r in res.items()})
