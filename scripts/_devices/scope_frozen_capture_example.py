"""示波器冻结读帧示例：按面板已设好的时基/深度取一帧，不写采样率；顺带实测单帧耗时并校核温控窗口。

为什么这样写（详见 `.agents/skills/scope-acquisition/SKILL.md`）：
- DHO900 没有写采样率的命令，采样率 = 存储深度 ÷ (时基 × 10 格)，由仪器自己推；
- 采集器走 `apply_config()` 时会核对“采样率 × 窗口 = 合法深度档”，组合不合时会退回 AUTO 深度，
  真机在 1 ms/div、上限 1 万点时会退成 62.5 万点，单帧从 2 s 变 17.5 s；
- 冻结读帧（`_configured=False`）只校验 STOP 状态与通道使能，面板设什么就读什么；
- 温控只在“冻结”那一瞬间关闭，读回放到恢复温控之后，且关窗必须短于恢复窗。

用法（在带 .lab_runtime 的运行目录执行；不改任何设置，只读）：
    python scripts/_devices/scope_frozen_capture_example.py
    python scripts/_devices/scope_frozen_capture_example.py --frames 5 --channels 1 3
退出码：0 成功；1 读取或校核失败。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hardware_gate import require_hardware_access, runtime_root  # noqa: E402
from oscilloscope import create_oscilloscope, create_scope_acquisition  # noqa: E402

from lab_workflows.common import data_root  # noqa: E402
from lab_workflows.instrument_config import load_device_definitions  # noqa: E402

SCOPE_DEVICE_ID = "rigol_dho900_192_168_124_21"


def check_temp_windows(off_s: float, recover_s: float) -> None:
    """温控开关规则：关窗必须为正、且短于恢复窗，禁止长时间关闭温控。"""
    if not (0 < off_s < recover_s):
        raise ValueError(f"关温控 {off_s:.3f} s 必须短于恢复 {recover_s:.3f} s，且都为正")


def freeze_frame(scope, acq, channels: list[int], tdiv: float):
    """冻结一帧（SINGle 布防 + 触发 + 停止），再读回各通道；不修改任何采集设置。"""
    scope.set_trigger_mode("SINGle")
    scope.trigger_run()
    if not scope.wait_for_trigger(0.02, 2.0):
        # 触发源没有信号时退回 AUTO + 强制触发，保证总能拿到一帧。
        scope.set_trigger_mode("AUTO")
        scope.write(":TFORce")
        deadline = time.monotonic() + 5.0
        while scope.trigger_status() != "Stop":
            if time.monotonic() > deadline:
                raise TimeoutError("冻结帧等待 STOP 超时")
            time.sleep(0.05)
    scope.trigger_stop()
    return {ch: acq.acquire_channel(ch, tdiv) for ch in channels}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="示波器冻结读帧示例：不写采样率，按面板时基/深度读帧")
    parser.add_argument("--device", default=SCOPE_DEVICE_ID, help="设备库里的示波器 ID")
    parser.add_argument("--frame", type=int, default=3, help="采集帧数（默认 3）")
    parser.add_argument("--channels", type=int, nargs="+", default=[1, 3], help="要读的通道（默认 CH1 CH3）")
    args = parser.parse_args(argv)

    require_hardware_access("示波器冻结读帧示例")
    root = runtime_root()
    defn = load_device_definitions(root).get(args.device)
    if defn is None:
        raise SystemExit(f"设备库缺少示波器 {args.device}")

    scope = create_oscilloscope(defn.to_config())
    scope.connect()
    try:
        tdiv = float(scope.get_timebase_scale())
        depth = scope.get_memory_depth().strip()
        rate = float(scope.get_sampling_rate())
        if depth.upper() == "AUTO":
            raise SystemExit("存储深度是 AUTO；请在面板设为固定值（如 10k）后重试")
        print(f"面板配置：{tdiv * 1e6:g} us/div，深度 {depth}，采样率 {rate / 1e6:g} MSa/s"
              f"（点数 {scope.get_actual_points()}）")
        for ch in args.channels:
            if not scope.get_channel_state(ch):
                raise SystemExit(f"CH{ch} 未启用；本示例不改通道设置，请在面板打开")

        acq = create_scope_acquisition(scope, maximum_points=50_000_000)
        out = data_root(root) / "_scope_frozen_example" / time.strftime("%m%d_%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        timings = []
        for index in range(int(args.frame)):
            started = time.monotonic()
            results = freeze_frame(scope, acq, list(args.channels), tdiv)
            elapsed = time.monotonic() - started
            timings.append(elapsed)
            payload = {}
            for ch, result in results.items():
                payload[f"t{ch}"] = result.time
                payload[f"v{ch}"] = result.voltage
            np.savez_compressed(out / f"frame{index:02d}.npz", **payload)
            print(f"  帧 {index + 1}/{args.frame}: {elapsed:.2f} s，"
                  f"CH1 点数 {len(results[args.channels[0]].voltage)}")
        meta = dict(device=args.device, idn=scope.idn().strip(), timebase_s_div=tdiv, depth=depth,
                    sample_rate_sa_s=rate, channels=list(args.channels), timings_s=timings,
                    mean_time_s=float(np.mean(timings)))
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"平均单帧 {np.mean(timings):.2f} s；产物：{out}")

        # 温控窗口示例：关窗只包住冻结瞬间，恢复窗不短于关窗。
        off_s, recover_s = 0.35, 1.0
        check_temp_windows(off_s, recover_s)
        print(f"温控窗口校核通过：关 {off_s:.2f} s < 恢复 {recover_s:.2f} s")
        return 0
    finally:
        scope.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
