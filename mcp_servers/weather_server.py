"""示例 MCP Server：天气查询（用官方 FastMCP 快速实现）。

启动方式（stdio 模式，由 DocMind 主程序自动拉起）：
    python mcp_servers/weather_server.py

扩展建议（面试可讲）：
- 真实场景可对接和风天气 / OpenWeatherMap API
- 这里用模拟数据，保证项目离线可演示
"""
import random

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

_WEATHERS = ["晴", "多云", "小雨", "阴", "大风"]


@mcp.tool()
def get_weather(city: str) -> str:
    """查询指定城市的当前天气（温度与天气状况）"""
    # 模拟数据：同一城市多次查询保持稳定
    seed = sum(ord(c) for c in city)
    random.seed(seed)
    temp = random.randint(-5, 35)
    weather = random.choice(_WEATHERS)
    return f"{city}当前天气：{weather}，气温 {temp}°C（模拟数据）"


if __name__ == "__main__":
    mcp.run()  # 默认 stdio 传输
