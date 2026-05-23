#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库自动更新脚本
用于更新现有数据库结构和数据
"""

import os
import sqlite3
from datetime import datetime

# 数据库文件路径
DB_PATH = os.path.join(os.getcwd(), 'data', 'lottery_system.db')
DB_TYPE = os.environ.get("DB_TYPE", "sqlite").lower()
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def _using_mysql():
    if DB_TYPE in ("mysql", "mariadb"):
        return True
    return DATABASE_URL.lower().startswith("mysql")

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

def update_database():
    """更新数据库结构和数据"""
    if _using_mysql():
        print("MySQL configured, skipping sqlite auto update.")
        return True
    if not check_database_exists():
        print(f"数据库文件不存在: {DB_PATH}")
        print("请先运行 create_db.py 创建数据库")
        return False
    
    print(f"正在更新数据库: {DB_PATH}")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
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
            SET auto_prediction_strategies = 'hot,cold,trend,hybrid,balanced,ml' 
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
            SET auto_prediction_strategies = 'hot,cold,trend,hybrid,balanced,ml'
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
        print("- 默认策略: hot,cold,trend,hybrid,balanced,ml (所有预测策略)")
        print("- 默认地区: hk,macau (香港和澳门)")
    else:
        print("\n数据库更新失败！请检查错误信息并重试。")

if __name__ == '__main__':
    main()
