# 运行目录说明

这是**运行目录**，只用来运行实验，不在此开发。

- 代码更新只能从开发目录发布：在开发目录执行 `tools\release_to_lab.ps1`（只允许快进，发布前检查 GUI 空闲）。
- 不要在本目录改代码、切分支或提交（`params/` 的界面改动由发布脚本自动回存）。
- 回滚：开发目录执行 `tools\release_to_lab.ps1 -Rollback <lab-标签>`。
- 启动：双击 `start_lab.vbs`；停止：双击 `stop_gui.bat`（等待安全收尾，不要强杀）。
- 本目录有独立的 `agent_exp_env`（可编辑安装指向本目录），不要复用开发目录的环境。
- 代理（AI 助手）对 `data/` **只读**：实验数据、历史运行不得修改或删除。
- 发布日志：`tmp\release.log`；GUI 日志：`tmp\gui-*.log`。