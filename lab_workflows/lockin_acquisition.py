"""正式实验的解调采集阶段声明；与运行共用底层选择规则。"""

from lockin_amplifier import AcquisitionRequest, select_acquisition, lockin_capabilities

XY = ("sample.x", "sample.y")
RXY = ("sample.r", "sample.x", "sample.y")
Y = ("sample.y",)
R = ("sample.r",)

# 字段均来自实际参数契约；新增采集阶段需在这里声明，禁止猜测 RATE 常量。
_DIRECT = (("RF 相位/响应/带宽", "DEMOD3_RATE", "SCAN_POINT_DURATION_S", "DEMOD3_IDX", RXY),
           ("RF 噪声", "NOISE_RATE", "NOISE_DURATION", "DEMOD3_IDX", ("sample.x",)))
_XY = (("XY 噪声", "HF2_DAQ_RATE", "HF2_DAQ_DURATION", "HF2_DEMOD_IDX", Y),)
_MX = (("响应", "RESPONSE_RATE_SA_S", "RESPONSE_DURATION_S", "DEMOD_IDX", R),
       ("扫频", "RESPONSE_RATE_SA_S", "FREQUENCY_DURATION_S", "DEMOD_IDX", R),
       ("噪声", "NOISE_RATE_SA_S", "NOISE_DURATION_S", "DEMOD_IDX", R))
_FREQUENCY = (("扫频", "RESPONSE_RATE_SA_S", "FREQUENCY_DURATION_S", "DEMOD_IDX", R),)
_RESPONSE = (("响应", "RESPONSE_RATE_SA_S", "RESPONSE_DURATION_S", "DEMOD_IDX", R),)

ACQUISITION_STAGES = {
    "rf-sensitivity-aw": _DIRECT,
    "rf-sensitivity-aw-r": _DIRECT,
    "rf-sensitivity-aw-known-noise": _DIRECT,
    "rf-control-scale-sensitivity": _DIRECT,
    "noise-spectrum-xy": _XY,
    "noise-spectrum-xy-v2": _XY,
    "noise-spectrum-xy-uncontrolled-probe-am": _XY,
    "noise-spectrum-xy-known-noise": _XY,
    "noise-spectrum-xy-demod3-r": (("Demod3 R 扫描", "DEMOD3_RATE_SA_S", "DEMOD3_ACQUISITION_DURATION_S", "DEMOD3_IDX", R),),
    "static-sensitivity": (("色散", "hf2_demod_rate", "dispersion_duration_s", "hf2_demod_idx", XY),
                           ("静磁噪声", "hf2_noise_rate", "noise_duration", "hf2_demod_idx", Y)),
    "detection-chain-frequency-response": (("探测链路扫频", "HF2_DAQ_RATE_SA_S", "HF2_DAQ_DURATION_S", "HF2_DEMOD_IDX", Y),),
    "mx-y-rf-sensitivity": _MX,
    "mx-y-rf-frequency-response": _FREQUENCY,
    "mx-y-optimal-control-rf-frequency-response": _FREQUENCY,
    "mx-y-rf-sensitivity-drift": _MX,
    "mx-y-rf-power-optimization": _MX,
    "mx-y-rf-probe-detuning-optimization": _MX,
    "mx-z-optimal-control-rf-sensitivity": ((*_MX[0][:4], RXY), _MX[2]),
    "mx-z-optimal-control-xy-rf-sensitivity": ((*_MX[0][:4], RXY), _MX[2]),
    "mx-z-optimal-control-xy-rf-phase-response": _RESPONSE,
    "mx-keithley-6221-optimal-control-rf-sensitivity": ((*_MX[0][:4], RXY), _MX[2]),
    "mx-keithley-6221-optimal-control-balanced-sensitivity": ((*_MX[0][:4], RXY), _MX[2]),
    "mx-z-optimal-control-xyz-balance": _RESPONSE,
    "mx-z-optimal-control-dg4000-bias-xyz-balance": _RESPONSE,
    "mx-keithley-6221-optimal-control-xyz-balance": _RESPONSE,
    "mx-z-optimal-control-xy-leakage-response": _RESPONSE,
    "mx-z-optimal-control-xy-noise-spectrum": _MX,
    "mx-z-field-calibration": _FREQUENCY,
    "mx-z-current-coupling-calibration": _FREQUENCY,
    "mx-main-field-calibration": _FREQUENCY,
    "mx-keithley-6221-main-field-calibration": _FREQUENCY,
    "bell-bloom-z-field-calibration": _FREQUENCY,
    "mx-z-noise-spectrum": (("Z 场噪声", "REQUESTED_RATE_SA_S", "ACQUISITION_DURATION_S", "DEMOD_IDX", R),),
    "mx-main-field-noise-spectrum": (("主场噪声", "REQUESTED_RATE_SA_S", "ACQUISITION_DURATION_S", "DEMOD_IDX", R),),
    "photon-shot-noise": (("光子噪声", "HF2_DEMOD_RATE", "ACQ_DURATION", "HF2_DEMOD_IDX", XY),),
    "rf-sensitivity-aw-frequency": (("RF 扫频", "DEMOD0_RATE", "POINT_DURATION_s", "DEMOD0_IDX", Y),),
    "rf-sensitivity-direct-aw-frequency": (("RF 扫频", "DEMOD0_RATE", "POINT_DURATION_s", "DEMOD0_IDX", Y),),
    "rf-sensitivity-constxy-frequency": (("RF 扫频", "HF2_DEMOD0_RATE", "POINT_DURATION_s", "HF2_DEMOD0_IDX", Y),),
    "xy-direct-aw-dc-calibration": (("XY 电压标定", "DEMOD0_RATE_Sa_s", "POINT_DURATION_s", "DEMOD0_IDX", Y),),
    "xy-aw-voltage-calibration": (("XY 电压标定", "HF2_DEMOD0_RATE", "POINT_DURATION_s", "HF2_DEMOD0_IDX", Y),),
    "xy-dc-voltage-calibration": (("XY 电压标定", "HF2_DEMOD0_RATE", "POINT_DURATION_s", "HF2_DEMOD0_IDX", Y),),
    "z-field-bandwidth": (("Z 带宽", "HF2_DEMOD0_RATE", "POINT_DURATION", "HF2_DEMOD0_IDX", Y),),
    "static-sensitivity-optimize": (("触发色散", "HF2_DEMOD_RATE", "DAQ_DURATION", "HF2_DEMOD_IDX", RXY),
                                    ("噪声", "HF2_NOISE_RATE", "NOISE_DURATION", "HF2_DEMOD_IDX", Y)),
    "static-sensitivity-optimize-local": (("DC 逐点色散", "HF2_DEMOD_RATE", "DISPERSION_DURATION_S", "HF2_DEMOD_IDX", XY),
                                          ("噪声", "HF2_NOISE_RATE", "NOISE_DURATION", "HF2_DEMOD_IDX", Y)),
}

# 这些既有入口将时长/速率固定在工作流中，没有对应 GUI 参数。
_FIXED_STAGE_VALUES = {
    key: {"POINT_DURATION_s": 1.0} for key in (
        "rf-sensitivity-aw-frequency", "rf-sensitivity-direct-aw-frequency",
        "rf-sensitivity-constxy-frequency", "xy-aw-voltage-calibration", "xy-dc-voltage-calibration")
}
_FIXED_STAGE_VALUES["xy-direct-aw-dc-calibration"] = {"POINT_DURATION_s": 1.0, "DEMOD0_RATE_Sa_s": 100000.0}


def experiment_acquisition_requests(experiment_id: str, values: dict) -> tuple[AcquisitionRequest, ...]:
    """从显式字段生成请求；部分参数按注册入口的当前默认值补齐。"""
    requests = []
    values = {**_FIXED_STAGE_VALUES.get(experiment_id, {}), **values}
    stages = ACQUISITION_STAGES.get(experiment_id, ())
    required = {key for _, rate, duration, demod, _ in stages for key in (rate, duration, demod)}
    if stages and not required.issubset(values):
        from .experiments import get_experiment
        defaults = {field["name"]: field["default"] for field in get_experiment(experiment_id).schema()["fields"]}
        values = {**defaults, **values}
    for stage, rate_key, duration_key, demod_key, signals in stages:
        if rate_key not in values or duration_key not in values:
            continue
        if experiment_id == "rf-control-scale-sensitivity" and stage == "RF 噪声" and not values.get("INCLUDE_NOISE", True):
            continue
        if stage == "扫频" and values.get("LINEWIDTH_MODE", "frequency_sweep") != "frequency_sweep":
            continue
        if experiment_id in {"rf-sensitivity-direct-aw-frequency", "xy-direct-aw-dc-calibration", "xy-aw-voltage-calibration", "xy-dc-voltage-calibration", "z-field-bandwidth"} and values.get("ACQUISITION_MODE") == "demod_rxy":
            continue
        triggered = stage == "触发色散"
        requests.append(AcquisitionRequest(stage, values[rate_key], values[duration_key],
            values.get(demod_key, 0), signals, trigger_type=int(triggered),
            extra_paths=("aux_trigger",) if triggered else ()))
    return tuple(requests)


def acquisition_preflight(config: dict, requests: tuple[AcquisitionRequest, ...]) -> list[dict]:
    """返回 GUI 可展示的预计模块/速率；真实选件由连接后再校验。"""
    model = config.get("model", "HF2")
    caps = lockin_capabilities(model, config.get("capabilities", {}).get("options", []))
    plans = []
    for request in requests:
        try:
            plan = select_acquisition(model, caps, request.config(), request.requested_rate,
                                      request.demod_index, config.get("acquisition_mode", "auto"))
        except ValueError as exc:
            raise ValueError(f"{request.stage}: {exc}") from exc
        plans.append(dict(stage=request.stage, acquisition_module=plan.module,
                          requested_rate_Sa_s=plan.requested_rate, estimated_rate_Sa_s=plan.estimated_rate,
                          duration_s=request.duration, demod_index=request.demod_index, signals=list(request.signals)))
    return plans
