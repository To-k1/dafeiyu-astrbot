# FishHome 台式机托盘控制台

双击 `%FISH_HOME%\dev\Start-FishHome.cmd` 启动全部组件并打开 WebUI。直接打开旁边的 `FishHome.exe` 可先查看现有状态，再按需启动。右键 FishHome 托盘图标可打开 WebUI、启动全部或退出控制台。

控制台支持 AstrBot、NapCat、Ollama、DeepSeek Harness、PhoneMCP、雷电实例 0、B 站只读伴侣、房间与记忆星海、人民币计费服务。已识别为运行中或正在启动的组件会跳过。可以单独启动、停止或重启识别到的服务。退出控制台时服务继续运行。雷电仍使用正常的图形窗口。

## 台式机试运行

1. 双击 `Start-FishHome.cmd`。组件陆续启动后，在控制台运行「试运行自检」。自检核对唯一的 QQ 主人私聊、OneBot、DSH Bridge、Ollama 向量模型、房间与 Android 连接。
2. 在「FishHome 本机设置」保存机器人快登 QQ、Bridge 主人 QQ、雷电安装目录和 Ollama 模型目录。更改启动选项后重启对应服务。保存主人前，先在 AstrBot 设置唯一 QQ 管理员及其私聊白名单，关闭群聊和私聊的管理员豁免。NapCat 的登录验证和 OneBot 参数仍在 NapCat 面板设置。
3. 在「人民币预算与凭据」保存 DSH 专用标准账户密码、DeepSeek 官方 Key，查看并调节两个账本。可选择只影响本预算日或修改默认额度。
4. 「各模块设置与日志位置」可打开对应目录。AstrBot 的模型、LivingMemory、工具权限在 AstrBot 面板管理。活动插件、自主调度、DSH 文件权限使用各自的配置。

「深海工作台」首页的「去看看肥鱼」打开原版房间，旁边可打开记忆星海、AstrBot、NapCat 和 DSH WebUI。右上可切换深海模式与黑夜模式。

「备份与记忆」可在线生成本机 SQLite 归档、导出可读记忆并查看桌面备份历史。归档写到 `%FISH_HOME%\backups`，导出写到 `%FISH_HOME%\exports`。在线备份分别对长期记忆、活动账本和已安装心境中枢的 `affective.db` 取一致性快照，排除向量索引和原始聊天库。需要含索引的停写归档和隔离恢复时使用 [fish.ps1](../scripts/README.md)。

当前试跑配置只接收管理员 QQ 私聊。公共写入和自主活动处于关闭状态。公共写入总开关在「FishHome 本机设置」，平台开关、审批和额度仍在各模块。控制台启动的 B 站伴侣始终使用只读配置，当前试跑无需导入 Cookie。Ollama 使用当前 Windows 用户的持久模型目录 `%USERPROFILE%\.ollama\models`，通过本机 Vulkan 路径加载 `bge-m3`。

## 人民币计费

- 管理员 QQ 私聊发送 `/额度`，可查看交互与自主两本人民币账，默认各 ¥5，北京时间 00:00 换日。
- 更新官方 DeepSeek API Key 时，在「本机凭据与计费接入」保存后重启「人民币计费」。Key 由 Windows DPAPI 加密保存。待填写期间可查询账本，收费请求在本机返回 503。
- 台式机 AstrBot 的 Flash 请求进入交互账本，DSH 的 Flash 请求进入自主账本。两条路径依赖托盘启动时注入的进程环境。更多配置见 [M5 验收记录](M5_ACCEPTANCE.md)。

## 安装与维护

开发源在 `deploy/desktop_manager`。从仓库运行 `powershell -File deploy/desktop_manager/install.ps1`，使用 Windows 自带 .NET Framework 编译器生成 `%FISH_HOME%\dev\FishHome.exe`。运行时复用开发实例的 AstrBot Python 环境。覆盖 EXE 前先退出托盘控制台。安装保留旧启动脚本 `Start-FishHome.before-tray.cmd`。`-InstallBudgetBridge` 更新计费 Bridge 并备份原源码。`-InstallActivity` 在 AstrBot 停止时更新活动插件源码并保留原插件副本。运行配置和账本仍在 `data\plugin_data`。本机日志、进程识别记录和加密凭据都在 `%LOCALAPPDATA%\FishHome\manager`。

WebUI 只监听 `127.0.0.1:6196`，由托盘打开的浏览器会话可操作服务。DSH 仍在 `FishHomeRunner` 下运行，托盘启动后会更新签名票据。NapCat 复用原脚本的快登配置。正式 ThinkBook 的路径和启动策略另行验收。

## 界面、图标和人格源码

- **控制台 WebUI** 的 HTML、CSS 和 JavaScript 都在 [index.html](../deploy/desktop_manager/index.html)。修改页面后刷新控制台即可预览。服务 API 在 [manager.py](../deploy/desktop_manager/manager.py)。
- **鲸鱼图标** 使用用户提供的 PNG 派生 [网页图标](../deploy/desktop_manager/fishhome.png) 和 [Windows 多尺寸 ICO](../deploy/desktop_manager/fishhome.ico)。[install.ps1](../deploy/desktop_manager/install.ps1#L27) 将 ICO 编译到 EXE，[Tray.cs](../deploy/desktop_manager/Tray.cs#L81) 从 EXE 读取托盘图标。
- **大肥鱼人格** 的当前版本与回退操作见 [人格目录](../persona/README.md)。课程内容另放在 [课程记忆-模板.md](../persona/课程记忆-模板.md)。

## 台式机实测

- 已编译并安装 `FishHome.exe`，替换本机一键启动脚本。8 个组件均通过本机状态检测。重开托盘与重复一键启动保留了既有服务进程。单独停止和重启房间后重新收到 HTTP 200，空闲 DSH 经单项重启恢复并重新签发 Bridge 票据。雷电使用原图形启动入口后通过 `emulator-5554` 的 Android 开机探针。
- 2026-11-09 12:00:22 已收到真实管理员 QQ `/额度` 回执，交互和自主各 ¥5，北京时间 04:00 换日。用户保存 Key 后，本机计费入口在线。AstrBot 和 DSH 已完成切换，两条零额度请求均在网关处被拦截，测试后恢复原额度。
- 台式机试运行的记忆归档、清理范围和最新验收结果见 [试运行检查点](M6_CHECKPOINT.md)。

### 本机切换与回滚

AstrBot 的 `deepseek` 提供商与模型配置均引用 `${FISHHOME_INTERACTION_TOKEN}`。DSH 使用 `m5-file-only.yml` 保留原文件权限并附加本机计费配置。托盘在 `metered_dsh: true` 时向 `FishHomeRunner` 进程注入自主凭据。固定 DSH 版本的 Web 配置模块 ID 为 `llm-deepseek`。DSH 重启时，控制台等待启动日志中出现有效连接票据后更新 Bridge。

切换前的 `cmd_config.json`、托盘本地设置和 DSH 启动脚本以 DPAPI 加密保存于 `%LOCALAPPDATA%\FishHome\manager\m5-cutover-before.bin`。需要回滚时先停止 AstrBot 与 DSH，再用当前 Windows 用户解密还原，重开托盘并启动两项服务。SQLite 账本与现有会话保持独立。
