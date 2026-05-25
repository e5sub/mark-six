#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过 Python 命令行重置管理员账号密码。

用法示例：
    python reset_admin.py --username admin --password NewPass123
    python reset_admin.py --email admin@example.com --password NewPass123
    python reset_admin.py --list-admins
"""

import argparse
import sys

from app import app
from models import db, User


def build_parser():
    parser = argparse.ArgumentParser(description="重置管理员账号密码")
    parser.add_argument("--username", help="管理员用户名")
    parser.add_argument("--email", help="管理员邮箱")
    parser.add_argument("--password", help="新的管理员密码")
    parser.add_argument(
        "--list-admins",
        action="store_true",
        help="列出当前所有管理员账号",
    )
    return parser


def list_admins():
    admins = User.query.filter(User.is_admin.is_(True)).order_by(User.id.asc()).all()
    if not admins:
        print("当前没有管理员账号。")
        return 1

    print("当前管理员账号：")
    for admin in admins:
        print(f"- id={admin.id} username={admin.username} email={admin.email} active={admin.is_active}")
    return 0


def reset_admin_password(username=None, email=None, password=None):
    if not password:
        print("错误：必须提供 --password")
        return 1

    if len(password) < 6:
        print("错误：新密码至少需要 6 个字符")
        return 1

    query = User.query.filter(User.is_admin.is_(True))
    if username:
        query = query.filter(User.username == username)
    elif email:
        query = query.filter(User.email == email)
    else:
        print("错误：请使用 --username 或 --email 指定管理员账号")
        return 1

    admin = query.first()
    if not admin:
        print("错误：未找到匹配的管理员账号")
        return 1

    admin.set_password(password)
    admin.is_admin = True
    db.session.commit()
    print(f"已重置管理员密码：username={admin.username} email={admin.email}")
    return 0


def main():
    parser = build_parser()
    args = parser.parse_args()

    with app.app_context():
        if args.list_admins:
            return list_admins()
        return reset_admin_password(
            username=(args.username or "").strip() or None,
            email=(args.email or "").strip() or None,
            password=args.password,
        )


if __name__ == "__main__":
    sys.exit(main())
