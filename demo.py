import sys
import traceback
import os
import logging
import functools
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable

import tushare as ts
import pandas as pd
import uvicorn
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException, Body

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from mcp.server.sse import SseServerTransport

# --- 1. Setup & Configuration ---

# 配置日志系统
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)

# 环境变量文件路径
ENV_FILE = Path("/tmp") / ".tushare_env"


# --- 2. Core Helper Functions ---

def init_env_file():
    """初始化环境变量文件及其目录。"""
    try:
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not ENV_FILE.exists():
            ENV_FILE.touch()
        load_dotenv(ENV_FILE)
    except Exception as e:
        logging.error(f"初始化环境文件失败: {e}")
        traceback.print_exc(file=sys.stderr)

def get_tushare_token() -> Optional[str]:
    """从环境中获取Tushare token。"""
    init_env_file()
    return os.getenv("TUSHARE_TOKEN")

def set_tushare_token(token: str):
    """在环境文件中设置Tushare token。"""
    init_env_file()
    try:
        set_key(ENV_FILE, "TUSHARE_TOKEN", token)
        ts.set_token(token)
    except Exception as e:
        logging.error(f"设置Tushare token失败: {e}")

def _get_stock_name(pro_api_instance, ts_code: str) -> str:
    """根据ts_code获取股票名称的辅助函数。"""
    if not pro_api_instance:
        return ts_code
    try:
        df_basic = pro_api_instance.stock_basic(ts_code=ts_code, fields='ts_code,name')
        if not df_basic.empty:
            return df_basic.iloc[0]['name']
    except Exception as e:
        logging.warning(f"获取股票名称失败 {ts_code}: {e}")
    return ts_code

def _get_latest_report_df(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """从DataFrame中筛选出最新报告期的数据。"""
    if df.empty:
        return None
    latest_end_date = df['end_date'].iloc[0]
    return df[df['end_date'] == latest_end_date]


# --- 3. Decorators for Tools ---

def tushare_tool_handler(func: Callable) -> Callable:
    """
    一个用于MCP工具的装饰器，自动处理token获取、API初始化和异常捕获。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logging.info(f"调用工具: {func.__name__}，参数: {kwargs}")
        token_value = get_tushare_token()
        if not token_value:
            return "错误：Tushare token 未配置。请先进行配置。"
        
        try:
            pro = ts.pro_api(token_value)
            
            # --- 关键修复：直接在kwargs中注入或覆盖pro和stock_name ---
            kwargs['pro'] = pro
            ts_code = kwargs.get('ts_code')
            if ts_code:
                kwargs['stock_name'] = _get_stock_name(pro, ts_code)
            
            # 使用更新后的kwargs调用函数
            return func(*args, **kwargs)
            
        except Exception as e:
            logging.error(f"工具 {func.__name__} 执行出错: {e}")
            traceback.print_exc(file=sys.stderr)
            return f"查询失败：{str(e)}"
            
    return wrapper


# --- 4. MCP & FastAPI Initialization ---

mcp = FastMCP("Tushare Tools Optimized")
app = FastAPI(
    title="Tushare MCP API (Optimized)",
    description="An optimized remote API for Tushare MCP tools via FastAPI.",
    version="1.0.0"
)


# --- 5. MCP Tools ---

@mcp.prompt()
def configure_token() -> str:
    """提供配置Tushare token的提示模板。"""
    return """请提供您的Tushare API token。
您可以在 https://tushare.pro/user/token 获取您的token。
如果您还没有Tushare账号，请先在 https://tushare.pro/register 注册。

请输入您的token:"""

@mcp.tool()
def setup_tushare_token(token: str) -> str:
    """设置并验证Tushare API token。"""
    if not token or not isinstance(token, str):
        return "Token无效，请输入一个有效的字符串。"
    try:
        set_tushare_token(token)
        ts.pro_api(token)
        return "Token配置成功！您现在可以使用Tushare的API功能了。"
    except Exception as e:
        logging.error(f"Token验证失败: {e}")
        return f"Token配置失败：{e}"

@mcp.tool()
def check_token_status() -> str:
    """检查已配置的Tushare token的状态和有效性。"""
    token = get_tushare_token()
    if not token:
        return "未配置Tushare token。请使用 setup_tushare_token 来设置您的token。"
    try:
        ts.pro_api(token)
        return "Token配置正常，可以使用Tushare API。"
    except Exception as e:
        logging.error(f"Token状态检查失败: {e}")
        return f"Token无效或已过期。错误信息: {e}"

@mcp.tool()
@tushare_tool_handler
def get_stock_basic_info(pro, ts_code: str = "", name: str = "", **kwargs) -> str:
    """获取股票基本信息。"""
    query_params = {}
    if ts_code: query_params['ts_code'] = ts_code
    if name: query_params['name'] = name
    
    fields = 'ts_code,name,area,industry,list_date,market,exchange,list_status,delist_date'
    df = pro.stock_basic(**query_params, fields=fields)

    if df.empty: return "未找到符合条件的股票"

    results = []
    for _, row in df.head(50).iterrows():
        parts = [f"股票代码: {row.get('ts_code', 'N/A')}", f"股票名称: {row.get('name', 'N/A')}"]
        optional = {'area': '所属地区', 'industry': '所属行业', 'list_date': '上市日期', 'market': '市场类型', 'exchange': '交易所', 'list_status': '上市状态', 'delist_date': '退市日期'}
        for k, label in optional.items():
            if pd.notna(row.get(k)): parts.append(f"{label}: {row[k]}")
        results.append("\n".join(parts) + "\n------------------------")

    if len(df) > 50: results.append("注意: 结果超过50条，仅显示前50条。")
    return "\n".join(results)

@mcp.tool()
@tushare_tool_handler
def get_money_flow_for_past_days(pro, ts_code: str, days: int = 30, stock_name: str = "", **kwargs) -> str:
    """获取指定股票在过去N天内的累计资金净流入情况。"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    end_date_str, start_date_str = end_date.strftime('%Y%m%d'), start_date.strftime('%Y%m%d')

    df = pro.moneyflow(ts_code=ts_code, start_date=start_date_str, end_date=end_date_str)
    if df.empty: return f"在 {start_date_str} 到 {end_date_str} 期间未找到 {stock_name} ({ts_code}) 的资金流向数据。"

    total_net_vol = df['net_mf_vol'].sum()
    total_net_amount = df['net_mf_amount'].sum()

    return "\n".join([
        f"--- {stock_name} ({ts_code}) 最近 {days} 天资金流向统计 ---",
        f"查询区间: {start_date_str} 至 {end_date_str}",
        f"累计净流入量: {total_net_vol:,.0f} 手",
        f"累计净流入额: {total_net_amount:,.2f} 万元"
    ])

@mcp.tool()
@tushare_tool_handler
def get_top10_holders(pro, ts_code: str, period: str = None, stock_name: str = "", **kwargs) -> str:
    """获取上市公司前十大股东数据。"""
    params = {'ts_code': ts_code}
    if period: params['period'] = period
    
    df = pro.top10_holders(**params)
    df_latest = _get_latest_report_df(df)
    if df_latest is None: return f"未找到 {stock_name} ({ts_code}) 的前十大股东数据。"

    latest_end_date = df_latest['end_date'].iloc[0]
    header = f"--- {stock_name} ({ts_code}) 报告期 {latest_end_date} 前十大股东 ---"
    results = [header]
    for _, row in df_latest.iterrows():
        results.append(f"股东名称: {row['holder_name']}\n  - 持有数量: {row['hold_amount']:,.0f} 股\n  - 占总股本比例: {row['hold_ratio']:.2f}%")
    return "\n".join(results)

@mcp.tool()
@tushare_tool_handler
def get_top10_float_holders(pro, ts_code: str, period: str = None, stock_name: str = "", **kwargs) -> str:
    """获取上市公司前十大流通股东数据。"""
    params = {'ts_code': ts_code}
    if period: params['period'] = period

    df = pro.top10_floatholders(**params)
    df_latest = _get_latest_report_df(df)
    if df_latest is None: return f"未找到 {stock_name} ({ts_code}) 的前十大流通股东数据。"

    latest_end_date = df_latest['end_date'].iloc[0]
    header = f"--- {stock_name} ({ts_code}) 报告期 {latest_end_date} 前十大流通股东 ---"
    results = [header]
    for _, row in df_latest.iterrows():
        results.append(f"股东名称: {row['holder_name']}\n  - 持有数量: {row['hold_amount']:,.0f} 股\n  - 占流通股本比例: {row['hold_float_ratio']:.2f}%")
    return "\n".join(results)

@mcp.tool()
@tushare_tool_handler
def get_shareholder_trades(pro, ts_code: str, days: int = 90, trade_type: str = None, stock_name: str = "", **kwargs) -> str:
    """获取上市公司股东在过去N天内的增减持数据。"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    params = {'ts_code': ts_code, 'start_date': start_date.strftime('%Y%m%d'), 'end_date': end_date.strftime('%Y%m%d')}
    
    # --- 关键优化：增加对无效 trade_type 的校验 ---
    if trade_type:
        trade_type_upper = trade_type.upper()
        if trade_type_upper in ['IN', 'DE']:
            params['trade_type'] = trade_type_upper
        else:
            return f"错误：无效的交易类型 '{trade_type}'。请使用 'IN' (增持) 或 'DE' (减持)。"

    df = pro.stk_holdertrade(**params)
    if df.empty:
        trade_type_str = {"IN": "增持", "DE": "减持"}.get(params.get('trade_type'), "")
        return f"在最近 {days} 天内未找到 {stock_name} ({ts_code}) 的{trade_type_str}记录。"

    header = f"--- {stock_name} ({ts_code}) 最近 {days} 天股东增减持记录 ---"
    results = [header]
    for _, row in df.iterrows():
        trade_action = "增持" if row['in_de'] == 'IN' else "减持"
        change_vol_str = f"{row['change_vol']:,.0f}" if pd.notna(row['change_vol']) else "N/A"
        change_ratio_str = f"{row['change_ratio']:.4f}" if pd.notna(row['change_ratio']) else "N/A"
        after_share_str = f"{row['after_share']:,.0f}" if pd.notna(row['after_share']) else "N/A"
        after_ratio_str = f"{row['after_ratio']:.4f}" if pd.notna(row['after_ratio']) else "N/A"
        results.append(
            f"公告日期: {row['ann_date']}\n"
            f"  - 股东名称: {row['holder_name']}\n"
            f"  - 变动类型: {trade_action}\n"
            f"  - 变动数量: {change_vol_str} 股\n"
            f"  - 占流通股比例: {change_ratio_str}%\n"
            f"  - 变动后持股数: {after_share_str} 股\n"
            f"  - 变动后占流通股比例: {after_ratio_str}%"
        )
    return "\n".join(results)


# --- 6. FastAPI Endpoints & Server Mounting ---

@app.get("/")
async def read_root():
    return {"message": "Hello World - Tushare MCP API (Optimized) is running!"}

@app.post("/tools/setup_tushare_token", summary="Setup Tushare API token")
async def api_setup_tushare_token(payload: dict = Body(...)):
    """通过REST端点设置Tushare API token。"""
    token = payload.get("token")
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=400, detail="Payload中缺少或包含无效的'token'。")
    try:
        result_message = setup_tushare_token(token=token)
        if "配置成功" in result_message:
            return {"status": "success", "message": result_message}
        else:
            raise HTTPException(status_code=401, detail=result_message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"设置token时发生意外错误: {e}")

# --- MCP SSE Workaround Integration ---
MCP_BASE_PATH = "/mcp"
try:
    messages_full_path = f"{MCP_BASE_PATH}/messages/"
    sse_transport = SseServerTransport(messages_full_path)
    async def handle_mcp_sse_handshake(request: Request) -> None:
        async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
            await mcp._mcp_server.run(read_stream, write_stream, mcp._mcp_server.create_initialization_options())
    app.add_route(MCP_BASE_PATH, handle_mcp_sse_handshake, methods=["GET"])
    app.mount(messages_full_path, sse_transport.handle_post_message)
except Exception as e:
    logging.critical(f"应用MCP SSE workaround时发生严重错误: {e}")
    traceback.print_exc(file=sys.stderr)


# --- 7. Server Execution ---

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)