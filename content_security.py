"""Content-Security-Policy 构建与脚本 nonce 注入。

设计目标：在不改动模板的前提下，给现有页面加一层能真正收窄 XSS 影响的 CSP。

nonce 必须在**模板编译期**写入，不能在响应期扫描正文补写——否则攻击者注入
的 ``<script>`` 也会被一起补上 nonce，等于亲手给注入脚本发了通行证。所以
这里提供 ``inject_template_nonce()``，由 Jinja 扩展在解析模板源码时把
``<script>`` 改写成 ``<script nonce="{{ csp_nonce }}">``；用户注入的内容不经过
模板编译，因此永远拿不到 nonce，被 ``script-src`` 直接拦下。

除了脚本，CSP 还用 ``object-src 'none'`` / ``base-uri 'self'`` /
``form-action 'self'`` / ``connect-src`` / ``img-src`` / ``frame-ancestors``
掐断外带通道：即使真有脚本执行，也无法加载远程 payload 或把数据送出去。

内联事件处理器（``onclick=`` 等）默认保留 ``'unsafe-inline'``：全站 60+ 处
写法各异（含模板字符串插值），强行改成 hash 白名单在部分浏览器上会让按钮
集体失效。需要更强隔离时把 ``CSP_INLINE_HANDLERS=0``，此时会扫描本响应里
出现的处理器原文，折算成 ``'unsafe-hashes'`` + sha256 白名单，只有页面里真实
存在过的处理器才放行。
"""

import base64
import hashlib
import html as html_lib
import re
import secrets

__all__ = [
    "new_nonce",
    "inject_template_nonce",
    "collect_handler_hashes",
    "find_unprotected_scripts",
    "build_policy",
    "MINIMAL_POLICY",
]

# 样式与字体来自这两个 CDN；Turnstile 需要脚本 + 内嵌 frame。
_STYLE_SOURCES = ("https://cdnjs.cloudflare.com", "https://fastly.jsdelivr.net")
_TURNSTILE = "https://challenges.cloudflare.com"

# 模板源码里的 <script>：已带 nonce 的跳过，避免重复处理。
_TEMPLATE_SCRIPT_TAG = re.compile(r"<script(?![^>]*\bnonce\s*=)(\s[^>]*)?>", re.IGNORECASE)
# 渲染后的正文里仍未带 nonce 的 <script>（= 不是模板产出的，需要关注）
_UNPROTECTED_SCRIPT_TAG = re.compile(r"<script(?![^>]*\bnonce\s*=)(\s[^>]*)?>", re.IGNORECASE)
# 内联事件处理器：on*="..." 或 on*='...'
_EVENT_ATTR = re.compile(r"""\son[a-z]+\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)

_TEMPLATE_NONCE_TAG = '<script nonce="{{ csp_nonce }}"'

# 单个响应最多带多少个 hash，超出就退回 unsafe-inline，避免响应头失控。
_MAX_HANDLER_HASHES = 200

# 非 HTML 响应（JSON / 静态文件）用这套：一旦被当成文档解析，什么都不能加载。
MINIMAL_POLICY = "default-src 'none'; frame-ancestors 'self'"


def new_nonce():
    """每个响应一个 nonce。"""
    return secrets.token_urlsafe(18)


def inject_template_nonce(source):
    """把模板源码里的 ``<script>`` 改写为带 nonce 的形式（Jinja 扩展入口）。"""
    if not source or "<script" not in source.lower():
        return source
    return _TEMPLATE_SCRIPT_TAG.sub(
        lambda m: _TEMPLATE_NONCE_TAG + (m.group(1) or "") + ">",
        source,
    )


def find_unprotected_scripts(html_body):
    """返回渲染后仍未带 nonce 的 ``<script>`` 片段，用于自检告警。"""
    if not html_body:
        return []
    return [m.group(0)[:80] for m in _UNPROTECTED_SCRIPT_TAG.finditer(html_body)]


def _sha256_b64(text):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def collect_handler_hashes(html_body, limit=_MAX_HANDLER_HASHES):
    """把页面里出现的内联事件处理器折算成 CSP hash 白名单。

    返回 ``(hash 列表, 是否完整)``。浏览器计算 hash 用的是属性值经实体解码后
    的文本，所以这里先 ``unescape`` 再算。超过 ``limit`` 时第二个返回值为
    False，调用方应退回 ``'unsafe-inline'``。
    """
    hashes = []
    if not html_body:
        return hashes, True

    for match in _EVENT_ATTR.finditer(html_body):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        digest = _sha256_b64(html_lib.unescape(raw or ""))
        if digest not in hashes:
            hashes.append(digest)
        if len(hashes) > limit:
            return hashes, False
    return hashes, True


def build_policy(nonce, handler_hashes=None, allow_inline_handlers=True, report_uri=None):
    """拼出完整 CSP。``handler_hashes`` 仅在 ``allow_inline_handlers=False`` 时生效。"""
    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'self'",
        "form-action 'self'",
        "manifest-src 'self'",
        "worker-src 'self' blob:",
        # nonce 只发给模板编译期注入的脚本；strict-dynamic 让带 nonce 的脚本
        # 动态加载的依赖（Turnstile、Bootstrap 按需模块）继续可用。
        f"script-src 'self' 'nonce-{nonce}' 'strict-dynamic'",
    ]

    if allow_inline_handlers:
        directives.append("script-src-attr 'unsafe-inline'")
    else:
        hashes = " ".join(f"'{item}'" for item in (handler_hashes or []))
        directives.append(f"script-src-attr 'unsafe-hashes' {hashes}".rstrip())

    directives.extend([
        "style-src 'self' 'unsafe-inline' " + " ".join(_STYLE_SOURCES),
        "font-src 'self' data: " + " ".join(_STYLE_SOURCES),
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "connect-src 'self' " + _TURNSTILE,
        "frame-src 'self' " + _TURNSTILE,
    ])

    if report_uri:
        directives.append(f"report-uri {report_uri}")

    return "; ".join(directives)
