#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库迁移脚本 - 添加用户激活到期时间字段
"""

import os
import sys
import sqlite3
from datetime import datetime

# 确保数据目录存在
data_dir = os.path.join(os.getcwd(), 'data')
os.makedirs(data_dir, exist_ok=True)

# 数据库文件路径
DB_PATH = os.path.join(data_dir, 'lottery_system.db')

def migrate_database():
    """迁移数据库，添加新字段"""
    if not os.path.exists(DB_PATH):
        print("数据库文件不存在，无需迁移")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 检查是否已经存在activation_expires_at字段
        cursor.execute("PRAGMA table_info(user)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'activation_expires_at' not in columns:
            print("添加activation_expires_at字段到user表...")
            cursor.execute('ALTER TABLE user ADD COLUMN activation_expires_at TIMESTAMP')
            print("✓ activation_expires_at字段添加成功")
        else:
            print("activation_expires_at字段已存在，跳过")
        
        # 提交更改
        conn.commit()
        print("数据库迁移完成")
        
    except Exception as e:
        print(f"数据库迁移失败: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    migrate_database()