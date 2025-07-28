#!/bin/sh
export FLASK_APP=app

# 确保数据目录存在
mkdir -p /app/data
chmod 777 /app/data

echo "正在初始化数据库..."
python -c "from app import app, init_database; app.app_context().push(); init_database()"
echo "数据库初始化完成。"

echo "正在启动 Gunicorn 服务器..."
exec "$@"
