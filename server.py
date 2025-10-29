import os
import sys
import re
import traceback
import logging
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from supabase import create_client, Client

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import Response  # 【修复2】: 导入 Response 类
from mcp.server.sse import SseServerTransport

# --- 1. 日志和环境配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)
load_dotenv()

# --- 2. Supabase 客户端初始化 ---
SUPABASE_URL: Optional[str] = os.environ.get("SUPABASE_URL")
SUPABASE_KEY: Optional[str] = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("错误：环境变量 SUPABASE_URL 或 SUPABASE_KEY 未设置。")
    sys.exit(1)

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("Supabase 客户端初始化成功。")
except Exception as e:
    logging.error(f"Supabase 客户端初始化失败: {e}")
    sys.exit(1)

# --- 3. MCP & FastAPI 初始化 ---
mcp = FastMCP("Supabase PE Tool")
app = FastAPI(
    title="Supabase PE Tool MCP API",
    description="一个通过 FastAPI 暴露的用于查询 Supabase PE 数据的 MCP 工具。",
    version="1.0.0"
)

# --- 4. 核心辅助函数 ---
def _convert_to_supabase_format(ts_code: str) -> Optional[str]:
    """
    将 Tushare 格式的股票代码 (如 '000603.SZ') 转换为 Supabase 使用的格式 (如 'sz000603')。
    如果格式不匹配，返回 None。
    """
    match = re.match(r'^(\d{6})\.(SH|SZ)$', ts_code, re.IGNORECASE)
    if match:
        code, market = match.groups()
        return f"{market.lower()}{code}"
    # 也支持直接输入 'sz000603' 格式
    if re.match(r'^(sh|sz)\d{6}$', ts_code, re.IGNORECASE):
        return ts_code.lower()
    return None

# --- 5. MCP 工具定义 ---
@mcp.tool()
def get_pe_percentile(stock_code: str) -> str:
    """
    从 Supabase 数据库获取指定股票的近三年PE历史分位数据。
    支持 Tushare 格式 (如 '000603.SZ') 和 Supabase 格式 (如 'sz000603')。

    参数:
        stock_code: 股票代码，用于查询的唯一标识。
    """
    logging.info(f"调用工具 get_pe_percentile，原始输入: {stock_code}")
    if not stock_code:
        return "错误：必须提供股票代码 (stock_code)。"

    supabase_code = _convert_to_supabase_format(stock_code)
    if not supabase_code:
        return f"错误：输入的股票代码 '{stock_code}' 格式无效。请使用 '000603.SZ' 或 'sz000603' 格式。"

    try:
        # 使用你提供的正确列名 'stock_code'
        response = supabase.table('stocks').select('pe_percentile_3y').eq('stock_code', supabase_code).execute()
        
        if response.data:
            # 【修复1】: 增加类型检查，确保 response.data[0] 是字典
            first_record = response.data[0]
            if isinstance(first_record, dict):
                pe_value = first_record.get('pe_percentile_3y')
                if pe_value is not None:
                    return f"股票 {stock_code} ({supabase_code}) 的近三年PE历史分位为: {pe_value:.4f}"
                else:
                    return f"找到了股票 {stock_code} ({supabase_code})，但其 'pe_percentile_3y' 字段为空。"
            else:
                return f"数据库返回了非预期的格式 for {stock_code} ({supabase_code})。"
        else:
            return f"未在数据库中找到股票代码为 {stock_code} ({supabase_code}) 的记录。"
            
    except Exception as e:
        logging.error(f"查询 Supabase 时出错 (代码: {supabase_code}): {e}")
        traceback.print_exc(file=sys.stderr)
        return f"查询失败：{str(e)}"

# --- 6. FastAPI 端点和服务器挂载 ---
@app.get("/")
async def read_root():
    return {"message": "Supabase PE Tool is running!"}

# --- MCP SSE Workaround Integration ---
MCP_BASE_PATH = "/mcp"
try:
    messages_full_path = f"{MCP_BASE_PATH}/messages/"
    sse_transport = SseServerTransport(messages_full_path)
    # 【修复2】: 修改函数签名以匹配 FastAPI 的期望
    async def handle_mcp_sse_handshake(request: Request) -> Response:
        async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
            await mcp._mcp_server.run(read_stream, write_stream, mcp._mcp_server.create_initialization_options())
        # 虽然连接已被接管，但为满足类型检查器，返回一个名义上的响应
        return Response(status_code=204)

    app.add_route(MCP_BASE_PATH, handle_mcp_sse_handshake, methods=["GET"])
    app.mount(messages_full_path, sse_transport.handle_post_message)
except Exception as e:
    logging.critical(f"应用MCP SSE workaround时发生严重错误: {e}")
    traceback.print_exc(file=sys.stderr)

# --- 7. 服务器执行 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)