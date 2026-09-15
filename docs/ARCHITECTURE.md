# 架构与维护说明

## 组件职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| `daemon.py` | 常驻循环、信号退出、执行频率 | 内容转换、Git |
| `poll_trilium_changes.py` | ETAPI 扫描、快照差异、生成事件 | 构建、提交 |
| `runner.py` | Git 工作区、并发锁、构建、提交推送、报告 | Trilium 内容规则 |
| `sync_trilium_posts.py` | 内容、标签、附件、生成数据、回写状态 | 容器调度 |
| `healthcheck.py` | 检查近期轮询成功 | 修复故障 |

## 持久状态

| 文件 | 是否必须备份 | 用途 |
|---|---:|---|
| `runtime/state/trilium_poll_snapshot.json` | 推荐 | 判断新变化 |
| `runtime/state/published_at_registry.json` | 必须 | 防止重新发布重置时间 |
| `runtime/reports/last_report.json` | 推荐 | 最近任务结果 |
| `ssh/id_ed25519` | 必须 | GitHub 推送 |
| `workspace/current` | 否 | 可重新 clone |

## 失败语义

- ETAPI 失败：不更新快照，下轮重试；
- 同步转换失败：不更新快照，下轮重试；
- 构建失败：不 commit、不 push；
- Git push 失败：保留失败报告，下轮在重置工作区后重新生成；
- 容器崩溃：文件锁由内核释放，不会永久锁死。

## 双实例禁令

同一个 GitHub 分支只能有一个生产 poller。雨云和飞牛同时运行可能导致：

- 两个实例同时触发；
- 同一事件重复提交；
- 一方 reset 另一方刚推送的变更；
- Trilium 状态回写竞争。

正式切换时必须先停旧再启新。

## 变更范围

自动提交白名单：

```text
src/data/trilium-posts.content.generated.ts
src/data/trilium-posts.meta.generated.ts
src/data/posts.ts
public/trilium-assets/
```

前端手工代码不在自动提交范围内。
