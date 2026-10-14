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
- `connection`：非 VISA 连接参数，例如 HF2 / MFLI device ID、数据服务器地址和接口、DLC pro 端口。
- `capabilities`：通道范围、任意波点数和无限 Burst 等能力。
- `reference_clock`：`INT`、`EXT` 或不适用。
- `acquisition_mode`：锁相解调时序全局模式 `auto`、`daq`、`scope`；缺字段按 `auto`。

锁相卡片的“自动／DAQ／Scope”随设备库草稿整批保存，沿用设备库/映射修订检查；
硬件任务活动或排队时不能修改采集模式或保存设备库。映射解析将模式传给 `create_lockin()`，
新会话固定该设置，已有会话保持启动快照。自动模式下 HF2 使用 DAQ，MFLI 按当次请求率
以 70 kSa/s 为界选择 DAQ/Scope。能力与单段内存不足时预检拒绝，不改接线或回退；
详情见 [解调采集规则](lockin_amplifier.md#解调时序的模块选择)。

设备库读取和保存时，对已支持的 DG4000/DG900 补全缺失的
`supports_arbitrary`、`supports_infinite_burst` 为 `true`，适用于 GUI 扫描入库、
手工新增和已有的不完整记录。显式配置的 `false`、通道范围及点数限制保持原值；
未知型号和其他设备类型不自动获得信号源能力。读取不改写文件，保存时持久化补全结果。

设备文档的顶层 `network_resources` 保存待扫描的 TCPIP VISA 地址，与 USB 设备使用
同一设备库和修订号。GUI 可按行编辑并随设备草稿一次保存，最多 64 个地址，VISA
别名会去重。仅当此字段缺失时才读取旧 `params/instrument_discovery.yaml`；新字段
存在时始终优先使用，包括空列表。空列表只清空扫描候选，已入库设备仍参与扫描。

`192.168.124.31/.32/.33/.34` 已列入候选，当前设备库也保存了对应的独立仪器定义。
后续新增仪器仍需主动扫描确认身份后独立入库；USB 与网络中的不同仪器不做自动配对。

`params/clock_sources.yaml` 仅用于旧入口兼容回退。新模式的参考时钟目标来自
设备库，并在连接后由 `synchronize_connected_clocks()` 逐台设置和回读。
该步骤及 `synchronize_clock_device()` 支持可选 `settle_wait=callback`，把稳定等待交给调用方；
默认仍使用 `time.sleep`。静磁场提供每 0.1 秒检查取消的等待，取消异常直接传播，
不被转换成普通时钟失败记录；设置后仍严格回读，未缩短硬件稳定等待时长。

### 统一外置参考时钟

当前系统的 DG4000、DG900 和 HF2 / MFLI 统一使用同一外置 **10 MHz** 参考源，
在[设备库](../params/devices.yaml)中配置 `reference_clock: EXT`。
Pump 调制信号源 `dg4e222800868`（`DG4E222800868`）也使用外部参考，
其 `Pump_modulation` 载波通道与 `Time_sequence` 门控通道共用该设备的参考时钟。

运行前开启外置参考源，将同一参考信号分别接入各 DG4000、DG900 和所选锁相放大器的
外部参考时钟输入，并在仪器上确认接线和锁定状态。外置源由人工设置，
同步程序只配置已接入的仪器；门控与外触发接线继续按各实验说明执行。

新模式工作流在连接后自动设置并严格回读。旧 Python 实验运行前，先在 GUI
“功能模块 → 参考时钟同步”执行同步，或在仓库根目录运行
`agent_exp_env\Scripts\python.exe experiments\Set_All_Clocks_External.py`，
确认所有设备同步成功后再启动实验。两个批量入口默认读取设备库，
[旧时钟配置](../params/clock_sources.yaml)保留兼容用途，并同样将 Pump 配置为 `EXT`。
批量同步每次读取已保存的当前映射，USB 与 TCPIP 信号源可混用；同一资源的多通道
只连接并同步一次。时钟目标与设备识别使用同一规则：USB 沿用地址中的序列号，
TCPIP 使用设备库 ID，锁相使用 `connection.device_id`。网络地址中的 `inst0`、端口或
`INSTR`/`SOCKET` 不作为设备 ID；更换并保存 Resource 后使用新设备的参考时钟目标。

GUI/脚本批量同步分两阶段执行：先连接全部设备、读取原值并写入目标时钟源，保持连接，
再统一回读校验并释放连接。同步入口显式跳过信号源自动负载策略，不改变输出负载、
幅度、偏置或输出开关。

负载由独立的“功能模块 → 输出负载初始化”调用 `initialize_output_loads()` 设置：
Pump 调制通道设为 50 Ω，其余设为高阻，包括 Probe AOM 载波、加热和温度开关。
DG900 使用 `INF` 写入高阻并回读验证，负载不匹配则报告该设备失败。
负载初始化不改变参考时钟；更改负载后应核对幅度和偏置回读。正式实验通过设备工厂
连接信号源时仍默认应用本次物理量及联动组的负载策略。

同机通道复用连接时只同步一次；工作流中设置异常或回读不一致会终止实验，
批量入口继续报告其他设备的结果。`EXT` 回读仅验证参考源选择，不能证明外部参考已锁定。

## 物理量绑定

每个 mapping key 保存期望设备类型、`device_id`、类型化 `endpoint`、标签和说明。
端点类型包括信号源/TEC 的 `channel`、锁相放大器的 `demod` 和 DLC pro 的
`laser_channel`。`load_mapping()` 仍返回带 `instrument`、`model`、`resource`、
`channel`、`demod_idx` 等字段的展开结构，供旧脚本兼容读取。展开结构中的
`device_library_id` 保留设备库逻辑 ID；`device_id` 是驱动实际使用的连接 ID，
例如 HF2 的 `dev18246`，避免把逻辑 ID `hf2_dev18246` 误用于 LabOne 节点路径。

信号源允许不同实验的物理量绑定同一设备通道，例如 Z/时序与 Probe AOM 可按实验分别使用。
新模式实验在连接前按本次必需物理量检查占用：独立物理量占用同一通道时拒绝启动。
`constraints.shared_channel_groups` 声明必须联动、始终使用同一端点的别名，组内物理量
也可以在同一实验中引用；其他设备类型仍拒绝未声明共享组的重复端点。设备控制页以 mapping key 为入口，共享端点的当前选择
直接确定安全语义，不允许前端另行提交设备 ID、通道或其他 mapping key。

`constraints.colocation_groups` 声明当前必须同机的物理量。删除或调整同机组后，
新模式工作流可把每个绑定连接到独立设备；同一 resource 仍由 `DeviceSession` 复用。
温控两路由 `temperature_control` 同机组约束：`Heat_Control`（加热信号）使用 CH1，
`Temp_Switch`（温度开关信号）使用同一台仪器的 CH2；保存时拒绝只更换其中一路的设备。

GUI 更换设备时沿同机组和共享通道组的重叠成员联动整个组，保留所有端点类型与
通道号；目标设备缺少任一成员的通道时拒绝整个切换，不自动改用第一通道。
两类组的成员都填写物理量键，不填写设备 ID。联动先更新草稿，整批保存仍由后端
检查类型、通道、重复占用和组约束，通过后原子替换文件。

`create_signal_generator("Z_magnetic_field")` 按当前绑定返回对应 DG4000/DG900
未连接实例，默认通道取该物理量端点；原配置字典和 resource 字符串调用保持兼容，
显式 `channel=` 仍可覆盖默认值。连接失败直接报错，不自动更换设备。

信号发生器连接时按本次物理量及其同机/共享通道组应用负载策略：`Pump_modulation`
所在通道为 `50 Ω`，其余通道为 `High-Z`，包括 `Probe_AOM_Carrier`。同一端点跨实验复用时，
选择 Z/时序仍使用高阻，不被另一实验的 Pump 调制映射覆盖。展开映射保留 `mapping_key`
供配置字典调用识别用途；仅传 resource 的旧调用继续使用该资源的整体策略，复用设备时
应传物理量键或 `load_mapping()` 返回的配置。策略由工厂和仪器控制页的连接入口应用。

DG900 Pro 高阻使用 `INF` 参数，并回读确认返回高阻数值（约 `9.9E+37`）；
数值负载同样验证。期望高阻却回读为 `1 Ω` 或 `50 Ω` 时立即报错，避免继续配置实验输出。

## GUI 与 API

“仪器控制”页面包含“物理量映射”“设备库”“设备控制”三个标签页。映射和设备库
都采用整批草稿保存；被当前映射引用的设备不能删除。“扫描 USB / 网络 / 锁相放大器”
枚举 VISA 资源（包含 TCPIP SOCKET）、设备库已有的 VISA 地址，以及
[设备库](../params/devices.yaml) 顶层 `network_resources` 中的指定地址；VISA 只查询 `*IDN?`。
锁相设备通过已配置的数据服务器（未配置时使用 HF2 本机 `127.0.0.1:8005`）枚举设备节点并读取型号，
不会调用 `connectDevice`、`disconnectDevice` 或改变仪器设置。其他非 VISA 设备仍需手工录入。

扫描结果显示序列号、完整连接地址和“已入库 / 已加入，待保存 / 未入库”状态；
USB 与网络连接按型号及序列号识别为同一台设备，已有设备禁止重复加入。
扫描失败的地址和原因显示在结果底部。扫描不会自动更新设备库或切换已有设备的连接方式，
新设备点击“加入设备库”后，还需点击“保存设备库”才能写入 `params/devices.yaml`。
新加入的 VISA 设备将序列号保存在 `connection.serial_number`；已有 USB 设备从 Resource 提取序列号。
物理量设备下拉列表同时显示 USB 和 IP 仪器的标签、型号、连接地址及可用序列号。
修改网络扫描地址后先保存设备库，再扫描；单个地址连接失败不会隐藏其他扫描结果。
信号源卡片按用途和联动组展示，Resource 下拉选择设备库中的实际仪器。比如 Z/时序
可在 `USB0::0x1AB1::0x0641::DG4E242401288::INSTR` 与 `TCPIP0::192.168.124.33::INSTR`
之间选择，整组保留 CH1/CH2；允许目标通道同时保留其他实验的映射，不自动交换用途。
加热与温度开关合并为一张“加热信号 / 温度开关信号”卡片，共用一个 Resource 下拉菜单，
点击“保存设备库”后两路一起生效，通道分配保持 CH1/CH2。
主磁场 GS200 卡片同样提供 Resource 下拉菜单，只列出设备库中的 `gs200` 设备，
不混入信号发生器或 Keithley 6221。网络 GS200 `9026D6597` 使用已验证可回读的
`TCPIP0::192.168.124.41::7655::SOCKET`，原 USB 设备 `gs200_90z631552` 仍保留为候选。
主磁场当前绑定以 [mapping.yaml](../params/mapping.yaml) 为准；选择候选后点击
“保存设备库”才切换实际设备，保留 `source_function` 等原映射字段。
7655 命令 Socket 使用 LF 收发终止符，仪器发送终止符应为 LF 或 CR+LF；
连接或身份查询失败时释放会话。HTTP 网页可访问不代表 VXI-11 命令服务可用。
卡片显示所选设备的 ID 和配置，原设备仍保留在设备库；“设备连接地址”修改所选仪器
自身的 Resource。不同用途选中同一仪器时，其设备配置也是同一份。

HF2 与 MFLI 合并显示为单张“锁相放大器”卡片，全系统选择更新全部锁相物理量草稿，
保留各绑定的 `demod` 索引。信号源、主磁场 GS200 和锁相选择均在点击“保存设备库”后与设备配置一起
生效；页面提示未保存更改，失败保留草稿。硬件活动/排队时允许编辑草稿，禁止保存。
逐行映射不再提供另一处锁相设备选择。
卡片显示当前设备的 `connection.device_id` 和数据服务器 `host:port`，MFLI 说明同时覆盖 USB RNDIS 与网口。当前 `dev30661` 使用电脑本地服务器 `127.0.0.1:8004`，服务器经 USB RNDIS 地址 `192.168.150.246` 访问仪器，实际报告的 `connection.interface` 为 `1GbE`；设备库标签标明 USB RNDIS，标签本身不选择物理链路。访问仪器内置服务器时，接口才为内部 `PCIe`。扫描使用 `/zi/devices` 中的实际 `INTERFACE`，不按型号或接线名称推断；不要仅为更改显示而将当前接口改成 `USB`。两线同时连接时固定 USB 的服务器启动参数见 [锁相连接说明](lockin_amplifier.md#同时连接-usb-与网线)。

配置接口为：

- `GET/PUT /api/device-library`（PUT 可同时提交物理量映射草稿和对应修订号）
- `POST /api/device-library/discover-visa`
- `GET/PUT /api/physical-mappings`
- `PUT /api/lockin-selection`（旧 API 兼容入口；GUI 统一通过保存设备库提交选择）

设备控制接口为 `GET /api/control-targets`、`POST /api/control-targets/refresh-all`、`POST .../{mapping_key}/refresh` 和
按设备类型区分的 `PUT .../{mapping_key}/generator|current-source|laser|emission|scope|tec|lockin`。
目录请求只读取配置，不连接硬件；读取和写入必须携带设备库与映射修订号，并在短硬件锁
内重新解析绑定。锁相面板提供统一解调、Signal Output 和 Aux Output 设置；输出必须配置对应设备/通道的安全限值，否则只允许回读及关闭主输出。
`POST .../{mapping_key}/lockin-capture` 使用全局任务队列采集解调 X/Y/R 或内置 Scope 原始电压，并支持取消。旧 `/api/devices` 接口保留兼容。细节及两机差异见 [锁相接口说明](lockin_amplifier.md)。

控制页严格按映射顺序以多列网格显示物理量，支持逐项读取和顺序读取全部。批量读取允许
部分设备失败，并在对应面板显示错误。`rf_coil` 是 `Y_magnetic_field` 的历史别名，控制页
只显示“Y方向磁场”并采用 `-10～10 V` 安全范围。信号源只读取目标通道；
非 Vpp 状态允许回读和关闭输出，其余写入必须先切换为 Vpp 并重新读取。TEC 只开放目标
温度写入，使能、输出模式、电阻和 PID 不提供写入口。

GS200 与 Keithley 6221 是两个独立设备类型，但都映射到控制页的 `current_source`
类别。GS200 只接受原有直流电流和输出字段；6221 另有量程、Compliance、滤波、响应
速度及内部波形设置。6221 只有在设备库、物理 mapping 和对应安全限值都配置完整后才会
进入控制目录；设备库接入本身不会自动创建物理绑定或安全规则。

当前 `main_magnetic_field` 保持绑定 GS200，`keithley_6221_main_field` 独立绑定
Keithley 6221。Mx Keithley 6221 最优控制 RF 灵敏度实验中，两个 key 分别对应
Z 主磁场线圈和 Z 小磁场线圈，可以同时输出；运行前仍必须人工确认两路没有接到同一线圈。
当前映射编辑器按 key 声明的
`instrument` 过滤候选设备，因此 `main_magnetic_field` 不能直接在 GS200 与 6221
之间切换；若以后需要通用切换，必须先把旧实验的 GS200 专属调用重构为按型号分派。

每次 GET 返回内容修订号；PUT 必须携带 `base_revision` 和完整草稿。保存使用进程锁、
临时文件替换和保存后回读。过期修订返回 409，配置校验失败返回 422，硬件任务运行
期间返回 409 并禁止修改。

设备库 PUT 接收 `network_resources`，显式 `[]` 清空候选；旧调用未提供此字段时保留
现有候选列表。网络地址修改也会改变设备库修订号，过期草稿不会覆盖新地址。

GUI 的设备库 PUT 同时提交 `mapping` 和 `physical_mapping_revision`，后端在同一硬件锁、
配置进程锁内检查设备库与映射两份修订，再校验整批配置。映射写入失败会恢复设备库原始
内容，成功响应附带 `physical_mappings` 及新修订号。旧 PUT 未提供 `mapping` 时只保存设备库。

## 实验契约与快照

`typed_workflow` 通过 `ExperimentDefinition.required_mapping_keys` 声明物理量依赖。
预检和运行开始时重新解析配置，并在连接前验证设备类型、端点及任意波/无限 Burst
能力。每次运行的 `experiment_config.yaml` 保存设备库和映射修订、两个原始配置文档、
完整解析映射、安全限值及工作流实际设备状态，历史数据不依赖之后修改的全局映射。

锁相预检还检查所选型号的频率、解调器和振荡器范围；双主电压回采实验选择 MFLI 时拒绝启动。不会自动降频、改接线或回退 HF2，具体清单见 [锁相实验兼容性](legacy_lockin_experiments.md)。
