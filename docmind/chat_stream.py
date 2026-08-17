"""SSE 聊天核心流程：结构化事件生成器（纯数据、渲染无关）。

定位：为前后端分离的新 UI（Ant Design X 等）提供流式协议层。与 Gradio 的
respond_simple 并存互不影响——ACL/语义缓存/OOD 守卫/防注入行为与主链路一致，
但多轮上下文改为「每请求从 chat.db raw 对确定性重建」（无单例状态污染，
为并发请求铺路；Gradio 下线后此模块即唯一应答链路）。

事件协议（kind + data，endpoint 逐条转 SSE）：
  cache     {"cached_question", "answer"}   语义缓存命中秒回
  thinking  {"text"}                        模型思维链增量
  token     {"text"}                        终答正文增量
  step      {"step_kind", "text"}           工具轨迹 tool_call/tool_result/rewrite/guard
  error     {"message"}                     异常兜底（随后仍发 final）
  final     {"answer"}                      完整终答（纯净版，落库/上下文直接用）
"""
from collections.abc import Iterator

from docmind import acl, config, semantic_cache
from docmind import store
from docmind.agent.react_agent import SYSTEM_PROMPT
from docmind.llm import embed

_TIME_SENSITIVE = {"get_weather", "get_current_time"}


def stream_events(agent, question: str, session_id: str = "",
                  user: str = "") -> Iterator[dict]:
    """核心应答流程，yield 结构化事件；任何异常收敛为 error+final，不挂空流"""
    acl.set_current_user(user)   # 文档级 ACL：检索/缓存按当前用户过滤

    # 1) 多轮上下文：从 DB raw 对确定性重建（切换会话/并发请求均不串上下文）
    agent.reset()
    if session_id:
        try:
            pairs = store.load_raw_pairs(session_id)
        except Exception as e:  # noqa: BLE001
            pairs = []
            print(f"[警告] SSE 上下文重建失败: {e}")
        if pairs:
            agent.history.append({"role": "system", "content": SYSTEM_PROMPT})
            agent.history.extend({"role": r, "content": c} for r, c in pairs)

    # 2) 语义缓存：高频问题秒回，跳过整个 Agent 链路
    q_vec = None
    if config.SEMANTIC_CACHE:
        try:
            q_vec = embed([question])[0]
            hit = semantic_cache.lookup(q_vec)
        except Exception as e:  # noqa: BLE001 - 缓存故障不阻塞主链路
            hit = None
            print(f"[警告] 语义缓存查询失败: {e}")
        if hit and not acl.answer_allowed(hit[1], user):
            hit = None   # 缓存答案引用了当前用户无权的受限文档 → 防跨用户泄露
        if hit:
            cq, cached_answer, _ = hit
            yield {"kind": "cache", "cached_question": cq, "answer": cached_answer}
            yield {"kind": "final", "answer": cached_answer}
            return

    # 3) Agent 主链路：步骤与 token 逐条透传
    final_answer = ""
    partial = ""
    try:
        for step in agent.ask(question):
            if step.kind == "token":
                partial += step.text
                yield {"kind": "token", "text": step.text}
            elif step.kind == "thinking":
                yield {"kind": "thinking", "text": step.text}
            elif step.kind == "final":
                final_answer = step.text
            else:
                yield {"kind": "step", "step_kind": step.kind, "text": step.text}
    except Exception as e:  # noqa: BLE001
        final_answer = f"⚠️ 处理过程中出现异常：{e}\n请重试，若持续失败请检查 API 额度与网络。"
        yield {"kind": "error", "message": str(e)}
    if not final_answer:
        final_answer = partial or "⚠️ 未获得模型回复，请重试。"

    # 4) 写语义缓存：与主链路同规则（实时类工具/错误答案/受限引用不入缓存）
    if (config.SEMANTIC_CACHE and q_vec is not None and final_answer
            and not final_answer.startswith("⚠️")
            and not (agent.last_tools & _TIME_SENSITIVE)
            and acl.answer_allowed(final_answer, user)):
        try:
            semantic_cache.save(question, final_answer, q_vec)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] 语义缓存写入失败: {e}")

    yield {"kind": "final", "answer": final_answer}
