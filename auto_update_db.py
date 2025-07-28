#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库自动更新脚本
"""

import sqlite3
import os
from datetime import datetime

def check_and_update_database():
    """检查并更新数据库结构"""
    print("正在检查数据库结构...")
    
    # 确保数据目录存在
    data_dir = os.path.join(os.getcwd(), 'data')
    os.makedirs(data_dir, exist_ok=True)
    
    # 数据库路径
    db_path = os.path.join(data_dir, 'lottery_system.db')
    
    # 如果数据库文件不存在，则不需要更新
    if not os.path.exists(db_path):
        print("数据库文件不存在，将创建新数据库")
        return
    
    try:
        # 连接数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查prediction_record表是否存在actual_special_zodiac字段
        cursor.execute("PRAGMA table_info(prediction_record)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        # 如果不存在actual_special_zodiac字段，则添加
        if 'actual_special_zodiac' not in column_names:
            print("正在添加actual_special_zodiac字段到prediction_record表...")
            cursor.execute("ALTER TABLE prediction_record ADD COLUMN actual_special_zodiac VARCHAR(10)")
            conn.commit()
            print("✅ 成功添加actual_special_zodiac字段")
            
            # 更新现有记录的actual_special_zodiac字段
            print("正在更新现有记录的actual_special_zodiac字段...")
            
            # 获取所有已更新结果的预测记录
            cursor.execute("""
                SELECT id, actual_special_number, region
                FROM prediction_record
                WHERE is_result_updated = 1 AND actual_special_number IS NOT NULL
            """)
            records = cursor.fetchall()
            
            # 定义生肖映射函数
            ZODIAC_MAPPING_SEQUENCE = ("虎", "兔", "龙", "蛇", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马")
            
            def get_number_zodiac(number):
                try:
                    num = int(number)
                    if not 1 <= num <= 49: return ""
                    return ZODIAC_MAPPING_SEQUENCE[(num - 1) % 12]
                except:
                    return ""
            
            # 更新每条记录
            for record in records:
                record_id, special_number, region = record
                if special_number:
                    zodiac = get_number_zodiac(special_number)
                    cursor.execute(
                        "UPDATE prediction_record SET actual_special_zodiac = ? WHERE id = ?",
                        (zodiac, record_id)
                    )
            
            conn.commit()
            print(f"✅ 成功更新了 {len(records)} 条记录的actual_special_zodiac字段")
        
        # 关闭数据库连接
        conn.close()
        
    except Exception as e:
        print(f"更新数据库结构时出错: {e}")
        try:
            conn.close()
        except:
            pass

if __name__ == "__main__":
    check_and_update_database()