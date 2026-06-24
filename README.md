# C组 自动报单系统

报单群消息 → 机器人抓取 → Claude 解析 → **审核后台人工确认** → 落库 + 看板。
另接财务群抓 收款/预支/支出。

## 模块状态 (全部完成)

| 模块 | 状态 |
|------|------|
| `core/settle.py` 账务引擎 | ✅ 宪法v1.0·5预设×四流向·38测试·闭环0 |
| `db/models.py` 数据库(12表) | ✅ |
| `db/migrate.py` 字典+历史导入 | ✅ 351单·5月精确对平旧库 |
| `web/app.py` 审核后台 | ✅ 看板/导入/审核队列/登录 |
| `parser/parse.py` 解析引擎 | ✅ Claude API·规则+字典 |
| `bot/main.py` Telegram机器人 | ✅ 报单群+财务群 |

## 部署 (Railway)

1. 代码传 GitHub 私库
2. Railway → Deploy from GitHub repo
3. + New → Database → PostgreSQL (自动注入 DATABASE_URL)
4. web 服务 Variables 设引用 `DATABASE_URL=${{Postgres.DATABASE_URL}}`
5. 两个进程 = 两个服务(同一个库):
   - **web** = 审核后台 (Procfile web)
   - **worker** = 机器人 (Procfile worker)
6. 环境变量(见 .env.example): `ADMIN_PASSWORD` `TELEGRAM_BOT_TOKEN` `ANTHROPIC_API_KEY` `REPORT_GROUP_ID` `FINANCE_GROUP_ID` `ADMIN_USER_IDS`

## 上线后

1. 开 web 网址 → 登录(admin + 密码) → 「导入数据」上传旧主文件 → 351单+字典灌上库
2. @BotFather 建机器人拿 token；机器人加进两个群、关隐私模式或设群管理员
3. 每个艺人对机器人点一次 /start (否则收不到私发月报)
4. 报单群发报单 → 机器人解析进审核队列 → 后台确认入库

## 日常

机器人自动抓单解析 → 你在审核后台 改/确认 → 入库 → 看板看账。
