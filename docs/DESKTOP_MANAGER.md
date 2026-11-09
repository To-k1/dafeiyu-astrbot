# FishHome 台式机托盘控制台

双击 `%FISH_HOME%\dev\Start-FishHome.cmd` 启动全部组件并打开 WebUI。直接打开旁边的 `FishHome.exe` 可先查看现有状态，再按需启动。右键 FishHome 托盘图标可打开 WebUI、启动全部或退出控制台。

控制台支持 AstrBot、NapCat、Ollama、DeepSeek Harness、PhoneMCP、雷电实例 0、房间与记忆星海、人民币计费服务。已有监听或正在启动的组件会跳过。可以单独启动、停止或重启识别到的服务。退出控制台时服务继续运行。雷电仍使用正常的图形窗口。

## 人民币计费

- 管理员 QQ 私聊发送 `/额度`，可查看交互与自主两本人民币账，默认各 ¥5，北京时间 04:00 换日。
- 更新官方 DeepSeek API Key 时，在「本机凭据与计费接入」保存后重启「人民币计费」。Key 由 Windows DPAPI 加密保存。待填写期间可查询账本，收费请求在本机返回 503。
- 台式机 AstrBot 的 Flash 请求进入交互账本，DSH 的 Flash 请求进入自主账本。两条路径依赖托盘启动时注入的进程环境。更多配置见 [M5 验收记录](M5_ACCEPTANCE.md)。

## 安装与维护

开发源在 `deploy/desktop_manager`。从仓库运行 `powershell -File deploy/desktop_manager/install.ps1 -InstallBudgetBridge`，使用 Windows 自带 .NET Framework 编译器生成 `%FISH_HOME%\dev\FishHome.exe`。运行时复用开发实例的 AstrBot Python 环境。覆盖 EXE 前先退出托盘控制台。安装保留旧启动脚本 `Start-FishHome.before-tray.cmd`，并备份 Bridge 源码到 `%LOCALAPPDATA%\FishHome\manager\bridge-before-budget`。本机日志、进程识别记录和加密凭据都在 `%LOCALAPPDATA%\FishHome\manager`。

WebUI 只监听 `127.0.0.1:6196`，由托盘打开的浏览器会话可操作服务。DSH 仍在 `FishHomeRunner` 下运行，托盘启动后会更新签名票据。NapCat 复用原脚本的快登配置。正式 ThinkBook 的路径和启动策略另行验收。

## 台式机实测

- 已编译并安装 `FishHome.exe`，替换本机一键启动脚本。8 个组件均通过本机状态检测。重开托盘与重复一键启动保留了既有服务进程。单独停止和重启房间后重新收到 HTTP 200，空闲 DSH 经单项重启恢复并重新签发 Bridge 票据。雷电使用原图形启动入口后通过 `emulator-5554` 的 Android 开机探针。
- 2026-11-09 12:00:22 已收到真实管理员 QQ `/额度` 回执，交互和自主各 ¥5，北京时间 04:00 换日。用户保存 Key 后，本机计费入口在线。AstrBot 和 DSH 已完成切换，两条零额度请求均在网关处被拦截，测试后恢复原额度。

### 本机切换与回滚

AstrBot 的 `deepseek` 提供商与模型配置均引用 `${FISHHOME_INTERACTION_TOKEN}`。DSH 使用 `m5-file-only.yml` 保留原文件权限并附加本机计费配置。托盘在 `metered_dsh: true` 时向 `FishHomeRunner` 进程注入自主凭据。固定 DSH 版本的 Web 配置模块 ID 为 `llm-deepseek`。DSH 重启时，控制台等待启动日志中出现有效连接票据后更新 Bridge。

切换前的 `cmd_config.json`、托盘本地设置和 DSH 启动脚本以 DPAPI 加密保存于 `%LOCALAPPDATA%\FishHome\manager\m5-cutover-before.bin`。需要回滚时先停止 AstrBot 与 DSH，再用当前 Windows 用户解密还原，重开托盘并启动两项服务。SQLite 账本与现有会话保持独立。
