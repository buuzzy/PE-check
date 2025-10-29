import os
import sys
import re
import logging
import functools
from typing import Optional, Dict, Any, Callable

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from supabase import create_client, Client
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import Response
from mcp.server.sse import SseServerTransport

# --- 1. 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)

# --- 2. 错误处理装饰器 ---
def supabase_tool_handler(func: Callable) -> Callable:
    """统一处理 Supabase 查询的错误和日志"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logging.info(f"调用工具: {func.__name__}，参数: {kwargs}")
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f"查询出错: {e}", exc_info=True)
            return f"查询失败: {str(e)}"
    return wrapper

# --- 3. 初始化 ---
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PORT = int(os.environ.get("PORT", 8080))

# 环境变量检查
if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("环境变量 SUPABASE_URL 或 SUPABASE_KEY 未设置")
    sys.exit(1)

assert isinstance(SUPABASE_URL, str), "SUPABASE_URL 必须是字符串"
assert isinstance(SUPABASE_KEY, str), "SUPABASE_KEY 必须是字符串"

# Supabase 客户端初始化
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("Supabase 客户端初始化成功")
except Exception as e:
    logging.error(f"Supabase 初始化失败: {e}", exc_info=True)
    sys.exit(1)

# FastAPI & MCP 初始化
app = FastAPI(
    title="PE分位数查询工具",
    version="1.0.0",
    description="查询股票近三年PE历史分位数的工具"
)
mcp = FastMCP("PE Query Tool")

def normalize_stock_code(code: str) -> Optional[str]:
    """验证股票代码格式是否符合标准 (sh600739 或 sz301011)
    
    Args:
        code: 输入的股票代码
        
    Returns:
        Optional[str]: 格式正确则返回小写的代码，否则返回 None
    """
    code = code.strip().lower()
    if re.match(r'^(sh|sz)\d{6}$', code):
        return code
    return None

@mcp.tool()
@supabase_tool_handler
def get_pe_percentile(stock_code: str) -> str:
    """查询股票PE分位数
    
    Args:
        stock_code: 股票代码，如 'sh600739' 或 'sz301011'
    """
    if not (normalized_code := normalize_stock_code(stock_code)):
        return f"股票代码格式错误：'{stock_code}'。请使用标准格式，如：sh600739 或 sz301011"
    
    response = supabase.table('stocks') \
        .select('stock_code, stock_name, pe_percentile_3y') \
        .eq('stock_code', normalized_code) \
        .execute()
    
    if not response.data:
        return f"未找到股票：{normalized_code}"
        
    stock_data = response.data[0]
    pe_value = stock_data.get('pe_percentile_3y')
    stock_name = stock_data.get('stock_name', '未知')
    
    if pe_value is None:
        return f"股票 {normalized_code}（{stock_name}）暂无PE分位数据"
        
    return f"股票 {normalized_code}（{stock_name}）的近三年PE分位：{pe_value:.4f}"

@app.get("/")
async def health_check() -> Dict[str, str]:
    """健康检查端点"""
    return {"status": "healthy"}

# --- MCP SSE 集成 (最终修正版) ---
MCP_BASE_PATH = "/mcp"
try:
    messages_full_path = f"{MCP_BASE_PATH}/messages/"
    # 创建一个单一的 transport 实例来管理所有会话
    sse_transport = SseServerTransport(messages_full_path)

    # 这个函数处理初始的 GET 请求，它必须返回 Response 类型以兼容 FastAPI
    async def handle_mcp_sse_handshake(request: Request) -> Response:
        """
        处理 MCP 的 SSE 握手。
        此函数将连接的控制权交给 sse_transport，后者负责维持长连接并发送事件流。
        """
        # `connect_sse` 上下文管理器会接管底层的 ASGI 连接。
        # 在客户端断开连接之前，代码会一直停留在 `with` 块内部。
        async with sse_transport.connect_sse(
            scope=request.scope,
            receive=request.receive,
            send=request._send,
        ) as (read_stream, write_stream):
            await mcp._mcp_server.run(
                read_stream,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )
        
        # 当客户端断开连接后, `with` 块会退出。
        # 我们必须在这里返回一个 Response 对象来满足 FastAPI 框架的要求。
        # 状态码 204 (No Content) 是最合适的，因为所有通信都已通过 SSE 流完成。
        return Response(status_code=204)

    @mcp.prompt()
    def usage_guide() -> str:
        """提供使用指南"""
        return """欢迎使用 PE 分位数查询工具！

股票代码格式说明：
- 上海证券交易所：sh + 6位数字，如 sh600739
- 深圳证券交易所：sz + 6位数字，如 sz301011

示例查询：
> get_pe_percentile("sh600739")  # 新华百货
> get_pe_percentile("sz301011")  # 华立新材
"""

    # 注册路由
    # 使用 add_api_route 注册 GET 握手端点
    app.add_api_route(MCP_BASE_PATH, handle_mcp_sse_handshake, methods=["GET"])
    # 挂载 ASGI 应用来处理 POST 消息
    app.mount(messages_full_path, sse_transport.handle_post_message)
    
    logging.info("MCP SSE 集成设置完成")

except Exception as e:
    logging.critical(f"应用 MCP SSE 设置时发生严重错误: {e}", exc_info=True)
    sys.exit(1)

if __name__ == "__main__":
    logging.info(f"启动服务器，监听端口: {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)