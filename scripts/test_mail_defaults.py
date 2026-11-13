"""邮件通知默认值清理 + SMTP TLS/SSL 联动验证（临时目录运行）。"""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, r"D:\github\mark-six")

BASE = tempfile.mkdtemp(prefix="ms_mail_")
os.environ["DATABASE_URL"] = f"sqlite:///{BASE}/data/t.db"
os.environ["NOTIFY_ASYNC"] = "0"
os.environ["SECRET_KEY"] = "test-secret"
os.chdir(BASE)

from app import app, db  # noqa: E402
from models import SystemConfig, User  # noqa: E402
from notification_service import _smtp_connection_mode, send_html_email  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

with app.app_context():
    db.create_all()
    admin = User(username="admin", email="admin@example.com",
                 password_hash=generate_password_hash("AdminPass123"))
    admin.is_admin = True
    admin.is_active = True
    db.session.add(admin)
    db.session.commit()

# 1. 启动迁移代码已删除：启动过程不应写入迁移标记，也不强制写用户邮件开关
with app.app_context():
    flags = SystemConfig.query.filter(SystemConfig.key.in_(
        ['email_notify_user_default_off_v1', 'email_notify_default_off_v1']
    )).all()
    assert not flags, f"迁移标记仍在: {[f.key for f in flags]}"
    user_keys = SystemConfig.query.filter(
        SystemConfig.key.like('user_notify_%_email_enabled')).all()
    assert not user_keys, f"启动仍在写用户邮件开关: {[k.key for k in user_keys]}"
print("[1] 启动不再执行迁移、不再写迁移标记和用户开关  ✓")

# 2. 注册后默认关闭：新注册用户 email_enabled 默认 False
with app.app_context():
    from models import db as _db
    new_user = User(username="newbie", email="n@example.com",
                    password_hash=generate_password_hash("UserPass123"))
    new_user.is_active = True
    _db.session.add(new_user)
    _db.session.commit()

    from notification_service import get_user_notification_config
    cfg = get_user_notification_config(new_user)
    assert cfg.get('email_enabled') is False, f"新用户邮件通知默认应为关: {cfg}"
print("[2] 注册后邮件通知默认关闭  ✓")

# 3. TLS/SSL 联动逻辑
def set_cfg(key, value):
    SystemConfig.set_config(key, value)

with app.app_context():
    # 默认：TLS 开（默认 true），SSL 自动 → 关
    SystemConfig.query.filter(SystemConfig.key.in_(['smtp_use_tls', 'smtp_use_ssl'])).delete()
    _db.session.commit()
    tls, ssl = _smtp_connection_mode()
    assert (tls, ssl) == (True, False), f"默认应为 TLS开/SSL关: {(tls, ssl)}"

    # TLS 开 + SSL 自动 → SSL 关
    set_cfg('smtp_use_tls', 'true'); set_cfg('smtp_use_ssl', '')
    tls, ssl = _smtp_connection_mode()
    assert (tls, ssl) == (True, False), f"TLS开时SSL应默认关: {(tls, ssl)}"

    # TLS 关 + SSL 自动 → SSL 开
    set_cfg('smtp_use_tls', 'false'); set_cfg('smtp_use_ssl', '')
    tls, ssl = _smtp_connection_mode()
    assert (tls, ssl) == (False, True), f"TLS关时SSL应默认开: {(tls, ssl)}"

    # TLS 关 + SSL 显式关 → 都关（纯明文，管理员显式选择）
    set_cfg('smtp_use_ssl', 'false')
    tls, ssl = _smtp_connection_mode()
    assert (tls, ssl) == (False, False), f"SSL显式关应生效: {(tls, ssl)}"

    # TLS 开 + SSL 显式开 → SSL 优先（直连）
    set_cfg('smtp_use_tls', 'true'); set_cfg('smtp_use_ssl', 'true')
    tls, ssl = _smtp_connection_mode()
    assert (tls, ssl) == (True, True), f"SSL显式开应生效: {(tls, ssl)}"
print("[3] TLS/SSL 联动逻辑（自动跟随 + 显式覆盖）✓")

# 4. 发信连接类型按配置选择
SMTP_USER = 'u@example.com'; SMTP_PASS = 'p'
with app.app_context():
    set_cfg('smtp_server', 'smtp.example.com')
    set_cfg('smtp_port', '587')
    set_cfg('smtp_username', SMTP_USER)
    set_cfg('smtp_password', SMTP_PASS)

    import smtplib as _smtplib

    def send():
        send_html_email('to@example.com', 'test', '<p>hi</p>')

    # TLS 开 / SSL 自动 → SMTP + starttls
    set_cfg('smtp_use_tls', 'true'); set_cfg('smtp_use_ssl', '')
    with patch('smtplib.SMTP_SSL') as ssl_mock, \
         patch.object(_smtplib, 'SMTP') as plain_mock:
        send()
        assert ssl_mock.call_count == 0
        assert plain_mock.call_count == 1
        inst = plain_mock.return_value
        inst.starttls.assert_called_once()
    print("[4a] TLS 开 → SMTP(587) + STARTTLS，不用 SSL  ✓")

    # TLS 关 / SSL 自动 → SMTP_SSL，不调 starttls
    set_cfg('smtp_use_tls', 'false'); set_cfg('smtp_use_ssl', '')
    with patch('smtplib.SMTP_SSL') as ssl_mock, \
         patch.object(_smtplib, 'SMTP') as plain_mock:
        send()
        assert ssl_mock.call_count == 1
        assert plain_mock.call_count == 0
        ssl_mock.return_value.starttls.assert_not_called()
    print("[4b] TLS 关 → SSL 直连（SMTP_SSL，端口 465）✓")

    # SSL 显式关 + TLS 显式关 → 明文 SMTP
    set_cfg('smtp_use_ssl', 'false')
    with patch('smtplib.SMTP_SSL') as ssl_mock, \
         patch.object(_smtplib, 'SMTP') as plain_mock:
        send()
        assert ssl_mock.call_count == 0 and plain_mock.call_count == 1
        plain_mock.return_value.starttls.assert_not_called()
    print("[4c] 双关 → 明文 SMTP（管理员显式选择）✓")

# 5. 后台配置页渲染 + 保存 TLS/SSL
client = app.test_client()
lp = client.get("/auth/login").get_data(as_text=True)
import re
csrf_m = (re.search(r'name="csrf-token"[^>]*content="([^"]+)"', lp)
          or re.search(r'name="csrf_token"[^>]*value="([^"]+)"', lp))
client.post("/auth/login", data={"username": "admin", "password": "AdminPass123", "csrf_token": csrf_m.group(1)})
page = client.get("/admin/system_config")
html = page.get_data(as_text=True)
assert page.status_code == 200
assert 'name="smtp_use_tls"' in html and 'name="smtp_use_ssl"' in html
csrf = re.search(r'name="csrf-token"[^>]*content="([^"]+)"', html).group(1)
r = client.post("/admin/system_config/save", json={
    "smtp_use_tls": "false", "smtp_use_ssl": "true",
}, headers={"X-CSRFToken": csrf, "Content-Type": "application/json"})
assert r.status_code == 200
with app.app_context():
    assert SystemConfig.get_config('smtp_use_tls') == 'false'
    assert SystemConfig.get_config('smtp_use_ssl') == 'true'
print("[5] 后台 TLS/SSL 配置渲染 + 保存落库  ✓")

print("\n全部 5 项验证通过 ✅")
