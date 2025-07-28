#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
六合彩数据分析系统启动脚本
"""

import os
import sys
from app import app, init_database

def main():
    """主函数"""
    print("=" * 60)
    print("六合彩数据分析系统")
    print("=" * 60)
    
    try:
        # 初始化数据库
        print("正在初始化数据库...")
        init_database()
        print("✓ 数据库初始化完成")
        
        # 检查数据文件
        if not os.path.exists('data/hk.json'):
            print("正在下载香港六合彩数据...")
            from app import update_hk_data_from_source
            if update_hk_data_from_source():
                print("✓ 数据下载完成")
            else:
                print("⚠ 数据下载失败，将在首次访问时重试")
        
        print("\n系统信息:")
        print("- 默认管理员账号: admin")
        print("- 默认管理员密码: admin123")
        print("- 请在首次登录后修改管理员密码")
        print("- 请在管理后台配置AI API和邮箱服务")
        
        print("\n启动Web服务器...")
        print("访问地址: http://localhost:5000")
        print("按 Ctrl+C 停止服务器")
        print("=" * 60)
        
        # 启动Flask应用
        app.run(
            host='0.0.0.0',
            port=5000,
            debug=True,
            use_reloader=False  # 避免重复初始化
        )
        
    except KeyboardInterrupt:
        print("\n\n服务器已停止")
    except Exception as e:
        print(f"\n启动失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()