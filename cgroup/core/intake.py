"""从解析/审核的 payload 建 Order —— 网页后台确认 和 TG 按钮确认 共用。"""
from datetime import date, datetime

from ..db.models import Order
from .names import find_artist, find_mama, find_venue
from .settlement import RATIOS, FLOW_CASH_KEPT
from .status import derive_status


class IntakeError(ValueError):
    """payload 不能入库(艺人不在字典 / 档位非法 …), 消息给人看。"""


def _parse_date(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date() if s else date.today()
    except Exception:
        return date.today()


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        raise IntakeError(f"金额不是数字: {v!r}")


def check_payload(d, session):
    """校验 + 认人, 返回 (artist, mama, venue, mode, flow)。不合格抛 IntakeError。"""
    art = find_artist(session, d.get("艺人"))
    if not art:
        raise IntakeError(f"艺人「{d.get('艺人') or '空'}」不在字典, 先去字典加或改成正名")
    mama = find_mama(session, d.get("妈咪")) or find_mama(session, d.get("助理"))
    if (d.get("妈咪") or "").strip() and not mama:
        raise IntakeError(f"妈咪「{d.get('妈咪')}」不在字典(也不是任何妈咪的助理)")
    ven = find_venue(session, d.get("场所"))
    mode = d.get("合作模式") or ("标准" if mama else "自单")
    if mode not in RATIOS:
        raise IntakeError(f"未知分成档「{mode}」, 只能是 {'/'.join(RATIOS)}")
    if mode in ("标准", "直结") and not mama:
        raise IntakeError(f"分成档「{mode}」必须有妈咪")
    flow = (d.get("流向") or "").strip().upper() or None
    if flow and flow not in FLOW_CASH_KEPT:
        raise IntakeError(f"未知现金流向「{flow}」, 只能是 {'/'.join(FLOW_CASH_KEPT)}")
    return art, mama, ven, mode, flow


def create_order_from_payload(d, session, source_msg_id=None):
    """d: 审核 payload(中文键)。建 Order(已审核)并加入 session, 返回该 Order。"""
    art, mama, ven, mode, flow = check_payload(d, session)
    K, M, O = _num(d.get("K")), _num(d.get("M")), _num(d.get("O"))
    wp = _num(d.get("工价")) if d.get("工价") not in (None, "") else None
    cs, ms = derive_status(credit_k=K, cash_m=M, mode=mode, flow=flow)
    booker = d.get("助理") or (d.get("妈咪") if mama and d.get("妈咪") != mama.name else None)
    o = Order(
        biz_date=_parse_date(d.get("日期")),
        artist_id=art.id,
        venue_id=ven.id if ven else None,
        room=d.get("包厢") or None, booker=booker or None,
        mama_id=mama.id if mama else None,
        mode=mode, flow=flow,
        credit_k=K, cash_m=M, ticket_o=O, wp=wp,
        credit_status=cs, cash_status=ms,
        customer=(d.get("客人") or None) and str(d.get("客人"))[:20],
        start_time=str(d.get("上班") or "")[:8],
        end_time=str(d.get("下班") or "")[:8],
        remark=d.get("备注") or None, status="已审核",
        source_msg_id=str(source_msg_id) if source_msg_id else None)
    session.add(o)
    return o
