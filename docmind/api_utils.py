"""API 层通用助手：服务端错误的安全响应。

问题：`raise HTTPException(500, detail=f"XX失败: {e}")` 模式把内部异常
文本（SQLite 错误、文件路径、上游 SDK 报文）直接透给浏览器——信息泄露
+ 前端文案不可控。统一收敛：完整堆栈只进服务端日志，对外返回通用文案。
"""
import logging


def server_error(action: str, exc: Exception, logger_name: str = "docmind.api"):
    """记录完整异常（含堆栈）后返回脱敏的 500 HTTPException。

    action：业务动作名（如 "会话删除"），对外文案为 "{action}失败，请稍后重试"。
    """
    logging.getLogger(logger_name).error("%s失败", action, exc_info=exc)
    from fastapi import HTTPException
    return HTTPException(status_code=500, detail=f"{action}失败，请稍后重试")
