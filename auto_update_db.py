#!/usr/bin/env python3
"""
自动数据库更新模块
在应用启动时自动检查并更新数据库结构
"""

from flask import current_app
from models import db
import sqlite3
import os

def check_and_update_database():
    """检查并自动更新数据库结构"""
    try:
        # 获取数据库文件路径
        db_path = current_app.config.get('DATABASE_PATH', 'instance/database.db')
        
        if not os.path.exists(db_path):
            print("数据库文件不存在，将创建新数据库")
            db.create_all()
            return True
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查User表是否有新字段
        cursor.execute("PRAGMA table_info(user)")
        columns = [column[1] for column in cursor.fetchall()]
        
        updates_needed = False
        
        # 需要添加的字段
        new_fields = [
            ('invited_by', 'VARCHAR(80)'),
            ('invite_code_used', 'VARCHAR(32)'),
            ('invite_activated_at', 'DATETIME')
        ]
        
        for field_name, field_type in new_fields:
            if field_name not in columns:
                try:
                    cursor.execute(f"ALTER TABLE user ADD COLUMN {field_name} {field_type}")
                    print(f"✅ 自动添加字段: user.{field_name}")
                    updates_needed = True
                except sqlite3.Error as e:
                    print(f"⚠️  添加字段失败: {e}")
        
        # 检查InviteCode表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='invite_code'")
        if not cursor.fetchone():
            # 创建InviteCode表
            create_table_sql = """
            CREATE TABLE invite_code (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(32) UNIQUE NOT NULL,
                created_by VARCHAR(80) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME,
                is_used BOOLEAN NOT NULL DEFAULT 0,
                used_by VARCHAR(80),
                used_at DATETIME,
                max_uses INTEGER DEFAULT 1,
                current_uses INTEGER DEFAULT 0
            )
            """
            cursor.execute(create_table_sql)
            print("✅ 自动创建InviteCode表")
            updates_needed = True
        
        if updates_needed:
            conn.commit()
            print("✅ 数据库结构自动更新完成")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ 自动更新数据库失败: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

def init_database_with_invite_support():
    """初始化支持邀请功能的数据库"""
    try:
        # 创建所有表
        db.create_all()
        
        # 检查并更新现有数据库
        check_and_update_database()
        
        print("✅ 数据库初始化完成，支持邀请功能")
        return True
        
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        return False