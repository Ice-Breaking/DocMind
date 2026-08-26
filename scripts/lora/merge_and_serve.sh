#!/bin/bash
# LoRA 微调实验 · 第二步：合并 adapter → 导入 Ollama 托管
#
# 前置：已完成 llamafactory-cli train scripts/lora/lora_qwen_rewrite.yaml
# 产物：saves/qwen2.5-1.5b-rewrite/lora-sft（adapter）
#
# 用法：bash scripts/lora/merge_and_serve.sh
set -euo pipefail
cd "$(dirname "$0")/../.."   # 项目根

ADAPTER_DIR="${ADAPTER_DIR:-saves/qwen2.5-1.5b-rewrite/lora-sft}"
BASE_MODEL="${BASE_MODEL:-models/Qwen2.5-1.5B-Instruct}"   # 本地权重（ModelScope 预下载，见 README 步骤 0）
MERGE_DIR="${MERGE_DIR:-saves/qwen2.5-1.5b-rewrite/merged}"
LLAMA_CLI="${LLAMA_CLI:-$(pwd)/.venv-lora/bin/llamafactory-cli}"
# 注入指令文本只需 import docmind.rag.eval_set（连带 chromadb），用项目 venv
PYBIN="${PYBIN:-$(pwd)/.venv/bin/python}"
# GGUF 转换依赖 torch/transformers，用 LoRA 训练 venv
CONV_PY="${CONV_PY:-$(pwd)/.venv-lora/bin/python}"

echo "== 1/3 合并 LoRA adapter 到底座权重 =="
# LLaMA-Factory 导出即合并：产出完整 safetensors 目录
cat > /tmp/merge_rewrite.yaml <<EOF
### model
model_name_or_path: ${BASE_MODEL}
adapter_name_or_path: ${ADAPTER_DIR}
template: qwen
finetuning_type: lora

### export
export_dir: ${MERGE_DIR}
export_size: 2
export_device: cpu
export_legacy_format: false
EOF
"${LLAMA_CLI}" export /tmp/merge_rewrite.yaml
echo "   已合并 → ${MERGE_DIR}"

echo "== 2/4 转 GGUF（Ollama 只认 GGUF，不认 safetensors 目录）=="
# convert_hf_to_gguf.py 取自 llama.cpp master（脚本与 gguf-py 需同版本），
# 放置位置与依赖安装见 README 步骤 0；F16 精度足够（改写是窄任务）
CONVERT_SCRIPT="${CONVERT_SCRIPT:-/tmp/llamacpp-src/llama.cpp-master/convert_hf_to_gguf.py}"
GGUF_FILE="saves/qwen2.5-1.5b-rewrite/rewrite-f16.gguf"
PYTHONPATH="$(dirname "${CONVERT_SCRIPT}")/gguf-py" "${CONV_PY}" "${CONVERT_SCRIPT}" \
    "${MERGE_DIR}" --outfile "${GGUF_FILE}" --outtype f16
echo "   已转换 → ${GGUF_FILE}"

echo "== 3/4 写 Ollama Modelfile =="
# 注意：ollama create 的 FROM 不支持相对路径，必须用绝对路径
GGUF_ABS="$(pwd)/${GGUF_FILE}"
cat > scripts/lora/Modelfile.rewrite <<EOF
# 由 merge_and_serve.sh 自动生成；FROM 指向合并后导出的 GGUF 文件
FROM ${GGUF_ABS}
PARAMETER temperature 0.1
PARAMETER num_predict 128
SYSTEM """__INSTRUCTION_PLACEHOLDER__"""
EOF
# 注入与训练一致的指令文本（保证 serving 与 SFT 分布一致）
"${PYBIN}" - <<'PY'
import re, sys
sys.path.insert(0, ".")
from scripts.lora.gen_rewrite_data import INSTRUCTION
p = "scripts/lora/Modelfile.rewrite"
s = open(p).read().replace("__INSTRUCTION_PLACEHOLDER__", INSTRUCTION)
open(p, "w").write(s)
print("   Modelfile.rewrite 就绪")
PY

echo "== 4/4 注册到 Ollama =="
ollama create qwen2.5-rewrite-lora -f scripts/lora/Modelfile.rewrite
echo ""
echo "✅ 完成。验证：ollama run qwen2.5-rewrite-lora \"帮我问下啥是LoRA呀？\""
echo "   下一步 A/B 评测：.venv/bin/python scripts/lora/eval_rewrite.py"