#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全回归测试：覆盖存储型 XSS 净化与登录限流/防爆破两处加固。

用法（在项目根目录执行）::

    python scripts/security_regression.py

脚本会把工作目录切到系统临时目录，并使用临时 SQLite 库，
**不会读写真实的 data/lottery_system.db**。
"""

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_WORKDIR = tempfile.mkdtemp(prefix="mark-six-sec-")
os.chdir(_WORKDIR)
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_WORKDIR, 'regression.db').as_posix()}"
os.environ["NOTIFY_ASYNC"] = "0"
os.environ.setdefault("FLASK_ENV", "testing")

from flask import g  # noqa: E402

from app import app, db, _prediction_notice_wrapper_html  # noqa: E402
from models import User, UserNotification, ActivationCodeRequest  # noqa: E402
from notification_service import create_station_notification  # noqa: E402
from auth import send_activation_request_notification  # noqa: E402
from rate_limiter import client_ip, rate_blocked, rate_record, rate_reset  # noqa: E402
from security_policy import password_policy_error  # noqa: E402
from user import _format_notification_item  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def compact(value):
    return (value or "").replace(" ", "").lower()


def main():
    with app.app_context():
        db.drop_all()
        db.create_all()

        admin = User(username="admin_r", email="admin_r@example.com", is_admin=True)
        admin.set_password("AdminPass123")
        normal = User(username="user_r", email="user_r@example.com")
        normal.set_password("UserPass123")
        db.session.add_all([admin, normal])
        db.session.commit()

        print("\n--- 1. 正常预测通知（富文本白名单）---")
        good_html = _prediction_notice_wrapper_html(
            "香港 2026/001 预测",
            "系统已为您生成最新预测",
            '<div class="notice-email-panel" style="background:#ffffff;color:#334155;">'
            '<table class="notice-number-table" cellpadding="0" cellspacing="0">'
            '<tr><td style="text-align:center;color:#ef4444;">01</td></tr></table></div>',
            footer_note="仅供参考",
        )
        rec = create_station_notification(admin, "预测汇总", good_html,
                                          event_type="prediction_generated", preserve_html=True)
        db.session.commit()
        check("富文本通知标记为 html", rec.content_format == "html", rec.content_format)
        check("保留 prediction-summary-notice", "prediction-summary-notice" in rec.content)
        check("保留内联样式", "background:#ffffff" in compact(rec.content))
        check("保留表格结构", "<table" in rec.content and "<td" in rec.content)
        check("保留 <style> 块", "<style>" in rec.content)
        check("保留 !important", "!important" in rec.content)

        print("\n--- 2. 历史记录（content_format=NULL）兼容 ---")
        legacy = UserNotification(user_id=admin.id, event_type="prediction_generated",
                                  title="历史预测通知", content=good_html, content_format=None)
        db.session.add(legacy)
        db.session.commit()
        legacy_card = _format_notification_item(legacy)
        check("历史富文本仍按富文本渲染", legacy_card["html_content"] != "")

        print("\n--- 3. 存储型 XSS：激活申请备注注入 ---")
        payload = ('<div class="prediction-summary-notice">'
                   '<img src=x onerror="window.__pwned=1">'
                   '<script>document.title="pwned"</script>'
                   '<style>body{background:url(http://evil/x)}</style>'
                   '</div>')
        req = ActivationCodeRequest(user_id=normal.id, username="attacker",
                                    email="a@evil.com", request_note=payload, status="pending")
        db.session.add(req)
        db.session.commit()

        with app.test_request_context("/"):
            send_activation_request_notification(req)

        stored = (UserNotification.query.filter_by(user_id=admin.id)
                  .order_by(UserNotification.id.desc()).first())
        check("注入通知已落库", stored is not None)
        check("纯文本通知标记为 text", (stored.content_format or "") == "text", str(stored.content_format))
        check("纯文本通知不进入 HTML 渲染", _format_notification_item(stored)["html_content"] == "")

        stored.content_format = "html"
        db.session.commit()
        forced = _format_notification_item(stored)["html_content"]
        check("强制 html 时 script 被剥离", "<script" not in forced.lower())
        check("强制 html 时 onerror 被剥离", "onerror" not in forced.lower())
        check("强制 html 时 url() 被剥离", "url(" not in forced.lower())
        stored.content_format = "text"
        db.session.commit()

        print("\n--- 4. 页面渲染（真实请求）---")
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = admin.id
            sess["username"] = admin.username
            sess["is_admin"] = True
            sess["_csrf_token"] = "regression-token"
        resp = client.get("/user/notifications")
        html = resp.get_data(as_text=True)
        check("通知页返回 200", resp.status_code == 200, str(resp.status_code))
        check("页面不含可执行 script", "<script>document.title" not in html)
        check("页面不含可执行 img", "<img src=x" not in html)
        check("正常预测通知仍渲染", "prediction-summary-notice" in html)
        check("注入文本被转义", "&lt;div class=" in html)

        print("\n--- 5. 客户端 IP 不可伪造 ---")
        with app.test_request_context("/", headers={"X-Forwarded-For": "1.1.1.1"},
                                      environ_base={"REMOTE_ADDR": "10.9.9.9"}):
            check("X-Forwarded-For 默认不被信任", client_ip() == "10.9.9.9", client_ip())
        with app.test_request_context("/", headers={"CF-Connecting-IP": "2.2.2.2"},
                                      environ_base={"REMOTE_ADDR": "10.9.9.9"}):
            check("CF-Connecting-IP 默认不被信任", client_ip() == "10.9.9.9", client_ip())
        with app.test_request_context("/", headers={"X-Forwarded-For": "not-an-ip"},
                                      environ_base={"REMOTE_ADDR": "10.9.9.9"}):
            check("非法 IP 头被忽略", client_ip() == "10.9.9.9", client_ip())

        rate_reset("unit:test")
        for _ in range(3):
            rate_record("unit:test", 60)
        check("失败计数生效", rate_blocked("unit:test", 3, 60) is True)
        rate_reset("unit:test")
        check("计数可清零", rate_blocked("unit:test", 3, 60) is False)

        print("\n--- 6. 登录防爆破 ---")
        client2 = app.test_client()
        client2.get("/auth/login")
        with client2.session_transaction() as sess:
            token = sess.get("_csrf_token", "")

        def post_login(username, password, remote="10.1.1.1", xff=None):
            headers = {"X-CSRFToken": token}
            if xff:
                headers["X-Forwarded-For"] = xff
            return client2.post("/auth/login",
                                data={"username": username, "password": password,
                                      "csrf_token": token},
                                headers=headers, environ_base={"REMOTE_ADDR": remote})

        lock_hit, hits = False, 0
        for i in range(6):
            resp = post_login("user_r", "wrong-password", xff=f"9.9.9.{i}")
            hits = i + 1
            if "登录失败次数过多" in resp.get_data(as_text=True):
                lock_hit = True
                break
        check("同账号连续失败后提示锁定", lock_hit, f"第 {hits} 次触发")
        resp = post_login("user_r", "UserPass123")
        check("正确密码不受账号锁定影响（避免锁定型 DoS）",
              resp.status_code == 302, str(resp.status_code))

        rate_reset("login:ip:10.2.2.2")
        ip_blocked, ip_hits = False, 0
        for i in range(40):
            resp = post_login(f"ghost_{i}", "x", remote="10.2.2.2", xff=f"7.7.7.{i % 250}")
            ip_hits = i + 1
            if resp.status_code == 429:
                ip_blocked = True
                break
        check("伪造 XFF 无法绕过 IP 维度限制", ip_blocked, f"第 {ip_hits} 次被 429 拦截")

        print("\n--- 7. 密码策略 ---")
        check("6 位密码被拒绝", password_policy_error("abc123") != "")
        check("8 位密码通过", password_policy_error("abcd1234") == "")
        check("超长密码被拒绝", password_policy_error("a" * 129) != "")
        check("空密码被拒绝", password_policy_error("") != "")
        check("纯空白密码被拒绝", password_policy_error("        ") != "")

        print("\n--- 8. 页面冒烟 ---")
        for path in ("/", "/auth/login", "/auth/register", "/auth/forgot_password"):
            resp = client2.get(path)
            check(f"GET {path}", resp.status_code in (200, 302), str(resp.status_code))
        body = client2.get("/auth/register").get_data(as_text=True)
        check("注册页提示 8 位密码", "至少 8 位" in body)
        check("注册页 minlength=8", 'minlength="8"' in body)

        print("\n--- 9. CSP ---")
        page = client2.get("/auth/login")
        csp = page.headers.get("Content-Security-Policy", "")
        check("HTML 响应带 CSP", bool(csp))
        check("object-src 全禁", "object-src 'none'" in csp)
        check("base-uri 限本域", "base-uri 'self'" in csp)
        check("form-action 限本域", "form-action 'self'" in csp)
        check("frame-ancestors 限本域", "frame-ancestors 'self'" in csp)
        check("connect-src 白名单", "connect-src 'self'" in csp)
        check("img-src 禁止外域", "img-src 'self' data: blob:" in csp)

        nonce_match = re.search(r"script-src[^;]*'nonce-([^']+)'", csp)
        check("CSP 使用 nonce", bool(nonce_match))
        nonce = nonce_match.group(1) if nonce_match else ""
        tags = re.findall(r"<script[^>]*>", page.get_data(as_text=True))
        check("页面脚本全部带 nonce", bool(tags) and all("nonce=" in t for t in tags), f"{len(tags)} 个")
        check("脚本 nonce 与响应头一致",
              all(nonce in t for t in tags if "nonce=" in t))
        second = re.search(r"'nonce-([^']+)'",
                           client2.get("/auth/login").headers["Content-Security-Policy"]).group(1)
        check("nonce 每响应随机", second != nonce)

        # 模板编译期注入：模板里写的 <script> 有 nonce，运行时拼进来的没有。
        tpl = app.jinja_env.from_string(
            '<div>{{ payload|safe }}</div><script>ok()</script>'
        )
        rendered = tpl.render(payload='<script>alert(1)</script>', csp_nonce="TESTNONCE")
        check("模板脚本获得 nonce", '<script nonce="TESTNONCE">ok()' in rendered, rendered[:120])
        check("注入脚本拿不到 nonce", '<script>alert(1)</script>' in rendered)

        static_resp = client2.get("/static/css/dark-scrollbar.css")
        check("静态资源使用最小策略",
              "default-src 'none'" in static_resp.headers.get("Content-Security-Policy", ""))

        print("\n--- 10. 邮件正文注入 ---")
        import auth as auth_module
        from notification_service import text_to_html

        captured = {}
        original_send = auth_module._send_html_email
        auth_module._send_html_email = (
            lambda to, subject, body: captured.update({"to": to, "body": body})
        )
        original_notify_admins = auth_module.notify_admins
        auth_module.notify_admins = (
            lambda subject, text, **kwargs: captured.update({"body": kwargs.get("html_content", "")})
        )
        try:
            attacker = User(username='<img src=x onerror="alert(1)">', email="evil@example.com")
            with app.test_request_context("/"):
                auth_module.send_verification_email(attacker, "tok-verify")
                body = captured.get("body", "")
                check("验证邮件不渲染注入标签", "<img src=x" not in body, body[:80])
                check("验证邮件保留转义文本", "&lt;img" in body)

                auth_module.send_reset_email("victim@example.com", "<script>alert(1)</script>", "tok-reset")
                body = captured.get("body", "")
                check("重置邮件剥离脚本", "<script>alert" not in body)
                check("重置邮件保留转义文本", "&lt;script&gt;" in body)

                auth_module.send_email_change_verification(
                    attacker, 'x" onmouseover="alert(1)', "tok-change"
                )
                body = captured.get("body", "")
                check("换绑邮件转义目标邮箱", 'onmouseover="alert' not in body, body[:80])

                request_record = ActivationCodeRequest(
                    user_id=admin.id,
                    username='<svg onload="alert(1)">',
                    email="a@example.com",
                    request_note='"><script>alert(1)</script>',
                    status='<img src=x onerror="alert(1)">',
                )
                db.session.add(request_record)
                db.session.commit()
                auth_module.send_activation_request_notification(request_record)
                body = captured.get("body", "")
                check("申请通知转义备注", "<script>alert" not in body)
                check("申请通知转义状态字段", "<img src=x onerror" not in body)
                check("申请通知转义用户名", '<svg onload="alert' not in body)
        finally:
            auth_module._send_html_email = original_send
            auth_module.notify_admins = original_notify_admins

        plain = text_to_html("<script>alert(1)</script>\n第二行")
        check("纯文本通知按文本转义", "<script" not in plain and "&lt;script&gt;" in plain)
        check("纯文本通知保留换行", "<br>" in plain)

        print("\n--- 11. 邮件通知：用户级订阅默认关闭 ---")
        import notification_service as ns_module

        sub_user = User(username="sub_user_r", email="sub_user_r@example.com",
                        is_admin=False, is_active=True)
        sub_user.set_password("UserPass123")
        db.session.add(sub_user)
        db.session.commit()

        check("新用户默认不订阅邮件",
              ns_module.get_user_notification_config(sub_user).get("email_enabled") is False)

        class _Form(dict):
            def get(self, key, default=None):
                return dict.get(self, key, default)

        mailbox = []
        original_send_html = ns_module.send_html_email
        ns_module.send_html_email = lambda to, subject, body: mailbox.append(to)
        try:
            ns_module.notify_user(sub_user, "测试通知", "内容")
            check("默认关闭时不发通知邮件", mailbox == [], str(mailbox))

            ns_module.save_user_notification_config(sub_user, _Form(email_enabled="on"))
            check("用户开启后状态为已订阅",
                  ns_module.get_user_notification_config(sub_user).get("email_enabled") is True)

            mailit = []
            ns_module.send_html_email = lambda to, subject, body: mailit.append(to)
            ns_module.notify_user(sub_user, "测试通知", "内容")
            check("用户开启后发送通知邮件", mailit == ["sub_user_r@example.com"], str(mailit))

            ns_module.save_user_notification_config(sub_user, _Form())
            mailit.clear()
            ns_module.notify_user(sub_user, "测试通知", "内容")
            check("取消订阅后立即停发", mailit == [], str(mailit))
        finally:
            ns_module.send_html_email = original_send_html

        sub_client = app.test_client()
        with sub_client.session_transaction() as sess:
            sess["user_id"] = sub_user.id
            sess["is_active"] = True
        page = sub_client.get("/user/notification_settings")
        html = page.get_data(as_text=True) if page.status_code == 200 else ""
        check("推送设置页有邮件开关", 'name="email_enabled"' in html, str(page.status_code))
        check("未订阅时开关为未勾选",
              'name="email_enabled"' in html and "checked" not in
              html.split('name="email_enabled"', 1)[1].split(">", 1)[0])

    print()
    print("FAILED:", FAILS if FAILS else "none")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
