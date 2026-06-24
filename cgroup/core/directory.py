"""
C组 字典认人 / 同名消歧 (宪法 v1.0 · Block J)  ·  directory.py
一张 master_data 表; 同名各占一行, 靠 disambig 标签区分; 录单遇歧义名弹选。

根治 5 月血泪: 雪儿×2 / 安心×2 / 娜娜≠NANA / coco 是助理 / 星澄是错名(不收)。

规则:
  · 主名精确命中优先; 不中再按别名(错名不在别名里 → 自然不收)。
  · 同名多行 → ambiguous, 返回候选(带 disambig)让录单弹选。
  · 停用(status=停用)的行不参与认人。
  · 认人(报单给/负责人): 助理 → 带出其所属主妈咪。
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Entry:
    name: str
    type: str                       # 艺人/主妈咪/助理/经纪人/场所
    aliases: tuple = ()
    disambig: Optional[str] = None
    linked_to: Optional[str] = None  # 助理→主妈咪名; 艺人→经纪人名
    status: str = "在用"


@dataclass
class Resolution:
    status: str                      # matched / ambiguous / not_found
    match: Optional[Entry] = None
    candidates: List[Entry] = field(default_factory=list)


def _active(entries):
    return [e for e in entries if (e.status or "在用") == "在用"]


def resolve(entries, name, type: Optional[str] = None) -> Resolution:
    """认人。同名多行返回 ambiguous + 候选; 错名/新名返回 not_found。"""
    name = (name or "").strip()
    if not name:
        return Resolution("not_found")
    pool = _active(entries)
    if type:
        pool = [e for e in pool if e.type == type]
    hits = [e for e in pool if e.name == name]
    if not hits:                                  # 主名不中 → 试别名(错名不在别名内)
        hits = [e for e in pool if name in (e.aliases or ())]
    if len(hits) == 1:
        return Resolution("matched", match=hits[0])
    if len(hits) > 1:
        return Resolution("ambiguous", candidates=hits)
    return Resolution("not_found")


@dataclass
class MamaResolution:
    mama: Optional[str] = None
    assistant: Optional[str] = None
    candidates: List[str] = field(default_factory=list)
    flag: Optional[str] = None


def resolve_mama(entries, name) -> MamaResolution:
    """认「报单给/负责人」→ 主妈咪(+助理)。歧义弹选, 不在字典/错名 flag。"""
    r = resolve(entries, name)
    if r.status == "matched":
        e = r.match
        if e.type == "主妈咪":
            return MamaResolution(mama=e.name)
        if e.type == "助理":
            return MamaResolution(mama=e.linked_to, assistant=e.name)
        return MamaResolution(flag=f"'{name}' 是{e.type}, 非妈咪/助理, 请核")
    if r.status == "ambiguous":
        return MamaResolution(
            candidates=[c.disambig or c.name for c in r.candidates],
            flag=f"同名'{name}'需弹选")
    return MamaResolution(flag=f"'{name}' 不在字典(新妈咪/助理 或 错名), 请核")


def load_entries(session) -> List[Entry]:
    """从 master_data 表载入 Entry 列表 (linked_to 解析成名字)。"""
    from ..db.models import MasterData
    rows = session.query(MasterData).all()
    name_by_id = {m.id: m.name for m in rows}
    out = []
    for m in rows:
        aliases = tuple(x for x in (m.aliases or "").split(",") if x)
        out.append(Entry(name=m.name, type=m.type, aliases=aliases,
                         disambig=m.disambig,
                         linked_to=name_by_id.get(m.linked_to),
                         status=m.status or "在用"))
    return out


def _split(s) -> tuple:
    return tuple(x.strip() for x in (s or "").split(",") if x.strip())


def entries_from_legacy(session) -> List[Entry]:
    """把现有分表(Broker/Artist/Mama/MamaAssistant/Venue) 投影成 Entry 列表。
    切换期桥接: 让认人在当前数据上即时可用, 无需先迁移 master_data。"""
    from ..db.models import Broker, Artist, Mama, MamaAssistant, Venue
    out: List[Entry] = []
    bro = {b.id: b.name for b in session.query(Broker).all()}
    for name in bro.values():
        out.append(Entry(name, "经纪人"))
    for a in session.query(Artist).all():
        out.append(Entry(a.name, "艺人", aliases=_split(a.aliases),
                         linked_to=bro.get(a.broker_id)))
    mama = {m.id: m.name for m in session.query(Mama).all()}
    for m in session.query(Mama).all():
        out.append(Entry(m.name, "主妈咪", aliases=_split(m.aliases)))
    for asst in session.query(MamaAssistant).all():
        out.append(Entry(asst.name, "助理", linked_to=mama.get(asst.mama_id)))
    for v in session.query(Venue).all():
        out.append(Entry(v.name, "场所", aliases=_split(v.aliases)))
    return out
