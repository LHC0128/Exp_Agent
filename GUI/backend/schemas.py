"""GUI 设备接口的请求与响应模型。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class StrictModel(BaseModel):
    """拒绝未知字段，避免仪器设置键拼写错误被静默忽略。"""

    model_config = ConfigDict(extra="forbid")


class DeviceChannel(StrictModel):
    number: int
    label: str
    mapping_key: str
    mapping_keys: list[str] = Field(default_factory=list)
    read_only: bool


class DeviceBase(StrictModel):
    id: str
    label: str
    resource: str
    short_resource: str
    options: dict[str, JsonValue] = Field(default_factory=dict)


class DeviceSummary(DeviceBase):
    type: Literal["DG4000", "DG900", "GS200", "6221", "DLC_PRO", "SDS"]
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
    mapping_keys: list[str] = Field(default_factory=list)
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


class CurrentSourceDeviceSnapshot(DeviceBase):
    type: Literal["GS200"]
    idn: str
    mapping_key: str
    source_function: str
    output: bool
    current_ma: float | None
    current_range_ma: float | None
    voltage_limit_v: float
    current_limit_ma: float
    min_current_ma: float
    max_current_ma: float


class Keithley6221WaveformSnapshot(StrictModel):
    shape: Literal["SIN", "SQU", "RAMP", "ARB"]
    frequency_hz: float
    amplitude_peak_ma: float
    offset_ma: float
    duty_cycle_percent: float
    ranging: Literal["BEST", "FIXED"]
    duration_mode: Literal["TIME", "CYCLES", "INFINITE", "MIXED"]
    duration_value: float | None
    duration_time_s: float | Literal["INF"]
    duration_cycles: float | Literal["INF"]
    arbitrary_point_count: int


class Keithley6221DeviceSnapshot(DeviceBase):
    type: Literal["6221"]
    idn: str
    mapping_key: str
    output: bool
    current_ma: float
    current_range_ma: float
    autorange: bool
    compliance_v: float
    analog_filter: bool
    output_response: Literal["FAST", "SLOW"]
    min_current_ma: float
    max_current_ma: float
    waveform: Keithley6221WaveformSnapshot


class LaserDeviceSnapshot(DeviceBase):
    type: Literal["DLC_PRO"]
    controller_serial: str
    system_type: str
    system_label: str
    firmware_version: str
    system_health_code: int
    system_health: str
    interlock_open: bool
    front_key_locked: bool
    emission: bool
    laser_type: str
    laser_product_name: str
    laser_enabled: bool
    laser_health_code: int
    laser_health: str
    laser_emission: bool
    laser_head_model: str
    laser_head_serial: str
    current_set_ma: float
    current_actual_ma: float
    current_clip_ma: float
    current_clip_limit_ma: float
    min_current_ma: float
    max_current_ma: float
    temperature_set_c: float
    temperature_actual_c: float
    min_temperature_c: float
    max_temperature_c: float
    pzt_voltage_v: float
    pzt_actual_v: float
    min_pzt_voltage_v: float
    max_pzt_voltage_v: float
    scan_amplitude_vpp: float
    min_scan_amplitude_vpp: float
    max_scan_amplitude_vpp: float
    scan_frequency_hz: float
    scan_enabled: bool
    scan_unit: str
    scan_output_channel: int
    remote_emission_control_enabled: bool
    safety_keys: dict[str, str]
    state_known: bool


class TecDeviceSnapshot(DeviceBase):
    type: Literal["TEC103"]
    channel: int
    target_temperature_c: float
    actual_temperature_c: float | None
    enabled: bool
    output_mode: int
    resistance_kohm: float
    min_temperature_c: float
    max_temperature_c: float


class Hf2DeviceSnapshot(DeviceBase):
    type: Literal["HF2"]
    demod_idx: int
    x: float
    y: float
    r: float
    phase: float
    frequency: float
    enabled: bool
    sample_rate: float
    time_constant: float
    order: int
    harmonic: int
    phase_shift: float


DeviceSnapshot = Annotated[
    GeneratorDeviceSnapshot
    | ScopeDeviceSnapshot
    | CurrentSourceDeviceSnapshot
    | Keithley6221DeviceSnapshot
    | LaserDeviceSnapshot
    | TecDeviceSnapshot
    | Hf2DeviceSnapshot,
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
    mapping_key: str | None = None
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


class Keithley6221WaveformSettings(StrictModel):
    action: Literal["configure", "configure_and_start", "abort"]
    shape: Literal["SIN", "SQU", "RAMP", "ARB"] | None = None
    frequency_hz: float | None = None
    amplitude_peak_ma: float | None = None
    offset_ma: float | None = None
    duty_cycle_percent: float | None = None
    ranging: Literal["BEST", "FIXED"] | None = None
    duration_mode: Literal["TIME", "CYCLES", "INFINITE"] | None = None
    duration_value: float | None = None
    arbitrary_points: list[float] | None = None
    confirm_start: bool = False

    @model_validator(mode="after")
    def validate_action_fields(self):
        config_fields = (
            self.shape, self.frequency_hz, self.amplitude_peak_ma,
            self.offset_ma, self.duty_cycle_percent, self.ranging,
            self.duration_mode, self.duration_value, self.arbitrary_points,
        )
        if self.action == "abort":
            if any(value is not None for value in config_fields):
                raise ValueError("中止波形时不能携带波形配置字段")
            return self
        required = {
            "shape": self.shape,
            "frequency_hz": self.frequency_hz,
            "amplitude_peak_ma": self.amplitude_peak_ma,
            "offset_ma": self.offset_ma,
            "ranging": self.ranging,
            "duration_mode": self.duration_mode,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError("配置波形缺少字段: " + ", ".join(missing))
        if self.duration_mode == "INFINITE" and self.duration_value is not None:
            raise ValueError("无限时长不能提供 duration_value")
        if self.duration_mode in {"TIME", "CYCLES"} and self.duration_value is None:
            raise ValueError("有限波形时长必须提供 duration_value")
        if self.shape == "ARB" and self.arbitrary_points is None:
            raise ValueError("任意波必须提供 arbitrary_points")
        if self.shape != "ARB" and self.arbitrary_points is not None:
            raise ValueError("只有 ARB 波形可以提供 arbitrary_points")
        if self.action == "configure_and_start" and not self.confirm_start:
            raise ValueError("启动波形必须提供 confirm_start=true")
        return self


class CurrentSourceSettings(StrictModel):
    current_ma: float | None = None
    output: bool | None = None
    confirm_output_enable: bool = False
    current_range_ma: float | None = None
    autorange: bool | None = None
    compliance_v: float | None = None
    analog_filter: bool | None = None
    output_response: Literal["FAST", "SLOW"] | None = None
    waveform: Keithley6221WaveformSettings | None = None

    @model_validator(mode="after")
    def require_setting(self):
        dc_values = (
            self.current_ma, self.output, self.current_range_ma, self.autorange,
            self.compliance_v, self.analog_filter, self.output_response,
        )
        has_dc = any(value is not None for value in dc_values)
        if has_dc and self.waveform is not None:
            raise ValueError("直流字段与 waveform 不能混在同一请求")
        if not has_dc and self.waveform is None:
            raise ValueError("电流源设置至少需要一个直流字段或 waveform")
        return self


class CurrentSourceSettingsBody(StrictModel):
    settings: CurrentSourceSettings


class LaserSettings(StrictModel):
    current_set_ma: float | None = None
    temperature_set_c: float | None = None
    pzt_voltage_v: float | None = None
    scan_amplitude_vpp: float | None = None
    scan_enabled: bool | None = None

    @model_validator(mode="after")
    def require_setting(self):
        if all(
            value is None
            for value in (
                self.current_set_ma,
                self.temperature_set_c,
                self.pzt_voltage_v,
                self.scan_amplitude_vpp,
                self.scan_enabled,
            )
        ):
            raise ValueError("激光器设置至少需要一个控制量")
        return self


class LaserSettingsBody(StrictModel):
    settings: LaserSettings


class LaserEmissionSettings(StrictModel):
    enabled: bool
    safety_acknowledged: bool = False
    confirm_emission_enable: bool = False
    confirmation_text: str | None = None


class LaserEmissionSettingsBody(StrictModel):
    settings: LaserEmissionSettings


class ControlEndpoint(StrictModel):
    kind: Literal["channel", "demod", "laser_channel"]
    index: int


class ControlTarget(StrictModel):
    mapping_key: str
    label: str
    description: str
    kind: Literal["generator", "current_source", "laser", "scope", "tec", "hf2"]
    device_id: str
    device_label: str
    model: str
    short_resource: str
    endpoint: ControlEndpoint | None
    shared_mapping_keys: list[str]
    safety: dict[str, JsonValue]
    read_only: bool


class ControlTargetCatalog(StrictModel):
    device_library_revision: str
    physical_mapping_revision: str
    targets: list[ControlTarget]


class ControlRevisionBody(StrictModel):
    device_library_revision: str
    physical_mapping_revision: str


class ControlGeneratorBody(ControlRevisionBody):
    settings: GeneratorChannelSettings

    @model_validator(mode="after")
    def reject_mapping_override(self):
        if self.settings.mapping_key is not None:
            raise ValueError("新控制接口不接受 mapping_key 覆盖")
        return self


class ZArbitrarySettingsBody(StrictModel):
    control_burst_phase_deg: float = Field(default=0.0, allow_inf_nan=False)
    output_amplitude_vpp: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    link_trigger: bool = False
    trigger_frequency_hz: float = Field(default=100.0, gt=0, allow_inf_nan=False)
    trigger_amplitude_vpp: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    trigger_offset_v: float = Field(default=2.5, allow_inf_nan=False)
    trigger_duty_percent: float = Field(default=50.0, ge=20, le=80, allow_inf_nan=False)


class ZArbitraryActionBody(ControlRevisionBody):
    action: Literal["configure", "configure_and_start", "stop"]
    run_name: str | None = None
    waveform_sha256: str | None = None
    settings: ZArbitrarySettingsBody = Field(default_factory=ZArbitrarySettingsBody)


class ZArbitrarySourceSummary(StrictModel):
    run_name: str
    waveform_sha256: str
    points: int
    repeat_frequency_hz: float
    period_s: float
    amplitude_vpp: float
    offset_v: float
    minimum_v: float
    maximum_v: float


class ZArbitraryPreview(ZArbitrarySourceSummary):
    time_s: list[float]
    voltage_v: list[float]


class ZArbitrarySourceItem(StrictModel):
    run_name: str
    summary: ZArbitrarySourceSummary | None
    error: str | None


class ZArbitraryApplied(StrictModel):
    revisions: list[str]
    link_trigger: bool
    source: ZArbitrarySourceSummary | None
    settings: ZArbitrarySettingsBody | None
    state: Literal["enabled", "off", "unknown"]
    updated_at: str
    snapshots: list[ControlTargetResponse]


class ZArbitraryStatus(ControlTargetCatalog):
    source_known: bool
    last_applied: ZArbitraryApplied | None


class ControlCurrentSourceBody(ControlRevisionBody):
    settings: CurrentSourceSettings


class ControlLaserBody(ControlRevisionBody):
    settings: LaserSettings


class ControlEmissionBody(ControlRevisionBody):
    settings: LaserEmissionSettings


class ControlScopeBody(ControlRevisionBody):
    settings: ScopeSettings


class TecSettings(StrictModel):
    target_temperature_c: float


class ControlTecBody(ControlRevisionBody):
    settings: TecSettings


class ControlTargetResponse(StrictModel):
    target: ControlTarget
    snapshot: DeviceSnapshot
    read_at: str


class ControlTargetBulkItem(StrictModel):
    target: ControlTarget
    snapshot: DeviceSnapshot | None = None
    read_at: str | None = None
    error: str | None = None


class ControlTargetBulkResponse(StrictModel):
    results: list[ControlTargetBulkItem]
