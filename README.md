# Telegram interactive bot (Telegram 双向客服机器人)

Telegram 开源双向客服机器人。用户私聊机器人后，消息会被转发到后台管理群的独立话题；客服在对应话题中回复，机器人再将消息转回用户。

[English](./README.en.md) | [示例机器人](https://t.me/CustomerConnectBot) | [示例后台](https://t.me/MiHaCMSGroup)

## 功能特性

- 每个用户自动创建一个后台群话题，方便多客服协作。
- 用户消息、客服回复双向转发。
- 支持回复引用映射，尽量保持上下文。
- 支持媒体组/相册延迟聚合转发。
- 内置多题型安全验证（数字排序 / 算术 / 指定点击），减少 userbot/垃圾消息。
- 支持消息频率限制。
- 支持关闭/重新打开话题控制会话状态。
- 支持闲置会话自动关闭（可配置小时数）。
- 支持 `/info` 查看用户档案、`/stats` 查看运营数据统计。
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

注意：`ADMIN_GROUP_ID` 必须是机器人所在后台群的真实 ID。超级群/话题群通常以 `-100` 开头；如果普通群升级为超级群或开启话题后 ID 变化，请重新获取。

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
WELCOME_MESSAGE="您好，请问有什么可以帮助您的吗？"

DELETE_TOPIC_AS_FOREVER_BAN=FALSE
DELETE_USER_MESSAGE_ON_CLEAR_CMD=FALSE
DISABLE_CAPTCHA=FALSE
MESSAGE_INTERVAL=5
CAPTCHA_COOLDOWN_SECONDS=30
MEDIA_GROUP_DELAY=3
IDLE_CLOSE_HOURS=0
ENABLE_PICKLE_PERSISTENCE=FALSE
DATABASE_URL=sqlite:////app/data/db.sqlite3
PERSISTENCE_PATH=/app/data/interactive-bot.pickle
LOG_LEVEL=INFO
```

重要说明：

- `DELETE_USER_MESSAGE_ON_CLEAR_CMD` 默认建议保持 `FALSE`，避免误删用户侧聊天记录。
- `DISABLE_CAPTCHA=FALSE` 时，用户需要先完成数字顺序安全验证。验证题通过按钮操作，不会创建后台会话；验证通过后，用户发送的第一条正式消息才会创建后台话题。
- `CAPTCHA_COOLDOWN_SECONDS` 同时作为验证码错误惩罚的基础等待时间。连续答错会按指数退避延长等待时间，最高 15 分钟。
- `ENABLE_PICKLE_PERSISTENCE` 默认关闭；pickle 文件只能来自可信 `data/` 目录，不能挂载或复制不可信文件。
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

- `/start`：管理员私聊机器人时检查后台群配置，并显示快捷管理按钮。
- `/status`：管理员在后台群查看机器人、数据库、后台群、用户/话题统计状态。
- `/stats`：管理员在后台群查看运营数据（累计用户、今日新增、今日消息量、活跃会话、封禁数等，按 UTC 统计）。
- `/info`：管理员在用户会话话题内查看该用户档案（ID、用户名、会员、首次/最近联系、累计消息、封禁状态）；也支持 `/info 用户ID`。
- `/help`：查看管理指令说明和快捷管理按钮。
- `/ban 备注`：管理员在用户会话话题内封禁当前用户，备注必填；也支持 `/ban 用户ID 备注`。
- `/ban list [数量]`：查看封禁用户列表，默认显示 20 条，最多 50 条。
- `/banlist [数量]`：查看封禁用户列表（与 `/ban list` 等价，更易在 `/` 菜单中发现）。
- `/unban`：管理员在用户会话话题内解除当前用户封禁；也支持 `/unban 用户ID`。解除后会话话题状态会恢复为 opened。
- `/clear`：在后台话题内删除该话题；如果 `DELETE_USER_MESSAGE_ON_CLEAR_CMD=TRUE`，还会尝试删除用户侧消息。
- `/broadcast`：管理员在后台群回复某条消息并发送 `/broadcast`，机器人会把被回复的消息广播给未封禁的已记录用户。

机器人启动后会自动向 Telegram 注册快捷指令：私聊只展示 `/start`，后台管理群（按 chat 作用域精准下发）展示完整管理命令，并启用输入框旁的「菜单」按钮。管理员私聊 `/start` 或在后台群使用 `/status`、`/help` 时，会看到快捷按钮：系统状态、封禁列表、管理指令说明。

## 安全验证流程

- 用户私聊 `/start` 后会看到欢迎信息和“开始安全验证”按钮。
- 验证题随机出现三种题型之一：数字排序（保证打乱、必须真正重排）、简单算术（如 `13 + 8 = ?` 点选答案）、指定点击（点击某个数字）。
- 用户可以点击“刷新验证题”更换题目。
- 验证题有效期为 120 秒，过期后会自动刷新。
- 连续答错会进入等待期，等待时间按 `CAPTCHA_COOLDOWN_SECONDS` 指数退避，最高 15 分钟。
- 验证通过前，系统不会创建后台会话话题；验证通过后，用户发送第一条正式消息时才会创建后台话题并转发给管理员。

## 常见问题

### `BadRequest: Chat not found`

如果日志出现：

```text
telegram.error.BadRequest: Chat not found
```

通常是 `ADMIN_GROUP_ID` 配错，或机器人还没有加入该后台群。请按顺序检查：

1. 把机器人加入后台管理群。
2. 将机器人提升为管理员，并授予消息管理、话题管理权限。
3. 使用 `@GetTheirIDBot` 在后台群里重新获取群 ID。
4. 确认 `.env` 中的 `ADMIN_GROUP_ID` 是完整值。超级群/话题群一般形如 `-1001234567890`，不要漏掉 `-100`。
5. 修改 `.env` 后执行：

```bash
docker compose restart
```

### `InvalidToken`

BotFather token 应只有一个冒号，格式为：

```text
数字ID:密钥
```

如果写成 `数字ID:数字ID:密钥`，Telegram 会拒绝启动。

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
