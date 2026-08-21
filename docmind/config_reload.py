"""配置热加载：无需重启服务即可更新部分配置。

支持热加载的配置：
- CACHE_THRESHOLD: 语义缓存阈值
- WEB_SEARCH_TIMEOUT: 搜索超时
- WEB_SEARCH_CACHE_TTL: 搜索缓存 TTL
- MAX_OUTPUT_TOKENS: 最大输出 token 数

不支持热加载的配置（需要重启）：
- API Keys（DASHSCOPE_API_KEY 等）
- 模型标识（CHAT_MODEL 等）
- 数据库路径、端口等基础设施配置
"""
import logging
import os

from dotenv import load_dotenv

from docmind import config

logger = logging.getLogger(__name__)


def reload_config() -> dict[str, any]:
    """重新加载 .env 配置，返回变更的配置项

    仅加载运行时可变的配置，基础设施类配置需要重启
    """
    # 重新加载 .env
    load_dotenv(override=True)

    changes = {}

    # 语义缓存阈值
    new_threshold = float(os.getenv("CACHE_THRESHOLD", "0.92"))
    if new_threshold != config.CACHE_THRESHOLD:
        old = config.CACHE_THRESHOLD
        config.CACHE_THRESHOLD = new_threshold
        changes["CACHE_THRESHOLD"] = {"old": old, "new": new_threshold}
        logger.info(f"配置热加载: CACHE_THRESHOLD {old} → {new_threshold}")

    # 搜索超时
    new_timeout = int(os.getenv("WEB_SEARCH_TIMEOUT", "8"))
    if new_timeout != config.WEB_SEARCH_TIMEOUT:
        old = config.WEB_SEARCH_TIMEOUT
        config.WEB_SEARCH_TIMEOUT = new_timeout
        changes["WEB_SEARCH_TIMEOUT"] = {"old": old, "new": new_timeout}
        logger.info(f"配置热加载: WEB_SEARCH_TIMEOUT {old} → {new_timeout}")

    # 搜索缓存 TTL
    new_ttl = int(os.getenv("WEB_SEARCH_CACHE_TTL", "1800"))
    if new_ttl != config.WEB_SEARCH_CACHE_TTL:
        old = config.WEB_SEARCH_CACHE_TTL
        config.WEB_SEARCH_CACHE_TTL = new_ttl
        changes["WEB_SEARCH_CACHE_TTL"] = {"old": old, "new": new_ttl}
        logger.info(f"配置热加载: WEB_SEARCH_CACHE_TTL {old} → {new_ttl}")

    # 最大输出 token 数
    new_tokens = int(os.getenv("MAX_OUTPUT_TOKENS", "2000"))
    if new_tokens != config.MAX_OUTPUT_TOKENS:
        old = config.MAX_OUTPUT_TOKENS
        config.MAX_OUTPUT_TOKENS = new_tokens
        changes["MAX_OUTPUT_TOKENS"] = {"old": old, "new": new_tokens}
        logger.info(f"配置热加载: MAX_OUTPUT_TOKENS {old} → {new_tokens}")

    if not changes:
        logger.info("配置热加载: 无变更")

    return changes


def get_reloadable_configs() -> dict[str, any]:
    """获取当前可热加载的配置值"""
    return {
        "CACHE_THRESHOLD": config.CACHE_THRESHOLD,
        "WEB_SEARCH_TIMEOUT": config.WEB_SEARCH_TIMEOUT,
        "WEB_SEARCH_CACHE_TTL": config.WEB_SEARCH_CACHE_TTL,
        "MAX_OUTPUT_TOKENS": config.MAX_OUTPUT_TOKENS,
    }
