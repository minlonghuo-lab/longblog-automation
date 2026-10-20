# 架构与维护说明

## 组件职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| `daemon.py` | 常驻循环、信号退出、执行频率 | 内容转换、Git |
| `poll_trilium_changes.py` | ETAPI 扫描、快照差异、生成事件 | 构建、提交 |
| `runner.py` | Git 工作区、并发锁、构建、提交推送、报告 | Trilium 内容规则 |
| `sync_trilium_posts.py` | 正文、本地摘要、附件、生成数据、回写状态 | 容器调度 |
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
public/trilium-assets/
```

前端手工代码不在自动提交范围内。

## 发布策略 v3（包含 v2 的元数据精简）

- 轮询只读取笔记属性，`publish` 切换和 `sync` 从 false 到 true 产生任务；旧的标签、手填摘要、AI 刷新和置顶不再是输入。
- 一批任务只调用一次 runner，显式目标 ID 随事件传递。失败后保留事件，新任务合并入待重试批次；完整同步、构建和推送成功后才更新轮询快照并删除事件。
- `article_metadata.py` 从发布正文提取最多 80 字的摘要。迁移时仅使用仓库已发布的正文，不把用户未要求同步的草稿带上线。
- `legacy_attributes.py` 在删除旧字段前原子写入备份。只处理博客树和指定模板内的自有属性，错误会中止迁移并重试。
- 转换器不再执行 Git 或模型子进程，runner 独占构建和推送职责；转换存在失败时不写出不完整文章列表，也不回收附件。
- `slug` 在 Trilium 和生成数据中均强制等于原样 noteID。移除标题转 slug、手工别名、碰撞后缀分配；纯标识迁移不读取正文、不改文章日期，覆盖未发布草稿。
- 生成数据保留 `tags: []` 兼容前端接口，不导出 `pinned`；排序仅依据首次发布时间。

Trilium 的字段值和显示入口分别是普通标签与 `label:` 定义，因此迁移需要一并清理。参见 [Trilium 升级属性文档](https://docs.triliumnotes.org/user-guide/advanced-usage/attributes/promoted-attributes)。
