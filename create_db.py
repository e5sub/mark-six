#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI数据分析预测系统 - 数据库创建脚本

用于首次部署时创建完整的 SQLite 数据库。
注意：如果 data/lottery_system.db 已存在，本脚本会先删除旧库再重新创建。
"""

import hashlib
import os
import sqlite3
import sys


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DB_TYPE = os.environ.get("DB_TYPE", "sqlite").lower()
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if DB_TYPE in ("mysql", "mariadb") or DATABASE_URL.lower().startswith("mysql"):
    print("检测到 MySQL/MariaDB 配置，执行数据库结构更新。")
    try:
        import auto_update_db

        update_success = auto_update_db.update_database()
        if update_success:
            print("MySQL/MariaDB 数据库结构更新完成。")
            sys.exit(0)
        print("MySQL/MariaDB 数据库结构更新失败，请检查数据库连接配置。")
        sys.exit(1)
    except Exception as e:
        print(f"MySQL/MariaDB 数据库结构更新失败: {e}")
        sys.exit(1)

if False and (DB_TYPE in ("mysql", "mariadb") or DATABASE_URL.lower().startswith("mysql")):
    print("⚠ 检测到 MySQL/MariaDB 配置，跳过 SQLite 数据库创建。")
    sys.exit(0)


print(f"当前工作目录: {os.getcwd()}")

data_dir = os.path.join(os.getcwd(), "data")
os.makedirs(data_dir, exist_ok=True)
print(f"✓ 数据目录已创建: {os.path.abspath(data_dir)}")

DB_PATH = os.path.join(data_dir, "lottery_system.db")
print(f"数据库文件路径: {os.path.abspath(DB_PATH)}")

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
    print("⚠ 已删除旧数据库文件。")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print("正在创建数据库表...")


# 用户表
cursor.execute("""
CREATE TABLE user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(80) NOT NULL UNIQUE,
    email VARCHAR(120) NOT NULL UNIQUE,
    github_id VARCHAR(64) UNIQUE,
    github_username VARCHAR(120),
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
    auto_prediction_enabled BOOLEAN DEFAULT 1,
    auto_prediction_strategies TEXT DEFAULT 'hot,cold,trend,hybrid,balanced,markov,ml',
    auto_prediction_regions TEXT DEFAULT 'hk,macau',
    show_normal_numbers BOOLEAN DEFAULT 0
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_user_created_at
ON user (created_at)
""")

cursor.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_github_id
ON user (github_id)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_user_activation_expires_at
ON user (activation_expires_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_user_invited_by_created_at
ON user (invited_by, created_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_user_active_auto_prediction
ON user (is_active, auto_prediction_enabled)
""")


# 激活码表
cursor.execute("""
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
""")


# 激活码申请表
cursor.execute("""
CREATE TABLE activation_code_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username VARCHAR(80) NOT NULL,
    email VARCHAR(120) NOT NULL,
    request_note VARCHAR(255),
    status VARCHAR(20) DEFAULT 'pending',
    admin_note VARCHAR(255),
    issued_code VARCHAR(64),
    issued_validity_type VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user (id)
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_activation_code_request_user_status
ON activation_code_request (user_id, status)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_activation_code_request_user_created_at
ON activation_code_request (user_id, created_at)
""")


# 预测记录表
cursor.execute("""
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
    prediction_metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    actual_normal_numbers VARCHAR(50),
    actual_special_number VARCHAR(10),
    actual_special_zodiac VARCHAR(10),
    accuracy_score FLOAT,
    is_result_updated BOOLEAN DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES user (id)
)
""")

cursor.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction_record_user_region_period_strategy
ON prediction_record (user_id, region, period, strategy)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_prediction_record_user_strategy_created_at
ON prediction_record (user_id, strategy, created_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_prediction_record_user_strategy_region_period
ON prediction_record (user_id, strategy, region, period)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_prediction_record_user_created_at
ON prediction_record (user_id, created_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_prediction_record_region_created_at
ON prediction_record (region, created_at)
""")


# 回测记录表
cursor.execute("""
CREATE TABLE backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(120) NOT NULL,
    region VARCHAR(10),
    strategies VARCHAR(255),
    periods_evaluated INTEGER DEFAULT 0,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_backtest_runs_region_name
ON backtest_runs (region, name)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_backtest_runs_region_created_at
ON backtest_runs (region, created_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_backtest_runs_created_at
ON backtest_runs (created_at)
""")


# 邀请码表
cursor.execute("""
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
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_invite_code_created_by_used_created_at
ON invite_code (created_by, is_used, created_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_invite_code_created_at
ON invite_code (created_at)
""")


# 系统配置表
cursor.execute("""
CREATE TABLE system_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key VARCHAR(100) NOT NULL UNIQUE,
    value TEXT,
    description VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")


# 用户站内通知表
cursor.execute("""
CREATE TABLE user_notification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_type VARCHAR(50) DEFAULT 'general',
    title VARCHAR(160) NOT NULL,
    content TEXT,
    link_url VARCHAR(255),
    is_read BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user (id)
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_user_notification_user_created_at
ON user_notification (user_id, created_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_user_notification_user_read
ON user_notification (user_id, is_read)
""")


# 手工下注记录表
cursor.execute("""
CREATE TABLE manual_bet_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    region VARCHAR(10) NOT NULL,
    period VARCHAR(20) NOT NULL,
    bettor_name VARCHAR(50),
    selected_numbers VARCHAR(200),
    selected_zodiacs VARCHAR(100),
    selected_colors VARCHAR(50),
    selected_parity VARCHAR(20),
    odds_number FLOAT,
    odds_zodiac FLOAT,
    odds_color FLOAT,
    odds_parity FLOAT,
    stake_special FLOAT,
    stake_common FLOAT,
    result_number BOOLEAN,
    result_zodiac BOOLEAN,
    result_color BOOLEAN,
    result_parity BOOLEAN,
    profit_number FLOAT,
    profit_zodiac FLOAT,
    profit_color FLOAT,
    profit_parity FLOAT,
    total_profit FLOAT,
    total_stake FLOAT,
    special_number VARCHAR(10),
    special_zodiac VARCHAR(10),
    special_color VARCHAR(10),
    special_parity VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user (id)
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_manual_bet_records_user_region_created_at
ON manual_bet_records (user_id, region, created_at)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_manual_bet_records_region_period_profit
ON manual_bet_records (region, period, total_profit)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_manual_bet_records_user_region_period_profit_created_at
ON manual_bet_records (user_id, region, period, total_profit, created_at)
""")


# 开奖记录表
cursor.execute("""
CREATE TABLE lottery_draws (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region VARCHAR(10) NOT NULL,
    draw_id VARCHAR(20) NOT NULL,
    draw_date VARCHAR(20),
    normal_numbers VARCHAR(50) NOT NULL,
    special_number VARCHAR(10) NOT NULL,
    special_zodiac VARCHAR(10),
    raw_zodiac VARCHAR(100),
    raw_wave VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(region, draw_id)
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_lottery_draws_region_draw_date_draw_id
ON lottery_draws (region, draw_date, draw_id)
""")

cursor.execute("""
CREATE TABLE macau_collected_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region VARCHAR(10) NOT NULL DEFAULT 'macau',
    year INTEGER NOT NULL,
    source_period VARCHAR(10) NOT NULL,
    period VARCHAR(20) NOT NULL,
    numbers VARCHAR(100),
    zodiacs VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(region, period)
)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_macau_collected_region_period
ON macau_collected_data (region, period)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_macau_collected_year_source_period
ON macau_collected_data (year, source_period)
""")

cursor.execute("""
CREATE INDEX IF NOT EXISTS ix_macau_collected_created_at
ON macau_collected_data (created_at)
""")


# 生肖设置表
cursor.execute("""
CREATE TABLE zodiac_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    zodiac VARCHAR(10) NOT NULL,
    numbers VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(year, zodiac)
)
""")

print("表创建完成，正在添加初始数据...")


def generate_password_hash(password):
    """生成与 Werkzeug 兼容的密码哈希。"""
    method = "pbkdf2:sha256:150000"
    salt = os.urandom(8).hex()
    hash_value = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        150000,
    ).hex()
    return f"{method}${salt}${hash_value}"


# 系统配置默认值，需与后台配置页保持一致。
configs = [
    ("ai_api_key", "", "AI API 密钥"),
    ("ai_api_url", "https://api.deepseek.com/v1/chat/completions", "AI API 地址"),
    ("ai_model", "deepseek-chat", "AI 模型"),
    ("smtp_server", "", "SMTP 服务器"),
    ("smtp_port", "587", "SMTP 端口"),
    ("smtp_username", "", "SMTP 用户名"),
    ("smtp_password", "", "SMTP 密码"),
    ("activation_request_notify_emails", "", "激活申请通知邮箱"),
    ("notify_email_enabled", "true", "启用邮件通知"),
    ("site_name", "AI数据分析预测系统", "站点名称"),
    ("site_description", "", "站点描述"),
    ("seo_title", "彩研所 - 香港澳门彩票数据分析与智能预测", "SEO标题"),
    ("seo_description", "彩研所提供香港、澳门彩票开奖记录、生肖号码、波色单双、历史走势和智能预测分析，帮助用户快速查看开奖数据并辅助选号研究，仅供数据分析参考。", "SEO描述"),
    ("invite_daily_limit", "3", "每日邀请码生成限制"),
    ("invite_code_validity_days", "7", "邀请码有效期天数"),
    ("system_name", "AI数据分析预测系统", "系统名称"),
    ("system_description", "", "系统描述"),
    ("allow_registration", "true", "允许用户注册"),
    ("require_email_verification", "false", "注册是否需要邮箱验证"),
    ("enable_turnstile", "false", "启用 Cloudflare Turnstile 人机验证"),
    ("turnstile_site_key", "", "Cloudflare Turnstile 站点密钥"),
    ("turnstile_secret_key", "", "Cloudflare Turnstile 私钥"),
    ("enable_github_login", "false", "启用 GitHub 登录"),
    ("github_client_id", "", "GitHub OAuth Client ID"),
    ("github_client_secret", "", "GitHub OAuth Client Secret"),
    ("enable_personalized_predictions", "false", "启用个性化预测"),
    ("auto_optimize_enabled", "false", "启用策略自动优化"),
    ("auto_optimize_level", "balanced", "策略自动优化等级"),
    ("auto_optimize_min_gain", "0.6", "策略自动优化最小收益阈值"),
]

for key, value, description in configs:
    cursor.execute(
        """
        INSERT INTO system_config (key, value, description)
        VALUES (?, ?, ?)
        """,
        (key, value, description),
    )

conn.commit()
conn.close()

print(f"✓ 数据库文件已成功创建: {DB_PATH}")

print("\n正在运行数据库自动更新...")
try:
    import auto_update_db

    update_success = auto_update_db.update_database()
    if update_success:
        print("✓ 数据库自动更新完成。")
    else:
        print("⚠ 数据库自动更新失败，请手动执行 auto_update_db.py。")
except Exception as e:
    print(f"⚠ 数据库自动更新失败: {str(e)}")
    print("⚠ 请手动执行: python auto_update_db.py")

print("\n系统信息:")
print("- 首次部署时，第一个注册用户会自动成为管理员。")
print("- 系统不再写入固定的默认管理员账号和密码。")
print("- 请在管理后台配置 AI API 和 SMTP 服务。")
print("\n邀请系统:")
print("- 已创建邀请码表和相关用户字段。")
print("- 管理员可以在后台生成邀请码。")
print("- 用户可以通过邀请码注册。")
