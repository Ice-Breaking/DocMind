"""联网搜索失败后的智能降级策略。

场景：
1. 网络故障/超时
2. 搜索引擎无结果
3. 搜索到的结果不相关

降级策略：
1. 查询改写重试（扩大/缩小范围）
2. 时间范围放宽（具体日期 → 月份 → 年份）
3. 给出明确的"无数据"回复 + 替代建议
"""
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)


def extract_date_components(text: str) -> dict:
    """从文本中提取日期组件

    返回：{'year': str, 'month': str, 'day': str}
    未匹配的部分为 None
    """
    result = {'year': None, 'month': None, 'day': None}
    if not text:
        return result

    # 2023年8月15日
    match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})[日号]', text)
    if match:
        result['year'] = match.group(1)
        result['month'] = match.group(2)
        result['day'] = match.group(3)
        return result

    # 2023-08-15
    match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
    if match:
        result['year'] = match.group(1)
        result['month'] = match.group(2)
        result['day'] = match.group(3)
        return result

    # 8月15日（无年份）
    match = re.search(r'(\d{1,2})月(\d{1,2})[日号]', text)
    if match:
        result['month'] = match.group(1)
        result['day'] = match.group(2)
        return result

    # 2023年8月（无日期）
    match = re.search(r'(\d{4})年(\d{1,2})月', text)
    if match:
        result['year'] = match.group(1)
        result['month'] = match.group(2)
        return result

    return result


def generate_fallback_queries(original_query: str, timeliness_analysis: dict) -> list[str]:
    """生成降级查询列表，从精确到模糊

    策略：
    1. 去掉具体日期，保留月份
    2. 去掉月份，保留年份
    3. 去掉年份，改为"最新"
    4. 提取核心主题词
    """
    fallback_queries = []

    detected_date = timeliness_analysis.get('detected_date')
    if not detected_date:
        return fallback_queries

    date_comp = extract_date_components(detected_date)

    # 原始问题去掉具体日期
    query_without_date = original_query.replace(detected_date, '').strip()

    # 策略1：保留年月，去掉日期
    if date_comp['year'] and date_comp['month'] and date_comp['day']:
        fallback_queries.append(
            f"{date_comp['year']}年{date_comp['month']}月 {query_without_date}"
        )

    # 策略2：只保留年份
    if date_comp['year']:
        fallback_queries.append(
            f"{date_comp['year']} {query_without_date}"
        )

    # 策略3：改为"最新"
    fallback_queries.append(
        f"最新 {query_without_date}"
    )

    # 策略4：只保留核心主题
    # 去掉数据类型关键词，保留实体名称
    core_query = re.sub(r'(数据|情况|如何|怎么样|是多少|有多少)\??', '', query_without_date).strip()
    if core_query and core_query != query_without_date:
        fallback_queries.append(core_query)

    return fallback_queries


def format_no_data_response(question: str, timeliness_analysis: dict,
                            search_attempted: bool = True) -> str:
    """格式化"无数据"回复，提供有价值的替代信息

    返回一个友好的回复，说明：
    1. 为什么找不到数据
    2. 可能的原因
    3. 替代建议
    """
    # 注意 detect_timeliness 总是包含该键（可能为 None），
    # get 的默认值在键存在时不生效，必须用 or 兜底
    detected_date = timeliness_analysis.get('detected_date') or None
    detected_data = timeliness_analysis.get('detected_data_type', [])

    response_parts = []

    # 1. 开场：坦诚告知（无日期的问题不能硬套日期话术）
    date_label = f"**{detected_date}**" if detected_date else "您询问的问题"
    data_label = f"的{'/'.join(detected_data[:2])}数据" if detected_data else "相关数据"
    if search_attempted:
        response_parts.append(
            f"抱歉，我已尝试联网搜索，但未能找到{date_label}{data_label}。"
        )
    else:
        response_parts.append(
            f"抱歉，由于联网搜索暂时不可用，无法获取{date_label}{data_label}。"
        )

    # 2. 可能的原因
    response_parts.append("\n**可能的原因：**")
    reasons = [
        "该数据可能尚未公开发布",
        "该日期可能是非工作日或特殊时期，数据发布延迟",
        "搜索引擎尚未收录该时间点的数据报告",
    ]

    date_comp = extract_date_components(detected_date)
    if date_comp['year']:
        try:
            year_num = int(date_comp['year'])
            current_year = datetime.now().year
            if year_num > current_year:
                reasons.insert(0, f"您询问的是未来时间（{detected_date}），该数据尚不存在")
        except ValueError:
            pass

    for i, reason in enumerate(reasons[:3], 1):
        response_parts.append(f"{i}. {reason}")

    # 3. 替代建议
    response_parts.append("\n**替代建议：**")
    suggestions = []

    # 建议1：扩大时间范围
    if date_comp['day']:
        if date_comp['month']:
            suggestions.append(f"- 尝试查询整个**{date_comp['month']}月**的数据")
        if date_comp['year']:
            suggestions.append(f"- 查看**{date_comp['year']}年度**的总体数据")

    # 建议2：查询官方渠道
    if detected_data:
        data_type = detected_data[0]
        suggestions.append(f"- 访问官方平台或专业数据网站查询{data_type}")

    # 建议3：查询相关报告
    suggestions.append("- 搜索第三方分析报告或行业研报（可能包含该时期数据）")

    response_parts.extend(suggestions[:3])

    # 4. 我能提供什么
    response_parts.append("\n**我可以帮您：**")
    response_parts.append("- 如果您有相关报告文档，可上传到知识库后，我能帮您提取和分析数据")
    response_parts.append("- 可以为您解释该类数据的计算方法和行业标准")

    return "\n".join(response_parts)


def is_search_result_relevant(search_result: str, question: str,
                              timeliness_analysis: dict) -> bool:
    """判断搜索结果是否相关

    简单启发式：
    - 搜索结果长度 > 50
    - 包含问题中的关键实体（电影名、产品名等）
    - 包含数据类型关键词
    """
    if len(search_result) < 50:
        return False

    if "[错误]" in search_result or "暂不可用" in search_result:
        return False

    # 提取问题中的实体名称（简化版：匹配《》、「」中的内容）
    entities = re.findall(r'[《「](.*?)[》」]', question)
    for entity in entities:
        if entity in search_result:
            return True

    # 检查是否包含数据类型关键词
    detected_data = timeliness_analysis.get('detected_data_type', [])
    for data_type in detected_data:
        if data_type in search_result:
            return True

    return False
