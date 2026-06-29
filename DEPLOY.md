# 部署手册 (Railway)

> 代码层面已就绪：`uvicorn cgroup.web.app:app` 生产启动通过，`/health` 正常，145 测试全绿。
> 部署 = 把代码推上 GitHub → Railway 从仓库部署 → 配两个服务 + Postgres + 环境变量。

## 0. 前置：代码上 GitHub

Railway 从 GitHub 仓库部署，所以**先把分支推上去**：

```bash
git push -u origin claude/optimistic-davinci-fph3s8
# 若本会话无写权限报 403, 在本地仓库用 bundle 推, 或重连 GitHub 授权后再推
```

## 1. Railway 建项目

1. Railway → **New Project → Deploy from GitHub repo** → 选本仓库/分支。
2. **+ New → Database → PostgreSQL**（自动生成 `DATABASE_URL`）。

## 2. 两个服务（同一个库、同一份代码）

| 服务 | 启动命令 | 说明 |
|---|---|---|
| **web** | `uvicorn cgroup.web.app:app --host 0.0.0.0 --port $PORT` | 审核后台（Procfile `web` / Dockerfile 默认 CMD） |
| **worker** | `python -m cgroup.bot.main` | Telegram 机器人（Procfile `worker` / 自定义启动命令覆盖） |

- 用 Nixpacks：识别 `Procfile`，建两个服务分别选 web / worker。
- 用 Dockerfile：web 用默认 CMD；worker 服务把 Start Command 覆盖为 `python -m cgroup.bot.main`。

## 3. 环境变量（两个服务都设；见 `.env.example`）

| 变量 | 必填 | 来源 / 说明 |
|---|---|---|
| `DATABASE_URL` | ✅ | 引用 `${{Postgres.DATABASE_URL}}` |
| `ADMIN_PASSWORD` | ✅ | 审核后台登录密码（web 必需） |
| `ANTHROPIC_API_KEY` | ✅ | console.anthropic.com（解析用，worker 必需） |
| `TELEGRAM_BOT_TOKEN` | ✅ | @BotFather（worker 必需） |
| `REPORT_GROUP_ID` | ✅ | 报单群 chat_id |
| `FINANCE_GROUP_ID` | ✅ | 财务群 chat_id |
| `ADMIN_USER_IDS` | ✅ | 管理员 Telegram user_id，逗号分隔 |
| `ADMIN_URL` | 选 | 后台网址（机器人回执里提示用） |
| `CLAUDE_MODEL` | 选 | 默认 `claude-sonnet-4-6` |
| `TZ` | 选 | `Asia/Kuala_Lumpur` |

## 4. 上线后

1. 开 web 网址 → 登录（admin + 密码）→ 验证 `/health` 返回 `{"ok":true}`。
2. **灌字典**：`/upload` 上传旧主文件，或 Railway shell 跑 `python -m cgroup.db.seed`。
3. @BotFather 建机器人拿 token；把机器人**加进报单群+财务群**，关隐私模式或设群管理员。
4. 每个艺人对机器人点一次 `/start`（否则收不到私发月报）。
5. 报单群发报单 → 机器人解析进审核队列 → 后台 `/review` 确认入库 → `/`/`/profit`/`/billing` 看账。

## 注意

- **首次部署用全新 Postgres**：启动时 `init_db()`(create_all) 自动建全部表。
- **若覆盖旧库**：`create_all` 只建缺失表、**不改已有表结构**。本版 schema 较旧版有变动（`orders.preset`/`order_id`/`cust_*`、新增 `payments`/`fx_flows`/`lodging`/`bad_debts`/`master_data`）→ 旧库需手工迁移或重建，否则运行报缺列。
- 健康检查路径用 `/health`。
