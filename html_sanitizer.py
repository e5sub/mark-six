"""HTML 白名单净化器。

用于把「富文本通知」安全地渲染到页面上（Jinja ``|safe`` 之前必须过一遍）。

策略
----
* 标签白名单：只保留邮件/通知模板实际用到的排版标签；
* ``script`` / ``iframe`` / ``object`` / ``form`` / ``svg`` 等危险容器
  连同**内容**一并丢弃，不产生任何输出；
* 属性白名单：任何 ``on*`` 事件处理器一律丢弃，``href`` 只允许
  http(s)/mailto/tel/相对路径；
* ``style`` 属性与 ``<style>`` 块做 CSS 净化，去掉 ``expression()``、
  ``javascript:``、``@import``、``url()``、``position:fixed`` 等可用于
  脚本执行、外链跟踪或界面劫持的写法。

注意：本模块只做「输出前净化」，输入侧仍应尽量使用 ``escape()``。
"""

from __future__ import annotations

import re
from functools import lru_cache
from html import escape, unescape
from html.parser import HTMLParser

__all__ = ["sanitize_html", "looks_like_rich_notification"]

# 允许保留的标签（覆盖 _prediction_notice_wrapper_html 的排版需求）
_ALLOWED_TAGS = {
    "div", "span", "p", "br", "hr", "strong", "b", "em", "i", "u", "s",
    "small", "sub", "sup", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
    "ul", "ol", "li", "dl", "dt", "dd", "blockquote",
    "a", "center", "font", "section", "article", "header", "footer",
    "style",
}

_VOID_TAGS = {"br", "hr"}

# 这些标签连同内部内容整体丢弃
_DROP_WITH_CONTENT = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet",
    "template", "noscript", "svg", "math", "form", "input", "textarea",
    "select", "option", "button", "link", "meta", "base", "audio", "video",
    "source", "canvas", "marquee", "xml",
}

_GLOBAL_ATTRS = {
    "class", "id", "style", "title", "align", "valign", "bgcolor", "color",
    "face", "size", "width", "height", "border", "cellpadding", "cellspacing",
    "colspan", "rowspan", "dir", "lang", "nowrap", "scope",
}

_TAG_ATTRS = {
    "a": {"href", "target", "rel", "name"},
    "style": {"type", "media"},
}

_URL_ATTRS = {"href", "src", "action", "formaction", "background", "poster"}

_ALLOWED_SCHEMES = ("http://", "https://", "mailto:", "tel:")
_ALLOWED_RELATIVE = ("/", "#", "?", "./", "../")

_CSS_DANGEROUS = re.compile(
    r"url\s*\([^)]*\)"              # 外部资源加载 / 老式 javascript: 载体
    r"|expression\s*\([^)]*\)"
    r"|@import[^;]*;?"
    r"|-moz-binding\s*:[^;]*;?"
    r"|behavior\s*:[^;]*;?"
    r"|javascript\s*:"
    r"|vbscript\s*:",
    re.IGNORECASE,
)
_CSS_POSITION = re.compile(r"position\s*:\s*(?:fixed|sticky|absolute)", re.IGNORECASE)
_HAS_DECLARATION = re.compile(r"[A-Za-z-]+\s*:")

# 历史数据（无 content_format 标记）的兼容识别：预测汇总通知专属标记
_RICH_MARKER = re.compile(
    r"<(?:div|table|section|span)\b[^>]*\bprediction-summary-notice\b",
    re.IGNORECASE,
)

_MAX_OUTPUT = 200_000

# 通知中心一页会渲染 20 条富文本，同一条预测通知在一批里常重复出现；
# 只对较大的片段做记忆化，小片段本身解析成本很低，无需占用缓存。
_CACHE_THRESHOLD = 2048


@lru_cache(maxsize=128)
def _sanitize_cached(text: str, max_length: int) -> str:
    return _sanitize_uncached(text[:max_length])


def looks_like_rich_notification(value) -> bool:
    """判断内容是否是系统生成的「预测汇总」富文本通知。

    仅用于历史记录兼容（早期版本没有 content_format 列）。命中不代表可信，
    后续仍必须经过 :func:`sanitize_html`。
    """
    return bool(_RICH_MARKER.search(str(value or "")[:_MAX_OUTPUT]))


def _sanitize_css(css: str) -> str:
    """净化 ``<style>`` 块：无法按声明切分，只做危险构造剔除。"""
    text = _CSS_DANGEROUS.sub("", str(css or ""))
    text = _CSS_POSITION.sub("position: static", text)
    return text


def _sanitize_style_attr(css: str) -> str:
    """净化 ``style`` 属性：逐条声明过滤，丢弃被切碎的残缺声明。"""
    kept = []
    for declaration in str(css or "").split(";"):
        if not _HAS_DECLARATION.search(declaration):
            continue
        cleaned = _sanitize_css(declaration).strip()
        if not cleaned or cleaned.count("(") != cleaned.count(")"):
            continue
        kept.append(cleaned)
    return "; ".join(kept)


def _clean_attrs(tag: str, attrs):
    allowed = _GLOBAL_ATTRS | _TAG_ATTRS.get(tag, set())
    cleaned = []
    for name, value in attrs:
        attr = str(name or "").strip().lower()
        if not attr or attr.startswith("on"):
            continue
        if attr not in allowed:
            continue

        raw = unescape(str(value or ""))

        if attr == "style":
            css = _sanitize_style_attr(raw)
            if css:
                cleaned.append(("style", escape(css, quote=True)))
            continue

        if attr in _URL_ATTRS:
            compact = re.sub(r"[\x00-\x20]", "", raw).lower()
            if not compact.startswith(_ALLOWED_SCHEMES) and not compact.startswith(_ALLOWED_RELATIVE):
                continue

        if attr == "target" and raw.strip().lower() not in {"_blank", "_self"}:
            continue

        cleaned.append((attr, escape(raw, quote=True)))

    if tag == "a" and any(name == "target" for name, _ in cleaned):
        if not any(name == "rel" for name, _ in cleaned):
            cleaned.append(("rel", "noopener noreferrer"))
    return cleaned


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.dropped: list[str] = []
        self.in_style = False

    # -- 输出 ---------------------------------------------------------
    def _emit(self, text: str) -> None:
        self.out.append(text)

    def _render_attrs(self, tag: str, attrs) -> str:
        return "".join(
            f' {name}="{value}"' for name, value in _clean_attrs(tag, attrs)
        )

    def _open(self, tag: str, attrs) -> None:
        self._emit(f"<{tag}{self._render_attrs(tag, attrs)}>")
        if tag in _VOID_TAGS:
            return
        self.stack.append(tag)
        if tag == "style":
            self.in_style = True

    # -- HTMLParser 回调 ---------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.dropped:
            if tag in _DROP_WITH_CONTENT:
                self.dropped.append(tag)
            return
        if tag in _DROP_WITH_CONTENT:
            if tag not in _VOID_TAGS:
                self.dropped.append(tag)
            return
        if tag not in _ALLOWED_TAGS:
            return
        self._open(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.dropped or tag in _DROP_WITH_CONTENT or tag not in _ALLOWED_TAGS:
            return
        self._emit(f"<{tag}{self._render_attrs(tag, attrs)} />")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
            return
        if tag not in self.stack:
            return
        # 补齐中间未闭合的标签，保证 DOM 结构与页面布局不被挤坏
        while self.stack:
            open_tag = self.stack.pop()
            if open_tag == "style":
                self.in_style = False
            self._emit(f"</{open_tag}>")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self.dropped or not data:
            return
        if self.in_style:
            self._emit(_sanitize_css(data))
            return
        self._emit(escape(data, quote=False))

    def handle_entityref(self, name):
        if self.dropped:
            return
        if self.in_style:
            self._emit(f"&{name};")
            return
        # 原样保留实体引用：&lt;script&gt; 会被浏览器当作文本显示，不会执行
        self._emit(f"&{name};")

    def handle_charref(self, name):
        if self.dropped:
            return
        self._emit(f"&#{name};")

    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return


def sanitize_html(value, max_length: int = _MAX_OUTPUT) -> str:
    """返回可安全交给 ``|safe`` 渲染的 HTML 片段。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) > max_length:
        text = text[:max_length]
    if len(text) > _CACHE_THRESHOLD:
        return _sanitize_cached(text, max_length)
    return _sanitize_uncached(text)


def _sanitize_uncached(text: str) -> str:
    parser = _Sanitizer()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # 解析器异常时退化成纯文本，宁可丢样式也不能漏脚本
        return escape(text, quote=False)

    while parser.stack:
        tag = parser.stack.pop()
        parser._emit(f"</{tag}>")

    return "".join(parser.out)
