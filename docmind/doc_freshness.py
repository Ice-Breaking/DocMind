"""文档时效性检测与过期管理。

功能：
1. 从文档中提取时间信息（文件名、内容、元数据）
2. 判断文档是否过期（基于文档类型和当前时间）
3. 在引用时自动标注时效性警告

过期规则：
- 政策法规类：超过 1 年 → 高风险过期
- 数据报告类：超过 6 个月 → 高风险过期
- 技术文档类：超过 2 年 → 中风险过期
- 基础知识类：不过期
"""
import os
import re
from datetime import datetime, timedelta
from pathlib import Path


# 文档类型分类关键词
_DOC_TYPE_KEYWORDS = {
    'policy': ['政策', '法规', '条例', '规定', '办法', '通知', '公告', '管理', '实施细则'],
    'data': ['数据', '报告', '统计', '分析', '榜单', '排名', '财报', '业绩'],
    'tech': ['教程', '指南', '手册', 'API', '文档', '说明'],
    'knowledge': ['什么是', '介绍', '概述', '原理', '基础'],
}

# 过期阈值（天数）
_EXPIRY_THRESHOLDS = {
    'policy': 365,      # 政策法规：1年
    'data': 180,        # 数据报告：6个月
    'tech': 730,        # 技术文档：2年
    'knowledge': None,  # 基础知识：不过期
}


def extract_year_from_text(text: str) -> int | None:
    """从文本中提取年份

    优先级：
    1. 文件名中的年份（2023年、2023版）
    2. 内容开头的年份（前500字符）
    3. 内容中最晚的年份
    """
    if not text:
        return None

    # 匹配 2020-2030 年份
    years = []
    for match in re.finditer(r'20[2-3]\d', text[:500]):
        try:
            year = int(match.group())
            if 2020 <= year <= 2030:
                years.append(year)
        except ValueError:
            continue

    if not years:
        return None

    # 返回最新的年份（假设文档更倾向于标注最新时间）
    return max(years)


def classify_doc_type(filename: str, content: str = '') -> str:
    """判断文档类型

    返回：'policy' / 'data' / 'tech' / 'knowledge'
    """
    text = f"{filename} {content[:200]}".lower()

    # 优先匹配政策法规（最严格）
    for keyword in _DOC_TYPE_KEYWORDS['policy']:
        if keyword in text:
            return 'policy'

    # 再匹配数据报告
    for keyword in _DOC_TYPE_KEYWORDS['data']:
        if keyword in text:
            return 'data'

    # 技术文档
    for keyword in _DOC_TYPE_KEYWORDS['tech']:
        if keyword in text:
            return 'tech'

    # 默认基础知识
    return 'knowledge'


def check_document_freshness(source_file: str, content_preview: str = '') -> dict:
    """检查文档时效性

    返回格式：
    {
        'doc_year': int or None,          # 文档年份
        'doc_type': str,                  # 文档类型
        'is_expired': bool,               # 是否过期
        'expire_risk': str,               # 'high' / 'medium' / 'low' / 'none'
        'age_years': int,                 # 文档年龄（距今几年）
        'warning_message': str,           # 警告信息
        'should_web_search': bool,        # 是否应该强制联网
    }
    """
    current_year = datetime.now().year
    filename = os.path.basename(source_file)

    # 1. 提取文档年份
    doc_year = extract_year_from_text(f"{filename} {content_preview}")

    # 2. 判断文档类型
    doc_type = classify_doc_type(filename, content_preview)

    # 3. 计算过期情况
    result = {
        'doc_year': doc_year,
        'doc_type': doc_type,
        'is_expired': False,
        'expire_risk': 'none',
        'age_years': 0,
        'warning_message': '',
        'should_web_search': False,
    }

    if doc_year is None:
        # 无法判断年份，保守处理
        result['expire_risk'] = 'medium'
        result['warning_message'] = '⚠️ 文档未标注年份，无法判断时效性'
        result['should_web_search'] = (doc_type in ['policy', 'data'])
        return result

    age_years = current_year - doc_year
    result['age_years'] = age_years

    # 4. 根据文档类型和年龄判断过期风险
    if doc_type == 'knowledge':
        # 基础知识不过期
        result['expire_risk'] = 'none'
        result['warning_message'] = ''
        return result

    threshold_days = _EXPIRY_THRESHOLDS.get(doc_type, 365)

    if age_years >= 3:
        # 超过3年：高风险
        result['is_expired'] = True
        result['expire_risk'] = 'high'
        result['warning_message'] = f'⚠️ 严重过期：该文档来自 {doc_year} 年（距今 {age_years} 年），信息可能已失效'
        result['should_web_search'] = True

    elif age_years >= 2:
        # 2-3年：中高风险
        result['is_expired'] = True
        result['expire_risk'] = 'medium'
        result['warning_message'] = f'⚠️ 数据过期：该文档来自 {doc_year} 年（距今 {age_years} 年），建议核实最新信息'
        result['should_web_search'] = (doc_type in ['policy', 'data'])

    elif age_years >= 1:
        # 1-2年：中低风险
        if doc_type in ['policy', 'data']:
            result['is_expired'] = True
            result['expire_risk'] = 'medium'
            result['warning_message'] = f'⚠️ 可能过期：该文档来自 {doc_year} 年（距今 {age_years} 年），政策/数据可能已更新'
            result['should_web_search'] = True
        else:
            result['expire_risk'] = 'low'
            result['warning_message'] = f'💡 文档来自 {doc_year} 年（距今 {age_years} 年）'

    else:
        # 1年内：基本新鲜
        result['expire_risk'] = 'low'
        result['warning_message'] = f'✓ 文档来自 {doc_year} 年（较新）'

    return result


def format_kb_result_with_freshness(kb_result: str, source: str, content_preview: str = '') -> str:
    """为知识库检索结果添加时效性标注

    输入：知识库原始结果
    输出：带时效性警告的结果
    """
    freshness = check_document_freshness(source, content_preview)

    if freshness['expire_risk'] == 'none':
        # 不过期，直接返回
        return kb_result

    # 添加时效性警告
    warning = freshness['warning_message']

    if freshness['should_web_search']:
        warning += '\n\n💡 系统建议：由于文档已过期，已自动尝试联网搜索最新信息（见下方）'

    return f"{warning}\n\n{kb_result}"


def should_prioritize_web_search(question: str, kb_sources: list[str]) -> tuple[bool, str]:
    """判断是否应该优先联网搜索而非使用知识库

    返回：(是否应该, 原因)
    """
    # 检查知识库中最新文档的年份
    if not kb_sources:
        return False, ''

    latest_year = None
    for source in kb_sources:
        year = extract_year_from_text(os.path.basename(source))
        if year and (latest_year is None or year > latest_year):
            latest_year = year

    if latest_year is None:
        return False, ''

    current_year = datetime.now().year
    age = current_year - latest_year

    if age >= 2:
        return True, f'知识库最新文档来自 {latest_year} 年（距今 {age} 年），优先联网搜索'

    return False, ''
