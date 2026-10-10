"""两款信号发生器共用的参数类型和负载归一化；类型名称不代表型号能力。"""

import math
from typing import Literal

WaveShape = Literal[
    "SINusoid", "SQUare", "RAMP", "PULSe", "NOISe",
    "USER", "HARMonic", "CUSTom", "DC",
]

ModType = Literal[
    "AM", "FM", "PM", "FSKey", "BPSKey", "QPSKey",
    "3FSKey", "4FSKey", "OSKey", "ASKey", "PSKey",
    "PWM",
]

SweepSpacing = Literal["LINear", "LOGarithmic"]
BurstMode = Literal["TRIGgered", "GATed", "INFinity"]
PulseTransition = float | Literal["MINimum", "MAXimum"]
VoltageUnit = Literal["VPP", "VRMS", "DBM"]
OutputPolarity = Literal["NORMal", "INVerted"]
SyncPolarity = Literal["POSitive", "NEGative"]


def normalize_output_load(ohms: float | str) -> float | str:
    """高阻数值是查询标记，再次写入时必须使用 INF 关键字。"""
    if str(ohms).strip().upper() in {"INF", "INFINITY", "INFINITE"}:
        return "INF"
    numeric = float(ohms)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("负载必须是有限正数或 INF")
    return "INF" if numeric >= 9e37 else ohms
