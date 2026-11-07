# Windows 双机版验收清单 v0.7

> 用例状态区分 `源码确认 / mock / 台式机本地 / 真实 QQ&API / ThinkBook 长期运行`。任何一种不能冒充另一种。M0 不得越权进行真实上线测试。

## A. Windows 环境与 Git

- [ ] Windows 台式机只有 `FISH_HOME` 项目路径变量，`repo/` Git 根；`backups`/`exports` 在 repo 外且不会被推到公开 Fork。
- [ ] Codex 检查/创建公开 Fork，`origin/upstream` 正确，`main/fish-home/feature/*` 可维护；保护原有仓库文件，不使用 destructive reset/force push。
- [ ] Windows 原生 AstrBot/NapCat、插件和 DSH 分别记录具体版本/build/日志，没被 WSL2/VMware 强行绑定。
- [ ] ThinkBook 专用 Windows 标准用户实测不能访问 Codex GitHub/SSH 与管理员资料。

## B. 聊天、安卓与生活

- [ ] Dev QQ 连接成功；同一 QQ 时阻止同时启动第二个 prod 自治实例；独立 QQ 模式也能配置。
- [ ] 上游 LivingMemory、Goals、LifePlanner、Workshop、项目、来源可用；不会并行产生第二套生活目标真相。
- [ ] Windows 安卓模拟器里实际安装/登录 B站，推荐页能刷新、滑动与播放；ADB/PhoneMCP 可截图、读取元素、恢复连接。先人工在场联调，验证登录态写入受限后开放自主浏览。已知部分 QQ/模拟器服务属于 GUI 登录会话，Windows 更新后用户重新登录可以恢复。
- [ ] 不把“打开网页/手机”和“模型真实看过视频”混同；活动与来源日志能核查。

## C. 原作者小房间与星海

- [ ] 台式机本地启动原版 `/room`、星海 `/`，不重做 UI；演示/真实数据有明显标记。
- [ ] 只读读取 dev 数据成功；ThinkBook 正式环境脱敏快照能接入，断网时显示最后同步时间，房间动作与真实事件相互区分。管理员另行拉取原生数据和经授权的私聊原文。
- [ ] 房间服务器默认只绑定 loopback，不把私密记忆服务开放给 LAN。

## D. DSH、峰谷与费用

- [ ] DSH Bridge/官方 Windows 版本能真实执行最小文件任务，产物文件存在，长任务状态可恢复，未知外部副作用为 `needs_review`。
- [ ] 用量按模型、渠道、任务、token 类别、缓存/工具费用汇总；自主和交互分别默认 ¥5 CNY。用假时钟验证 03:59/04:00 分账、临时额度恢复、永久调额保留已用金额、跨日续跑归属及单账本耗尽的请求前拦截。
- [ ] FakeClock 普通工作日 10:00 非紧急大额任务可延期、低成本/有时限任务可执行；低谷复评不是强迫开工。周末/节假日用可更新计价规则。
- [ ] 没有真实逐请求拦截时明示软限额，未验证前不启用无人值守收费任务；不会自动切 Pro。

## E. 公共平台写入与权限

- [ ] 默认总开关关，平台写入不可执行；总开但平台关也拒绝；总开+对应平台开且审批/额度内才可以进行经用户批准的小范围测试。
- [ ] QQ 面向用户正常聊天和 Codex GitHub push 不受公共写总开关影响；安全审计涵盖 DSH arbitrary Shell / HTTP 绕行风险，不误报“严格网络硬隔离”。
- [ ] 仿造恶意网页的提示注入无法修改授权策略或读取部署密钥。
- [ ] DSH 无鉴权 Web RPC 不监听可由普通 LAN 客户直连的地址。

## F. 数据/备份/迁移

- [ ] ThinkBook prod live 数据唯一；台式机只有开发测试数据库与已校验备份、可读导出、房间只读缓存。
- [ ] SQLite 写入时生成一致性备份、`*.partial` 中断不覆盖最后成功文件、故意损坏 hash 被拒绝，隔离恢复保留来源/项目/已完成状态。
- [ ] ThinkBook 台式机关机时可本地生成归档，台式机上线后 SSH 补拉；导出 JSONL+Markdown 能人读且不冒充完整恢复。
- [ ] 迁移 QQ `shared/separate`、记忆 `none/selective/full` 测试通过，`none` 不擦 prod、`full` 强制保护性备份/差异预览。
- [ ] ThinkBook 登录 Windows 后自动恢复 GUI 相关应用；用户可停止、监控、重新启动，不要求未登录自启 Android。
- [ ] 普通备份和公开 Fork 均不含密钥、QQ/Android 登录态、个人私密原文；明文归档风险已透明标注。

## 单条验收证据模板

| ID | 级别 | 环境/版本 | 命令或操作 | 预期 | 真实输出/截图/日志 | 结论 |
|---|---|---|---|---|---|---|
| TODO | mock / Windows dev / online / ThinkBook | TODO | TODO | TODO | TODO | 待测 |
