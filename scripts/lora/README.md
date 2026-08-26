# LoRA 微调实验：检索查询改写器（P1-3）

## 为什么做这个（面试一句话版）

> 知识库检索对规范提问友好，真实用户口语化/中英混杂的提问会拉低向量召回。
> 我用 LoRA 微调了一个 Qwen2.5-1.5B 小模型专职做查询改写，跑在本地 Ollama 上，
> 替代"每次问答都让云端大模型顺手改写"——改写环节零 API 成本、亚秒级延迟，
> 且微调前后 Recall@k 有可复现的对比数字。

## 流水线总览

```
gen_rewrite_data.py          llamafactory-cli train        merge_and_serve.sh         eval_rewrite.py
 ┌─────────────────┐   →    ┌──────────────────┐   →   ┌──────────────┐   →   ┌────────────────┐
 │ eval_set 种子问题 │       │ LoRA-SFT 1.5B     │       │ 合并 adapter  │       │ 基线 vs 微调    │
 │ 确定性加噪变体    │       │ rank8 α16 lr1e-4  │       │ Ollama 托管   │       │ Recall@k 对比  │
 └─────────────────┘       └──────────────────┘       └──────────────┘       └────────────────┘
```

## 步骤

### 0. 环境

> 已在本机装好（macOS arm64）：`.venv-lora/` 独立 venv，Python 3.11.15（uv 版，
> 自带 `_lzma`），llamafactory 0.9.3 + torch 2.13.0(MPS) + peft 0.15.2。
> 直接跳到步骤 1；重装时参考下述命令。

```bash
# pyenv 编译的 Python 缺 _lzma 会炸 datasets 导入，必须用 uv 的完整构建
uv python install 3.11 && uv venv .venv-lora --python 3.11
VIRTUAL_ENV=$PWD/.venv-lora uv pip install "llamafactory[torch,metrics]==0.9.3" \
    --index-url https://mirrors.aliyun.com/pypi/simple/
# 底座权重走 ModelScope（Qwen 官方源，实测 25MB/s；hf-mirror 对新版 hub 校验不过）
VIRTUAL_ENV=$PWD/.venv-lora uv pip install modelscope \
    --index-url https://mirrors.aliyun.com/pypi/simple/
.venv-lora/bin/modelscope download --model Qwen/Qwen2.5-1.5B-Instruct \
    --local_dir models/Qwen2.5-1.5B-Instruct          # 预下载底座 ~3GB → yaml 直接用本地路径
ollama --version                                    # 推理托管（已有）
```

### 1. 生成训练数据（离线零成本）

```bash
.venv/bin/python scripts/lora/gen_rewrite_data.py                 # 确定性变体 ~300 条
.venv/bin/python scripts/lora/gen_rewrite_data.py --augment 2     # 追加 LLM 自然变体
```

产物：`data/lora/query_rewrite_{train,test}.jsonl` + `dataset_info.json`。
加噪逻辑固定随机种子——任何人重跑得到完全相同的数据集（评测纪律）。

### 2. 训练（M4 24GB 可跑）

```bash
HF_ENDPOINT=https://hf-mirror.com .venv-lora/bin/llamafactory-cli train \
    scripts/lora/lora_qwen_rewrite.yaml
# 约 10-20 分钟；loss 曲线在 saves/qwen2.5-1.5b-rewrite/lora-sft/training_loss.png
```

### 3. 合并并导入 Ollama

```bash
bash scripts/lora/merge_and_serve.sh
ollama run qwen2.5-rewrite-lora "帮我问下啥是LoRA呀？"   # 应输出「什么是 LoRA？」
```

脚本做四件事：合并 adapter → 转 GGUF → 生成 Modelfile → `ollama create`。
两个实测踩坑（已固化在脚本里）：

- **Ollama 只认 GGUF**，`FROM safetensors目录` 会报
  `400 Bad Request: invalid model name`。需先用 llama.cpp 的
  `convert_hf_to_gguf.py` 转换（脚本默认取
  `/tmp/llamacpp-src/llama.cpp-master/convert_hf_to_gguf.py`，获取方式：
  `curl -sSL -o /tmp/lcpp.tgz https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/heads/master
  && mkdir -p /tmp/llamacpp-src && tar xzf /tmp/lcpp.tgz -C /tmp/llamacpp-src`，
  另需 `uv pip install gguf sentencepiece` 进 `.venv-lora`）；
- **Modelfile 的 FROM 不支持相对路径**，必须写绝对路径（脚本已处理）。

### 4. A/B 对比评测（产出面试数字）

```bash
.venv/bin/python scripts/lora/eval_rewrite.py \
    --baseline-model qwen2.5:1.5b --tuned-model qwen2.5-rewrite-lora \
    --report data/lora/ab_report.json
```

前置：知识库已建索引 + `ollama pull qwen2.5:1.5b`（基线，~1GB）。

输出两组 Recall@4 与平均改写延迟、未命中样例分析、常规/困难分组对比。
**报告里的每个数字都由本命令生成**，简历只写脚本可复现的值。

### 本机实测结果（2026-08-26，126 样本 = 47 题 × 3 变体，top_k=4）

| 口径 | 基线 qwen2.5:1.5b | 微调 qwen2.5-rewrite-lora | Δ |
|---|---|---|---|
| 总体 Recall@4 | 0.754 | **0.794** | **+3.97pp** |
| 常规集（n=81） | 0.827 | 0.889 | **+6.17pp** |
| 困难集（n=45） | 0.622 | 0.622 | ±0 |

平均改写延迟：基线 270ms / 微调 402ms（本地 Ollama，F16）。

**困难集零提升的归因（面试加分点）**：对 HARD_SET 规范问题直接检索
（完全不经改写器）Recall 仅 0.588——瓶颈在示例知识库的内容覆盖
（6 文档/12 chunks，不含 MCP、端口冲突等主题），而非改写质量；
且微调版改写后的 0.622 高于人工规范问法直检的 0.588，说明小模型
改写器在最难样本上已不输人工。扩充知识库后改写收益才会进一步放大。

## 目录结构

| 文件 | 说明 |
|---|---|
| `gen_rewrite_data.py` | 训练数据构造（种子+确定性加噪+可选 LLM 增强） |
| `lora_qwen_rewrite.yaml` | LLaMA-Factory 训练配置 |
| `merge_and_serve.sh` | adapter 合并 → Ollama 导入 一键执行 |
| `Modelfile.rewrite` | （脚本生成）Ollama 模型描述文件 |
| `eval_rewrite.py` | 改写器 A/B 对比：Recall@k + 延迟 |

## 面试追问预案

- **为什么不直接 prompt 大模型改写？** 成本与延迟：每请求多一次云调用；
  专用小模型本地部署后该环节边际成本为零。且窄任务微调后风格稳定可控。
- **为什么选查询改写而不是微调对话模型？** 数据可得性（eval_set 天然提供
  规范问题）+ 效果可度量（Recall@k 直接量化收益）+ 任务窄（1.5B 足够）。
- **过拟合怎么防？** 低秩(rank8)+dropout 0.05+仅 3 epoch+留出测试集监控；
  加噪多样性覆盖寒暄/口语/英文/错字四类分布偏移。
