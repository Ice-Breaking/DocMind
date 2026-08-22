"""SSE 聊天核心流程：结构化事件生成器（纯数据、渲染无关）。

定位：为前后端分离的新 UI（Ant Design X 等）提供流式协议层。与 Gradio 的
respond_simple 并存互不影响——ACL/语义缓存/OOD 守卫/防注入行为与主链路一致，
但多轮上下文改为「每请求从 chat.db raw 对确定性重建」（无单例状态污染，
为并发请求铺路；Gradio 下线后此模块即唯一应答链路）。

事件协议（kind + data，endpoint 逐条转 SSE）：
  cache     {"cached_question", "answer"}   语义缓存命中秒回
  reasoning {"answer"}                       Agent推理缓存命中
  thinking  {"text"}                        模型思维链增量
  token     {"text"}                        终答正文增量
  step      {"step_kind", "text"}           工具轨迹 tool_call/tool_result/rewrite/guard
  error     {"message"}                     异常兜底（随后仍发 final）
  final     {"answer"}                      完整终答（纯净版，落库/上下文直接用）
"""
import contextvars
import logging
from collections.abc import Iterator

from docmind import acl, config, semantic_cache
from docmind import agent_reasoning_cache
from docmind import store
from docmind.metrics import CACHE_HITS, CACHE_MISSES
from docmind.agent.react_agent import SYSTEM_PROMPT
from docmind.llm import embed

logger = logging.getLogger(__name__)

_TIME_SENSITIVE = {"get_weather", "get_current_time"}

# 助手上下文：端点在请求开始时 set 当前助手绑定的 KB 列表，
# core.knowledge_search 惰性读取以动态路由检索目标（空列表=默认知识库）
current_kb_ids: contextvars.ContextVar[list] = contextvars.ContextVar(
    "current_kb_ids", default=[])


def stream_events(agent, question: str, session_id: str = "",
                  user: str = "", assistant_id: str = "",
                  system_prompt: str | None = None) -> Iterator[dict]:
    """核心应答流程，yield 结构化事件；任何异常收敛为 error+final，不挂空流

    assistant_id 非空且非 "default" 时视为自定义助手：跳过语义缓存
    （自定义 system_prompt/KB 的应答不应污染/命中默认缓存）；
    system_prompt 覆盖多轮历史重建用的系统提示。"""
    acl.set_current_user(user)   # 文档级 ACL：检索/缓存按当前用户过滤
    sp = system_prompt if system_prompt else SYSTEM_PROMPT
    # 非默认助手不走语义缓存（读与写都跳过），默认链路行为保持逐字节一致
    use_cache = not assistant_id or assistant_id == "default"

    # 1) 多轮上下文：从 DB raw 对确定性重建（切换会话/并发请求均不串上下文）
    # 注意：agent 现在是每请求独立实例，无需 reset
    if session_id:
        try:
            pairs = store.load_raw_pairs(session_id)
        except Exception as e:  # noqa: BLE001
            pairs = []
            logger.warning(f"SSE 上下文重建失败: {e}")
        if pairs:
            agent.history.append({"role": "system", "content": sp})
            agent.history.extend({"role": r, "content": c} for r, c in pairs)

    # 2) 语义缓存：高频问题秒回，跳过整个 Agent 链路
    q_vec = None
    if config.SEMANTIC_CACHE and use_cache:
        try:
            q_vec = embed([question])[0]
            hit = semantic_cache.lookup(q_vec)
        except Exception as e:  # noqa: BLE001 - 缓存故障不阻塞主链路
            hit = None
            logger.warning(f"语义缓存查询失败: {e}")
        try:
            if hit:
                CACHE_HITS.inc()
            elif q_vec is not None:
                CACHE_MISSES.inc()   # 查询成功但未命中（查询异常不计 miss）
        except Exception:  # noqa: BLE001 - 指标故障不阻塞主链路
            pass
        if hit and not acl.answer_allowed(hit[1], user):
            hit = None   # 缓存答案引用了当前用户无权的受限文档 → 防跨用户泄露
        if hit:
            cq, cached_answer, _ = hit
            yield {"kind": "cache", "cached_question": cq, "answer": cached_answer}
            yield {"kind": "final", "answer": cached_answer}
            return

    # 2.5) Agent 推理缓存：完全相同的问题跳过 LLM 推理
    if use_cache:
        try:
            kb_ids = current_kb_ids.get() if assistant_id else []
            reasoning_hit = agent_reasoning_cache.lookup(question, kb_ids, sp)
            if reasoning_hit and acl.answer_allowed(reasoning_hit, user):
                yield {"kind": "reasoning", "answer": reasoning_hit}
                yield {"kind": "final", "answer": reasoning_hit}
                return
        except Exception as e:  # noqa: BLE001 - 缓存故障不阻塞主链路
            logger.warning(f"Agent推理缓存查询失败: {e}")

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
        logger.exception("Agent 应答链路异常")   # 细节只进日志，不透给用户
        final_answer = ("⚠️ 抱歉，处理您的问题时出现了内部故障，请稍后重试；"
                        "若持续失败请联系管理员。")
        yield {"kind": "error", "message": "处理过程中出现异常，请稍后重试"}
    if not final_answer:
        final_answer = partial or "⚠️ 未获得模型回复，请重试。"

    # 4) 写语义缓存：与主链路同规则（实时类工具/错误答案/受限引用不入缓存）
    if (config.SEMANTIC_CACHE and use_cache and q_vec is not None and final_answer
            and not final_answer.startswith("⚠️")
            and not (agent.last_tools & _TIME_SENSITIVE)
            and acl.answer_allowed(final_answer, user)):
        try:
            semantic_cache.save(question, final_answer, q_vec)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"语义缓存写入失败: {e}")

    # 5) 写 Agent 推理缓存：纯知识检索类问题可缓存
    if (use_cache and final_answer and not final_answer.startswith("⚠️")
            and not (agent.last_tools & _TIME_SENSITIVE)
            and acl.answer_allowed(final_answer, user)):
        try:
            kb_ids = current_kb_ids.get() if assistant_id else []
            agent_reasoning_cache.save(
                question, kb_ids, sp, final_answer, list(agent.last_tools)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Agent推理缓存写入失败: {e}")

    # failed 标记：兜底答案（⚠️ 开头）告知前端展示内联重试按钮
    yield {"kind": "final", "answer": final_answer,
           "failed": final_answer.startswith("⚠️")}
