# 飞牛 EVO2：TriliumNext → GitHub 自动发布部署包

这套部署包用于在飞牛 EVO2 上运行 longBlog 自动发布服务：你只需要在飞牛 Docker 中的 TriliumNext 里写作和修改标签，自动化容器会通过 ETAPI 检测变化、生成博客数据、构建验证并推送到 GitHub。

## 1. 稳定主链路

```text
飞牛 TriliumNext（Docker）
  ↓ ETAPI
poll_trilium_changes.py（每 60 秒检测）
  ↓
runner.py（锁、Git 工作区、构建、提交、推送、报告）
  ↓
sync_trilium_posts.py（文章/标签/附件转换）
  ↓
GitHub longBlog:main
  ↓
EdgeOne Pages / 你的部署平台
```

本包**不依赖** Trilium 后端脚本、webhook、Nginx 或 systemd timer。跨客户端同步到飞牛 TriliumNext 的变化也能被检测到。

## 2. 目录结构

```text
longblog-fnos/
├── Dockerfile
├── compose.yaml
├── .env.example
├── service/
│   ├── daemon.py
│   ├── poll_trilium_changes.py
│   ├── runner.py
│   ├── sync_trilium_posts.py
│   └── healthcheck.py
└── scripts/
    ├── init.sh
    ├── check.sh
    └── backup.sh
```

首次运行后新增：

```text
data/
├── runtime/       # 快照、报告、日志、首次发布时间登记表
├── ssh/           # GitHub Deploy Key 和 known_hosts
└── workspace/     # 自动 clone 的 longBlog 仓库
```

## 3. 前置条件

- 飞牛已安装 Docker 和 Docker Compose；
- TriliumNext 已运行，并能从 NAS 主机端口访问，例如 `http://NAS-IP:8080`；
- TriliumNext 已创建 ETAPI Token；
- 已知 longBlog 根笔记 ID；
- GitHub 仓库允许添加具有写权限的 Deploy Key；
- GitHub 仓库当前仍为 `minlonghuo-lab/longBlog`，否则修改 `.env`。

## 4. 找到 TriliumNext 的连接地址

### 方式 A：通过 NAS 主机端口（最简单）

如果 TriliumNext Compose 中有：

```yaml
ports:
  - "8080:8080"
```

自动化容器中填写：

```env
TRILIUM_BASE_URL=http://host.docker.internal:8080
```

`compose.yaml` 已提供 Linux 所需的 `host-gateway` 映射。

### 方式 B：同一 Docker 网络（更直接）

把两个服务加入同一个外部 Docker 网络，假设 Trilium 服务名是 `trilium`：

```env
TRILIUM_BASE_URL=http://trilium:8080
```

只有在确认容器 DNS 名可达时才使用此方式。

## 5. 创建 ETAPI Token

在飞牛 TriliumNext 中进入 ETAPI / Token 设置，创建专用于 longBlog 自动化的 Token。不要复用管理员密码，不要将 Token 提交到 Git。

测试地址：

```bash
curl -H "Authorization: Bearer 你的Token" \
  http://NAS-IP:8080/etapi/app-info
```

如果版本没有 `/etapi/app-info`，可测试：

```bash
curl -H "Authorization: Bearer 你的Token" \
  http://NAS-IP:8080/etapi/notes/longBlog根笔记ID
```

## 6. 安装步骤

### 6.1 解压到持久化目录

推荐：

```text
/vol1/1000/docker/longblog-automation
```

实际路径按飞牛共享文件夹调整。

```bash
cd /你的/docker目录
unzip longblog-fnos.zip
cd longblog-fnos
```

### 6.2 创建配置

```bash
cp .env.example .env
```

必须填写：

```env
TRILIUM_BASE_URL=http://host.docker.internal:8080
TRILIUM_ETAPI_TOKEN=你的ETAPI令牌
TRILIUM_BLOG_ROOT_NOTE_ID=你的longBlog根笔记ID
LONGBLOG_REPO_URL=git@github.com:minlonghuo-lab/longBlog.git
```

建议保持：

```env
LONGBLOG_POLL_INTERVAL=60
LONGBLOG_GIT_BRANCH=main
TZ=Asia/Shanghai
```

`.env` 含密钥，权限建议：

```bash
chmod 600 .env
```

## 6.3 Bark 推送通知（可选）

Bark 用于在自动发布成功或失败时向 iPhone 推送通知。它不参与发布逻辑，通知失败不会影响构建和推送。

在飞牛上安装并打开 Bark App，复制它的推送地址，例如：

```text
https://api.day.app/你的Key
```

然后在 `.env` 中填写：

```env
LONGBLOG_BARK_BASE_URL=https://api.day.app/你的Key
LONGBLOG_BARK_ICON_URL=https://huowenlong.com/favicon.svg
LONGBLOG_BARK_GROUP=longBlog
LONGBLOG_BARK_ENABLED=true
```

行为说明：

| 场景 | 是否通知 |
|---|---|
| 有新文章或修改并成功推送 | 通知 |
| 构建失败或同步失败 | 失败通知，高优先级 |
| 轮询发现但无实际变化 | 不通知 |
| 自动化长时间未成功 | 异常告警 |
| Bark 服务不可用 | 记录日志，不影响发布 |

不想接收通知时，把 `LONGBLOG_BARK_ENABLED` 设为 `false`，或留空 `LONGBLOG_BARK_BASE_URL`。

验证通知配置：

```bash
docker compose run --rm --entrypoint python3 longblog-automation -c "
import os,sys; sys.path.insert(0,'/app/service')
from notify import send
print(send('longBlog 测试', 'Bark 配置正确'))
"
```

不要在 GitHub、截图或聊天中暴露 Bark 地址，它等同于推送凭证。

### 6.3 初始化目录和 GitHub Deploy Key

```bash
chmod +x scripts/*.sh
./scripts/init.sh
```

`init.sh` 会自动导入本包 `migration/published_at_registry.json` 中现有 15 篇文章的首次发布时间，避免迁移后重新发布导致日期改变。

脚本会生成：

```text
data/ssh/id_ed25519
-data/ssh/id_ed25519.pub
-data/ssh/known_hosts
```

将脚本打印的公钥添加到：

```text
GitHub → minlonghuo-lab/longBlog
→ Settings → Deploy keys → Add deploy key
→ 勾选 Allow write access
```

私钥只保留在飞牛：

```text
data/ssh/id_ed25519
```

### 6.4 启动

```bash
docker compose up -d --build
```

查看：

```bash
docker compose ps
docker compose logs -f --tail=100
```

## 7. 首次启动行为

第一次启动会建立快照：

```text
initialized snapshot notes=N
```

**首次初始化不会自动推送全部文章**，这是为了避免在配置错误时产生大范围仓库变更。

初始化后，在 Trilium 中任选文章：

- 修改正文；或
- 将 `sync` 改为 `true`；或
- 修改 `publish` / `pinned`。

最多等待一个轮询周期，然后运行：

```bash
./scripts/check.sh
```

## 8. 支持的文章标签

| 标签 | 作用 |
|---|---|
| `publish=true` | 发布文章 |
| `publish=false` | 从博客撤下 |
| `sync=true` | 强制同步正文和附件，完成后自动回写 false |
| `pinned=true/false` | 置顶/取消置顶，不改变首次发布时间 |
| `aiRefresh=true` | 强制刷新 AI 摘要/标签（需要配置模型密钥） |
| `publishedAt` | 首次发布时间，只在从未发布时创建 |
| `updatedAt` | 最近成功同步时间 |
| `syncStatus` | `publishing` / `published` / `removed` / `error` |
| `syncHash` | 内容指纹 |

## 9. 首次发布时间保护

发布时间优先级：

```text
Trilium publishedAt
→ data/runtime/state/published_at_registry.json
→ Git 仓库已有 meta
→ 均不存在时才使用首次发布时刻
```

取消发布再重新发布不会重置 `publishedAt`。必须备份：

```text
data/runtime/state/published_at_registry.json
```

## 10. 构建和 Git 安全策略

流程顺序为：

```text
生成数据
→ npm ci（依赖变化时）
→ npm run build
→ git commit
→ git push
```

构建失败时不会推送损坏版本。容器使用文件锁防并发，崩溃后不会因残留目录锁永久卡住。

自动提交仅包含：

```text
src/data/trilium-posts.content.generated.ts
src/data/trilium-posts.meta.generated.ts
src/data/posts.ts
public/trilium-assets/
```

不会提交 `.env`、ETAPI Token 或 SSH 私钥。

## 11. 巡检和日志

一键巡检：

```bash
./scripts/check.sh
```

日志：

```text
data/runtime/logs/daemon.log
data/runtime/logs/poller.log
data/runtime/logs/runner.log
data/runtime/logs/build.log
```

最近报告：

```text
data/runtime/reports/last_report.json
```

常见成功链路：

```text
poller.log: changes=1 event=sync_requested noteId=...
runner.log: success ... pushed=True
last_report.json: failed=[] / gitPushed=true / buildRan=true
```

## 12. 备份和恢复

备份：

```bash
./scripts/backup.sh /你的/备份目录
```

至少备份：

```text
.env
data/runtime/
data/ssh/
compose.yaml
Dockerfile
service/
```

`data/workspace/current` 可从 GitHub 重新 clone；`node_modules` 和 `dist` 无需备份。

恢复时：

```bash
docker compose down
# 解压备份覆盖当前目录
docker compose up -d --build
```

## 13. 从雨云切换到飞牛

切换前不要让两台自动化服务同时工作，否则两边可能同时操作 GitHub。

推荐顺序：

1. 在飞牛部署并完成 ETAPI 连接测试；
2. 从雨云复制发布时间登记表与快照（可选但推荐）：
   ```text
   runtime/state/published_at_registry.json
   runtime/state/trilium_poll_snapshot.json
   ```
3. 在雨云停止 `longblog-poller.timer`；
4. 启动飞牛自动化容器；
5. 修改一篇测试文章并设置 `sync=true`；
6. 确认 GitHub 和线上部署成功；
7. 保留雨云备份，但不要重新启用旧 poller。

如果不复制旧快照，飞牛第一次只建立新快照，不会立即推送，这是安全行为。

## 14. 常见问题

### `Connection refused` / ETAPI 不通

检查 `TRILIUM_BASE_URL`、Trilium 主机端口、Docker 网络。进入容器测试：

```bash
docker compose exec longblog-automation sh
curl -I http://host.docker.internal:8080
```

### `Permission denied (publickey)`

Deploy Key 未添加、未勾选写权限，或 `data/ssh` 文件权限错误：

```bash
chmod 700 data/ssh
chmod 600 data/ssh/id_ed25519 data/ssh/known_hosts
```

### `Host key verification failed`

重新生成：

```bash
ssh-keyscan -t ed25519 github.com > data/ssh/known_hosts
```

### `build failed`

查看：

```bash
tail -100 data/runtime/logs/build.log
tail -100 data/runtime/logs/runner.log
```

构建失败不会 push。

### 变更没有触发

先确认 TriliumNext 服务端已经同步到修改，再看：

```bash
tail -100 data/runtime/logs/poller.log
```

正文变化、标题、`publish`、`sync`、`pinned`、`aiRefresh` 都会检测。

## 15. 安全说明

- 不需要开启 TriliumNext 后端脚本执行；
- 不需要公网暴露 webhook；
- ETAPI 可以只在 NAS/Docker 私网访问；
- 使用仓库专属 Deploy Key，而不是个人 GitHub Token；
- `.env`、私钥和运行态均不提交到 Git；
- 飞牛 Docker 目录和备份目录应限制访问权限。
