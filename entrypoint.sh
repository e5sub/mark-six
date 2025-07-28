#!/bin/sh
export FLASK_APP=app

# 确保数据目录存在并有正确的权限
mkdir -p /app/data
chmod 777 /app/data
chown -R nobody:nogroup /app/data 2>/dev/null || echo "无法更改所有者，继续执行..."

# 打印当前目录和数据目录内容
echo "当前目录: $(pwd)"
echo "数据目录内容:"
ls -la /app/data

# 检查数据库文件是否存在
if [ ! -f /app/data/lottery_system.db ]; then
    echo "数据库文件不存在，正在初始化数据库..."
    # 使用create_db.py脚本创建数据库
    python create_db.py
    
    # 再次检查数据库文件是否创建成功
    if [ -f /app/data/lottery_system.db ]; then
        echo "数据库文件创建成功: $(ls -la /app/data/lottery_system.db)"
        # 确保数据库文件权限正确
        chmod 666 /app/data/lottery_system.db
    else
        echo "警告: 数据库文件创建失败!"
    fi
else
    echo "数据库文件已存在: $(ls -la /app/data/lottery_system.db)"
    # 确保现有数据库文件权限正确
    chmod 666 /app/data/lottery_system.db
fi

echo "正在启动 Gunicorn 服务器..."
exec "$@"
