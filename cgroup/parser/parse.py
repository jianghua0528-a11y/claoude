"""
报单解析引擎  ·  parse.py
机器人收到报单群消息 → 调 Claude API 按规则解析 → 结构化订单 + 必问清单 → 进审核队列。
依赖: ANTHROPIC_API_KEY (环境变量), anthropic SDK 2.x。
模型拒答时走服务端 fallback (fallbacks="default"); 拒答/截断/非JSON 抛 ParseError, 机器人把原文转人工。
"""
import os
import json
import re

import anthropic

from ..db.models import Mama, MamaAssistant, Artist, Venue, ReviewItem
from ..core.intake import check_payload, IntakeError

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

SYSTEM = """你是 C 组（吉隆坡夜场艺人管理）的报单解析器。把艺人在报单群发的、格式乱七八糟的报单文字，解析成结构化订单。

# 报单长什么样
报单有好几种模板（"开心🏠报单表"、"C组📝报单表 Declaration Form"、"报单给X"、"报单 小宝"等），字段大同小异：报单给/负责人（妈咪）、姓名（艺人）、日期、上班地点（场所+包厢）、上下班时间、自收/现金、挂单、门票、客人名字。

# 关键：先分清完成单 vs 上班单 vs 重复
- 同一个班，艺人通常先报"上班"（没下班时间、没金额），下班后再报一次"下班"（填了下班时间+挂单/自收金额）。
- **只把"完成单"（有下班时间 AND 有挂单或自收金额）输出为订单。**
- 同一艺人+同日期+同场所+同客人的多条报单 = 同一个班，**合并、只取最后那条完成版**（金额/场所/时间以最后为准）。
- 只报了上班、没下班/没金额的 → 放进 open_shifts（开放班次），不当订单。
- 完全空的模板（所有字段空）→ 丢弃。

# 每个完成单要抽这些字段
- artist 艺人：去掉"c组/C组"后缀
- biz_date 日期：YYYY-MM-DD（年份2026）
- venue 场所 / room 包厢
- mama 主妈咪 / assistant 助理：见下方"认人"
- mode 分成档：见下方
- customer 客人名字
- K 挂账(MYR) / M 现金(MYR) / O 门票(MYR)：见下方"金额"
- flow 现金流向：现金单才有（A/B/D/E）；不确定就留空并 warn
- start 上班 / end 下班 时间（HH:MM）

# 金额规则
- "自收"/"现金" = 现金 M；"挂单" = 挂账 K；一单既有挂单又有自收 = 混合单，两个都填。
- **超时/加时/超时费 一律算进金额**（如"3000+1200超时"→该项=4200）。
- 门票拆出来单列 O：如"3000+150门票"→K=3000,O=150；"门票:150"也填O=150；"无票"→O=0。
- 外币（USDT/U/RMB）：把原文金额放进 raw_amount 字段、币种放 currency，金额本身按工价折算的事后台处理，你只如实记录数字+币种。

# 认人（用下方"参考字典"）
- 报单给/负责人 的名字，先在字典里找：是主妈咪→填 mama；是某主妈咪的助理/别名→mama 填那个主妈咪、assistant 填这个名字。
- 报单给和负责人填了两个不同的人 → 判断谁主谁助；若两个都是主妈咪（冲突）→ 照填其一并 warn"双主妈咪冲突"。
- 名字不在字典 → mama 照填原文，warn"新妈咪/新助理 X 不在字典"。

# 分成档 mode（每单直接定，不绑妈咪状态）
- 没有妈咪（自带客/客找/G空）→ "自单"（艺90/公10）
- 有妈咪 → 默认 "标准"（艺70/妈20/公10）
- 妈咪跟艺人直结、公司不沾的单 → "直结"（艺70/妈30/公0）；报单里有明示才标，拿不准就用"标准"并 warn 让人工定
- 特批不抽（公司+妈咪都不抽，艺人100%）→ "全归艺人"

# 日期归属
- 工作日 = 中午12:00 到次日12:00 算同一天。
- 上班 AM 0–6 → 归前一天（如 00:50 上班、报单写的日期是今天，实际归前一天）。
- 上班 AM 6–12 → 可能延续夜班也可能午班，按报单写的日期，但 warn"日期归属待确认"。
- 以报单写的日期为基准，结合上班时间判断。

# 必问清单 warnings（拿不准就标，绝不瞎猜）
- 新妈咪/新场所/新助理 不在字典
- 双主妈咪冲突
- 客人名字缺失（除非报单写了"妈咪不让写客人名字"之类豁免）
- 现金单流向不明
- 日期归属不确定（上班 AM 6–12）
- 大额单（挂账或现金 > 10000）提示复核
- 金额含混合/小费/不抽等特殊情况

# 输出格式
只输出 JSON，不要任何解释、不要 markdown 代码块。结构：
{
  "orders": [
    {"artist":"","biz_date":"2026-06-16","venue":"","room":"","mama":"","assistant":"","mode":"标准","customer":"","K":0,"M":0,"O":150,"flow":null,"raw_amount":null,"currency":"MYR","start":"22:55","end":"02:02","warnings":[],"备注":""}
  ],
  "open_shifts": [ {"artist":"","venue":"","mama":"","note":"只报了上班"} ],
  "dropped": 0
}
注意：字段名严格用上面的（artist/biz_date/venue/room/mama/assistant/mode/customer/K/M/O/flow/start/end/warnings）。"""


def _dict_context(session):
    """把字典压成给 Claude 的参考文本: 主妈咪(+别名), 助理→主妈咪, 艺人(+别名), 场所(+别名, 默认门票)。"""
    def named(name, aliases):
        return f"{name}({aliases})" if aliases else name

    lines = ["主妈咪(括号内为别名, 名字用于认人):"]
    lines.append("; ".join(named(m.name, m.aliases) for m in session.query(Mama).order_by(Mama.name)))
    amap = {}
    for a in session.query(MamaAssistant).order_by(MamaAssistant.id):
        amap.setdefault(a.mama.name, []).append(a.name)
    lines.append("助理→主妈咪:")
    lines.append("; ".join(f"{','.join(v)}→{k}" for k, v in sorted(amap.items())))
    lines.append("艺人(括号内为别名):")
    lines.append(", ".join(named(a.name, a.aliases)
                           for a in session.query(Artist).filter(Artist.active.is_(True)).order_by(Artist.name)))
    lines.append("场所(括号内为别名)=默认门票:")
    lines.append("; ".join(f"{named(v.name, v.aliases)}={int(v.default_ticket or 0)}"
                           for v in session.query(Venue).order_by(Venue.name)))
    return "\n".join(lines)


def _clean_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    # 取第一个 { 到最后一个 }
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i >= 0 and j > i else text


class ParseError(RuntimeError):
    """Claude 没给出可用结果(拒答/截断/非 JSON/接口错误), 原文转人工。"""


def parse_reports(raw_text, session):
    """解析一批报单文字 → dict(orders, open_shifts, dropped)。失败抛 ParseError。"""
    client = anthropic.Anthropic()          # 读 ANTHROPIC_API_KEY
    # 固定规则放第一块并缓存; 字典会变, 放缓存点之后
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": "# 参考字典\n" + _dict_context(session)}]
    try:
        with client.beta.messages.stream(
                model=MODEL, max_tokens=32000, system=system,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                messages=[{"role": "user", "content": raw_text}]) as stream:
            resp = stream.get_final_message()
    except anthropic.APIStatusError as e:
        raise ParseError(f"Claude 接口错误 {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ParseError("连不上 Claude 接口") from e
    if resp.stop_reason == "refusal":
        raise ParseError("Claude 拒绝解析这条")
    if resp.stop_reason == "max_tokens":
        raise ParseError("报单太长, 解析被截断, 请分几条发")
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(_clean_json(text))
    except json.JSONDecodeError as e:
        raise ParseError(f"解析结果不是 JSON: {text[:200]}") from e


def to_payload(o):
    """Claude 输出的英文键订单 → 审核接口用的中文键 payload。"""
    return {
        "艺人": o.get("artist"), "场所": o.get("venue"), "包厢": o.get("room"),
        "妈咪": o.get("mama"), "助理": o.get("assistant"), "合作模式": o.get("mode"),
        "客人": o.get("customer"), "K": o.get("K", 0), "M": o.get("M", 0),
        "O": o.get("O", 0), "流向": o.get("flow"), "上班": o.get("start"),
        "下班": o.get("end"), "日期": o.get("biz_date"), "备注": o.get("备注", ""),
    }


def precheck(payload, session):
    """入库前按字典复核一遍: 名字改回正名, 认不出的追加到 warnings。返回新增 warnings 列表。"""
    try:
        art, mama, ven, mode, flow = check_payload(payload, session)
    except IntakeError as e:
        return [str(e)]
    payload["艺人"] = art.name
    if mama and payload.get("妈咪") != mama.name:
        payload["助理"] = payload.get("助理") or payload.get("妈咪")
        payload["妈咪"] = mama.name
    if ven:
        payload["场所"] = ven.name
    elif payload.get("场所"):
        return [f"场所「{payload['场所']}」不在字典"]
    return []


def ingest(raw_text, source_group, session, tg_msg_id=None, tg_sender=None):
    """解析 + 把每单写进审核队列(待审)。返回 [(review_id, payload, warnings), ...] 供机器人发按钮。"""
    data = parse_reports(raw_text, session)
    items = []
    for o in data.get("orders", []):
        payload = to_payload(o)
        warns = list(o.get("warnings") or []) + precheck(payload, session)
        warn = " / ".join(dict.fromkeys(warns)) or None
        ri = ReviewItem(
            source_group=source_group, raw_message=raw_text[:2000],
            parsed_json=json.dumps(payload, ensure_ascii=False),
            parse_warnings=warn, status="待审",
            tg_msg_id=str(tg_msg_id) if tg_msg_id else None, tg_sender=tg_sender)
        session.add(ri); session.flush()
        items.append((ri.id, payload, warn))
    session.commit()
    return items
