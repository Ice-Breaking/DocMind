#!/usr/bin/env bash
# 在云服务器（Ubuntu/Debian/CentOS）上运行：一键部署 DocMind
# 用法：先把整个项目传到服务器，再执行本脚本
#   Mac 上执行：scp -r /path/to/chat-1 root@<服务器IP>:/opt/docmind
#   服务器上执行：cd /opt/docmind && bash scripts/deploy_server.sh
set -e
cd "$(dirname "$0")/.."

echo "==> [1/3] 检查 Docker"
if ! command -v docker >/dev/null 2>&1; then
  echo "    安装 Docker..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "    安装 docker compose 插件..."
  sudo apt-get update -y && sudo apt-get install -y docker-compose-plugin || \
  sudo yum install -y docker-compose-plugin
fi

echo "==> [2/3] 检查 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    已生成 .env，请填写 DASHSCOPE_API_KEY 与 ADMIN_PASSWORD 后重新执行本脚本"
  exit 1
fi
grep -q 'DASHSCOPE_API_KEY=.\+' .env || { echo "    请先在 .env 填写 DASHSCOPE_API_KEY"; exit 1; }

echo "==> [3/3] 构建并启动（首次约 3-5 分钟）"
sudo docker compose up -d --build

echo ""
echo "✅ 部署完成！访问：http://$(curl -s ifconfig.me || echo '<服务器公网IP>')"
echo "   查看日志：sudo docker compose logs -f"
echo "   停止服务：sudo docker compose down"
