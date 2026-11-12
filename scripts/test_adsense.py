"""Google AdSense 配置功能验证（临时目录运行，不碰真实库）。"""
import os
import sys
import tempfile

sys.path.insert(0, r"D:\github\mark-six")

BASE = tempfile.mkdtemp(prefix="ms_adsense_")
os.environ["DATABASE_URL"] = f"sqlite:///{BASE}/data/t.db"
os.environ["NOTIFY_ASYNC"] = "0"
os.environ["SECRET_KEY"] = "test-secret"
os.chdir(BASE)

from app import app, db  # noqa: E402
from models import SystemConfig, User  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

with app.app_context():
    db.create_all()
    admin = User(username="admin", email="admin@example.com",
                 password_hash=generate_password_hash("AdminPass123"))
    admin.is_admin = True
    admin.is_active = True
    db.session.add(admin)
    db.session.commit()

client = app.test_client()

# 1. 未开启广告：页面无 adsbygoogle，/ads.txt 404
r = client.get("/auth/login")
assert r.status_code == 200, r.status_code
assert "adsbygoogle.js" not in r.get_data(as_text=True)
r = client.get("/ads.txt")
assert r.status_code == 404, f"ads.txt should 404, got {r.status_code}"
print("[1] 默认关闭：页面无广告脚本，/ads.txt 404  ✓")

# 2. 登录管理员，保存 AdSense 配置
import re as _re_csrf
login_page = client.get("/auth/login").get_data(as_text=True)
csrf = _re_csrf.search(r'name="csrf-token"[^>]*content="([^"]+)"', login_page) \
    or _re_csrf.search(r'name="csrf_token"[^>]*value="([^"]+)"', login_page) \
    or _re_csrf.search(r'name="csrf_token"\s+value="([^"]+)"', login_page)
csrf = csrf.group(1)
client.post("/auth/login", data={"username": "admin", "password": "AdminPass123", "csrf_token": csrf})

page = client.get("/admin/system_config").get_data(as_text=True)
csrf = _re_csrf.search(r'name="csrf-token"[^>]*content="([^"]+)"', page).group(1)
_headers = {"X-CSRFToken": csrf, "Content-Type": "application/json"}

r = client.post("/admin/system_config/save", json={
    "adsense_enabled": "true",
    "adsense_client_id": "ca-pub-1234567890123456",
    "adsense_adsense_txt": "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0",
}, headers=_headers)
assert r.status_code == 200, r.status_code
print("[2] 后台保存广告配置  ✓")

# 3. 配置页渲染新区块
r = client.get("/admin/system_config")
html = r.get_data(as_text=True)
assert "广告配置" in html and "adsense_client_id" in html and "adsense_adsense_txt" in html
print("[3] 配置页出现广告配置区块  ✓")

# 4. 开启后前台加载脚本（带 nonce）、CSP 放行广告域名、ads.txt 可访问
r = client.get("/")
assert r.status_code == 200, r.status_code
body = r.get_data(as_text=True)
assert "adsbygoogle.js?client=ca-pub-1234567890123456" in body
import re as _re
tag = _re.search(r'<script[^>]*adsbygoogle[^>]*>', body).group(0)
assert 'nonce="' in tag, f"广告脚本没带上 nonce: {tag}"
csp = r.headers.get("Content-Security-Policy", "")
for host in ("pagead2.googlesyndication.com", "googleads.g.doubleclick.net",
             "tpc.googlesyndication.com", "fundingchoicesmessages.google.com"):
    assert host in csp, f"CSP 缺少 {host}"
assert "connect-src" in csp and "frame-src" in csp
print(f"[4] 广告脚本带 nonce 且 CSP 已放行  ✓")
print(f"    script tag: {tag[:100]}")

r = client.get("/ads.txt")
assert r.status_code == 200
assert r.mimetype == "text/plain"
assert "pub-1234567890123456, DIRECT" in r.get_data(as_text=True)
print("[5] /ads.txt 返回配置内容  ✓")

# 6. 非法 client id 不生效（自动回退到关闭状态）
client.post("/admin/system_config/save", json={
    "adsense_enabled": "true",
    "adsense_client_id": '"><script>alert(1)</script>',
    "adsense_adsense_txt": "bad line <script>x</script>\ngoogle.com, pub-1, DIRECT, abc",
}, headers=_headers)
r = client.get("/auth/login")
body = r.get_data(as_text=True)
assert "adsbygoogle.js?client=" not in body, "非法 ID 不应注入脚本"
r = client.get("/ads.txt")
assert r.status_code == 404, "非法内容不应生成 ads.txt"
print("[6] 非法发布商 ID / ads.txt 内容被拒（XSS 防护）✓")

# 7. 关闭开关后恢复
client.post("/admin/system_config/save", json={
    "adsense_enabled": "false",
    "adsense_client_id": "ca-pub-1234567890123456",
    "adsense_adsense_txt": "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0",
}, headers=_headers)
r = client.get("/")
assert r.status_code == 200
assert "adsbygoogle.js" not in r.get_data(as_text=True)
r = client.get("/ads.txt")
assert r.status_code == 404
print("[7] 关闭开关后广告脚本与 ads.txt 均停止  ✓")

print("\n全部 7 项验证通过 ✅")
