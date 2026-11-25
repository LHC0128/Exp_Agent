# 运行目录说明

这是**运行目录**，只用来运行实验，不在此开发。

- 代码更新只能从开发目录发布：在开发目录执行 `tools\release_to_lab.ps1`（发布前检查 GUI 空闲）。
  - 正式发布：从 master 发布，`lab` 分支只快进，打 `lab-YYYYMMDD-N` 标签。
  - 迭代发布（协同实验会话）：`-Iterate` 从 `exp/*` 实验分支发布，本目录切到分离 HEAD 并打 `lab-wip-YYYYMMDD-N` 标签，`lab` 分支不动；收尾由正式发布切回 `lab`。
- 本目录的 `origin` 是开发目录：脚本比对开发目录 HEAD 与这里刚拉到的同名分支，不一致即停止。
- 不要在本目录改代码、手动切分支或提交（`params/` 的界面改动由发布脚本回存到开发目录并丢弃本地改动，本目录不留提交）。
- 回滚：开发目录执行 `tools\release_to_lab.ps1 -Rollback <标签>`；`lab-*` 回到 `lab` 分支，`lab-wip-*` 回到对应的分离 HEAD。
- 启动：双击 `start_lab.vbs`；停止：双击 `stop_gui.bat`（等待安全收尾，不要强杀）。
- 本目录有独立的 `agent_exp_env`（可编辑安装指向本目录），不要复用开发目录的环境。
- 代理（AI 助手）可读写 `data/`，可整理历史运行；删除或移动前核实路径并备份。`data/` 下不新增代码，研究脚本放开发仓库 `scripts/`。
- 发布日志：`tmp\release.log`；GUI 日志：`tmp\gui-*.log`。