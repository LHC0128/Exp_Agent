"""低端采样电阻的逐帧电流及功率安全检查。"""
from ...current_feedback import sense_voltage_to_current, validate_current_power


def validate_capture_current(frame, params):
    """在真实帧保存前检查实测电流，扫描和 SDS 协议复用电感频响职责模块。"""
    current = sense_voltage_to_current(frame["measured_voltage_v"], params.sense_resistor_ohm)
    validate_current_power(current, params.sense_resistor_ohm, params.sense_resistor_power_rating_w,
        derating_fraction=params.sense_resistor_power_derating, maximum_current_a=params.maximum_current_a)
