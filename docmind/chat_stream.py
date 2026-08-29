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
from docmind.agent.react_agent import default_system_prompt
from docmind.llm import embed
from docmind.rag.query_cache import embed_query_cached

logger = logging.getLogger(__name__)

_TIME_SENSITIVE = {"get_weather", "get_current_time"}

# 时效敏感词：命中则跳过答案缓存（读与写），强制走主链路联网核实。
# 铁律「用户搜索必须拿到最新信息」的代码级闸门——时效检测原本在 Agent 内部，
# 排在缓存 lookup 之后会导致时效问题命中旧缓存秒回、绕过强制联网；
# 这里用纯本地规则检测（毫秒级、零 API），在缓存之前拦截。
_FRESHNESS_GATE_WORDS = ("最新", "新闻", "热点", "刚刚", "实时", "今天", "今日",
                         "现在", "目前", "当前", "最近", "近期", "昨天", "今年")

# 多轮上下文中最多回填的图片数（VL token 成本高，只保留最近的图）
_MAX_HISTORY_IMAGES = 2


def _image_file_to_data_url(path: str | None) -> str | None:
    """/files/uploads/xxx → data URL（多轮重建回填多模态消息用）。
    文件缺失/读取失败返回 None（降级为纯文本，不阻断重建）"""
    if not path:
        return None
    try:
        import base64
        import mimetypes
        import os
        fname = os.path.basename(path.split("/uploads/")[-1])
        fp = os.path.join("data", "uploads", fname)
        if not os.path.isfile(fp):
            return None
        mime = mimetypes.guess_type(fname)[0] or "image/png"
        with open(fp, "rb") as f:
            return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"
    except Exception:  # noqa: BLE001
        return None


def _is_freshness_critical(question: str) -> bool:
    """时效闸门：纯本地规则判定该问题是否必须绕过缓存获取最新信息"""
    if any(w in question for w in _FRESHNESS_GATE_WORDS):
        return True
    try:
        from docmind.timeliness_detector import detect_timeliness
        if detect_timeliness(question)["is_time_sensitive"]:
            return True
        from docmind.intent_understanding import detect_question_intent
        if detect_question_intent(question)["needs_latest_data"]:
            return True
    except Exception:  # noqa: BLE001 - 检测故障放行走缓存（宁快勿断）
        return False
    return False

# 助手上下文：端点在请求开始时 set 当前助手绑定的 KB 列表，
# core.knowledge_search 惰性读取以动态路由检索目标（空列表=默认知识库）
current_kb_ids: contextvars.ContextVar[list] = contextvars.ContextVar(
    "current_kb_ids", default=[])

# 查询向量复用：语义缓存已对原始问题 embed 过一次，检索层若用同一
# 文本检索可直接复用（存 (文本, 向量) 二元组，文本一致才复用——
# 模型改写后的检索词向量不同，不能混用）
current_query_vec: contextvars.ContextVar[tuple] = contextvars.ContextVar(
    "current_query_vec", default=(None, None))


def stream_events(agent, question: str, session_id: str = "",
                  user: str = "", assistant_id: str = "",
                  system_prompt: str | None = None,
                  image_data: 'str | list[str] | None' = None,
                  kb_ids: list | None = None) -> Iterator[dict]:
    """核心应答流程，yield 结构化事件；任何异常收敛为 error+final，不挂空流

    assistant_id 非空且非 "default" 时视为自定义助手：跳过语义缓存
    （自定义 system_prompt/KB 的应答不应污染/命中默认缓存）；
    system_prompt 覆盖多轮历史重建用的系统提示。
    image_data：图片 base64（data URL）——多模态消息当轮现算，
    与时效问题一样跳过答案缓存读写。
    kb_ids：当前请求绑定的知识库列表。必须显式传参而非依赖调用方
    预先 set ContextVar——本生成器通常在独立 producer 线程中运行，
    threading.Thread 不继承父线程的 contextvars，跨线程 set 的值
    在本线程内读不到（自定义助手的 KB 路由会静默失效回退默认库）；
    在本函数入口 set 保证与后续 knowledge_search 同线程可见。"""
    acl.set_current_user(user)   # 文档级 ACL：检索/缓存按当前用户过滤
    current_kb_ids.set(list(kb_ids or []))   # 本线程内检索层可读（见 docstring）
    sp = system_prompt if system_prompt else default_system_prompt()
    # 非默认助手不走语义缓存（读与写都跳过），默认链路行为保持逐字节一致
    use_cache = not assistant_id or assistant_id == "default"

    # 1) 多轮上下文：从 DB raw 对确定性重建（切换会话/并发请求均不串上下文）
    #    滑动窗口：仅带最近 MAX_HISTORY_TURNS 条消息进 prompt——长会话下
    #    全量历史会让 token 与延迟线性膨胀；被截断轮次以注记替代，
    #    模型仍知晓存在更早对话（指代消解所需的近邻轮次完整保留）
    #    图片上下文：user 轮次携带的附件重新编码进多模态消息——
    #    追问"右边那张是什么"时模型必须还能看到图（只回填最近
    #    _MAX_HISTORY_IMAGES 张，VL token 成本高，更早的图丢弃）
    if session_id:
        try:
            triples = store.load_pairs_with_images(session_id)
        except Exception as e:  # noqa: BLE001
            triples = []
            logger.warning(f"SSE 上下文重建失败: {e}")
        keep_imgs: set[str] = set()
        for _r, _t, p in reversed(triples):
            if p:
                keep_imgs.add(p)
                if len(keep_imgs) >= _MAX_HISTORY_IMAGES:
                    break
        pairs = [(r, t, p if (r == "user" and p in keep_imgs) else None)
                 for r, t, p in triples if t]
        if len(pairs) > config.MAX_HISTORY_TURNS:
            dropped = len(pairs) - config.MAX_HISTORY_TURNS
            pairs = pairs[-config.MAX_HISTORY_TURNS:]
            pairs = [("system",
                      f"[上下文窗口注记] 更早的 {dropped} 条消息未载入，"
                      "如用户提及更早内容请说明记忆范围有限", None)] + pairs
        if pairs:
            agent.history.append({"role": "system", "content": sp})
            for r, t, img in pairs:
                data_url = _image_file_to_data_url(img) if img else None
                if data_url:
                    agent.history.append({"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": t},
                    ]})
                else:
                    agent.history.append({"role": r, "content": t})

    # 1.5) 预启动术语解读（后台线程）：该步是独立 LLM 调用，与下方
    #      embedding/缓存查询互不依赖——并行执行把一次串行 LLM 往返
    #      （约 300-800ms 首响应延迟）藏进等待窗口；agent.ask 内直接收取。
    #      图片消息跳过（视觉理解为主）；命中缓存提前返回时 cancel 未启动
    #      的任务，尽量减少缓存命中路径的无谓消耗（已启动的结果弃用，无害）
    #      兼容性：getattr 探测，最小实现的 agent 替身（测试/嵌入方）无需实现
    interpret_future = None
    _start_interpret = getattr(agent, "start_interpret", None)
    if _start_interpret is not None:
        interpret_future = _start_interpret(question,
                                            skip=image_data is not None)

    # 2) 语义缓存：高频问题秒回，跳过整个 Agent 链路
    #    时效闸门在前：时效性问题跳过缓存读写，强制走联网链路（铁律保证）；
    #    图片消息同样跳过（多模态当轮现算，且缓存为纯文本语义）
    q_vec = None
    freshness_critical = _is_freshness_critical(question)
    bypass_cache = freshness_critical or image_data is not None
    if config.SEMANTIC_CACHE and use_cache and not bypass_cache:
        try:
            # 查询向量走 LRU 热缓存（query_cache）：同题重复请求免一次
            # embedding 网络往返；时效性问题本就不进缓存，无新鲜度冲突
            q_vec = embed_query_cached(embed, question)
            current_query_vec.set((question, q_vec))   # 供检索层复用（文本一致时）
            hit = semantic_cache.lookup(q_vec, kb_ids)
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
            if interpret_future is not None:
                interpret_future.cancel()   # 缓存命中无需解读结果，未启动即取消
            cq, cached_answer, _ = hit
            yield {"kind": "cache", "cached_question": cq, "answer": cached_answer}
            yield {"kind": "final", "answer": cached_answer}
            return

    # 2.5) Agent 推理缓存：完全相同的问题跳过 LLM 推理（时效/图片问题同样跳过）
    if use_cache and not bypass_cache:
        try:
            reasoning_hit = agent_reasoning_cache.lookup(question, kb_ids or [], sp)
            if reasoning_hit and acl.answer_allowed(reasoning_hit, user):
                if interpret_future is not None:
                    interpret_future.cancel()   # 同语义缓存命中：取消未启动任务
                yield {"kind": "reasoning", "answer": reasoning_hit}
                yield {"kind": "final", "answer": reasoning_hit}
                return
        except Exception as e:  # noqa: BLE001 - 缓存故障不阻塞主链路
            logger.warning(f"Agent推理缓存查询失败: {e}")

    # 3) Agent 主链路：步骤与 token 逐条透传（图片消息带多模态数据）
    final_answer = ""
    partial = ""
    try:
        for step in agent.ask(question, image_data=image_data,
                              interpret_future=interpret_future):
            if step.kind == "token":
                partial += step.text
                yield {"kind": "token", "text": step.text}
            elif step.kind == "thinking":
                yield {"kind": "thinking", "text": step.text}
            elif step.kind == "final":
                final_answer = step.text
            else:
                yield {"kind": "step", "step_kind": step.kind, "text": step.text}
    except Exception:  # noqa: BLE001
        logger.exception("Agent 应答链路异常")   # 细节只进日志，不透给用户
        final_answer = ("⚠️ 抱歉，处理您的问题时出现了内部故障，请稍后重试；"
                        "若持续失败请联系管理员。")
        yield {"kind": "error", "message": "处理过程中出现异常，请稍后重试"}
    if not final_answer:
        final_answer = partial or "⚠️ 未获得模型回复，请重试。"

    # 4) 写语义缓存：与主链路同规则（实时类工具/错误答案/受限引用/时效问题不入缓存）
    if (config.SEMANTIC_CACHE and use_cache and q_vec is not None and final_answer
            and not bypass_cache
            and not final_answer.startswith("⚠️")
            and not (agent.last_tools & _TIME_SENSITIVE)
            and acl.answer_allowed(final_answer, user)):
        try:
            semantic_cache.save(question, final_answer, q_vec, kb_ids)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"语义缓存写入失败: {e}")

    # 5) 写 Agent 推理缓存：纯知识检索类问题可缓存（时效问题不入）
    if (use_cache and final_answer and not bypass_cache
            and not final_answer.startswith("⚠️")
            and not (agent.last_tools & _TIME_SENSITIVE)
            and acl.answer_allowed(final_answer, user)):
        try:
            agent_reasoning_cache.save(
                question, kb_ids or [], sp, final_answer, list(agent.last_tools)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Agent推理缓存写入失败: {e}")

    # failed 标记：兜底答案（⚠️ 开头）告知前端展示内联重试按钮
    yield {"kind": "final", "answer": final_answer,
           "failed": final_answer.startswith("⚠️")}
