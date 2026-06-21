"""从解析/审核的 payload 建 Order —— 网页后台确认 和 TG 按钮确认 共用。"""
from datetime import date, datetime

from ..db.models import Order, Artist, Mama, Venue
from .status import derive_status


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else date.today()
    except Exception:
        return date.today()


def create_order_from_payload(d, session, source_msg_id=None):
    """d: 审核 payload(中文键)。建 Order(已审核)并加入 session, 返回该 Order。"""
    art = session.query(Artist).filter_by(name=(d.get("艺人") or "")).first()
    mama = session.query(Mama).filter_by(name=(d.get("妈咪") or "")).first()
    ven = session.query(Venue).filter_by(name=(d.get("场所") or "")).first()
    mode = d.get("合作模式") or ("标准" if mama else "自单")
    K = float(d.get("K", 0) or 0)
    M = float(d.get("M", 0) or 0)
    flow = d.get("流向")
    cs, ms = derive_status(credit_k=K, cash_m=M, mode=mode, flow=flow)
    o = Order(
        biz_date=_parse_date(d.get("日期")),
        artist_id=art.id if art else None,
        venue_id=ven.id if ven else None,
        room=d.get("包厢"), booker=d.get("助理"),
        mama_id=mama.id if mama else None,
        mode=mode, flow=flow,
        credit_k=K, cash_m=M, ticket_o=float(d.get("O", 0) or 0),
        credit_status=cs, cash_status=ms,
        customer=d.get("客人"),
        start_time=str(d.get("上班") or "")[:8],
        end_time=str(d.get("下班") or "")[:8],
        remark=d.get("备注"), status="已审核", source_msg_id=source_msg_id)
    session.add(o)
    return o
