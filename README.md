# C组 自动报单系统

报单群消息 → 机器人抓取 → Claude 解析 → **阿豪后台审核** → 落库 + 日报发群 + 月报私发艺人。
另接财务群抓 收款/预支/支出。

---

## 现在的进度

| 模块 | 状态 |
|------|------|
| `core/settlement.py` 账务引擎 | ✅ 完成 + 验证(5 例对账, 闭环 0) |
| `db/models.py` 数据库结构 | ✅ 完成(12 张表) |
| `db/seed.py` 字典初始化 | ✅ 完成(经纪人/艺人/场所) |
| `db/session.py` 连接 | ✅ Postgres/SQLite 通用 |
| `parser/` 报单解析(Claude) | ⏳ 下一步 |
| `bot/` Telegram 机器人 | ⏳ 下一步 |
| `web/` 审核后台 | ⏳ 下一步 |
| 妈咪字典 + 历史报单导入 | ⏳ 等主文件 |

---

## 目录结构

```
cgroup_system/
├─ cgroup/
│  ├─ core/settlement.py   账务引擎(唯一计算入口)
│  ├─ db/models.py         数据库结构
│  ├─ db/session.py        连接
│  ├─ db/seed.py           字典初始化
│  ├─ parser/parse.py      报单解析(Claude)  ← 待建
│  ├─ bot/main.py          Telegram 机器人    ← 待建
│  └─ web/app.py           审核后台           ← 待建
├─ requirements.txt
├─ Procfile                Railway 进程(web + worker)
└─ .env.example            环境变量模板
```

## 路线图(按顺序建)

1. **解析核心** — Claude 把乱报单转结构化 + 必问清单校验
2. **审核后台** — 看解析结果, 改/确认/拒, 确认入库
3. **机器人接报单群** — 抓消息 → 解析 → 进审核队列
4. **报表** — 日报 PNG 发群 + 艺人月报私发
5. **财务群** — 抓 收款/预支/支出

---

## 部署到 Railway

1. 把这个项目传到一个 GitHub 私库
2. Railway 新建 Project → Deploy from GitHub repo → 选这个库
3. 项目里 **+ New → Database → PostgreSQL**(Railway 自动注入 `DATABASE_URL`)
4. Variables 里填好 `.env.example` 里那些(机器人 token、Claude key、群 id、管理员、后台密码)
5. 部署后跑一次 `python -m cgroup.db.seed` 灌字典
6. web 服务给一个网址 = 审核后台; worker 进程 = 机器人

> 准备工作: ① @BotFather 建机器人拿 token ② 机器人加进两个群并关隐私模式/设管理员
> ③ 每个艺人对机器人点一次 start(否则收不到私发月报)
