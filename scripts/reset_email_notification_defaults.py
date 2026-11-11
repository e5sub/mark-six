#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把邮件通知重置为「用户级默认关闭」。

适用场景：升级后希望存量环境立即进入默认关闭状态，而不必等应用重启触发
启动迁移。

用法（项目根目录）::

    python scripts/reset_email_notification_defaults.py            # 只看状态
    python scripts/reset_email_notification_defaults.py --apply    # 实际写入

做的事：

1. 全局开关 ``notify_email_enabled`` 恢复为 ``true``——它表示「邮件通道是否可用」，
   不再代表「所有用户都收信」；
2. 给每个还没有个人开关的用户补一条 ``user_notify_<id>_email_enabled = false``；
3. 写入迁移标记，避免启动时重复执行。

幂等：重复执行不会覆盖用户之后手动开启的选择。
只影响通知类邮件；邮箱验证、换绑确认、重置密码属账户事务邮件，不走这套开关。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import app, db  # noqa: E402
from models import SystemConfig, User  # noqa: E402

GLOBAL_KEY = 'notify_email_enabled'
MIGRATION_FLAG = 'email_notify_user_default_off_v1'
LEGACY_FLAG = 'email_notify_default_off_v1'


def main():
    apply = '--apply' in sys.argv

    with app.app_context():
        users = User.query.all()
        existing = {
            row.key: row.value
            for row in SystemConfig.query.filter(SystemConfig.key.like('user_notify_%_email_enabled')).all()
        }

        pending = []
        for user in users:
            key = f'user_notify_{user.id}_email_enabled'
            if key in existing:
                continue
            pending.append((user.id, user.username, key))

        global_value = SystemConfig.get_config(GLOBAL_KEY, '<未设置>')

        print(f'用户总数：{len(users)}')
        print(f'已有个开关：{len(existing)}，待补齐：{len(pending)}')
        print(f'{GLOBAL_KEY} = {global_value!r}')
        print(f'迁移标记 {MIGRATION_FLAG}：'
              f'{"已存在" if SystemConfig.get_config(MIGRATION_FLAG, "") else "未写入"}')
        print(f'旧标记 {LEGACY_FLAG}：'
              f'{"存在" if SystemConfig.get_config(LEGACY_FLAG, "") else "不存在"}')

        for user_id, username, _ in pending[:10]:
            print(f'  - {user_id} {username}')
        if len(pending) > 10:
            print(f'  ... 另有 {len(pending) - 10} 个')

        if not apply:
            print('\n这是预览模式，未写入任何数据。加 --apply 执行。')
            return 0

        # 旧版迁移曾把全局开关改成 false，这里还原为通道可用
        if global_value != 'true':
            SystemConfig.set_config(
                GLOBAL_KEY, 'true', '启用邮件通道（用户需在用户中心自行开启接收）'
            )
            print(f'  {GLOBAL_KEY}: {global_value!r} -> "true"')

        for user_id, username, key in pending:
            SystemConfig.set_config(
                key, 'false', f'User notification config {user_id}:email_enabled'
            )
            print(f'  {key} = "false"  ({username})')

        SystemConfig.set_config(MIGRATION_FLAG, '1', '邮件通知用户级默认关闭迁移标记')
        db.session.commit()

        print('\n完成。用户可在「用户中心 → 推送设置 → 邮件通知」自行开启。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
