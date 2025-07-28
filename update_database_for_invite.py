#!/usr/bin/env python3
"""
邀请系统数据库更新脚本
为现有数据库添加邀请相关字段和表
"""

import sqlite3
import os
from datetime import datetime

def update_database():
    """更新数据库结构以支持邀请功能"""
    db_path = 'instance/database.db'
    
    # 检查数据库文件是否存在
    if not os.path.exists(db_path):
        print("❌ 数据库文件不存在，请先运行应用创建数据库")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("🔄 开始更新数据库结构...")
        
        # 1. 检查并添加User表的新字段
        print("\n📝 检查User表结构...")
        
        # 获取User表的列信息
        cursor.execute("PRAGMA table_info(user)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # 需要添加的新字段
        new_fields = [
            ('invited_by', 'VARCHAR(80)'),
            ('invite_code_used', 'VARCHAR(32)'),
            ('invite_activated_at', 'DATETIME')
        ]
        
        for field_name, field_type in new_fields:
            if field_name not in columns:
                try:
                    cursor.execute(f"ALTER TABLE user ADD COLUMN {field_name} {field_type}")
                    print(f"✅ 添加字段: {field_name}")
                except sqlite3.Error as e:
                    print(f"⚠️  字段 {field_name} 可能已存在: {e}")
        
        # 2. 创建InviteCode表
        print("\n📝 创建InviteCode表...")
        
        create_invite_code_table = """
        CREATE TABLE IF NOT EXISTS invite_code (
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
        
        cursor.execute(create_invite_code_table)
        print("✅ InviteCode表创建成功")
        
        # 3. 创建索引以提高查询性能
        print("\n📝 创建索引...")
        
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_invite_code_code ON invite_code(code)",
            "CREATE INDEX IF NOT EXISTS idx_invite_code_created_by ON invite_code(created_by)",
            "CREATE INDEX IF NOT EXISTS idx_invite_code_used_by ON invite_code(used_by)",
            "CREATE INDEX IF NOT EXISTS idx_user_invited_by ON user(invited_by)",
            "CREATE INDEX IF NOT EXISTS idx_user_invite_code_used ON user(invite_code_used)"
        ]
        
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
                print(f"✅ 索引创建成功")
            except sqlite3.Error as e:
                print(f"⚠️  索引可能已存在: {e}")
        
        # 4. 验证表结构
        print("\n🔍 验证更新后的表结构...")
        
        # 检查User表
        cursor.execute("PRAGMA table_info(user)")
        user_columns = cursor.fetchall()
        print(f"User表字段数: {len(user_columns)}")
        
        # 检查InviteCode表
        cursor.execute("PRAGMA table_info(invite_code)")
        invite_columns = cursor.fetchall()
        print(f"InviteCode表字段数: {len(invite_columns)}")
        
        # 5. 创建一些示例数据（可选）
        print("\n📊 检查是否需要创建示例数据...")
        
        # 检查是否有管理员用户
        cursor.execute("SELECT username FROM user WHERE is_admin = 1 LIMIT 1")
        admin_user = cursor.fetchone()
        
        if admin_user:
            admin_username = admin_user[0]
            
            # 检查是否已有邀请码
            cursor.execute("SELECT COUNT(*) FROM invite_code WHERE created_by = ?", (admin_username,))
            existing_codes = cursor.fetchone()[0]
            
            if existing_codes == 0:
                # 创建3个示例邀请码
                import random
                import string
                
                for i in range(3):
                    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                    expires_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    cursor.execute("""
                        INSERT INTO invite_code (code, created_by, expires_at) 
                        VALUES (?, ?, datetime('now', '+30 days'))
                    """, (code, admin_username))
                
                print(f"✅ 为管理员 {admin_username} 创建了3个示例邀请码")
        
        # 提交所有更改
        conn.commit()
        
        print("\n🎉 数据库更新完成！")
        print("\n📋 更新内容总结：")
        print("1. ✅ User表添加了邀请相关字段：")
        print("   - invited_by: 邀请人用户名")
        print("   - invite_code_used: 使用的邀请码")
        print("   - invite_activated_at: 邀请激活时间")
        print("2. ✅ 创建了InviteCode表用于管理邀请码")
        print("3. ✅ 创建了相关索引以提高查询性能")
        print("4. ✅ 创建了示例邀请码（如果有管理员用户）")
        
        print("\n🚀 现在可以使用邀请功能了！")
        
    except sqlite3.Error as e:
        print(f"❌ 数据库更新失败: {e}")
        conn.rollback()
    
    except Exception as e:
        print(f"❌ 更新过程中出现错误: {e}")
        conn.rollback()
    
    finally:
        if conn:
            conn.close()

def backup_database():
    """备份现有数据库"""
    db_path = 'instance/database.db'
    backup_path = f'instance/database_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
    
    if os.path.exists(db_path):
        try:
            import shutil
            shutil.copy2(db_path, backup_path)
            print(f"✅ 数据库已备份到: {backup_path}")
            return True
        except Exception as e:
            print(f"❌ 备份失败: {e}")
            return False
    else:
        print("⚠️  数据库文件不存在，无需备份")
        return True

def main():
    """主函数"""
    print("=" * 60)
    print("🔧 邀请系统数据库更新工具")
    print("=" * 60)
    
    # 询问是否备份
    backup_choice = input("\n是否要先备份现有数据库？(y/n，默认y): ").strip().lower()
    if backup_choice != 'n':
        if not backup_database():
            print("❌ 备份失败，建议手动备份后再继续")
            return
    
    # 确认更新
    confirm = input("\n确认要更新数据库结构吗？(y/n): ").strip().lower()
    if confirm == 'y':
        update_database()
    else:
        print("❌ 用户取消了更新操作")

if __name__ == '__main__':
    main()