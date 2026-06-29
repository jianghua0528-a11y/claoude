# CLAUDE.md — C组自动报单系统 开发指南

> 本文件给 Claude Code / 开发者快速理解本仓库。**业务规则的唯一真相源是《C组运营系统宪法 v1.0》**（外部文档）；本文件是「宪法 → 代码」的导航与约定速查。遇宪法未覆盖的情况，先问业务方（阿豪），不自行假设。

## 是什么

吉隆坡夜场艺人经纪公司的报单/账务系统：报单群消息 → 机器人抓取 → Claude 解析 → **审核后台人工确认** → 落库 → 看板/利润/结款。另接财务群录收款/成本/换汇。部署在 Railway（web + worker 两进程，PostgreSQL；本地无 DATABASE_URL 时回退 SQLite）。

## 运行 / 测试

```bash
pip install -r requirements.txt
python -m pytest tests/ -q        # 全套回归（约 145 例，全绿）
python -m cgroup.db.seed          # 灌字典（经纪人/艺人/场所）
uvicorn cgroup.web.app:app        # 审核后台 (Basic Auth: admin / $ADMIN_PASSWORD)
```

测试用 `tests/conftest.py` 指定的临时 SQLite 共享库（每轮重建）；DB 集成测试请用**独立日期/唯一名字**避免串扰。环境变量见 `.env.example`。

## 宪法 Block → 代码映射（计算层，全部纯函数 + 回归测试）

| Block | 模块 | 职责 |
|---|---|---|
| A 分成 · B 流向 · C 门票 | `cgroup/core/settle.py` | 唯一结算入口 `settle()`；5 预设 + 流向 A/B/D/E；公司净恒 `c*wp`；闭环 `verify_closure` |
| D 日期归属 | `cgroup/core/workdate.py` | `attribute_date()` 12点边界/夜班/灰区 flag |
| E 汇差 | `cgroup/core/fx.py` | `Σ(实收RMB − 换出×结算率)`，否决基准法 |
| F 挂账识别 | `cgroup/core/pricing.py` | 工时反推底价 + 档位标价 + `reconcile_credit` |
| G 结款 | `cgroup/core/billing.py` | `order_id=YYMMDDNN` 主键 + `apply_payment` 单一真相源 |
| I 利润分红 | `cgroup/core/profit.py` | 利润链 + 三股东业绩占比分红 + `profit_summary` |
| J 同名消歧 | `cgroup/core/directory.py` | `resolve`/`resolve_mama` 弹选/错名不收/助理带出主妈咪 |

接线层：`cgroup/core/enrich.py`（录单时跑 D/F/J 确定性校验，并入审核 warnings）；`cgroup/core/intake.py`（payload→Order，建单即分配 order_id）；`cgroup/core/queries.py`（看板汇总，经引擎）。

其余：`parser/parse.py`（LLM 解析→审核队列）、`web/app.py`（看板/利润/结款/财务/审核/改单）、`bot/main.py`（Telegram）、`reports/`（PNG 报表）、`db/models.py`（schema）。

## 关键口径约定（改代码前必读）

- **金额**：`K`(挂账工价) / `M`(现金工价) **均不含门票**；`O`(门票) 永远单列、100% 归艺人；`wp`(标准工价) 不给则默认 `K+M`。现场"含门票"的现金由引擎按流向显式加 `O`，**杜绝门票双算**（5 月血泪坑）。
- **分成**：每单存 `preset`（标准/无水单/代收无水/自单/自定义）；`on_company_books` 由 preset 唯一决定；自定义档用 `cust_a/cust_m/cust_c`。**无水单=表外**，不进收款/工资/闭环。
- **业绩/分红**：只有公司有份的单（c>0 且 on_books）算业绩；分红仅三股东（阿星/阿豪/老方），按业绩占比全额分。
- **公司净恒 = `c*wp`**（Block B 铁律，引擎内断言守护）。
- **结款单一真相源**：按 `order_id` 关联（`payments.covers`），**不用行号交叉引用**；每单 `settle_status` 待结/已结。
- **混合单**：宪法未定义实操口径 → `settle()` 标 `needs_review`，**不自行假设**。

## 已知缺口 / 刻意延后

- 字典 5 张分表（Broker/Artist/Mama/MamaAssistant/Venue）→ `master_data` 统一表**未合并**；`directory.entries_from_legacy` 已桥接，认人可用。
- `settle.Settlement` 保留兼容别名（`artist_month_end`/`mama_receivable`/`mama_rebate`/`is_direct_settle`）供 queries/reports/web 使用，未退役。
- 住宿净收入「按重叠天数算」未实现，`Lodging.net_income` 为录入值。

## 约定

- 提交信息用中文、描述清楚；不提交 `__pycache__`/`*.db`（见 `.gitignore`）。
- 新增计算逻辑一律配回归测试，并尽量对齐宪法的 worked example。
- 改结算/口径相关代码后跑 `verify_closure` 与 §13 golden（`tests/test_profit.py`）确认不破口径。
