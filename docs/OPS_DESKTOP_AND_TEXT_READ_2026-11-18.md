# 完整文本读取与 Ops 自动桌面 · 2026-11-18

## 文本读取

本次 QQ 附件事故的真实会话中，`astrbot_file_read_tool` 结果只剩 226 字符，并带有“较早的手机工具结果已折叠”标记。通用工具历史整理错误地折叠了文件正文。修复后文字工具结果保持完整，图片历史仍可按既有规则整理；上下文重试不再通过无提示地丢掉文本来换取成功。已被折叠的旧历史不会自动重建，需要重新读取原文件。

DSH 原生 `workspaceFiles.read` 使用分页结果；客户端现在按 `offset` 接续读到 `eof`，检查读取版本与翻页进度，拼回全文。文件任务产物读取和共享目录文本读取同时取消此前另加的正文长度裁剪。没有修改人格和记忆内容。

真实 ThinkBook DSH 验证：251 行、33,383 UTF-8 字节的中英混合文本逐字一致，包含分页边界和末尾。未调用模型；临时文件清理完毕，空白验证会话保留。AstrBot 自带文件工具另有明确报错的单次大小限制，超限可使用其 `offset/limit` 分段读取；此次未改第三方读取工具的大小限制。

## Ops 桌面服务

复用现有 `FishHomeDesktop` 标准账户、`CreateProcessWithLogonW`、桌面 broker 和配对令牌。控制台通过 Windows DPAPI 保存该标准账户密码，启动前确认 Ops 位于已解锁的交互式 `WinSta0\Default`，向专用账户提供此窗口站和桌面所需权限。进程仍以专用账户身份运行，窗口位于 Ops 当前会话。

新增控制台“Windows 个人桌面”服务项、专用账户密码和随登录自动启动开关。控制台启动时检查开关并复用已有进程；跨 Windows 会话的旧 broker 返回明确提示。正常停止要求 broker 确认没有未关闭程序，并等待进程退出，避免紧接着启动时误认退出中的进程为已运行。不会强杀未保存窗口。

帮助位于控制台“使用帮助 · 主人 QQ 与 DSH”。手动代码同步步骤另见 [CODE_SYNC.md](CODE_SYNC.md)。

## 验证与回滚

公开源码基线通过 1045 项测试，19 项跳过；最终桌面与文本相关增量通过 46 项测试，覆盖密码不回显、自动启动、会话归属、正常退出及拒绝停止等。

正式 ThinkBook 已验证：Ops 和 broker 同处 session 1 的 `WinSta0\Default`；登记的样例程序实际启动并正常关闭；服务停止后能立即再次启动。进一步停止 broker、退出并重开托盘（未手动启动服务），broker 自动恢复在 Ops 会话。当前登录启动快捷方式指向 FishHome.exe，含 `--background --start-all`，密码及自动启动开关已在运行端保存。期间未向 QQ 发测试消息、未写 LivingMemory，也未改变人格。

ThinkBook 更新前的源码保存在运行账户管理目录的 `before-text-desktop-*`。受保护 broker 更新前的源码另存为 `ProgramData\FishHomeDesktop\windows_desktop.before-*.py`。回滚时先正常关闭个人程序和对应服务，再恢复代码并重启托盘/AstrBot；不覆盖数据库或人格。首次升级受保护 broker 需要 Windows UAC 确认。

未执行整机重启；登录自动恢复的现有入口沿用启动目录中的 `FishHome.exe --background --start-all`。锁屏后的 GUI 操作仍不可用，后台服务不因此等于停止。
