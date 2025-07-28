#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重置管理员密码脚本
"""

import os
import sqlite3
from werkzeug.security import generate_password_hash

# 确保数据目录存在
data_dir = os.path.join(os.getcwd(), 'data')
db_path = os.path.join(data_dir, 'lottery_system.db')

if not os.path.exists(db_path):
    print(f"错误: 数据库文件不存在: {db_path}")
    exit(1)

# 连接数据库
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 新密码
new_password = 'admin123'
password_hash = generate_password_hash(new_password)

# 更新管理员密码
cursor.execute('''
UPDATE user 
SET password_hash = ? 
WHERE username = 'admin'
''', (password_hash,))

# 提交更改并关闭连接
conn.commit()
conn.close()

print(f"✓ 管理员密码已重置为: {new_password}")
print("请立即登录并修改此默认密码")