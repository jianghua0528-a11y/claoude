"""
Telegram 机器人  ·  main.py
  · 报单群消息 → 解析 → 私聊管理员发「✅确认 / ❌拒」按钮 → 点确认入库
  · 财务流水群消息 → 进审核队列(原文)
  · /start → 建立会话(收私信前提)
环境变量: TELEGRAM_BOT_TOKEN, REPORT_GROUP_ID, FINANCE_GROUP_ID, ADMIN_USER_IDS, ANTHROPIC_API_KEY
"""
import os
import re
import json
import logging
from io import BytesIO
from datetime import datetime, date, timedelta, time as dtime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, MessageHandler, CommandHandler,
                          CallbackQueryHandler, filters, ContextTypes)

from ..db.session import get_session, init_db
from ..db.models import ReviewItem
from ..parser.parse import ingest
from ..core.intake import create_order_from_payload

KL = ZoneInfo("Asia/Kuala_Lumpur")

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("cgroup-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
REPORT_GROUP = os.getenv("REPORT_GROUP_ID", "")
FINANCE_GROUP = os.getenv("FINANCE_GROUP_ID", "")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]


def _review_text(payload, warn):
    p = payload
    t = (f"🆕 待审\n"
         f"{p.get('艺人','?')} · {p.get('场所','?')} {p.get('包厢') or ''}\n"
         f"妈咪 {p.get('妈咪') or '自单'} · 客 {p.get('客人') or '—'}\n"
         f"挂账 {p.get('K',0)} / 现金 {p.get('M',0)} / 门票 {p.get('O',0)}"
         f" · {p.get('合作模式') or '标准'}")
    if p.get("上班") or p.get("下班"):
        t += f"\n{p.get('上班') or '?'}→{p.get('下班') or '?'}"
    if warn:
        t += f"\n⚠️ {warn}"
    return t


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "C组机器人已就位。报单群的单我会解析后发给老板按钮审核。\n"
        "艺人：发 `/我是 你的艺名` 申请绑定，老板确认后就能 `/我的` 查业绩。",
        parse_mode="Markdown")


async def on_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """报单群: 解析 → 私聊管理员发按钮。"""
    msg = update.message
    text = (msg.text or "").strip()
    if len(text) < 10:
        return
    s = get_session()
    try:
        items = ingest(text, "报单群", s,
                       tg_msg_id=msg.message_id,
                       tg_sender=msg.from_user.full_name if msg.from_user else None,
                       msg_date=msg.date.date() if msg.date else None)
    except Exception as e:
        log.exception("解析失败")
        s2 = get_session()
        try:
            s2.add(ReviewItem(source_group="报单群", raw_message=text[:2000],
                              parse_warnings=f"解析异常:{e}", status="待审",
                              tg_msg_id=str(msg.message_id)))
            s2.commit()
        finally:
            s2.close()
        await msg.reply_text("⚠️ 这条没解析出来，已记原文待人工看。")
        return
    finally:
        s.close()

    if not items:
        return                       # 只报了上班/空模板 → 不打扰
    await msg.reply_text(f"✅ 收到 {len(items)} 单，已发审核。")
    # 私聊每个管理员, 每单一条带按钮
    for rid, payload, warn in items:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 确认", callback_data=f"ok:{rid}"),
            InlineKeyboardButton("❌ 拒", callback_data=f"no:{rid}"),
        ]])
        body = _review_text(payload, warn)
        for admin_id in ADMIN_IDS:
            try:
                await ctx.bot.send_message(chat_id=int(admin_id), text=body, reply_markup=kb)
            except Exception:
                log.warning("发管理员 %s 失败(他可能没/start过机器人)", admin_id)


async def on_review_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """管理员点 确认/拒 按钮。"""
    q = update.callback_query
    await q.answer()
    try:
        action, rid = q.data.split(":")
        rid = int(rid)
    except ValueError:
        return
    s = get_session()
    try:
        ri = s.get(ReviewItem, rid)
        if not ri or ri.status != "待审":
            await q.edit_message_text((q.message.text or "") + "\n(已处理过)")
            return
        if action == "ok":
            d = json.loads(ri.parsed_json or "{}")
            create_order_from_payload(d, s, source_msg_id=ri.tg_msg_id)
            ri.status = "已确认"
            s.commit()
            await q.edit_message_text((q.message.text or "") + "\n\n✅ 已入库")
        elif action == "no":
            ri.status = "已拒"
            s.commit()
            await q.edit_message_text((q.message.text or "") + "\n\n❌ 已拒")
    except Exception:
        log.exception("审核动作失败")
        await q.edit_message_text((q.message.text or "") + "\n\n⚠️ 处理出错，去网页后台看")
    finally:
        s.close()


async def on_finance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """财务群: 原文进审核队列。"""
    msg = update.message
    text = (msg.text or "").strip()
    if len(text) < 5:
        return
    s = get_session()
    try:
        s.add(ReviewItem(source_group="财务群", raw_message=text[:2000],
                         status="待审", tg_msg_id=str(msg.message_id),
                         tg_sender=msg.from_user.full_name if msg.from_user else None))
        s.commit()
    finally:
        s.close()
    await msg.reply_text("✅ 财务流水已进审核后台。")


# ───────────────────── 绑定 + 自助查 + 管理员查询 ─────────────────────
def _is_admin(update):
    return str(update.effective_user.id) in ADMIN_IDS


def _fmt(n):
    return f"{n:,.0f}"


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


async def on_private(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """私聊路由: 我是 / 我的 / 今日 / 艺人 / 妈咪 / 催账 (支持带不带 / )。"""
    from ..db.session import get_session
    from ..db.models import Artist, Mama, OperationLog
    from ..core.queries import artist_summary, mama_summary, day_summary
    from datetime import date

    text = (update.message.text or "").strip().lstrip("/")
    parts = text.split(maxsplit=1)
    cmd = parts[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    uid = str(update.effective_user.id)
    s = get_session()
    try:
        # 艺人申请绑定
        if cmd == "我是":
            if not arg:
                await update.message.reply_text("用法：我是 你的艺名（例：我是 桃子）"); return
            art = s.query(Artist).filter_by(name=arg).first()
            if not art:
                await update.message.reply_text(f"字典里没有艺人「{arg}」，确认艺名对不对。"); return
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ 确认绑定", callback_data=f"bind:{art.id}:{uid}"),
                InlineKeyboardButton("❌ 拒", callback_data=f"unbind:{art.id}:{uid}")]])
            who = update.effective_user.full_name
            for admin_id in ADMIN_IDS:
                try:
                    await ctx.bot.send_message(int(admin_id),
                        f"🔗 绑定申请\n「{who}」(TG:{uid}) 想绑定艺人「{arg}」", reply_markup=kb)
                except Exception:
                    pass
            await update.message.reply_text("已提交，等老板确认。")
            return

        # 艺人自助查
        if cmd in ("我的", "工资", "业绩"):
            art = s.query(Artist).filter_by(tg_user_id=uid).first()
            if not art:
                await update.message.reply_text("你还没绑定。先发：我是 你的艺名"); return
            today = date.today()
            d = artist_summary(s, art.id, today.year, today.month)
            await update.message.reply_text(
                f"🎤 {art.name} · {today.month}月\n单数 {d['n']}\n业绩 {_fmt(d['perf'])}\n"
                f"门票 {_fmt(d['tickets'])}\n应发(月底应结) {_fmt(d['wage'])} MYR")
            return

        # 以下管理员专用
        if not _is_admin(update):
            await update.message.reply_text("艺人指令：我是 X / 我的"); return

        if cmd == "今日":
            d = day_summary(s, date.today())
            if not d["n"]:
                await update.message.reply_text("今天还没单。"); return
            lines = [f"{o.customer or '—'} 挂{_fmt(o.credit_k)}" for o in d["rows"][:20]]
            await update.message.reply_text(
                f"📅 今日 {d['n']}单\n挂账{_fmt(d['K'])} 现金{_fmt(d['M'])} 门票{_fmt(d['O'])}")
            return

        if cmd == "艺人":
            art = s.query(Artist).filter_by(name=arg).first()
            if not art:
                await update.message.reply_text(f"没有艺人「{arg}」"); return
            today = date.today()
            d = artist_summary(s, art.id, today.year, today.month)
            await update.message.reply_text(
                f"🎤 {art.name} · {today.month}月\n单数{d['n']} 业绩{_fmt(d['perf'])} "
                f"门票{_fmt(d['tickets'])} 应发{_fmt(d['wage'])}")
            return

        if cmd == "妈咪":
            mama = s.query(Mama).filter_by(name=arg).first()
            if not mama:
                await update.message.reply_text(f"没有妈咪「{arg}」"); return
            d = mama_summary(s, mama.id)
            head = f"👩 {mama.name} 名下 {d['n']}单\n挂账{_fmt(d['K'])} 门票{_fmt(d['O'])} 应结C组{_fmt(d['recv'])}\n—"
            lines = [f"{(o.biz_date.strftime('%m/%d') if o.biz_date else '?')} {o.customer or '—'} "
                     f"挂{_fmt(o.credit_k)}" for o, r in d["rows"][:25]]
            await update.message.reply_text(head + "\n" + "\n".join(lines))
            return

        if cmd in ("作废", "废单"):
            oid = arg.lstrip("#").strip()
            if not oid.isdigit():
                await update.message.reply_text("用法：作废 #单号（例：作废 #128）"); return
            from ..db.models import Order
            o = s.get(Order, int(oid))
            if not o:
                await update.message.reply_text(f"没找到 #{oid}"); return
            o.status = "作废"
            s.add(OperationLog(action="作废单", target=f"order#{oid}"))
            s.commit()
            await update.message.reply_text(f"✅ #{oid} 已作废，所有报表自动排除。")
            return

        if cmd == "改":
            oid = arg.lstrip("#").strip()
            url = os.getenv("ADMIN_URL", "").rstrip("/")
            link = f"{url}/orders/{oid}/edit" if url else f"/orders/{oid}/edit"
            await update.message.reply_text(f"改 #{oid} → 开这个链接改（改完所有报表自动更新）：\n{link}")
            return

        if cmd == "催账":
            if not arg:
                await update.message.reply_text("用法：催账 妈咪名 [范围]\n例：催账 小宝  /  催账 小宝 6.15-6.20")
                return
            sub = arg.split()
            mama = s.query(Mama).filter_by(name=sub[0]).first()
            if not mama:
                await update.message.reply_text(f"没有妈咪「{sub[0]}」")
                return
            start = end = None
            plabel = "全部"
            if len(sub) > 1:
                mr = re.match(r"(\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})", sub[1])
                if mr:
                    start = date(2026, int(mr.group(1)), int(mr.group(2)))
                    end = date(2026, int(mr.group(3)), int(mr.group(4)))
                    plabel = sub[1]
            from ..reports.build import mama_statement_png
            png = mama_statement_png(s, mama.id, start, end, f"2026年 · {plabel}")
            if not png:
                await update.message.reply_text(f"{sub[0]} 这段时间没单")
                return
            await ctx.bot.send_photo(update.effective_chat.id, photo=BytesIO(png),
                                     caption=f"{sub[0]} 对账单（{plabel}）")
            return

        if cmd in ("发月报", "月报群发"):
            from ..db.models import Artist
            from ..reports.build import artist_payslip_png
            y, mo = date.today().year, date.today().month
            ar = re.match(r"(\d{4})-(\d{1,2})", arg) if arg else None
            if ar:
                y, mo = int(ar.group(1)), int(ar.group(2))
            await update.message.reply_text(f"开始群发 {y}年{mo}月 工资单…（人多会等一会）")
            sent = 0; unbound = []
            for a in s.query(Artist).filter(Artist.active.is_(True)).all():
                png, _w = artist_payslip_png(s, a.id, y, mo)
                if not png:
                    continue
                if not a.tg_user_id:
                    unbound.append(a.name)
                    continue
                try:
                    await ctx.bot.send_photo(int(a.tg_user_id), photo=BytesIO(png),
                                             caption=f"{a.name} · {y}年{mo}月 工资单")
                    sent += 1
                except Exception:
                    unbound.append(a.name + "(失败)")
            msg = f"✅ 已发 {sent} 人。"
            if unbound:
                msg += f"\n没发出（{len(unbound)}，未绑定/发送失败）：{', '.join(unbound[:25])}\n让她们私聊机器人发「我是 艺名」绑定。"
            await update.message.reply_text(msg)
            return

        # ── 利润 / 分红 ──
        if cmd in ("利润", "分红"):
            from ..core.profit import profit_summary
            y, mo = date.today().year, date.today().month
            ar = re.match(r"(\d{4})-(\d{1,2})", arg) if arg else None
            if ar:
                y, mo = int(ar.group(1)), int(ar.group(2))
            p = profit_summary(s, y, mo)
            div = "\n".join(f"  {n} {_fmt(v)}"
                            for n, v in sorted(p["dividends"].items(), key=lambda x: -x[1]))
            await update.message.reply_text(
                f"💰 {y}年{mo}月 利润\n公司毛 {_fmt(p['gross'])}\n住宿 {_fmt(p['lodging'])}\n"
                f"经纪人提成 -{_fmt(p['commission'])}\n运营成本 -{_fmt(p['costs'])}\n"
                f"经营利润 {_fmt(p['operating'])}\n汇差 {_fmt(p['spread'])}\n"
                f"总利润 {_fmt(p['total'])} RMB\n— 分红(RMB) —\n{div or '  无'}")
            return

        # ── 待结挂账列表 ──
        if cmd in ("待结", "未结"):
            from ..db.models import Order
            from ..core.billing import order_receivable
            q = s.query(Order).filter(Order.status == "已审核",
                                      Order.settle_status == "待结", Order.credit_k > 0)
            if arg:
                mama = s.query(Mama).filter_by(name=arg).first()
                if mama:
                    q = q.filter(Order.mama_id == mama.id)
            rows = q.order_by(Order.biz_date).all()[:30]
            if not rows:
                await update.message.reply_text("没有待结挂账单。")
                return
            mam = {x.id: x.name for x in s.query(Mama).all()}
            lines = [f"{o.order_id or '—'} {mam.get(o.mama_id, '')} 应收{_fmt(order_receivable(o))}"
                     for o in rows]
            await update.message.reply_text("📋 待结挂账（工单号 妈咪 应收C组）:\n" + "\n".join(lines))
            return

        # ── 结款: 结款 妈咪 金额 工单号… ──
        if cmd in ("结款", "收款"):
            sub = arg.split()
            if len(sub) < 3:
                await update.message.reply_text(
                    "用法：结款 妈咪名 金额 工单号1 工单号2…\n例：结款 小宝 5200 26052001 26052002")
                return
            from ..db.models import Payment
            from ..core.billing import apply_payment
            mama = s.query(Mama).filter_by(name=sub[0]).first()
            amount = _num(sub[1]) or 0.0
            pay = Payment(pay_date=date.today(), mama_id=mama.id if mama else None, amount=amount)
            res = apply_payment(s, pay, sub[2:])
            s.commit()
            await update.message.reply_text(
                res.flag or f"✅ 已结 {len(res.marked)} 单，收款 {_fmt(amount)}（应收 {_fmt(res.expected)}）")
            return

        # ── 财务录入: 成本 / 住宿 / 坏账 / 换汇 ──
        if cmd in ("成本", "支出"):
            sub = arg.split()
            amt = _num(sub[-1]) if len(sub) >= 2 else None
            if amt is None:
                await update.message.reply_text("用法：成本 类别 金额\n例：成本 场地 13778")
                return
            from ..db.models import Expense
            s.add(Expense(spend_date=date.today(), category=sub[0], amount=amt))
            s.commit()
            await update.message.reply_text(f"✅ 记成本 {sub[0]} {_fmt(amt)}")
            return

        if cmd == "住宿":
            sub = arg.split(maxsplit=1)
            amt = _num(sub[0]) if sub else None
            if amt is None:
                await update.message.reply_text("用法：住宿 净收入 [备注]\n例：住宿 11400 5月")
                return
            from ..db.models import Lodging
            s.add(Lodging(record_date=date.today(), net_income=amt,
                          note=(sub[1] if len(sub) > 1 else None)))
            s.commit()
            await update.message.reply_text(f"✅ 记住宿净收入 {_fmt(amt)}")
            return

        if cmd == "坏账":
            sub = arg.split()
            amt = _num(sub[0]) if sub else None
            if amt is None:
                await update.message.reply_text("用法：坏账 金额 [工单号]\n例：坏账 3000 26052001")
                return
            from ..db.models import BadDebt
            s.add(BadDebt(record_date=date.today(), amount=amt,
                          order_id=(sub[1] if len(sub) > 1 else None)))
            s.commit()
            await update.message.reply_text(f"✅ 记坏账 {_fmt(amt)}")
            return

        if cmd == "换汇":
            sub = arg.split()
            if len(sub) < 3 or _num(sub[1]) is None or _num(sub[2]) is None:
                await update.message.reply_text("用法：换汇 币种 换出额 实收RMB\n例：换汇 MYR 10000 17050")
                return
            from ..db.models import Fx
            s.add(Fx(fx_date=date.today(), out_ccy=sub[0],
                     out_amount=_num(sub[1]), in_rmb=_num(sub[2])))
            s.commit()
            await update.message.reply_text(f"✅ 记换汇 {sub[0]} {_fmt(_num(sub[1]))}→{_fmt(_num(sub[2]))}RMB")
            return

        await update.message.reply_text(
            "管理员指令：\n查询: 今日 / 艺人X / 妈咪X / 利润[年-月] / 待结[妈咪]\n"
            "结款: 结款 妈咪 金额 工单号…\n"
            "财务: 成本 类别 金额 / 住宿 金额 / 坏账 金额 / 换汇 币种 换出 实收\n"
            "报表: 催账X[范围] / 发月报[年-月]\n改单: 作废#N / 改#N\n艺人: 我是X / 我的")
    finally:
        s.close()


async def on_bind_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """管理员确认/拒 绑定。"""
    from ..db.session import get_session
    from ..db.models import Artist
    q = update.callback_query
    await q.answer()
    try:
        action, aid, uid = q.data.split(":")
    except ValueError:
        return
    s = get_session()
    try:
        art = s.get(Artist, int(aid))
        if not art:
            return
        if action == "bind":
            art.tg_user_id = uid
            s.commit()
            await q.edit_message_text((q.message.text or "") + "\n\n✅ 已绑定")
            try:
                await ctx.bot.send_message(int(uid), f"绑定成功！你已绑定艺人「{art.name}」，发「我的」查业绩。")
            except Exception:
                pass
        else:
            await q.edit_message_text((q.message.text or "") + "\n\n❌ 已拒绝绑定")
    finally:
        s.close()


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    """每天 19:00 把前一晚(工作日)已确认的单出日报发报单群。"""
    if not REPORT_GROUP:
        return
    from ..reports.build import daily_report_png
    s = get_session()
    try:
        yest = datetime.now(KL).date() - timedelta(days=1)
        png = daily_report_png(s, yest)
    finally:
        s.close()
    if png:
        await context.bot.send_photo(int(REPORT_GROUP), photo=BytesIO(png),
                                     caption=f"{yest.month}月{yest.day}日 日报")


def main():
    if not TOKEN:
        raise SystemExit("缺 TELEGRAM_BOT_TOKEN")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_review_action, pattern=r"^(ok|no):"))
    app.add_handler(CallbackQueryHandler(on_bind_action, pattern=r"^(bind|unbind):"))
    if REPORT_GROUP:
        app.add_handler(MessageHandler(
            filters.Chat(int(REPORT_GROUP)) & filters.TEXT & ~filters.COMMAND, on_report))
    if FINANCE_GROUP:
        app.add_handler(MessageHandler(
            filters.Chat(int(FINANCE_GROUP)) & filters.TEXT & ~filters.COMMAND, on_finance))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private))
    # 日报: 每天 19:00 (吉隆坡) 自动发前一晚日报
    if app.job_queue:
        app.job_queue.run_daily(daily_report_job, time=dtime(19, 0, tzinfo=KL))
    log.info("机器人启动: 报单群=%s 财务群=%s 管理员=%s 日报定时=19:00",
             REPORT_GROUP or "未设", FINANCE_GROUP or "未设", ADMIN_IDS or "未设")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
