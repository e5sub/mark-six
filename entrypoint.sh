#!/bin/sh
export FLASK_APP=app
echo "正在初始化数据库..."
flask init-db
echo "数据库初始化完成。"
echo "正在启动 Gunicorn 服务器..."
exec gunicorn --bind 0.0.0.0:5000 app:app