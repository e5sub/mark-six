#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库自动更新脚本
用于更新现有数据库结构和数据
"""

import os
import sqlite3
from datetime import datetime
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# 数据库文件路径
DB_PATH = os.path.join(os.getcwd(), 'data', 'lottery_system.db')
DB_TYPE = os.environ.get("DB_TYPE", "sqlite").lower()
DATABASE_URL = os.environ.get("DATABASE_URL", "")


MYSQL_CHARSET = "utf8mb4"

def _using_mysql():
    if DB_TYPE in ("mysql", "mariadb"):
        return True
    return DATABASE_URL.lower().startswith("mysql")


def _build_mysql_database_uri():
    if DATABASE_URL:
        return DATABASE_URL

    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ.get("DB_NAME", "mark_six")
    user = quote_plus(os.environ.get("DB_USER", "root"))
    password = quote_plus(os.environ.get("DB_PASSWORD", ""))
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset={MYSQL_CHARSET}"


def _mysql_table_exists(connection, table_name):
    result = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalar()
    return bool(result)


def _mysql_column_exists(connection, table_name, column_name):
    result = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar()
    return bool(result)


def _mysql_index_exists(connection, table_name, index_name):
    result = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND INDEX_NAME = :index_name
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    ).scalar()
    return bool(result)


def _mysql_ensure_system_config(connection, key, value, description):
    if not _mysql_table_exists(connection, "system_config"):
        return
    connection.execute(
        text(
            """
            INSERT INTO system_config (`key`, `value`, description)
            SELECT :key, :value, :description
            WHERE NOT EXISTS (
                SELECT 1 FROM system_config WHERE `key` = :key
            )
            """
        ),
        {"key": key, "value": value, "description": description},
    )


def _update_mysql_database():
    database_uri = _build_mysql_database_uri()
    backend = (make_url(database_uri).get_backend_name() or "").lower()
    if backend not in ("mysql", "mariadb"):
        print(f"Unsupported MySQL update backend: {backend}")
        return False

    engine = create_engine(database_uri, pool_pre_ping=True, pool_recycle=280)
    try:
        changes = []
        with engine.begin() as connection:
            configs = [
                ("enable_turnstile", "false", "启用 Cloudflare Turnstile 人机验证"),
                ("turnstile_site_key", "", "Cloudflare Turnstile 站点密钥"),
                ("turnstile_secret_key", "", "Cloudflare Turnstile 私钥"),
                ("enable_github_login", "false", "启用 GitHub 登录"),
                ("github_client_id", "", "GitHub OAuth Client ID"),
                ("github_client_secret", "", "GitHub OAuth Client Secret"),
            ]
            for key, value, description in configs:
                _mysql_ensure_system_config(connection, key, value, description)

            if _mysql_table_exists(connection, "user"):
                user_columns = {
                    "auto_prediction_regions": "VARCHAR(20) DEFAULT 'hk,macau'",
                    "show_normal_numbers": "BOOLEAN DEFAULT 0",
                    "github_id": "VARCHAR(64)",
                    "github_username": "VARCHAR(120)",
                }
                for column_name, ddl in user_columns.items():
                    if not _mysql_column_exists(connection, "user", column_name):
                        connection.execute(text(f"ALTER TABLE `user` ADD COLUMN `{column_name}` {ddl}"))
                        changes.append(f"Added user.{column_name}")

                if not _mysql_index_exists(connection, "user", "ix_user_github_id"):
                    connection.execute(text("CREATE UNIQUE INDEX ix_user_github_id ON `user` (`github_id`)"))
                    changes.append("Created ix_user_github_id")

            if _mysql_table_exists(connection, "prediction_record"):
                if not _mysql_column_exists(connection, "prediction_record", "prediction_metadata"):
                    connection.execute(text("ALTER TABLE prediction_record ADD COLUMN prediction_metadata MEDIUMTEXT"))
                    changes.append("Added prediction_record.prediction_metadata")

            if _mysql_table_exists(connection, "manual_bet_records"):
                if not _mysql_column_exists(connection, "manual_bet_records", "bettor_name"):
                    connection.execute(text("ALTER TABLE manual_bet_records ADD COLUMN bettor_name VARCHAR(50)"))
                    changes.append("Added manual_bet_records.bettor_name")

            if not _mysql_table_exists(connection, "macau_collected_data"):
                connection.execute(text("""
                    CREATE TABLE macau_collected_data (
                        id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        region VARCHAR(10) NOT NULL DEFAULT 'macau',
                        year INTEGER NOT NULL,
                        source_period VARCHAR(10) NOT NULL,
                        period VARCHAR(20) NOT NULL,
                        numbers VARCHAR(100),
                        zodiacs VARCHAR(100),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uix_macau_collected_region_period (region, period),
                        INDEX ix_macau_collected_region_period (region, period),
                        INDEX ix_macau_collected_year_source_period (year, source_period)
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
                changes.append("Created macau_collected_data")

        if changes:
            print("MySQL/MariaDB database schema updated:")
            for change in changes:
                print(f"- {change}")
        return True
    except Exception as e:
        print(f"MySQL/MariaDB database update failed: {e}")
        return False
    finally:
        engine.dispose()

def check_database_exists():
    """检查数据库是否存在"""
    return os.path.exists(DB_PATH)

def check_column_exists(cursor, table_name, column_name):
    """检查表中是否存在指定列"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    return column_name in columns

def check_table_exists(cursor, table_name):
    """检查表是否存在"""
    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
    return cursor.fetchone() is not None


def check_index_exists(cursor, index_name):
    """检查索引是否存在"""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    )
    return cursor.fetchone() is not None


def ensure_system_config(cursor, key, value, description):
    cursor.execute("SELECT id FROM system_config WHERE key = ?", (key,))
    if cursor.fetchone():
        print(f"system_config {key} already exists")
        return
    cursor.execute(
        """
        INSERT INTO system_config (key, value, description)
        VALUES (?, ?, ?)
        """,
        (key, value, description),
    )
    print(f"Added system_config {key}")


def update_database():
    """更新数据库结构和数据"""
    if _using_mysql():
        return _update_mysql_database()
    if not check_database_exists():
        print(f"数据库文件不存在: {DB_PATH}")
        print("请先运行 create_db.py 创建数据库")
        return False
    
    print(f"正在更新数据库: {DB_PATH}")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        if check_table_exists(cursor, 'system_config'):
            ensure_system_config(cursor, 'enable_turnstile', 'false', '启用 Cloudflare Turnstile 人机验证')
            ensure_system_config(cursor, 'turnstile_site_key', '', 'Cloudflare Turnstile 站点密钥')
            ensure_system_config(cursor, 'turnstile_secret_key', '', 'Cloudflare Turnstile 私钥')
            ensure_system_config(cursor, 'enable_github_login', 'false', '启用 GitHub 登录')
            ensure_system_config(cursor, 'github_client_id', '', 'GitHub OAuth Client ID')
            ensure_system_config(cursor, 'github_client_secret', '', 'GitHub OAuth Client Secret')
        
        # 检查并添加 auto_prediction_regions 字段
        if not check_column_exists(cursor, 'user', 'auto_prediction_regions'):
            print("添加 auto_prediction_regions 字段...")
            cursor.execute('''
                ALTER TABLE user ADD COLUMN auto_prediction_regions TEXT DEFAULT 'hk,macau'
            ''')
            print("✓ auto_prediction_regions 字段添加成功")
        else:
            print("auto_prediction_regions 字段已存在")

        # 检查并添加 show_normal_numbers 字段
        if not check_column_exists(cursor, 'user', 'show_normal_numbers'):
            print("添加 show_normal_numbers 字段...")
            cursor.execute('''
                ALTER TABLE user ADD COLUMN show_normal_numbers BOOLEAN DEFAULT 0
            ''')
            print("show_normal_numbers column added")
        else:
            print("show_normal_numbers column already exists")

        if not check_column_exists(cursor, 'user', 'github_id'):
            print("Adding github_id column...")
            cursor.execute('''
                ALTER TABLE user ADD COLUMN github_id VARCHAR(64)
            ''')
            print("github_id column added")
        else:
            print("github_id column already exists")

        if not check_column_exists(cursor, 'user', 'github_username'):
            print("Adding github_username column...")
            cursor.execute('''
                ALTER TABLE user ADD COLUMN github_username VARCHAR(120)
            ''')
            print("github_username column added")
        else:
            print("github_username column already exists")

        github_index_name = 'ix_user_github_id'
        if not check_index_exists(cursor, github_index_name):
            print("Creating github_id index...")
            cursor.execute(f'''
                CREATE UNIQUE INDEX {github_index_name}
                ON user (github_id)
            ''')
            print("github_id index created")
        else:
            print("github_id index already exists")
        if False:
            print("✓ show_normal_numbers 字段添加成功")
        else:
            print("show_normal_numbers 字段已存在")
        
        # 更新现有用户的 auto_prediction_regions 字段
        print("更新现有用户的自动预测地区设置...")
        cursor.execute('''
            UPDATE user 
            SET auto_prediction_regions = 'hk,macau' 
            WHERE auto_prediction_regions IS NULL 
               OR auto_prediction_regions = '' 
               OR auto_prediction_regions = 'NULL'
        ''')
        updated_users = cursor.rowcount
        print(f"✓ 更新了 {updated_users} 个用户的自动预测地区设置")

        print("更新现有有效用户的自动预测状态...")
        cursor.execute('''
            UPDATE user 
            SET auto_prediction_enabled = 1 
            WHERE (auto_prediction_enabled IS NULL OR auto_prediction_enabled = 0) AND is_active = 1
        ''')
        updated_enabled = cursor.rowcount
        print(f"✓ 更新了 {updated_enabled} 个用户的自动预测状态为开启")

        print("更新现有用户的预测展示设置...")
        cursor.execute('''
            UPDATE user
            SET show_normal_numbers = 0
            WHERE show_normal_numbers IS NULL
        ''')
        updated_display_settings = cursor.rowcount
        print(f"✓ 更新了 {updated_display_settings} 个用户的预测展示设置")
        
        # 更新现有用户的 auto_prediction_strategies 字段
        print("更新现有用户的自动预测策略设置...")
        cursor.execute('''
            UPDATE user 
            SET auto_prediction_strategies = 'hot,cold,trend,hybrid,balanced,markov,ml' 
            WHERE auto_prediction_strategies IS NULL 
               OR auto_prediction_strategies = '' 
               OR auto_prediction_strategies = 'NULL'
               OR auto_prediction_strategies = 'balanced'
        ''')
        updated_strategies = cursor.rowcount
        print(f"✓ 更新了 {updated_strategies} 个用户的自动预测策略设置")
        
        # 检查并创建 lottery_draws 表
        cursor.execute('''
            UPDATE user
            SET auto_prediction_strategies = 'hot,cold,trend,hybrid,balanced,markov,ml'
            WHERE auto_prediction_strategies LIKE '%smart%'
        ''')
        cleaned_smart_strategies = cursor.rowcount
        print(f"Cleaned smart strategies for {cleaned_smart_strategies} users")

        if not check_table_exists(cursor, 'lottery_draws'):
            print("创建 lottery_draws 表...")
            cursor.execute('''
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
            ''')
            print("✓ lottery_draws 表创建成功")
        else:
            print("lottery_draws 表已存在")
            
        if not check_table_exists(cursor, 'macau_collected_data'):
            print("Creating macau_collected_data table...")
            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX ix_macau_collected_region_period
                ON macau_collected_data (region, period)
            ''')
            cursor.execute('''
                CREATE INDEX ix_macau_collected_year_source_period
                ON macau_collected_data (year, source_period)
            ''')
            print("macau_collected_data table created")
        else:
            print("macau_collected_data table already exists")

        # 检查并创建 zodiac_settings 表
        if not check_table_exists(cursor, 'activation_code_request'):
            print("Creating activation_code_request table...")
            cursor.execute('''
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
            ''')
            print("activation_code_request table created")
        else:
            print("activation_code_request table already exists")

        if not check_table_exists(cursor, 'zodiac_settings'):
            print("创建 zodiac_settings 表...")
            cursor.execute('''
            CREATE TABLE zodiac_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                zodiac VARCHAR(10) NOT NULL,
                numbers VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(year, zodiac)
            )
            ''')
            print("✓ zodiac_settings 表创建成功")
        else:
            print("zodiac_settings 表已存在")

        # 检查并创建 manual_bet_records 表
        if not check_column_exists(cursor, 'prediction_record', 'prediction_metadata'):
            print("Adding prediction_record.prediction_metadata column...")
            cursor.execute('''
                ALTER TABLE prediction_record ADD COLUMN prediction_metadata TEXT
            ''')
            print("prediction_record.prediction_metadata column added")
        else:
            print("prediction_record.prediction_metadata column already exists")

        if not check_table_exists(cursor, 'backtest_runs'):
            print("Creating backtest_runs table...")
            cursor.execute('''
            CREATE TABLE backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(120) NOT NULL,
                region VARCHAR(10),
                strategies VARCHAR(255),
                periods_evaluated INTEGER DEFAULT 0,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            print("backtest_runs table created")
        else:
            print("backtest_runs table already exists")

        if not check_table_exists(cursor, 'user_notification'):
            print("Creating user_notification table...")
            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX ix_user_notification_user_created_at
                ON user_notification (user_id, created_at)
            ''')
            cursor.execute('''
                CREATE INDEX ix_user_notification_user_read
                ON user_notification (user_id, is_read)
            ''')
            print("user_notification table created")
        else:
            print("user_notification table already exists")

        if check_table_exists(cursor, 'prediction_record'):
            print("Cleaning duplicate prediction_record rows...")
            cursor.execute('''
                DELETE FROM prediction_record
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM prediction_record
                    GROUP BY user_id, region, period, strategy
                )
            ''')
            removed_duplicates = cursor.rowcount
            print(f"Removed {removed_duplicates} duplicate prediction records")

            unique_index_name = 'uq_prediction_record_user_region_period_strategy'
            if not check_index_exists(cursor, unique_index_name):
                print("Creating unique index for prediction_record...")
                cursor.execute(f'''
                    CREATE UNIQUE INDEX {unique_index_name}
                    ON prediction_record (user_id, region, period, strategy)
                ''')
                print("Unique index for prediction_record created")
            else:
                print("Unique index for prediction_record already exists")

            created_at_index_name = 'ix_prediction_record_user_strategy_created_at'
            if not check_index_exists(cursor, created_at_index_name):
                print("Creating created_at index for prediction_record...")
                cursor.execute(f'''
                    CREATE INDEX {created_at_index_name}
                    ON prediction_record (user_id, strategy, created_at)
                ''')
                print("created_at index for prediction_record created")
            else:
                print("created_at index for prediction_record already exists")

            region_period_index_name = 'ix_prediction_record_user_strategy_region_period'
            if not check_index_exists(cursor, region_period_index_name):
                print("Creating region/period index for prediction_record...")
                cursor.execute(f'''
                    CREATE INDEX {region_period_index_name}
                    ON prediction_record (user_id, strategy, region, period)
                ''')
                print("region/period index for prediction_record created")
            else:
                print("region/period index for prediction_record already exists")

        if not check_table_exists(cursor, 'manual_bet_records'):
            print("创建 manual_bet_records 表...")
            cursor.execute('''
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
            ''')
            print("manual_bet_records 表创建成功")
        else:
            print("manual_bet_records 表已存在")
            if not check_column_exists(cursor, 'manual_bet_records', 'bettor_name'):
                print("添加 manual_bet_records.bettor_name 字段...")
                cursor.execute('''
                    ALTER TABLE manual_bet_records ADD COLUMN bettor_name VARCHAR(50)
                ''')
                print("manual_bet_records.bettor_name 字段添加成功")
        
        # 验证更新结果
        print("\n验证更新结果:")
        cursor.execute('''
            SELECT username, auto_prediction_enabled, auto_prediction_strategies, auto_prediction_regions 
            FROM user
        ''')
        users = cursor.fetchall()
        
        for user in users:
            username, enabled, strategies, regions = user
            print(f"用户 {username}: 启用={enabled}, 策略='{strategies}', 地区='{regions}'")
        
        # 提交更改
        conn.commit()
        print(f"\n✓ 数据库更新完成！")
        
        return True
        
    except Exception as e:
        print(f"数据库更新失败: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        return False
        
    finally:
        if 'conn' in locals():
            conn.close()

def check_and_update_database():
    """检查并更新数据库（供app.py调用）"""
    return update_database()

def main():
    """主函数"""
    print("=== 数据库自动更新工具 ===")
    print(f"当前工作目录: {os.getcwd()}")
    
    success = update_database()
    
    if success:
        print("\n数据库更新成功！")
        print("现在所有用户的自动预测设置都有正确的默认值：")
        print("- 默认策略: hot,cold,trend,hybrid,balanced,markov,ml (所有预测策略)")
        print("- 默认地区: hk,macau (香港和澳门)")
    else:
        print("\n数据库更新失败！请检查错误信息并重试。")

if __name__ == '__main__':
    main()
