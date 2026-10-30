# C组 自动报单系统

报单群消息 → 机器人抓取 → Claude 解析 → **审核后台人工确认** → 落库 + 看板。
另接财务群抓 收款/预支/支出。

## 模块状态

| 模块 | 状态 |
|------|------|
| `core/settlement.py` 账务引擎 | ✅ 5例验证·闭环0·全归艺人模式 |
| `core/names.py` 认人 | ✅ 正名/别名/助理→主妈咪/去「C组」后缀 (v2) |
| `core/intake.py` 入库校验 | ✅ 艺人不在字典/档位非法 → 报错不入库 (v2) |
| `db/models.py` 数据库(12表) | ✅ |
| `db/migrate.py` 字典+历史导入 | ✅ 351单·5月精确对平旧库 |
| `web/app.py` 审核后台 | ✅ 看板/审核队列/订单/导出/日志/导入 |
| `web/finance.py` 财务流水 | ✅ 预支·罚款/支出/妈咪结款/汇率 (v2) |
| `web/dicts.py` 字典管理 | ✅ 艺人/妈咪+助理/场所/经纪人 增改 (v2) |
| `parser/parse.py` 解析引擎 | ✅ Claude API (SDK 2.x)·规则+字典 |
| `bot/main.py` Telegram机器人 | ✅ 报单群+财务群 |
| `tests/` 自动测试 | ✅ 35 例 (v2) |

## v2 升级内容

**后台新功能**
- 审核队列：看群原文 → **改完再入库**（不用先入库再去订单页改）；已确认/已拒 可回查
- 财务群消息 → 「录成流水」：原文和表单同屏，录完自动标已处理
- **财务流水**页：预支/罚款（按扣款月份，月报自动扣）、运营支出（按类别汇总）、妈咪结款、汇率
- **字典管理**页：艺人(别名/经纪人/在职/解绑TG)、妈咪(别名/结款周期/助理)、场所(别名/门票/包厢)、经纪人
- 看板：**按月切换**；新增运营支出、公司净−支出、预支待扣；妈咪欠款「明细」「全部结清」(自动记结款流水)
- 订单页：服务器端搜索 + 月份/作废/待结筛选 + 翻页；**导出 Excel**（含引擎算出的各方应得）；作废单可恢复
- 操作日志页，所有操作记操作人（网页=登录名，TG=管理员名字）

**修复**
- 审核「确认」重复点会重复入库 → 已防
- 艺人名不在字典时确认会直接报 500（艺人ID为空）→ 现在提示原因，留在待审
- 报单里的别名/助理名/「桃子C组」认不出 → 统一走 `core/names.py`
- 群消息原文直接拼进网页（XSS）→ 全部转义
- 妈咪对账单「应结」按 挂账×0.8 硬算，直结单也被算进去 → 改用引擎应收
- 「催账 6.15-6.20」写死 2026 年 → 按当前日期推年份（支持跨年）
- 「今日」按自然日 → 按 12:00 工作日边界；新增「昨日」「待审」指令
- 作废单没清结款状态；一个 TG 号可绑多个艺人 → 已修
- 工资单：自动扣本月「待扣」预支/罚款；RMB 折算取财务流水里最新 RMB 结算价

**解析器**
- anthropic SDK 0.39 → 2.3；默认模型 `claude-sonnet-5-5`（`CLAUDE_MODEL` 可改）
- 流式请求 + 服务端拒答回退 (`fallbacks="default"`)；规则提示词做了缓存，省钱
- 拒答/截断/非 JSON → 原文进审核队列并在群里说明原因（以前是静默丢掉）
- 解析完按字典复核：名字改回正名，认不出的写进疑点

## 部署 (Railway)

1. 代码传 GitHub 私库
2. Railway → Deploy from GitHub repo
3. + New → Database → PostgreSQL (自动注入 DATABASE_URL)
4. web 服务 Variables 设引用 `DATABASE_URL=${{Postgres.DATABASE_URL}}`
5. 两个进程 = 两个服务(同一个库):
   - **web** = 审核后台 (Procfile web)
   - **worker** = 机器人 (Procfile worker)
6. 环境变量(见 .env.example): `ADMIN_USER` `ADMIN_PASSWORD` `TELEGRAM_BOT_TOKEN` `ANTHROPIC_API_KEY` `REPORT_GROUP_ID` `FINANCE_GROUP_ID` `ADMIN_USER_IDS`

## 上线后

1. 开 web 网址 → 登录(admin + 密码) → 「导入数据」上传旧主文件 → 351单+字典灌上库
2. @BotFather 建机器人拿 token；机器人加进两个群、关隐私模式或设群管理员
3. 每个艺人对机器人点一次 /start (否则收不到私发月报)
4. 报单群发报单 → 机器人解析进审核队列 → 后台确认入库

## 日常

机器人自动抓单解析 → 你在审核后台 改/确认 → 入库 → 看板看账。

## 开发 / 测试

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                                    # 用临时 SQLite, 不碰线上库; 报表图测试需 Noto CJK 字体
uvicorn cgroup.web.app:app --reload       # 本地后台, 不设 DATABASE_URL 则用 cgroup_local.db
```
