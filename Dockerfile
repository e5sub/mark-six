FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    sqlite3 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 创建非 root 运行用户 appuser，并创建仅该用户可读写的数据目录。
# 固定 UID/GID=1000，便于宿主机挂载卷预先对齐属主，避免容器启动时权限不匹配。
ENV APP_USER_UID=1000 \
    APP_USER_GID=1000
RUN groupadd --system --gid ${APP_USER_GID} appuser && \
    useradd --system --uid ${APP_USER_UID} --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app && \
    chmod 750 /app/data

# 复制项目文件
COPY --chown=appuser:appuser . .

# 确保脚本可执行
RUN chmod +x /app/entrypoint.sh /app/create_db.py /app/reset_admin.py

# 暴露端口
EXPOSE 5000

# 以非 root 身份运行容器
USER appuser

# 使用entrypoint.sh脚本启动
ENTRYPOINT ["/app/entrypoint.sh"]

# 启动命令
# gthread worker：每个 worker 多线程并发，开奖 SSE 长连接不会占满 worker 导致普通请求阻塞
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "8", "--worker-class", "gthread", "--timeout", "300", "app:app"]
