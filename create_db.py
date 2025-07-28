#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI数据分析系统 - 数据库创建脚本
此脚本用于创建一个完整的预初始化数据库文件
"""

import os
import sys
import sqlite3
from datetime import datetime
import hashlib
import uuid

# 确保数据目录存在
os.makedirs('data', exist_ok=True)

# 数据库文件路径
DB_PATH = 'data/lottery_system.db'

# 如果数据库文件已存在，先删除它
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

# 创建新的数据库连接
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print("正在创建数据库表...")

# 创建用户表
cursor.execute('''
CREATE TABLE user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(80) NOT NULL UNIQUE,
    email VARCHAR(120) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT 0,
    is_admin BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# 创建激活码表
cursor.execute('''
CREATE TABLE activation_code (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(100) NOT NULL UNIQUE,
    is_used BOOLEAN DEFAULT 0,
    used_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP,
    validity_type VARCHAR(10) DEFAULT 'permanent',
    validity_days INTEGER DEFAULT 0,
    expires_at TIMESTAMP,
    FOREIGN KEY (used_by) REFERENCES user (id)
)
''')

# 创建预测记录表
cursor.execute('''
CREATE TABLE prediction_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    region VARCHAR(10) NOT NULL,
    strategy VARCHAR(20) NOT NULL,
    period VARCHAR(50) NOT NULL,
    normal_numbers VARCHAR(50) NOT NULL,
    special_number VARCHAR(10) NOT NULL,
    special_zodiac VARCHAR(10),
    prediction_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    actual_normal_numbers VARCHAR(50),
    actual_special_number VARCHAR(10),
    accuracy_score FLOAT,
    is_result_updated BOOLEAN DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES user (id)
)
''')

# 创建系统配置表
cursor.execute('''
CREATE TABLE system_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key VARCHAR(100) NOT NULL UNIQUE,
    value TEXT,
    description VARCHAR(255),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

print("表创建完成，正在添加初始数据...")

# 创建管理员用户
# 使用简单的密码哈希函数（实际应用中应使用更安全的方法）
def generate_password_hash(password):
    salt = uuid.uuid4().hex
    return hashlib.sha256(salt.encode() + password.encode()).hexdigest() + ':' + salt

admin_password_hash = generate_password_hash('admin123')
cursor.execute('''
INSERT INTO user (username, email, password_hash, is_active, is_admin)
VALUES (?, ?, ?, ?, ?)
''', ('admin', 'admin@example.com', admin_password_hash, 1, 1))

# 添加系统配置
configs = [
    ('ai_api_key', '', 'AI API密钥'),
    ('ai_api_url', 'https://api.deepseek.com/v1/chat/completions', 'AI API地址'),
    ('ai_model', 'gemini-2.0-flash', 'AI模型'),
    ('smtp_server', '', 'SMTP服务器'),
    ('smtp_port', '587', 'SMTP端口'),
    ('smtp_username', '', 'SMTP用户名'),
    ('smtp_password', '', 'SMTP密码'),
]

for key, value, description in configs:
    cursor.execute('''
    INSERT INTO system_config (key, value, description)
    VALUES (?, ?, ?)
    ''', (key, value, description))

# 提交更改并关闭连接
conn.commit()
conn.close()

print(f"✓ 数据库文件已成功创建: {DB_PATH}")
print("\n系统信息:")
print("- 默认管理员账号: admin")
print("- 默认管理员密码: admin123")
print("- 请在首次登录后修改管理员密码")
print("- 请在管理后台配置AI API和邮箱服务")