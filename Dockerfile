# --- 构建阶段 ---
FROM python:3.10-slim as builder

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libffi-dev \
    libc-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

# 设置并激活虚拟环境
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# 安装依赖
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 运行阶段 ---
FROM python:3.10-slim

# 【修复1】安装 curl 用于健康检查
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# 创建非特权用户
RUN groupadd -r appuser && useradd --no-create-home -r -g appuser appuser

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 设置工作目录并复制应用代码
WORKDIR /app
COPY . .

# 修改所有权
RUN chown -R appuser:appuser /app

# 切换到非特权用户
USER appuser

# 环境变量设置
ENV PYTHONUNBUFFERED=1
ENV PORT=8080 

# 健康检查 (确保 curl 可用)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/ || exit 1

# 暴露端口
EXPOSE ${PORT} 

# 启动命令：移除 --workers 参数，确保单进程运行
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"] # 【修复】硬编码为 8080