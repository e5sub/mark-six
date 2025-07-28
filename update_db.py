#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import os
from datetime import datetime

def update_database():
    """更新数据库，添加activation_expires_at字段"""
    db_path = 'data/lottery_system.db'
    
    if not os.path.exists(db_path):
        print(f"数据库文件不存在: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查字段是否已存在
        cursor.execute("PRAGMA table_info(user)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'activation_expires_at' not in columns:
            print("添加activation_expires_at字段...")
            cursor.execute("ALTER TABLE user ADD COLUMN activation_expires_at DATETIME")
            print("字段添加成功！")
        else:
            print("activation_expires_at字段已存在")
        
        # 检查ActivationCode表的used_by字段类型
        cursor.execute("PRAGMA table_info(activation_code)")
        columns_info = cursor.fetchall()
        
        for column in columns_info:
            if column[1] == 'used_by':
                print(f"used_by字段类型: {column[2]}")
                break
        
        conn.commit()
        conn.close()
        print("数据库更新完成！")
        return True
        
    except Exception as e:
        print(f"数据库更新失败: {e}")
        return False

if __name__ == '__main__':
    update_database()