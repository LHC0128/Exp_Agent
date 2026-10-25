# 文档导航

## 分类

| 位置 | 内容 |
| --- | --- |
| [experiments/](experiments/) | 各实验的原理、接线、操作、数据采集和离线分析手册 |
| [instruments/](instruments/) | 本仓库的仪器驱动使用说明、接线说明和编程补充笔记 |
| [manuals/](manuals/) | 厂商 PDF / CHM / HTML 说明书、通讯协议、提取文本及厂家示例 |
| 当前目录 | 跨实验公共规范、配置说明、模板与验证报告 |
| [old/](old/) | 历史说明、Notebook 和架构图归档，不作为当前入口 |
| [agents/](agents/) | 本地 AI 协作说明，不上传 GitHub |

实验脚本与文档的对应关系见[主要实验入口](../README.md#主要实验入口)。
`experiments/` 中也保留了已归档实验的参考手册；当前可运行实验、稳定 ID 和执行模式
以[实验注册表](../lab_workflows/experiments/registry.py)为准。
实验文档的 `defaults_file` 仍相对仓库根目录解释，历史参数以各次运行的配置快照为准。

## 仪器使用说明

- [DG4000 / DG900 Pro 信号发生器](instruments/signal_generator.md)
  · [DG900 Pro 编程补充](instruments/dg900_pro_programming_notes.md)
  · [DG4000 与示波器 CH3 接地接线](instruments/dg4000_scope_ch3_grounding.md)
- [HF2 / MFLI 锁相放大器](instruments/lockin_amplifier.md)
- [SDS 示波器采集](instruments/sds_acquisition.md)
- [GS200 直流源](instruments/gs200.md)
- [Keithley 6221 电流源](instruments/keithley_6221.md)
- [TEC103 温控器](instruments/tec_controller.md)
- [TOPTICA DLC pro 激光器](instruments/toptica_dlc_pro.md)

## 厂商原始资料

| 设备 / 软件 | 手册与配套资料 |
| --- | --- |
| DG4000 | [编程手册 CHM](manuals/DG4000_ProgrammingGuide_CN.chm)、[提取的 HTML](manuals/DG4000_extracted/) |
| DG900 Pro | [用户手册](manuals/DG900Pro_UserGuide_CN.pdf)、[编程手册](manuals/DG900Pro_ProgrammingGuide_CN0.pdf) |
| GS200 | [操作手册](manuals/IM_GS210-01CN_20210112.pdf) |
| HF2 | [中文用户手册](manuals/ziHF2_UserManual_cn.pdf) |
| LabOne | [编程手册](manuals/LabOneProgrammingManual_20.07.0.pdf)、[API 文档](manuals/LabOne_API_Docs/)、[API 示例](manuals/labone-api-examples/) |
| SDS | [编程手册](manuals/SDS_ProgrammingGuide_CN11G.pdf) |
| TEC103 | [温控器说明书](manuals/RD104_SPEC_TEC103系列温控器说明书_v1.6.0.pdf)、[通讯协议](manuals/RD105_SPEC_通讯协议_v1.3.0.pdf) |

原仓库根目录 `manuals/` 已整体合并至此。两份内容完全相同的 DG900 Pro 用户手册合并为一份；
其余原始文件名、提取文本与厂家示例的内部目录保持不变。厂商资料沿用本地保留策略。

## 公共说明

- [设备库与物理量映射](instrument_mapping.md)
- [采集时序、系统稳定与 TEC 设置](acquisition_timing.md)
- [科研绘图规范](plotting_style.md)
- [历史分析与绘图入口](legacy_analysis_plotting.md)
- [锁相实验兼容性](legacy_lockin_experiments.md)
- [实验方案模板](experiment_template.md)
- [历史实验代码归档与恢复说明](../old/experiments/README.md)
