# longBlog 飞牛 EVO2 部署检查表

## 部署前

- [ ] 飞牛 Docker 中 TriliumNext 正常运行
- [ ] TriliumNext 主机端口或共享 Docker 网络可达
- [ ] 已创建专用 ETAPI Token
- [ ] 已确认 longBlog 根笔记 ID
- [ ] 已复制 `.env.example` 为 `.env`
- [ ] 已执行 `./scripts/init.sh`
- [ ] GitHub Deploy Key 已添加且勾选写权限
- [ ] 雨云旧 poller 尚未关闭（仅部署测试阶段）

## 飞牛启动

- [ ] `docker compose up -d --build` 成功
- [ ] `docker compose ps` 显示 healthy
- [ ] `poller.log` 出现 `initialized snapshot`
- [ ] 容器内可以访问 Trilium ETAPI
- [ ] 容器内可以通过 SSH 访问 GitHub

## 切换生产

- [ ] 已复制 `published_at_registry.json`（推荐）
- [ ] 已复制 `trilium_poll_snapshot.json`（推荐）
- [ ] 已停止雨云 `longblog-poller.timer`
- [ ] 飞牛容器已启动
- [ ] 在一篇文章上设置 `sync=true`
- [ ] `last_report.json` 中 `failed=[]`
- [ ] `gitPushed=true`
- [ ] GitHub main 出现新提交
- [ ] 部署平台构建成功
- [ ] 线上文章和附件正常

## 回滚

- [ ] 停止飞牛自动化容器
- [ ] 仅在必要时重新启用雨云 poller
- [ ] 不允许两套 poller 同时运行

## 发布策略 v2 升级验证

- [ ] 重新构建并启动镜像
- [ ] `retired_attributes_backup.json` 留有旧字段备份
- [ ] 文章和模板不再显示标签、摘要、刷新 AI、置顶入口
- [ ] `publication_policy.json` 中 version 为 2
- [ ] 摘要自动生成且不超过 80 字；首次发布时间保持不变
- [ ] 单纯编辑正文不会发布，`sync=true` 可以发布修改
- [ ] 多篇修改只产生一次构建和推送
- [ ] 构建/推送失败后 last_event.json 保留，下轮能重试
