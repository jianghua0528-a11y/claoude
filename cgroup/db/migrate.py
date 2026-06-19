"""
迁移引导  ·  migrate.py
一次性把旧主文件(xlsx)的字典 + 历史报单 导进新库(Postgres/SQLite)。
部署时跑一次: python -m cgroup.db.migrate /path/to/主文件.xlsx
之后机器人直接往库里写新单, 不再依赖 xlsx。
"""
import re
import sys
from openpyxl import load_workbook
from .session import init_db, get_session
from .models import Broker, Artist, Mama, MamaAssistant, Venue

BROKERS = [("阿星", 0.07, True), ("阿豪", 0.07, True),
           ("老方", 0.07, True), ("老杨", 0.10, False)]

_SPLIT = re.compile(r"[,，、/]+")
_PAREN = re.compile(r"[（(].*?[）)]")


def clean_names(s):
    if not s:
        return []
    out = []
    for part in _SPLIT.split(str(s)):
        name = _PAREN.sub("", part).strip()      # 去掉 (助理)/(惠子) 等括注
        if name:
            out.append(name)
    return out


def migrate(xlsx_path):
    wb = load_workbook(xlsx_path, data_only=True)
    init_db()
    s = get_session()

    # 经纪人
    bmap = {}
    for name, pct, share in BROKERS:
        b = s.query(Broker).filter_by(name=name).one_or_none()
        if not b:
            b = Broker(name=name, pct=pct, is_shareholder=share)
            s.add(b); s.flush()
        bmap[name] = b.id

    # 艺人字典
    aw = wb["📋 艺人字典"]
    n_art = 0
    for r in range(4, aw.max_row + 1):
        name = aw.cell(row=r, column=2).value
        if not name:
            continue
        name = str(name).strip()
        if s.query(Artist).filter_by(name=name).first():
            continue
        alias = aw.cell(row=r, column=3).value
        broker = aw.cell(row=r, column=4).value
        s.add(Artist(name=name,
                     aliases=str(alias).strip() if alias and alias != "-" else None,
                     broker_id=bmap.get(str(broker).strip()) if broker else None))
        n_art += 1

    # 妈咪团队架构 (+ 助理)
    mw = wb["📋 妈咪团队架构"]
    n_mama = n_asst = 0
    for r in range(4, mw.max_row + 1):
        name = mw.cell(row=r, column=2).value
        if not name:
            continue
        name = str(name).strip()
        if s.query(Mama).filter_by(name=name).first():
            continue
        status = (mw.cell(row=r, column=3).value or "已合作").strip()
        alias = mw.cell(row=r, column=4).value
        team = mw.cell(row=r, column=5).value
        cycle = (mw.cell(row=r, column=7).value or "半月结").strip()
        m = Mama(name=name, cooperation_status=status, settlement_cycle=cycle,
                 aliases=str(alias).strip() if alias else None)
        s.add(m); s.flush(); n_mama += 1
        for a in clean_names(team):
            s.add(MamaAssistant(name=a, mama_id=m.id)); n_asst += 1

    # 场所字典
    vw = wb["📋 场所字典"]
    n_ven = 0
    for r in range(4, vw.max_row + 1):
        name = vw.cell(row=r, column=2).value
        if not name:
            continue
        name = str(name).strip()
        if s.query(Venue).filter_by(name=name).first():
            continue
        alias = vw.cell(row=r, column=3).value
        vtype = vw.cell(row=r, column=4).value
        ticket = vw.cell(row=r, column=5).value
        rooms = vw.cell(row=r, column=6).value
        try:
            ticket = float(ticket) if ticket not in (None, "") else 0.0
        except (ValueError, TypeError):
            ticket = 0.0          # 备注文字混进门票列 → 当0, 录单时人工补
        s.add(Venue(name=name,
                    aliases=str(alias).strip() if alias and alias != "-" else None,
                    vtype=str(vtype).strip() if vtype else "标准",
                    default_ticket=ticket,
                    rooms=str(rooms).strip() if rooms else None))
        n_ven += 1

    s.commit()
    print(f"字典迁移完成: 经纪人{len(BROKERS)} 艺人{n_art} 妈咪{n_mama}(助理{n_asst}) 场所{n_ven}")
    s.close()
    # TODO(下一步): migrate_orders(wb) —— 读 📥 数据库 358 单, 过引擎验闭环后入 orders 表


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else "master.xlsx")
