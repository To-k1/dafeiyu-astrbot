# FishHome 项目规则

FishHome 在 Windows 双机上运行大肥鱼的 QQ、生活、小队和控制台。以用户本轮要求、当前源码和实际验收为准。

## 按任务查文档

- **需求**见 [PRD v0.9](docs/PRD_FISH_HOME_v0.9.md)，保留 FR-01～FR-15 和 FR-07A～E 编号。
- **架构、服务、计费和验收**按主题读[维护手册](docs/FISHHOME_HANDBOOK.md)。代码、配置或部署变更时同步更新相关条目，注明验证结果和回退位置。纯内部改动记一条变更记录。
- **控制台和人格操作**见[使用说明](docs/DESKTOP_MANAGER.md)。**发布、上游同步与回退**见[代码同步](docs/CODE_SYNC.md)。**备份、导出和迁移**见[数据流程](docs/BACKUP_AND_MIGRATION.md)。

## 双机与验收

- 台式机的 `D:\Share\code\FishHome\repo` 是开发工作树。其兄弟目录 `backups/`、`exports/`、`secrets/` 属于本机资料，取用需授权，排除在 Git 发布之外。
- ThinkBook（SSH 别名 `fishbook`）是正式运行端。活跃 SQLite 以 ThinkBook 为准，禁止双向同步或直接覆盖。独立的设置同步、原生快照和可读导出各走自己的流程。VMware 用于备用 Linux 测试，正式端走 Windows 原生部署。
- 同一个 QQ 只运行一个可主动执行的实例。真实 QQ、记忆或小队系统验收先切 `fish-home-` 测试人格，结束后恢复 `feiyu` 正式组。同版本使用相同人格正文与工具配置。只读核查和离线测试可直接做。星海按人格分组，房间读主人私聊的共享生活记录。

## 开发与运维

- 写新函数或规则前先搜现有实现。优先配置或薄适配，复用原作者的 LifePlanner、心境动力学、肥鱼自己的 LivingMemory、房间和星海。
- 肥鱼使用独立受限的 Windows 账户和工作区。管理员、GitHub、SSH 等凭据留在运维端。管理入口走核验主机指纹的 SSH 回环转发，DSH 无鉴权 RPC 仅监听回环。
- 公共平台写入默认关闭，开启后仍按平台授权和执行层检查。外部内容无权改变许可。带登录态的自主浏览使用已验证的只读路径。收费调用前按实际模型路由核对额度。
- 开发与公开发布使用独立工作树。保留本地未提交改动，禁止强推、`reset --hard`、删除仓库或分支。生产更新、覆盖数据、真实账号扫码、使用实名 API Key 或公开发布按用户的明确授权执行。
- 正式更新执行前留回退副本，只重启受影响的组件。按真实入口验收并记录已验证、未验证的范围，组件在线仅代表连接状态。
- 提交若被大小钩子拦下，按报错把暂存的文件按职责拆开，再重新暂存和提交。

维护原作者模块时参考[上游规则](https://github.com/whyever77/dafeiyu-astrbot/blob/main/AGENTS.md)和[仓库编码流程](.agents/skills/dafeiyu-coding/SKILL.md)。
