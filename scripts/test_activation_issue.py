#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：管理员后台发放激活码申请必须成功。

新生成的激活码需要先 flush 拿到 id，use_code() 的原子占位 UPDATE
才能正确匹配；否则会被误判为“激活码已被使用”。

用法（在项目根目录执行）::

    python scripts/test_activation_issue.py

使用内存 SQLite，不读写真实数据库。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from activation_code_routes import activation_code_bp  # noqa: E402
import activation_code_routes as activation_routes  # noqa: E402
from models import ActivationCode, ActivationCodeRequest, User, db  # noqa: E402


def main():
    # 用桩替代通知发送，测试只关注发放与激活逻辑
    activation_routes._send_activation_request_result_notification = lambda *args, **kwargs: None

    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SECRET_KEY="test-secret",
        TESTING=True,
    )
    db.init_app(app)
    app.register_blueprint(activation_code_bp)

    with app.app_context():
        db.create_all()
        admin = User(username="admin_test", email="admin_test@example.com", is_admin=True, is_active=True)
        admin.set_password("AdminPass123")
        applicant = User(username="applicant_test", email="applicant_test@example.com")
        applicant.set_password("UserPass123")
        db.session.add_all([admin, applicant])
        db.session.commit()
        req = ActivationCodeRequest(
            user_id=applicant.id,
            username=applicant.username,
            email=applicant.email,
            status="pending",
        )
        db.session.add(req)
        db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["is_admin"] = True

    response = client.post("/admin/activation_codes/requests/1/issue", json={"validity_type": "month"})
    payload = response.get_json() or {}

    with app.app_context():
        code_rows = ActivationCode.query.all()
        request_row = db.session.get(ActivationCodeRequest, 1)
        applicant = db.session.get(User, 2)
        checks = [
            ("发放接口返回成功", payload.get("success") is True, str(payload)),
            ("返回激活码与库中记录一致", len(code_rows) == 1 and payload.get("code") == code_rows[0].code),
            ("激活码已标记为使用", len(code_rows) == 1 and code_rows[0].is_used is True),
            ("申请单状态变为 used", request_row.status == "used", request_row.status),
            ("申请单记录发放码", bool(payload.get("code")) and request_row.issued_code == payload.get("code")),
            ("用户已激活", applicant.is_active is True),
        ]

    failed = 0
    for name, ok, *extra in checks:
        print(("PASS  " if ok else "FAIL  ") + name + (f"  [{extra[0]}]" if extra and not ok else ""))
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
