"""
字典初始化  ·  seed.py
跑一次把 经纪人/艺人/场所 灌进库。
妈咪 + 全部历史报单 → 等阿豪最新主文件上来后用 migrate 脚本导入(数据量大且状态需准)。
用法: python -m cgroup.db.seed
"""
from .session import init_db, get_session
from .models import Broker, Artist, Venue

# 经纪人 (确认 2026-05-24)
BROKERS = [
    ("阿星", 0.07, True),
    ("阿豪", 0.07, True),
    ("老方", 0.07, True),
    ("老杨", 0.10, False),
]

# 艺人 → 经纪人 (确认 2026-05-24; VIVI/钱钱/蛋挞/小渔儿 待分配, 留空)
ARTISTS = {
    "阿星": ["桃子", "小小", "倩倩", "雨涵", "梦梦", "尤娜", "艾拉", "元元", "瑶宝", "cc"],
    "阿豪": ["高寒", "夏沫", "米妮", "梨涡", "佳佳", "小羊", "coco"],
    "老方": ["萌萌", "豆芽", "小白", "QQ"],
    "老杨": ["林然"],
}
ARTISTS_UNASSIGNED = ["VIVI", "钱钱", "蛋挞", "小渔儿"]

# 场所 (name, 默认门票, 类型, 包厢)  —— 取自当前场所字典
VENUES = [
    ("88", 150, "标准", "小时光,巴黎"),
    ("9号", 150, "标准", "11,21,25,6,666,888"),
    ("Setia", 0, "标准", "s.b"),
    ("TRX", 0, "标准", "2"),
    ("YESKTV", 0, "标准", "7"),
    ("亿豪", 150, "标准", "夜宴,沉香,至尊"),
    ("华纳", 150, "标准", "V3"),
    ("名门", 200, "标准", "土星,小行星,火星,天王星,水星"),
    ("天上", 150, "标准", "杭州"),
    ("天成", 0, "标准", "VVIP2"),
    ("天际线", 200, "标准", "至尊,888"),
    ("紫荆城", 150, "标准", "乾清宫,国公府,将军府,尚书府,庆王府,恭王府,文华殿,王府,延禧宫,储秀宫,长春宫,坤宁宫,666,999"),
    ("金悦会", 150, "标准", "111,v5"),
    ("公寓", 0, "酒店公寓", ""),
    ("别墅", 0, "酒店公寓", ""),
    ("翠莲公寓", 0, "酒店公寓", "公寓"),
    ("梦幻星球", 200, "标准", ""),
    ("皇庭", 100, "标准", ""),
    ("GBC", 200, "标准", ""),
    ("宝丽金", 150, "标准", "巨蟹座"),
    ("天意", 100, "标准", ""),
    ("凯旋门", 100, "标准", ""),
    ("维加斯", 100, "标准", ""),
    ("Yaki soul kL", 0, "酒店公寓", ""),
    ("万豪公寓", 0, "酒店公寓", ""),
    ("Menara HLX", 0, "酒店公寓", ""),
    ("四季公寓", 0, "酒店公寓", ""),
    ("天城大厦", 0, "标准", ""),
    ("皇家", 100, "标准", ""),
    ("Redbox", 0, "标准", ""),
    ("SEASON KL", 0, "标准", ""),
    ("THEFACE", 0, "标准", ""),
    ("永和", 150, "标准", ""),
    ("酒店", 0, "标准", ""),
]

# 场所别名
VENUE_ALIASES = {
    "9号": "九号,9club", "天上": "天上人间", "天成": "天成大厦", "紫荆城": "紫禁城",
    "金悦会": "金悦汇", "翠莲公寓": "翠联", "皇庭": "黄庭", "酒店": "通用酒店",
    "SEASON KL": "WelcometoSEASONKL",
}


def run():
    init_db()
    s = get_session()
    if s.query(Broker).count() == 0:
        bmap = {}
        for name, pct, share in BROKERS:
            b = Broker(name=name, pct=pct, is_shareholder=share)
            s.add(b); s.flush(); bmap[name] = b.id
        for bname, names in ARTISTS.items():
            for n in names:
                s.add(Artist(name=n, broker_id=bmap[bname]))
        for n in ARTISTS_UNASSIGNED:
            s.add(Artist(name=n))
        for name, ticket, vtype, rooms in VENUES:
            s.add(Venue(name=name, default_ticket=ticket, vtype=vtype,
                        rooms=rooms or None, aliases=VENUE_ALIASES.get(name)))
        s.commit()
        print(f"灌入: 经纪人{len(BROKERS)} 艺人{sum(len(v) for v in ARTISTS.values())+len(ARTISTS_UNASSIGNED)} 场所{len(VENUES)}")
    else:
        print("已有数据, 跳过")
    s.close()


if __name__ == "__main__":
    run()
