"""
Telegram 机器人  ·  main.py
  · 报单群消息 → 调解析引擎 → 写审核队列(待审) → 回执
  · 财务流水群消息 → 写审核队列(待审, 财务) → 回执
  · /start → 让艺人/管理员跟机器人建立会话(之后才能私发月报)
需求: 机器人加进两个群且关隐私模式或设群管理员, 才读得到全部消息。
环境变量: TELEGRAM_BOT_TOKEN, REPORT_GROUP_ID, FINANCE_GROUP_ID, ADMIN_USER_IDS, ADMIN_URL
"""
import os
import logging

from telegram import Update
from telegram.ext import (Application, MessageHandler, CommandHandler,
                          filters, ContextTypes)

from ..db.session import get_session, init_db
from ..db.models import ReviewItem
from ..parser.parse import ingest

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("cgroup-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
REPORT_GROUP = os.getenv("REPORT_GROUP_ID", "")
FINANCE_GROUP = os.getenv("FINANCE_GROUP_ID", "")
ADMIN_URL = os.getenv("ADMIN_URL", "审核后台")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "C组机器人已就位。\n报单群 / 财务群的消息我会自动整理进审核后台等老板确认。\n"
        "（艺人点了这里之后才能收到私发的月报。）")


async def on_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """报单群: 解析 → 审核队列。"""
    msg = update.message
    text = (msg.text or "").strip()
    if len(text) < 10:
        return
    s = get_session()
    try:
        n = ingest(text, "报单群", s,
                   tg_msg_id=msg.message_id,
                   tg_sender=msg.from_user.full_name if msg.from_user else None)
    except Exception as e:
        log.exception("解析失败")
        await msg.reply_text("⚠️ 这条我没解析出来，已记原文待人工看。")
        s2 = get_session()
        try:
            s2.add(ReviewItem(source_group="报单群", raw_message=text[:2000],
                              parse_warnings=f"解析异常:{e}", status="待审",
                              tg_msg_id=str(msg.message_id)))
            s2.commit()
        finally:
            s2.close()
        return
    finally:
        s.close()
    if n:
        await msg.reply_text(f"✅ 收到 {n} 单，已进审核后台待确认。")
    # 没解析出订单(只报了上班/空模板) → 不回执, 不打扰


async def on_finance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """财务群: 收款/预支/支出 → 审核队列(原文, 后台再归类入账)。"""
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


def main():
    if not TOKEN:
        raise SystemExit("缺 TELEGRAM_BOT_TOKEN")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    if REPORT_GROUP:
        app.add_handler(MessageHandler(
            filters.Chat(int(REPORT_GROUP)) & filters.TEXT & ~filters.COMMAND, on_report))
    if FINANCE_GROUP:
        app.add_handler(MessageHandler(
            filters.Chat(int(FINANCE_GROUP)) & filters.TEXT & ~filters.COMMAND, on_finance))
    log.info("机器人启动: 报单群=%s 财务群=%s", REPORT_GROUP or "未设", FINANCE_GROUP or "未设")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
