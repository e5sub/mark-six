#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：移动端注单的独立结算接口。

覆盖：正常结算、未开奖期号、重复结算、越权记录，以及旧的提交入口
（settle=false）创建待结算记录后仍可用新接口结算。

用法（在项目根目录执行）::

    python scripts/test_manual_bet_settle.py

使用内存 SQLite，不读写真实数据库。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from api_mobile import mobile_api_bp  # noqa: E402
from models import LotteryDraw, ManualBetRecord, User, db  # noqa: E402


CSRF_TOKEN = "test-csrf-token"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["_mobile_csrf_token"] = CSRF_TOKEN


def _post(client, url, payload=None):
    return client.post(
        url,
        json=payload,
        headers={"X-CSRF-Token": CSRF_TOKEN},
    )


def main():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SECRET_KEY="test-secret",
        TESTING=True,
    )
    db.init_app(app)
    app.register_blueprint(mobile_api_bp)

    with app.app_context():
        db.create_all()
        u1 = User(username="settle_user1", email="settle1@example.com", is_active=True)
        u1.set_password("Pass12345")
        u2 = User(username="settle_user2", email="settle2@example.com", is_active=True)
        u2.set_password("Pass12345")
        db.session.add_all([u1, u2])
        db.session.commit()

        db.session.add(
            LotteryDraw(
                region="macau",
                draw_id="2026118",
                normal_numbers="01,02,03,04,05,06",
                special_number="08",
                special_zodiac="羊",
                raw_zodiac="鼠,牛,虎,兔,龙,蛇,马,羊",
            )
        )
        record = ManualBetRecord(
            user_id=u1.id,
            region="macau",
            period="2026118",
            bettor_name="tester",
            selected_numbers="8",
            odds_number=24,
            stake_special=20,
            total_stake=20,
        )
        pending_other_period = ManualBetRecord(
            user_id=u1.id,
            region="macau",
            period="2026119",
            selected_numbers="8",
            odds_number=24,
            stake_special=20,
            total_stake=20,
        )
        db.session.add_all([record, pending_other_period])
        db.session.commit()
        record_id = record.id
        other_period_id = pending_other_period.id
        u1_id, u2_id = u1.id, u2.id

    client = app.test_client()
    checks = []

    # 1. 越权：其他用户的记录不可结算
    _login(client, u2_id)
    resp = _post(client, f"/api/mobile/manual_bets/{record_id}/settle")
    payload = resp.get_json() or {}
    checks.append(("越权记录返回 404", resp.status_code == 404, str(payload)))

    # 2. 未开奖：期号没有对应开奖记录
    _login(client, u1_id)
    resp = _post(client, f"/api/mobile/manual_bets/{other_period_id}/settle")
    payload = resp.get_json() or {}
    checks.append(("未开奖期号返回 404", resp.status_code == 404, str(payload)))

    # 3. 正常结算：08 命中，20 元 × 24 倍 - 20 元 = 460 元
    resp = _post(client, f"/api/mobile/manual_bets/{record_id}/settle")
    payload = resp.get_json() or {}
    checks.append((
        "正常结算返回盈亏 460 元",
        resp.status_code == 200
        and payload.get("success") is True
        and payload.get("total_profit") == 460,
        str(payload),
    ))

    # 4. 重复结算被拒绝
    resp = _post(client, f"/api/mobile/manual_bets/{record_id}/settle")
    payload = resp.get_json() or {}
    checks.append(("重复结算返回 400", resp.status_code == 400, str(payload)))

    # 5. 旧入口 settle=false 创建待结算记录，仍可用新接口结算
    resp = _post(
        client,
        "/api/mobile/manual_bets",
        {
            "settle": False,
            "region": "macau",
            "period": "2026118",
            "bet_number": True,
            "numbers": [8],
            "stake_special": 20,
            "odds_number": 24,
            "bettor_name": "tester",
        },
    )
    payload = resp.get_json() or {}
    new_id = payload.get("record_id")
    checks.append(("旧入口创建待结算记录", resp.status_code == 200 and bool(new_id), str(payload)))
    if new_id:
        resp = _post(client, f"/api/mobile/manual_bets/{new_id}/settle")
        payload = resp.get_json() or {}
        checks.append(("新接口结算旧入口记录", resp.status_code == 200 and payload.get("total_profit") == 460, str(payload)))

    with app.app_context():
        row = db.session.get(ManualBetRecord, record_id)
        checks.append(("结算结果写回记录", row.total_profit == 460 and row.special_number == "08", str(row.total_profit)))
        other = db.session.get(ManualBetRecord, other_period_id)
        checks.append(("未开奖记录保持待结算", other.total_profit is None, str(other.total_profit)))

    failed = 0
    for name, ok, *extra in checks:
        print(("PASS  " if ok else "FAIL  ") + name + (f"  [{extra[0]}]" if extra and not ok else ""))
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
