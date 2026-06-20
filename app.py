"""
报单解析 (Claude API)  ·  下一步实现。
输入: 群里一条/一批乱报单文字。
输出: 结构化 dict 列表 + 必问清单触发的疑点 (写进 review_queue 待审)。

流程:
  1. 取字典(妈咪/场所/艺人别名) 拼进 system prompt 让 Claude 对照
  2. Claude 抽字段: 艺名/场所/包厢/妈咪/助理/客人/挂账/现金/门票/上下班/流向...
  3. 规则校验: 工价对照标价、日期12点归属、挂账门票拆分、双主妈咪、币种
  4. 不确定的标进 warnings, 不猜
"""
# TODO(下一步): 实现 parse_report(raw_text, dicts) -> (records, warnings)
