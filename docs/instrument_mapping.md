# 设备库与物理量映射

仪器配置分为两个独立文档：`params/devices.yaml` 保存物理设备，
`params/mapping.yaml` 保存实验物理量到设备端点的绑定。新模式实验只通过
`lab_workflows.instrument_config.resolve_mapping()` 或兼容入口 `load_mapping()`
读取解析后的当前映射，不在实验模块导入阶段缓存设备型号或资源地址。

## 设备库

每台设备使用稳定 `device_id`，其定义包含：

- `instrument`：设备类型，例如 `signal_generator`、`gs200`、`keithley_6221`、
  `lockin_amplifier`、`sds_acquisition`、`tec_controller`。
- `model`：具体驱动型号，例如 `DG4000` 或 `DG900`。
- `label`、`resource`：GUI 标签和 VISA/网络/串口资源。
- `connection`：非 VISA 连接参数，例如 HF2 device ID、DLC pro 端口。
- `capabilities`：通道范围、任意波点数和无限 Burst 等能力。
- `reference_clock`：`INT`、`EXT` 或不适用。

`params/clock_sources.yaml` 仅用于旧入口兼容回退。新模式的参考时钟目标来自
设备库，并在连接后由 `synchronize_connected_clocks()` 逐台设置和回读。

## 物理量绑定

每个 mapping key 保存期望设备类型、`device_id`、类型化 `endpoint`、标签和说明。
端点类型包括信号源/TEC 的 `channel`、HF2 的 `demod` 和 DLC pro 的
`laser_channel`。`load_mapping()` 仍返回带 `instrument`、`model`、`resource`、
`channel`、`demod_idx` 等字段的展开结构，供旧脚本兼容读取。

`constraints.shared_channel_groups` 显式允许多个物理量共用同一端点。未加入同一
共享组的重复端点会被拒绝。设备控制页以 mapping key 为入口，共享端点的当前选择
直接确定安全语义，不允许前端另行提交设备 ID、通道或其他 mapping key。

`constraints.colocation_groups` 声明当前必须同机的物理量。删除或调整同机组后，
新模式工作流可把每个绑定连接到独立设备；同一 resource 仍由 `DeviceSession` 复用。

## GUI 与 API

“仪器控制”页面包含“物理量映射”“设备库”“设备控制”三个标签页。映射和设备库
都采用整批草稿保存；被当前映射引用的设备不能删除。VISA 扫描仅枚举资源并查询
`*IDN?`，不会更改仪器状态，非 VISA 设备需要手工录入。

配置接口为：

- `GET/PUT /api/device-library`
- `POST /api/device-library/discover-visa`
- `GET/PUT /api/physical-mappings`

设备控制接口为 `GET /api/control-targets`、`POST /api/control-targets/refresh-all`、`POST .../{mapping_key}/refresh` 和
按设备类型区分的 `PUT .../{mapping_key}/generator|current-source|laser|emission|scope|tec`。
目录请求只读取配置，不连接硬件；读取和写入必须携带设备库与映射修订号，并在短硬件锁
内重新解析绑定。HF2 Demod 只读，不注册写接口。旧 `/api/devices` 接口保留兼容。

控制页严格按映射顺序以多列网格显示物理量，支持逐项读取和顺序读取全部。批量读取允许
部分设备失败，并在对应面板显示错误。`rf_coil` 是 `Y_magnetic_field` 的历史别名，控制页
只显示“Y方向磁场”并采用 `-10～10 V` 安全范围。信号源只读取目标通道；
非 Vpp 状态允许回读和关闭输出，其余写入必须先切换为 Vpp 并重新读取。TEC 只开放目标
温度写入，使能、输出模式、电阻和 PID 不提供写入口。

GS200 与 Keithley 6221 是两个独立设备类型，但都映射到控制页的 `current_source`
类别。GS200 只接受原有直流电流和输出字段；6221 另有量程、Compliance、滤波、响应
速度及内部波形设置。6221 只有在设备库、物理 mapping 和对应安全限值都配置完整后才会
进入控制目录；设备库接入本身不会自动创建物理绑定或安全规则。

每次 GET 返回内容修订号；PUT 必须携带 `base_revision` 和完整草稿。保存使用进程锁、
临时文件替换和保存后回读。过期修订返回 409，配置校验失败返回 422，硬件任务运行
期间返回 409 并禁止修改。

## 实验契约与快照

`typed_workflow` 通过 `ExperimentDefinition.required_mapping_keys` 声明物理量依赖。
预检和运行开始时重新解析配置，并在连接前验证设备类型、端点及任意波/无限 Burst
能力。每次运行的 `experiment_config.yaml` 保存设备库和映射修订、两个原始配置文档、
完整解析映射、安全限值及工作流实际设备状态，历史数据不依赖之后修改的全局映射。
