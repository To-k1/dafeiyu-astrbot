# Windows ↔ Windows 完整鱼生存档案与迁移合同 v0.7

> 状态：待 M0 锁定上游版本和真实 Windows 数据目录；这里是要实现的功能合同。不要把任何示意命令当成现成代码。

## 备份、导出、房间快照不是同一件事

| 类型 | 目标 | 数据范围 | 是否可作为完整恢复依据 |
|---|---|---|---|
| **原生完整备份** | ThinkBook → 台式机 `backups/` | 原生 DB、项目、来源、成果、任务状态、usage、兼容元数据 | **是，必须演练** |
| **人类可读导出** | ThinkBook → 台式机 `exports/` | JSONL、Markdown、经授权的真记忆与活动 | 否 |
| **房间只读快照** | ThinkBook → 台式机本地房间 | 观测图需要的选择性、脱敏、可过期状态 | 否 |
| **开发→正式迁移** | 台式机 dev → ThinkBook prod | 由 `none/selective/full` 选择 | 取决于模式，不能默认覆盖 prod |

## 权威与隔离

- ThinkBook 保存 prod 的唯一活跃记忆；台式机开发实例只保存 dev 数据。生产账号与开发账号 QQ 可相同，但同 QQ 不能有两个自治实例。
- 固定 `FISH_HOME` 的台式机只有 `repo/`、`backups/`、`exports/`、可选 `secrets/`；ThinkBook 的实际插件目录/数据目录由 Windows 实机审计决定，不硬编码 Linux `/var/lib/...`。
- 不传输 Windows 用户级登录态、QQ cookie、DeepSeek Key、GitHub/SSH 密钥或 Android 模拟器私密 profile；必要时在 ThinkBook 单独授权和登录。

## 最小完整备份协议

1. 暂停新后台工作，等待或检查当前步骤、对外写入状态，记录 `needs_review` 条目。
2. 调用原版 LivingMemory / AstrBot / Life/Goals/Workshop 的受支持备份方法或 SQLite backup API。重要文件与多个 DB 应捕捉到尽可能一致的逻辑快照；必要时停写，而不是复制正在写的 `.db`。
3. 额外包含 DSH 的工作空间、来源、成果、已完成步骤、最小 Fish Extensions 检查点、人民币 usage 账本、失败重试和去重标记；不新增第二套目标 DB。
4. 生成 `manifest.json`：源主机、环境 dev/prod、时间、上游/Fork commit、依赖版本、schema/嵌入模型信息、各来源路径映射、条目数、SHA256、排除项、完成标志。
5. 临时归档经本机校验成功后再作为已完成快照提供；台式机 SSH 复制 `.partial` 后再次校验，成功再更新 `last_verified` 指针。失败不能误报或清除最后成功历史。
6. 若备份前后台已暂停，备份结束仍保持暂停；若恢复失败，进入受控状态，不重复执行已完成的外部动作。

## 明文备份策略和物理副本

- 用户明确选择**不加密**，并未要求外置介质/NAS；不加密 ≠ 可以把凭据或未经审查的聊天原文混入备份。
- 只要 ThinkBook 的已完成归档已成功被拉回台式机，就属于两个物理设备上的副本；但目前没有异地或第三独立存储，不能声称抵御同地点共同故障。
- 建议 ThinkBook 每日 1 次本地一致性归档，台式机在线时补拉；保留建议 7 日/4 周/3 月。Windows 任务计划程序在**运行服务用户/受审查的管理账户**下执行，不需要 Codex 常驻，也无需模型 API 调用。
- 每周校验 SHA，每月至少一次在独立空目录/隔离实例做恢复演练。上游重大升级前额外备份。

## 人类可读记忆导出

- 真实字段优先（如 ID、来源、会话、时间、类型、重要性、项目关联，字段是否存在取决于源码），生成 `memory.jsonl`、`projects.jsonl`、`README.md`、`INDEX.md`，带来源 commit/数据版本和 hash。未知值不编造。
- 默认排除密钥、Cookie、QQ 登录态、无授权的私聊原文；普通导出不自动包含 Android 浏览器及应用数据。
- 房间只读缓存提供脱敏摘要和来源标识，显示上次同步时间和来源。台式机管理员通过独立通道按计划同步原生数据，私聊原文默认按需拉取，可单独开启自动同步。

## 从台式机 dev 到 ThinkBook prod 的迁移

- **QQ**：默认共享，也允许独立。先停旧实例，完成任务/账本检查，再启用新实例。不能用两个实例同时控制同一 QQ 身份。
- **记忆**：默认 `none`，保留正式机既有数据；`selective` 在人工预览/确认后迁移指定真记录；`full` 仅在备份 prod、展示差异与获得覆盖许可后迁移完整开发状态。
- **软件**：锁定上游/Fork SHA 由台式机发布到 ThinkBook，安装本机 Windows 原生依赖。不要以 Copy 所有目录代替版本兼容和数据策略。
- **恢复**：启动前检查 schema/索引与任务外部效果，将未知副作用转为 `needs_review`，先不开无人值守收费任务/公共写入，验收后再逐项开启。

## 验收故障场景

- 写入中 SQLite 一致性快照可打开，行数/来源能对比。
- 手动打断 50% 下载，不覆盖上一份校验通过的备份。
- 修改归档 1 字节，dry-run/restore 拒绝。
- ThinkBook 正式数据已经存在时，`none` 不能覆盖，`selective/full` 要明确报告风险。
- QQ 双开保护；已完成外部动作不重放；失败任务可审阅。
- 台式机关机时 ThinkBook 仍能本地备份；台式机开机后补拉。

源码依据：<https://github.com/whyever77/dafeiyu-astrbot>、<https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory>。其中 Linux `deploy/backup.sh` 是候选参考，不可直接宣称覆盖 Windows 全数据。
