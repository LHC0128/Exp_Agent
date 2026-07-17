"""GUI 设备接口的请求与响应模型。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class StrictModel(BaseModel):
    """拒绝未知字段，避免仪器设置键拼写错误被静默忽略。"""

    model_config = ConfigDict(extra="forbid")


class DeviceChannel(StrictModel):
    number: int
    label: str
    mapping_key: str
    read_only: bool


class DeviceBase(StrictModel):
    id: str
    label: str
    resource: str
    short_resource: str
    options: dict[str, JsonValue] = Field(default_factory=dict)


class DeviceSummary(DeviceBase):
    type: Literal["DG4000", "DG900", "SDS"]
    channels: list[DeviceChannel]


class ReadbackError(StrictModel):
    field: str
    message: str


class ModulationSnapshot(StrictModel):
    enabled: bool
    type: Literal["AM", "FM", "PM", "FSKey", "PWM"] | None
    source: str | None
    internal_frequency: float | None
    internal_function: str | None
    am_depth: float | None
    fm_deviation: float | None
    pm_deviation: float | None
    fsk_frequency: float | None
    fsk_rate: float | None
    fsk_polarity: str | None
    pwm_duty_deviation: float | None


class BurstSnapshot(StrictModel):
    enabled: bool
    mode: str | None
    ncycles: int | float | str | None
    phase: float | None
    period: float | None
    delay: float | None
    trigger_source: str | None
    trigger_slope: str | None


class GeneratorChannelSnapshot(StrictModel):
    number: int
    mapping_key: str
    label: str
    read_only: bool
    output: bool
    shape: str
    frequency: float | None
    amplitude: float | None
    offset: float | None
    phase: float | None
    voltage_unit: str | None
    load: int | float | str | None
    square_duty: float | None
    ramp_symmetry: float | None
    pulse_width: float | None
    pulse_delay: float | None
    mod: ModulationSnapshot
    burst: BurstSnapshot
    readback_errors: list[ReadbackError]


class GeneratorDeviceSnapshot(DeviceBase):
    type: Literal["DG4000", "DG900"]
    idn: str
    reference_clock: str
    channels: list[GeneratorChannelSnapshot]


class ScopeChannelSnapshot(StrictModel):
    number: int
    label: str
    enabled: bool
    scale: float
    offset: float
    coupling: str
    impedance: str
    probe: float


class ScopeTriggerSnapshot(StrictModel):
    mode: str
    type: str
    source: str
    slope: str
    level: float


class ScopeDeviceSnapshot(DeviceBase):
    type: Literal["SDS"]
    idn: str
    sampling_rate: float
    memory_depth: str
    acquire_type: str
    timebase_scale: float
    timebase_delay: float
    channels: list[ScopeChannelSnapshot]
    trigger: ScopeTriggerSnapshot


DeviceSnapshot = Annotated[
    GeneratorDeviceSnapshot | ScopeDeviceSnapshot,
    Field(discriminator="type"),
]


class ModulationSettings(StrictModel):
    enabled: bool | None = None
    type: Literal["AM", "FM", "PM", "FSKey", "PWM"] | None = None
    source: str | None = None
    internal_frequency: float | None = None
    internal_function: str | None = None
    am_depth: float | None = None
    fm_deviation: float | None = None
    pm_deviation: float | None = None
    fsk_frequency: float | None = None
    fsk_rate: float | None = None
    fsk_polarity: str | None = None
    pwm_duty_deviation: float | None = None


class BurstSettings(StrictModel):
    enabled: bool | None = None
    mode: str | None = None
    ncycles: int | float | str | None = None
    phase: float | None = None
    period: float | None = None
    delay: float | None = None
    trigger_source: str | None = None
    trigger_slope: str | None = None


class GeneratorChannelSettings(StrictModel):
    output: bool | None = None
    shape: str | None = None
    frequency: float | None = None
    amplitude: float | None = None
    offset: float | None = None
    phase: float | None = None
    voltage_unit: str | None = None
    load: int | float | str | None = None
    square_duty: float | None = None
    ramp_symmetry: float | None = None
    pulse_width: float | None = None
    pulse_delay: float | None = None
    target_mode: Literal["mod", "burst"] | None = None
    mod: ModulationSettings | None = None
    burst: BurstSettings | None = None


class GeneratorChannelSettingsBody(StrictModel):
    settings: GeneratorChannelSettings


class ScopeChannelSettings(StrictModel):
    number: int
    enabled: bool | None = None
    scale: float | None = None
    offset: float | None = None
    coupling: str | None = None
    impedance: str | None = None
    probe: float | None = None


class ScopeTriggerSettings(StrictModel):
    mode: str | None = None
    type: str | None = None
    source: str | None = None
    slope: str | None = None
    level: float | None = None


class ScopeSettings(StrictModel):
    sampling_rate: float | None = None
    memory_depth: str | None = None
    acquire_type: str | None = None
    acquire_type_param: int | None = None
    timebase_scale: float | None = None
    timebase_delay: float | None = None
    channels: list[ScopeChannelSettings] | None = None
    trigger: ScopeTriggerSettings | None = None


class ScopeSettingsBody(StrictModel):
    settings: ScopeSettings
