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

def check_database_exists():
    """检查数据库是否存在"""
    return os.path.exists(DB_PATH)

def check_column_exists(cursor, table_name, column_name):
    """检查表中是否存在指定列"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    return column_name in columns

def update_database():
    """更新数据库结构和数据"""
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
        
        # 更新现有用户的 auto_prediction_strategies 字段
        print("更新现有用户的自动预测策略设置...")
        cursor.execute('''
            UPDATE user 
            SET auto_prediction_strategies = 'balanced' 
            WHERE auto_prediction_strategies IS NULL 
               OR auto_prediction_strategies = '' 
               OR auto_prediction_strategies = 'NULL'
        ''')
        updated_strategies = cursor.rowcount
        print(f"✓ 更新了 {updated_strategies} 个用户的自动预测策略设置")
        
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

def main():
    """主函数"""
    print("=== 数据库自动更新工具 ===")
    print(f"当前工作目录: {os.getcwd()}")
    
    success = update_database()
    
    if success:
        print("\n数据库更新成功！")
        print("现在所有用户的自动预测设置都有正确的默认值：")
        print("- 默认策略: balanced (均衡预测)")
        print("- 默认地区: hk,macau (香港和澳门)")
    else:
        print("\n数据库更新失败！请检查错误信息并重试。")

if __name__ == '__main__':
    main()