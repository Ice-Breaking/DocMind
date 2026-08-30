"""RAGAS 式生成质量评测（LLM-as-judge）：补齐检索指标之外的「答案侧」度量。

背景：
    原有评测体系只覆盖检索侧（Recall@k / MRR）——检索对了不代表回答可信。
    参照 RAGAS 论文的四指标思路自研实现（不引第三方依赖、判官走既有
    llm.chat 通道），把质量闭环从「检索准不准」延伸到「回答可不可信」。

四指标：
    faithfulness      忠实度：答案拆成原子断言后，逐条判定是否被检索
                      上下文支持。得分 = 支持数 / 断言总数。防幻觉核心指标。
    answer_relevancy  答案相关性：由答案反向生成 3 个候选问题，与原问题做
                      embedding 余弦相似度取均值。衡量答非所问程度。
    context_precision 上下文精确率：逐条判定召回上下文与问题相关性，
                      按排名计算平均精确率 AP（相关的排得越靠前分越高）。
    context_recall    上下文召回率：标准答案逐句判定能否归因到召回上下文，
                      覆盖比例。（需数据集带参考答案，缺省跳过）

工程约定：
    - 单指标失败互不牵连（score=None + note），增强类故障不阻断整体评估；
    - 判官输出严格 JSON，解析容错（剥代码围栏 + 括号配平扫描）；
    - 判官调用复用 llm.chat（自动获得重试/指标/追踪能力）。
"""
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from docmind.llm import chat, embed

logger = logging.getLogger(__name__)

_JUDGE_MAX_TOKENS = 800

# ── 判官提示词（中文，要求严格输出 JSON）─────────────────────────────────

_FAITHFUL_PROMPT = """你严格的评审。基于给定的参考资料，判断回答中的每一条陈述是否有依据。

资料：
{contexts}

回答：
{answer}

请把回答拆解为原子断言（每条一个独立事实点），逐条判断资料能否支持。
严格输出 JSON，不要任何其他文字：
{{"claims": [{{"claim": "断言内容", "supported": true}}, ...]}}"""

_RELEVANCY_PROMPT = """基于下面的回答，反向生成 3 个「该回答恰好能解答」的用户问题。

回答：
{answer}

要求：问题的具体程度与原问题相当；严格输出 JSON，不要任何其他文字：
{{"questions": ["问题1", "问题2", "问题3"]}}"""

_CTX_RELEVANT_PROMPT = """判断以下文本对于回答用户问题是否相关（包含有助于回答的信息即为相关，无需直接给出完整答案）。

问题：{question}

文本：
{context}

严格输出 JSON，不要任何其他文字：{{"relevant": true}} 或 {{"relevant": false}}"""

_RECALL_PROMPT = """参考资料如下：

{contexts}

请判断：下面这句话能否完全依据上述资料得出（归因成立）？

句子：{sentence}

严格输出 JSON，不要任何其他文字：{{"attributable": true}} 或 {{"attributable": false}}"""


def _extract_json(text: str) -> dict | None:
    """从判官输出提取首个平衡的 JSON 对象：剥代码围栏 + 括号配平扫描，
    避免正则贪婪匹配把尾随文字吞进 payload 导致解析失败。"""
    if not text:
        return None
    t = re.sub(r"```(?:json)?|```", "", text).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _ask_judge(prompt: str) -> dict | None:
    """调一次判官并解析 JSON；失败返回 None（由调用方决定降级方式）。"""
    msg = chat([{"role": "user", "content": prompt}],
               max_tokens=_JUDGE_MAX_TOKENS, temperature=0)
    return _extract_json(getattr(msg, "content", "") or "")


def _split_sentences(text: str) -> list[str]:
    """中文句级切分（忠实度/召回率的原子单位）。"""
    parts = re.split(r"[。！？!?;\n；]+", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python，避免为单次点积引入 numpy 依赖）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _fmt_contexts(contexts: list[str]) -> str:
    return "\n\n".join(f"[资料{i + 1}]\n{str(c)[:600]}"
                       for i, c in enumerate(contexts))


# ── 四指标实现 ────────────────────────────────────────────────────────────

def faithfulness(question: str, answer: str, contexts: list[str]) -> dict:
    """忠实度：断言级支持率。无断言/解析失败时 score=None 并给 note。"""
    out = _ask_judge(_FAITHFUL_PROMPT.format(
        contexts=_fmt_contexts(contexts), answer=(answer or "")[:1500]))
    claims = (out or {}).get("claims")
    if not isinstance(claims, list) or not claims:
        return {"score": None, "note": "未解析到有效断言"}
    supported = sum(1 for c in claims
                    if isinstance(c, dict) and c.get("supported") is True)
    return {"score": round(supported / len(claims), 4),
            "claims_total": len(claims), "claims_supported": supported}


def answer_relevancy(question: str, answer: str) -> dict:
    """答案相关性：反向问题与原问题的 embedding 相似度均值。"""
    out = _ask_judge(_RELEVANCY_PROMPT.format(answer=(answer or "")[:1200]))
    qs = (out or {}).get("questions")
    if not isinstance(qs, list):
        return {"score": None, "note": "未解析到反向问题"}
    qs = [str(q).strip() for q in qs if str(q).strip()][:3]
    if not qs:
        return {"score": None, "note": "反向问题为空"}
    vecs = embed([question] + qs)
    sims = [_cosine(vecs[0], v) for v in vecs[1:]]
    return {"score": round(sum(sims) / len(sims), 4),
            "generated_questions": qs}


def context_precision(question: str, contexts: list[str]) -> dict:
    """上下文精确率：逐条相关性判定的平均精确率（AP，排名加权）。"""
    if not contexts:
        return {"score": None, "note": "无上下文"}

    def _judge(ctx: str) -> bool:
        out = _ask_judge(_CTX_RELEVANT_PROMPT.format(
            question=question, context=str(ctx)[:800]))
        return bool(out and out.get("relevant") is True)

    # 受控并行：逐条判官调用是纯 RTT 等待，与 embed 分批同策略
    with ThreadPoolExecutor(max_workers=4) as ex:
        rels = list(ex.map(_judge, contexts))

    hits, ap_sum = 0, 0.0
    for i, rel in enumerate(rels, 1):
        if rel:
            hits += 1
            ap_sum += hits / i          # P@i × rel_i
    total_rel = sum(1 for r in rels if r)
    if total_rel == 0:
        return {"score": 0.0, "relevances": rels}
    return {"score": round(ap_sum / total_rel, 4), "relevances": rels}


def context_recall(expected_answer: str, contexts: list[str]) -> dict:
    """上下文召回率：标准答案逐句的可归因比例。"""
    sentences = _split_sentences(expected_answer)
    if not sentences:
        return {"score": None, "note": "无参考答案或无可切分句子"}

    def _judge(sentence: str) -> bool:
        out = _ask_judge(_RECALL_PROMPT.format(
            contexts=_fmt_contexts(contexts), sentence=sentence))
        return bool(out and out.get("attributable") is True)

    with ThreadPoolExecutor(max_workers=4) as ex:
        flags = list(ex.map(_judge, sentences))
    hit = sum(1 for f in flags if f)
    return {"score": round(hit / len(flags), 4),
            "sentences": len(flags), "attributable": hit}


def evaluate_ragas(question: str, answer: str, contexts: list[str],
                   expected_answer: str | None = None) -> dict:
    """跑全部指标并聚合；单指标异常被隔离为 score=None，不影响其余。

    返回结构：
    {
      "metrics": {"faithfulness": {...}, ...},
      "summary": {"avg_score": 0.xx, "scored_metrics": n},
      "meta": {"question": ..., "contexts": n, "elapsed_ms": ...}
    }"""
    t0 = time.time()
    metrics: dict[str, dict] = {}

    def _run(name: str, fn, *args):
        try:
            metrics[name] = fn(*args)
        except Exception as e:  # noqa: BLE001 - 单指标失败不拖垮整体
            logger.warning("RAGAS 指标 %s 执行失败: %s", name, e)
            metrics[name] = {"score": None, "note": f"执行失败: {e}"}

    ctx_list = [str(c) for c in (contexts or []) if str(c).strip()]
    _run("faithfulness", faithfulness, question, answer, ctx_list)
    _run("answer_relevancy", answer_relevancy, question, answer)
    _run("context_precision", context_precision, question, ctx_list)
    if expected_answer:
        _run("context_recall", context_recall, expected_answer, ctx_list)

    scores = [m["score"] for m in metrics.values()
              if isinstance(m.get("score"), (int, float))]
    return {
        "metrics": metrics,
        "summary": {
            "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
            "scored_metrics": len(scores),
        },
        "meta": {"question": question,
                 "contexts": len(ctx_list),
                 "elapsed_ms": int((time.time() - t0) * 1000)},
    }


# ── API 路由注册（管理员） ────────────────────────────────────────────────

def register_ragas_routes(app) -> None:
    """挂载 RAGAS 评估端点：POST 单条评估 + GET 指标元信息。"""
    import fastapi
    from docmind.deps import RequireAdmin
    from fastapi.responses import JSONResponse

    @app.get("/api/admin/eval/ragas/meta", include_in_schema=False)
    async def _ragas_meta(request: fastapi.Request, _user: RequireAdmin):
        return JSONResponse({
            "metrics": [
                {"key": "faithfulness", "name": "忠实度",
                 "desc": "答案断言被检索上下文支持的占比（防幻觉核心）"},
                {"key": "answer_relevancy", "name": "答案相关性",
                 "desc": "反向生成问题与原问题的 embedding 相似度均值"},
                {"key": "context_precision", "name": "上下文精确率",
                 "desc": "召回上下文的相关性平均精确率（排名加权）"},
                {"key": "context_recall", "name": "上下文召回率",
                 "desc": "标准答案要点被召回上下文覆盖的比例"},
            ],
        })

    @app.post("/api/admin/eval/ragas", include_in_schema=False)
    async def _ragas_run(request: fastapi.Request, _user: RequireAdmin):
        from fastapi import HTTPException
        body = await request.json()
        question = str(body.get("question") or "").strip()
        answer = str(body.get("answer") or "").strip()
        contexts = body.get("contexts") or []
        if not question or not answer:
            raise HTTPException(status_code=400,
                                detail="question 与 answer 必填")
        if not isinstance(contexts, list):
            raise HTTPException(status_code=400,
                                detail="contexts 必须是字符串数组")
        result = evaluate_ragas(
            question, answer, contexts,
            expected_answer=(str(body["expected_answer"]).strip()
                             if body.get("expected_answer") else None))
        return JSONResponse(result)