import os
import sys
import re
import logging
from typing import Optional, Dict, Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from supabase import create_client, Client
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import Response
from mcp.server.sse import SseServerTransport

# --- 1. 配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- 2. 初始化 ---
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PORT = int(os.environ.get("PORT", 8080))  # 统一使用 8080 端口

# 类型安全检查
if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("环境变量 SUPABASE_URL 或 SUPABASE_KEY 未设置")
    sys.exit(1)

# 确保类型安全
assert isinstance(SUPABASE_URL, str), "SUPABASE_URL 必须是字符串"
assert isinstance(SUPABASE_KEY, str), "SUPABASE_KEY 必须是字符串"

# Supabase 客户端
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logging.error(f"Supabase 初始化失败: {e}")
    sys.exit(1)

# FastAPI & MCP
app = FastAPI(title="PE分位数查询工具", version="1.0.0")
mcp = FastMCP("PE Query Tool")

def normalize_stock_code(code: str) -> Optional[str]:
    """统一股票代码格式为 Supabase 格式 (sh600739 或 sz301011)"""
    # Tushare 格式转换 (000603.SZ -> sz000603)
    if match := re.match(r'^(\d{6})\.(SH|SZ)$', code, re.IGNORECASE):
        code, market = match.groups()
        return f"{market.lower()}{code}"
    
    # 直接格式检查 (已经是 sh600739 或 sz301011 格式)
    if re.match(r'^(sh|sz)\d{6}$', code, re.IGNORECASE):
        return code.lower()
    
    return None

@mcp.tool()
def get_pe_percentile(stock_code: str) -> str:
    """查询股票PE分位数
    
    Args:
        stock_code: 支持 '000603.SZ' 或 'sz000603' 格式
    """
    logging.info(f"查询股票: {stock_code}")
    
    if not (supabase_code := normalize_stock_code(stock_code)):
        return f"股票代码 '{stock_code}' 格式错误，请使用 '000603.SZ' 或 'sz000603' 格式"
    
    try:
        response = supabase.table('stocks') \
            .select('pe_percentile_3y') \
            .eq('stock_code', supabase_code) \
            .execute()
        
        if not response.data:
            return f"未找到股票: {stock_code}"
            
        pe_value = response.data[0].get('pe_percentile_3y')
        if pe_value is None:
            return f"股票 {stock_code} 的PE分位数据不存在"
            
        return f"股票 {stock_code} 的近三年PE分位: {pe_value:.4f}"
        
    except Exception as e:
        logging.error(f"查询出错: {e}")
        return f"查询失败: {str(e)}"

@app.get("/")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy"}

# --- MCP SSE 集成 ---
MCP_BASE_PATH = "/mcp"
try:
    messages_full_path = f"{MCP_BASE_PATH}/messages/"
    sse_transport = SseServerTransport(messages_full_path)
    
    async def handle_mcp_sse_handshake(request: Request) -> Response:  # 修改返回类型
        async with sse_transport.connect_sse(
            request.scope, 
            request.receive, 
            request._send
        ) as (read_stream, write_stream):
            await mcp._mcp_server.run(
                read_stream, 
                write_stream, 
                mcp._mcp_server.create_initialization_options()
            )
        return Response(status_code=204)  # 返回响应对象

    # 添加提示信息
    @mcp.prompt()
    def usage_guide() -> str:
        """提供使用指南"""
        return """欢迎使用 PE 分位数查询工具！

支持的股票代码格式:
1. Tushare 格式: '600739.SH' 或 '301011.SZ'
2. 标准格式: 'sh600739' 或 'sz301011'

示例查询:
> get_pe_percentile("sh600739")  # 新华百货
> get_pe_percentile("600739.SH")  # 新华百货
> get_pe_percentile("sz301011")  # 华立新材
> get_pe_percentile("301011.SZ")  # 华立新材
"""

    # 路由注册
    app.add_route(MCP_BASE_PATH, handle_mcp_sse_handshake, methods=["GET"])
    app.mount(messages_full_path, sse_transport.handle_post_message)
except Exception as e:
    logging.critical(f"应用 MCP SSE 设置时发生严重错误: {e}")
    sys.exit(1)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)