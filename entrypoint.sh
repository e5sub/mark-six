#!/bin/sh
set -eu

export FLASK_APP=app

mkdir -p /app/data
chmod 777 /app/data
chown -R nobody:nogroup /app/data 2>/dev/null || echo "无法更改 /app/data 所有者，继续执行..."

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
        chmod 666 /app/data/lottery_system.db
    else
        echo "警告：SQLite 数据库创建失败。"
    fi
else
    echo "SQLite 数据库已存在，跳过初始化。"
    chmod 666 /app/data/lottery_system.db
fi

echo "正在启动 Gunicorn..."
exec "$@"
