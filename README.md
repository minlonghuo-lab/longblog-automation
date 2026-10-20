# 飞牛 EVO2：TriliumNext → GitHub 自动发布部署包

这套部署包用于在飞牛 EVO2 上运行 longBlog 自动发布服务：你只需要在飞牛 Docker 中的 TriliumNext 里写作，使用“发布”和“同步”控制上线，自动化容器会通过 ETAPI 检测发布指令、生成博客数据、构建验证并推送到 GitHub。

## 1. 稳定主链路

```text
飞牛 TriliumNext（Docker）
  ↓ ETAPI
poll_trilium_changes.py（每 60 秒检测）
  ↓
runner.py（锁、Git 工作区、构建、提交、推送、报告）
  ↓
sync_trilium_posts.py（正文/手写摘要/附件转换）
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

- 新文章将 `publish` 设为 `true`；
- 已发布文章修改完成后，将 `sync` 设为 `true`；
- 撤下文章将 `publish` 设为 `false`。

仅编辑正文或标题不会发布，`sync` 回写为 `false` 也不会再次触发。

最多等待一个轮询周期，然后运行：

```bash
./scripts/check.sh
```

## 8. 文章发布属性

| 属性 | 作用 |
|---|---|
| `publish=true` | 发布文章 |
| `publish=false` | 从博客撤下 |
| `sync=true` | 同步正文、手写摘要和附件，完成后自动回写 false |
| `摘要` | 作者手写总结，随文章静态发布给小宠物 |
| `slug`（URL 标识） | 程序自动维护为原样 noteID，不再接受标题或自定义别名 |
| `publishedAt` | 首次发布时间，只在从未发布时创建 |
| `updatedAt` | 最近成功同步时间 |
| `syncStatus` | `publishing` / `published` / `removed` / `error` |
| `syncHash` | 内容指纹 |

### 摘要、旧字段和 URL 标识迁移

小宠物摘要只读取作者填写的中文 `摘要` 标签，完整保留文字和换行。SEO 与引用卡片优先使用手写摘要，未填写时从已发布正文提取最多 80 字的短摘录；这个备用摘录不会作为小宠物回复。不调用 AI，不需要模型密钥，不向 Trilium 回写生成的摘要。

`tags`、`summary`、`aiRefresh`、`pinned` 已退役。升级后，已有快照的实例会自动运行一次迁移：

1. 先把博客子树和 `TRILIUM_TEMPLATE_NOTE_ID` 指定模板中这四个字段的自有值、`label:` 升级属性定义和 `child:` 复制定义备份到 `data/runtime/state/retired_attributes_backup.json`；
2. 删除上述旧属性，包括截图里的四个入口。不会修改博客范围外的属性来源或其它关系；
3. 读取手写摘要，并从仓库中已发布的正文生成备用 SEO 摘录、清空导出分类标签并移除置顶数据，不读取尚未要求同步的正文修改，保留首次发布和更新时间；
4. 将现有文章（含未发布草稿）的 URL 标识 `slug` 纠正为该笔记的原样 noteID；导出的 `slug` 也等于 `id`，不读取草稿正文、不改变日期；
5. 构建验证并推送，成功后记录 `data/runtime/state/publication_policy.json`，后续只处理新发布指令。

发布策略版本为 4；旧版本实例升级后会执行一次手写摘要字段迁移，并保留 URL 标识纠正。例如 `xtusIYhPQyK5` 的 URL 标识必须为 `xtusIYhPQyK5`，文章 URL 为 `/blog/xtusIYhPQyK5/`，不随标题改变。旧中文链接的静态跳转表仍由博客保留。

`tags: []` 保留在生成数据中，以兼容博客当前的数据结构；不会再生成或处理文章分类标签；中文 `摘要` 标签单独保留。首次部署仍只建立快照，首次手动发布/同步时执行字段清理。失败任务保存在 `last_event.json` 中，构建或推送失败后自动重试；多篇文章的指令合并为一次构建和推送。

升级生产容器（在 NAS 的现有部署目录执行）：

```bash
git pull --ff-only
docker compose up -d --build
./scripts/check.sh
```

镜像重建并连接 Trilium 后，旧字段才会从编辑器中消失。请备份运行态目录，其中保留旧字段值；若使用了不同模板，需先设置正确的 `TRILIUM_TEMPLATE_NOTE_ID`。

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
public/trilium-assets/
```

不会提交 `.env`、ETAPI Token 或 SSH 私钥。

### 文章附件回收

完整同步成功并写出文章数据后，服务按最终正文中 `/trilium-assets/` 的引用回收旧附件。只处理本次输出文章目录内、符合生成文件名规则的文件；跨文章引用会统一保留，不处理品牌图片、友链图标、手动命名文件或符号链接。

同步存在失败记录、附件 URL 尚未完成本地化，或正文引用的本地文件缺失时，跳过回收。报告中的 `prunedAssets` 列出已回收路径，`assetCleanupSkipped` 说明跳过原因。回收代码构建进容器镜像，更新仓库后需重新构建并启动镜像才能生效：

```bash
git pull
docker compose up -d --build
```

本地可离线验证回收逻辑，不需要 NAS 或 Trilium 凭证：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

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

只有 `publish` 的变化和 `sync=true` 才触发发布；正文或标题编辑完成后，需要主动设置 `sync=true`。笔记删除或移出博客子树会撤下对应文章。

## 15. 安全说明

- 不需要开启 TriliumNext 后端脚本执行；
- 不需要公网暴露 webhook；
- ETAPI 可以只在 NAS/Docker 私网访问；
- 使用仓库专属 Deploy Key，而不是个人 GitHub Token；
- `.env`、私钥和运行态均不提交到 Git；
- 飞牛 Docker 目录和备份目录应限制访问权限。


## 手写「摘要」与静态小宠物（策略版本 4）

在文章笔记中添加 **文本标签 `摘要`**，值填写你自己的总结，建议 50–150 字。
标签名是中文 `摘要`，不是旧的英文 `summary`。自动化会在文章模板中补上 `label:摘要=promoted`，已有定义和手写值不会被删除或覆盖。

保存后，按原有流程勾选 `同步`，或首次勾选 `发布`。仍然采用显式发布：只改摘要或正文不会自动发布未确认的草稿。
摘要随文章一起写入静态数据的 `manualSummary`；小宠物只读取这一字段，点击后逐字展示，不请求推理服务、不下载模型。未填写时提示尚未填写摘要。作者文字不截断；减少动态效果的访客直接看到完整内容。

SEO 和文章引用卡片优先使用手写摘要，未填写时继续用公开正文的短摘录。短摘录不会作为小宠物回复。升级到策略版本 4 时，会用已发布正文缓存迁移字段，不读取未请求同步的正文草稿；原始发布日期保持不变。

升级容器需执行 `git pull --ff-only` 后重新构建并启动 `docker compose up -d --build`。不要删除 data、状态快照或文章日期登记表。建议升级后选择一篇文章填写「摘要」并勾选「同步」，检查 GitHub 生成数据、EdgeOne 部署和小宠物回复是否一致。
