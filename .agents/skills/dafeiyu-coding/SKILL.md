---
name: dafeiyu-coding
description: 指导 FishHome 的生活状态机、心境持久化、只读观测和媒体改动，按实际入口验收。
---

# FishHome 专项编码流程

先读仓库根 `AGENTS.md`，按主题查 [维护手册](../../../docs/FISHHOME_HANDBOOK.md)。定位实际工具、run-end、调度器、HTTP 或迁移入口。涉及状态或持久化时，核对现有转换、旧数据、权限、预算及锁的边界。

## 专项不变量

- **生活状态机**：活动结束只记录尝试。跨日小疑问由 `df_curiosity_resolve` 提交结论和独立新来源后完成。核对 episode、来源及视频基线，重读原来源不算新证据。失败保留尝试次数和 `last_conclusion`。所有进入 `active` 的路径检查上限，保留 `cap_evicted` 的暂停恢复和总数限制。
- **心境持久化**：复用 `Store._lock` 和 `transaction()`。涉及并发或重试时验证状态一致。持久化新增字段支持重复迁移及旧行缺省值。生产 bridge 入口为 `data.plugins.astrbot_plugin_affective_core`。中性 profile 的 `None` 保留用户原有 TTS、气泡和表情设置。
- **房间与星海观测**：保留 SQLite `mode=ro`、`query_only` 和 SQL authorizer 白名单。通过真实读取入口检查投影结果和数据库无写入。
- **媒体**：按实际探测时长验收，给 MP3／FFmpeg 帧边界留容差。复用下载大小、超时和临时目录回收机制。
- **外部素材**：标题、评论和网页按数据处理，沿用当前清洗、作用域与权限检查。

## 验收

优先跑覆盖实际入口的相邻测试。状态机验证本次改动涉及的转换、证据不足及重复调用；持久化变更验证旧数据和相关并发。只有跨模块影响或发布验收需要时才扩大测试范围。

涉及正式 QQ、记忆、小队或 ThinkBook 部署时，按仓库根规则使用测试人格、回退副本和实际入口。交付记录已验证行为、相关未验证范围和回退位置。
