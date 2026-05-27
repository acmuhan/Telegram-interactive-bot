# Telegram interactive bot (Telegram 双向客服机器人)

Telegram 开源双向客服机器人。用户私聊机器人后，消息会被转发到后台管理群的独立话题；客服在对应话题中回复，机器人再将消息转回用户。

[English](./README.en.md) | [示例机器人](https://t.me/CustomerConnectBot) | [示例后台](https://t.me/MiHaCMSGroup)

## 功能特性

- 每个用户自动创建一个后台群话题，方便多客服协作。
- 用户消息、客服回复双向转发。
- 支持回复引用映射，尽量保持上下文。
- 支持媒体组/相册延迟聚合转发。
- 内置图片验证码，减少 userbot/垃圾消息。
- 支持消息频率限制。
- 支持关闭/重新打开话题控制会话状态。
- 支持管理员 `/broadcast` 广播。
- 支持 Docker / Docker Compose 部署。
- GitHub Actions 自动构建并发布 GHCR Docker 镜像。

## 准备工作

1. 找 [@BotFather](https://t.me/BotFather) 创建机器人并获取 `BOT_TOKEN`。
2. 创建一个 Telegram 群组。
3. 打开群组的话题/Forum 功能。
4. 将机器人加入群组并提升为管理员。
5. 确保机器人至少拥有：
   - 消息管理权限
   - 话题管理权限
6. 通过 [@GetTheirIDBot](https://t.me/GetTheirIDBot) 获取：
   - 后台群组 ID：`ADMIN_GROUP_ID`
   - 管理员用户 ID：`ADMIN_USER_IDS`

## 配置

复制示例配置：

```bash
cp .env_example .env
```

按需修改 `.env`：

```env
APP_NAME=interactive-bot
BOT_TOKEN=123456789:replace-with-your-bot-token
ADMIN_GROUP_ID=-1000000000000
ADMIN_USER_IDS=123456789,987654321
WELCOME_MESSAGE="你好，请问有什么可以帮助你的吗？"

DELETE_TOPIC_AS_FOREVER_BAN=FALSE
DELETE_USER_MESSAGE_ON_CLEAR_CMD=FALSE
DISABLE_CAPTCHA=FALSE
MESSAGE_INTERVAL=5
MEDIA_GROUP_DELAY=3
DATABASE_URL=sqlite:////app/data/db.sqlite3
PERSISTENCE_PATH=/app/data/interactive-bot.pickle
LOG_LEVEL=INFO
```

重要说明：

- `DELETE_USER_MESSAGE_ON_CLEAR_CMD` 默认建议保持 `FALSE`，避免误删用户侧聊天记录。
- Docker 部署时，数据库与持久化文件建议保存在 `/app/data`，并通过 volume 挂载出来。
- 本项目默认使用 SQLite；高并发或长期大规模运营时建议改用 PostgreSQL。

## 本地运行

```bash
git clone https://github.com/acmuhan/Telegram-interactive-bot.git
cd Telegram-interactive-bot
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
cp .env_example .env
# 编辑 .env
python -m interactive_bot
```

## Docker 运行

### 使用 GitHub 托管镜像

GitHub Actions 会发布镜像到：

```text
ghcr.io/acmuhan/telegram-interactive-bot:latest
```

运行：

```bash
cp .env_example .env
cp docker-compose.template.yml docker-compose.yml
# 编辑 .env
mkdir -p data
docker compose up -d
```

### 本地构建镜像

```bash
docker build -t telegram-interactive-bot .
docker run -d \
  --name telegram-interactive-bot \
  --restart unless-stopped \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  telegram-interactive-bot:latest
```

## GitHub Actions / GHCR

仓库包含 `.github/workflows/docker-image.yml`：

- PR：只构建，不推送镜像。
- push 到 `master`：构建并推送 `latest`、分支、SHA 标签。
- push tag `v*`：构建并推送版本标签。
- 支持 `linux/amd64` 和 `linux/arm64`。

如果 GHCR 包不可见，可在 GitHub 仓库页面进入 Packages，将容器包 visibility 调整为 Public。

## 管理命令

- `/start`：管理员私聊机器人时检查后台群配置。
- `/clear`：在后台话题内删除该话题；如果 `DELETE_USER_MESSAGE_ON_CLEAR_CMD=TRUE`，还会尝试删除用户侧消息。
- `/broadcast`：管理员在后台群回复某条消息并发送 `/broadcast`，机器人会把被回复的消息广播给已记录用户。

## 近期现代化更新

- 适配 `python-telegram-bot 22.x`。
- 适配 `SQLAlchemy 2.x` 声明式基类。
- 将 Python 包目录从 `interactive-bot` 迁移为 `interactive_bot`。
- 移除全局共享数据库 Session，改为按操作创建/关闭 session。
- 修复 SQLite 数据路径，默认写入 `data/`，便于 Docker volume 持久化。
- 清理重复依赖。
- 改善 Dockerfile：镜像内包含完整源码，不再依赖把整个源码目录挂载进容器。
- 新增 `docker-compose.yml`。
- 新增 GHCR Docker 镜像构建工作流。

## 许可证与致谢

本项目基于 Apache 协议开源。原作者：米哈 [@MrMiHa](https://t.me/MrMiHa)。

如需 fork 或二次分发，请保留原作者信息。
