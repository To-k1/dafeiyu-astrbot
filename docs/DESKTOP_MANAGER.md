# FishHome 台式机托盘控制台

双击 `%FISH_HOME%\dev\Start-FishHome.cmd` 启动全部组件并打开 WebUI。直接打开旁边的 `FishHome.exe` 可先查看现有状态，再按需启动。右键 FishHome 托盘图标可打开 WebUI、启动全部或退出控制台。

控制台支持 AstrBot、NapCat、Ollama、DeepSeek Harness、PhoneMCP、雷电实例 0、房间与记忆星海、人民币计费服务。已有监听或正在启动的组件会跳过。可以单独启动、停止或重启识别到的服务。退出控制台时服务继续运行。雷电仍使用正常的图形窗口。

## 人民币计费联调

1. 在管理员 QQ 私聊发送 `/额度`。当前台式机应直接回复交互和自主两本人民币余额，默认各 ¥5，并标明北京时间 04:00 换日。
2. 在控制台的「本机凭据与计费接入」填入已有的官方 DeepSeek API Key，保存后重启「人民币计费」。Key 由 Windows DPAPI 加密保存。待填写期间可查询账本，模型和收费工具请求在本机返回 503。
3. 切换 AstrBot 和 DSH 的实际提供商之前先核对 [M5 配置](M5_ACCEPTANCE.md)。AstrBot 的 OpenAI Chat Completion 提供商使用页面显示的本机凭据，DSH 的自主入口使用单独的自主凭据。切换后再在 QQ 验收额度耗尽时的拒绝和普通聊天恢复。

## 安装与维护

开发源在 `deploy/desktop_manager`。从仓库运行 `powershell -File deploy/desktop_manager/install.ps1 -InstallBudgetBridge`，使用 Windows 自带 .NET Framework 编译器生成 `%FISH_HOME%\dev\FishHome.exe`。运行时复用开发实例的 AstrBot Python 环境。覆盖 EXE 前先退出托盘控制台。安装保留旧启动脚本 `Start-FishHome.before-tray.cmd`，并备份 Bridge 源码到 `%LOCALAPPDATA%\FishHome\manager\bridge-before-budget`。本机日志、进程识别记录和加密凭据都在 `%LOCALAPPDATA%\FishHome\manager`。

WebUI 只监听 `127.0.0.1:6196`，由托盘打开的浏览器会话可操作服务。DSH 仍在 `FishHomeRunner` 下运行，托盘启动后会更新签名票据。NapCat 复用原脚本的快登配置。正式 ThinkBook 的路径和启动策略另行验收。

## 台式机实测

- 已编译并安装 `FishHome.exe`，替换本机一键启动脚本。8 个组件均通过本机状态检测。重开托盘与重复一键启动保留了既有服务进程。单独停止和重启房间后重新收到 HTTP 200，空闲 DSH 经单项重启恢复并重新签发 Bridge 票据。雷电使用原图形启动入口后通过 `emulator-5554` 的 Android 开机探针。
- 2026-11-09 12:00:22 已收到真实管理员 QQ `/额度` 回执，交互和自主各 ¥5，北京时间 04:00 换日。用户保存 Key 并重启后，本机计费服务以在线模式提供余额。真实模型提供商切换待完成。

### 计费切换断点

自动审批拒绝了会生成明文凭据批处理的切换脚本，脚本未执行。仓库已准备进程环境方案，使用非敏感测试值实测 `FishHomeRunner` 子进程能继承临时变量。现有额度与运行配置仍是切换前状态。

接续时先关闭并重开托盘控制台以加载更新，服务继续运行。AstrBot 的 `deepseek` 提供商可使用 `${FISHHOME_INTERACTION_TOKEN}` 引用，由控制台启动时注入。DSH 使用 `deploy/dsh_bridge/m5-gateway.yml` 配合现有文件权限配置，并在控制台本地设置 `metered_dsh: true` 后重启。切换后先用临时 ¥0 验证两个实际请求路径，随后恢复原额度。DeepSeek 官方 Key 继续保存在 Windows DPAPI 加密存储中。
