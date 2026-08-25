# DocMind 一键部署镜像
# 构建：docker build -t docmind .
# 运行：docker compose up -d
#   或：docker run --env-file .env -p 7860:7860 -v docmind-data:/app/data docmind
FROM python:3.11-slim

WORKDIR /app

# LibreOffice headless（docx→PDF 保真预览）+ 中文字体（缺字体会转码豆腐块）
# libreoffice-writer-nogui 仅装 Writer 组件，比完整 libreoffice 小很多；
# apt 换国内镜像提速（海外环境可删掉 sed 那行）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 依赖层单独缓存，改代码不用重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 应用代码 + MCP Server + 运行时必需的 docs 子集
# （只拷 KNOWLEDGE_DIR 知识库语料与 glossary.md 术语表；
#   UI 截图等纯文档资产不入镜像，省约 2MB）
COPY docmind ./docmind
COPY mcp_servers ./mcp_servers
COPY docs/knowledge ./docs/knowledge
COPY docs/glossary.md ./docs/glossary.md

# 构建指纹：对镜像内全部 Python 源码取哈希，/health 暴露——
# 防止「运行容器 ≠ 工作区代码」的部署漂移（QA 实测发现：容器跑着
# 11 小时前旧镜像而无人察觉）。本地开发（非镜像）回退实时计算
RUN python -c "import hashlib,glob; h=hashlib.sha256(); [h.update(open(f,'rb').read()) for f in sorted(glob.glob('docmind/**/*.py', recursive=True)+glob.glob('mcp_servers/**/*.py', recursive=True))]; open('/app/.build_fingerprint','w').write(h.hexdigest()[:12])"

# 容器内监听所有网卡（否则宿主机映射访问不到）
ENV DOCMIND_HOST=0.0.0.0 \
    DOCMIND_PORT=7860 \
    PYTHONUNBUFFERED=1

# 非 root 运行：容器逃逸纵深防御。LibreOffice 转码/OCR 临时文件走 /tmp，
# 端口 7860 为非特权端口；data 卷(named volume)首次挂载沿用镜像内属主。
# 宿主 bind mount 场景需自行保证目录可写(chown -R 10001)
RUN useradd --system --uid 10001 docmind \
    && mkdir -p /app/data \
    && chown -R docmind:docmind /app/data
USER docmind

EXPOSE 7860

# 健康检查：/health 端点检查数据库/磁盘/知识库状态（不依赖登录态）
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=3)" || exit 1

CMD ["python", "-u", "-m", "docmind.app"]
