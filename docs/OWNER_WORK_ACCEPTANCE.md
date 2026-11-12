# 台式机主人私聊验收

2026 年 11 月 12 日，AstrBot 4.29.4、NapCat 4.18.38、DSH 0.2.2-rc.1。验收在台式机开发实例完成。

离线回归运行 945 项，925 项通过，20 项跳过。另用实际 DSH 服务空跑，核对文件模式和 Flash 选择后，再做 QQ 委托实测。

| 功能 | 使用方式 | 真实验收 |
| --- | --- | --- |
| 百炼合成 | 在主人私聊要求用语音回答。明确要求只用语音时，收到 OneBot 回执后省略文字确认。 | QQ 收到并播放一条正确的语音，复验无额外文字。 |
| 百炼听写 | 在主人私聊发送 QQ 语音。 | 听写记录正确识别「海底花园」，QQ 返回相同四个字。 |
| 主人提醒 | `/提醒 YYYY-MM-DD HH:MM 内容`，北京时间。另有 `/提醒 列表` 和 `/提醒 取消 ID`。 | 收到创建回执。05:24:03 收到一条 05:24 的定时提醒，数据库保存 OneBot 消息回执。 |
| 受托文件任务 | `/委托 文件名 处理要求`，另有 `/委托 列表` 和 `/委托 取消 ID`。 | QQ 接受委托后返回 `sample.csv` 的合计 5。已核对报告、来源清单、只读输入、模型用量和 QQ 发送回执。交互账本有已结算的 Anthropic 请求。 |

## 配置和状态

- 百炼提供商在 AstrBot 面板配置，使用与音色匹配的 `qwen-audio-` 模型和现有 `qwen3-asr-flash` 听写。全局自动语音保持关闭。百炼费用走其账户，DeepSeek 人民币账本覆盖受控 Flash 调用。
- 提醒走现有活动插件的定时检查与活动 SQLite 的 `settings`。通用 `add_cron_tools` 保持关闭。创建后可在投递前取消，等待 QQ 重连后继续投递，结果不明时保留状态供人工核查。
- 受托文件服务仅监听 `127.0.0.1:59935`，以 `FishHomeRunner` 运行，使用独立的 DSH 状态目录和 `fishhome-files` 文件工具模式。发往现有交互账本。主人只能委托管理员放入 `commission-files` 并加入本机 DSH Bridge `m3.json` 的 `commission_sources` 白名单的文本文件。当前白名单只有验收用的 `sample.csv`。
- 委托、取消、产物核验状态、来源 QQ 消息及投递回执保存在活动 SQLite。报告和来源 JSON 保存在 Runner 的 `commission-files/output`。QQ 回执附报告前 1200 字。取消限 DSH 提交前，断线后的未知投递保留为待核实，运行过程中断时按已保存的 DSH 会话继续核查。

## 安装和回滚

停 AstrBot 并退出托盘后，使用 `powershell -File deploy/desktop_manager/install.ps1 -InstallOwnerWork` 安装。重开控制台后可以单独启动「DeepSeek Harness（受托文件）」。启动会准备工作区，文件白名单由台式机管理员设置。AstrBot 的百炼 Key 和音色继续通过本机面板配置。

台式机安装前的活动源码、DSH Bridge 源码与 AstrBot 语音适配器在本机控制台状态目录下的两份 `owner-work-before-*` 中。回滚时停止 AstrBot 和受托 DSH，用本轮首次备份还原源码，移除本轮新增的运行端插件文件和受托 DSH 启动器，启动原服务。保留业务数据库和产物。ThinkBook 安装与验收另行记录。
