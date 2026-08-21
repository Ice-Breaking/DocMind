"""问题意图理解：判断用户是否需要最新数据。

核心思路：
不只看明确的时间词（"2026年"、"最新"），而是通过语义理解判断问题的性质。

问题分类：
1. 时效性强问题 - 需要最新数据
   - 政策法规（"天津钓鱼政策"、"禁渔期规定"）
   - 实时数据（"票房"、"股价"、"销量"）
   - 当前状态（"哪里可以钓鱼"、"现在能钓吗"）

2. 时效性弱问题 - 可用旧数据
   - 基础知识（"什么是机器学习"）
   - 历史事件（"2020年发生了什么"）
   - 技术原理（"RAG是什么"）

策略：
- 问题包含政策/法规/许可/申请类词汇 → 默认需要当前年份的信息
- 问题包含地名+活动（"天津钓鱼"） → 默认需要当前规定
- 问题包含"能不能"、"可以吗" → 默认需要当前状态
"""
import re
from datetime import datetime


# 时效性强的主题类别
_TIMELY_TOPICS = {
    'policy': [
        '政策', '法规', '条例', '规定', '办法', '通知',
        '许可', '申请', '审批', '禁止', '允许', '限制',
        '管理', '要求', '标准', '流程', '手续', '证件',
        '禁渔', '开放', '关闭', '封闭', '管制',
    ],
    'permission': [
        '能不能', '可以吗', '允许吗', '能否', '是否可以',
        '能去', '能钓', '能做', '能用', '能办',
    ],
    'current_state': [
        '哪里', '在哪', '什么地方', '哪个区域',
        '开没开', '开了吗', '营业吗', '开放吗',
        '现在', '目前', '当前', '如今',
    ],
    'data': [
        '数据', '统计', '报告', '排名', '榜单',
        '票房', '销量', '股价', '市值', '营收',
        '用户数', '转化率', '增长率', '占比',
    ],
}

# 时效性弱的主题类别
_TIMELESS_TOPICS = {
    'knowledge': [
        '什么是', '是什么', '定义', '概念', '原理',
        '介绍', '解释', '说明', '讲解',
    ],
    'history': [
        '历史', '由来', '起源', '发展史',
        '过去', '曾经', '以前', '当时',
    ],
    'tech': [
        '怎么做', '如何', '方法', '步骤', '教程',
        '代码', '实现', '配置', '安装',
    ],
}


def detect_question_intent(question: str) -> dict:
    """检测问题意图，判断是否需要最新数据

    返回格式：
    {
        'needs_latest_data': bool,        # 是否需要最新数据
        'confidence': float,              # 置信度 0-1
        'reason': str,                    # 判定理由
        'topic_category': str,            # 主题类别
        'inferred_year': int or None,     # 推断的目标年份
    }
    """
    result = {
        'needs_latest_data': False,
        'confidence': 0.5,
        'reason': '',
        'topic_category': 'unknown',
        'inferred_year': None,
    }

    current_year = datetime.now().year

    # 1. 检查是否明确提到未来年份（如"2026年"）
    mentioned_years = []
    for match in re.finditer(r'20[2-3]\d', question):
        try:
            year = int(match.group())
            if 2020 <= year <= 2030:
                mentioned_years.append(year)
        except ValueError:
            pass

    if mentioned_years:
        max_year = max(mentioned_years)
        if max_year >= current_year:
            # 明确问当前或未来年份
            result['needs_latest_data'] = True
            result['confidence'] = 0.95
            result['reason'] = f'问题明确提到 {max_year} 年（当前或未来）'
            result['inferred_year'] = max_year
            result['topic_category'] = 'explicit_year'
            return result
        elif max_year == current_year - 1:
            # 问去年，也算需要较新数据
            result['needs_latest_data'] = True
            result['confidence'] = 0.8
            result['reason'] = f'问题提到 {max_year} 年（去年），需要近期数据'
            result['inferred_year'] = max_year
            result['topic_category'] = 'recent_year'
            return result
        else:
            # 明确问历史年份
            result['needs_latest_data'] = False
            result['confidence'] = 0.9
            result['reason'] = f'问题提到 {max_year} 年（历史），不需要最新数据'
            result['inferred_year'] = max_year
            result['topic_category'] = 'history'
            return result

    # 2. 检查时效性强的主题（政策、许可、当前状态、数据）
    timely_matches = []
    for category, keywords in _TIMELY_TOPICS.items():
        for keyword in keywords:
            if keyword in question:
                timely_matches.append((category, keyword))

    if timely_matches:
        # 命中时效性强的主题
        category = timely_matches[0][0]
        keywords = [m[1] for m in timely_matches]

        result['needs_latest_data'] = True
        result['topic_category'] = category

        # 根据类别调整置信度
        if category == 'policy':
            result['confidence'] = 0.9
            result['reason'] = f'涉及政策法规类问题（关键词：{", ".join(keywords[:3])}），需要{current_year}年最新规定'
        elif category == 'permission':
            result['confidence'] = 0.85
            result['reason'] = f'询问当前是否允许（关键词：{", ".join(keywords[:2])}），需要最新状态'
        elif category == 'current_state':
            result['confidence'] = 0.85
            result['reason'] = f'询问当前状态（关键词：{", ".join(keywords[:2])}），需要实时信息'
        elif category == 'data':
            result['confidence'] = 0.8
            result['reason'] = f'询问数据指标（关键词：{", ".join(keywords[:2])}），需要最新数据'

        result['inferred_year'] = current_year
        return result

    # 3. 检查时效性弱的主题（基础知识、历史、技术）
    timeless_matches = []
    for category, keywords in _TIMELESS_TOPICS.items():
        for keyword in keywords:
            if keyword in question:
                timeless_matches.append((category, keyword))

    if timeless_matches:
        # 命中时效性弱的主题
        category = timeless_matches[0][0]
        keywords = [m[1] for m in timeless_matches]

        result['needs_latest_data'] = False
        result['topic_category'] = category
        result['confidence'] = 0.8
        result['reason'] = f'{category}类问题（关键词：{", ".join(keywords[:2])}），不强制要求最新数据'
        return result

    # 4. 地名+活动模式（如"天津钓鱼"） → 默认需要当前规定
    location_activity_pattern = r'(天津|北京|上海|深圳|广州|杭州|成都|武汉|[省市区县])\s*(钓鱼|滑雪|露营|徒步|登山|游泳|野餐)'
    if re.search(location_activity_pattern, question):
        result['needs_latest_data'] = True
        result['confidence'] = 0.85
        result['reason'] = f'询问特定地点的活动规定，需要{current_year}年当前政策'
        result['topic_category'] = 'location_activity'
        result['inferred_year'] = current_year
        return result

    # 5. 默认：中等置信度，倾向于不需要（避免过度联网）
    result['needs_latest_data'] = False
    result['confidence'] = 0.6
    result['reason'] = '未识别到明确的时效性特征，默认不强制联网'
    result['topic_category'] = 'general'

    return result


def should_web_search_first(question: str, kb_available: bool = True,
                            kb_freshness: dict | None = None) -> tuple[bool, str]:
    """综合判断是否应该优先联网搜索

    考虑因素：
    1. 问题意图（是否需要最新数据）
    2. 知识库是否可用
    3. 知识库文档是否过期

    返回：(是否应该优先联网, 原因)
    """
    intent = detect_question_intent(question)

    # 情况1：问题不需要最新数据 → 不强制联网
    if not intent['needs_latest_data']:
        return False, intent['reason']

    # 情况2：需要最新数据，但知识库不可用 → 必须联网
    if not kb_available:
        return True, f"{intent['reason']}，且知识库无相关内容"

    # 情况3：需要最新数据，知识库可用但过期 → 优先联网
    if kb_freshness and kb_freshness.get('should_web_search'):
        return True, f"{intent['reason']}，且知识库文档已过期（{kb_freshness.get('doc_year')}年）"

    # 情况4：需要最新数据，知识库较新 → 联网+知识库结合
    if kb_freshness and kb_freshness.get('expire_risk') in ['low', 'none']:
        return False, f"{intent['reason']}，但知识库文档较新，优先使用知识库（可辅以联网核实）"

    # 情况5：需要最新数据，知识库新鲜度未知 → 优先联网
    return True, f"{intent['reason']}，知识库时效性未知，优先联网搜索"
