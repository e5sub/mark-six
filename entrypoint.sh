#!/bin/sh
export FLASK_APP=app

# 确保数据目录存在
mkdir -p /app/data
chmod 777 /app/data

# 检查数据库文件是否存在
if [ ! -f /app/data/lottery_system.db ]; then
    echo "数据库文件不存在，正在初始化数据库..."
    python -c "from app import app, init_database; with app.app_context(): init_database()"
    echo "数据库初始化完成。"
else
    echo "数据库文件已存在，跳过初始化步骤。"
fi

echo "正在启动 Gunicorn 服务器..."
exec "$@"
