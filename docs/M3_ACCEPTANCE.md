# M3 台式机 DSH 文件任务验收

2026-11-08，台式机开发实例使用官方 npm 包 `@deepseek-ai/dsh@0.2.2-rc.1` 和 Node.js 24.21.0。复用 DSH Bridge 1.0.0 的 `DshClient.run_prompt`，其上游源码检查点为 `bf49f1f`。FishHome 的 [兼容层](../deploy/dsh_bridge/dsh_remote.py) 使用官方本机认证，适配新版会话 RPC 和历史响应。

| 项目 | 实测结果 |
|---|---|
| 执行 | 新会话选择官方 `deepseek-official` 的 `deepseek-flash`。Bridge 等待 6.094 秒，DSH 记录 `turn/end` 为 `completed`。 |
| 文件 | `sample.csv` 保持原样。`output/report.md` 写出总计 24 分钟并引用原始文件。`output/sources.json` 记录来源 `sample.csv` 和总计 24。输出目录仅这两个文件。 |
| 用量 | DSH `tokenUsage` 记录未缓存输入 10,083、缓存命中输入 19,584、输出 478、缓存写入 0。 |
| 认证与失败 | 无凭据会话请求返回 401。认证后可列出、创建空会话并读取历史和状态。对空会话的取消返回 `accepted=true`。关闭的本机端口返回连接错误。 |

## 运行边界

- DSH 监听 `127.0.0.1:59934`，以本地标准账户 `FishHomeRunner` 运行。实际测试工作区在该账户的 `%LOCALAPPDATA%\FishHome\m3-file-check`，DSH 状态在 `%LOCALAPPDATA%\FishHome\DSH`。官方模型凭据通过本机 DSH 页面配置。
- `D:\Share\code\FishHome\dev\dsh-0.2.2-rc.1\Start-DSH-M3.cmd` 启动固定版本。它依赖 [M3 配置层](../deploy/dsh_bridge/m3-file-only.yml) 的本机副本，并使用 `.m3-root` 标记测试工作区。实测文件工具可用，Web、Shell 和后台作业工具关闭。
- Windows 专用账户的实际访问检查显示，它可以读取测试输入，打开输入进行写入会失败。仓库、AstrBot 和 Codex 的配置文件在权限探针中均无法打开。
- 早期尝试在共享路径的父目录解析阶段结束。失败会话在模型第一步开始前退出，且没有生成文件。保留它们的回执供核对，实际付费任务在私有工作区完成。
- `dev\dsh-0.2.2-rc.1\workspaces\m3-file-check\output` 存放两份核验后的产物副本，`dev\dsh-0.2.2-rc.1\m3-file-run.json` 存放已完成任务回执。回退时可停止该独立 DSH 实例，保留用户目录下的设置和任务日志。

运行中的任务遇到断线后的重连，以及活动回合被取消后的终态仍待实测。本轮通过独立的 Bridge 客户端调用完成文件验收，接入 AstrBot 的 QQ 触发和上游生活流程留待后续。费用硬上限仍按 M5 验收。
