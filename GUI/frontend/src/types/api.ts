export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type JobEvent = {
  index: number;
  timestamp: string;
  stage: string;
  message: string;
  percent: number | null;
  level: string;
  data: Record<string, unknown>;
};

export type Job = {
  id: string;
  kind: string;
  status: JobStatus;
  stage: string;
  percent: number | null;
  message: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  result?: unknown;
  error?: string | null;
  events: JobEvent[];
};

export type DeviceChannel = {
  number: number;
  label: string;
  mapping_key: string;
  read_only: boolean;
};

export type Device = {
  id: string;
  type: "DG4000" | "DG900" | "SDS";
  label: string;
  resource: string;
  short_resource: string;
  channels: DeviceChannel[];
  options: Record<string, unknown>;
};

export type ReadbackError = {
  field: string;
  message: string;
};

export type ModulationType = "AM" | "FM" | "PM" | "FSKey" | "PWM";

export type ModulationSnapshot = {
  enabled: boolean;
  type: ModulationType | null;
  source: string | null;
  internal_frequency: number | null;
  internal_function: string | null;
  am_depth: number | null;
  fm_deviation: number | null;
  pm_deviation: number | null;
  fsk_frequency: number | null;
  fsk_rate: number | null;
  fsk_polarity: string | null;
  pwm_duty_deviation: number | null;
};

export type BurstSnapshot = {
  enabled: boolean;
  mode: string | null;
  ncycles: number | string | null;
  phase: number | null;
  period: number | null;
  delay: number | null;
  trigger_source: string | null;
  trigger_slope: string | null;
};

export type GeneratorChannelSnapshot = {
  number: number;
  mapping_key: string;
  label: string;
  read_only: boolean;
  output: boolean;
  shape: string;
  frequency: number | null;
  amplitude: number | null;
  offset: number | null;
  phase: number | null;
  voltage_unit: string | null;
  load: number | string | null;
  square_duty: number | null;
  ramp_symmetry: number | null;
  pulse_width: number | null;
  pulse_delay: number | null;
  target_mode?: "mod" | "burst";
  mod: ModulationSnapshot;
  burst: BurstSnapshot;
  readback_errors: ReadbackError[];
};

export type GeneratorChannelSettings = Pick<
  GeneratorChannelSnapshot,
  | "output"
  | "shape"
  | "frequency"
  | "amplitude"
  | "offset"
  | "phase"
  | "voltage_unit"
  | "load"
  | "square_duty"
  | "ramp_symmetry"
  | "pulse_width"
  | "pulse_delay"
  | "target_mode"
  | "mod"
  | "burst"
>;

export type GeneratorSnapshot = Omit<Device, "type" | "channels"> & {
  type: "DG4000" | "DG900";
  idn: string;
  reference_clock: string;
  channels: GeneratorChannelSnapshot[];
};

export type ScopeChannelSnapshot = {
  number: number;
  label: string;
  enabled: boolean;
  scale: number;
  offset: number;
  coupling: string;
  impedance: string;
  probe: number;
};

export type ScopeTriggerSnapshot = {
  mode: string;
  type: string;
  source: string;
  slope: string;
  level: number;
};

export type ScopeSnapshot = Omit<Device, "type" | "channels"> & {
  type: "SDS";
  idn: string;
  sampling_rate: number;
  memory_depth: string;
  acquire_type: string;
  acquire_type_param?: number | null;
  timebase_scale: number;
  timebase_delay: number;
  channels: ScopeChannelSnapshot[];
  trigger: ScopeTriggerSnapshot;
};

export type ScopeSettings = Pick<
  ScopeSnapshot,
  | "sampling_rate"
  | "memory_depth"
  | "acquire_type"
  | "acquire_type_param"
  | "timebase_scale"
  | "timebase_delay"
  | "channels"
  | "trigger"
>;

export type DeviceSnapshot = GeneratorSnapshot | ScopeSnapshot;

export type ParameterValue = string | number | boolean | null | number[] | string[];
export type ParameterValues = Record<string, ParameterValue>;

export type SchemaOption = { value: string | number; label: string };
export type SchemaField = {
  name: string;
  label: string;
  type: "string" | "boolean" | "integer" | "number" | "array";
  unit: string;
  group: string;
  default: ParameterValue;
  minimum?: number;
  maximum?: number;
  description?: string;
  options?: SchemaOption[];
};
export type ParameterGroup = "basic" | "advanced";
export type ParameterLayout = Record<ParameterGroup, string[]>;
export type ExperimentSchema = {
  fields: SchemaField[];
  parameter_layout?: ParameterLayout;
  parameter_layout_saved?: boolean;
};
export type ExperimentDefinition = {
  id: string;
  title: string;
  category: string;
  category_label: string;
  family: string;
  variant: string;
  description: string;
  required_devices: string[];
  execution_mode: "typed_workflow" | "legacy_script";
  acquisition_program: string;
  analysis_program: string | null;
  wiring_notes: string[];
  safety_notes: string[];
  supports_cancel: boolean;
  can_analyze: boolean;
};
export type ExperimentTag = { id: string; label: string };
export type ExperimentCatalogConfig = {
  schema_version: number;
  tags: ExperimentTag[];
  assignments: Record<string, string>;
  experiment_descriptions: Record<string, string>;
  experiment_titles: Record<string, string>;
};

export type RunSummary = {
  id: string;
  experiment_id: string;
  experiment_title: string;
  path: string;
  artifacts: string[];
  can_analyze: boolean;
};
