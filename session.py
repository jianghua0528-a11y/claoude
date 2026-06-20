"""
Telegram 机器人入口  ·  之后实现。
  · 监听 报单群 → parser.parse → 写 review_queue → 通知管理员去后台审核
  · 监听 财务群 → 解析 收款/预支/支出 → 写 review_queue
  · 管理员确认后 → 落库 → 生成日报发群 + 私发艺人月报
  · 需求: 机器人关闭隐私模式或设群管理员才能读全部消息
"""
# TODO(下一步): python-telegram-bot Application, 消息 handler
if __name__ == "__main__":
    print("bot 占位 — 解析+审核核心跑通后接入")
