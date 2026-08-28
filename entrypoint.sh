#!/bin/sh
set -eu

export FLASK_APP=app

# 容器以非 root 用户 appuser 运行，/app/data 已在镜像中归属 appuser 并设为 750。
# 这里仅确保目录存在；不再放宽权限到 777（旧版会暴露密钥文件与数据库给任意进程）。
mkdir -p /app/data

if [ "${DEBUG_STARTUP:-0}" = "1" ]; then
    echo "当前目录: $(pwd)"
    echo "数据目录内容:"
    ls -la /app/data
fi

DB_TYPE_LOWER=$(echo "${DB_TYPE:-}" | tr '[:upper:]' '[:lower:]')
if [ "$DB_TYPE_LOWER" = "mysql" ] || [ "$DB_TYPE_LOWER" = "mariadb" ] || echo "${DATABASE_URL:-}" | grep -qi "^mysql"; then
    echo "已配置 MySQL，跳过 SQLite 初始化。"
    exec "$@"
fi

if [ ! -f /app/data/lottery_system.db ]; then
    echo "未找到 SQLite 数据库，正在初始化..."
    python create_db.py

    if [ -f /app/data/lottery_system.db ]; then
        echo "SQLite 数据库创建成功。"
        # appuser 是数据库文件的属主，仅限本人读写，无需放宽到 666。
        chmod 640 /app/data/lottery_system.db
    else
        echo "警告：SQLite 数据库创建失败。"
    fi
else
    echo "SQLite 数据库已存在，跳过初始化。"
    chmod 640 /app/data/lottery_system.db 2>/dev/null || true
fi

echo "正在启动 Gunicorn..."
exec "$@"
