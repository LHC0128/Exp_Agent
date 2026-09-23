"""本实验成对采集步骤；接收已连接设备，导入不访问硬件。"""
import json
import time

import numpy as np

from ...experiment_runtime import check_cancelled


def wait_for_settle(seconds):
    """稳定等待期间响应取消，异常由工作流统一安全收尾。"""
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def acquire_pair(index, raw_dir, generator, channel, acquire, settle_s):
    """相邻点交替两态顺序；每段立即落盘，第二段失败时保留第一段。"""
    states = (False, True) if index % 2 == 0 else (True, False)
    for order, enabled in enumerate(states):
        check_cancelled()
        state = "on" if enabled else "off"
        generator.set_output(enabled, channel=channel)
        wait_for_settle(settle_s)
        started = time.time()
        waveform = acquire()
        finished = time.time()
        name = f"waveform_C{index:04d}_{state}.npy"
        np.save(raw_dir / name, waveform)
        waveform = np.asarray(waveform)
        nonfinite = int((~np.isfinite(waveform)).sum())
        valid = bool(waveform.ndim == 1 and waveform.size >= 2 and nonfinite == 0
                     and np.std(waveform) > 0)
        # 主机时间标记 DAQ 调用边界，不冒充仪器触发时间。
        with (raw_dir / "pair_timing.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(point=index, order=order, state=state, file=name,
                                         started_unix_s=started, finished_unix_s=finished,
                                         settle_s=settle_s, samples=int(waveform.size),
                                         nonfinite_samples=nonfinite, waveform_valid=valid)) + "\n")
        if not valid:
            print(f"警告: {name} 波形无效（非有限样本 {nonfinite}）；保留原文件，离线排除该数据对。", flush=True)
