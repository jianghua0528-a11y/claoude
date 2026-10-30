# Telegram 服装店

顾客在 Telegram 机器人里 **逛店 → 选颜色/尺码 → 加购物车 → 填地址下单 → 转账发截图**，
店家在手机上点按钮确认收款，在网页后台管理商品、库存和发货。

和 C组报单系统完全独立（单独的 `shop/` 包、单独的机器人、表名都带 `shop_` 前缀），可以共用同一个 Postgres。

## 功能

**顾客（机器人）**
- 底部菜单：🛍 逛店 / 🛒 购物车 / 📦 我的订单 / 💬 联系客服
- 分类 → 商品列表（分页）→ 商品卡片（图片、价格、介绍、现货尺码）
- 先选颜色再选尺码，售罄的尺码显示「售罄」，加购不能超过库存
- 购物车 ➖➕🗑，显示运费和「再买多少包邮」
- 结算：姓名 → 手机 → 地址 → 备注 → 确认；下次可一键沿用上次地址
- 下单即**锁库存**，显示收款信息（文字或收款码图片）
- 直接发**付款截图**，自动挂到待付款的订单上
- 查看订单状态、取消未付款订单（库存退回）
- 收款确认 / 发货（附快递单号）会自动收到通知

**店家**
- 新订单、付款截图私聊推送，截图下方有「✅ 确认收款 / ❌ 驳回」按钮
- `/admin` 查看待处理数量和低库存
- 超时未付款的订单自动取消、退库存（默认 24 小时）

**网页后台**（`/`，HTTP Basic 登录，密码 = `SHOP_ADMIN_PASSWORD`）
- 看板：待确认收款 / 待发货 / 待付款、本月成交单数和营业额、低库存提醒
- 订单：按状态筛选、搜索（单号/姓名/电话/快递单号），查看付款截图，确认收款、驳回、发货填单号、完成、取消，店内备注
- 商品：新增/编辑/上下架/删除，上传主图，**颜色 × 尺码批量生成规格**，逐个改库存、单独定价
- 分类：增删改、排序

订单状态：`待付款 → 待确认(已传截图) → 已付款 → 已发货 → 已完成`，发货前都可取消（库存自动退回）。

## 本地试跑

```bash
pip install -r requirements.txt
```

```bash
export SHOP_BOT_TOKEN=你的token SHOP_ADMIN_IDS=你的TGid SHOP_ADMIN_PASSWORD=123456
```

```bash
uvicorn shop.web:app --reload
```

另开一个终端：

```bash
python -m shop.bot
```

打开 http://127.0.0.1:8000 → 先建分类 → 新增商品（上传图片）→ 批量生成颜色/尺码 → 去 Telegram 给机器人发 `/start`。

## 部署到 Railway

1. @BotFather → `/newbot` 新建**服装店专用**机器人，拿 token
2. 同一个 Railway 项目里新建两个服务（都从这个 GitHub 仓库部署），Custom Start Command 分别填：
   - **shop-web**：`uvicorn shop.web:app --host 0.0.0.0 --port $PORT`（开 Public Networking 拿网址）
   - **shop-bot**：`python -m shop.bot`
3. 两个服务都设环境变量（见 `shop/.env.example`）；数据库引用 `DATABASE_URL=${{Postgres.DATABASE_URL}}`
4. 店家先给机器人发一次 `/start`（否则机器人没法私聊推送）；发 `/admin` 能看到自己的 Telegram ID

商品图片存在数据库里（Railway 磁盘重启会清空），第一次发给顾客后会缓存 Telegram 的 file_id，之后秒发。

## 测试

```bash
pytest tests/test_shop.py
```
