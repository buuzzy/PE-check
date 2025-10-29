# PE-check

一个基于 FastAPI 和 Supabase 的股票 PE 分位数查询工具。

## 功能特性

- 支持 Tushare 格式和 Supabase 格式的股票代码查询
- 提供近三年 PE 历史分位数据
- 使用 MCP 工具进行交互

## 环境要求

- Python 3.10+
- FastAPI
- Supabase

## 快速开始

1. 克隆仓库：
```bash
git clone https://github.com/buuzzy/PE-check.git
cd PE-check
```

2. 安装依赖：
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. 配置环境变量：
```bash
cp .env.example .env
# 编辑 .env 文件，填入您的 Supabase 配置
```

4. 运行服务：
```bash
uvicorn server:app --reload
```

## 部署

本项目支持 Google Cloud Run 部署，详见 Dockerfile。

## API 文档

服务运行后访问 `/docs` 查看 API 文档。