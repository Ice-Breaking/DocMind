# DocMind 一键部署镜像
# 构建：docker build -t docmind .
# 运行：docker run --env-file .env -p 7860:7860 -v docmind-data:/app/data docmind
FROM python:3.11-slim

WORKDIR /app

# 依赖层单独缓存，改代码不用重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 应用代码 + MCP Server + 知识库
COPY docmind ./docmind
COPY mcp_servers ./mcp_servers
COPY docs ./docs

# 容器内监听所有网卡（否则宿主机映射访问不到）
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    PYTHONUNBUFFERED=1

EXPOSE 7860

CMD ["python", "-m", "docmind.app"]
