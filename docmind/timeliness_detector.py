"""时效性检测增强模块：识别需要实时数据的问题，强制联网查询。

三层检测机制：
1. 具体日期检测（2023年8月15日、2023-08-15、8月15日）
2. 数据类型检测（票房、转化率、股价、销量等）
3. 模糊时间词检测（今天、最新、最近等）

命中规则：
- 具体日期 + 数据类型 → 强制联网（高优先级）
- 模糊时间词 + 数据类型 → 强制联网
- 仅具体日期或仅时间词 → 联网提示
"""
import re
from datetime import datetime, timedelta


# 具体日期模式（匹配各种格式）
_DATE_PATTERNS = [
    r'\d{4}年\d{1,2}月\d{1,2}[日号]',  # 2023年8月15日
    r'\d{4}-\d{1,2}-\d{1,2}',          # 2023-08-15
    r'\d{4}/\d{1,2}/\d{1,2}',          # 2023/08/15
    r'\d{1,2}月\d{1,2}[日号]',         # 8月15日
    r'\d{4}年\d{1,2}月',               # 2023年8月
]

# 数据类型关键词（需要实时数据的场景）
_DATA_KEYWORDS = [
    # 财务/商业数据
    '票房', '销量', '营收', '利润', '市值', '估值', '融资', '股价', '涨跌',
    # 用户/流量数据
    '用户数', '活跃用户', 'DAU', 'MAU', '转化率', '留存率', '点击率', '曝光',
    '互动量', '点赞', '评论', '转发', '播放量', '阅读量', '下载量',
    # 排名/榜单
    '排名', '榜单', '第几名', '热搜', '热度', 'TOP',
    # 事件/新闻
    '发布', '上线', '宣布', '公布', '报道', '消息', '新闻',
]

# 模糊时间词
_TIME_WORDS = [
    '今天', '今日', '昨天', '前天', '明天', '后天',
    '今年', '去年', '明年', '本年', '上年',
    '本月', '上月', '这个月', '上个月',
    '本周', '上周', '这周', '上周',
    '当前', '现在', '目前', '此刻',
    '最新', '最近', '近期', '近日', '刚刚', '刚才',
    '实时', '即时', '最新版', '新版',
    '热点', '热门', '爆火', '火爆',
]

# 年份检测（2020-2030）
_YEAR_PATTERN = r'20[2-3]\d年'


def detect_timeliness(question: str) -> dict:
    """检测问题的时效性特征，返回详细分析结果

    返回格式：
    {
        'is_time_sensitive': bool,        # 是否时效性问题
        'priority': str,                  # 'high' / 'medium' / 'low'
        'detected_date': str or None,     # 检测到的具体日期
        'detected_data_type': list,       # 检测到的数据类型关键词
        'detected_time_words': list,      # 检测到的时间词
        'reason': str,                    # 判定理由
    }
    """
    result = {
        'is_time_sensitive': False,
        'priority': 'low',
        'detected_date': None,
        'detected_data_type': [],
        'detected_time_words': [],
        'reason': '',
    }

    # 1. 检测具体日期
    for pattern in _DATE_PATTERNS:
        match = re.search(pattern, question)
        if match:
            result['detected_date'] = match.group()
            break

    # 2. 检测数据类型关键词
    for keyword in _DATA_KEYWORDS:
        if keyword in question:
            result['detected_data_type'].append(keyword)

    # 3. 检测模糊时间词
    for word in _TIME_WORDS:
        if word in question:
            result['detected_time_words'].append(word)

    # 4. 检测年份
    year_match = re.search(_YEAR_PATTERN, question)
    detected_year = year_match.group() if year_match else None
    current_year = datetime.now().year

    # 5. 综合判定优先级
    if result['detected_date'] and result['detected_data_type']:
        # 具体日期 + 数据类型 → 高优先级强制联网
        result['is_time_sensitive'] = True
        result['priority'] = 'high'
        result['reason'] = f"包含具体日期（{result['detected_date']}）和数据类型（{', '.join(result['detected_data_type'][:3])}），需要实时数据"

    elif result['detected_time_words'] and result['detected_data_type']:
        # 时间词 + 数据类型 → 高优先级
        result['is_time_sensitive'] = True
        result['priority'] = 'high'
        result['reason'] = f"包含时间词（{', '.join(result['detected_time_words'][:2])}）和数据类型，需要最新数据"

    elif result['detected_date']:
        # 仅具体日期 → 中优先级
        result['is_time_sensitive'] = True
        result['priority'] = 'medium'
        result['reason'] = f"包含具体日期（{result['detected_date']}），可能需要实时信息"

    elif detected_year:
        # 包含年份 → 检查是否是近期年份
        try:
            year_num = int(detected_year.replace('年', ''))
            if abs(year_num - current_year) <= 2:  # 前后2年内
                result['is_time_sensitive'] = True
                result['priority'] = 'medium'
                result['reason'] = f"包含近期年份（{detected_year}），建议联网核实"
        except ValueError:
            pass

    elif len(result['detected_time_words']) >= 2 or '最新' in result['detected_time_words']:
        # 多个时间词或包含"最新" → 中优先级
        result['is_time_sensitive'] = True
        result['priority'] = 'medium'
        result['reason'] = f"包含明确的时效性词汇（{', '.join(result['detected_time_words'][:3])}）"

    return result


def should_force_web_search(question: str) -> tuple[bool, str]:
    """判断是否应该强制联网搜索

    返回：(是否强制, 原因说明)
    """
    analysis = detect_timeliness(question)

    if analysis['priority'] == 'high':
        return True, analysis['reason']
    elif analysis['priority'] == 'medium':
        return True, analysis['reason']

    return False, ''


def extract_search_query(question: str, analysis: dict) -> str:
    """根据时效性分析提取更精准的搜索查询

    策略：
    - 如果有具体日期，加入搜索词
    - 如果有数据类型，保留关键词
    - 移除无关的疑问词
    """
    query = question

    # 保留核心信息
    if analysis['detected_date']:
        # 确保日期在搜索词中
        if analysis['detected_date'] not in query:
            query = f"{analysis['detected_date']} {query}"

    # 如果是"最新"类问题，添加年份限定
    if '最新' in analysis['detected_time_words']:
        current_year = datetime.now().year
        if str(current_year) not in query:
            query = f"{current_year} {query}"

    # 移除多余的疑问词（保留问题核心）
    query = re.sub(r'(是否|是不是|有没有|如何|怎么|怎样|为什么|吗|呢)\??$', '', query)

    return query.strip()


# 测试用例
if __name__ == "__main__":
    test_cases = [
        "《牛来》电影在2023年8月15日的票房数据是多少？",
        "小红书上最新的用户转化率是多少？",
        "今年iPhone的销量怎么样？",
        "2023年中国GDP增长率",
        "昨天的热搜排名",
        "什么是机器学习？",  # 非时效性
    ]

    for question in test_cases:
        result = detect_timeliness(question)
        print(f"\n问题：{question}")
        print(f"时效性：{result['is_time_sensitive']}")
        print(f"优先级：{result['priority']}")
        print(f"原因：{result['reason']}")
        if result['detected_date']:
            print(f"检测到日期：{result['detected_date']}")
        if result['detected_data_type']:
            print(f"数据类型：{', '.join(result['detected_data_type'])}")
