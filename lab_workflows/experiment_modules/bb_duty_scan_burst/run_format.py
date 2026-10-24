"""采集、离线分析和只读历史接口共用的数据身份与旧配置迁移。"""
from pathlib import Path
import yaml
from .models import BBDutyScanBurstParams

EXPERIMENT_ID = "bb-duty-scan-burst"
DATA_TYPE = "bb_rpump_duty_scan_burst"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = ("main_magnetic_field", "Pump_laser_power", "Probe_laser_power",
    "Pump_modulation", "Time_sequence", "scope_waveform", "Temp_Switch", "temperature")
UNUSED_FIELDS = ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field",
                 "X_magnetic_field_AM", "Y_magnetic_field_AM")
LEGACY_SECTIONS = {
    "burst_pump": {"modulation_freq_Hz": "MODULATION_FREQUENCY_HZ", "duty_cycle_list": "DUTY_CYCLE_LIST",
        "burst_cycles": "BURST_CYCLES", "burst_period_s": "BURST_BURST_PERIOD_S",
        "pre_baseline_s": "PRE_BASELINE_TIME_S", "post_dark_s": "POST_DARK_TIME_S",
        "rf_gate_high_V": "PUMP_RF_GATE_HIGH_V", "rf_gate_low_V": "PUMP_RF_GATE_LOW_V"},
    "fixed_pump": {"fixed_pump_power_voltage": "FIXED_PUMP_POWER_VOLTAGE"},
    "main_field": {"z_field_setpoint_mA": "Z_FIELD_SETPOINT"},
    "probe": {"probe_power_V": "PROBE_POWER"},
    "aom_carrier": {"freq_Hz": "AOM_CARRIER_FREQ_HZ", "amplitude_Vpp": "AOM_CARRIER_AMPLITUDE_V", "offset_V": "AOM_CARRIER_OFFSET_V"},
    "scope": {"sample_rate_Hz": "SDS_SAMPLE_RATE", "timebase_delay_s": "SDS_TIMEBASE_DELAY_S",
        "pd_channel": "SDS_PD_CHANNEL", "trig_channel": "SDS_TRIG_CHANNEL", "trig_level_V": "SDS_TRIG_LEVEL",
        "trig_slope": "SDS_TRIG_SLOPE", "volt_scale_pd_V_per_div": "SDS_VOLT_SCALE_PD"},
}


class ConfigLoader(yaml.SafeLoader):
    """只兼容已核实的 duty_cycle_list 数值数组，不执行 YAML 中的 Python 对象。"""

    def construct_document(self, node):
        if isinstance(node, yaml.MappingNode):
            root = {key.value: value for key, value in node.value if isinstance(key, yaml.ScalarNode)}
            burst = root.get("burst_pump")
            if isinstance(burst, yaml.MappingNode):
                entries = {key.value: value for key, value in burst.value if isinstance(key, yaml.ScalarNode)}
                duty = entries.get("duty_cycle_list")
                tags = {"tag:yaml.org,2002:python/object/apply:numpy._core.multiarray._reconstruct",
                        "tag:yaml.org,2002:python/object/apply:numpy.core.multiarray._reconstruct"}
                if duty is not None and duty.tag in tags:
                    values = self._numpy_duty(duty)
                    replacement = yaml.SequenceNode("tag:yaml.org,2002:seq", [
                        yaml.ScalarNode("tag:yaml.org,2002:float", repr(value)) for value in values])
                    burst.value = [(key, replacement if key.value == "duty_cycle_list" else value) for key, value in burst.value]
        return super().construct_document(node)

    def _numpy_duty(self, node):
        import numpy as np
        allowed = {"str", "int", "float", "bool", "null", "binary", "map", "seq", "python/tuple",
            "python/name:numpy.ndarray", "python/object/apply:numpy.dtype",
            "python/object/apply:numpy._core.multiarray._reconstruct", "python/object/apply:numpy.core.multiarray._reconstruct"}
        seen = set()
        def check(value):
            if id(value) in seen:
                return
            seen.add(id(value))
            if value.tag.removeprefix("tag:yaml.org,2002:") not in allowed:
                raise ValueError("旧占空比数组含未知 YAML 标签")
            if isinstance(value, yaml.MappingNode):
                for key, child in value.value:
                    check(key); check(child)
            elif isinstance(value, yaml.SequenceNode):
                for child in value.value:
                    check(child)
        check(node)
        try:
            fields = {key.value: value for key, value in node.value}
            state = fields["state"].value
            count = int(state[1].value[0].value)
            dtype = {key.value: value for key, value in state[2].value}
            order = dtype["state"].value[1].value
            # ponytail: 只读旧版一维 f8 数组；出现其它历史编码时补对应实测格式，不启用通用对象反序列化。
            if len(state) != 5 or state[0].value != "1" or len(state[1].value) != 1 or not 0 < count <= 100000:
                raise ValueError("旧占空比数组形状无效")
            if dtype["args"].value[0].value != "f8" or order not in ("<", ">", "=") or state[3].value != "false":
                raise ValueError("旧占空比数组只支持一维 C 顺序 float64")
            if state[4].tag != "tag:yaml.org,2002:binary":
                raise ValueError("旧占空比数组缺少二进制数值")
            data = self.construct_yaml_binary(state[4])
            if len(data) != count * 8:
                raise ValueError("旧占空比数组字节数与形状不一致")
            values = np.frombuffer(data, dtype=order + "f8")
            if not np.all(np.isfinite(values)):
                raise ValueError("旧占空比数组包含非有限值")
            return values.tolist()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ValueError("旧占空比数组结构无效") from exc


def read_config(path):
    try:
        return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=ConfigLoader)
    except (OSError, ValueError, yaml.YAMLError):
        return None


def parameters(config):
    if isinstance(config.get("parameters"), dict):
        return BBDutyScanBurstParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    values = {external: config[section][old] for section, keys in LEGACY_SECTIONS.items() for old, external in keys.items()}
    values.update(RUN_TAG=config.get("run_tag", "duty_scan"), TEC_TEMPERATURE=config["temperature_C"])
    return BBDutyScanBurstParams.from_external(values)


def recorded_parameters(config):
    """历史界面仅展示真实记录过的键；未知旧参数不以模型默认值冒充。"""
    if isinstance(config.get("parameters"), dict):
        return parameters(config).to_external()
    values = {external: config[section][old] for section, keys in LEGACY_SECTIONS.items()
              for old, external in keys.items() if old in config.get(section, {})}
    for key, external in (("run_tag", "RUN_TAG"), ("temperature_C", "TEC_TEMPERATURE")):
        if key in config:
            values[external] = config[key]
    return values


def format_block_reason(config):
    if not isinstance(config, dict):
        return "运行配置必须为映射"
    identities = (("experiment_id", EXPERIMENT_ID), ("experiment_type", DATA_TYPE), ("data_type", DATA_TYPE))
    if any(config.get(key) not in (None, expected) for key, expected in identities):
        return "运行目录属于其他实验"
    if not any(config.get(key) == expected for key, expected in identities):
        return "无法识别 BB 占空比扫描身份"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 无 raw 标记仅接受本次迁移前已存在的分节配置，不猜测未知强类型格式。
    if version is None and "parameters" in config:
        return "未标记 raw 格式的强类型数据无法识别"
    try:
        parameters(config)
    except (KeyError, TypeError, ValueError) as exc:
        return f"历史参数快照无效：{exc}"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在写入，请先结束本轮"
    return ""


def initial_metadata(p):
    result = dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
        execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION, completion_status="running",
        data_files=[], temperature_C=p.tec_temperature, initial_temperature_c=None,
        temperature_gating={"scope": "each_shot", "off_voltage_v": 0, "on_voltage_v": 5})
    values = p.to_external()
    for section, keys in LEGACY_SECTIONS.items():
        result[section] = {old: values[external] for old, external in keys.items()}
    result["burst_pump"].update(burst_duration_s=p.burst_duration_s, burst_trigger_source="INTernal", burst_mode="TRIGgered")
    result["scope"]["total_acq_s"] = p.total_acq_s
    return result
