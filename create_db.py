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

# 打印当前工作目录
print(f"当前工作目录: {os.getcwd()}")

# 确保数据目录存在
data_dir = os.path.join(os.getcwd(), 'data')
os.makedirs(data_dir, exist_ok=True)
print(f"数据目录已创建: {os.path.abspath(data_dir)}")

# 数据库文件路径
DB_PATH = os.path.join(data_dir, 'lottery_system.db')
print(f"数据库文件路径: {os.path.abspath(DB_PATH)}")

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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    activation_expires_at TIMESTAMP,
    invited_by VARCHAR(80),
    invite_code_used VARCHAR(32),
    invite_activated_at TIMESTAMP,
    last_login DATETIME,
    login_count INTEGER DEFAULT 0,
    auto_prediction_enabled BOOLEAN DEFAULT 0,
    auto_prediction_strategies TEXT
)
''')

# 创建激活码表
cursor.execute('''
CREATE TABLE activation_code (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(100) NOT NULL UNIQUE,
    is_used BOOLEAN DEFAULT 0,
    used_by VARCHAR(80),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP,
    validity_type VARCHAR(20) DEFAULT 'permanent',
    expires_at TIMESTAMP
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
    actual_special_zodiac VARCHAR(10),
    accuracy_score FLOAT,
    is_result_updated BOOLEAN DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES user (id)
)
''')

# 创建邀请码表
cursor.execute('''
CREATE TABLE invite_code (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(32) NOT NULL UNIQUE,
    created_by VARCHAR(80) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_used BOOLEAN DEFAULT 0,
    used_by VARCHAR(80),
    used_at TIMESTAMP,
    expires_at TIMESTAMP
)
''')

# 创建系统配置表
cursor.execute('''
CREATE TABLE system_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key VARCHAR(100) NOT NULL UNIQUE,
    value TEXT,
    description VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

print("表创建完成，正在添加初始数据...")

# 创建管理员用户
# 使用与Werkzeug兼容的密码哈希格式
def generate_password_hash(password):
    """生成与Werkzeug兼容的密码哈希"""
    # 使用pbkdf2:sha256方法，与Werkzeug默认方法兼容
    method = 'pbkdf2:sha256:150000'
    salt = os.urandom(8).hex()
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 150000)
    hash_value = h.hex()
    return f"{method}${salt}${hash_value}"

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
    ('invite_daily_limit', '3', '每日邀请码生成限制'),
    ('invite_code_validity_days', '7', '邀请码有效期（天）'),
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
print("\n邀请系统:")
print("- 已创建邀请码表和相关字段")
print("- 管理员可在后台生成邀请码")
print("- 用户可通过邀请码注册获得奖励")
print("- 邀请人和被邀请人都将获得1天有效期")