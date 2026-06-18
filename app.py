from flask import Flask, jsonify, render_template, request, session, redirect, url_for, flash
from flask import Response, stream_with_context
from flask_login import LoginManager, current_user
import json
import hashlib
import math
import os
import copy
import sys
import requests
import secrets
import threading
import hmac
from contextlib import contextmanager
from collections import Counter
import re
from urllib.parse import quote_plus, urlparse
from datetime import datetime, timedelta
import time
from markupsafe import escape
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import make_url
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from werkzeug.middleware.proxy_fix import ProxyFix

# 导入用户系统模块
from models import db, User, PredictionRecord, SystemConfig, InviteCode, LotteryDraw, ManualBetRecord, BacktestRun
from auth import auth_bp
from admin import admin_bp
from user import user_bp
from activation_code_routes import activation_code_bp
from invite_routes import invite_bp
from api_mobile import mobile_api_bp
from notification_service import notify_user

# --- 配置信息 ---
data_dir = os.path.join(os.getcwd(), 'data')
os.makedirs(data_dir, exist_ok=True)


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


_ML_PREDICTION_CACHE_TTL_SECONDS = 900
_ML_PREDICTION_CACHE_MAX_ITEMS = 24
_AI_PREDICTION_CACHE_TTL_SECONDS = 300
_AI_HTTP_CONNECT_TIMEOUT_SECONDS = _env_float("AI_HTTP_CONNECT_TIMEOUT_SECONDS", 10)
_AI_HTTP_READ_TIMEOUT_SECONDS = _env_float("AI_HTTP_READ_TIMEOUT_SECONDS", 90)
_RUNTIME_ANALYSIS_CACHE_MAX_ITEMS = 256
_ml_prediction_cache = {}
_ml_prediction_cache_lock = threading.Lock()
_ml_prediction_build_events = {}
_ai_prediction_cache = {}
_ai_prediction_cache_lock = threading.Lock()
_runtime_analysis_cache_local = threading.local()
_strategy_config_override_local = threading.local()
_backtest_cutoff_period_local = threading.local()
_backtest_strict_strategy_local = threading.local()
_SYSTEM_LOG_FILE_PATH = os.path.join(data_dir, "system.log")
_SYSTEM_LOG_RETENTION_DAYS = 30
_SYSTEM_LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_SYSTEM_LOG_PREFIX_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s?(.*)$")
_SYSTEM_LOG_INLINE_TIME_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
_system_log_stream_lock = threading.Lock()
_system_log_tee_installed = False


def _ai_http_timeout():
    return (_AI_HTTP_CONNECT_TIMEOUT_SECONDS, _AI_HTTP_READ_TIMEOUT_SECONDS)


def _current_system_log_timestamp():
    return datetime.now().strftime(_SYSTEM_LOG_TIMESTAMP_FORMAT)


def _split_system_log_timestamp(line):
    text = "" if line is None else str(line)
    prefix_match = _SYSTEM_LOG_PREFIX_RE.match(text)
    if prefix_match:
        return prefix_match.group(1), prefix_match.group(2)

    inline_match = _SYSTEM_LOG_INLINE_TIME_RE.search(text)
    if inline_match:
        return inline_match.group(1), text

    return "", text


def _system_log_archive_path(log_date):
    return f"{_SYSTEM_LOG_FILE_PATH}.{log_date.strftime('%Y-%m-%d')}"


def _cleanup_old_system_log_archives():
    cutoff_date = datetime.now().date() - timedelta(days=_SYSTEM_LOG_RETENTION_DAYS)
    log_dir = os.path.dirname(_SYSTEM_LOG_FILE_PATH)
    base_name = os.path.basename(_SYSTEM_LOG_FILE_PATH)
    prefix = f"{base_name}."

    try:
        for name in os.listdir(log_dir):
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            try:
                archive_date = datetime.strptime(suffix, "%Y-%m-%d").date()
            except ValueError:
                continue
            if archive_date < cutoff_date:
                try:
                    os.remove(os.path.join(log_dir, name))
                except OSError:
                    pass
    except OSError:
        pass


class _SystemLogFileManager:
    def __init__(self, file_path):
        self._file_path = file_path
        self._handle = None
        self._current_date = None

    def _rotate_if_needed(self):
        today = datetime.now().date()
        if self._handle is None:
            self._handle = open(self._file_path, "a", encoding="utf-8", buffering=1, errors="replace")
            self._current_date = today
            _cleanup_old_system_log_archives()
            return

        if self._current_date == today:
            return

        try:
            self._handle.flush()
            self._handle.close()
        except Exception:
            pass

        if os.path.exists(self._file_path):
            archive_path = _system_log_archive_path(self._current_date or today)
            try:
                if os.path.exists(archive_path):
                    os.remove(archive_path)
            except OSError:
                pass
            try:
                os.replace(self._file_path, archive_path)
            except OSError:
                pass

        self._handle = open(self._file_path, "a", encoding="utf-8", buffering=1, errors="replace")
        self._current_date = today
        _cleanup_old_system_log_archives()

    def write(self, text):
        self._rotate_if_needed()
        self._handle.write(text)

    def flush(self):
        if self._handle is None:
            self._rotate_if_needed()
        self._handle.flush()

    def truncate(self):
        if self._handle is not None:
            try:
                self._handle.flush()
                self._handle.close()
            except Exception:
                pass
        self._handle = open(self._file_path, "w", encoding="utf-8", buffering=1, errors="replace")
        self._current_date = datetime.now().date()
        _cleanup_old_system_log_archives()


class _TeeStream:
    def __init__(self, original, file_manager):
        self._original = original
        self._file_manager = file_manager
        self._line_start = True

    def _with_timestamps(self, text):
        output = []
        for chunk in text.splitlines(keepends=True):
            if self._line_start:
                output.append(f"[{_current_system_log_timestamp()}] ")
            output.append(chunk)
            self._line_start = chunk.endswith(("\n", "\r"))
        return "".join(output)

    def write(self, data):
        text = "" if data is None else str(data)
        if not text:
            return 0
        with _system_log_stream_lock:
            try:
                self._original.write(text)
            except Exception:
                pass
            try:
                self._file_manager.write(self._with_timestamps(text))
            except Exception:
                pass
            try:
                self._file_manager.flush()
            except Exception:
                pass
        return len(text)

    def flush(self):
        with _system_log_stream_lock:
            try:
                self._original.flush()
            except Exception:
                pass
            try:
                self._file_manager.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self._original.isatty())
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._original, name)


def _install_system_log_tee():
    global _system_log_tee_installed
    if _system_log_tee_installed:
        return
    try:
        os.makedirs(os.path.dirname(_SYSTEM_LOG_FILE_PATH), exist_ok=True)
        log_manager = _SystemLogFileManager(_SYSTEM_LOG_FILE_PATH)
        sys.stdout = _TeeStream(sys.stdout, log_manager)
        sys.stderr = _TeeStream(sys.stderr, log_manager)
        _system_log_tee_installed = True
    except Exception:
        _system_log_tee_installed = False


def get_system_log_file_path():
    return _SYSTEM_LOG_FILE_PATH


def _list_system_log_files():
    files = []
    if os.path.exists(_SYSTEM_LOG_FILE_PATH):
        files.append((_SYSTEM_LOG_FILE_PATH, datetime.max.date()))

    log_dir = os.path.dirname(_SYSTEM_LOG_FILE_PATH)
    base_name = os.path.basename(_SYSTEM_LOG_FILE_PATH)
    prefix = f"{base_name}."
    try:
        for name in os.listdir(log_dir):
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            try:
                log_date = datetime.strptime(suffix, "%Y-%m-%d").date()
            except ValueError:
                continue
            files.append((os.path.join(log_dir, name), log_date))
    except OSError:
        pass

    files.sort(key=lambda item: item[1])
    return [path for path, _ in files]


def get_system_logs(limit=200):
    try:
        normalized_limit = max(1, min(int(limit or 200), 1000))
    except (TypeError, ValueError):
        normalized_limit = 200

    log_files = _list_system_log_files()
    if not log_files:
        return []

    try:
        tail_lines = []
        for log_file in log_files:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    tail_lines.append(line.rstrip("\r\n"))
                    if len(tail_lines) > normalized_limit:
                        tail_lines.pop(0)
        logs = []
        for line in reversed(tail_lines):
            log_time, log_line = _split_system_log_timestamp(line)
            logs.append({
                "time": log_time,
                "line": log_line,
            })
        return logs
    except Exception as e:
        return [{"time": _current_system_log_timestamp(), "line": f"读取系统日志失败: {e}"}]


def clear_system_logs():
    try:
        for log_file in _list_system_log_files():
            try:
                os.remove(log_file)
            except OSError:
                pass
        if hasattr(sys.stdout, "_file_manager"):
            sys.stdout._file_manager.truncate()
        elif hasattr(sys.stderr, "_file_manager"):
            sys.stderr._file_manager.truncate()
        else:
            with open(_SYSTEM_LOG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("")
    except Exception:
        pass


_install_system_log_tee()


def _runtime_cache_bucket(name):
    caches = getattr(_runtime_analysis_cache_local, "caches", None)
    if caches is None:
        caches = {}
        _runtime_analysis_cache_local.caches = caches
    return caches.setdefault(name, {})


def _runtime_cache_get(name, key):
    bucket = _runtime_cache_bucket(name)
    value = bucket.get(key)
    if value is None:
        return None
    return copy.deepcopy(value)


def _runtime_cache_set(name, key, value, max_items=_RUNTIME_ANALYSIS_CACHE_MAX_ITEMS):
    bucket = _runtime_cache_bucket(name)
    if len(bucket) >= max(1, int(max_items or _RUNTIME_ANALYSIS_CACHE_MAX_ITEMS)):
        try:
            bucket.pop(next(iter(bucket)))
        except StopIteration:
            pass
    bucket[key] = copy.deepcopy(value)
    return value


def _clear_runtime_analysis_caches():
    try:
        _runtime_analysis_cache_local.caches = {}
    except Exception:
        pass


def _runtime_draws_signature(data, limit=None):
    selected = list(data or [])
    if limit:
        selected = selected[:max(0, int(limit or 0))]
    return tuple(str(item.get("id") or "").strip() for item in selected)


def _runtime_json_signature(value):
    try:
        payload = json.dumps(value or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except TypeError:
        payload = str(value or "")
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _prediction_cache_meta(region, data):
    normalized_region = str(region or "").strip().lower()
    latest_period = ""
    for item in list(data or [])[:16]:
        latest_period = str((item or {}).get("id") or "").strip()
        if latest_period:
            break
    return normalized_region, latest_period


def _prediction_cache_ttl_seconds(default_ttl):
    try:
        if not _personalized_predictions_enabled():
            return None
    except Exception:
        pass
    return int(default_ttl or 0)


def _build_ai_prediction_cache_key(region, data, tuned, ai_config, prompt, temperature, sample_count, candidate_count):
    tuned_payload = {
        key: value
        for key, value in dict(tuned or {}).items()
        if key not in {"updated_at", "auto_optimize_history", "auto_rollback_history", "auto_restore_history"}
    }
    ai_payload = {
        "api_url": str((ai_config or {}).get("api_url") or ""),
        "model": str((ai_config or {}).get("model") or ""),
    }
    payload = {
        "cache_version": 1,
        "region": str(region or "").strip().lower(),
        "periods": _runtime_draws_signature(data, limit=18),
        "draw_count": len(data or []),
        "tuned": tuned_payload,
        "ai": ai_payload,
        "prompt_hash": hashlib.md5(str(prompt or "").encode("utf-8")).hexdigest(),
        "temperature": round(float(temperature or 0.0), 4),
        "sample_count": int(sample_count or 0),
        "candidate_count": int(candidate_count or 0),
    }
    fingerprint = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(fingerprint.encode("utf-8")).hexdigest()


def _prune_stale_ai_prediction_cache(region, latest_period):
    normalized_region = str(region or "").strip().lower()
    latest_period = str(latest_period or "").strip()
    if not normalized_region or not latest_period:
        return
    with _ai_prediction_cache_lock:
        stale_keys = [
            key
            for key, item in _ai_prediction_cache.items()
            if str(item.get("region") or "").strip().lower() == normalized_region
            and str(item.get("latest_period") or "").strip() != latest_period
        ]
        for key in stale_keys:
            _ai_prediction_cache.pop(key, None)


def _clear_ai_prediction_cache(region=None):
    normalized_region = str(region or "").strip().lower()
    with _ai_prediction_cache_lock:
        if not normalized_region:
            _ai_prediction_cache.clear()
            return
        for key in [
            key
            for key, item in _ai_prediction_cache.items()
            if str(item.get("region") or "").strip().lower() == normalized_region
        ]:
            _ai_prediction_cache.pop(key, None)


def _get_cached_ai_prediction(cache_key):
    now = time.time()
    ttl_seconds = _prediction_cache_ttl_seconds(_AI_PREDICTION_CACHE_TTL_SECONDS)
    with _ai_prediction_cache_lock:
        cached = _ai_prediction_cache.get(cache_key)
        if not cached:
            return None
        cached_at = float(cached.get("cached_at") or 0.0)
        if ttl_seconds is not None and now - cached_at > ttl_seconds:
            _ai_prediction_cache.pop(cache_key, None)
            return None
        cached["last_used_at"] = now
        result = copy.deepcopy(cached.get("result"))
    if isinstance(result, dict):
        meta = dict(result.get("model_meta") or {})
        meta["ai_cache_hit"] = True
        meta["ai_cache_age_seconds"] = round(now - cached_at, 2)
        result["model_meta"] = meta
    return result


def _store_cached_ai_prediction(cache_key, result, region=None, latest_period=None):
    if not isinstance(result, dict) or result.get("error"):
        return result
    now = time.time()
    ttl_seconds = _prediction_cache_ttl_seconds(_AI_PREDICTION_CACHE_TTL_SECONDS)
    cached_result = copy.deepcopy(result)
    meta = dict(cached_result.get("model_meta") or {})
    meta["ai_cache_hit"] = False
    meta["ai_cached_at"] = datetime.now().isoformat(timespec="seconds")
    cached_result["model_meta"] = meta
    with _ai_prediction_cache_lock:
        if ttl_seconds is not None:
            expired_keys = [
                key
                for key, item in _ai_prediction_cache.items()
                if now - float(item.get("cached_at") or 0.0) > ttl_seconds
            ]
            for key in expired_keys:
                _ai_prediction_cache.pop(key, None)
        if len(_ai_prediction_cache) >= 64:
            oldest_key = min(
                _ai_prediction_cache.keys(),
                key=lambda key: float(_ai_prediction_cache[key].get("last_used_at") or _ai_prediction_cache[key].get("cached_at") or 0.0),
            )
            _ai_prediction_cache.pop(oldest_key, None)
        _ai_prediction_cache[cache_key] = {
            "cached_at": now,
            "last_used_at": now,
            "region": str(region or "").strip().lower(),
            "latest_period": str(latest_period or "").strip(),
            "result": cached_result,
        }
    return copy.deepcopy(cached_result)

def _load_or_create_secret_key():
    env_secret = os.environ.get("SECRET_KEY")
    if env_secret:
        return env_secret

    import secrets
    secret_key_path = os.path.join(data_dir, "secret_key.txt")
    try:
        if os.path.exists(secret_key_path):
            with open(secret_key_path, "r", encoding="utf-8") as f:
                persisted = (f.read() or "").strip()
                if persisted:
                    return persisted
        persisted = secrets.token_hex(32)
        with open(secret_key_path, "w", encoding="utf-8") as f:
            f.write(persisted)
        return persisted
    except OSError:
        # 兜底：即使文件写入失败，也至少保证当前进程可运行
        return secrets.token_hex(32)

app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
_session_cookie_secure = os.environ.get("SESSION_COOKIE_SECURE")
app.config["SESSION_COOKIE_SECURE"] = (
    _session_cookie_secure.lower() in ("1", "true", "yes")
    if _session_cookie_secure is not None
    else os.environ.get("FLASK_ENV", "").lower() == "production"
)
_trust_proxy_headers = os.environ.get("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes")
if _trust_proxy_headers:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

_SECURITY_RATE_LIMITS = {}
_SECURITY_RATE_LIMITS_LOCK = threading.Lock()


def _client_rate_key(scope):
    user_id = session.get("user_id")
    remote_addr = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    remote_addr = remote_addr.split(",", 1)[0].strip()
    return f"{scope}:user:{user_id}" if user_id else f"{scope}:ip:{remote_addr}"


def _rate_limited(scope, limit, window_seconds):
    now = time.time()
    key = _client_rate_key(scope)
    with _SECURITY_RATE_LIMITS_LOCK:
        bucket = [
            timestamp
            for timestamp in _SECURITY_RATE_LIMITS.get(key, [])
            if now - timestamp < window_seconds
        ]
        if len(bucket) >= limit:
            _SECURITY_RATE_LIMITS[key] = bucket
            return True
        bucket.append(now)
        _SECURITY_RATE_LIMITS[key] = bucket
    return False


def _same_origin_request():
    expected_host = request.host
    for header_name in ("Origin", "Referer"):
        raw_value = request.headers.get(header_name)
        if not raw_value:
            continue
        parsed = urlparse(raw_value)
        if parsed.netloc and parsed.netloc != expected_host:
            return False
    return True


def _get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _request_csrf_token():
    return (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or request.form.get("_csrf_token")
        or ""
    )


def _csrf_exempt_endpoint():
    endpoint = request.endpoint or ""
    path = request.path or ""
    return (
        endpoint.startswith("static")
        or endpoint.startswith("mobile_api.")
        or path.startswith("/api/mobile/")
    )


def _csrf_token_valid():
    expected = session.get("_csrf_token")
    supplied = _request_csrf_token()
    return bool(expected and supplied and hmac.compare_digest(str(expected), str(supplied)))


def _wants_json_response():
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )


def _json_auth_error(message, status=401, code="auth_required"):
    return jsonify({"success": False, "error": code, "message": message}), status


def _require_active_session_json():
    user_id = session.get("user_id")
    if not user_id:
        return None, _json_auth_error("请先登录", 401, "auth_required")
    user = User.query.get(user_id)
    if not user:
        session.clear()
        return None, _json_auth_error("登录状态已失效，请重新登录", 401, "auth_required")
    user.check_and_update_activation_status()
    session["is_active"] = bool(user.is_active)
    if not user.is_active:
        return None, _json_auth_error("账号未激活或已过期", 403, "activation_required")
    return user, None


def _require_admin_session_json():
    user_id = session.get("user_id")
    if not user_id:
        return None, _json_auth_error("请先登录", 401, "auth_required")
    user = User.query.get(user_id)
    if not user or not user.is_admin:
        return None, _json_auth_error("需要管理员权限", 403, "admin_required")
    return user, None


@app.before_request
def security_request_guards():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _csrf_exempt_endpoint():
        if _csrf_token_valid():
            pass
        elif not _same_origin_request():
            if request.path.startswith("/api/") or _wants_json_response():
                return _json_auth_error("跨站请求已被拦截", 403, "csrf_blocked")
            return ("跨站请求已被拦截", 403)
        else:
            if request.path.startswith("/api/") or _wants_json_response():
                return _json_auth_error("CSRF token 无效或缺失", 403, "csrf_blocked")
            return ("CSRF token 无效或缺失", 403)

    if request.method == "POST" and request.endpoint in {"auth.login", "auth.register"}:
        if _rate_limited(request.endpoint, 8, 300):
            return ("请求过于频繁，请稍后再试", 429)

    if request.method == "POST" and request.endpoint in {"auth.forgot_password", "auth.reset_password"}:
        if _rate_limited(request.endpoint, 5, 900):
            return ("请求过于频繁，请稍后再试", 429)

    if request.endpoint == "unified_predict_api":
        if _rate_limited("api.predict", 30, 300):
            return _json_auth_error("预测请求过于频繁，请稍后再试", 429, "rate_limited")

    if request.endpoint == "handle_chat" and request.method == "POST":
        if _rate_limited("api.chat", 20, 300):
            return _json_auth_error("聊天请求过于频繁，请稍后再试", 429, "rate_limited")


_CSRF_AUTO_SCRIPT = """
<meta name="csrf-token" content="{token}">
<script>
(function() {{
  var token = "{token}";
  function isUnsafe(method) {{
    return !["GET", "HEAD", "OPTIONS", "TRACE"].includes(String(method || "GET").toUpperCase());
  }}
  document.addEventListener("submit", function(event) {{
    var form = event.target;
    if (!form || !form.method || !isUnsafe(form.method)) return;
    if (!form.querySelector('input[name="csrf_token"]')) {{
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = token;
      form.appendChild(input);
    }}
  }}, true);
  if (window.fetch && !window.fetch.__csrfPatched) {{
    var originalFetch = window.fetch;
    var patchedFetch = function(input, init) {{
      init = init || {{}};
      var method = init.method || (input && input.method) || "GET";
      if (isUnsafe(method)) {{
        var headers = new Headers(init.headers || (input && input.headers) || {{}});
        if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
        init.headers = headers;
      }}
      return originalFetch(input, init);
    }};
    patchedFetch.__csrfPatched = true;
    window.fetch = patchedFetch;
  }}
}})();
</script>
"""


@app.after_request
def inject_csrf_helpers(response):
    try:
        if request.method != "GET":
            return response
        if not response.mimetype or "text/html" not in response.mimetype:
            return response
        body = response.get_data(as_text=True)
        if "</head>" not in body or 'name="csrf-token"' in body:
            return response
        token = _get_csrf_token()
        body = body.replace("</head>", _CSRF_AUTO_SCRIPT.format(token=token) + "\n</head>", 1)
        response.set_data(body)
        response.headers["Content-Length"] = str(len(response.get_data()))
    except Exception as e:
        print(f"注入 CSRF 脚本失败: {e}")
    return response


def _safe_system_config(key, default=""):
    try:
        return SystemConfig.get_config(key, default)
    except Exception:
        return default


@app.context_processor
def inject_system_settings():
    default_seo_title = "彩研所 - 香港澳门彩票数据分析与智能预测"
    default_seo_description = (
        "彩研所提供香港、澳门彩票开奖记录、生肖号码、波色单双、历史走势和智能预测分析，"
        "帮助用户快速查看开奖数据并辅助选号研究，仅供数据分析参考。"
    )
    site_name = _safe_system_config("site_name", "AI数据分析预测系统")
    site_description = _safe_system_config("site_description", "")
    system_name = _safe_system_config("system_name", site_name or "AI数据分析预测系统")
    system_description = _safe_system_config("system_description", site_description or "")
    seo_title = _safe_system_config("seo_title", "") or default_seo_title
    seo_description = (
        _safe_system_config("seo_description", "")
        or site_description
        or system_description
        or default_seo_description
    )
    return {
        "site_name": site_name,
        "site_description": site_description,
        "system_name": system_name,
        "system_description": system_description,
        "seo_title": seo_title,
        "seo_description": seo_description,
    }

_startup_log_lock_path = None
_startup_log_lock_acquired = False

def _pid_is_running(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

def _try_acquire_startup_log_lock():
    import tempfile
    global _startup_log_lock_path, _startup_log_lock_acquired
    if _startup_log_lock_acquired:
        return True
    lock_path = os.path.join(tempfile.gettempdir(), "mark-six-startup.log.lock")
    pid = os.getpid()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(pid))
        _startup_log_lock_path = lock_path
        _startup_log_lock_acquired = True
        return True
    except FileExistsError:
        try:
            with open(lock_path, "r") as f:
                existing_pid = int((f.read() or "").strip() or "0")
        except Exception:
            existing_pid = 0
        if existing_pid and _pid_is_running(existing_pid):
            return False
        try:
            os.remove(lock_path)
        except OSError:
            return False
        return _try_acquire_startup_log_lock()

def _release_startup_log_lock():
    global _startup_log_lock_path, _startup_log_lock_acquired
    if not _startup_log_lock_acquired or not _startup_log_lock_path:
        return
    try:
        os.remove(_startup_log_lock_path)
    except OSError:
        pass
    _startup_log_lock_path = None
    _startup_log_lock_acquired = False

def _should_log_startup():
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        return False
    if not _try_acquire_startup_log_lock():
        return False
    import atexit
    atexit.register(_release_startup_log_lock)
    return True


@contextmanager
def _startup_schema_lock(timeout_seconds=120):
    import tempfile
    lock_path = os.path.join(tempfile.gettempdir(), "mark-six-schema.lock")
    pid = os.getpid()
    deadline = time.time() + timeout_seconds
    acquired = False

    while time.time() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(pid))
            acquired = True
            break
        except FileExistsError:
            existing_pid = 0
            try:
                with open(lock_path, "r") as f:
                    existing_pid = int((f.read() or "").strip() or "0")
            except Exception:
                existing_pid = 0
            if existing_pid and not _pid_is_running(existing_pid):
                try:
                    os.remove(lock_path)
                    continue
                except OSError:
                    pass
            time.sleep(0.25)

    if not acquired:
        print("Startup schema lock timeout; continuing without exclusive schema lock.")

    try:
        yield
    finally:
        if acquired:
            try:
                os.remove(lock_path)
            except OSError:
                pass

MYSQL_CHARSET = "utf8mb4"
MYSQL_COLLATION = os.environ.get("MYSQL_COLLATION", "utf8mb4_unicode_ci")


def _build_database_uri(db_path):
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return db_url

    db_type = os.environ.get("DB_TYPE", "sqlite").lower()
    if db_type in ("mysql", "mariadb"):
        host = os.environ.get("DB_HOST", "localhost")
        port = os.environ.get("DB_PORT", "3306")
        name = os.environ.get("DB_NAME", "mark_six")
        user = quote_plus(os.environ.get("DB_USER", "root"))
        password = quote_plus(os.environ.get("DB_PASSWORD", ""))
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset={MYSQL_CHARSET}"

    return f"sqlite:///{db_path}"


def _build_engine_options(database_uri):
    options = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }
    try:
        backend = (make_url(database_uri).get_backend_name() or "").lower()
    except Exception:
        backend = ""
    if backend in ("mysql", "mariadb"):
        options["connect_args"] = {
            "init_command": f"SET NAMES {MYSQL_CHARSET} COLLATE {MYSQL_COLLATION}",
        }
    return options


def _install_mysql_connection_collation_hook():
    try:
        backend = (make_url(app.config['SQLALCHEMY_DATABASE_URI']).get_backend_name() or "").lower()
    except Exception:
        backend = ""
    if backend not in ("mysql", "mariadb"):
        return

    @event.listens_for(db.engine, "connect")
    def _set_mysql_connection_collation(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET NAMES {MYSQL_CHARSET} COLLATE {MYSQL_COLLATION}")
            cursor.execute("SET collation_connection = %s", (MYSQL_COLLATION,))
        finally:
            cursor.close()

def _mask_db_uri(uri):
    return re.sub(r'//([^:/@]+):([^@]+)@', r'//\1:***@', uri)


def _ensure_mysql_database_exists(database_uri):
    try:
        url = make_url(database_uri)
    except Exception:
        return

    backend = (url.get_backend_name() or "").lower()
    if backend not in ("mysql", "mariadb"):
        return

    db_name = str(url.database or "").strip()
    if not db_name:
        return

    server_url = url.set(database=None)
    admin_engine = None
    try:
        admin_engine = create_engine(
            server_url,
            pool_pre_ping=True,
            pool_recycle=280,
            connect_args={
                "init_command": f"SET NAMES {MYSQL_CHARSET} COLLATE {MYSQL_COLLATION}",
            },
        )
        escaped_name = db_name.replace("`", "``")
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE IF NOT EXISTS `{escaped_name}` "
                f"CHARACTER SET {MYSQL_CHARSET} COLLATE {MYSQL_COLLATION}"
            )
        if _should_log_startup():
            print(f"MySQL database ready: {db_name}")
    except Exception as e:
        print(f"Failed to ensure MySQL database exists: {e}")
    finally:
        if admin_engine is not None:
            admin_engine.dispose()


def _describe_database_target(database_uri):
    try:
        url = make_url(database_uri)
    except Exception:
        return "unknown", ""

    backend = (url.get_backend_name() or "").lower()
    database_name = str(url.database or "").strip()

    if backend in ("mysql", "mariadb"):
        return "MySQL", database_name
    if backend == "sqlite":
        return "SQLite", database_name
    return backend or "unknown", database_name

STRATEGY_LABELS = {
    'ml': '机器学习预测',
    'markov': '马尔科夫预测',
    'balanced': '均衡预测',
    'ai': 'AI智能预测',
    'hot': '热门预测',
    'cold': '冷门预测',
    'trend': '走势预测',
    'hybrid': '综合预测'
}

STRATEGY_ICON_MAP = {
    'hot': 'fire',
    'cold': 'snowflake',
    'trend': 'chart-line',
    'hybrid': 'sliders-h',
    'balanced': 'balance-scale',
    'markov': 'project-diagram',
    'ml': 'flask',
    'ai': 'robot',
}

def _get_strategy_label(strategy):
    return STRATEGY_LABELS.get(strategy, strategy or '未知策略')


def _build_prediction_display_copy(requested_strategy=None, resolved_strategy=None):
    strategy_key = str(resolved_strategy or requested_strategy or "").strip()
    strategy_title = _get_strategy_label(strategy_key)
    if strategy_key == "ml":
        analysis_title = "机器学习分析"
    elif strategy_key == "ai":
        analysis_title = "AI分析"
    else:
        analysis_title = "分析说明"
    return {
        "strategy_title": strategy_title,
        "strategy_icon": STRATEGY_ICON_MAP.get(strategy_key, "dice"),
        "analysis_title": analysis_title,
        "normal_numbers_title": "平码参考",
        "special_number_title": "特码",
        "special_focus_title": "本期主推特码",
        "special_focus_hint": "重点参考号码",
    }


def _attach_prediction_display_copy(result, requested_strategy=None, resolved_strategy=None):
    payload = dict(result or {})
    existing = payload.get("display_copy") or {}
    payload["display_copy"] = {
        **_build_prediction_display_copy(requested_strategy, resolved_strategy),
        **dict(existing or {}),
    }
    return payload

def _build_strategy_note(requested_strategy, resolved_strategy):
    return _get_strategy_label(resolved_strategy)

def _build_special_focus_text(special, normal=None, strategy_name=None, accuracy=None, samples=None, confidence=None, extra_reason=None):
    lines = [f"本期主推特码：{special}"]
    if normal:
        lines.append(f"参考平码：{', '.join(map(str, normal))}")
    if strategy_name:
        lines.append(f"预测策略：{strategy_name}")
    if accuracy is not None:
        lines.append(f"历史参考值：{accuracy}%")
    if samples is not None:
        lines.append(f"学习样本：{samples}期")
    if confidence is not None:
        lines.append(f"本期参考分：{confidence}%")
    if extra_reason:
        lines.append(f"简要说明：{extra_reason}")
    return "\n".join(lines)

def _has_meaningful_ai_reasoning(text):
    content = str(text or "").strip()
    if not content:
        return False
    if len(content) >= 120:
        return True
    return any(token in content for token in ("理由", "分析", "排除", "风险", "信心", "波色", "生肖", "单双"))


def _build_ai_number_reason(number, zodiac_map):
    value = str(number).strip()
    try:
        num = int(value)
    except (TypeError, ValueError):
        return None

    color = _get_color_zh(num) or "待定"
    zodiac = zodiac_map.get(str(num), "") or "待定"
    parity = "双" if num % 2 == 0 else "单"
    zone = "小号区" if num <= 16 else "中段区" if num <= 33 else "大号区"
    tail = num % 10
    return (
        f"号码：`{num:02d}`\n"
        f"理由：{color}波、生肖{zodiac}、{parity}数，落在{zone}，尾数为`{tail}`，用于补齐组合的区间与属性分布。\n"
        f"风险：如果本期继续集中在相邻区间或同属性号码，这个点位会先被挤掉。"
    )


def _build_ai_reason_fallback(special_number, normal_numbers, region=None):
    year = datetime.now().year
    try:
        number_to_zodiac = _get_number_to_zodiac_map(year)
    except Exception:
        number_to_zodiac = {}

    normal_sections = []
    for number in normal_numbers or []:
        section = _build_ai_number_reason(number, number_to_zodiac)
        if section:
            normal_sections.append(section)

    try:
        special_num = int(str(special_number).strip())
    except (TypeError, ValueError):
        special_num = None

    special_color = _get_color_zh(special_num) if special_num is not None else ""
    special_zodiac = number_to_zodiac.get(str(special_num), "") if special_num is not None else ""
    special_parity = ""
    if special_num is not None:
        special_parity = "双" if special_num % 2 == 0 else "单"

    normal_text = "\n\n".join(normal_sections) if normal_sections else "暂无可用的平码说明。"
    region_label = "香港" if str(region or "").lower() == "hk" else "澳门" if str(region or "").lower() == "macau" else "当前"

    return (
        f"**平码预测**\n\n{normal_text}\n\n"
        f"**特别号**\n\n"
        f"号码：`{special_number}`\n"
        f"理由：作为本期主推特码，优先参考{region_label}最近样本里的结构平衡；当前属性为{special_color or '待定'}波、生肖{special_zodiac or '待定'}、{special_parity or '待定'}数，用来和平码候选拉开主次。\n"
        f"风险：特码本身波动更大，即使属性匹配，也可能被临场冷号打断。\n\n"
        f"**排除逻辑**\n\n"
        f"1. 不优先追与主推号完全同属性、且最近已经连续出现的过热号码。\n"
        f"2. 不把六码全部压在同一区间，尽量避免小号、中段、大号失衡。\n"
        f"3. 如果 AI 原始回复只给了号码，这一段是系统自动补的结构化理由。"
    )


def _compose_ai_recommendation_text(ai_response, special_number, normal_numbers, region=None):
    raw_text = str(ai_response or "").strip()
    fallback_text = _build_ai_reason_fallback(special_number, normal_numbers, region=region)
    if not _has_meaningful_ai_reasoning(raw_text):
        return fallback_text

    has_structured_sections = all(token in raw_text for token in ("号码", "理由")) and "排除" in raw_text
    if has_structured_sections:
        return raw_text
    return f"{raw_text}\n\n{fallback_text}"


def _decorate_recommendation_text(requested_strategy, resolved_strategy, recommendation_text):
    return recommendation_text or ''


def _build_ai_normal_summary(normal_numbers, zodiac_map):
    numbers = []
    for value in normal_numbers or []:
        try:
            numbers.append(int(str(value).strip()))
        except (TypeError, ValueError):
            continue

    if not numbers:
        return "参考平码：暂无可用号码。"

    labels = []
    for num in numbers:
        zodiac = zodiac_map.get(str(num), "") or ""
        color = _get_color_zh(num) or ""
        parts = [f"{num:02d}"]
        if zodiac:
            parts.append(zodiac)
        if color:
            parts.append(color)
        labels.append("/".join(parts))

    zone_names = []
    if any(num <= 16 for num in numbers):
        zone_names.append("小号区")
    if any(17 <= num <= 33 for num in numbers):
        zone_names.append("中段区")
    if any(num >= 34 for num in numbers):
        zone_names.append("大号区")
    zone_text = "、".join(zone_names) if zone_names else "多区间"

    color_counter = Counter(_get_color_zh(num) or "待定" for num in numbers)
    color_text = "、".join(
        f"{name}{count}枚"
        for name, count in color_counter.items()
        if name and name != "待定"
    ) or "属性均衡"

    return (
        f"参考平码：[{', '.join(f'{num:02d}' for num in numbers)}]\n"
        f"简述：{'、'.join(labels)}。整体以{zone_text}分散覆盖为主，当前波色分布为{color_text}，主要用于配合特码，不逐个展开。"
    )


def _build_ai_reason_fallback(special_number, normal_numbers, region=None):
    year = datetime.now().year
    try:
        number_to_zodiac = _get_number_to_zodiac_map(year)
    except Exception:
        number_to_zodiac = {}

    try:
        special_num = int(str(special_number).strip())
    except (TypeError, ValueError):
        special_num = None

    special_color = _get_color_zh(special_num) if special_num is not None else ""
    special_zodiac = number_to_zodiac.get(str(special_num), "") if special_num is not None else ""
    special_parity = ""
    if special_num is not None:
        special_parity = "双" if special_num % 2 == 0 else "单"

    normal_text = _build_ai_normal_summary(normal_numbers, number_to_zodiac)
    region_label = "香港" if str(region or "").lower() == "hk" else "澳门" if str(region or "").lower() == "macau" else "当前"

    return (
        f"**平码预测**\n\n{normal_text}\n\n"
        f"**特码重点**\n\n"
        f"号码：`{special_number}`\n"
        f"理由：作为本期主推特码，优先参考{region_label}最近样本里的结构平衡；当前属性为{special_color or '待定'}波、生肖{special_zodiac or '待定'}、{special_parity or '待定'}数，用来和平码候选拉开主次。\n"
        f"风险：特码本身波动更大，即使属性匹配，也可能被临场冷号打断。\n\n"
        f"**排除逻辑**\n\n"
        f"1. 不优先追与主推号完全同属性、且最近已经连续出现的过热号码。\n"
        f"2. 不把六码全部压在同一区间，尽量避免小号、中段、大号失衡。\n"
        f"3. 如果 AI 原始回复只给了号码，这一段是系统自动补的结构化说明。"
    )


def _compose_ai_recommendation_text(ai_response, special_number, normal_numbers, region=None):
    raw_text = str(ai_response or "").strip()
    fallback_text = _build_ai_reason_fallback(special_number, normal_numbers, region=region)
    if not _has_meaningful_ai_reasoning(raw_text):
        return fallback_text
    if "<!--AI_DYNAMIC_SELECTION-->" in raw_text:
        return raw_text.replace("<!--AI_DYNAMIC_SELECTION-->", "").strip()

    final_special = _normalize_draw_number(special_number)
    _, extracted_special = _extract_ai_numbers_v2(raw_text, region=region)
    cleaned_lines = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if any(token in stripped for token in ("本期主推特码", "参考平码", "特码重点")):
            continue
        conflicting_specials = re.findall(r"(?:特码|主推特码|本期主推特码)\D{0,4}(\d{1,2})", stripped)
        if conflicting_specials and any(_normalize_draw_number(item) != final_special for item in conflicting_specials):
            continue
        cleaned_lines.append(line)

    cleaned_text = "\n".join(cleaned_lines).strip()
    if extracted_special and _normalize_draw_number(extracted_special) != final_special:
        cleaned_text = ""
    if not _has_meaningful_ai_reasoning(cleaned_text):
        return fallback_text
    if len(cleaned_text) >= 180:
        return cleaned_text
    return f"{cleaned_text}\n\n{fallback_text}"


def _build_ai_dynamic_selection_text(best_candidate, context, ranked_candidates):
    special = best_candidate.get("special")
    normal = [int(number) for number in (best_candidate.get("normal") or []) if str(number).isdigit()]
    special_votes = context.get("special_votes") or Counter()
    normal_votes = context.get("normal_votes") or Counter()
    phase_profile = dict(context.get("phase_profile") or {})
    gate_profile = dict(context.get("gate_profile") or {})
    diagnostics = dict(best_candidate.get("score_diagnostics") or {})
    recent_specials = [int(number) for number in (context.get("recent_specials") or []) if str(number).isdigit()]
    region = str((context.get("structured_payload") or {}).get("region") or "")
    region_label = "香港" if region == "hk" else "澳门" if region == "macau" else "当前地区"

    try:
        special_num = int(str(special).strip())
    except (TypeError, ValueError):
        special_num = 0

    try:
        year = datetime.now().year
        zodiac_map = _get_number_to_zodiac_map(year)
    except Exception:
        zodiac_map = {}

    special_color = _get_color_zh(special_num) or "待定"
    special_zodiac = zodiac_map.get(str(special_num), "") or "待定"
    special_parity = "双" if special_num and special_num % 2 == 0 else "单" if special_num else "待定"
    special_zone = "小号区" if special_num <= 16 else "中段区" if special_num <= 33 else "大号区" if special_num else "待定区"
    normal_zone_count = len(set(1 if number <= 16 else 2 if number <= 33 else 3 for number in normal))
    normal_tail_count = len(set(number % 10 for number in normal))
    normal_vote_avg = round(
        sum(_safe_float(normal_votes.get(int(number), 0.0)) for number in normal) / max(len(normal), 1),
        2,
    )
    special_vote = round(_safe_float(special_votes.get(int(special_num), 0.0)), 2) if special_num else 0.0
    recent_text = "、".join(f"{number:02d}" for number in recent_specials[:5]) or "暂无"
    alternatives = "、".join(
        f"{item.get('special')}({round(_safe_float(item.get('aggregate_score'), 0.0), 3)})"
        for item in (ranked_candidates or [])[:4]
        if item.get("special") is not None
    ) or "暂无"

    repeat_note = (
        "主推号刚在近期出现过，已在评分中扣除重复风险后仍保留。"
        if special_num in recent_specials[:5]
        else "主推号避开了最近几期的重复特码，短期拥挤度相对低。"
    )
    gate_status = str(gate_profile.get("status") or "active")
    gate_note = {
        "guarded": "当前网关偏谨慎，系统会压低高波动候选。",
        "fallback": "当前样本信号偏弱，系统采用保守兜底筛选。",
        "active": "当前网关允许 AI 主动选择，但仍会保留结构校验。",
    }.get(gate_status, f"当前网关状态：{gate_status}。")
    phase_label = str(phase_profile.get("label") or "neutral")
    phase_guidance = str(phase_profile.get("guidance") or "保持中性判断。")
    quality = round(_safe_float(best_candidate.get("aggregate_score"), 0.0), 4)
    rerank = round(_safe_float(best_candidate.get("rerank_score"), 0.0), 4)
    structure_bonus = round(_safe_float(diagnostics.get("structure_bonus"), 0.0), 4)
    repeat_penalty = round(_safe_float(diagnostics.get("repeat_penalty"), 0.0), 4)
    shape_score = round(_safe_float(diagnostics.get("shape_score"), 0.0), 4)

    raw_reason = str(best_candidate.get("why") or "").strip()
    raw_reason_line = f"\n模型原始理由：{raw_reason}" if raw_reason else ""

    return (
        "<!--AI_DYNAMIC_SELECTION-->\n"
        f"**本期 AI 判断**\n\n"
        f"{region_label}当前阶段：{phase_label}，{phase_guidance} {gate_note}\n"
        f"最近特码序列：{recent_text}。\n\n"
        f"**特码重点**\n\n"
        f"号码：`{special}`\n"
        f"理由：{special_color}波、生肖{special_zodiac}、{special_parity}数，落在{special_zone}；"
        f"系统重排分{rerank}，综合参考分{quality}，特码票数{special_vote}。{repeat_note}{raw_reason_line}\n\n"
        f"**平码结构**\n\n"
        f"参考平码：[{', '.join(f'{number:02d}' for number in normal)}]\n"
        f"说明：六码覆盖{normal_zone_count}个区间、{normal_tail_count}组尾数，平均票数{normal_vote_avg}；"
        f"结构加成{structure_bonus}，形态评分{shape_score}，重复扣分{repeat_penalty}。\n\n"
        f"**候选对比**\n\n"
        f"候选排序：{alternatives}。本期优先选择分数、结构和短期重复风险更均衡的一组。\n\n"
        f"**风险提示**\n\n"
        f"AI 结果已通过本地候选池重排，仍然只适合作为参考；如果临场继续集中在最近热区，特码可能被相邻区间或同属性号码打断。"
    )


def _normalize_special_candidate_numbers(candidates):
    normalized = []
    seen = set()
    for value in candidates or []:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if not (1 <= number <= 49) or number in seen:
            continue
        seen.add(number)
        normalized.append(number)
    return normalized


def _load_used_special_numbers(user_id, region, period, exclude_strategy=None):
    if not user_id or not region or not period:
        return set()

    query = PredictionRecord.query.filter_by(
        user_id=user_id,
        region=region,
        period=period,
    )
    if exclude_strategy:
        query = query.filter(PredictionRecord.strategy != exclude_strategy)

    used_numbers = set()
    for row in query.with_entities(PredictionRecord.special_number).all():
        raw = str(row[0] or "").strip()
        if raw.isdigit():
            used_numbers.add(int(raw))
    return used_numbers


def _refresh_special_recommendation_text(strategy, recommendation_text, special_number, normal_numbers, region=None):
    if strategy == "ai":
        return _compose_ai_recommendation_text(
            recommendation_text,
            str(special_number),
            normal_numbers,
            region=region,
        )

    return _build_special_focus_text(
        str(special_number),
        normal_numbers,
        strategy_name=_get_strategy_label(strategy),
    )


def _ensure_period_unique_special(
    result,
    strategy,
    region,
    period,
    user_id=None,
    prediction_zodiac_year=None,
    used_special_numbers=None,
):
    if not result or not user_id:
        return result

    special_info = result.get("special") or {}
    special_raw = str(special_info.get("number") or "").strip()
    if not special_raw.isdigit():
        return result

    current_special = int(special_raw)
    used_numbers = set(used_special_numbers or _load_used_special_numbers(
        user_id,
        region,
        period,
        exclude_strategy=strategy,
    ))
    if current_special not in used_numbers:
        return result

    normal_numbers = []
    for value in result.get("normal") or []:
        try:
            normal_numbers.append(int(str(value).strip()))
        except (TypeError, ValueError):
            continue

    model_meta = dict(result.get("model_meta") or {})
    candidate_numbers = _normalize_special_candidate_numbers(model_meta.get("special_candidates"))
    if current_special not in candidate_numbers:
        candidate_numbers.insert(0, current_special)

    replacement = None
    for candidate in candidate_numbers:
        if candidate == current_special:
            continue
        if candidate in used_numbers or candidate in normal_numbers:
            continue
        replacement = candidate
        break

    if replacement is None:
        return result

    zodiac_year = prediction_zodiac_year or datetime.now().year
    zodiac_map = _get_number_to_zodiac_map(zodiac_year)
    result["special"] = {
        "number": str(replacement),
        "sno_zodiac": zodiac_map.get(str(replacement), ""),
    }
    model_meta["special_candidates"] = candidate_numbers
    model_meta["special_unique_original"] = str(current_special)
    model_meta["special_unique_adjusted"] = True
    model_meta["special_unique_reason"] = "deduplicated_within_period"
    result["model_meta"] = model_meta
    result["recommendation_text"] = _refresh_special_recommendation_text(
        strategy,
        result.get("recommendation_text", ""),
        replacement,
        normal_numbers,
        region=region,
    )
    return result

def _get_email_strategy_display(prediction):
    return _get_strategy_label(prediction.strategy)


def _prediction_notice_ball_html(number, zodiac=None, label=None, large=False):
    raw_number_text = str(number or '').strip()
    if not raw_number_text:
        return ''
    try:
        number_value = int(raw_number_text)
    except (TypeError, ValueError):
        return ''
    if not 1 <= number_value <= 49:
        return ''
    number_key = str(number_value)
    number_text = f"{number_value:02d}"
    color = _get_hk_number_color(number_key)
    zodiac_text = str(zodiac or _prediction_notice_zodiac(number_key) or '').strip()
    palette = {
        'red': ('#ef4444', '#991b1b'),
        'green': ('#22c55e', '#166534'),
        'blue': ('#3b82f6', '#1d4ed8'),
    }.get(color, ('#64748b', '#334155'))
    ball_class = f"notice-ball notice-ball-{color or 'unknown'}"
    if large:
        ball_class += " notice-ball-large"
    size = 46 if large else 40
    number_size = 17 if large else 15
    special_decoration = (
        "border:3px solid #facc15;box-shadow:0 0 0 4px rgba(250,204,21,.18),0 0 22px rgba(250,204,21,.55);"
        if large else
        "border:1px solid rgba(255,255,255,.22);box-shadow:inset 0 2px 5px rgba(255,255,255,.28),0 5px 12px rgba(15,23,42,.22);"
    )
    label_html = (
        f'<span class="notice-ball-label" style="display:none!important;mso-hide:all;font-size:0;line-height:0;max-height:0;overflow:hidden;">{escape(label)}</span>'
        if label else ''
    )
    return f'''
    <span class="notice-ball-wrap" style="display:inline-block;vertical-align:top;margin:4px 3px 8px 0;text-align:center;white-space:normal;">
        <span class="{ball_class}" style="display:inline-block;width:{size}px;height:{size}px;border-radius:50%;background:{palette[0]};background:linear-gradient(145deg,{palette[0]},{palette[1]});color:#fff;-webkit-text-fill-color:#fff;{special_decoration}font-weight:800;text-align:center;overflow:hidden;">
            <span class="notice-ball-number" style="display:block;font-size:{number_size}px;line-height:{size}px;color:#fff;-webkit-text-fill-color:#fff;text-shadow:0 1px 2px rgba(15,23,42,.35);">{escape(number_text)}</span>
        </span>
        <span class="notice-ball-zodiac" style="display:block;font-size:11px;line-height:1;color:#475569;-webkit-text-fill-color:#475569;margin-top:4px;font-weight:700;text-align:center;">{escape(zodiac_text)}</span>
        <span class="notice-ball-color-label" style="display:none!important;mso-hide:all;font-size:0;line-height:0;max-height:0;overflow:hidden;"></span>
        {label_html}
    </span>
    '''


def _prediction_notice_zodiac(number):
    try:
        zodiac_year = datetime.now().year
        mapping = _get_number_to_zodiac_map(zodiac_year) or {}
        return mapping.get(str(number), '')
    except Exception:
        return ''


def _prediction_notice_numbers(numbers):
    if numbers is None:
        return []
    if isinstance(numbers, str):
        raw_items = re.split(r'[,，\s]+', numbers.strip())
    else:
        raw_items = list(numbers or [])
    normalized = []
    for item in raw_items:
        text = str(item or '').strip()
        if not text:
            continue
        try:
            number = int(text)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 49:
            normalized.append(str(number))
    return normalized


def _prediction_notice_zodiac_list(raw_zodiacs, limit=None):
    if raw_zodiacs is None:
        return []
    if isinstance(raw_zodiacs, str):
        items = re.split(r'[,，\s]+', raw_zodiacs.strip())
    else:
        items = list(raw_zodiacs or [])
    normalized = [str(item or '').strip() for item in items]
    return normalized[:limit] if limit else normalized


def _prediction_notice_balls_html(numbers, special_number=None, special_zodiac=None, normal_zodiacs=None):
    normalized_numbers = _prediction_notice_numbers(numbers)
    normalized_zodiacs = _prediction_notice_zodiac_list(normal_zodiacs, limit=len(normalized_numbers))
    normal_html = ''.join(
        _prediction_notice_ball_html(
            number,
            zodiac=normalized_zodiacs[index] if index < len(normalized_zodiacs) else None,
        )
        for index, number in enumerate(normalized_numbers)
    )
    special_html = _prediction_notice_ball_html(
        special_number,
        zodiac=special_zodiac,
        label='特码',
        large=True,
    )
    return normal_html, special_html


def _prediction_notice_number_table_html(normal_html, special_html):
    return f'''
    <table class="notice-number-table" role="presentation" cellpadding="0" cellspacing="0" width="100%" style="width:100%;border-collapse:collapse;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
        <tr>
            <th style="width:76%;padding:9px 10px;text-align:center;background:#e2e8f0;color:#0f172a;-webkit-text-fill-color:#0f172a;font-size:14px;font-weight:800;border-bottom:1px solid #cbd5e1;border-right:1px solid #cbd5e1;">平码</th>
            <th style="width:24%;padding:9px 10px;text-align:center;background:#e2e8f0;color:#0f172a;-webkit-text-fill-color:#0f172a;font-size:14px;font-weight:800;border-bottom:1px solid #cbd5e1;">特码</th>
        </tr>
        <tr>
            <td class="notice-normal-cell" style="padding:14px 10px;text-align:center;white-space:nowrap;vertical-align:top;background:#f8fafc;border-right:1px solid #e2e8f0;">
                {normal_html or '<span class="notice-muted-text" style="color:#64748b;-webkit-text-fill-color:#64748b;">暂无</span>'}
            </td>
            <td class="notice-special-cell" style="padding:14px 10px;text-align:center;white-space:nowrap;vertical-align:top;background:#f8fafc;">
                {special_html or '<span class="notice-muted-text" style="color:#64748b;-webkit-text-fill-color:#64748b;">暂无</span>'}
            </td>
        </tr>
    </table>
    '''


def _prediction_notice_special_only_table_html(special_html):
    return f'''
    <table class="notice-number-table" role="presentation" cellpadding="0" cellspacing="0" width="100%" style="width:100%;border-collapse:collapse;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
        <tr>
            <th style="padding:9px 10px;text-align:center;background:#e2e8f0;color:#0f172a;-webkit-text-fill-color:#0f172a;font-size:14px;font-weight:800;border-bottom:1px solid #cbd5e1;">特码</th>
        </tr>
        <tr>
            <td class="notice-special-cell" style="padding:14px 10px;text-align:center;white-space:nowrap;vertical-align:top;background:#f8fafc;">
                {special_html or '<span class="notice-muted-text" style="color:#64748b;-webkit-text-fill-color:#64748b;">暂无</span>'}
            </td>
        </tr>
    </table>
    '''


def _prediction_notice_card_html(title, normal_numbers, special_number, special_zodiac=None, accent='#93c5fd', normal_zodiacs=None, show_normal_numbers=True):
    normal_html, special_html = _prediction_notice_balls_html(normal_numbers, special_number, special_zodiac, normal_zodiacs=normal_zodiacs)
    number_table_html = (
        _prediction_notice_number_table_html(normal_html, special_html)
        if show_normal_numbers
        else _prediction_notice_special_only_table_html(special_html)
    )
    return f'''
    <div class="notice-prediction-card" style="padding:14px 0;border-bottom:1px solid rgba(148,163,184,.18);">
        <div class="notice-card-title" style="font-weight:800;color:{accent};font-size:15px;margin-bottom:8px;">{escape(title)}</div>
        {number_table_html}
    </div>
    '''


def _prediction_notice_email_style():
    return '''
    <style>
    :root {
        color-scheme: light dark;
        supported-color-schemes: light dark;
    }
    .prediction-summary-notice,
    .prediction-summary-notice * {
        box-sizing: border-box;
    }
    .prediction-summary-notice {
        background: #ffffff !important;
        color: #334155 !important;
    }
    .prediction-summary-notice .notice-shell {
        background: #ffffff !important;
        border-color: #e2e8f0 !important;
    }
    .prediction-summary-notice .notice-email-panel {
        background: #ffffff !important;
        color: #334155 !important;
        -webkit-text-fill-color: #334155 !important;
    }
    .prediction-summary-notice .notice-number-table {
        background: #f8fafc !important;
        border-color: #e2e8f0 !important;
    }
    .prediction-summary-notice .notice-number-table th {
        background: #e2e8f0 !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
    }
    .prediction-summary-notice .notice-number-table td {
        background: #f8fafc !important;
    }
    .prediction-summary-notice .notice-ball,
    .prediction-summary-notice .notice-ball-number {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    .prediction-summary-notice .notice-ball-zodiac {
        color: #475569 !important;
        -webkit-text-fill-color: #475569 !important;
    }
    .prediction-summary-notice .notice-muted-text,
    .prediction-summary-notice .notice-footer {
        color: #64748b !important;
        -webkit-text-fill-color: #64748b !important;
    }
    .prediction-summary-notice .notice-ball-red {
        background: #ef4444 !important;
        background-image: linear-gradient(145deg, #ef4444, #991b1b) !important;
    }
    .prediction-summary-notice .notice-ball-blue {
        background: #3b82f6 !important;
        background-image: linear-gradient(145deg, #3b82f6, #1d4ed8) !important;
    }
    .prediction-summary-notice .notice-ball-green {
        background: #22c55e !important;
        background-image: linear-gradient(145deg, #22c55e, #166534) !important;
    }
    .prediction-summary-notice .notice-ball-large {
        border: 3px solid #facc15 !important;
        box-shadow: 0 0 0 4px rgba(250,204,21,.18), 0 0 22px rgba(250,204,21,.55) !important;
    }
    .prediction-summary-notice .notice-ball-color-label,
    .prediction-summary-notice .notice-ball-label {
        display: none !important;
        mso-hide: all;
        font-size: 0 !important;
        line-height: 0 !important;
        max-height: 0 !important;
        overflow: hidden !important;
    }
    @media (prefers-color-scheme: dark) {
        .prediction-summary-notice {
            background: #0f172a !important;
            color: #e2e8f0 !important;
        }
        .prediction-summary-notice .notice-shell {
            background: #0f172a !important;
            border-color: rgba(148, 163, 184, .22) !important;
        }
        .prediction-summary-notice .notice-email-panel {
            background: #0f172a !important;
            color: #e2e8f0 !important;
            -webkit-text-fill-color: #e2e8f0 !important;
        }
        .prediction-summary-notice .notice-number-table {
            background: #0f172a !important;
            border-color: rgba(148, 163, 184, .22) !important;
        }
        .prediction-summary-notice .notice-number-table th {
            background: #1e293b !important;
            color: #f8fafc !important;
            -webkit-text-fill-color: #f8fafc !important;
            border-color: rgba(148, 163, 184, .24) !important;
        }
        .prediction-summary-notice .notice-number-table td {
            background: #0f172a !important;
            border-color: rgba(148, 163, 184, .18) !important;
        }
        .prediction-summary-notice p {
            color: #cbd5e1 !important;
            -webkit-text-fill-color: #cbd5e1 !important;
        }
        .prediction-summary-notice .notice-ball-zodiac {
            color: #cbd5e1 !important;
            -webkit-text-fill-color: #cbd5e1 !important;
        }
        .prediction-summary-notice .notice-muted-text,
        .prediction-summary-notice .notice-footer {
            color: #94a3b8 !important;
            -webkit-text-fill-color: #94a3b8 !important;
        }
    }
    </style>
    '''


def _prediction_notice_wrapper_html(title, intro, body_html, footer_note='', tone='blue'):
    accent = '#2563eb' if tone == 'blue' else '#16a34a'
    return f'''
    {_prediction_notice_email_style()}
    <div class="prediction-summary-notice" style="font-family:Arial,'Microsoft YaHei',sans-serif;line-height:1.55;background:#ffffff;color:#334155;color-scheme:light dark;supported-color-schemes:light dark;">
        <div class="notice-shell" style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
            <div style="background:{accent};color:#fff;padding:16px 18px;">
                <div style="font-size:18px;font-weight:800;">{escape(title)}</div>
                <div style="font-size:13px;opacity:.9;margin-top:3px;">{escape(intro)}</div>
            </div>
            <div class="notice-email-panel" style="padding:16px 18px;background:#ffffff;color:#334155;">
                {body_html}
                {f'<div class="notice-footer" style="font-size:12px;color:#64748b;-webkit-text-fill-color:#64748b;margin-top:12px;">{escape(footer_note)}</div>' if footer_note else ''}
            </div>
        </div>
    </div>
    '''

LOCAL_STRATEGY_KEYS = ["hot", "cold", "trend", "hybrid", "balanced", "markov", "ml"]
_draws_api_cache_lock = threading.Lock()
_draws_api_cache = {}
_DRAWS_API_CACHE_TTL = 60

# 数据库配置
db_path = os.path.join(data_dir, 'lottery_system.db')
app.config['SQLALCHEMY_DATABASE_URI'] = _build_database_uri(db_path)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = _build_engine_options(
    app.config['SQLALCHEMY_DATABASE_URI']
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
_ensure_mysql_database_exists(app.config['SQLALCHEMY_DATABASE_URI'])

# 只在主进程中打印一次
if _should_log_startup():
    db_kind, db_target = _describe_database_target(app.config['SQLALCHEMY_DATABASE_URI'])
    print(f"Database backend: {db_kind}{f' ({db_target})' if db_target else ''}")
    print(f"数据库路径: {db_path}")
    print(f"数据库URI: {_mask_db_uri(app.config['SQLALCHEMY_DATABASE_URI'])}")

# 初始化数据库
# 初始化数据库
db.init_app(app)
with app.app_context():
    _install_mysql_connection_collation_hook()


def _execute_ddl(statement):
    with db.engine.begin() as connection:
        connection.exec_driver_sql(statement)


def _mysql_column_data_type(table_name, column_name):
    if db.engine.dialect.name not in ('mysql', 'mariadb'):
        return ''
    try:
        with db.engine.connect() as connection:
            value = connection.exec_driver_sql(
                """
                SELECT DATA_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND COLUMN_NAME = %s
                LIMIT 1
                """,
                (table_name, column_name),
            ).scalar()
            return str(value or '').lower()
    except Exception:
        return ''


def _quote_identifier(name):
    return db.engine.dialect.identifier_preparer.quote_identifier(str(name))


def _deduplicate_prediction_records():
    duplicate_groups = (
        db.session.query(
            PredictionRecord.user_id,
            PredictionRecord.region,
            PredictionRecord.period,
            PredictionRecord.strategy,
        )
        .group_by(
            PredictionRecord.user_id,
            PredictionRecord.region,
            PredictionRecord.period,
            PredictionRecord.strategy,
        )
        .having(db.func.count(PredictionRecord.id) > 1)
        .all()
    )

    removed_count = 0
    for user_id, region, period, strategy in duplicate_groups:
        duplicates = (
            PredictionRecord.query.filter_by(
                user_id=user_id,
                region=region,
                period=period,
                strategy=strategy,
            )
            .order_by(PredictionRecord.id.desc())
            .all()
        )
        for stale_row in duplicates[1:]:
            db.session.delete(stale_row)
            removed_count += 1

    if removed_count:
        db.session.commit()
        if _should_log_startup():
            print(f"Removed {removed_count} duplicate prediction_record rows")


def _sync_mysql_collation(existing_tables):
    db_name = str(db.engine.url.database or "").strip()
    if not db_name:
        return

    escaped_db_name = db_name.replace("`", "``")
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            f"ALTER DATABASE `{escaped_db_name}` "
            f"CHARACTER SET {MYSQL_CHARSET} COLLATE {MYSQL_COLLATION}"
        )
        rows = connection.exec_driver_sql(
            """
            SELECT DISTINCT TABLE_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND COLLATION_NAME IS NOT NULL
              AND COLLATION_NAME <> %s
            """,
            (db_name, MYSQL_COLLATION),
        ).fetchall()

        for row in rows:
            table_name = row[0]
            if table_name not in existing_tables:
                continue
            try:
                connection.exec_driver_sql(
                    f"ALTER TABLE {_quote_identifier(table_name)} "
                    f"CONVERT TO CHARACTER SET {MYSQL_CHARSET} COLLATE {MYSQL_COLLATION}"
                )
                if _should_log_startup():
                    print(f"Converted {table_name} to {MYSQL_CHARSET}/{MYSQL_COLLATION}")
            except Exception as e:
                print(f"Failed to convert {table_name} collation: {e}")


def _sync_runtime_database_schema():
    inspector = inspect(db.engine)
    dialect = db.engine.dialect.name
    try:
        existing_tables = set(inspector.get_table_names())
    except Exception:
        existing_tables = set()
    if dialect in ('mysql', 'mariadb'):
        try:
            _sync_mysql_collation(existing_tables)
        except Exception as e:
            print(f"Failed to sync MySQL collation: {e}")

    column_specs = {
        'user': {
            'auto_prediction_regions': "VARCHAR(20) DEFAULT 'hk,macau'",
            'show_normal_numbers': 'BOOLEAN DEFAULT 0',
            'github_id': 'VARCHAR(64)',
            'github_username': 'VARCHAR(120)',
        },
        'prediction_record': {
            'prediction_metadata': 'MEDIUMTEXT' if dialect in ('mysql', 'mariadb') else 'TEXT',
        },
        'manual_bet_records': {
            'bettor_name': 'VARCHAR(50)',
        },
    }

    for table_name, columns in column_specs.items():
        if table_name not in existing_tables:
            continue
        try:
            existing_columns = {column['name'] for column in inspector.get_columns(table_name)}
        except Exception:
            existing_columns = set()
        for column_name, ddl in columns.items():
            if column_name in existing_columns:
                continue
            try:
                quoted_table = _quote_identifier(table_name)
                quoted_column = _quote_identifier(column_name)
                _execute_ddl(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {ddl}")
                if _should_log_startup():
                    print(f"Added missing column {table_name}.{column_name} for {dialect}")
            except Exception as e:
                print(f"Failed to add missing column {table_name}.{column_name}: {e}")

    if 'user' in existing_tables:
        try:
            user_indexes = {index.get('name') for index in inspector.get_indexes('user')}
        except Exception:
            user_indexes = set()
        if 'ix_user_github_id' not in user_indexes:
            try:
                _execute_ddl(
                    f"CREATE UNIQUE INDEX ix_user_github_id "
                    f"ON {_quote_identifier('user')} ({_quote_identifier('github_id')})"
                )
                if _should_log_startup():
                    print(f"Added missing index user.github_id for {dialect}")
            except Exception as e:
                print(f"Failed to add missing index user.github_id: {e}")

    if dialect in ('mysql', 'mariadb'):
        mysql_text_columns = {
            'prediction_record': {
                'prediction_text': 'MEDIUMTEXT',
                'prediction_metadata': 'MEDIUMTEXT',
            },
            'backtest_runs': {
                'payload': 'MEDIUMTEXT',
            },
            'system_config': {
                'value': 'MEDIUMTEXT',
            },
        }

        for table_name, columns in mysql_text_columns.items():
            if table_name not in existing_tables:
                continue
            try:
                existing_columns = {column['name'] for column in inspector.get_columns(table_name)}
            except Exception:
                existing_columns = set()
            for column_name, ddl in columns.items():
                if column_name not in existing_columns:
                    continue
                desired_type = str(ddl or '').split()[0].lower()
                current_type = _mysql_column_data_type(table_name, column_name)
                if current_type == desired_type:
                    continue
                try:
                    _execute_ddl(
                        f"ALTER TABLE {_quote_identifier(table_name)} "
                        f"MODIFY COLUMN {_quote_identifier(column_name)} {ddl}"
                    )
                    if _should_log_startup():
                        print(f"Widened {table_name}.{column_name} to {ddl} for {dialect}")
                except Exception as e:
                    print(f"Failed to widen {table_name}.{column_name}: {e}")

    def _index_ddl(table_name, index_name, columns, unique=False):
        unique_sql = 'UNIQUE ' if unique else ''
        column_sql = ', '.join(_quote_identifier(column) for column in columns)
        return (
            f'CREATE {unique_sql}INDEX {_quote_identifier(index_name)} '
            f'ON {_quote_identifier(table_name)} ({column_sql})'
        )

    runtime_index_specs = {
        'user': {
            'ix_user_created_at': ('created_at',),
            'ix_user_activation_expires_at': ('activation_expires_at',),
            'ix_user_invited_by_created_at': ('invited_by', 'created_at'),
            'ix_user_active_auto_prediction': ('is_active', 'auto_prediction_enabled'),
        },
        'activation_code_request': {
            'ix_activation_code_request_user_status': ('user_id', 'status'),
            'ix_activation_code_request_user_created_at': ('user_id', 'created_at'),
        },
        'prediction_record': {
            'uq_prediction_record_user_region_period_strategy': (
                ('user_id', 'region', 'period', 'strategy'),
                True,
            ),
            'ix_prediction_record_user_strategy_created_at': ('user_id', 'strategy', 'created_at'),
            'ix_prediction_record_user_strategy_region_period': (
                'user_id',
                'strategy',
                'region',
                'period',
            ),
            'ix_prediction_record_user_created_at': ('user_id', 'created_at'),
            'ix_prediction_record_region_created_at': ('region', 'created_at'),
        },
        'backtest_runs': {
            'ix_backtest_runs_region_name': ('region', 'name'),
            'ix_backtest_runs_region_created_at': ('region', 'created_at'),
            'ix_backtest_runs_created_at': ('created_at',),
        },
        'invite_code': {
            'ix_invite_code_created_by_used_created_at': ('created_by', 'is_used', 'created_at'),
            'ix_invite_code_created_at': ('created_at',),
        },
        'manual_bet_records': {
            'ix_manual_bet_records_user_region_created_at': ('user_id', 'region', 'created_at'),
            'ix_manual_bet_records_region_period_profit': ('region', 'period', 'total_profit'),
            'ix_manual_bet_records_user_region_period_profit_created_at': (
                'user_id',
                'region',
                'period',
                'total_profit',
                'created_at',
            ),
        },
        'lottery_draws': {
            'ix_lottery_draws_region_draw_date_draw_id': ('region', 'draw_date', 'draw_id'),
        },
    }

    for table_name, indexes in runtime_index_specs.items():
        if table_name not in existing_tables:
            continue
        try:
            existing_indexes = {item['name'] for item in inspector.get_indexes(table_name)}
        except Exception:
            continue
        try:
            existing_unique_constraints = {
                item['name']
                for item in inspector.get_unique_constraints(table_name)
                if item.get('name')
            }
        except Exception:
            existing_unique_constraints = set()

        for name, spec in indexes.items():
            unique = False
            columns = spec
            if (
                isinstance(spec, tuple)
                and len(spec) == 2
                and isinstance(spec[0], tuple)
                and isinstance(spec[1], bool)
            ):
                columns, unique = spec

            if name == 'uq_prediction_record_user_region_period_strategy':
                try:
                    _deduplicate_prediction_records()
                except Exception as e:
                    db.session.rollback()
                    print(f"Failed to deduplicate prediction_record before creating {name}: {e}")

            if name in existing_indexes or name in existing_unique_constraints:
                continue
            try:
                _execute_ddl(_index_ddl(table_name, name, columns, unique=unique))
                if _should_log_startup():
                    print(f"Created missing index {name} on {table_name} for {dialect}")
            except Exception as e:
                print(f"Failed to create missing index {name} on {table_name}: {e}")

def ensure_runtime_database_schema():
    """在应用启动时尽早补齐数据库结构，避免WSGI模式下缺列报错。"""
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print(f"创建数据库表时出错: {e}")

        try:
            _sync_runtime_database_schema()
        except Exception as e:
            print(f"Runtime schema sync failed: {e}")

        try:
            from auto_update_db import check_and_update_database
            check_and_update_database()
        except Exception as e:
            print(f"运行时自动更新数据库结构时出错: {e}")

with _startup_schema_lock():
    ensure_runtime_database_schema()

# 初始化Flask-Login
def cleanup_legacy_smart_strategy():
    """Clean up legacy smart auto-prediction strategies stored in the database."""
    with app.app_context():
        try:
            users = User.query.filter(User.auto_prediction_strategies.like('%smart%')).all()
            if not users:
                return
            default_strategies = ",".join(LOCAL_STRATEGY_KEYS)
            for user in users:
                raw = str(user.auto_prediction_strategies or "").strip()
                parts = [part.strip() for part in raw.split(",") if part.strip() in LOCAL_STRATEGY_KEYS]
                user.auto_prediction_strategies = ",".join(parts) if parts else default_strategies
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Failed to clean legacy smart strategies: {e}")

cleanup_legacy_smart_strategy()

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面。'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.before_request
def _refresh_persistent_session():
    if session.get("user_id"):
        session.permanent = True
        session.modified = True

# 注册蓝图
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(admin_bp)
app.register_blueprint(user_bp)
app.register_blueprint(activation_code_bp)
app.register_blueprint(invite_bp, url_prefix='/invite')
app.register_blueprint(mobile_api_bp)

# 获取AI配置的函数
def get_ai_config():
    return {
        'api_key': SystemConfig.get_config('ai_api_key', '你的_AI_API_KEY'),
        'api_url': SystemConfig.get_config('ai_api_url', 'https://api.deepseek.com/v1/chat/completions'),
        'model': SystemConfig.get_config('ai_model', 'gemini-2.0-flash')
    }
# 澳门数据API
# 原始API可能不可访问，使用备用API
# MACAU_API_URL_TEMPLATE = "https://history.macaumarksix.com/history/macaujc2/y/{year}"
MACAU_API_URL_TEMPLATE = "https://api.macaumarksix.com/history/macaujc2/y/{year}"
# 只在主进程中打印一次
if _should_log_startup():
    print(f"澳门API模板: {MACAU_API_URL_TEMPLATE}")
# 香港数据API
HK_DATA_SOURCE_URL = "https://api3.marksix6.net/lottery_api.php?type=hk"
HK_NEXT_DRAW_TIME_URL = "https://api3.marksix6.net/"

# --- 号码属性计算与映射 ---
ZODIAC_MAPPING_SEQUENCE = ("虎", "兔", "龙", "蛇", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马")
RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46]
BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48]
GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]
COLOR_MAP_EN_TO_ZH = {'red': '红', 'blue': '蓝', 'green': '绿'}
ZODIAC_TRAD_TO_SIMP = {'鼠':'鼠','牛':'牛','虎':'虎','兔':'兔','龍':'龙','蛇':'蛇','馬':'马','羊':'羊','猴':'猴','雞':'鸡','狗':'狗','豬':'猪'}

# 此函数已不再使用，保留是为了兼容性
def _get_hk_number_zodiac(number):
    """
    此函数已不再使用，香港数据也应使用澳门接口返回的生肖数据
    保留此函数仅为兼容性考虑
    """
    return ""

def _get_hk_number_color(number):
    try:
        num = int(number)
        if num in RED_BALLS: return 'red'
        if num in BLUE_BALLS: return 'blue'
        if num in GREEN_BALLS: return 'green'
        return ""
    except:
        return ""

def _get_color_zh(number):
    try:
        num = int(number)
    except (TypeError, ValueError):
        return ""
    if num in RED_BALLS:
        return "红"
    if num in BLUE_BALLS:
        return "蓝"
    if num in GREEN_BALLS:
        return "绿"
    return ""

def _parse_csv_list(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(',') if item.strip()]

def _parse_number_stakes_from_string(value):
    stakes = {}
    if not value:
        return stakes
    for chunk in str(value).split(','):
        part = chunk.strip()
        if not part or ':' not in part:
            continue
        num_str, stake_str = part.split(':', 1)
        try:
            number = int(num_str.strip())
            amount = float(stake_str.strip())
        except (TypeError, ValueError):
            continue
        if number > 0 and amount > 0:
            stakes[number] = amount
    return stakes


def _parse_common_stake_entries(value):
    if not value:
        return []
    entries = []
    for part in str(value).split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            continue
        key, amount_text = piece.split(":", 1)
        key = key.strip()
        try:
            amount = float(amount_text.strip())
        except (TypeError, ValueError):
            continue
        if key and amount > 0:
            entries.append((key, amount))
    return entries


def _serialize_prediction_metadata(metadata):
    if not metadata:
        return ""
    try:
        return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return ""


def _deserialize_prediction_metadata(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _hydrate_prediction_model_meta(strategy, existing_meta, data, region):
    meta = dict(existing_meta or {})
    if strategy != "ml" or not data:
        return meta

    try:
        refreshed = (_predict_with_ml(data, region) or {}).get("model_meta") or {}
        if refreshed:
            meta = {**meta, **refreshed}
        if meta:
            meta["display_copy"] = _build_ml_display_copy(meta)
    except Exception as e:
        print(f"补齐机器学习预测诊断信息失败: {e}")
    return meta


def _hydrate_prediction_recommendation_text(
    strategy,
    existing_text,
    data,
    region,
    special_number=None,
    normal_numbers=None,
    existing_meta=None,
):
    normal_values = []
    if isinstance(normal_numbers, str):
        normal_values = [item.strip() for item in normal_numbers.split(",") if item.strip()]
    elif isinstance(normal_numbers, (list, tuple)):
        normal_values = [str(item).strip() for item in normal_numbers if str(item).strip()]

    special_value = str(special_number or "").strip()

    if strategy == "ai":
        if special_value:
            return _compose_ai_recommendation_text(
                existing_text,
                special_value,
                normal_values,
                region=region,
            )
        return existing_text or ""

    if strategy != "ml":
        return existing_text or ""

    try:
        hydrated_meta = _hydrate_prediction_model_meta(
            strategy,
            existing_meta or {},
            data,
            region,
        )
        if special_value:
            rebuilt_text = _build_special_focus_text(
                special_value,
                normal_values,
                strategy_name="机器学习预测",
                samples=hydrated_meta.get("samples"),
                confidence=hydrated_meta.get("special_probability"),
            )
            if rebuilt_text:
                return rebuilt_text
    except Exception as e:
        print(f"补齐机器学习预测文案失败: {e}")
    return existing_text or ""

def _dedupe_keep_order(values):
    seen = set()
    result = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result

_DRAW_SYNC_INTERVAL = timedelta(minutes=5)
_last_draw_sync_times = {
    'hk': datetime.min,
    'macau': datetime.min
}
_last_sync_window_skip_date = None

def _is_within_sync_window(now):
    if now.hour != 21:
        return False
    return 32 <= now.minute <= 40

def _format_datetime_ymdhm(value):
    return value.strftime("%Y-%m-%d %H:%M")

def _normalize_datetime_string(value):
    if not value:
        return None
    match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})', value)
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    try:
        dt = datetime(int(year), int(month), int(day), int(hour), int(minute))
    except ValueError:
        return None
    return _format_datetime_ymdhm(dt)

def _parse_hk_next_draw_time_from_text(text):
    if not text:
        return None
    match = re.search(r'下期时间[:：]\s*([^\n\r<]+)', text)
    if not match:
        return None
    raw = match.group(1).strip()
    return _normalize_datetime_string(raw) or raw

def _compute_next_hk_draw_time(now=None):
    now = now or datetime.now()
    draw_hour = 21
    draw_minute = 32
    draw_days = {1, 3, 5}  # Tue, Thu, Sat (Python: Mon=0)
    today_draw = datetime(now.year, now.month, now.day, draw_hour, draw_minute)
    if now.weekday() in draw_days and now < today_draw:
        return today_draw
    for i in range(1, 8):
        candidate = now + timedelta(days=i)
        if candidate.weekday() in draw_days:
            return datetime(candidate.year, candidate.month, candidate.day, draw_hour, draw_minute)
    return today_draw + timedelta(days=2)

def _compute_next_macau_draw_time(now=None):
    now = now or datetime.now()
    draw_hour = 21
    draw_minute = 32
    today_draw = datetime(now.year, now.month, now.day, draw_hour, draw_minute)
    if now < today_draw:
        return today_draw
    return today_draw + timedelta(days=1)

def update_hk_next_draw_time_cache(force=False):
    now = datetime.now()
    if not force:
        cached_at = SystemConfig.get_config('hk_next_draw_time_cached_at', '').strip()
        if cached_at:
            try:
                cached_dt = datetime.fromisoformat(cached_at)
                if now - cached_dt < timedelta(minutes=30):
                    return
            except ValueError:
                pass

    value = None
    try:
        response = requests.get(HK_NEXT_DRAW_TIME_URL, timeout=10)
        if response.ok:
            if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
                response.encoding = "utf-8"
            value = _parse_hk_next_draw_time_from_text(response.text)
    except Exception as e:
        print(f"获取香港下期时间失败: {e}")

    if not value:
        value = _format_datetime_ymdhm(_compute_next_hk_draw_time(now))

    SystemConfig.set_config('hk_next_draw_time', value, '香港下期时间')
    SystemConfig.set_config(
        'hk_next_draw_time_cached_at',
        now.isoformat(timespec='seconds'),
        '香港下期时间缓存更新时间'
    )

@app.route('/api/next_draw_time')
def next_draw_time_api():
    region = request.args.get('region', 'hk').strip().lower()
    now = datetime.now()
    if region == 'hk':
        update_hk_next_draw_time_cache(force=False)
        value = SystemConfig.get_config('hk_next_draw_time', '').strip()
        if not value:
            value = _format_datetime_ymdhm(_compute_next_hk_draw_time(now))
            SystemConfig.set_config('hk_next_draw_time', value, '香港下期时间')
            SystemConfig.set_config(
                'hk_next_draw_time_cached_at',
                now.isoformat(timespec='seconds'),
                '香港下期时间缓存更新时间'
            )
        return jsonify({
            "success": True,
            "region": "hk",
            "next_time": value
        })
    if region == 'macau':
        value = _format_datetime_ymdhm(_compute_next_macau_draw_time(now))
        return jsonify({
            "success": True,
            "region": "macau",
            "next_time": value
        })
    return jsonify({
        "success": False,
        "message": "未知地区"
    }), 400

def _finalize_ai_result(ai_response):
    normal_numbers, special_number = _extract_ai_numbers(ai_response)
    if not normal_numbers or not special_number:
        return None, "无法从AI回复中提取有效号码"

    normal_numbers = [n for n in normal_numbers if 1 <= n <= 49]
    if len(normal_numbers) < 6:
        return None, "AI生成的平码数量不足"

    try:
        special_num_value = int(special_number)
    except (TypeError, ValueError):
        special_num_value = None
    if special_num_value is not None:
        normal_numbers = [n for n in normal_numbers if n != special_num_value]
    normal_numbers = _dedupe_keep_order(normal_numbers)[:6]

    if not special_number or not (1 <= int(special_number) <= 49):
        return None, "AI生成的特码无效"

    sno_zodiac = ""
    return {
        "recommendation_text": _build_special_focus_text(special_number, normal_numbers),
        "normal": normal_numbers,
        "special": {
            "number": special_number,
            "sno_zodiac": sno_zodiac
        }
    }, None

def _extract_ai_numbers(ai_response):
    if not ai_response:
        return None, None

    normalized = (
        str(ai_response)
        .replace("：", ":")
        .replace("，", ",")
        .replace("、", ",")
        .replace("【", "[")
        .replace("】", "]")
        .replace("特碼", "特码")
    )

    json_match = re.search(
        r'"normal"\s*:\s*\[\s*([0-9\s,]{5,})\s*\].{0,120}?"special"\s*:\s*"?(\d{1,2})"?',
        normalized,
        flags=re.IGNORECASE | re.DOTALL
    )
    if json_match:
        normal_numbers = [int(n) for n in re.findall(r'\d{1,2}', json_match.group(1))]
        special_number = json_match.group(2)
        if len(normal_numbers) >= 6:
            return normal_numbers[:6], special_number

    list_patterns = [
        r'推荐号码\s*[:：]\s*\[\s*([0-9\s,，]{5,})\s*\]',
        r'号码推荐\s*[:：]\s*\[\s*([0-9\s,，]{5,})\s*\]',
        r'参考平码\s*[:：]\s*\[\s*([0-9\s,，]{5,})\s*\]',
        r'推荐号码\s*[:：]\s*([0-9\s,，]{5,})',
        r'号码推荐\s*[:：]\s*([0-9\s,，]{5,})',
        r'参考平码\s*[:：]\s*([0-9\s,，]{5,})',
    ]
    special_patterns = [
        r'特?码\s*[:：]\s*\[\s*(\d{1,2})(?:\s*[^\d\]]+)?\s*\]',
        r'特?码\s*[:：]\s*(\d{1,2})(?:\s*[^\d]+)?',
    ]

    normal_numbers = None
    special_number = None

    for pattern in list_patterns:
        matches = list(re.finditer(pattern, normalized, flags=re.IGNORECASE))
        if not matches:
            continue
        match = matches[-1]
        normal_numbers = [int(n) for n in re.findall(r'\d{1,2}', match.group(1))]
        break

    for pattern in special_patterns:
        matches = list(re.finditer(pattern, normalized, flags=re.IGNORECASE))
        if not matches:
            continue
        special_number = matches[-1].group(1)
        break

    if not special_number:
        special_line_match = re.search(r'特码\s*[:：]\s*\[\s*(\d{1,2})\s*\]', normalized)
        if special_line_match:
            special_number = special_line_match.group(1)

    if normal_numbers and special_number:
        return normal_numbers, special_number

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    candidate_lines = [
        line for line in lines
        if any(k in line for k in ("推荐号码", "号码推荐", "参考平码", "特码"))
    ]

    for line in candidate_lines:
        if "特码" not in line:
            continue
        parts = re.split(r'特码', line, maxsplit=1)
        normal_numbers = [int(n) for n in re.findall(r'\d{1,2}', parts[0])]
        special_candidates = re.findall(r'\d{1,2}', parts[1]) if len(parts) > 1 else []
        if len(normal_numbers) >= 6 and special_candidates:
            return normal_numbers[:6], special_candidates[0]

    scoped_numbers = []
    for line in candidate_lines:
        scoped_numbers.extend(re.findall(r'\d{1,2}', line))
    valid_numbers = [int(n) for n in scoped_numbers if 1 <= int(n) <= 49]
    if len(valid_numbers) >= 7:
        return valid_numbers[:6], str(valid_numbers[6])

    return None, None


def _normalize_ai_response_v2(ai_response):
    return (
        str(ai_response or "")
        .replace("：", ":")
        .replace("，", ",")
        .replace("【", "[")
        .replace("】", "]")
        .replace("特碼", "特码")
        .replace("特码号", "特码")
        .replace("號碼", "号码")
    )


def _extract_ai_numbers_v2(ai_response, region=None):
    if not ai_response:
        return None, None

    normalized = _normalize_ai_response_v2(ai_response)

    json_patterns = [
        r'"normal"\s*:\s*\[\s*([0-9\s,]{5,})\s*\].{0,160}?"special"\s*:\s*"?(\d{1,2})"?',
        r'"recommended_numbers"\s*:\s*\[\s*([0-9\s,]{5,})\s*\].{0,160}?"special_number"\s*:\s*"?(\d{1,2})"?',
    ]
    for pattern in json_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        normal_numbers = [int(n) for n in re.findall(r'\d{1,2}', match.group(1))]
        if len(normal_numbers) >= 6:
            return normal_numbers[:6], match.group(2)

    label_pairs = [("推荐号码", "特码"), ("号码推荐", "特码"), ("参考平码", "本期主推特码"), ("参考平码", "特码")]
    if region == "macau":
        label_pairs.extend([("平码", "特码"), ("号码", "特码")])
    for normal_label, special_label in label_pairs:
        pattern = rf'{normal_label}\s*[:：]\s*\[\s*([0-9\s,，]{{5,}})\s*\].{{0,80}}?{special_label}\s*[:：]\s*\[\s*(\d{{1,2}})\s*\]'
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        normal_numbers = [int(n) for n in re.findall(r'\d{1,2}', match.group(1))]
        if len(normal_numbers) >= 6:
            return normal_numbers[:6], match.group(2)

    return _extract_ai_numbers(normalized)


def _finalize_ai_result_v2(ai_response, region=None):
    normal_numbers, special_number = _extract_ai_numbers_v2(ai_response, region=region)
    if not normal_numbers or not special_number:
        return None, "无法从AI回复中提取有效号码"

    normal_numbers = [n for n in normal_numbers if 1 <= n <= 49]
    if len(normal_numbers) < 6:
        return None, "AI生成的平码数量不足"

    try:
        special_num_value = int(special_number)
    except (TypeError, ValueError):
        special_num_value = None

    if special_num_value is None or not (1 <= special_num_value <= 49):
        return None, "AI生成的特码无效"

    normal_numbers = [n for n in normal_numbers if n != special_num_value]
    normal_numbers = _dedupe_keep_order(normal_numbers)[:6]
    if len(normal_numbers) < 6:
        return None, "AI生成的平码数量不足"

    return {
        "recommendation_text": _compose_ai_recommendation_text(
            ai_response,
            str(special_num_value),
            normal_numbers,
            region=region,
        ),
        "normal": normal_numbers,
        "special": {
            "number": str(special_num_value),
            "sno_zodiac": ""
        }
    }, None

def _settle_manual_bet_record(record, draw):
    raw_zodiacs = _parse_csv_list(draw.raw_zodiac)
    special_zodiac = draw.special_zodiac or ""
    if raw_zodiacs:
        special_zodiac = raw_zodiacs[-1] or special_zodiac

    special_number = draw.special_number or ""
    special_color = _get_color_zh(special_number)
    special_parity = ""
    try:
        special_parity = "双" if int(special_number) % 2 == 0 else "单"
    except (TypeError, ValueError):
        special_parity = ""

    number_stakes = _parse_number_stakes_from_string(record.selected_numbers)
    if number_stakes:
        selected_numbers = list(number_stakes.keys())
    else:
        selected_numbers = [int(n) for n in _parse_csv_list(record.selected_numbers) if n.isdigit()]
    zodiac_entries = _parse_common_stake_entries(record.selected_zodiacs)
    color_entries = _parse_common_stake_entries(record.selected_colors)
    parity_entries = _parse_common_stake_entries(record.selected_parity)
    selected_zodiacs = (
        [value for value, _ in zodiac_entries]
        if zodiac_entries
        else _parse_csv_list(record.selected_zodiacs)
    )
    selected_colors = (
        [value for value, _ in color_entries]
        if color_entries
        else _parse_csv_list(record.selected_colors)
    )
    selected_parity = (
        [value for value, _ in parity_entries]
        if parity_entries
        else _parse_csv_list(record.selected_parity)
    )

    stake_special = record.stake_special or 0
    stake_common = record.stake_common or 0
    odds_number = record.odds_number or 0
    odds_zodiac = record.odds_zodiac or 0
    odds_color = record.odds_color or 0
    odds_parity = record.odds_parity or 0

    result_number = None
    result_zodiac = None
    result_color = None
    result_parity = None
    profit_number = None
    profit_zodiac = None
    profit_color = None
    profit_parity = None
    total_profit = 0

    if selected_numbers:
        result_number = special_number.isdigit() and int(special_number) in selected_numbers
        if number_stakes:
            hit_stake = number_stakes.get(int(special_number), 0) if special_number.isdigit() else 0
            total_stake_number = sum(number_stakes.values())
            profit_number = hit_stake * odds_number - total_stake_number
            total_profit += profit_number
        else:
            profit_number = (
                stake_special * odds_number - stake_special
                if result_number
                else -stake_special
            )
            total_profit += profit_number

    if selected_zodiacs:
        if zodiac_entries:
            result_zodiac = any(value == special_zodiac for value, _ in zodiac_entries)
            profit_zodiac = 0
            for value, amount in zodiac_entries:
                if value == special_zodiac:
                    profit_zodiac += amount * odds_zodiac - amount
                else:
                    profit_zodiac += -amount
        else:
            result_zodiac = special_zodiac in selected_zodiacs
            profit_zodiac = (
                stake_common * odds_zodiac - stake_common
                if result_zodiac
                else -stake_common
            )
        total_profit += profit_zodiac

    if selected_colors:
        if color_entries:
            result_color = any(value == special_color for value, _ in color_entries)
            profit_color = 0
            for value, amount in color_entries:
                if value == special_color:
                    profit_color += amount * odds_color - amount
                else:
                    profit_color += -amount
        else:
            result_color = special_color in selected_colors
            profit_color = (
                stake_common * odds_color - stake_common
                if result_color
                else -stake_common
            )
        total_profit += profit_color

    if selected_parity:
        if parity_entries:
            result_parity = any(value == special_parity for value, _ in parity_entries)
            profit_parity = 0
            for value, amount in parity_entries:
                if value == special_parity:
                    profit_parity += amount * odds_parity - amount
                else:
                    profit_parity += -amount
        else:
            result_parity = special_parity in selected_parity
            profit_parity = (
                stake_common * odds_parity - stake_common
                if result_parity
                else -stake_common
            )
        total_profit += profit_parity

    record.result_number = result_number
    record.result_zodiac = result_zodiac
    record.result_color = result_color
    record.result_parity = result_parity
    record.profit_number = profit_number
    record.profit_zodiac = profit_zodiac
    record.profit_color = profit_color
    record.profit_parity = profit_parity
    record.total_profit = total_profit
    if number_stakes and record.total_stake is None:
        total_stake_number = sum(number_stakes.values())
        extra_common = 0
        if zodiac_entries:
            extra_common += sum(amount for _, amount in zodiac_entries)
        elif selected_zodiacs:
            extra_common += stake_common
        if color_entries:
            extra_common += sum(amount for _, amount in color_entries)
        elif selected_colors:
            extra_common += stake_common
        if parity_entries:
            extra_common += sum(amount for _, amount in parity_entries)
        elif selected_parity:
            extra_common += stake_common
        record.total_stake = total_stake_number + extra_common
    record.special_number = special_number
    record.special_zodiac = special_zodiac
    record.special_color = special_color
    record.special_parity = special_parity

def settle_pending_manual_bets(region, draw_id):
    if not draw_id:
        return 0
    draw = LotteryDraw.query.filter_by(region=region, draw_id=draw_id).first()
    if not draw:
        return 0
    pending_records = ManualBetRecord.query.filter_by(
        region=region, period=draw_id
    ).filter(ManualBetRecord.total_profit.is_(None)).all()
    if not pending_records:
        return 0
    for record in pending_records:
        _settle_manual_bet_record(record, draw)
    db.session.commit()
    return len(pending_records)

# --- 数据加载与处理 ---
def load_hk_data(force_refresh=False):
    """从新接口获取香港开奖数据"""
    try:
        print(f"正在获取香港数据，URL: {HK_DATA_SOURCE_URL}")
        params = {"_": int(time.time())} if force_refresh else None
        response = requests.get(HK_DATA_SOURCE_URL, params=params, timeout=15)
        response.raise_for_status()
        api_data = response.json()
        
        # 标准化数据格式
        normalized_data = []
        
        # 如果返回的是单条数据，转换为列表
        if isinstance(api_data, dict):
            api_data = [api_data]
        elif isinstance(api_data, list):
            pass
        else:
            print(f"香港API返回数据格式错误: {api_data}")
            return []
        
        for record in api_data:
            raw_numbers_str = record.get("openCode", "").split(',')
            try:
                numbers = [str(int(n)) for n in raw_numbers_str]
            except (ValueError, TypeError):
                continue
            
            traditional_zodiacs = record.get("zodiac", "").split(',')
            if len(numbers) < 7:
                continue
            
            # 简化生肖
            simplified_zodiacs = [ZODIAC_TRAD_TO_SIMP.get(z, z) for z in traditional_zodiacs]
            
            # 提取波浪（颜色）信息
            wave = record.get("wave", "").split(',')
            
            normalized_data.append({
                "id": record.get("expect"),
                "date": record.get("openTime"),
                "no": numbers[:6],
                "sno": numbers[6],
                "sno_zodiac": simplified_zodiacs[6] if len(simplified_zodiacs) >= 7 else "",
                "raw_zodiac": ",".join(simplified_zodiacs),
                "raw_wave": ",".join(wave)
            })
        
        print(f"香港API返回数据条数: {len(normalized_data)}")
        
        # 去重
        unique_data = []
        seen_ids = set()
        for record in normalized_data:
            record_id = record.get("id")
            if record_id and record_id not in seen_ids:
                unique_data.append(record)
                seen_ids.add(record_id)
        
        print(f"去重后数据条数: {len(unique_data)}")
        
        # 按日期和期号排序（降序）
        result = sorted(unique_data, key=lambda x: (x.get('date', ''), x.get('id', '')), reverse=True)
        
        if len(result) > 0:
            print(f"最新一期数据: {result[0]}")
        
        return result
        
    except Exception as e:
        print(f"从URL获取香港数据失败: {e}")
        return []

def _fetch_macau_data_from_api(year):
    url = MACAU_API_URL_TEMPLATE.format(year=year)
    try:
        print(f"正在获取澳门数据，URL: {url}")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        api_data = response.json()
        if not api_data or not api_data.get("data"): 
            print(f"澳门API返回空数据或格式错误: {api_data}")
            return []
        
        print(f"澳门API返回数据条数: {len(api_data['data'])}")
        
        normalized_data = []
        for record in api_data["data"]:
            raw_numbers_str = record.get("openCode", "").split(',')
            try:
                numbers = [str(int(n)) for n in raw_numbers_str]
            except (ValueError, TypeError):
                continue
            traditional_zodiacs = record.get("zodiac", "").split(',')
            if len(numbers) < 7: continue

            simplified_zodiacs = [ZODIAC_TRAD_TO_SIMP.get(z, z) for z in traditional_zodiacs]
            
            normalized_data.append({
                "id": record.get("expect"), "date": record.get("openTime"), "no": numbers[:6], "sno": numbers[6],
                "sno_zodiac": simplified_zodiacs[6] if len(simplified_zodiacs) >= 7 else "",
                "raw_wave": record.get("wave", ""), "raw_zodiac": ",".join(simplified_zodiacs)
            })
        
        print(f"标准化后的数据条数: {len(normalized_data)}")
        
        # --- 新增去重逻辑 ---
        unique_data = []
        seen_ids = set()
        for record in normalized_data:
            record_id = record.get("id")
            if record_id and record_id not in seen_ids:
                unique_data.append(record)
                seen_ids.add(record_id)
        # --- 去重逻辑结束 ---
        
        print(f"去重后的数据条数: {len(unique_data)}")

        # 使用去重后的 unique_data 进行过滤和排序
        filtered_by_year = [rec for rec in unique_data if rec.get("date", "").startswith(str(year))]
        print(f"按年份过滤后的数据条数: {len(filtered_by_year)}")
        
        result = sorted(filtered_by_year, key=lambda x: (x.get('date', ''), x.get('id', '')), reverse=True)
        print(f"最终返回的数据条数: {len(result)}")
        
        if len(result) > 0:
            print(f"示例数据: {result[0]}")
        
        return result
    except Exception as e:
        print(f"Error in get_macau_data for year {year}: {e}")
        return []

def get_macau_data(year, force_api=False):
    if not force_api:
        try:
            query = LotteryDraw.query.filter_by(region='macau')
            if year != 'all':
                query = query.filter(LotteryDraw.draw_date.like(f"{year}%"))
            db_records = query.order_by(LotteryDraw.draw_date.desc()).all()
            if db_records:
                print(f"从数据库获取到{len(db_records)}条澳门{year}年数据")
                return [record.to_dict() for record in db_records]
        except Exception as e:
            print(f"从数据库获取澳门数据失败: {e}")

    return _fetch_macau_data_from_api(year)

def analyze_special_number_frequency(data):
    special_numbers = []
    for r in data:
        if r.get('sno'):
            special_numbers.append(r.get('sno'))
    counts = Counter(special_numbers)
    return {str(i): counts.get(str(i), 0) for i in range(1, 50)}

def _clamp(value, low, high):
    return max(low, min(high, value))


def _normalize_period_value(period):
    raw = str(period or "").strip()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or raw


def _period_sort_key(period):
    normalized = _normalize_period_value(period)
    if normalized.isdigit():
        return (1, int(normalized))
    return (0, normalized)


def _is_period_before(candidate_period, cutoff_period):
    if not cutoff_period:
        return True
    return _period_sort_key(candidate_period) < _period_sort_key(cutoff_period)


def _is_secondary_hit(prediction, actual_special, actual_zodiac):
    return False


def _softmax(values):
    if not values:
        return []
    max_value = max(values)
    exp_values = [math.exp(_clamp(value - max_value, -30.0, 30.0)) for value in values]
    total = sum(exp_values)
    if total <= 0:
        fallback = 1.0 / len(values)
        return [fallback] * len(values)
    return [value / total for value in exp_values]


def _build_ml_heuristic_score_map(feature_table):
    score_map = {}
    for key, features in (feature_table or {}).items():
        if not features:
            continue
        momentum_short = max(0.0, features[15]) if len(features) > 15 else 0.0
        momentum_medium = max(0.0, features[16]) if len(features) > 16 else 0.0
        recent_special_penalty = features[6] if len(features) > 6 else 0.0
        recent_number_penalty = features[7] if len(features) > 7 else 0.0
        score_map[key] = (
            features[0] * 0.22 +
            features[1] * 0.18 +
            features[2] * 0.10 +
            features[3] * 0.06 +
            features[4] * 0.12 +
            features[5] * 0.09 +
            features[8] * 0.07 +
            features[9] * 0.05 +
            features[10] * 0.06 +
            features[11] * 0.05 +
            momentum_short * 0.10 +
            momentum_medium * 0.08 -
            recent_special_penalty * 0.08 -
            recent_number_penalty * 0.04 +
            (features[17] * 0.05 if len(features) > 17 else 0.0) +
            (features[18] * 0.06 if len(features) > 18 else 0.0) -
            (features[19] * 0.07 if len(features) > 19 else 0.0) +
            (features[20] * 0.05 if len(features) > 20 else 0.0) +
            (features[21] * 0.06 if len(features) > 21 else 0.0) +
            (features[22] * 0.03 if len(features) > 22 else 0.0)
        )
    return _normalize_metric_map(score_map)


def _blend_ml_rankings(probability_map, heuristic_map, blend_weight):
    numbers = sorted({
        int(number)
        for number in list(probability_map.keys()) + list(heuristic_map.keys())
    })
    blended = {}
    for number in numbers:
        key = str(number)
        blended[number] = round(
            float(probability_map.get(number, probability_map.get(key, 0.0))) * blend_weight +
            float(heuristic_map.get(number, heuristic_map.get(key, 0.0))) * (1.0 - blend_weight),
            6
        )
    return blended

def _strategy_config_key(region, strategy):
    return f"strategy_config_{region}_{strategy}"

def _default_strategy_config(strategy):
    defaults = {
        "hot": {
            "window": 50,
            "pool": 16,
            "special_pool": 10,
            "weights": {"hot": 1.25, "trend": 0.55, "cold": 0.05, "normal": 0.35, "overdue": 0.15, "feedback": 0.95, "color": 0.18, "zodiac": 0.16, "parity": 0.18},
            "last_accuracy": 0.0,
            "last_total": 0
        },
        "cold": {
            "window": 50,
            "pool": 16,
            "special_pool": 10,
            "weights": {"hot": 0.10, "trend": 0.25, "cold": 1.20, "normal": 0.15, "overdue": 0.95, "feedback": 0.75, "color": 0.15, "zodiac": 0.14, "parity": 0.16},
            "last_accuracy": 0.0,
            "last_total": 0
        },
        "trend": {
            "window": 15,
            "pool": 18,
            "special_pool": 10,
            "weights": {"hot": 0.55, "trend": 1.30, "cold": 0.05, "normal": 0.40, "overdue": 0.12, "feedback": 0.90, "color": 0.18, "zodiac": 0.18, "parity": 0.18},
            "last_accuracy": 0.0,
            "last_total": 0
        },
        "balanced": {
            "window": 60,
            "pool": 16,
            "special_pool": 10,
            "bucket_counts": [2, 2, 2],
            "weights": {"hot": 0.55, "trend": 0.55, "cold": 0.45, "normal": 0.45, "overdue": 0.35, "feedback": 0.95, "color": 0.20, "zodiac": 0.20, "parity": 0.20},
            "last_accuracy": 0.0,
            "last_total": 0
        },
        "markov": {
            "window": 80,
            "pool": 18,
            "special_pool": 10,
            "transition_decay": 0.985,
            "source_special_weight": 1.28,
            "transition_min_samples": 3,
            "repeat_penalty": -0.18,
            "promotion_cooldown_hours": 6,
            "promotion_min_gain": 0.25,
            "weights": {"transition": 1.35, "transition_lift": 0.32, "second_order": 0.72, "phase_transition": 0.55, "attribute_transition": 0.42, "special_transition": 1.10, "special_transition_lift": 0.28, "special_chain": 0.62, "special_attribute": 0.28, "failure": 0.48, "hot": 0.22, "trend": 0.36, "normal": 0.22, "overdue": 0.16, "feedback": 0.85, "color": 0.18, "zodiac": 0.18, "parity": 0.16},
            "last_accuracy": 0.0,
            "last_total": 0
        },
        "hybrid": {
            "window": 50,
            "pool": 16,
            "special_pool": 10,
            "trend_window": 15,
            "mix": {"hot": 2, "cold": 2, "trend": 2},
            "weights": {"hot": 0.85, "trend": 0.85, "cold": 0.70, "normal": 0.40, "overdue": 0.35, "feedback": 1.05, "color": 0.22, "zodiac": 0.22, "parity": 0.22},
            "last_accuracy": 0.0,
            "last_total": 0
        },
        "ml": {
            "history_window": 120,
            "feature_window": 60,
            "evaluation_window": 30,
            "pool": 18,
            "special_pool": 8,
            "bucket_counts": [2, 2, 2],
            "epochs": 18,
            "learning_rate": 0.035,
            "l2": 0.0025,
            "early_stopping_patience": 4,
            "validation_floor": 0.88,
            "primary_feature_profile": "full",
            "primary_runtime_profile": "base",
            "blend_candidates": [0.55, 0.7, 0.82],
            "ensemble_core_strategies": ["hybrid", "balanced", "trend"],
            "ensemble_replace_margin": 4.0,
            "ensemble_replace_min_samples": 8,
            "last_accuracy": 0.0,
            "last_total": 0
        },
        "ai": {
            "history_window": 12,
            "temperature": 0.35,
            "sample_count": 3,
            "candidate_count": 3,
            "special_shortlist": 8,
            "normal_shortlist": 18,
            "target_mode": "top1",
            "rerank_weights": {
                "base_special": 0.9,
                "avg_normal": 0.55,
                "special_vote": 0.18,
                "normal_vote_avg": 0.1,
                "shortlist_bonus": 1.0,
                "attr_bonus": 1.0,
                "diversity_bonus": 1.0,
                "repeat_penalty": 1.0,
                "overheat_penalty": 1.0,
                "confidence_bonus": 1.0,
                "shape_score": 1.0,
                "structure_bonus": 1.0,
                "gate_adjustment": 1.0,
                "appearance_vote": 0.24,
            },
            "last_accuracy": 0.0,
            "last_total": 0
        },
    }
    return defaults.get(strategy, {})

def _load_strategy_config(strategy, region):
    key = _strategy_config_key(region, strategy)
    raw = SystemConfig.get_config(key, "")
    stored = {}
    if raw:
        try:
            stored = json.loads(raw)
        except Exception:
            stored = {}
    default = _default_strategy_config(strategy)
    merged = {**default, **stored}
    for key, default_value in default.items():
        stored_value = stored.get(key)
        if isinstance(default_value, dict) and isinstance(stored_value, dict):
            merged[key] = {**default_value, **stored_value}
    override_bucket = getattr(_strategy_config_override_local, "configs", {})
    override = dict(override_bucket.get((region, strategy)) or {})
    if override:
        base_before_override = dict(merged)
        merged = {**merged, **override}
        for field, override_value in override.items():
            default_value = base_before_override.get(field)
            if isinstance(default_value, dict) and isinstance(override_value, dict):
                merged[field] = {**default_value, **override_value}
    if "updated_at" not in merged:
        merged["updated_at"] = datetime.now().isoformat()
    return merged

def _save_strategy_config(strategy, region, config):
    key = _strategy_config_key(region, strategy)
    payload = json.dumps(config, ensure_ascii=True)
    SystemConfig.set_config(key, payload, f"Auto-tuned config for {strategy} ({region})")


@contextmanager
def _temporary_strategy_config_override(region, strategy, override):
    if not override:
        yield
        return

    bucket = dict(getattr(_strategy_config_override_local, "configs", {}) or {})
    override_key = (region, strategy)
    previous = bucket.get(override_key)
    merged_override = {**(previous or {}), **dict(override)}
    bucket[override_key] = merged_override
    _strategy_config_override_local.configs = bucket
    try:
        yield
    finally:
        current_bucket = dict(getattr(_strategy_config_override_local, "configs", {}) or {})
        if previous is None:
            current_bucket.pop(override_key, None)
        else:
            current_bucket[override_key] = previous
        _strategy_config_override_local.configs = current_bucket


def _current_backtest_cutoff_period():
    return str(getattr(_backtest_cutoff_period_local, "period", "") or "").strip()


def _backtest_strict_strategy_enabled():
    return bool(getattr(_backtest_strict_strategy_local, "enabled", False))


@contextmanager
def _temporary_backtest_cutoff_period(period):
    previous = getattr(_backtest_cutoff_period_local, "period", None)
    normalized = str(period or "").strip()
    if normalized:
        _backtest_cutoff_period_local.period = normalized
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_backtest_cutoff_period_local, "period")
            except AttributeError:
                pass
        else:
            _backtest_cutoff_period_local.period = previous


@contextmanager
def _temporary_strict_backtest_strategy(enabled=True):
    previous = getattr(_backtest_strict_strategy_local, "enabled", None)
    _backtest_strict_strategy_local.enabled = bool(enabled)
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_backtest_strict_strategy_local, "enabled")
            except AttributeError:
                pass
        else:
            _backtest_strict_strategy_local.enabled = previous


def _normalize_draw_number(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(int(text))
    except (TypeError, ValueError):
        return text


def _ml_runtime_profile_label(value):
    mapping = {
        "base": "标准模式",
        "compact": "轻量模式",
        "deep": "深度模式",
        "adaptive": "自动调整",
        "recent_bias": "更看近期走势",
        "context_bias": "更看号码属性",
        "recency_trim": "少看复杂走势",
        "regularized": "更稳一点",
        "blend_search": "多种算法混合试算",
        "learned_feature_bias": "按近期学习结果微调",
    }
    key = str(value or "").strip()
    return mapping.get(key, key or "标准模式")


def _ml_feature_profile_label(value):
    mapping = {
        "full": "综合参考全部因素",
        "compact_structure": "少看整体结构",
        "compact_attributes": "少看波色生肖单双",
        "compact_recency": "少看近期走势",
    }
    key = str(value or "").strip()
    return mapping.get(key, key or "综合参考全部因素")


def _ml_promotion_strength_label(value):
    mapping = {
        "hold": "继续观察",
        "watch": "重点观察",
        "promoted": "已作为常用设置",
    }
    key = str(value or "").strip()
    return mapping.get(key, key or "观察中")


def _build_ml_display_copy(model_meta):
    meta = dict(model_meta or {})
    display = {}
    normal_numbers = [
        str(item).strip()
        for item in (meta.get("normal_numbers") or [])
        if str(item).strip()
    ]

    primary_runtime = _ml_runtime_profile_label(meta.get("primary_runtime_profile"))
    primary_feature = _ml_feature_profile_label(meta.get("primary_feature_profile"))
    display["primary_config"] = (
        f"平时设置：{primary_runtime} · {primary_feature}；本次会再试算一遍，选表现更合适的组合。"
    )

    preferred_features = [
        _ml_feature_profile_label(item)
        for item in (meta.get("preferred_feature_profiles") or [])
        if str(item or "").strip()
    ]
    if preferred_features:
        line = f"本地区近期更适合：{'、'.join(preferred_features)}"
        if meta.get("profile_learning_confidence") is not None:
            line += f"；参考度 {meta.get('profile_learning_confidence')}%"
        display["preferred_features"] = line

    preferred_runtimes = [
        _ml_runtime_profile_label(item)
        for item in (meta.get("preferred_runtime_profiles") or [])
        if str(item or "").strip()
    ]
    if preferred_runtimes:
        display["preferred_runtimes"] = f"本地区常用偏向：{'、'.join(preferred_runtimes)}"

    color_preference = str(meta.get("preferred_special_color") or "").strip()
    color_preferences = meta.get("color_preferences") or {}
    if color_preference:
        color_conf = color_preferences.get(color_preference)
        suffix = (
            f"（历史特码参考 {color_conf}%）"
            if color_conf is not None else "（历史特码参考）"
        )
        display["color_preference"] = f"本期波色参考：{color_preference}{suffix}"

    parity_preference = str(meta.get("preferred_special_parity") or "").strip()
    parity_preferences = meta.get("parity_preferences") or {}
    if parity_preference:
        parity_conf = parity_preferences.get(parity_preference)
        suffix = (
            f"（历史特码参考 {parity_conf}%）"
            if parity_conf is not None else "（历史特码参考）"
        )
        display["parity_preference"] = f"本期单双参考：{parity_preference}{suffix}"

    if normal_numbers:
        display["six_reference"] = f"一起参考的六码：{'、'.join(normal_numbers[:6])}"

    if meta.get("final_top1_hit_rate") is not None:
        display["final_selection_backtest"] = (
            f"过去按这套选法，一码命中过 {meta.get('final_top1_hit_rate')}%"
            f"（看了 {int(meta.get('evaluation_draws') or 0)} 期样本）"
        )

    special_selection_reason = str(meta.get("special_selection_reason") or "").strip()
    if special_selection_reason:
        display["special_selection_reason"] = special_selection_reason

    selected_strategies = [
        _get_strategy_label(item)
        for item in (meta.get("ensemble_selected_strategies") or [])
        if str(item or "").strip()
    ]
    if selected_strategies:
        display["selected_strategies"] = f"这次主要参考：{'、'.join(selected_strategies)}"

    weight_entries = sorted(
        (meta.get("ensemble_strategy_weights") or {}).items(),
        key=lambda item: float(item[1] or 0.0),
        reverse=True,
    )
    weight_text = ""
    if weight_entries:
        weight_text = "、".join(
            f"{_get_strategy_label(key)}:{str(round(float(value), 1)).rstrip('0').rstrip('.') if '.' in str(round(float(value), 1)) else round(float(value), 1)}%"
            for key, value in weight_entries
        )

    special_votes = meta.get("ensemble_special_votes") or {}
    selected_special_number = str(meta.get("selected_special_number") or "").strip()
    if special_votes:
        sorted_special_votes = sorted(
            special_votes.items(),
            key=lambda item: (float(item[1] or 0.0), int(item[0])),
            reverse=True,
        )
        top_special_vote_numbers = {str(num) for num, _ in sorted_special_votes[:5]}
        vote_entries = "、".join(
            f"{num}({str(round(float(votes), 2)).rstrip('0').rstrip('.')})"
            for num, votes in sorted_special_votes[:5]
        )
        if selected_special_number and selected_special_number not in top_special_vote_numbers:
            selected_special_vote = special_votes.get(selected_special_number)
            if selected_special_vote is None and selected_special_number.isdigit():
                selected_special_vote = special_votes.get(int(selected_special_number), 0.0)
            display["special_votes"] = (
                f"其它策略看好的号码：{vote_entries}；本次最终按综合结果选择，没有直接选票数最高的号码。"
            )
        else:
            display["special_votes"] = f"其它策略看好的号码：{vote_entries}"
    elif selected_special_number:
        display["special_votes"] = "其它策略暂无明显共识，本次按综合结果选择。"

    diagnostics = meta.get("ensemble_weight_diagnostics") or {}
    weight_reason_items = []
    has_recent_zero_fallback = False
    rank_titles = [
        ("最常参考", "这次参考力度最高"),
        ("次常参考", "这次参考力度第二"),
        ("辅助参考", "这次作为补充参考"),
    ]
    for idx, (key, value) in enumerate(sorted(
        diagnostics.items(),
        key=lambda item: float((item[1] or {}).get("weighted_score", 0.0) or 0.0),
        reverse=True,
    )):
        title, note = rank_titles[min(idx, 2)]
        recent_accuracy = float((value or {}).get("recent_accuracy", 0.0) or 0.0)
        overall_accuracy = float((value or {}).get("overall_accuracy", 0.0) or 0.0)
        overall_top6_accuracy = float((value or {}).get("overall_top6_accuracy", 0.0) or 0.0)
        window_accuracies = (value or {}).get("window_accuracies") or []
        fallback_reason = str((value or {}).get("fallback_reason") or "").strip()
        if fallback_reason == "recent_zero_fallback":
            has_recent_zero_fallback = True
        window_accuracy_text = " / ".join(
            f"近{int(item.get('window', 0))}期命中 {float(item.get('accuracy', 0.0) or 0.0)}%"
            for item in window_accuracies
            if int(item.get("total", 0) or 0) > 0
        )
        accuracy_text = (
            f"近期命中过 {recent_accuracy}%"
            if window_accuracy_text else "近期样本还不够"
        )
        if fallback_reason == "recent_zero_fallback":
            accuracy_text = (
                f"最近一码命中偏少，先参考长期表现："
                f"一码{overall_accuracy}% / 六码{overall_top6_accuracy}% / 综合参考{recent_accuracy}%"
            )
            window_accuracy_text = ""
        weight_value = float((meta.get("ensemble_strategy_weights") or {}).get(key, 0.0) or 0.0)
        weight_reason_items.append({
            "rank": idx + 1,
            "ribbon_title": title,
            "ribbon_note": note,
            "strategy_label": _get_strategy_label(key),
            "weight_text": f"参考占比 {round(weight_value, 1)}%",
            "accuracy_text": accuracy_text,
            "multiplier_text": (
                f"系统给它的参考分：{(value or {}).get('weighted_score', '-')}"
            ),
            "window_accuracy_text": window_accuracy_text,
        })
    display["weight_reason_summary"] = (
        "如果最近一码命中偏少，系统会多看长期表现和六码命中情况。"
        if has_recent_zero_fallback
        else "系统主要看近20期，再参考近50期和近100期。"
    )
    if weight_text:
        if has_recent_zero_fallback:
            line = f"各策略参考占比：{weight_text}（最近一码命中偏少，已加入长期表现参考）"
        else:
            line = f"各策略参考占比：{weight_text}（主要按近期表现分配）"
        if meta.get("ensemble_weight_confidence") is not None:
            line += f"；整体参考度 {meta.get('ensemble_weight_confidence')}%"
        display["weight_summary"] = line
    display["weight_reason_items"] = weight_reason_items
    return display


def _calculate_strategy_accuracy(region, strategy, limit=200, cutoff_period=None):
    cutoff_period = cutoff_period or _current_backtest_cutoff_period()
    predictions = _load_learning_scope_predictions(
        region,
        strategy,
        limit=limit,
        cutoff_period=cutoff_period,
    )
    if not predictions:
        return 0.0, 0

    correct = 0
    valid_total = 0
    for pred in predictions:
        actual = _normalize_draw_number(pred.actual_special_number)
        if not actual:
            continue
        valid_total += 1
        if _normalize_draw_number(pred.special_number) == actual:
            correct += 1
    return (correct / valid_total) if valid_total else 0.0, valid_total


def _calculate_strategy_hit_rates(region, strategy, limit=200, cutoff_period=None):
    cutoff_period = cutoff_period or _current_backtest_cutoff_period()
    predictions = _load_learning_scope_predictions(
        region,
        strategy,
        limit=limit,
        cutoff_period=cutoff_period,
    )
    if not predictions:
        return {"top1": 0.0, "top6": 0.0, "zodiac": 0.0, "total": 0}

    top1 = 0
    top6 = 0
    zodiac = 0
    valid_total = 0
    for pred in predictions:
        actual = _normalize_draw_number(pred.actual_special_number)
        if not actual:
            continue
        valid_total += 1
        predicted_special = _normalize_draw_number(pred.special_number)
        normal_numbers = {
            _normalize_draw_number(item)
            for item in str(getattr(pred, "normal_numbers", "") or "").split(",")
            if _normalize_draw_number(item)
        }
        predicted_zodiac = str(getattr(pred, "special_zodiac", "") or "").strip()
        actual_zodiac = str(getattr(pred, "actual_special_zodiac", "") or "").strip()
        if predicted_special == actual:
            top1 += 1
        if actual in normal_numbers:
            top6 += 1
        if predicted_zodiac and actual_zodiac and predicted_zodiac == actual_zodiac:
            zodiac += 1

    if valid_total <= 0:
        return {"top1": 0.0, "top6": 0.0, "zodiac": 0.0, "total": 0}
    return {
        "top1": round(top1 / valid_total, 4),
        "top6": round(top6 / valid_total, 4),
        "zodiac": round(zodiac / valid_total, 4),
        "total": valid_total,
    }


def _calculate_strategy_hit_rate_windows(region, strategy, windows=(12, 36, 72)):
    window_items = []
    weighted = {"top1": 0.0, "top6": 0.0, "zodiac": 0.0}
    total_weight = 0.0
    for idx, window in enumerate(windows or ()):
        stats = _calculate_strategy_hit_rates(region, strategy, limit=window)
        samples = int(stats.get("total", 0) or 0)
        base_weight = max(0.35, 1.0 - idx * 0.18)
        confidence = _clamp(samples / max(min(int(window), 24), 1), 0.18, 1.0)
        effective_weight = base_weight * confidence
        window_item = {
            "window": int(window),
            "top1": round(_safe_float(stats.get("top1"), 0.0), 4),
            "top6": round(_safe_float(stats.get("top6"), 0.0), 4),
            "zodiac": round(_safe_float(stats.get("zodiac"), 0.0), 4),
            "total": samples,
            "weight": round(effective_weight, 4),
        }
        window_items.append(window_item)
        total_weight += effective_weight
        for key in weighted.keys():
            weighted[key] += _safe_float(window_item.get(key), 0.0) * effective_weight

    if total_weight <= 0:
        aggregate = {"top1": 0.0, "top6": 0.0, "zodiac": 0.0, "total": 0}
    else:
        aggregate = {
            "top1": round(weighted["top1"] / total_weight, 4),
            "top6": round(weighted["top6"] / total_weight, 4),
            "zodiac": round(weighted["zodiac"] / total_weight, 4),
            "total": max((item["total"] for item in window_items), default=0),
        }
    return {
        "windows": window_items,
        "aggregate": aggregate,
    }


def _score_strategy_window_rates(summary):
    aggregate = dict((summary or {}).get("aggregate") or {})
    return round(
        _safe_float(aggregate.get("top1"), 0.0) * 100.0 +
        _safe_float(aggregate.get("top6"), 0.0) * 35.0 +
        _safe_float(aggregate.get("zodiac"), 0.0) * 15.0,
        4,
    )


def _build_config_rollback_snapshot(config):
    snapshot = copy.deepcopy(dict(config or {}))
    snapshot.pop("rollback_guard", None)
    snapshot.pop("auto_restore_guard", None)
    snapshot.pop("auto_rollback_history", None)
    snapshot.pop("auto_rollback_last_at", None)
    snapshot.pop("auto_rollback_last_reason", None)
    snapshot.pop("auto_restore_history", None)
    snapshot.pop("auto_restore_last_at", None)
    snapshot.pop("auto_restore_last_reason", None)
    return snapshot


def _load_backtest_draws_for_guard(region):
    try:
        return _load_backtest_draws_from_db(region, limit=AUTO_BACKTEST_LIMIT)
    except Exception:
        db.session.rollback()
        return []


def _maybe_rollback_strategy_config(strategy, region, config):
    guard = dict((config or {}).get("rollback_guard") or {})
    if not guard.get("active"):
        return {"rolled_back": False, "config": config}

    previous_config = guard.get("previous_config")
    if not isinstance(previous_config, dict) or not previous_config:
        return {"rolled_back": False, "config": config}

    stats = _calculate_strategy_hit_rate_windows(region, strategy, windows=(12, 36, 72))
    aggregate = dict(stats.get("aggregate") or {})
    total = int(aggregate.get("total") or 0)
    min_samples = int(guard.get("min_samples") or 8)
    if total < min_samples:
        guard["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
        guard["last_check_skipped"] = "insufficient_samples"
        config["rollback_guard"] = guard
        return {"rolled_back": False, "config": config}

    baseline_score = _safe_float(guard.get("baseline_score"), 0.0)
    current_score = _score_strategy_window_rates(stats)
    tolerance = _safe_float(guard.get("drop_tolerance"), 0.8)
    degraded = current_score < (baseline_score - tolerance)
    consecutive = int(guard.get("consecutive_degrade") or 0)
    consecutive = consecutive + 1 if degraded else 0
    patience = int(guard.get("patience") or 3)

    guard.update({
        "last_checked_at": datetime.now().isoformat(timespec="seconds"),
        "last_score": round(current_score, 4),
        "last_total": total,
        "last_degraded": bool(degraded),
        "consecutive_degrade": consecutive,
        "last_window_rates": stats,
    })

    if consecutive < patience:
        config["rollback_guard"] = guard
        return {"rolled_back": False, "config": config}

    restored = copy.deepcopy(previous_config)
    restored["last_accuracy"] = round(_safe_float(aggregate.get("top1"), 0.0), 4)
    restored["last_total"] = total
    restored["updated_at"] = datetime.now().isoformat()
    restored["rollback_guard"] = {
        "active": False,
        "rolled_back_from_score": round(current_score, 4),
        "baseline_score": round(baseline_score, 4),
        "rolled_back_at": datetime.now().isoformat(timespec="seconds"),
    }
    restored["auto_restore_guard"] = {
        "active": True,
        "candidate_config": _build_config_rollback_snapshot(config),
        "current_config": _build_config_rollback_snapshot(restored),
        "rolled_back_at": datetime.now().isoformat(timespec="seconds"),
        "restore_margin": round(float(guard.get("restore_margin") or 0.9), 4),
        "min_periods": int(guard.get("restore_min_periods") or 24),
        "cooldown_checks": int(guard.get("restore_cooldown_checks") or 1),
        "checks": 0,
        "last_checked_at": "",
    }
    history = list((config or {}).get("auto_rollback_history") or [])
    history.insert(0, {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "region": region,
        "strategy": strategy,
        "baseline_score": round(baseline_score, 4),
        "current_score": round(current_score, 4),
        "consecutive_degrade": consecutive,
        "patience": patience,
        "total": total,
    })
    restored["auto_rollback_history"] = history[:8]
    restored["auto_rollback_last_at"] = datetime.now().isoformat(timespec="seconds")
    restored["auto_rollback_last_reason"] = "performance_degraded"
    _save_strategy_config(strategy, region, restored)
    return {"rolled_back": True, "config": restored}


def _maybe_restore_rolled_back_strategy_config(strategy, region, config):
    guard = dict((config or {}).get("auto_restore_guard") or {})
    if not guard.get("active"):
        return {"restored": False, "config": config}

    candidate_config = guard.get("candidate_config")
    if not isinstance(candidate_config, dict) or not candidate_config:
        return {"restored": False, "config": config}

    checks = int(guard.get("checks") or 0) + 1
    cooldown_checks = int(guard.get("cooldown_checks") or 1)
    guard["checks"] = checks
    guard["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
    if checks <= cooldown_checks:
        config["auto_restore_guard"] = guard
        return {"restored": False, "config": config}

    draws = _load_backtest_draws_for_guard(region)
    if not draws:
        config["auto_restore_guard"] = guard
        return {"restored": False, "config": config}

    current_summary = _build_strategy_backtest_summary(region, strategy, draws=draws)
    candidate_summary = _build_strategy_backtest_summary(
        region,
        strategy,
        draws=draws,
        config_override=candidate_config,
    )
    min_periods = int(guard.get("min_periods") or 24)
    current_periods = int(current_summary.get("periods_evaluated") or current_summary.get("total") or 0)
    candidate_periods = int(candidate_summary.get("periods_evaluated") or candidate_summary.get("total") or 0)
    if min(current_periods, candidate_periods) < min_periods:
        guard["last_check_skipped"] = "insufficient_periods"
        config["auto_restore_guard"] = guard
        return {"restored": False, "config": config}

    current_score = _score_auto_optimize_summary(current_summary)
    candidate_score = _score_auto_optimize_summary(candidate_summary)
    restore_margin = _safe_float(guard.get("restore_margin"), 0.9)
    guard.update({
        "last_current_score": round(current_score, 4),
        "last_candidate_score": round(candidate_score, 4),
        "last_periods": min(current_periods, candidate_periods),
    })
    if candidate_score < current_score + restore_margin:
        config["auto_restore_guard"] = guard
        return {"restored": False, "config": config}

    restored = copy.deepcopy(candidate_config)
    restored["updated_at"] = datetime.now().isoformat()
    restored["auto_restore_guard"] = {
        "active": False,
        "restored_at": datetime.now().isoformat(timespec="seconds"),
        "current_score": round(current_score, 4),
        "candidate_score": round(candidate_score, 4),
    }
    restored["rollback_guard"] = {
        "active": True,
        "previous_config": _build_config_rollback_snapshot(config),
        "baseline_score": round(current_score, 4),
        "applied_score": round(candidate_score, 4),
        "applied_gain": round(candidate_score - current_score, 4),
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "source": "auto_restore",
        "patience": int(config.get("rollback_patience") or 3),
        "min_samples": int(config.get("rollback_min_samples") or 8),
        "drop_tolerance": round(float(config.get("rollback_drop_tolerance") or 0.8), 4),
        "consecutive_degrade": 0,
        "last_checked_at": "",
    }
    rollback_history = list((config or {}).get("auto_rollback_history") or [])
    if rollback_history:
        restored["auto_rollback_history"] = rollback_history[:8]
    restore_history = list((config or {}).get("auto_restore_history") or [])
    restore_history.insert(0, {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "region": region,
        "strategy": strategy,
        "current_score": round(current_score, 4),
        "candidate_score": round(candidate_score, 4),
        "gain": round(candidate_score - current_score, 4),
        "periods": min(current_periods, candidate_periods),
    })
    restored["auto_restore_history"] = restore_history[:8]
    restored["auto_restore_last_at"] = datetime.now().isoformat(timespec="seconds")
    restored["auto_restore_last_reason"] = "candidate_recovered"
    _save_strategy_config(strategy, region, restored)
    return {"restored": True, "config": restored}


def _load_region_draw_history(region, limit=None):
    query = LotteryDraw.query.filter_by(region=region).order_by(
        LotteryDraw.draw_date.desc(),
        LotteryDraw.draw_id.desc(),
    )
    if limit:
        query = query.limit(limit)
    return [record.to_dict() for record in query.all()]


def _get_phase_history_before_period(draws_desc, target_period, window=12):
    if not draws_desc or not target_period:
        return []
    normalized_target = _normalize_period_value(target_period)
    history = []
    for draw in draws_desc:
        draw_period = _normalize_period_value(draw.get("id"))
        if not draw_period:
            continue
        if _is_period_before(draw_period, normalized_target):
            history.append(draw)
        if len(history) >= max(4, int(window or 12)):
            break
    return history


def _calculate_strategy_phase_hit_rates(region, strategy, phases=("hot", "cold", "concentrated", "dispersed"), phase_window=12, limit=180, cutoff_period=None):
    predictions = _load_learning_scope_predictions(
        region,
        strategy,
        limit=limit,
        cutoff_period=cutoff_period or _current_backtest_cutoff_period(),
    )
    if not predictions:
        return {
            "aggregate": {"top1": 0.0, "top6": 0.0, "zodiac": 0.0, "total": 0, "score": 0.0},
            "phases": {},
        }

    draws_desc = _load_region_draw_history(region)
    phase_counters = {
        phase: {"top1": 0, "top6": 0, "zodiac": 0, "total": 0}
        for phase in phases
    }
    aggregate = {"top1": 0, "top6": 0, "zodiac": 0, "total": 0}

    for pred in predictions:
        actual = _normalize_draw_number(pred.actual_special_number)
        if not actual:
            continue
        history = _get_phase_history_before_period(draws_desc, getattr(pred, "period", ""), window=phase_window)
        phase_profile = _classify_ai_market_phase(history, window=max(4, min(len(history), phase_window))) if history else {"label": "neutral"}
        phase_label = str(phase_profile.get("label") or "neutral")
        if phase_label not in phase_counters:
            continue

        predicted_special = _normalize_draw_number(pred.special_number)
        normal_numbers = {
            _normalize_draw_number(item)
            for item in str(getattr(pred, "normal_numbers", "") or "").split(",")
            if _normalize_draw_number(item)
        }
        predicted_zodiac = str(getattr(pred, "special_zodiac", "") or "").strip()
        actual_zodiac = str(getattr(pred, "actual_special_zodiac", "") or "").strip()

        aggregate["total"] += 1
        phase_counters[phase_label]["total"] += 1
        if predicted_special == actual:
            aggregate["top1"] += 1
            phase_counters[phase_label]["top1"] += 1
        if actual in normal_numbers:
            aggregate["top6"] += 1
            phase_counters[phase_label]["top6"] += 1
        if predicted_zodiac and actual_zodiac and predicted_zodiac == actual_zodiac:
            aggregate["zodiac"] += 1
            phase_counters[phase_label]["zodiac"] += 1

    def _normalize_phase_counter(counter):
        total = int(counter.get("total", 0) or 0)
        if total <= 0:
            return {"top1": 0.0, "top6": 0.0, "zodiac": 0.0, "total": 0, "score": 0.0}
        top1 = round(counter["top1"] / total, 4)
        top6 = round(counter["top6"] / total, 4)
        zodiac = round(counter["zodiac"] / total, 4)
        score = round(top1 + (top6 * 0.55) + (zodiac * 0.15), 4)
        return {
            "top1": top1,
            "top6": top6,
            "zodiac": zodiac,
            "total": total,
            "score": score,
        }

    return {
        "aggregate": _normalize_phase_counter(aggregate),
        "phases": {
            phase: _normalize_phase_counter(counter)
            for phase, counter in phase_counters.items()
        },
    }


def _build_local_phase_learning_map(strategy, base_weights, phase_hit_rates):
    base = dict(base_weights or {})
    aggregate = dict((phase_hit_rates or {}).get("aggregate") or {})
    overall_score = _safe_float(aggregate.get("score"), 0.0)
    phase_stats = dict((phase_hit_rates or {}).get("phases") or {})
    learning_map = {}
    phase_biases = {
        "hot": {
            "hot": {"hot": 0.22, "trend": 0.08, "feedback": 0.08},
            "cold": {"hot": -0.14, "cold": 0.06, "overdue": 0.05},
            "concentrated": {"trend": 0.08, "color": 0.05, "parity": 0.04},
            "dispersed": {"normal": 0.06, "cold": 0.04, "feedback": 0.04},
        },
        "cold": {
            "hot": {"overdue": 0.04, "cold": -0.08, "feedback": -0.04},
            "cold": {"cold": 0.2, "overdue": 0.12, "feedback": 0.04},
            "concentrated": {"color": 0.04, "parity": 0.04, "cold": 0.06},
            "dispersed": {"cold": 0.1, "normal": 0.05, "overdue": 0.06},
        },
        "trend": {
            "hot": {"trend": 0.16, "hot": 0.06, "feedback": 0.05},
            "cold": {"trend": -0.06, "cold": 0.05, "overdue": 0.04},
            "concentrated": {"trend": 0.14, "color": 0.05, "zodiac": 0.05},
            "dispersed": {"trend": 0.05, "normal": 0.05, "feedback": 0.04},
        },
        "balanced": {
            "hot": {"hot": 0.04, "trend": 0.05, "parity": 0.05},
            "cold": {"cold": 0.05, "overdue": 0.04, "parity": 0.05},
            "concentrated": {"color": 0.08, "zodiac": 0.08, "parity": 0.06},
            "dispersed": {"normal": 0.08, "cold": 0.04, "trend": 0.04},
        },
        "hybrid": {
            "hot": {"hot": 0.12, "trend": 0.07, "feedback": 0.07},
            "cold": {"cold": 0.1, "overdue": 0.06, "feedback": 0.05},
            "concentrated": {"trend": 0.08, "color": 0.06, "zodiac": 0.06},
            "dispersed": {"normal": 0.06, "cold": 0.05, "trend": 0.05},
        },
    }
    profile_biases = {
        "hot": {"special_focus_multiplier": 0.08, "feedback_multiplier": 0.07},
        "cold": {"cold_multiplier": 0.08, "overheat_multiplier": -0.06},
        "concentrated": {"attribute_multiplier": 0.08, "trend_multiplier": 0.05},
        "dispersed": {"feedback_multiplier": 0.05, "special_focus_multiplier": 0.04},
    }

    for phase, stats in phase_stats.items():
        samples = int(stats.get("total", 0) or 0)
        if samples < 6:
            tier = "low"
            gate_multiplier = 0.35
        elif samples < 12:
            tier = "medium"
            gate_multiplier = 0.65
        else:
            tier = "high"
            gate_multiplier = 1.0
        confidence = _clamp(samples / 8.0, 0.0, 1.0)
        phase_score = _safe_float(stats.get("score"), 0.0)
        score_delta = phase_score - overall_score
        drift = _clamp(score_delta * 1.8, -0.22, 0.28)
        strength = confidence * gate_multiplier * (0.65 + max(-0.25, drift))
        learned_weights = dict(base)
        for key, bias in (phase_biases.get(strategy, {}).get(phase) or {}).items():
            base_value = _safe_float(base.get(key), 0.0)
            if base_value <= 0:
                continue
            learned_weights[key] = round(_clamp(base_value + (bias * strength), 0.0, max(base_value + 0.5, 1.6)), 4)
        learning_map[phase] = {
            "weights": learned_weights,
            "samples": samples,
            "confidence": round(confidence, 4),
            "sample_tier": tier,
            "gate_multiplier": round(gate_multiplier, 4),
            "score": round(phase_score, 4),
            "score_delta": round(score_delta, 4),
            "profile_adjustments": {
                key: round(value * strength, 4)
                for key, value in profile_biases.get(phase, {}).items()
            },
        }
    return learning_map


def _build_local_phase_runtime_templates(strategy, config, phase_hit_rates):
    base_window = int(config.get("window") or 0)
    base_pool = int(config.get("pool") or 16)
    base_special_pool = int(config.get("special_pool") or max(8, base_pool // 2))
    base_trend_window = int(config.get("trend_window") or min(base_window or 15, 15))
    base_bucket_counts = list(config.get("bucket_counts") or [2, 2, 2])[:3]
    base_mix = dict(config.get("mix") or {"hot": 2, "cold": 2, "trend": 2})
    templates = {}
    phase_stats = dict((phase_hit_rates or {}).get("phases") or {})

    for phase, stats in phase_stats.items():
        samples = int(stats.get("total", 0) or 0)
        if samples < 6:
            gate_multiplier = 0.35
        elif samples < 12:
            gate_multiplier = 0.65
        else:
            gate_multiplier = 1.0
        phase_score = _safe_float(stats.get("score"), 0.0)
        score_lift = _clamp((phase_score - 0.3) * gate_multiplier, -0.12, 0.18)
        window = base_window
        pool = base_pool
        special_pool = base_special_pool
        trend_window = base_trend_window
        bucket_counts = list(base_bucket_counts)
        mix = dict(base_mix)

        if strategy == "hot":
            if phase == "hot":
                window = _clamp(int(base_window - 8 + score_lift * 20), 16, 72)
                pool = _clamp(int(base_pool - 2 + score_lift * 6), 8, 22)
                special_pool = _clamp(int(base_special_pool - 2 + score_lift * 4), 6, 12)
            elif phase == "cold":
                window = _clamp(int(base_window + 6), 18, 84)
                pool = _clamp(int(base_pool + 1), 9, 24)
            elif phase == "dispersed":
                pool = _clamp(int(base_pool + 2), 10, 24)
        elif strategy == "cold":
            if phase == "cold":
                window = _clamp(int(base_window + 10 + score_lift * 16), 20, 92)
                pool = _clamp(int(base_pool + 2 + score_lift * 4), 10, 24)
                special_pool = _clamp(int(base_special_pool + 1), 6, 14)
            elif phase == "hot":
                special_pool = _clamp(int(base_special_pool - 1), 6, 12)
            elif phase == "dispersed":
                pool = _clamp(int(base_pool + 3), 10, 24)
        elif strategy == "trend":
            if phase == "concentrated":
                trend_window = _clamp(int(base_trend_window - 4 + score_lift * 10), 6, 22)
                pool = _clamp(int(base_pool - 1 + score_lift * 4), 8, 20)
            elif phase == "hot":
                trend_window = _clamp(int(base_trend_window - 2), 6, 24)
            elif phase == "dispersed":
                trend_window = _clamp(int(base_trend_window + 2), 8, 28)
                pool = _clamp(int(base_pool + 1), 8, 22)
        elif strategy == "balanced":
            if phase == "concentrated":
                bucket_counts = [1, 3, 2]
            elif phase == "dispersed":
                bucket_counts = [2, 2, 2]
                pool = _clamp(int(base_pool + 2), 10, 24)
            elif phase == "hot":
                bucket_counts = [2, 3, 1]
            elif phase == "cold":
                bucket_counts = [2, 2, 2]
                special_pool = _clamp(int(base_special_pool + 1), 6, 14)
        elif strategy == "hybrid":
            if phase == "hot":
                mix = {"hot": 3, "cold": 1, "trend": 2}
            elif phase == "cold":
                mix = {"hot": 1, "cold": 3, "trend": 2}
            elif phase == "concentrated":
                mix = {"hot": 1, "cold": 2, "trend": 3}
                trend_window = _clamp(int(base_trend_window - 3), 6, 22)
            elif phase == "dispersed":
                mix = {"hot": 2, "cold": 3, "trend": 1}
                pool = _clamp(int(base_pool + 2), 10, 24)

        templates[phase] = {
            "window": int(window),
            "pool": int(pool),
            "special_pool": int(special_pool),
            "trend_window": int(trend_window),
            "bucket_counts": bucket_counts,
            "mix": mix,
            "samples": samples,
            "gate_multiplier": round(gate_multiplier, 4),
            "score": round(phase_score, 4),
        }
    return templates


def _score_local_strategy_phase_strength(config, phase_label):
    if not config or not phase_label:
        return 0.0, 0
    phase_stats = dict((config.get("phase_hit_rates") or {}).get("phases") or {}).get(phase_label) or {}
    aggregate = dict((config.get("phase_hit_rates") or {}).get("aggregate") or {})
    samples = int(phase_stats.get("total", 0) or 0)
    if samples <= 0:
        return round(_safe_float(aggregate.get("score"), 0.0) * 0.55, 4), 0
    if samples < 6:
        gate_multiplier = 0.35
    elif samples < 12:
        gate_multiplier = 0.65
    else:
        gate_multiplier = 1.0
    phase_score = _safe_float(phase_stats.get("score"), 0.0)
    overall_score = _safe_float(aggregate.get("score"), 0.0)
    blended = (phase_score * gate_multiplier) + (overall_score * (1.0 - gate_multiplier) * 0.75)
    return round(blended, 4), samples


def _calculate_strategy_window_stats(region, strategy, limit=50):
    accuracy, total = _calculate_strategy_accuracy(region, strategy, limit=limit)
    return {
        "strategy": strategy,
        "label": _get_strategy_label(strategy),
        "window": limit,
        "total": total,
        "accuracy": round(accuracy * 100, 1),
    }


def _score_prediction_outcome(prediction):
    actual_special = _normalize_draw_number(
        getattr(prediction, "actual_special_number", "")
    )
    special_number = _normalize_draw_number(
        getattr(prediction, "special_number", "")
    )
    normal_numbers = {
        _normalize_draw_number(item)
        for item in str(getattr(prediction, "normal_numbers", "") or "").split(",")
        if _normalize_draw_number(item)
    }
    actual_zodiac = str(getattr(prediction, "actual_special_zodiac", "") or "").strip()
    special_zodiac = str(getattr(prediction, "special_zodiac", "") or "").strip()

    if actual_special and actual_special == special_number:
        return 1.0

    score = 0.0
    if actual_special and actual_special in normal_numbers:
        score += 0.58
    if actual_zodiac and special_zodiac and actual_zodiac == special_zodiac:
        score += 0.26
    return round(min(score, 0.9), 4)


def _rank_weighted_preferences(counter_map, limit=3):
    if not counter_map:
        return []
    return [
        key
        for key, _ in sorted(
            counter_map.items(),
            key=lambda item: (item[1], str(item[0])),
            reverse=True,
        )[:limit]
    ]


def _recent_learning_weight(index, limit, floor=0.12, recent_window=24, recent_boost=0.32):
    limit = max(int(limit or 1), 1)
    base = max(float(floor), 1.0 - (int(index or 0) / limit) * (1.0 - float(floor)))
    if int(index or 0) < int(recent_window or 0):
        base *= 1.0 + float(recent_boost)
    return base


def _learning_adaptation_profiles():
    return {
        "conservative": {
            "recency_floor": 0.22,
            "recent_window": 20,
            "recent_boost": 0.18,
            "quality_sample_divisor": 10.0,
            "confidence_sample_divisor": 24.0,
            "confidence_floor": 0.2,
            "quality_floor": 0.35,
            "ml_promote_threshold": 0.42,
            "ml_watch_threshold": 0.3,
            "markov_promote_threshold": 0.4,
            "markov_watch_threshold": 0.28,
            "markov_cooldown_cap": 12.0,
            "markov_min_gain_cap": 0.45,
            "markov_promote_blend": 0.62,
            "markov_watch_blend": 0.35,
            "markov_tune_blend_factor": 0.38,
            "markov_tune_blend_min": 0.1,
            "markov_tune_blend_max": 0.38,
            "markov_weight_blend_factor": 0.32,
            "markov_weight_blend_min": 0.1,
            "markov_weight_blend_max": 0.32,
            "ml_feature_bonus": 0.075,
            "ml_feature_bonus_decay": 0.025,
            "ml_runtime_bonus": 0.055,
            "ml_runtime_bonus_decay": 0.018,
        },
        "balanced": {
            "recency_floor": 0.14,
            "recent_window": 28,
            "recent_boost": 0.38,
            "quality_sample_divisor": 8.0,
            "confidence_sample_divisor": 18.0,
            "confidence_floor": 0.28,
            "quality_floor": 0.4,
            "ml_promote_threshold": 0.34,
            "ml_watch_threshold": 0.22,
            "markov_promote_threshold": 0.32,
            "markov_watch_threshold": 0.2,
            "markov_cooldown_cap": 8.0,
            "markov_min_gain_cap": 0.35,
            "markov_promote_blend": 0.78,
            "markov_watch_blend": 0.55,
            "markov_tune_blend_factor": 0.55,
            "markov_tune_blend_min": 0.16,
            "markov_tune_blend_max": 0.55,
            "markov_weight_blend_factor": 0.45,
            "markov_weight_blend_min": 0.14,
            "markov_weight_blend_max": 0.45,
            "ml_feature_bonus": 0.11,
            "ml_feature_bonus_decay": 0.035,
            "ml_runtime_bonus": 0.085,
            "ml_runtime_bonus_decay": 0.028,
        },
        "responsive": {
            "recency_floor": 0.1,
            "recent_window": 36,
            "recent_boost": 0.62,
            "quality_sample_divisor": 6.5,
            "confidence_sample_divisor": 14.0,
            "confidence_floor": 0.34,
            "quality_floor": 0.45,
            "ml_promote_threshold": 0.28,
            "ml_watch_threshold": 0.16,
            "markov_promote_threshold": 0.26,
            "markov_watch_threshold": 0.15,
            "markov_cooldown_cap": 4.0,
            "markov_min_gain_cap": 0.25,
            "markov_promote_blend": 0.88,
            "markov_watch_blend": 0.68,
            "markov_tune_blend_factor": 0.68,
            "markov_tune_blend_min": 0.22,
            "markov_tune_blend_max": 0.68,
            "markov_weight_blend_factor": 0.58,
            "markov_weight_blend_min": 0.18,
            "markov_weight_blend_max": 0.58,
            "ml_feature_bonus": 0.15,
            "ml_feature_bonus_decay": 0.045,
            "ml_runtime_bonus": 0.12,
            "ml_runtime_bonus_decay": 0.036,
        },
    }


def _resolve_learning_adaptation(region, strategy=None):
    profiles = _learning_adaptation_profiles()
    mode = "balanced"
    payload = _load_latest_auto_backtest_payload(region)
    ranking = list(payload.get("ranking") or [])
    targets = [strategy] if strategy in {"ml", "markov"} else ["ml", "markov"]
    entries = [item for item in ranking if item.get("strategy") in targets]

    if entries:
        recent_scores = []
        overall_scores = []
        sample_counts = []
        for entry in entries:
            windows = list(entry.get("windows") or [])
            recent_window = windows[0] if windows else {}
            recent_total = int(recent_window.get("total") or 0)
            sample_counts.append(recent_total)
            recent_score = (
                _safe_float(recent_window.get("top1_hit_rate"), 0.0) +
                _safe_float(recent_window.get("top6_hit_rate"), 0.0) * 0.35 +
                _safe_float(recent_window.get("zodiac_hit_rate"), 0.0) * 0.15
            )
            overall_score = (
                _safe_float(entry.get("top1_hit_rate"), 0.0) +
                _safe_float(entry.get("top6_hit_rate"), 0.0) * 0.35 +
                _safe_float(entry.get("zodiac_hit_rate"), 0.0) * 0.15
            )
            recent_scores.append(recent_score)
            overall_scores.append(overall_score)

        avg_recent = sum(recent_scores) / len(recent_scores)
        avg_overall = sum(overall_scores) / len(overall_scores)
        recent_gap = avg_recent - avg_overall
        weakest_recent = min(recent_scores)
        enough_recent = max(sample_counts or [0]) >= 12

        if not enough_recent or weakest_recent <= 8.0 or recent_gap <= -2.2:
            mode = "responsive"
        elif recent_gap >= 2.4 and weakest_recent >= 13.0:
            mode = "conservative"

    profile = dict(profiles.get(mode, profiles["balanced"]))
    profile["mode"] = mode
    return profile


def _learn_ml_region_profile(region, limit=180):
    query = PredictionRecord.query.filter_by(
        region=region,
        strategy="ml",
        is_result_updated=True,
    ).filter(PredictionRecord.actual_special_number != None)
    predictions = query.order_by(PredictionRecord.created_at.desc()).limit(limit).all()

    feature_scores = Counter()
    runtime_scores = Counter()
    blend_scores = Counter()
    total_quality = 0.0
    adaptation = _resolve_learning_adaptation(region, "ml")

    for idx, prediction in enumerate(predictions):
        metadata = _deserialize_prediction_metadata(
            getattr(prediction, "prediction_metadata", "")
        )
        if not metadata:
            continue

        recency_weight = _recent_learning_weight(
            idx,
            limit,
            floor=adaptation["recency_floor"],
            recent_window=adaptation["recent_window"],
            recent_boost=adaptation["recent_boost"],
        )
        quality = max(0.05, _score_prediction_outcome(prediction)) * recency_weight
        total_quality += quality

        feature_profile = str(metadata.get("feature_profile") or "").strip()
        if feature_profile:
            feature_scores[feature_profile] += quality

        runtime_profile = str(metadata.get("runtime_profile") or "").strip()
        if runtime_profile:
            runtime_scores[runtime_profile] += quality

        try:
            blend_value = round(float(metadata.get("selected_blend", 0.0)) / 100.0, 2)
        except (TypeError, ValueError):
            blend_value = 0.0
        if 0.0 < blend_value <= 1.0:
            blend_scores[blend_value] += quality

    ensemble_raw = {}
    for strategy in [item for item in LOCAL_STRATEGY_KEYS if item != "ml"]:
        weighted_scores = []
        for idx, window in enumerate((20, 50, 100)):
            accuracy, total = _calculate_strategy_accuracy(region, strategy, limit=window)
            if total <= 0:
                continue
            recency_weight = max(0.45, 1.0 - idx * 0.18)
            confidence = _clamp(total / 10.0, 0.25, 1.0)
            weighted_scores.append(accuracy * recency_weight * confidence)
        ensemble_raw[strategy] = max(
            0.08,
            (sum(weighted_scores) / len(weighted_scores)) if weighted_scores else 0.18,
        )

    ensemble_total = sum(ensemble_raw.values()) or 1.0
    ensemble_bias = {
        key: round(value / ensemble_total, 4)
        for key, value in ensemble_raw.items()
    }

    confidence = _clamp(
        len(predictions) / adaptation["confidence_sample_divisor"],
        adaptation["confidence_floor"],
        1.0,
    ) * _clamp(
        total_quality / adaptation["quality_sample_divisor"],
        adaptation["quality_floor"],
        1.0,
    )
    learned_blends = _rank_weighted_preferences(blend_scores, limit=3)
    if not learned_blends:
        learned_blends = [0.55, 0.7, 0.82]

    return {
        "feature_profiles": _rank_weighted_preferences(feature_scores, limit=3),
        "runtime_profiles": _rank_weighted_preferences(runtime_scores, limit=3),
        "blend_candidates": [round(float(item), 2) for item in learned_blends[:3]],
        "ensemble_bias": ensemble_bias,
        "confidence": round(confidence, 4),
        "samples": len(predictions),
        "adaptation_mode": adaptation["mode"],
    }


def _load_latest_auto_backtest_payload(region):
    record = (
        BacktestRun.query.filter(
            BacktestRun.region == region,
            BacktestRun.name.like(f"auto-{region}-%"),
        )
        .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
        .first()
    )
    if not record or not record.payload:
        return {}
    try:
        payload = json.loads(record.payload)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _promote_ml_region_profile(region, persist=True):
    config = _load_strategy_config("ml", region)
    previous_primary_feature = str(config.get("primary_feature_profile") or "full").strip() or "full"
    previous_primary_runtime = str(config.get("primary_runtime_profile") or "base").strip() or "base"
    previous_promotion_strength = str(config.get("promotion_strength") or "hold").strip() or "hold"
    learned_profile = _learn_ml_region_profile(region)
    backtest_payload = _load_latest_auto_backtest_payload(region)
    ranking = backtest_payload.get("ranking") or []
    ml_rank = next((idx + 1 for idx, item in enumerate(ranking) if item.get("strategy") == "ml"), 0)
    ml_entry = next((item for item in ranking if item.get("strategy") == "ml"), {})
    leader_entry = ranking[0] if ranking else {}

    ml_top1 = float(ml_entry.get("top1_hit_rate") or 0.0)
    leader_top1 = float(leader_entry.get("top1_hit_rate") or 0.0)
    ml_top6 = float(ml_entry.get("top6_hit_rate") or 0.0)
    leader_top6 = float(leader_entry.get("top6_hit_rate") or 0.0)
    close_to_leader = (
        ml_rank == 1 or (
            ranking and
            (leader_top1 - ml_top1) <= 1.5 and
            (leader_top6 - ml_top6) <= 2.5
        )
    )

    feature_profiles = learned_profile.get("feature_profiles") or []
    runtime_profiles = learned_profile.get("runtime_profiles") or []
    blend_candidates = [
        round(float(item), 2)
        for item in (learned_profile.get("blend_candidates") or [])
        if 0.0 < float(item) <= 1.0
    ]
    learning_confidence = float(learned_profile.get("confidence") or 0.0)
    adaptation = _resolve_learning_adaptation(region, "ml")

    promotion_strength = "hold"
    if close_to_leader and learning_confidence >= adaptation["ml_promote_threshold"]:
        promotion_strength = "promoted"
    elif learning_confidence >= adaptation["ml_watch_threshold"]:
        promotion_strength = "watch"

    if feature_profiles and promotion_strength in ("promoted", "watch"):
        config["primary_feature_profile"] = feature_profiles[0]
    if runtime_profiles and promotion_strength in ("promoted", "watch"):
        config["primary_runtime_profile"] = runtime_profiles[0]

    default_blends = [0.55, 0.7, 0.82]
    merged_blends = []
    for candidate in blend_candidates + default_blends:
        rounded = round(float(candidate), 2)
        if rounded not in merged_blends:
            merged_blends.append(rounded)
        if len(merged_blends) >= 4:
            break
    if merged_blends:
        config["blend_candidates"] = merged_blends

    config["preferred_feature_profiles"] = feature_profiles
    config["preferred_runtime_profiles"] = runtime_profiles
    config["profile_learning_confidence"] = round(learning_confidence, 4)
    config["profile_learning_samples"] = int(learned_profile.get("samples") or 0)
    config["ensemble_bias"] = learned_profile.get("ensemble_bias", {})
    config["learning_adaptation_mode"] = adaptation["mode"]
    config["promotion_strength"] = promotion_strength
    config["promotion_backtest_rank"] = ml_rank
    config["promotion_backtest_top1"] = round(ml_top1, 2)
    config["promotion_backtest_top6"] = round(ml_top6, 2)
    config["promoted_at"] = datetime.now().isoformat()

    promotion_history = list(config.get("promotion_history") or [])
    current_primary_feature = str(config.get("primary_feature_profile") or "full").strip() or "full"
    current_primary_runtime = str(config.get("primary_runtime_profile") or "base").strip() or "base"
    change_detected = (
        current_primary_feature != previous_primary_feature or
        current_primary_runtime != previous_primary_runtime or
        promotion_strength != previous_promotion_strength
    )
    if change_detected:
        promotion_history.insert(0, {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "region": region,
            "strength": promotion_strength,
            "previous_feature_profile": previous_primary_feature,
            "previous_runtime_profile": previous_primary_runtime,
            "feature_profile": current_primary_feature,
            "runtime_profile": current_primary_runtime,
            "backtest_rank": ml_rank,
            "top1_hit_rate": round(ml_top1, 2),
            "top6_hit_rate": round(ml_top6, 2),
            "learning_confidence": round(learning_confidence * 100, 2),
            "adaptation_mode": adaptation["mode"],
        })
    config["promotion_history"] = promotion_history[:8]

    if persist:
        _save_strategy_config("ml", region, config)
    return config


def _learn_markov_region_profile(region, limit=180):
    query = PredictionRecord.query.filter_by(
        region=region,
        strategy="markov",
        is_result_updated=True,
    ).filter(PredictionRecord.actual_special_number != None)
    predictions = query.order_by(PredictionRecord.created_at.desc()).limit(limit).all()

    window_scores = Counter()
    pool_scores = Counter()
    special_pool_scores = Counter()
    decay_scores = Counter()
    source_weight_scores = Counter()
    transition_weight_scores = Counter()
    transition_lift_weight_scores = Counter()
    special_transition_weight_scores = Counter()
    special_transition_lift_weight_scores = Counter()
    second_order_weight_scores = Counter()
    phase_transition_weight_scores = Counter()
    attribute_transition_weight_scores = Counter()
    failure_weight_scores = Counter()
    total_quality = 0.0
    signal_count = 0
    adaptation = _resolve_learning_adaptation(region, "markov")

    for idx, prediction in enumerate(predictions):
        metadata = _deserialize_prediction_metadata(
            getattr(prediction, "prediction_metadata", "")
        )
        if metadata.get("markov_window") is not None:
            signal_count += 1
        recency_weight = _recent_learning_weight(
            idx,
            limit,
            floor=adaptation["recency_floor"],
            recent_window=adaptation["recent_window"],
            recent_boost=adaptation["recent_boost"],
        )
        quality = max(0.05, _score_prediction_outcome(prediction)) * recency_weight
        total_quality += quality

        def add_numeric(counter, value, precision=0, scale=1.0):
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return
            if numeric <= 0:
                return
            key = round(numeric, int(precision))
            counter[key] += quality * scale

        add_numeric(window_scores, metadata.get("markov_window"), 0)
        add_numeric(pool_scores, metadata.get("markov_pool"), 0)
        add_numeric(special_pool_scores, metadata.get("markov_special_pool"), 0)
        add_numeric(decay_scores, metadata.get("markov_transition_decay"), 4)
        add_numeric(source_weight_scores, metadata.get("markov_source_special_weight"), 3)

        weights = metadata.get("markov_weights") or {}
        if isinstance(weights, dict):
            add_numeric(transition_weight_scores, weights.get("transition"), 2)
            add_numeric(transition_lift_weight_scores, weights.get("transition_lift"), 2)
            add_numeric(special_transition_weight_scores, weights.get("special_transition"), 2)
            add_numeric(special_transition_lift_weight_scores, weights.get("special_transition_lift"), 2)
            add_numeric(second_order_weight_scores, weights.get("second_order"), 2)
            add_numeric(phase_transition_weight_scores, weights.get("phase_transition"), 2)
            add_numeric(attribute_transition_weight_scores, weights.get("attribute_transition"), 2)
            add_numeric(failure_weight_scores, weights.get("failure"), 2)

    confidence = _clamp(
        signal_count / adaptation["confidence_sample_divisor"],
        0.0,
        1.0,
    ) * _clamp(
        total_quality / adaptation["quality_sample_divisor"],
        adaptation["quality_floor"],
        1.0,
    )

    def best_numeric(counter, default):
        ranked = _rank_weighted_preferences(counter, limit=1)
        if not ranked:
            return default
        return ranked[0]

    preferred = {} if signal_count <= 0 else {
        "window": int(best_numeric(window_scores, 80)),
        "pool": int(best_numeric(pool_scores, 18)),
        "special_pool": int(best_numeric(special_pool_scores, 10)),
        "transition_decay": float(best_numeric(decay_scores, 0.985)),
        "source_special_weight": float(best_numeric(source_weight_scores, 1.28)),
        "transition_weight": float(best_numeric(transition_weight_scores, 1.35)),
        "transition_lift_weight": float(best_numeric(transition_lift_weight_scores, 0.32)),
        "special_transition_weight": float(best_numeric(special_transition_weight_scores, 1.10)),
        "special_transition_lift_weight": float(best_numeric(special_transition_lift_weight_scores, 0.28)),
        "second_order_weight": float(best_numeric(second_order_weight_scores, 0.72)),
        "phase_transition_weight": float(best_numeric(phase_transition_weight_scores, 0.55)),
        "attribute_transition_weight": float(best_numeric(attribute_transition_weight_scores, 0.42)),
        "failure_weight": float(best_numeric(failure_weight_scores, 0.48)),
    }
    return {
        "preferred_config": preferred,
        "confidence": round(confidence, 4),
        "samples": signal_count,
        "adaptation_mode": adaptation["mode"],
    }


def _promote_markov_region_profile(region, persist=True):
    config = _load_strategy_config("markov", region)
    adaptation = _resolve_learning_adaptation(region, "markov")
    cooldown_hours = min(_safe_float(config.get("promotion_cooldown_hours"), 6.0), adaptation["markov_cooldown_cap"])
    last_promoted_at = str(config.get("promoted_at") or "").strip()
    previous_promotion_strength = str(config.get("promotion_strength") or "").strip()
    if previous_promotion_strength == "promoted" and last_promoted_at and cooldown_hours > 0:
        try:
            last_time = datetime.fromisoformat(last_promoted_at)
            if datetime.now() - last_time < timedelta(hours=cooldown_hours):
                config["promotion_skipped_reason"] = "cooldown"
                config["promotion_next_allowed_at"] = (last_time + timedelta(hours=cooldown_hours)).isoformat(timespec="seconds")
                if persist:
                    _save_strategy_config("markov", region, config)
                return config
        except Exception:
            pass
    previous = {
        "window": config.get("window"),
        "pool": config.get("pool"),
        "special_pool": config.get("special_pool"),
        "transition_decay": config.get("transition_decay"),
        "source_special_weight": config.get("source_special_weight"),
    }
    learned_profile = _learn_markov_region_profile(region)
    preferred = dict(learned_profile.get("preferred_config") or {})
    learning_confidence = float(learned_profile.get("confidence") or 0.0)

    backtest_payload = _load_latest_auto_backtest_payload(region)
    ranking = backtest_payload.get("ranking") or []
    markov_rank = next((idx + 1 for idx, item in enumerate(ranking) if item.get("strategy") == "markov"), 0)
    markov_entry = next((item for item in ranking if item.get("strategy") == "markov"), {})
    leader_entry = ranking[0] if ranking else {}
    markov_top1 = float(markov_entry.get("top1_hit_rate") or 0.0)
    markov_top6 = float(markov_entry.get("top6_hit_rate") or 0.0)
    leader_top1 = float(leader_entry.get("top1_hit_rate") or 0.0)
    leader_top6 = float(leader_entry.get("top6_hit_rate") or 0.0)
    close_to_leader = (
        markov_rank == 1 or (
            ranking and
            (leader_top1 - markov_top1) <= 1.8 and
            (leader_top6 - markov_top6) <= 3.0
        )
    )
    previous_score = _safe_float(config.get("promotion_backtest_top1"), 0.0) + (_safe_float(config.get("promotion_backtest_top6"), 0.0) * 0.35)
    current_score = markov_top1 + (markov_top6 * 0.35)
    min_gain = min(_safe_float(config.get("promotion_min_gain"), 0.25), adaptation["markov_min_gain_cap"])
    ranking_size = len(ranking)
    bottom_rank = bool(markov_rank and ranking_size and markov_rank >= max(5, ranking_size - 1))
    weak_backtest = bool(markov_entry) and (
        markov_top1 <= 0.0 or
        current_score < 1.0 or
        bottom_rank
    )

    promotion_strength = "hold"
    if (
        not weak_backtest and
        close_to_leader and
        learning_confidence >= adaptation["markov_promote_threshold"] and
        (previous_score <= 0 or current_score >= previous_score + min_gain)
    ):
        promotion_strength = "promoted"
    elif not weak_backtest and learning_confidence >= adaptation["markov_watch_threshold"]:
        promotion_strength = "watch"
    elif weak_backtest:
        config["promotion_skipped_reason"] = "weak_backtest"
    else:
        config.pop("promotion_skipped_reason", None)

    if preferred and promotion_strength in ("promoted", "watch"):
        blend = adaptation["markov_promote_blend"] if promotion_strength == "promoted" else adaptation["markov_watch_blend"]

        def blended_int(key, low, high):
            base = int(config.get(key) or _default_strategy_config("markov").get(key) or low)
            target = int(preferred.get(key) or base)
            config[key] = _clamp(int(round(base * (1.0 - blend) + target * blend)), low, high)

        def blended_float(key, low, high, precision):
            base = float(config.get(key) or _default_strategy_config("markov").get(key) or low)
            target = float(preferred.get(key) or base)
            config[key] = round(_clamp(base * (1.0 - blend) + target * blend, low, high), precision)

        blended_int("window", 24, 160)
        blended_int("pool", 8, 24)
        blended_int("special_pool", 6, 14)
        blended_float("transition_decay", 0.965, 0.995, 4)
        blended_float("source_special_weight", 1.0, 1.7, 3)

        weights = dict(config.get("weights") or {})
        weights["transition"] = round(_clamp(
            float(weights.get("transition") or 1.35) * (1.0 - blend) + float(preferred.get("transition_weight") or 1.35) * blend,
            0.75,
            1.85,
        ), 2)
        weights["special_transition"] = round(_clamp(
            float(weights.get("special_transition") or 1.1) * (1.0 - blend) + float(preferred.get("special_transition_weight") or 1.1) * blend,
            0.65,
            1.65,
        ), 2)
        weights["second_order"] = round(_clamp(
            float(weights.get("second_order") or 0.72) * (1.0 - blend) + float(preferred.get("second_order_weight") or 0.72) * blend,
            0.0,
            1.35,
        ), 2)
        weights["phase_transition"] = round(_clamp(
            float(weights.get("phase_transition") or 0.55) * (1.0 - blend) + float(preferred.get("phase_transition_weight") or 0.55) * blend,
            0.0,
            1.25,
        ), 2)
        weights["attribute_transition"] = round(_clamp(
            float(weights.get("attribute_transition") or 0.42) * (1.0 - blend) + float(preferred.get("attribute_transition_weight") or 0.42) * blend,
            0.0,
            1.1,
        ), 2)
        weights["failure"] = round(_clamp(
            float(weights.get("failure") or 0.48) * (1.0 - blend) + float(preferred.get("failure_weight") or 0.48) * blend,
            0.0,
            1.2,
        ), 2)
        config["weights"] = weights

    config["preferred_markov_config"] = preferred
    config["profile_learning_confidence"] = round(learning_confidence, 4)
    config["profile_learning_samples"] = int(learned_profile.get("samples") or 0)
    config["learning_adaptation_mode"] = adaptation["mode"]
    config["promotion_strength"] = promotion_strength
    config["promotion_backtest_rank"] = markov_rank
    config["promotion_backtest_top1"] = round(markov_top1, 2)
    config["promotion_backtest_top6"] = round(markov_top6, 2)
    if promotion_strength in ("promoted", "watch"):
        config["promoted_at"] = datetime.now().isoformat()

    current = {
        "window": config.get("window"),
        "pool": config.get("pool"),
        "special_pool": config.get("special_pool"),
        "transition_decay": config.get("transition_decay"),
        "source_special_weight": config.get("source_special_weight"),
    }
    if current != previous or promotion_strength != str(config.get("previous_promotion_strength") or ""):
        history = list(config.get("promotion_history") or [])
        history.insert(0, {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "region": region,
            "strength": promotion_strength,
            "previous": previous,
            "current": current,
            "backtest_rank": markov_rank,
            "top1_hit_rate": round(markov_top1, 2),
            "top6_hit_rate": round(markov_top6, 2),
            "learning_confidence": round(learning_confidence * 100, 2),
            "adaptation_mode": adaptation["mode"],
        })
        config["promotion_history"] = history[:8]
    config["previous_promotion_strength"] = promotion_strength

    if persist:
        _save_strategy_config("markov", region, config)
    return config


def _get_recommended_strategy(region, windows=(20, 50, 100), min_samples=5, phase_label=None):
    candidates = list(LOCAL_STRATEGY_KEYS)
    current_phase_label = str(phase_label or "").strip()
    if not current_phase_label:
        try:
            current_phase_label = str(_classify_ai_market_phase(_load_region_draw_history(region, limit=12)).get("label") or "neutral")
        except Exception:
            current_phase_label = "neutral"
    scored = []
    for strategy in candidates:
        window_scores = []
        total_samples = 0
        for idx, window in enumerate(windows):
            accuracy, total = _calculate_strategy_accuracy(region, strategy, limit=window)
            total_samples = max(total_samples, total)
            if total <= 0:
                continue
            weight = max(0.4, 1.0 - idx * 0.2)
            confidence = _clamp(total / max(min_samples, 1), 0.3, 1.0)
            window_scores.append(accuracy * 100 * weight * confidence)
        if not window_scores:
            continue
        tuned = _load_strategy_config(strategy, region)
        config_bonus = float(tuned.get("last_accuracy") or 0.0) * 100 * 0.15
        phase_score, phase_samples = _score_local_strategy_phase_strength(tuned, current_phase_label)
        phase_bonus = (phase_score * 100 * 0.18) if current_phase_label in ("hot", "cold", "concentrated", "dispersed") else 0.0
        score = round(sum(window_scores) / len(window_scores) + config_bonus + phase_bonus, 2)
        scored.append({
            "strategy": strategy,
            "label": _get_strategy_label(strategy),
            "score": score,
            "samples": total_samples,
            "phase_score": round(phase_score, 4),
            "phase_samples": int(phase_samples or 0),
        })

    if not scored:
        return {"strategy": "hybrid", "label": _get_strategy_label("hybrid"), "score": 0.0, "samples": 0}

    scored.sort(key=lambda item: (item["score"], item["samples"]), reverse=True)
    return scored[0]


def _score_ml_ensemble_candidates(region, strategies=None, windows=(20, 50, 100), min_samples=5):
    candidates = tuple(strategies or ("hybrid", "balanced", "trend", "hot", "cold"))
    ml_config = _load_strategy_config("ml", region)
    learned_bias = ml_config.get("ensemble_bias") or {}
    learning_confidence = float(ml_config.get("profile_learning_confidence") or 0.0)
    scored = []
    window_weights = {
        20: 0.5,
        50: 0.3,
        100: 0.2,
    }

    for strategy in candidates:
        config = _load_strategy_config(strategy, region)
        overall_accuracy, overall_total = _calculate_strategy_accuracy(
            region, strategy, limit=None
        )
        overall_hit_rates = _calculate_strategy_hit_rates(region, strategy, limit=None)
        total_samples = int(overall_total or 0)
        accuracy_percent = round(float(overall_accuracy or 0.0) * 100, 2)
        overall_top6_percent = round(float((overall_hit_rates or {}).get("top6", 0.0) or 0.0) * 100, 2)
        window_summaries = []
        weighted_accuracy_sum = 0.0
        weighted_factor_sum = 0.0
        all_recent_zero = True
        for window in windows:
            window_accuracy, window_total = _calculate_strategy_accuracy(region, strategy, limit=window)
            window_hit_rates = _calculate_strategy_hit_rates(region, strategy, limit=window)
            window_accuracy_percent = round(float(window_accuracy or 0.0) * 100, 2)
            window_top6_percent = round(float((window_hit_rates or {}).get("top6", 0.0) or 0.0) * 100, 2)
            # 近期窗口样本越完整，越充分参与权重；样本不足时自动降权。
            window_factor = float(window_weights.get(window, 0.0)) * _clamp(
                int(window_total or 0) / max(min(window, min_samples * 2), 1),
                0.0,
                1.0,
            )
            if window_accuracy_percent > 0:
                all_recent_zero = False
            if window_factor > 0:
                weighted_accuracy_sum += window_accuracy_percent * window_factor
                weighted_factor_sum += window_factor
            window_summaries.append({
                "window": int(window),
                "accuracy": window_accuracy_percent,
                "top6_accuracy": window_top6_percent,
                "total": int(window_total or 0),
                "weight": round(float(window_weights.get(window, 0.0)), 3),
                "factor": round(window_factor, 4),
            })

        recent_accuracy_percent = round(
            (weighted_accuracy_sum / weighted_factor_sum) if weighted_factor_sum > 0 else accuracy_percent,
            2,
        )
        fallback_reason = ""
        if all_recent_zero:
            fallback_recent_score = 0.0
            fallback_recent_weight = 0.0
            for item in window_summaries:
                fallback_component = (
                    float(item.get("accuracy", 0.0) or 0.0) * 1.0 +
                    float(item.get("top6_accuracy", 0.0) or 0.0) * 0.35
                )
                fallback_factor = float(item.get("factor", 0.0) or 0.0)
                if fallback_factor > 0:
                    fallback_recent_score += fallback_component * fallback_factor
                    fallback_recent_weight += fallback_factor
            long_term_score = (accuracy_percent * 1.0) + (overall_top6_percent * 0.35)
            blended_fallback = (
                (fallback_recent_score / fallback_recent_weight) * 0.4
                if fallback_recent_weight > 0 else 0.0
            ) + (long_term_score * 0.6)
            recent_accuracy_percent = round(blended_fallback, 2)
            fallback_reason = "recent_zero_fallback"
        effective_sample_basis = sum(item["total"] * item["weight"] for item in window_summaries)
        confidence = _clamp(
            effective_sample_basis / max(min_samples * 8, 1),
            0.25,
            1.0,
        )
        base_score = max(0.01, recent_accuracy_percent * confidence)
        bias_value = float(learned_bias.get(strategy, 0.0) or 0.0)
        bias_multiplier = 1.0 + (
            (bias_value - (1.0 / max(len(candidates), 1))) * 0.12 * learning_confidence
        )
        score = round(max(0.01, base_score * bias_multiplier), 2)
        scored.append({
            "strategy": strategy,
            "label": _get_strategy_label(strategy),
            "score": score,
            "samples": total_samples,
            "bias": round(bias_value * 100, 2),
            "recent_accuracy": recent_accuracy_percent,
            "overall_accuracy": accuracy_percent,
            "overall_total": total_samples,
            "overall_top6_accuracy": overall_top6_percent,
            "window_accuracies": window_summaries,
            "fallback_reason": fallback_reason,
        })

    scored.sort(key=lambda item: (item["score"], item["samples"]), reverse=True)
    return scored


def _build_ai_gate_profile(region, windows=(20, 50), min_samples=6):
    ai_scores = []
    anchor_scores = []
    for idx, window in enumerate(windows):
        ai_accuracy, ai_total = _calculate_strategy_accuracy(region, "ai", limit=window)
        recommended = _get_recommended_strategy(region, windows=(window,), min_samples=min_samples)
        anchor_strategy = str(recommended.get("strategy") or "hybrid")
        anchor_accuracy, anchor_total = _calculate_strategy_accuracy(region, anchor_strategy, limit=window)
        weight = max(0.55, 1.0 - idx * 0.18)
        ai_confidence = _clamp(ai_total / max(min_samples, 1), 0.2, 1.0)
        anchor_confidence = _clamp(anchor_total / max(min_samples, 1), 0.2, 1.0)
        ai_scores.append(ai_accuracy * 100 * weight * ai_confidence)
        anchor_scores.append(anchor_accuracy * 100 * weight * anchor_confidence)

    ai_score = round(sum(ai_scores) / len(ai_scores), 2) if ai_scores else 0.0
    anchor_score = round(sum(anchor_scores) / len(anchor_scores), 2) if anchor_scores else 0.0
    score_gap = round(anchor_score - ai_score, 2)
    status = "active"
    if score_gap >= 5.0:
        status = "fallback"
    elif score_gap >= 2.5:
        status = "guarded"

    return {
        "status": status,
        "ai_score": ai_score,
        "anchor_score": anchor_score,
        "score_gap": score_gap,
        "anchor_strategy": str((_get_recommended_strategy(region, windows=windows, min_samples=min_samples) or {}).get("strategy") or "hybrid"),
    }


def _learn_ai_region_profile(region, limit=180):
    query = PredictionRecord.query.filter_by(
        region=region,
        strategy="ai",
        is_result_updated=True,
    ).filter(PredictionRecord.actual_special_number != None)
    predictions = query.order_by(PredictionRecord.created_at.desc()).limit(limit).all()

    structure_scores = Counter()
    failure_scores = Counter()
    target_scores = Counter()
    total_quality = 0.0

    for idx, prediction in enumerate(predictions):
        recency_weight = max(0.22, 1.0 - (idx / max(limit, 1)) * 0.72)
        quality = max(0.04, _score_prediction_outcome(prediction)) * recency_weight
        total_quality += quality
        raw_outcome = _score_prediction_outcome(prediction)

        try:
            special = int(str(getattr(prediction, "special_number", "") or "").strip())
        except (TypeError, ValueError):
            special = 0
        normal = [
            int(item)
            for item in _parse_csv_list(getattr(prediction, "normal_numbers", "") or "")
            if str(item).isdigit()
        ][:6]
        if special and normal:
            zone_spread = len(set(1 if n <= 16 else 2 if n <= 33 else 3 for n in normal))
            structure_scores[f"zone_spread:{zone_spread}"] += quality

            tail_spread = len(set(n % 10 for n in normal))
            if tail_spread >= 5:
                structure_scores["tail_spread:wide"] += quality
            elif tail_spread >= 4:
                structure_scores["tail_spread:balanced"] += quality
            else:
                structure_scores["tail_spread:tight"] += quality

            all_numbers = normal + [special]
            color_spread = len(set(_get_color_zh(n) for n in all_numbers if _get_color_zh(n)))
            structure_scores[f"color_spread:{color_spread}"] += quality

            odd_count = sum(1 for n in all_numbers if n % 2 == 1)
            even_count = len(all_numbers) - odd_count
            parity_gap = abs(odd_count - even_count)
            structure_scores["parity:balanced" if parity_gap <= 1 else "parity:skewed"] += quality

            special_zone = "small" if special <= 16 else "mid" if special <= 33 else "large"
            structure_scores[f"special_zone:{special_zone}"] += quality
            structure_scores[f"special_color:{_get_color_zh(special) or 'unknown'}"] += quality
            if raw_outcome < 0.3:
                failure_scores[f"zone_spread:{zone_spread}"] += recency_weight
                if tail_spread >= 5:
                    failure_scores["tail_spread:wide"] += recency_weight
                elif tail_spread >= 4:
                    failure_scores["tail_spread:balanced"] += recency_weight
                else:
                    failure_scores["tail_spread:tight"] += recency_weight
                failure_scores[f"color_spread:{color_spread}"] += recency_weight
                failure_scores["parity:balanced" if parity_gap <= 1 else "parity:skewed"] += recency_weight
                failure_scores[f"special_zone:{special_zone}"] += recency_weight
                failure_scores[f"special_color:{_get_color_zh(special) or 'unknown'}"] += recency_weight

        actual_special = _normalize_draw_number(getattr(prediction, "actual_special_number", ""))
        predicted_special = _normalize_draw_number(getattr(prediction, "special_number", ""))
        if actual_special and predicted_special == actual_special:
            target_scores["top1"] += quality
        elif actual_special and actual_special in {str(n) for n in normal}:
            target_scores["top6"] += quality

    confidence = _clamp(len(predictions) / 30.0, 0.2, 1.0) * _clamp(total_quality / 12.0, 0.3, 1.0)
    return {
        "samples": len(predictions),
        "confidence": round(confidence, 4),
        "structure_scores": dict(structure_scores),
        "failure_scores": dict(failure_scores),
        "target_scores": dict(target_scores),
        "preferred_structures": _rank_weighted_preferences(structure_scores, limit=8),
    }


def _learn_ai_offline_rerank_profile(region, limit=180):
    query = PredictionRecord.query.filter_by(
        region=region,
        strategy="ai",
        is_result_updated=True,
    ).filter(PredictionRecord.actual_special_number != None)
    predictions = query.order_by(PredictionRecord.created_at.desc()).limit(limit).all()
    if not predictions:
        return {
            "samples": 0,
            "confidence": 0.0,
            "weight_adjustments": {},
            "mode_scores": {},
        }

    adjustment_scores = Counter()
    mode_scores = Counter()
    mode_adjustments = {}
    mode_window_adjustments = {}
    ranking_signal_scores = Counter()
    total_quality = 0.0

    for idx, prediction in enumerate(predictions):
        metadata = _deserialize_prediction_metadata(
            getattr(prediction, "prediction_metadata", "")
        )
        recency_weight = max(0.22, 1.0 - (idx / max(limit, 1)) * 0.72)
        outcome = _score_prediction_outcome(prediction)
        total_quality += outcome * recency_weight

        actual_special = _normalize_draw_number(getattr(prediction, "actual_special_number", ""))
        predicted_special = _normalize_draw_number(getattr(prediction, "special_number", ""))
        normal_numbers = {
            _normalize_draw_number(item)
            for item in str(getattr(prediction, "normal_numbers", "") or "").split(",")
            if _normalize_draw_number(item)
        }
        predicted_zodiac = str(getattr(prediction, "special_zodiac", "") or "").strip()
        actual_zodiac = str(getattr(prediction, "actual_special_zodiac", "") or "").strip()
        top1_hit = bool(actual_special and predicted_special == actual_special)
        top6_hit = bool(actual_special and actual_special in normal_numbers)
        zodiac_hit = bool(predicted_zodiac and actual_zodiac and predicted_zodiac == actual_zodiac)
        target_mode = str(metadata.get("ai_target_mode") or "top1_safe")
        mode_scores[target_mode] += max(0.04, outcome) * recency_weight
        mode_bucket = mode_adjustments.setdefault(target_mode, Counter())
        window_bucket_key = "recent" if idx < 24 else "mid" if idx < 72 else "long"
        window_bucket = mode_window_adjustments.setdefault(target_mode, {}).setdefault(window_bucket_key, Counter())

        if top1_hit:
            adjustment_scores["base_special"] += 0.18 * recency_weight
            adjustment_scores["special_vote"] += 0.14 * recency_weight
            adjustment_scores["shortlist_bonus"] += 0.08 * recency_weight
            adjustment_scores["confidence_bonus"] += 0.07 * recency_weight
            adjustment_scores["appearance_vote"] += 0.05 * recency_weight
            mode_bucket["base_special"] += 0.16 * recency_weight
            mode_bucket["special_vote"] += 0.12 * recency_weight
            window_bucket["base_special"] += 0.14 * recency_weight
            window_bucket["special_vote"] += 0.1 * recency_weight
            if target_mode == "top1_strict":
                adjustment_scores["base_special"] += 0.06 * recency_weight
                mode_bucket["base_special"] += 0.06 * recency_weight
                window_bucket["base_special"] += 0.05 * recency_weight
        elif top6_hit:
            adjustment_scores["avg_normal"] += 0.16 * recency_weight
            adjustment_scores["diversity_bonus"] += 0.1 * recency_weight
            adjustment_scores["shape_score"] += 0.08 * recency_weight
            adjustment_scores["structure_bonus"] += 0.08 * recency_weight
            mode_bucket["avg_normal"] += 0.14 * recency_weight
            mode_bucket["structure_bonus"] += 0.08 * recency_weight
            window_bucket["avg_normal"] += 0.12 * recency_weight
            window_bucket["structure_bonus"] += 0.08 * recency_weight
            if target_mode == "top6_cover":
                adjustment_scores["avg_normal"] += 0.05 * recency_weight
                mode_bucket["avg_normal"] += 0.05 * recency_weight
                window_bucket["avg_normal"] += 0.05 * recency_weight

        if zodiac_hit and not top1_hit:
            adjustment_scores["attr_bonus"] += 0.08 * recency_weight
            adjustment_scores["special_vote"] += 0.04 * recency_weight
            mode_bucket["attr_bonus"] += 0.06 * recency_weight
            window_bucket["attr_bonus"] += 0.05 * recency_weight

        if outcome < 0.3:
            adjustment_scores["overheat_penalty"] += 0.08 * recency_weight
            adjustment_scores["repeat_penalty"] += 0.07 * recency_weight
            adjustment_scores["confidence_bonus"] -= 0.06 * recency_weight
            adjustment_scores["appearance_vote"] -= 0.04 * recency_weight
            mode_bucket["overheat_penalty"] += 0.06 * recency_weight
            mode_bucket["repeat_penalty"] += 0.05 * recency_weight
            mode_bucket["confidence_bonus"] -= 0.05 * recency_weight
            window_bucket["overheat_penalty"] += 0.05 * recency_weight
            window_bucket["repeat_penalty"] += 0.04 * recency_weight
            window_bucket["confidence_bonus"] -= 0.04 * recency_weight
            if not top6_hit:
                adjustment_scores["avg_normal"] -= 0.06 * recency_weight
                adjustment_scores["diversity_bonus"] -= 0.03 * recency_weight
                adjustment_scores["shape_score"] -= 0.03 * recency_weight
                mode_bucket["avg_normal"] -= 0.05 * recency_weight
                window_bucket["avg_normal"] -= 0.04 * recency_weight
            if not top1_hit:
                adjustment_scores["base_special"] -= 0.07 * recency_weight
                adjustment_scores["special_vote"] -= 0.05 * recency_weight
                mode_bucket["base_special"] -= 0.05 * recency_weight
                window_bucket["base_special"] -= 0.04 * recency_weight
            if target_mode == "top1_strict":
                adjustment_scores["base_special"] -= 0.04 * recency_weight
                mode_bucket["base_special"] -= 0.03 * recency_weight
                window_bucket["base_special"] -= 0.03 * recency_weight
            elif target_mode == "top6_cover":
                adjustment_scores["structure_bonus"] -= 0.03 * recency_weight
                mode_bucket["structure_bonus"] -= 0.03 * recency_weight
                window_bucket["structure_bonus"] -= 0.03 * recency_weight

        ranking = list(metadata.get("ai_candidate_ranking") or [])
        if actual_special and ranking:
            matched_candidate = None
            for candidate in ranking:
                candidate_special = _normalize_draw_number(candidate.get("special"))
                candidate_normals = {
                    _normalize_draw_number(item)
                    for item in (candidate.get("normal") or [])
                    if _normalize_draw_number(item)
                }
                if candidate_special == actual_special or actual_special in candidate_normals:
                    matched_candidate = candidate
                    break
            if matched_candidate:
                diagnostics = dict(matched_candidate.get("score_diagnostics") or {})
                ranking_weight = recency_weight * (0.8 if matched_candidate.get("special") == actual_special else 0.52)
                if _safe_float(diagnostics.get("base_special"), 0.0) > 0:
                    ranking_signal_scores["base_special"] += ranking_weight * 0.08
                if _safe_float(diagnostics.get("avg_normal"), 0.0) > 0:
                    ranking_signal_scores["avg_normal"] += ranking_weight * 0.08
                if _safe_float(diagnostics.get("attr_bonus"), 0.0) > 0:
                    ranking_signal_scores["attr_bonus"] += ranking_weight * 0.05
                if _safe_float(diagnostics.get("shape_score"), 0.0) > 0:
                    ranking_signal_scores["shape_score"] += ranking_weight * 0.05
                if _safe_float(diagnostics.get("structure_bonus"), 0.0) > 0:
                    ranking_signal_scores["structure_bonus"] += ranking_weight * 0.06
                if _safe_float(diagnostics.get("overheat_penalty"), 0.0) < 0:
                    ranking_signal_scores["overheat_penalty"] -= ranking_weight * 0.04
            elif outcome < 0.3 and ranking:
                top_diag = dict((ranking[0] or {}).get("score_diagnostics") or {})
                if _safe_float(top_diag.get("base_special"), 0.0) > 0:
                    ranking_signal_scores["base_special"] -= recency_weight * 0.04
                if _safe_float(top_diag.get("avg_normal"), 0.0) > 0:
                    ranking_signal_scores["avg_normal"] -= recency_weight * 0.04
                if _safe_float(top_diag.get("structure_bonus"), 0.0) > 0:
                    ranking_signal_scores["structure_bonus"] -= recency_weight * 0.03

    confidence = _clamp(len(predictions) / 28.0, 0.2, 1.0) * _clamp(total_quality / 10.0, 0.25, 1.0)
    normalized = {
        key: round(_clamp(value * max(0.35, confidence), -0.22, 0.22), 4)
        for key, value in adjustment_scores.items()
    }
    for key, value in ranking_signal_scores.items():
        normalized[key] = round(
            _clamp(_safe_float(normalized.get(key), 0.0) + (value * max(0.25, confidence)), -0.24, 0.24),
            4,
        )
    normalized_mode_adjustments = {
        mode: {
            key: round(_clamp(value * max(0.3, confidence), -0.2, 0.2), 4)
            for key, value in counter.items()
        }
        for mode, counter in mode_adjustments.items()
        if counter
    }
    normalized_mode_window_adjustments = {}
    for mode, windows in mode_window_adjustments.items():
        normalized_mode_window_adjustments[mode] = {}
        for window_key, counter in windows.items():
            normalized_mode_window_adjustments[mode][window_key] = {
                key: round(_clamp(value * max(0.28, confidence), -0.18, 0.18), 4)
                for key, value in counter.items()
            }
    return {
        "samples": len(predictions),
        "confidence": round(confidence, 4),
        "weight_adjustments": normalized,
        "mode_scores": dict(mode_scores),
        "mode_adjustments": normalized_mode_adjustments,
        "mode_window_adjustments": normalized_mode_window_adjustments,
        "ranking_signal_scores": {
            key: round(value, 4) for key, value in ranking_signal_scores.items()
        },
    }


def _resolve_ai_feedback_mix_weights(region, windows=(20, 50, 100)):
    ai_windows = _calculate_strategy_hit_rate_windows(region, "ai", windows=windows)
    ml_windows = _calculate_strategy_hit_rate_windows(region, "ml", windows=windows)
    ai_stats = dict(ai_windows.get("aggregate") or {})
    ml_stats = dict(ml_windows.get("aggregate") or {})

    ai_top1 = _safe_float(ai_stats.get("top1"), 0.0)
    ai_top6 = _safe_float(ai_stats.get("top6"), 0.0)
    ml_top1 = _safe_float(ml_stats.get("top1"), 0.0)
    ml_top6 = _safe_float(ml_stats.get("top6"), 0.0)
    ai_total = int(ai_stats.get("total", 0) or 0)
    ml_total = int(ml_stats.get("total", 0) or 0)

    ai_score = (ai_top1 * 0.62) + (ai_top6 * 0.28) + (_safe_float(ai_stats.get("zodiac"), 0.0) * 0.1)
    ml_score = (ml_top1 * 0.48) + (ml_top6 * 0.42) + (_safe_float(ml_stats.get("zodiac"), 0.0) * 0.1)

    ai_confidence = _clamp(ai_total / 18.0, 0.25, 1.0)
    ml_confidence = _clamp(ml_total / 18.0, 0.25, 1.0)
    ai_weight = max(0.25, ai_score * ai_confidence)
    ml_weight = max(0.25, ml_score * ml_confidence)
    total_weight = ai_weight + ml_weight

    return {
        "ai": round(ai_weight / total_weight, 4),
        "ml": round(ml_weight / total_weight, 4),
        "ai_stats": ai_windows,
        "ml_stats": ml_windows,
    }


def _resolve_ai_quality_threshold(context):
    gate_profile = dict(context.get("gate_profile") or {})
    status = str(gate_profile.get("status") or "active")
    target_mode = str(context.get("target_mode") or "top1_safe")
    structure_profile = dict(context.get("structure_profile") or {})
    confidence = _clamp(_safe_float(structure_profile.get("confidence"), 0.0), 0.0, 1.0)

    threshold = -0.12
    if status == "guarded":
        threshold += 0.03
    elif status == "fallback":
        threshold += 0.06

    if target_mode == "top1_strict":
        threshold += 0.03
    elif target_mode == "top6_cover":
        threshold -= 0.02

    if confidence >= 0.7:
        threshold -= 0.015
    elif confidence <= 0.35:
        threshold += 0.02

    return round(_clamp(threshold, -0.22, -0.04), 4)


def _format_ai_structure_guidance(profile):
    preferred = list((profile or {}).get("preferred_structures") or [])
    failed = list(_rank_weighted_preferences((profile or {}).get("failure_scores") or {}, limit=5))
    lines = []
    if preferred:
        lines.append("优先结构：" + "、".join(preferred[:5]))
    if failed:
        lines.append("避免结构：" + "、".join(failed[:4]))
    return "\n".join(lines)


def _build_ai_layered_shortlists(
    special_ranked,
    normal_ranked,
    feedback_special_rank,
    feedback_normal_rank,
    special_votes,
    normal_votes,
    special_limit,
    normal_limit,
):
    special_recent = _dedupe_keep_order(
        list(feedback_special_rank[:max(3, special_limit // 2)]) +
        [int(number) for number, _ in special_votes.most_common(max(3, special_limit // 2))]
    )[:max(3, special_limit // 2)]
    special_stable = _dedupe_keep_order(list(special_ranked[:special_limit]))[:special_limit]
    special_explore = _dedupe_keep_order(
        list(special_ranked[special_limit:special_limit * 2]) +
        list(feedback_special_rank[max(3, special_limit // 2):special_limit])
    )[:max(2, special_limit // 3)]

    normal_recent = _dedupe_keep_order(
        list(feedback_normal_rank[:max(6, normal_limit // 3)]) +
        [int(number) for number, _ in normal_votes.most_common(max(6, normal_limit // 3))]
    )[:max(6, normal_limit // 3)]
    normal_stable = _dedupe_keep_order(list(normal_ranked[:normal_limit]))[:normal_limit]
    normal_explore = _dedupe_keep_order(
        list(normal_ranked[normal_limit:normal_limit * 2]) +
        list(feedback_normal_rank[max(6, normal_limit // 3):normal_limit])
    )[:max(4, normal_limit // 4)]

    return {
        "special": {
            "recent": special_recent,
            "stable": special_stable,
            "explore": special_explore,
        },
        "normal": {
            "recent": normal_recent,
            "stable": normal_stable,
            "explore": normal_explore,
        },
    }


def _select_ml_ensemble_strategies(region, slots=3, persist=True):
    eligible = [strategy for strategy in LOCAL_STRATEGY_KEYS if strategy != "ml"]
    if not eligible:
        return []

    config = _load_strategy_config("ml", region)
    scored = _score_ml_ensemble_candidates(region, strategies=eligible)
    score_map = {item["strategy"]: item for item in scored}
    default_core = ["hybrid", "balanced", "trend"]
    configured_core = [
        strategy for strategy in (config.get("ensemble_core_strategies") or default_core)
        if strategy in eligible
    ]
    current_core = []
    for strategy in configured_core + default_core:
        if strategy in eligible and strategy not in current_core:
            current_core.append(strategy)
        if len(current_core) >= slots:
            break
    for item in scored:
        strategy = item["strategy"]
        if strategy not in current_core:
            current_core.append(strategy)
        if len(current_core) >= slots:
            break

    replace_margin = float(config.get("ensemble_replace_margin") or 4.0)
    min_replace_samples = int(config.get("ensemble_replace_min_samples") or 8)
    challengers = [item for item in scored if item["strategy"] not in current_core]
    incumbents = [
        score_map.get(strategy, {"strategy": strategy, "score": 8.0, "samples": 0})
        for strategy in current_core[:slots]
    ]

    replacement = None
    if challengers and incumbents:
        best_challenger = challengers[0]
        weakest_incumbent = min(incumbents, key=lambda item: (item.get("score", 0.0), item.get("samples", 0)))
        if (
            best_challenger.get("samples", 0) >= min_replace_samples and
            best_challenger.get("score", 0.0) >= weakest_incumbent.get("score", 0.0) + replace_margin
        ):
            current_core = [
                best_challenger["strategy"] if strategy == weakest_incumbent["strategy"] else strategy
                for strategy in current_core[:slots]
            ]
            replacement = {
                "in": best_challenger["strategy"],
                "out": weakest_incumbent["strategy"],
                "score_margin": round(
                    best_challenger.get("score", 0.0) - weakest_incumbent.get("score", 0.0),
                    2,
                ),
            }

    final_core = [strategy for strategy in current_core[:slots] if strategy in eligible]
    final_core.sort(
        key=lambda strategy: (
            score_map.get(strategy, {}).get("score", 0.0),
            score_map.get(strategy, {}).get("samples", 0),
        ),
        reverse=True,
    )

    if persist:
        previous_core = list(config.get("ensemble_core_strategies") or [])
        if previous_core != final_core or replacement:
            config["ensemble_core_strategies"] = final_core
            config["ensemble_last_selected_at"] = datetime.now().isoformat()
            if replacement:
                config["ensemble_last_replacement"] = {
                    **replacement,
                    "at": datetime.now().isoformat(),
                }
            _save_strategy_config("ml", region, config)

    return final_core

def _tune_strategy_config(strategy, region):
    accuracy, total = _calculate_strategy_accuracy(region, strategy)
    config = _load_strategy_config(strategy, region)
    rollback_result = _maybe_rollback_strategy_config(strategy, region, config)
    if rollback_result.get("rolled_back"):
        return
    config = rollback_result.get("config") or config
    restore_result = _maybe_restore_rolled_back_strategy_config(strategy, region, config)
    if restore_result.get("restored"):
        return
    config = restore_result.get("config") or config
    previous_accuracy = float(config.get("last_accuracy") or 0.0)
    previous_total = int(config.get("last_total") or 0)

    if previous_total > 0:
        config["prev_accuracy"] = round(previous_accuracy, 4)
        config["prev_total"] = previous_total
        config["prev_updated_at"] = config.get("updated_at", "")

    config["last_accuracy"] = round(accuracy, 4)
    config["last_total"] = total
    config["accuracy_delta"] = round(accuracy - previous_accuracy, 4) if previous_total > 0 else 0.0
    config["updated_at"] = datetime.now().isoformat()

    if total <= 0:
        _save_strategy_config(strategy, region, config)
        return

    learning_strength = _clamp(total / 80.0, 0.25, 1.0)
    weights = dict(config.get("weights") or {})
    if strategy in LOCAL_STRATEGY_KEYS:
        layered_stats = _calculate_strategy_hit_rate_windows(region, strategy, windows=(12, 36, 72))
        config["layered_hit_rates"] = layered_stats
        phase_hit_rates = _calculate_strategy_phase_hit_rates(region, strategy, limit=180)
        config["phase_hit_rates"] = phase_hit_rates
        local_top1 = _safe_float((layered_stats.get("aggregate") or {}).get("top1"), accuracy)
        local_top6 = _safe_float((layered_stats.get("aggregate") or {}).get("top6"), 0.0)
    else:
        phase_hit_rates = {}
        local_top1 = accuracy
        local_top6 = 0.0

    if strategy in ("hot", "cold"):
        config["window"] = _clamp(int(26 + local_top1 * 42 + local_top6 * 18), 18, 82)
        config["pool"] = _clamp(int(11 + local_top1 * 9 + local_top6 * 5), 10, 24)
        config["special_pool"] = _clamp(int(7 + local_top1 * 7), 6, 14)
    elif strategy == "trend":
        config["window"] = _clamp(int(8 + local_top1 * 18 + local_top6 * 8), 8, 32)
        config["pool"] = _clamp(int(10 + local_top1 * 7 + local_top6 * 5), 8, 21)
        config["special_pool"] = _clamp(int(7 + local_top1 * 7), 6, 14)
    elif strategy == "balanced":
        high_count = _clamp(int(2 + local_top1 * 2), 1, 4)
        low_count = _clamp(int(2 + max(0.0, 1 - local_top1) * 2), 1, 4)
        mid_count = 6 - high_count - low_count
        if mid_count < 1:
            mid_count = 1
            if high_count >= low_count:
                high_count = 6 - low_count - mid_count
            else:
                low_count = 6 - high_count - mid_count
        config["bucket_counts"] = [low_count, mid_count, high_count]
        config["window"] = _clamp(int(36 + local_top6 * 42 + local_top1 * 18), 28, 92)
        config["pool"] = _clamp(int(12 + local_top6 * 9 + local_top1 * 4), 10, 24)
        config["special_pool"] = _clamp(int(7 + local_top1 * 6), 6, 14)
    elif strategy == "hybrid":
        hot_count = _clamp(int(2 + local_top1 * 2), 1, 4)
        cold_count = _clamp(int(2 + max(0.0, 1 - local_top1) * 2), 1, 4)
        trend_count = 6 - hot_count - cold_count
        if trend_count < 1:
            trend_count = 1
            if hot_count >= cold_count:
                hot_count = 6 - cold_count - trend_count
            else:
                cold_count = 6 - hot_count - trend_count
        config["mix"] = {"hot": hot_count, "cold": cold_count, "trend": trend_count}
        config["window"] = _clamp(int(34 + local_top6 * 40 + local_top1 * 16), 28, 90)
        config["pool"] = _clamp(int(12 + local_top6 * 8 + local_top1 * 5), 10, 24)
        config["special_pool"] = _clamp(int(7 + local_top1 * 6), 6, 14)
        config["trend_window"] = _clamp(int(8 + local_top1 * 16 + local_top6 * 6), 8, 30)
    elif strategy == "markov":
        adaptation = _resolve_learning_adaptation(region, "markov")
        config["window"] = _clamp(int(54 + local_top6 * 70 + local_top1 * 34), 36, 150)
        config["pool"] = _clamp(int(13 + local_top6 * 9 + local_top1 * 5), 10, 24)
        config["special_pool"] = _clamp(int(7 + local_top1 * 7), 6, 14)
        config["transition_decay"] = round(_clamp(0.972 + local_top1 * 0.022 + local_top6 * 0.01, 0.965, 0.995), 4)
        config["source_special_weight"] = round(_clamp(1.12 + local_top1 * 0.38, 1.08, 1.55), 3)
        learned_profile = _learn_markov_region_profile(region)
        learned_confidence = _clamp(float(learned_profile.get("confidence") or 0.0), 0.0, 1.0)
        preferred_markov = dict(learned_profile.get("preferred_config") or {})
        if preferred_markov and learned_confidence >= adaptation["markov_watch_threshold"]:
            blend = _clamp(
                learned_confidence * adaptation["markov_tune_blend_factor"],
                adaptation["markov_tune_blend_min"],
                adaptation["markov_tune_blend_max"],
            )
            config["window"] = _clamp(
                int(round(config["window"] * (1.0 - blend) + int(preferred_markov.get("window") or config["window"]) * blend)),
                36,
                160,
            )
            config["pool"] = _clamp(
                int(round(config["pool"] * (1.0 - blend) + int(preferred_markov.get("pool") or config["pool"]) * blend)),
                10,
                24,
            )
            config["special_pool"] = _clamp(
                int(round(config["special_pool"] * (1.0 - blend) + int(preferred_markov.get("special_pool") or config["special_pool"]) * blend)),
                6,
                14,
            )
            config["transition_decay"] = round(_clamp(
                config["transition_decay"] * (1.0 - blend) + float(preferred_markov.get("transition_decay") or config["transition_decay"]) * blend,
                0.965,
                0.995,
            ), 4)
            config["source_special_weight"] = round(_clamp(
                config["source_special_weight"] * (1.0 - blend) + float(preferred_markov.get("source_special_weight") or config["source_special_weight"]) * blend,
                1.0,
                1.7,
            ), 3)
        config["preferred_markov_config"] = preferred_markov
        config["profile_learning_confidence"] = round(learned_confidence, 4)
        config["profile_learning_samples"] = int(learned_profile.get("samples") or 0)
        config["learning_adaptation_mode"] = adaptation["mode"]
    elif strategy == "ai":
        config["temperature"] = round(_clamp(0.42 - accuracy * 0.18, 0.18, 0.45), 2)
        config["history_window"] = _clamp(int(12 + (0.5 - accuracy) * 6), 8, 18)
        config["sample_count"] = _clamp(int(4 - accuracy * 2), 2, 5)
        config["candidate_count"] = _clamp(int(4 - accuracy), 2, 4)
        config["special_shortlist"] = _clamp(int(10 - accuracy * 4), 6, 10)
        config["normal_shortlist"] = _clamp(int(20 - accuracy * 6), 14, 22)
        target_mode, target_stats = _resolve_ai_target_mode(region, limit=140)
        config["target_mode"] = target_mode
        config["target_mode_stats"] = target_stats
        layered_stats = _calculate_strategy_hit_rate_windows(region, "ai", windows=(12, 36, 72))
        config["layered_hit_rates"] = layered_stats
        gate_profile = _build_ai_gate_profile(region)
        score_gap = max(0.0, float(gate_profile.get("score_gap") or 0.0))
        score_pressure = _clamp(score_gap / 8.0, 0.0, 1.0)
        feedback = _build_prediction_feedback(region, "ai", limit=160)
        feedback_confidence = _safe_float(feedback.get("confidence"), 0.0)
        ai_profile = _learn_ai_region_profile(region)
        offline_profile = _learn_ai_offline_rerank_profile(region)
        mix_weights = _resolve_ai_feedback_mix_weights(region)
        structure_confidence = _safe_float(ai_profile.get("confidence"), 0.0)
        rerank_weights = _default_ai_rerank_weights()
        rerank_weights["base_special"] = round(_clamp(0.72 + accuracy * 0.42 + feedback_confidence * 0.12 + structure_confidence * 0.08, 0.65, 1.3), 4)
        rerank_weights["avg_normal"] = round(_clamp(0.42 + accuracy * 0.28 + learning_strength * 0.08 + structure_confidence * 0.06, 0.35, 1.0), 4)
        rerank_weights["special_vote"] = round(_clamp(0.1 + learning_strength * 0.12 + score_pressure * 0.08, 0.08, 0.42), 4)
        rerank_weights["normal_vote_avg"] = round(_clamp(0.06 + learning_strength * 0.08 + score_pressure * 0.06, 0.04, 0.28), 4)
        rerank_weights["shortlist_bonus"] = round(_clamp(0.82 + learning_strength * 0.22 + score_pressure * 0.28 + structure_confidence * 0.08, 0.75, 1.5), 4)
        rerank_weights["attr_bonus"] = round(_clamp(0.72 + accuracy * 0.44 + feedback_confidence * 0.1, 0.65, 1.35), 4)
        rerank_weights["diversity_bonus"] = round(_clamp(0.74 + (1 - accuracy) * 0.32 + score_pressure * 0.18 + structure_confidence * 0.14, 0.7, 1.45), 4)
        rerank_weights["repeat_penalty"] = round(_clamp(1.0 + (1 - accuracy) * 0.55 + score_pressure * 0.35, 0.9, 1.95), 4)
        rerank_weights["overheat_penalty"] = round(_clamp(0.88 + (1 - accuracy) * 0.44 + score_pressure * 0.24, 0.78, 1.65), 4)
        rerank_weights["confidence_bonus"] = round(_clamp(0.5 + accuracy * 0.55 - score_pressure * 0.12, 0.38, 1.2), 4)
        rerank_weights["shape_score"] = round(_clamp(0.82 + accuracy * 0.36 + score_pressure * 0.14 + structure_confidence * 0.18, 0.78, 1.45), 4)
        rerank_weights["structure_bonus"] = round(_clamp(0.7 + structure_confidence * 0.55 + learning_strength * 0.12, 0.6, 1.4), 4)
        rerank_weights["gate_adjustment"] = round(_clamp(1.0 + score_pressure * 0.4, 1.0, 1.6), 4)
        rerank_weights["appearance_vote"] = round(_clamp(0.18 + accuracy * 0.1 + learning_strength * 0.04, 0.16, 0.38), 4)
        if target_mode == "top6_cover":
            rerank_weights["avg_normal"] = round(_clamp(rerank_weights["avg_normal"] + 0.18, 0.35, 1.1), 4)
            rerank_weights["diversity_bonus"] = round(_clamp(rerank_weights["diversity_bonus"] + 0.16, 0.7, 1.5), 4)
            rerank_weights["shortlist_bonus"] = round(_clamp(rerank_weights["shortlist_bonus"] + 0.08, 0.75, 1.55), 4)
            rerank_weights["base_special"] = round(_clamp(rerank_weights["base_special"] - 0.12, 0.55, 1.25), 4)
            rerank_weights["structure_bonus"] = round(_clamp(rerank_weights["structure_bonus"] + 0.12, 0.6, 1.5), 4)
        else:
            rerank_weights["base_special"] = round(_clamp(rerank_weights["base_special"] + 0.12, 0.65, 1.35), 4)
            rerank_weights["special_vote"] = round(_clamp(rerank_weights["special_vote"] + 0.05, 0.08, 0.48), 4)
            rerank_weights["appearance_vote"] = round(_clamp(rerank_weights["appearance_vote"] - 0.03, 0.14, 0.38), 4)
        offline_adjustments = dict(offline_profile.get("weight_adjustments") or {})
        mode_adjustments = dict((offline_profile.get("mode_adjustments") or {}).get(target_mode) or {})
        mode_window_adjustments = dict((offline_profile.get("mode_window_adjustments") or {}).get(target_mode) or {})
        offline_confidence = _safe_float(offline_profile.get("confidence"), 0.0)
        for key, delta in offline_adjustments.items():
            if key not in rerank_weights:
                continue
            rerank_weights[key] = round(
                _clamp(rerank_weights[key] + (_safe_float(delta, 0.0) * max(0.35, offline_confidence)), 0.04, 1.8),
                4,
            )
        for key, delta in mode_adjustments.items():
            if key not in rerank_weights:
                continue
            rerank_weights[key] = round(
                _clamp(rerank_weights[key] + (_safe_float(delta, 0.0) * max(0.25, offline_confidence) * 0.9), 0.04, 1.8),
                4,
            )
        window_blend = {"recent": 1.0, "mid": 0.62, "long": 0.35}
        for window_key, blend_weight in window_blend.items():
            for key, delta in dict(mode_window_adjustments.get(window_key) or {}).items():
                if key not in rerank_weights:
                    continue
                rerank_weights[key] = round(
                    _clamp(
                        rerank_weights[key] + (_safe_float(delta, 0.0) * max(0.22, offline_confidence) * blend_weight * 0.8),
                        0.04,
                        1.8,
                    ),
                    4,
                )
        config["rerank_weights"] = _normalize_ai_rerank_weights(rerank_weights)
        config["rerank_learning_confidence"] = round(
            _clamp((learning_strength * 0.55) + (feedback_confidence * 0.45), 0.2, 1.0),
            4,
        )
        config["rerank_gate_profile"] = {
            "status": gate_profile.get("status"),
            "score_gap": round(score_gap, 2),
            "anchor_strategy": gate_profile.get("anchor_strategy", ""),
        }
        config["feedback_mix_weights"] = {
            "ai": _safe_float(mix_weights.get("ai"), 0.5),
            "ml": _safe_float(mix_weights.get("ml"), 0.5),
        }
        config["feedback_mix_stats"] = {
            "ai": mix_weights.get("ai_stats", {}),
            "ml": mix_weights.get("ml_stats", {}),
        }
        config["structure_profile"] = {
            "confidence": round(structure_confidence, 4),
            "samples": int(ai_profile.get("samples", 0) or 0),
            "preferred_structures": list(ai_profile.get("preferred_structures") or []),
            "target_scores": dict(ai_profile.get("target_scores") or {}),
        }
        config["offline_rerank_profile"] = {
            "confidence": round(offline_confidence, 4),
            "samples": int(offline_profile.get("samples", 0) or 0),
            "weight_adjustments": offline_adjustments,
            "mode_scores": dict(offline_profile.get("mode_scores") or {}),
            "mode_adjustments": dict(offline_profile.get("mode_adjustments") or {}),
            "mode_window_adjustments": dict(offline_profile.get("mode_window_adjustments") or {}),
            "ranking_signal_scores": dict(offline_profile.get("ranking_signal_scores") or {}),
        }
    elif strategy == "ml":
        adaptation = _resolve_learning_adaptation(region, "ml")
        config["history_window"] = _clamp(int(90 + accuracy * 120), 80, 220)
        config["feature_window"] = _clamp(int(40 + accuracy * 40), 30, 80)
        config["evaluation_window"] = _clamp(int(18 + accuracy * 24), 12, 48)
        config["pool"] = _clamp(int(14 + accuracy * 8), 12, 24)
        config["special_pool"] = _clamp(int(6 + accuracy * 4), 6, 12)
        config["epochs"] = _clamp(int(15 + accuracy * 15), 15, 30)
        config["learning_rate"] = round(_clamp(0.02 + (1 - accuracy) * 0.08, 0.01, 0.08), 3)
        config["l2"] = round(_clamp(0.001 + (1 - accuracy) * 0.004, 0.001, 0.005), 4)
        learned_profile = _learn_ml_region_profile(region)
        config["preferred_feature_profiles"] = learned_profile.get("feature_profiles", [])
        config["preferred_runtime_profiles"] = learned_profile.get("runtime_profiles", [])
        config["profile_learning_confidence"] = learned_profile.get("confidence", 0.0)
        config["profile_learning_samples"] = learned_profile.get("samples", 0)
        config["learning_adaptation_mode"] = adaptation["mode"]
        config["ensemble_bias"] = learned_profile.get("ensemble_bias", {})

        learned_blends = [
            float(item)
            for item in (learned_profile.get("blend_candidates") or [])
            if 0.0 < float(item) <= 1.0
        ]
        default_blends = [0.55, 0.7, 0.82]
        merged_blends = []
        for candidate in learned_blends + default_blends:
            rounded = round(float(candidate), 2)
            if rounded not in merged_blends:
                merged_blends.append(rounded)
            if len(merged_blends) >= 4:
                break
        config["blend_candidates"] = merged_blends or default_blends
        config["primary_feature_profile"] = (
            (learned_profile.get("feature_profiles") or [config.get("primary_feature_profile") or "full"])[0]
        )
        config["primary_runtime_profile"] = (
            (learned_profile.get("runtime_profiles") or [config.get("primary_runtime_profile") or "base"])[0]
        )

    if weights:
        weights["feedback"] = round(_clamp(0.45 + learning_strength * 0.7 + accuracy * 0.3, 0.45, 1.35), 2)
        weights["color"] = round(_clamp(0.10 + accuracy * 0.20, 0.08, 0.35), 2)
        weights["zodiac"] = round(_clamp(0.10 + accuracy * 0.18, 0.08, 0.32), 2)
        weights["parity"] = round(_clamp(0.12 + accuracy * 0.18, 0.10, 0.30), 2)
        if strategy == "cold":
            weights["overdue"] = round(_clamp(0.70 + (1 - accuracy) * 0.35, 0.65, 1.20), 2)
        elif strategy == "trend":
            weights["trend"] = round(_clamp(1.00 + learning_strength * 0.25 + accuracy * 0.20, 1.0, 1.5), 2)
        elif strategy == "hot":
            weights["hot"] = round(_clamp(1.00 + learning_strength * 0.20 + accuracy * 0.20, 1.0, 1.5), 2)
        elif strategy == "balanced":
            weights["cold"] = round(_clamp(0.30 + (1 - accuracy) * 0.20, 0.25, 0.60), 2)
        elif strategy == "hybrid":
            weights["feedback"] = round(_clamp(weights["feedback"] + 0.10, 0.5, 1.45), 2)
            weights["parity"] = round(_clamp(weights["parity"] + 0.04, 0.12, 0.32), 2)
        elif strategy == "markov":
            adaptation = _resolve_learning_adaptation(region, "markov")
            weights["transition"] = round(_clamp(1.05 + learning_strength * 0.28 + accuracy * 0.24, 1.0, 1.65), 2)
            weights["transition_lift"] = round(_clamp(0.22 + learning_strength * 0.12 + local_top6 * 0.22, 0.12, 0.75), 2)
            weights["special_transition"] = round(_clamp(0.82 + learning_strength * 0.22 + accuracy * 0.22, 0.75, 1.45), 2)
            weights["special_transition_lift"] = round(_clamp(0.18 + learning_strength * 0.12 + local_top1 * 0.24, 0.08, 0.7), 2)
            weights["second_order"] = round(_clamp(0.44 + learning_strength * 0.18 + local_top6 * 0.34, 0.25, 1.2), 2)
            weights["phase_transition"] = round(_clamp(0.34 + learning_strength * 0.14 + local_top1 * 0.28, 0.15, 1.05), 2)
            weights["attribute_transition"] = round(_clamp(0.26 + learning_strength * 0.1 + local_top6 * 0.18, 0.12, 0.9), 2)
            weights["failure"] = round(_clamp(0.34 + (1 - accuracy) * 0.24 + learning_strength * 0.08, 0.22, 1.0), 2)
            preferred_markov = dict(config.get("preferred_markov_config") or {})
            learned_confidence = _clamp(float(config.get("profile_learning_confidence") or 0.0), 0.0, 1.0)
            if preferred_markov and learned_confidence >= adaptation["markov_watch_threshold"]:
                blend = _clamp(
                    learned_confidence * adaptation["markov_weight_blend_factor"],
                    adaptation["markov_weight_blend_min"],
                    adaptation["markov_weight_blend_max"],
                )
                weights["transition"] = round(_clamp(
                    weights["transition"] * (1.0 - blend) + float(preferred_markov.get("transition_weight") or weights["transition"]) * blend,
                    0.75,
                    1.85,
                ), 2)
                weights["transition_lift"] = round(_clamp(
                    weights["transition_lift"] * (1.0 - blend) + float(preferred_markov.get("transition_lift_weight") or weights["transition_lift"]) * blend,
                    0.0,
                    0.9,
                ), 2)
                weights["special_transition"] = round(_clamp(
                    weights["special_transition"] * (1.0 - blend) + float(preferred_markov.get("special_transition_weight") or weights["special_transition"]) * blend,
                    0.65,
                    1.65,
                ), 2)
                weights["special_transition_lift"] = round(_clamp(
                    weights["special_transition_lift"] * (1.0 - blend) + float(preferred_markov.get("special_transition_lift_weight") or weights["special_transition_lift"]) * blend,
                    0.0,
                    0.85,
                ), 2)
                for weight_key, pref_key, low, high in (
                    ("second_order", "second_order_weight", 0.0, 1.35),
                    ("phase_transition", "phase_transition_weight", 0.0, 1.25),
                    ("attribute_transition", "attribute_transition_weight", 0.0, 1.1),
                    ("failure", "failure_weight", 0.0, 1.2),
                ):
                    weights[weight_key] = round(_clamp(
                        weights[weight_key] * (1.0 - blend) + float(preferred_markov.get(pref_key) or weights[weight_key]) * blend,
                        low,
                        high,
                    ), 2)
        config["weights"] = weights
        if strategy in ("hot", "cold", "trend", "balanced", "hybrid"):
            config["phase_weight_learning"] = _build_local_phase_learning_map(strategy, weights, phase_hit_rates)
            config["phase_runtime_templates"] = _build_local_phase_runtime_templates(strategy, config, phase_hit_rates)

    if strategy in LOCAL_STRATEGY_KEYS:
        config["local_tuning_profile"] = {
            "top1_rate": round(local_top1 * 100, 2),
            "top6_rate": round(local_top6 * 100, 2),
            "window_bias": int(config.get("window") or 0),
            "pool_bias": int(config.get("pool") or 0),
        }

    _save_strategy_config(strategy, region, config)

def update_strategy_configs(region, strategies=None):
    strategies = list(strategies or ("hot", "cold", "trend", "balanced", "hybrid", "markov", "ml", "ai"))
    for strategy in strategies:
        try:
            _tune_strategy_config(strategy, region)
        except Exception as e:
            print(f"Strategy tuning failed for {strategy} ({region}): {e}")
    try:
        _promote_ml_region_profile(region, persist=True)
    except Exception as e:
        print(f"ML profile promotion failed for {region}: {e}")
    try:
        _promote_markov_region_profile(region, persist=True)
    except Exception as e:
        print(f"Markov profile promotion failed for {region}: {e}")

def _get_number_to_zodiac_map(year):
    number_to_zodiac = {}
    try:
        from models import ZodiacSetting
        mapping = ZodiacSetting.get_mapping_for_macau_year(year)
        if mapping:
            number_to_zodiac = {str(number): zodiac for number, zodiac in mapping.items()}
    except Exception as e:
        print(f"Failed to build zodiac mapping: {e}")

    if not number_to_zodiac:
        macau_data = get_macau_data(str(year))
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        number_to_zodiac[num] = zodiacs[i]

    return number_to_zodiac

def _get_next_period(region, latest_period):
    if region == 'hk':
        if latest_period and '/' in latest_period:
            parts = latest_period.split('/')
            if len(parts) == 2:
                year_part, num_part = parts
                try:
                    next_num = int(num_part) + 1
                    year_num = int(year_part)
                    year_width = max(2, len(year_part))
                    if next_num > 120:
                        next_year = year_num + 1
                        return f"{str(next_year).zfill(year_width)}/001"
                    return f"{year_part}/{next_num:03d}"
                except (ValueError, TypeError):
                    pass
        if latest_period and latest_period.isdigit():
            if len(latest_period) >= 7 and latest_period[:4].isdigit():
                year_part = latest_period[:4]
                seq_part = latest_period[4:]
                if seq_part.isdigit():
                    seq = int(seq_part)
                    next_seq = seq + 1
                    if len(seq_part) == 3 and next_seq > 999:
                        next_year = int(year_part) + 1
                        return f"{next_year}001"
                    return f"{year_part}{str(next_seq).zfill(len(seq_part))}"
            return str(int(latest_period) + 1)
        return _default_period(region)

    if latest_period and latest_period.isdigit():
        if len(latest_period) >= 7 and latest_period[:4].isdigit():
            year_part = latest_period[:4]
            seq_part = latest_period[4:]
            if seq_part.isdigit():
                seq = int(seq_part)
                next_seq = seq + 1
                if len(seq_part) == 3 and next_seq > 999:
                    next_year = int(year_part) + 1
                    return f"{next_year}001"
                return f"{year_part}{str(next_seq).zfill(len(seq_part))}"
        return str(int(latest_period) + 1)
    return datetime.now().strftime('%Y%m%d')

def _default_period(region):
    if region == 'hk':
        current_year = datetime.now().strftime('%Y')
        return f"{current_year}001"
    current_year = datetime.now().strftime('%y')
    return f"{current_year}/001"

def analyze_special_zodiac_frequency(data, region, year=None):
    zodiacs = []
    if year is None:
        year = datetime.now().year

    number_to_zodiac = {}
    if region == 'hk':
        number_to_zodiac = _get_number_to_zodiac_map(year)
    for r in data:
        sno = r.get('sno')
        if not sno: continue
        if region == 'hk':
            zodiacs.append(number_to_zodiac.get(str(sno), r.get('sno_zodiac')))
        else:
            zodiacs.append(r.get('sno_zodiac'))
    return Counter(z for z in zodiacs if z)


def _build_repeat_transition_profile(data, region, year=None, recent_window=36):
    records = list(data or [])
    if len(records) < 2:
        return {
            "latest_special": None,
            "latest_zodiac": "",
            "latest_special_streak": 0,
            "latest_zodiac_streak": 0,
            "latest_special_repeat_probability": 0.0,
            "latest_zodiac_repeat_probability": 0.0,
            "overall_special_repeat_rate": 0.0,
            "overall_zodiac_repeat_rate": 0.0,
        }

    if year is None:
        year = _infer_draw_year(records)
    number_to_zodiac = _get_number_to_zodiac_map(year) if region == "hk" else {}

    normalized = []
    for record in records:
        try:
            special = int(str(record.get("sno") or "").strip())
        except (TypeError, ValueError):
            continue
        if not (1 <= special <= 49):
            continue
        zodiac = number_to_zodiac.get(str(special), "") if number_to_zodiac else ""
        if not zodiac:
            zodiac = str(record.get("sno_zodiac") or "").strip()
        normalized.append({
            "special": special,
            "zodiac": zodiac,
        })

    if len(normalized) < 2:
        return {
            "latest_special": None,
            "latest_zodiac": "",
            "latest_special_streak": 0,
            "latest_zodiac_streak": 0,
            "latest_special_repeat_probability": 0.0,
            "latest_zodiac_repeat_probability": 0.0,
            "overall_special_repeat_rate": 0.0,
            "overall_zodiac_repeat_rate": 0.0,
        }

    latest_special = normalized[0]["special"]
    latest_zodiac = normalized[0]["zodiac"]

    latest_special_streak = 1
    for item in normalized[1:]:
        if item["special"] != latest_special:
            break
        latest_special_streak += 1

    latest_zodiac_streak = 1 if latest_zodiac else 0
    if latest_zodiac:
        for item in normalized[1:]:
            if item["zodiac"] != latest_zodiac:
                break
            latest_zodiac_streak += 1

    chronological = list(reversed(normalized))
    total_transitions = 0
    special_repeat_hits = 0
    zodiac_transition_total = 0
    zodiac_repeat_hits = 0
    latest_special_prev_total = 0
    latest_special_prev_hits = 0
    latest_zodiac_prev_total = 0
    latest_zodiac_prev_hits = 0

    recent_slice = chronological[-max(6, int(recent_window or 36)):]
    recent_total_transitions = 0
    recent_special_repeat_hits = 0
    recent_zodiac_total = 0
    recent_zodiac_repeat_hits = 0
    recent_latest_special_prev_total = 0
    recent_latest_special_prev_hits = 0
    recent_latest_zodiac_prev_total = 0
    recent_latest_zodiac_prev_hits = 0

    for idx in range(1, len(chronological)):
        prev_item = chronological[idx - 1]
        curr_item = chronological[idx]
        total_transitions += 1
        if curr_item["special"] == prev_item["special"]:
            special_repeat_hits += 1
        if prev_item["special"] == latest_special:
            latest_special_prev_total += 1
            if curr_item["special"] == latest_special:
                latest_special_prev_hits += 1
        prev_zodiac = prev_item["zodiac"]
        curr_zodiac = curr_item["zodiac"]
        if prev_zodiac:
            zodiac_transition_total += 1
            if curr_zodiac == prev_zodiac:
                zodiac_repeat_hits += 1
            if prev_zodiac == latest_zodiac:
                latest_zodiac_prev_total += 1
                if curr_zodiac == latest_zodiac:
                    latest_zodiac_prev_hits += 1

    for idx in range(1, len(recent_slice)):
        prev_item = recent_slice[idx - 1]
        curr_item = recent_slice[idx]
        recent_total_transitions += 1
        if curr_item["special"] == prev_item["special"]:
            recent_special_repeat_hits += 1
        if prev_item["special"] == latest_special:
            recent_latest_special_prev_total += 1
            if curr_item["special"] == latest_special:
                recent_latest_special_prev_hits += 1
        prev_zodiac = prev_item["zodiac"]
        curr_zodiac = curr_item["zodiac"]
        if prev_zodiac:
            recent_zodiac_total += 1
            if curr_zodiac == prev_zodiac:
                recent_zodiac_repeat_hits += 1
            if prev_zodiac == latest_zodiac:
                recent_latest_zodiac_prev_total += 1
                if curr_zodiac == latest_zodiac:
                    recent_latest_zodiac_prev_hits += 1

    overall_special_repeat_rate = special_repeat_hits / max(total_transitions, 1)
    overall_zodiac_repeat_rate = zodiac_repeat_hits / max(zodiac_transition_total, 1) if zodiac_transition_total else 0.0
    recent_special_repeat_rate = recent_special_repeat_hits / max(recent_total_transitions, 1) if recent_total_transitions else overall_special_repeat_rate
    recent_zodiac_repeat_rate = recent_zodiac_repeat_hits / max(recent_zodiac_total, 1) if recent_zodiac_total else overall_zodiac_repeat_rate

    latest_special_conditional = latest_special_prev_hits / max(latest_special_prev_total, 1) if latest_special_prev_total else overall_special_repeat_rate
    latest_zodiac_conditional = latest_zodiac_prev_hits / max(latest_zodiac_prev_total, 1) if latest_zodiac_prev_total else overall_zodiac_repeat_rate
    recent_latest_special_conditional = (
        recent_latest_special_prev_hits / max(recent_latest_special_prev_total, 1)
        if recent_latest_special_prev_total else latest_special_conditional
    )
    recent_latest_zodiac_conditional = (
        recent_latest_zodiac_prev_hits / max(recent_latest_zodiac_prev_total, 1)
        if recent_latest_zodiac_prev_total else latest_zodiac_conditional
    )

    latest_special_repeat_probability = (
        overall_special_repeat_rate * 0.20 +
        recent_special_repeat_rate * 0.25 +
        latest_special_conditional * 0.30 +
        recent_latest_special_conditional * 0.25
    )
    latest_zodiac_repeat_probability = (
        overall_zodiac_repeat_rate * 0.20 +
        recent_zodiac_repeat_rate * 0.25 +
        latest_zodiac_conditional * 0.30 +
        recent_latest_zodiac_conditional * 0.25
    )

    latest_special_repeat_probability *= (0.72 ** max(0, latest_special_streak - 1))
    latest_zodiac_repeat_probability *= (0.78 ** max(0, latest_zodiac_streak - 1))

    return {
        "latest_special": latest_special,
        "latest_zodiac": latest_zodiac,
        "latest_special_streak": latest_special_streak,
        "latest_zodiac_streak": latest_zodiac_streak,
        "latest_special_repeat_probability": round(_clamp(latest_special_repeat_probability, 0.0, 1.0), 6),
        "latest_zodiac_repeat_probability": round(_clamp(latest_zodiac_repeat_probability, 0.0, 1.0), 6),
        "overall_special_repeat_rate": round(_clamp(overall_special_repeat_rate, 0.0, 1.0), 6),
        "overall_zodiac_repeat_rate": round(_clamp(overall_zodiac_repeat_rate, 0.0, 1.0), 6),
    }

def analyze_special_color_frequency(data, region):
    colors = []
    for r in data:
        sno = r.get('sno')
        if not sno: continue
        if region == 'hk':
            color_en = _get_hk_number_color(sno)
            colors.append(COLOR_MAP_EN_TO_ZH.get(color_en))
        else:
            try:
                color_en = r.get('raw_wave', '').split(',')[-1]
                colors.append(COLOR_MAP_EN_TO_ZH.get(color_en))
            except IndexError:
                continue
    return Counter(c for c in colors if c)

def _get_parity_zh(number):
    try:
        return '双' if int(number) % 2 == 0 else '单'
    except (TypeError, ValueError):
        return ''

def analyze_special_parity_frequency(data):
    parities = []
    for r in data:
        sno = r.get('sno')
        if not sno:
            continue
        parity = _get_parity_zh(sno)
        if parity:
            parities.append(parity)
    return Counter(parities)

def _infer_draw_year(data):
    for record in data or []:
        raw_date = str(record.get("date", "")).strip()
        if len(raw_date) >= 4 and raw_date[:4].isdigit():
            return int(raw_date[:4])
        draw_id = str(record.get("id", "")).strip()
        if len(draw_id) >= 4 and draw_id[:4].isdigit():
            return int(draw_id[:4])
    return datetime.now().year

def _build_number_frequency(data):
    counts = Counter()
    for record in data or []:
        for number in record.get("no", []):
            if number:
                counts[str(number)] += 1
        sno = record.get("sno")
        if sno:
            counts[str(sno)] += 1
    return {str(i): counts.get(str(i), 0) for i in range(1, 50)}

def _build_overdue_scores(data):
    scores = {}
    for number in range(1, 50):
        gap = len(data or [])
        target = str(number)
        for idx, record in enumerate(data or []):
            if record.get("sno") == target:
                gap = idx
                break
        scores[str(number)] = gap
    return scores

def _normalize_metric_map(metric_map):
    if not metric_map:
        return {}
    values = list(metric_map.values())
    min_value = min(values)
    max_value = max(values)
    if max_value == min_value:
        fallback = 0.5 if max_value > 0 else 0.0
        return {key: fallback for key in metric_map}
    return {
        key: (value - min_value) / (max_value - min_value)
        for key, value in metric_map.items()
    }


def _normalize_signed_metric_map(metric_map):
    if not metric_map:
        return {}
    max_abs = max(abs(float(value)) for value in metric_map.values())
    if max_abs <= 0:
        return {key: 0.5 for key in metric_map}
    return {
        key: round(_clamp(0.5 + (float(value) / (2 * max_abs)), 0.0, 1.0), 4)
        for key, value in metric_map.items()
    }


def _feedback_confidence(sample_count, full_confidence=80):
    if sample_count <= 0:
        return 0.0
    return round(_clamp(sample_count / float(full_confidence), 0.15, 1.0), 4)


def _default_ai_rerank_weights():
    return dict(_default_strategy_config("ai").get("rerank_weights") or {})


def _normalize_ai_rerank_weights(weights):
    defaults = _default_ai_rerank_weights()
    incoming = dict(weights or {})
    normalized = {}
    for key, default_value in defaults.items():
        normalized[key] = round(
            _clamp(_safe_float(incoming.get(key), default_value), -2.5, 2.5),
            4,
        )
    return normalized


def _resolve_ai_target_mode(region, limit=120):
    layered = _calculate_strategy_hit_rate_windows(region, "ai", windows=(12, 36, 72))
    stats = dict(layered.get("aggregate") or {})
    stats["windows"] = layered.get("windows") or []
    total = int(stats.get("total", 0) or 0)
    if total < 12:
        return "top1_safe", stats

    top1 = _safe_float(stats.get("top1"), 0.0)
    top6 = _safe_float(stats.get("top6"), 0.0)
    if top6 >= max(top1 * 2.2, top1 + 0.16):
        return "top6_cover", stats
    if top1 >= max(0.14, top6 * 0.72):
        return "top1_strict", stats
    return "top1_safe", stats


def _blend_prediction_feedback_items(*weighted_feedbacks):
    sections = ("special", "normal", "color", "zodiac", "parity")
    weighted_items = []

    for item in weighted_feedbacks:
        if not item:
            continue
        if isinstance(item, tuple):
            feedback, explicit_weight = item
        else:
            feedback, explicit_weight = item, None
        if not feedback:
            continue

        confidence = _safe_float(feedback.get("confidence", 0.0))
        samples = max(0, int(feedback.get("samples", 0) or 0))
        sample_weight = _clamp(samples / 60.0, 0.2, 1.0) if samples > 0 else 0.2
        base_weight = _safe_float(explicit_weight, 1.0) if explicit_weight is not None else 1.0
        effective_weight = max(0.05, base_weight * max(0.15, confidence) * sample_weight)
        weighted_items.append((feedback, effective_weight, confidence, samples))

    if not weighted_items:
        return {
            "special": {},
            "normal": {},
            "color": {},
            "zodiac": {},
            "parity": {},
            "samples": 0,
            "confidence": 0.0,
        }

    total_weight = sum(item[1] for item in weighted_items) or 1.0
    merged = {}
    for section in sections:
        keys = set()
        for feedback, _, _, _ in weighted_items:
            keys.update((feedback.get(section) or {}).keys())
        merged[section] = {
            key: round(
                sum(
                    _safe_float((feedback.get(section) or {}).get(key, 0.5)) * weight
                    for feedback, weight, _, _ in weighted_items
                ) / total_weight,
                4,
            )
            for key in keys
        }

    merged["samples"] = sum(item[3] for item in weighted_items)
    merged["confidence"] = round(
        sum(confidence * weight for _, weight, confidence, _ in weighted_items) / total_weight,
        4,
    )
    return merged

def _personalized_predictions_enabled():
    raw = str(SystemConfig.get_config('enable_personalized_predictions', 'false')).strip().lower()
    return raw in {'true', '1', 'yes', 'on'}

def _build_prediction_feedback(region, strategy, limit=240, cutoff_period=None):
    cutoff_period = cutoff_period or _current_backtest_cutoff_period()
    predictions = _load_learning_scope_predictions(region, strategy, cutoff_period=cutoff_period)
    if limit:
        predictions = predictions[:limit]

    special_scores = Counter()
    normal_scores = Counter()
    color_scores = Counter()
    zodiac_scores = Counter()
    parity_scores = Counter()

    for idx, pred in enumerate(predictions):
        recency_weight = max(0.2, 1.0 - (idx / max(limit, 1)) * 0.75)
        actual = str(pred.actual_special_number or "").strip()
        special = str(pred.special_number or "").strip()
        predicted_zodiac = str(pred.special_zodiac or "").strip()
        normal_numbers = [n for n in _parse_csv_list(pred.normal_numbers) if n]

        actual_color = _get_color_zh(actual)
        actual_zodiac = str(pred.actual_special_zodiac or "").strip()
        actual_parity = _get_parity_zh(actual)
        predicted_parity = _get_parity_zh(special)
        zodiac_hit = bool(
            predicted_zodiac and actual_zodiac and predicted_zodiac == actual_zodiac
        )
        parity_hit = bool(
            predicted_parity and actual_parity and predicted_parity == actual_parity
        )

        if special and actual and special == actual:
            special_scores[special] += 2.4 * recency_weight
            normal_scores[special] += 0.6 * recency_weight
            if actual_color:
                color_scores[actual_color] += 1.6 * recency_weight
            if actual_zodiac:
                zodiac_scores[actual_zodiac] += 1.6 * recency_weight
            if actual_parity:
                parity_scores[actual_parity] += 1.35 * recency_weight
        elif actual and actual in normal_numbers:
            normal_scores[actual] += 1.4 * recency_weight
            if special:
                special_scores[special] -= 0.75 * recency_weight
            if actual_color:
                color_scores[actual_color] += 1.0 * recency_weight
            if actual_zodiac:
                zodiac_scores[actual_zodiac] += 1.0 * recency_weight
            if zodiac_hit:
                zodiac_scores[actual_zodiac] += 0.55 * recency_weight
            if actual_parity:
                parity_scores[actual_parity] += 0.9 * recency_weight
            if parity_hit:
                parity_scores[actual_parity] += 0.45 * recency_weight
        elif zodiac_hit:
            if special:
                special_scores[special] += 0.25 * recency_weight
            if actual_zodiac:
                zodiac_scores[actual_zodiac] += 1.25 * recency_weight
            if actual_color:
                color_scores[actual_color] += 0.35 * recency_weight
            if actual_parity:
                parity_scores[actual_parity] += 0.35 * recency_weight
        elif parity_hit:
            if special:
                special_scores[special] += 0.12 * recency_weight
            if actual_parity:
                parity_scores[actual_parity] += 1.0 * recency_weight
            if actual_color:
                color_scores[actual_color] += 0.18 * recency_weight
        else:
            if special:
                special_scores[special] -= 0.95 * recency_weight
                special_scores[special] -= 0.65 * recency_weight
            for number in normal_numbers:
                normal_scores[number] -= 0.18 * recency_weight
            if predicted_zodiac:
                zodiac_scores[predicted_zodiac] -= 0.4 * recency_weight
            if predicted_parity:
                parity_scores[predicted_parity] -= 0.28 * recency_weight

    confidence = _feedback_confidence(len(predictions))

    return {
        "special": _normalize_signed_metric_map({str(i): special_scores.get(str(i), 0.0) for i in range(1, 50)}),
        "normal": _normalize_signed_metric_map({str(i): normal_scores.get(str(i), 0.0) for i in range(1, 50)}),
        "color": _normalize_signed_metric_map({color: color_scores.get(color, 0.0) for color in ("红", "蓝", "绿")}),
        "zodiac": _normalize_signed_metric_map(dict(zodiac_scores)),
        "parity": _normalize_signed_metric_map({parity: parity_scores.get(parity, 0.0) for parity in ("单", "双")}),
        "samples": len(predictions),
        "confidence": confidence,
    }

def _build_attribute_preferences(data, region, feedback, year, apply_recent_zodiac_cooldown=True):
    color_counter = analyze_special_color_frequency(data, region)
    zodiac_counter = analyze_special_zodiac_frequency(data, region, year)
    parity_counter = analyze_special_parity_frequency(data)
    color_scores = _normalize_metric_map({color: color_counter.get(color, 0) for color in ("红", "蓝", "绿")})
    zodiac_scores = _normalize_metric_map(dict(zodiac_counter))
    parity_scores = _normalize_metric_map({parity: parity_counter.get(parity, 0) for parity in ("单", "双")})

    feedback_color = feedback.get("color") or {}
    feedback_zodiac = feedback.get("zodiac") or {}
    feedback_parity = feedback.get("parity") or {}
    feedback_confidence = float(feedback.get("confidence") or 0.0)
    feedback_weight = round(0.15 + 0.35 * feedback_confidence, 4)
    history_weight = round(1.0 - feedback_weight, 4)
    merged_color = {
        color: round(
            color_scores.get(color, 0.0) * history_weight +
            max(0.0, (feedback_color.get(color, 0.5) - 0.5) * 2) * feedback_weight,
            4
        )
        for color in ("红", "蓝", "绿")
    }
    zodiac_keys = set(zodiac_scores) | set(feedback_zodiac)
    merged_zodiac = {
        zodiac: round(
            zodiac_scores.get(zodiac, 0.0) * history_weight +
            max(0.0, (feedback_zodiac.get(zodiac, 0.5) - 0.5) * 2) * feedback_weight,
            4
        )
        for zodiac in zodiac_keys
    }
    if apply_recent_zodiac_cooldown:
        recent_records = list(data or [])[:6]
        recent_zodiacs = []
        number_to_zodiac = _get_number_to_zodiac_map(year)
        for record in recent_records:
            sno = str(record.get("sno") or "").strip()
            if not sno:
                continue
            zodiac = number_to_zodiac.get(sno, "") if number_to_zodiac else ""
            if not zodiac:
                zodiac = str(record.get("sno_zodiac") or "").strip()
            if zodiac:
                recent_zodiacs.append(zodiac)
        recent_zodiac_counter = Counter(recent_zodiacs)
        if recent_zodiacs:
            latest_zodiac = recent_zodiacs[0]
            recent_pair = set(recent_zodiacs[:2])
            for zodiac in list(merged_zodiac.keys()):
                cooled = merged_zodiac.get(zodiac, 0.0)
                if zodiac == latest_zodiac:
                    cooled *= 0.38
                elif zodiac in recent_pair:
                    cooled *= 0.62
                heat = int(recent_zodiac_counter.get(zodiac, 0) or 0)
                if heat >= 2:
                    cooled *= 0.58
                elif heat == 1:
                    cooled *= 0.9
                merged_zodiac[zodiac] = round(max(0.0, cooled), 4)
    parity_keys = set(parity_scores) | set(feedback_parity)
    merged_parity = {
        parity: round(
            parity_scores.get(parity, 0.0) * history_weight +
            max(0.0, (feedback_parity.get(parity, 0.5) - 0.5) * 2) * feedback_weight,
            4
        )
        for parity in parity_keys
    }
    return merged_color, merged_zodiac, merged_parity

def _rank_numbers(number_scores, candidates=None, exclude=None):
    exclude_set = {int(num) for num in (exclude or [])}
    if candidates is None:
        candidates = range(1, 50)
    ranked = []
    for number in candidates:
        if number in exclude_set:
            continue
        ranked.append((number, number_scores.get(number, 0.0)))
    ranked.sort(key=lambda item: (item[1], -abs(item[0] - 25), -item[0]), reverse=True)
    return [number for number, _ in ranked]


def _build_parity_target_counts(parity_pref, total=6):
    total = max(1, int(total or 0))
    odd_pref = float((parity_pref or {}).get("单", 0.5) or 0.5)
    even_pref = float((parity_pref or {}).get("双", 0.5) or 0.5)
    pref_sum = odd_pref + even_pref
    if pref_sum <= 0:
        odd_target = total // 2
    else:
        odd_target = int(round(total * (odd_pref / pref_sum)))
    odd_target = _clamp(odd_target, max(1, total // 3), min(total - 1, total - (total // 3)))
    return {"单": odd_target, "双": total - odd_target}


def _rebalance_selected_numbers_by_parity(selected_numbers, ranked_numbers, score_map, parity_pref, count=6):
    selected = [int(number) for number in (selected_numbers or []) if str(number).isdigit()]
    ranked = [int(number) for number in (ranked_numbers or []) if str(number).isdigit()]
    if not selected:
        return selected

    targets = _build_parity_target_counts(parity_pref, total=count)
    parity_counts = Counter(_get_parity_zh(number) for number in selected if _get_parity_zh(number))

    for overflow_parity in ("单", "双"):
        under_parity = "双" if overflow_parity == "单" else "单"
        while parity_counts.get(overflow_parity, 0) > targets.get(overflow_parity, 0):
            replacement = next(
                (
                    number for number in ranked
                    if number not in selected and _get_parity_zh(number) == under_parity
                ),
                None,
            )
            if replacement is None:
                break
            removable = sorted(
                [number for number in selected if _get_parity_zh(number) == overflow_parity],
                key=lambda number: (float(score_map.get(number, 0.0)), number),
            )
            if not removable:
                break
            removed = removable[0]
            selected[selected.index(removed)] = replacement
            parity_counts[overflow_parity] -= 1
            parity_counts[under_parity] += 1

    return sorted(selected[:count])

def _take_ranked(ranked_numbers, count, exclude=None):
    exclude_set = {int(num) for num in (exclude or [])}
    chosen = []
    for number in ranked_numbers:
        if number in exclude_set or number in chosen:
            continue
        chosen.append(number)
        if len(chosen) >= count:
            break
    return chosen

def _stable_hash_int(*parts):
    raw = "||".join(str(part) for part in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16)

def _take_personalized_ranked(ranked_numbers, count, variation_key=None, exclude=None, chunk_size=3, window_size=None):
    if not variation_key:
        return _take_ranked(ranked_numbers, count, exclude=exclude)

    exclude_set = {int(num) for num in (exclude or [])}
    filtered = [number for number in ranked_numbers if number not in exclude_set]
    if not filtered or count <= 0:
        return []

    window_size = min(len(filtered), int(window_size or max(count * 3, chunk_size)))
    head = filtered[:window_size]
    tail = filtered[window_size:]

    personalized = []
    for index in range(0, len(head), chunk_size):
        chunk = head[index:index + chunk_size]
        if not chunk:
            continue
        shift = _stable_hash_int(variation_key, index, len(chunk)) % len(chunk)
        personalized.extend(chunk[shift:] + chunk[:shift])

    chosen = []
    for number in personalized + tail:
        if number in chosen:
            continue
        chosen.append(number)
        if len(chosen) >= count:
            break
    return chosen

def _build_local_recommendation_text(strategy, config, normal, special, feedback):
    strategy_name = _get_strategy_label(strategy)
    accuracy = round(float(config.get("last_accuracy") or 0.0) * 100, 1)
    samples = int(feedback.get("samples") or 0)
    confidence = round(float(feedback.get("confidence") or 0.0) * 100, 1)
    return _build_special_focus_text(
        special,
        normal,
        strategy_name=strategy_name,
        accuracy=accuracy,
        samples=samples,
        confidence=confidence,
    )


def _extract_draw_numbers(record, include_special=True):
    numbers = []
    for raw_number in list((record or {}).get("no") or []):
        try:
            parsed = int(str(raw_number).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= parsed <= 49:
            numbers.append(parsed)
    if include_special:
        try:
            special = int(str((record or {}).get("sno") or "").strip())
        except (TypeError, ValueError):
            special = 0
        if 1 <= special <= 49:
            numbers.append(special)
    return _dedupe_keep_order(numbers)


def _safe_draw_special(record):
    try:
        value = int(str((record or {}).get("sno") or "").strip())
    except (TypeError, ValueError):
        return 0
    return value if 1 <= value <= 49 else 0


def _markov_zone(number):
    try:
        value = int(number)
    except (TypeError, ValueError):
        return ""
    if value <= 16:
        return "low"
    if value <= 33:
        return "mid"
    return "high"


def _markov_tail(number):
    try:
        return str(int(number) % 10)
    except (TypeError, ValueError):
        return ""


def _markov_attribute_state(number, zodiac_map=None):
    if not number:
        return {}
    key = str(number)
    return {
        "color": _get_color_zh(number) or "",
        "parity": _get_parity_zh(number) or "",
        "zone": _markov_zone(number),
        "tail": _markov_tail(number),
        "zodiac": (zodiac_map or {}).get(key, ""),
    }


def _score_markov_attribute_transition(candidate, latest_special, attribute_profile, zodiac_map=None):
    if not latest_special or not attribute_profile:
        return 0.0
    source_state = _markov_attribute_state(latest_special, zodiac_map=zodiac_map)
    candidate_state = _markov_attribute_state(candidate, zodiac_map=zodiac_map)
    total = 0.0
    active_attrs = 0
    for attr, source_value in source_state.items():
        if not source_value:
            continue
        targets = ((attribute_profile.get(attr) or {}).get(source_value) or {})
        target_value = candidate_state.get(attr)
        if target_value and targets:
            total += float(targets.get(target_value, 0.0) or 0.0)
            active_attrs += 1
    if active_attrs <= 0:
        return 0.0
    return round(total / active_attrs, 6)


def _build_markov_failure_profile(region, limit=180, cutoff_period=None):
    predictions = _load_learning_scope_predictions(
        region,
        "markov",
        limit=limit,
        minimum_samples=0,
        cutoff_period=cutoff_period,
    )
    candidate_scores = Counter()
    source_scores = Counter()
    samples = 0
    for idx, pred in enumerate(predictions):
        if not getattr(pred, "is_result_updated", False) or not getattr(pred, "actual_special_number", None):
            continue
        samples += 1
        recency_weight = max(0.2, 1.0 - (idx / max(limit, 1)) * 0.75)
        quality = _score_prediction_outcome(pred)
        metadata = _deserialize_prediction_metadata(getattr(pred, "prediction_metadata", ""))
        predicted_special = str(getattr(pred, "special_number", "") or "").strip()
        normal_numbers = [item for item in _parse_csv_list(getattr(pred, "normal_numbers", "")) if item]
        latest_sources = [str(item) for item in (metadata.get("markov_latest_sources") or [])]
        top_numbers = [str((item or {}).get("number")) for item in (metadata.get("markov_top_transitions") or []) if (item or {}).get("number")]
        signal_numbers = _dedupe_keep_order([predicted_special] + normal_numbers + top_numbers)
        if quality >= 0.58:
            for number in signal_numbers:
                candidate_scores[str(number)] += quality * recency_weight
            for source in latest_sources:
                source_scores[str(source)] += quality * recency_weight * 0.45
        else:
            for number in signal_numbers:
                candidate_scores[str(number)] -= (1.0 - quality) * recency_weight
            for source in latest_sources:
                source_scores[str(source)] -= (1.0 - quality) * recency_weight * 0.55
    return {
        "candidate": _normalize_signed_metric_map({str(i): candidate_scores.get(str(i), 0.0) for i in range(1, 50)}),
        "source": _normalize_signed_metric_map({str(i): source_scores.get(str(i), 0.0) for i in range(1, 50)}),
        "samples": samples,
        "confidence": _feedback_confidence(samples, full_confidence=60),
    }


def _blend_markov_with_anchor_weights(region, weights, cutoff_period=None):
    resolved = dict(weights or {})
    markov_accuracy, markov_total = _calculate_strategy_accuracy(
        region,
        "markov",
        limit=36,
        cutoff_period=cutoff_period,
    )
    anchors = []
    for strategy in ("ml", "hybrid", "balanced"):
        accuracy, total = _calculate_strategy_accuracy(
            region,
            strategy,
            limit=36,
            cutoff_period=cutoff_period,
        )
        if total > 0:
            anchors.append((accuracy, total))
    if markov_total <= 0 or not anchors:
        return resolved, {"active": False, "reason": "insufficient_samples"}
    anchor_accuracy = sum(acc * min(total, 36) for acc, total in anchors) / max(sum(min(total, 36) for _, total in anchors), 1)
    gap = anchor_accuracy - markov_accuracy
    confidence = _clamp(markov_total / 18.0, 0.2, 1.0)
    if gap <= 0.015:
        resolved["transition"] = round(_clamp(float(resolved.get("transition", 1.0)) + 0.08 * confidence, 0.75, 1.9), 2)
        resolved["second_order"] = round(_clamp(float(resolved.get("second_order", 0.72)) + 0.06 * confidence, 0.0, 1.35), 2)
        return resolved, {"active": True, "mode": "markov_strong", "gap": round(gap * 100, 2)}
    if gap >= 0.06:
        resolved["transition"] = round(_clamp(float(resolved.get("transition", 1.0)) - 0.24 * confidence, 0.65, 1.5), 2)
        resolved["second_order"] = round(_clamp(float(resolved.get("second_order", 0.72)) - 0.16 * confidence, 0.0, 1.0), 2)
        resolved["phase_transition"] = round(_clamp(float(resolved.get("phase_transition", 0.55)) - 0.1 * confidence, 0.0, 1.0), 2)
        resolved["feedback"] = round(_clamp(float(resolved.get("feedback", 0.85)) + 0.16 * confidence, 0.45, 1.45), 2)
        resolved["normal"] = round(_clamp(float(resolved.get("normal", 0.22)) + 0.08 * confidence, 0.12, 0.55), 2)
        return resolved, {"active": True, "mode": "anchor_guard", "gap": round(gap * 100, 2)}
    return resolved, {"active": False, "mode": "neutral", "gap": round(gap * 100, 2)}


def _markov_probability(numerator, denominator, min_samples=3):
    numerator = float(numerator or 0.0)
    denominator = float(denominator or 0.0)
    if denominator <= 0:
        return 0.0
    prior_strength = max(float(min_samples or 1), 1.0)
    uniform_probability = 1.0 / 49.0
    smoothed = (numerator + uniform_probability * prior_strength) / (denominator + prior_strength)
    confidence = _clamp(denominator / (denominator + prior_strength), 0.15, 1.0)
    return smoothed * confidence


def _build_markov_ml_distillation(region, cutoff_period=None):
    ml_feedback = _build_prediction_feedback(region, "ml", limit=160, cutoff_period=cutoff_period)
    confidence = float(ml_feedback.get("confidence") or 0.0)
    return {
        "special": ml_feedback.get("special") or {},
        "normal": ml_feedback.get("normal") or {},
        "confidence": confidence,
        "samples": int(ml_feedback.get("samples") or 0),
    }


def _build_markov_anchor_profile(region, cutoff_period=None):
    weighted_items = []
    for strategy in ("ml", "hybrid", "balanced", "trend"):
        rates = _calculate_strategy_hit_rates(region, strategy, limit=48, cutoff_period=cutoff_period)
        total = int(rates.get("total") or 0)
        if total <= 0:
            continue
        special_rate = float(rates.get("top1") or 0.0)
        normal_rate = float(rates.get("top6") or 0.0)
        quality = special_rate + normal_rate * 0.35
        if quality <= 0:
            continue
        feedback = _build_prediction_feedback(region, strategy, limit=160, cutoff_period=cutoff_period)
        confidence = float(feedback.get("confidence") or 0.0)
        weight = quality * _clamp(total / 24.0, 0.25, 1.0) * max(confidence, 0.2)
        weighted_items.append((feedback, weight, strategy, rates))

    if not weighted_items:
        return {"active": False, "confidence": 0.0, "samples": 0, "special": {}, "normal": {}, "strategies": []}

    total_weight = sum(item[1] for item in weighted_items) or 1.0
    merged = {"special": {}, "normal": {}}
    for section in ("special", "normal"):
        keys = {str(i) for i in range(1, 50)}
        merged[section] = {
            key: round(
                sum(
                    float((feedback.get(section) or {}).get(key, 0.5) or 0.5) * weight
                    for feedback, weight, _, _ in weighted_items
                ) / total_weight,
                4,
            )
            for key in keys
        }

    samples = sum(int((feedback or {}).get("samples") or 0) for feedback, _, _, _ in weighted_items)
    confidence = _clamp(
        sum(float((feedback or {}).get("confidence") or 0.0) * weight for feedback, weight, _, _ in weighted_items) / total_weight,
        0.0,
        1.0,
    )
    return {
        "active": True,
        "confidence": round(confidence, 4),
        "samples": samples,
        "special": merged["special"],
        "normal": merged["normal"],
        "strategies": [
            {
                "strategy": strategy,
                "weight": round(weight / total_weight, 4),
                "top1": round(float(rates.get("top1") or 0.0) * 100, 2),
                "top6": round(float(rates.get("top6") or 0.0) * 100, 2),
                "total": int(rates.get("total") or 0),
            }
            for _, weight, strategy, rates in weighted_items
        ],
    }


def _score_markov_combo_shape(numbers, data, region, year):
    selected = [int(number) for number in (numbers or []) if str(number).isdigit()]
    if not selected:
        return {"score": 0.0, "diagnostics": {}}
    recent = list(data or [])[:36]
    if not recent:
        return {"score": 0.0, "diagnostics": {}}
    zone_counts = Counter()
    color_counts = Counter()
    parity_counts = Counter()
    tail_spreads = []
    for record in recent:
        draw_numbers = _extract_draw_numbers(record, include_special=False)
        if not draw_numbers:
            continue
        zone_counts[len(set(_markov_zone(number) for number in draw_numbers if _markov_zone(number)))] += 1
        color_counts[len(set(_get_color_zh(number) for number in draw_numbers if _get_color_zh(number)))] += 1
        parity_counts[len(set(_get_parity_zh(number) for number in draw_numbers if _get_parity_zh(number)))] += 1
        tail_spreads.append(len(set(int(number) % 10 for number in draw_numbers)))

    zone_spread = len(set(_markov_zone(number) for number in selected if _markov_zone(number)))
    color_spread = len(set(_get_color_zh(number) for number in selected if _get_color_zh(number)))
    parity_spread = len(set(_get_parity_zh(number) for number in selected if _get_parity_zh(number)))
    tail_spread = len(set(int(number) % 10 for number in selected))
    avg_tail_spread = (sum(tail_spreads) / len(tail_spreads)) if tail_spreads else tail_spread
    score = 0.0
    score += 0.18 if zone_counts and zone_spread == zone_counts.most_common(1)[0][0] else -0.04
    score += 0.14 if color_counts and color_spread == color_counts.most_common(1)[0][0] else -0.03
    score += 0.1 if parity_counts and parity_spread == parity_counts.most_common(1)[0][0] else -0.02
    score += max(-0.08, 0.12 - abs(tail_spread - avg_tail_spread) * 0.035)
    return {
        "score": round(score, 6),
        "diagnostics": {
            "zone_spread": zone_spread,
            "color_spread": color_spread,
            "parity_spread": parity_spread,
            "tail_spread": tail_spread,
            "avg_tail_spread": round(avg_tail_spread, 2),
        },
    }


def _build_local_trend_reversal_profile(short_norm, medium_norm, long_norm):
    reversal_scores = {}
    active_count = 0
    for number in range(1, 50):
        key = str(number)
        short_value = float(short_norm.get(key, 0.0) or 0.0)
        medium_value = float(medium_norm.get(key, 0.0) or 0.0)
        long_value = float(long_norm.get(key, 0.0) or 0.0)
        if medium_value <= 0 and long_value <= 0:
            reversal_scores[key] = 0.0
            continue
        momentum_drop = max(0.0, medium_value - short_value)
        long_support = max(medium_value, long_value)
        score = _clamp(momentum_drop * 0.65 + max(0.0, long_support - short_value) * 0.25, 0.0, 0.42)
        if score >= 0.08:
            active_count += 1
        reversal_scores[key] = round(score, 6)
    return {
        "scores": reversal_scores,
        "active": active_count >= 3,
        "active_count": active_count,
    }


def _build_local_cold_trap_profile(cold_norm, overdue_norm, medium_norm, normal_norm, feedback=None, feedback_confidence=0.0):
    trap_scores = {}
    active_count = 0
    feedback = feedback or {}
    for number in range(1, 50):
        key = str(number)
        cold_score = float(cold_norm.get(key, 0.0) or 0.0)
        overdue_score = float(overdue_norm.get(key, 0.0) or 0.0)
        medium_support = float(medium_norm.get(key, 0.0) or 0.0)
        normal_support = float(normal_norm.get(key, 0.0) or 0.0)
        learned_support = (
            (float((feedback.get("special") or {}).get(key, 0.5) or 0.5) - 0.5) * 0.7 +
            (float((feedback.get("normal") or {}).get(key, 0.5) or 0.5) - 0.5) * 0.3
        ) * float(feedback_confidence or 0.0)
        raw_trap = max(0.0, (cold_score * 0.55 + overdue_score * 0.45) - (medium_support * 0.42 + normal_support * 0.28 + max(0.0, learned_support) * 0.9))
        score = _clamp(raw_trap, 0.0, 0.38)
        if score >= 0.14:
            active_count += 1
        trap_scores[key] = round(score, 6)
    return {
        "scores": trap_scores,
        "active": active_count >= 4,
        "active_count": active_count,
    }


def _rebalance_local_combo_shape(selected_numbers, ranked_numbers, score_map, recent_data, region, year):
    selected = sorted(_dedupe_keep_order([int(number) for number in selected_numbers if str(number).isdigit()])[:6])
    if len(selected) < 6:
        return selected, {"score": 0.0, "adjusted": False}
    profile = _score_markov_combo_shape(selected, recent_data, region, year)
    if float(profile.get("score") or 0.0) >= -0.02:
        profile["adjusted"] = False
        return selected, profile

    best_numbers = selected
    best_profile = profile
    candidates = [number for number in ranked_numbers[:24] if number not in selected]
    for replace_index, old_number in enumerate(list(selected)):
        base = [number for number in selected if number != old_number]
        for candidate in candidates:
            trial = sorted(_dedupe_keep_order(base + [candidate])[:6])
            if len(trial) < 6:
                continue
            trial_profile = _score_markov_combo_shape(trial, recent_data, region, year)
            trial_score = float(trial_profile.get("score") or 0.0) + float(score_map.get(candidate, 0.0) or 0.0) * 0.015
            best_score = float(best_profile.get("score") or 0.0)
            if trial_score > best_score + 0.015:
                best_numbers = trial
                best_profile = trial_profile
                best_profile["adjusted"] = True
                best_profile["replaced"] = {"from": old_number, "to": candidate, "index": replace_index}
    return sorted(best_numbers), best_profile


def _build_markov_special_transition_profile(data, window=80, decay=0.985, year=None, min_samples=3):
    ordered = list(reversed((data or [])[:max(2, int(window or 80))]))
    cache_key = (
        _runtime_draws_signature(data, limit=max(2, int(window or 80))),
        int(window or 80),
        round(float(decay or 0.985), 6),
        int(year or _infer_draw_year(data) or 0),
        int(min_samples or 3),
    )
    cached = _runtime_cache_get("markov_special_profile", cache_key)
    if cached is not None:
        return cached

    direct_transitions = {number: Counter() for number in range(1, 50)}
    direct_totals = Counter()
    second_order_transitions = Counter()
    second_order_totals = Counter()
    attribute_transitions = {
        "color": {},
        "parity": {},
        "zone": {},
        "tail": {},
        "zodiac": {},
    }
    zodiac_map = _get_number_to_zodiac_map(year or _infer_draw_year(data))

    for idx in range(1, len(ordered)):
        previous_previous = ordered[idx - 2] if idx >= 2 else None
        previous_special = _safe_draw_special(ordered[idx - 1])
        current_special = _safe_draw_special(ordered[idx])
        if not previous_special or not current_special:
            continue

        recency_weight = float(decay or 0.985) ** max(0, len(ordered) - idx - 1)
        direct_totals[previous_special] += recency_weight
        direct_transitions[previous_special][current_special] += recency_weight

        previous_state = _markov_attribute_state(previous_special, zodiac_map=zodiac_map)
        current_state = _markov_attribute_state(current_special, zodiac_map=zodiac_map)
        for attr, source_value in previous_state.items():
            target_value = current_state.get(attr)
            if not source_value or not target_value:
                continue
            attr_bucket = attribute_transitions.setdefault(attr, {})
            attr_bucket.setdefault(source_value, Counter())[target_value] += recency_weight

        if previous_previous:
            first_special = _safe_draw_special(previous_previous)
            if first_special:
                pair = (int(first_special), int(previous_special))
                pair_weight = recency_weight * 1.12
                second_order_totals[pair] += pair_weight
                second_order_transitions[(pair, int(current_special))] += pair_weight

    latest_special = _safe_draw_special(data[0]) if data else 0
    latest_previous_special = _safe_draw_special(data[1]) if len(data or []) > 1 else 0
    direct_sample_total = float(direct_totals.get(latest_special, 0.0) or 0.0)
    pair = (int(latest_previous_special), int(latest_special)) if latest_previous_special and latest_special else None
    second_order_sample_total = float(second_order_totals.get(pair, 0.0) or 0.0) if pair else 0.0
    direct_confidence = _clamp(direct_sample_total / max(float(min_samples or 1), 1.0), 0.0, 1.0)
    second_order_confidence = _clamp(second_order_sample_total / max(float(min_samples or 1) * 2.0, 1.0), 0.0, 1.0)

    direct_scores = {}
    second_order_scores = {}
    for candidate in range(1, 50):
        direct_scores[str(candidate)] = _markov_probability(
            direct_transitions[latest_special].get(candidate, 0.0),
            direct_totals.get(latest_special, 0.0),
            min_samples=min_samples,
        ) if latest_special else 0.0
        second_order_scores[str(candidate)] = _markov_probability(
            second_order_transitions.get((pair, candidate), 0.0),
            second_order_totals.get(pair, 0.0),
            min_samples=min_samples,
        ) if pair else 0.0

    attribute_profile = {}
    for attr, source_map in attribute_transitions.items():
        attribute_profile[attr] = {}
        for source_value, counter in source_map.items():
            total = sum(counter.values()) or 1.0
            attribute_profile[attr][source_value] = {
                target_value: round(value / total, 6)
                for target_value, value in counter.items()
            }

    return _runtime_cache_set("markov_special_profile", cache_key, {
        "latest_special": latest_special,
        "latest_previous_special": latest_previous_special,
        "direct_scores": _normalize_metric_map(direct_scores),
        "second_order_scores": _normalize_metric_map(second_order_scores),
        "attribute_profile": attribute_profile,
        "direct_confidence": round(direct_confidence, 6),
        "second_order_confidence": round(second_order_confidence, 6),
        "direct_sample_total": round(direct_sample_total, 3),
        "second_order_sample_total": round(second_order_sample_total, 3),
    })


def _build_markov_transition_profile(data, window=80, decay=0.985, source_special_weight=1.28, year=None, min_samples=3):
    ordered = list(reversed((data or [])[:max(2, int(window or 80))]))
    cache_key = (
        _runtime_draws_signature(data, limit=max(2, int(window or 80))),
        int(window or 80),
        round(float(decay or 0.985), 6),
        round(float(source_special_weight or 1.0), 6),
        int(year or _infer_draw_year(data) or 0),
        int(min_samples or 3),
    )
    cached = _runtime_cache_get("markov_transition_profile", cache_key)
    if cached is not None:
        return cached

    transitions = {number: Counter() for number in range(1, 50)}
    special_transitions = {number: Counter() for number in range(1, 50)}
    second_order_transitions = Counter()
    second_order_totals = Counter()
    phase_transitions = {}
    phase_totals = {}
    attribute_transitions = {
        "color": {},
        "parity": {},
        "zone": {},
        "tail": {},
        "zodiac": {},
    }
    source_totals = Counter()
    special_source_totals = Counter()
    target_totals = Counter()
    special_target_totals = Counter()
    target_total_weight = 0.0
    special_target_total_weight = 0.0
    zodiac_map = _get_number_to_zodiac_map(year or _infer_draw_year(data))

    for idx in range(1, len(ordered)):
        previous_previous = ordered[idx - 2] if idx >= 2 else None
        previous = ordered[idx - 1]
        current = ordered[idx]
        source_numbers = _extract_draw_numbers(previous, include_special=True)
        target_numbers = _extract_draw_numbers(current, include_special=True)
        previous_special = _safe_draw_special(previous)
        current_special = _safe_draw_special(current)
        if not source_numbers or not target_numbers:
            continue

        recency_weight = float(decay or 0.985) ** max(0, len(ordered) - idx - 1)
        phase_history = list(reversed(ordered[max(0, idx - 12):idx]))
        phase_label = str(_classify_ai_market_phase(phase_history, window=min(12, max(4, len(phase_history)))).get("label") or "neutral")
        if phase_label not in phase_transitions:
            phase_transitions[phase_label] = {number: Counter() for number in range(1, 50)}
            phase_totals[phase_label] = Counter()
        for target in target_numbers:
            target_totals[target] += recency_weight
            target_total_weight += recency_weight
        if 1 <= current_special <= 49:
            special_target_totals[current_special] += recency_weight
            special_target_total_weight += recency_weight
        for source in source_numbers:
            source_weight = recency_weight * (float(source_special_weight or 1.0) if source == previous_special else 1.0)
            source_totals[source] += source_weight
            phase_totals[phase_label][source] += source_weight
            for target in target_numbers:
                transitions[source][target] += source_weight
                phase_transitions[phase_label][source][target] += source_weight
            if 1 <= current_special <= 49:
                special_source_totals[source] += source_weight
                special_transitions[source][current_special] += source_weight

        if previous_previous:
            prior_sources = _extract_draw_numbers(previous_previous, include_special=True)
            for first_source in prior_sources:
                for second_source in source_numbers:
                    pair = (int(first_source), int(second_source))
                    pair_weight = recency_weight * (1.18 if second_source == previous_special else 1.0)
                    second_order_totals[pair] += pair_weight
                    for target in target_numbers:
                        second_order_transitions[(pair, int(target))] += pair_weight

        if previous_special and current_special:
            previous_state = _markov_attribute_state(previous_special, zodiac_map=zodiac_map)
            current_state = _markov_attribute_state(current_special, zodiac_map=zodiac_map)
            for attr, source_value in previous_state.items():
                target_value = current_state.get(attr)
                if not source_value or not target_value:
                    continue
                attr_bucket = attribute_transitions.setdefault(attr, {})
                attr_bucket.setdefault(source_value, Counter())[target_value] += recency_weight

    latest_sources = _extract_draw_numbers(data[0], include_special=True) if data else []
    latest_second_sources = _extract_draw_numbers(data[1], include_special=True) if len(data or []) > 1 else []
    latest_special = _safe_draw_special(data[0]) if data else 0
    latest_phase = str(_classify_ai_market_phase(list(data or [])[:12], window=min(12, max(4, len(data or [])))).get("label") or "neutral")
    second_order_sample_total = 0.0
    second_order_pair_count = 0
    for first_source in latest_second_sources:
        for second_source in latest_sources:
            pair = (int(first_source), int(second_source))
            pair_samples = float(second_order_totals.get(pair, 0.0) or 0.0)
            if pair_samples > 0:
                second_order_sample_total += pair_samples
                second_order_pair_count += 1
    second_order_avg_samples = second_order_sample_total / max(second_order_pair_count, 1)
    second_order_confidence = _clamp(
        second_order_avg_samples / max(float(min_samples or 1) * 2.0, 1.0),
        0.0,
        1.0,
    )
    transition_scores = {}
    special_transition_scores = {}
    transition_lift_scores = {}
    special_transition_lift_scores = {}
    second_order_scores = {}
    phase_transition_scores = {}
    latest_source_count = max(len(latest_sources), 1)
    for candidate in range(1, 50):
        total = 0.0
        special_total = 0.0
        second_total = 0.0
        phase_total = 0.0
        base_probability = _markov_probability(
            target_totals.get(candidate, 0.0),
            target_total_weight,
            min_samples=min_samples,
        )
        special_base_probability = _markov_probability(
            special_target_totals.get(candidate, 0.0),
            special_target_total_weight,
            min_samples=min_samples,
        )
        for source in latest_sources:
            total += _markov_probability(transitions[source].get(candidate, 0.0), source_totals.get(source, 0.0), min_samples=min_samples)
            special_total += _markov_probability(special_transitions[source].get(candidate, 0.0), special_source_totals.get(source, 0.0), min_samples=min_samples)
            if latest_phase in phase_transitions:
                phase_total += _markov_probability(phase_transitions[latest_phase][source].get(candidate, 0.0), phase_totals[latest_phase].get(source, 0.0), min_samples=min_samples)
        for first_source in latest_second_sources:
            for second_source in latest_sources:
                pair = (int(first_source), int(second_source))
                second_total += _markov_probability(second_order_transitions.get((pair, candidate), 0.0), second_order_totals.get(pair, 0.0), min_samples=min_samples)
        transition_scores[str(candidate)] = total
        special_transition_scores[str(candidate)] = special_total
        transition_lift_scores[str(candidate)] = max(0.0, (total / latest_source_count) - base_probability)
        special_transition_lift_scores[str(candidate)] = max(0.0, (special_total / latest_source_count) - special_base_probability)
        second_order_scores[str(candidate)] = second_total
        phase_transition_scores[str(candidate)] = phase_total

    attribute_profile = {}
    for attr, source_map in attribute_transitions.items():
        attribute_profile[attr] = {}
        for source_value, counter in source_map.items():
            total = sum(counter.values()) or 1.0
            attribute_profile[attr][source_value] = {
                target_value: round(value / total, 6)
                for target_value, value in counter.items()
            }

    normalized_transition = _normalize_metric_map(transition_scores)
    normalized_second = _normalize_metric_map(second_order_scores)
    normalized_phase = _normalize_metric_map(phase_transition_scores)
    top_support = {}
    for candidate in range(1, 50):
        candidate_support = []
        for source in latest_sources:
            source_probability = _markov_probability(transitions[source].get(candidate, 0.0), source_totals.get(source, 0.0), min_samples=min_samples)
            if source_probability > 0:
                candidate_support.append({
                    "type": "one_step",
                    "source": source,
                    "target": candidate,
                    "score": round(source_probability * 100, 2),
                    "samples": round(float(source_totals.get(source, 0.0) or 0.0), 2),
                })
        for first_source in latest_second_sources:
            for second_source in latest_sources:
                pair = (int(first_source), int(second_source))
                pair_probability = _markov_probability(second_order_transitions.get((pair, candidate), 0.0), second_order_totals.get(pair, 0.0), min_samples=min_samples)
                if pair_probability > 0:
                    candidate_support.append({
                        "type": "two_step",
                        "source": [first_source, second_source],
                        "target": candidate,
                        "score": round(pair_probability * 100, 2),
                        "samples": round(float(second_order_totals.get(pair, 0.0) or 0.0), 2),
                    })
        candidate_support.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        top_support[str(candidate)] = candidate_support[:4]

    return _runtime_cache_set("markov_transition_profile", cache_key, {
        "latest_sources": latest_sources,
        "latest_second_sources": latest_second_sources,
        "latest_phase": latest_phase,
        "latest_special": latest_special,
        "transition_scores": normalized_transition,
        "special_transition_scores": _normalize_metric_map(special_transition_scores),
        "transition_lift_scores": _normalize_metric_map(transition_lift_scores),
        "special_transition_lift_scores": _normalize_metric_map(special_transition_lift_scores),
        "second_order_scores": normalized_second,
        "phase_transition_scores": normalized_phase,
        "attribute_profile": attribute_profile,
        "support_chains": top_support,
        "transition_samples": sum(1 for counter in transitions.values() if counter),
        "second_order_confidence": round(second_order_confidence, 6),
        "second_order_sample_total": round(second_order_sample_total, 3),
    })


def _predict_with_markov(data, region, variation_key=None):
    if not data:
        return _build_default_baseline_prediction()

    config = _load_strategy_config("markov", region)
    window = _clamp(int(config.get("window") or 80), 12, 160)
    pool_size = _clamp(int(config.get("pool") or 18), 8, 24)
    special_pool_size = _clamp(int(config.get("special_pool") or 10), 6, 14)
    transition_min_samples = _clamp(int(config.get("transition_min_samples") or 3), 1, 12)
    weights = dict(config.get("weights") or {})
    cutoff_period = _current_backtest_cutoff_period()
    weights, markov_guard = _blend_markov_with_anchor_weights(region, weights, cutoff_period=cutoff_period)
    recent_data = data[:window]
    trend_window = min(18, len(recent_data))
    trend_data = recent_data[:trend_window] if trend_window > 0 else recent_data

    year = _infer_draw_year(recent_data)
    number_to_zodiac = _get_number_to_zodiac_map(year)
    transition_profile = _build_markov_transition_profile(
        recent_data,
        window=window,
        decay=float(config.get("transition_decay") or 0.985),
        source_special_weight=float(config.get("source_special_weight") or 1.28),
        year=year,
        min_samples=transition_min_samples,
    )
    special_profile = _build_markov_special_transition_profile(
        recent_data,
        window=window,
        decay=float(config.get("transition_decay") or 0.985),
        year=year,
        min_samples=transition_min_samples,
    )
    transition_norm = transition_profile.get("transition_scores") or {}
    special_transition_norm = transition_profile.get("special_transition_scores") or {}
    transition_lift_norm = transition_profile.get("transition_lift_scores") or {}
    special_transition_lift_norm = transition_profile.get("special_transition_lift_scores") or {}
    second_order_norm = transition_profile.get("second_order_scores") or {}
    phase_transition_norm = transition_profile.get("phase_transition_scores") or {}
    attribute_profile = transition_profile.get("attribute_profile") or {}
    second_order_confidence = float(transition_profile.get("second_order_confidence") or 0.0)
    special_direct_norm = special_profile.get("direct_scores") or {}
    special_second_order_norm = special_profile.get("second_order_scores") or {}
    special_attribute_profile = special_profile.get("attribute_profile") or {}
    special_direct_confidence = float(special_profile.get("direct_confidence") or 0.0)
    special_second_order_confidence = float(special_profile.get("second_order_confidence") or 0.0)
    hot_norm = _normalize_metric_map(analyze_special_number_frequency(recent_data))
    trend_norm = _normalize_metric_map(analyze_special_number_frequency(trend_data))
    normal_norm = _normalize_metric_map(_build_number_frequency(recent_data))
    overdue_norm = _normalize_metric_map(_build_overdue_scores(recent_data))
    feedback = _build_prediction_feedback(region, "markov", cutoff_period=cutoff_period)
    failure_profile = _build_markov_failure_profile(region, cutoff_period=cutoff_period)
    ml_distillation = _build_markov_ml_distillation(region, cutoff_period=cutoff_period) if markov_guard.get("mode") == "anchor_guard" else {"confidence": 0.0, "samples": 0, "special": {}, "normal": {}}
    anchor_profile = _build_markov_anchor_profile(region, cutoff_period=cutoff_period)
    feedback_confidence = float(feedback.get("confidence") or 0.0)
    failure_confidence = float(failure_profile.get("confidence") or 0.0)
    ml_distill_confidence = float(ml_distillation.get("confidence") or 0.0)
    anchor_confidence = float(anchor_profile.get("confidence") or 0.0)
    anchor_weight = 0.0
    if anchor_profile.get("active"):
        if markov_guard.get("mode") == "anchor_guard":
            anchor_weight = _clamp(0.35 + anchor_confidence * 0.45, 0.35, 0.8)
        elif markov_guard.get("mode") == "neutral":
            anchor_weight = _clamp(0.08 + anchor_confidence * 0.16, 0.08, 0.24)
    color_pref, zodiac_pref, parity_pref = _build_attribute_preferences(
        recent_data,
        region,
        feedback,
        year,
        apply_recent_zodiac_cooldown=True,
    )
    latest_numbers = set(_extract_draw_numbers(recent_data[0], include_special=True) if recent_data else [])
    preferred_parity = max(parity_pref.items(), key=lambda item: item[1])[0] if parity_pref else ""

    def attribute_score(number):
        return (
            float(weights.get("color", 0.0)) * color_pref.get(_get_color_zh(number), 0.0) +
            float(weights.get("zodiac", 0.0)) * zodiac_pref.get(number_to_zodiac.get(str(number), ""), 0.0) +
            float(weights.get("parity", 0.0)) * parity_pref.get(_get_parity_zh(number), 0.0)
        )

    def repeat_penalty(number):
        return float(config.get("repeat_penalty", -0.18) or -0.18) if number in latest_numbers else 0.0

    number_scores = {}
    special_scores = {}
    for number in range(1, 50):
        key = str(number)
        feedback_score = (
            (feedback.get("special", {}).get(key, 0.5) - 0.5) * 0.66 +
            (feedback.get("normal", {}).get(key, 0.5) - 0.5) * 0.34
        ) * feedback_confidence
        ml_distill_score = (
            ((ml_distillation.get("special") or {}).get(key, 0.5) - 0.5) * 0.58 +
            ((ml_distillation.get("normal") or {}).get(key, 0.5) - 0.5) * 0.42
        ) * ml_distill_confidence
        anchor_score = (
            ((anchor_profile.get("special") or {}).get(key, 0.5) - 0.5) * 0.62 +
            ((anchor_profile.get("normal") or {}).get(key, 0.5) - 0.5) * 0.38
        ) * anchor_confidence
        attr = attribute_score(number)
        attribute_transition_score = _score_markov_attribute_transition(
            number,
            transition_profile.get("latest_special"),
            attribute_profile,
            zodiac_map=number_to_zodiac,
        )
        special_attribute_transition_score = _score_markov_attribute_transition(
            number,
            special_profile.get("latest_special"),
            special_attribute_profile,
            zodiac_map=number_to_zodiac,
        )
        failure_adjustment = (float((failure_profile.get("candidate") or {}).get(key, 0.5)) - 0.5) * 2.0 * failure_confidence
        score = (
            float(weights.get("transition", 1.0)) * transition_norm.get(key, 0.0) +
            float(weights.get("transition_lift", 0.0)) * transition_lift_norm.get(key, 0.0) +
            float(weights.get("second_order", 0.0)) * second_order_confidence * second_order_norm.get(key, 0.0) +
            float(weights.get("phase_transition", 0.0)) * phase_transition_norm.get(key, 0.0) +
            float(weights.get("attribute_transition", 0.0)) * attribute_transition_score +
            float(weights.get("hot", 0.0)) * hot_norm.get(key, 0.0) +
            float(weights.get("trend", 0.0)) * trend_norm.get(key, 0.0) +
            float(weights.get("normal", 0.0)) * normal_norm.get(key, 0.0) +
            float(weights.get("overdue", 0.0)) * overdue_norm.get(key, 0.0) +
            float(weights.get("feedback", 0.0)) * feedback_score +
            (0.42 if markov_guard.get("mode") == "anchor_guard" else 0.12) * ml_distill_score +
            anchor_weight * anchor_score +
            float(weights.get("failure", 0.0)) * failure_adjustment +
            attr +
            repeat_penalty(number)
        )
        number_scores[number] = round(score, 6)
        parity_bonus = 0.08 if preferred_parity and _get_parity_zh(number) == preferred_parity else 0.0
        special_scores[number] = round(
            score +
            float(weights.get("special_transition", 0.0)) * special_transition_norm.get(key, 0.0) +
            float(weights.get("special_transition_lift", 0.0)) * special_transition_lift_norm.get(key, 0.0) +
            float(weights.get("special_chain", 0.0)) * (
                special_direct_confidence * special_direct_norm.get(key, 0.0) +
                special_second_order_confidence * special_second_order_norm.get(key, 0.0) * 0.72
            ) +
            float(weights.get("special_attribute", 0.0)) * special_direct_confidence * special_attribute_transition_score +
            float(weights.get("second_order", 0.0)) * second_order_confidence * second_order_norm.get(key, 0.0) * 0.35 +
            float(weights.get("phase_transition", 0.0)) * phase_transition_norm.get(key, 0.0) * 0.25 +
            (feedback.get("special", {}).get(key, 0.5) - 0.5) * feedback_confidence * 0.32 +
            ((anchor_profile.get("special") or {}).get(key, 0.5) - 0.5) * anchor_confidence * anchor_weight * 0.75 +
            parity_bonus,
            6,
        )

    overall_rank = _rank_numbers(number_scores)
    bucket_counts = _resolve_local_bucket_counts(config.get("bucket_counts") or [2, 2, 2], "neutral")
    low_bucket = [n for n in overall_rank if n <= 16]
    mid_bucket = [n for n in overall_rank if 17 <= n <= 33]
    high_bucket = [n for n in overall_rank if n >= 34]
    normal = []
    normal += _take_personalized_ranked(low_bucket, bucket_counts[0], variation_key=variation_key, window_size=max(pool_size // 2, 6))
    normal += _take_personalized_ranked(mid_bucket, bucket_counts[1], variation_key=variation_key, exclude=normal, window_size=max(pool_size // 2, 6))
    normal += _take_personalized_ranked(high_bucket, bucket_counts[2], variation_key=variation_key, exclude=normal, window_size=max(pool_size // 2, 6))
    if len(normal) < 6:
        normal += _take_personalized_ranked(overall_rank[:pool_size * 2], 6 - len(normal), variation_key=variation_key, exclude=normal, window_size=max(pool_size * 2, 12))
    normal = _rebalance_selected_numbers_by_parity(sorted(normal), overall_rank, number_scores, parity_pref, count=6)
    combo_profile = _score_markov_combo_shape(normal, recent_data, region, year)
    combo_score = float(combo_profile.get("score") or 0.0)
    if combo_score < -0.02:
        combo_candidates = {
                number: number_scores.get(number, 0.0) + _score_markov_combo_shape(
                    _dedupe_keep_order([item for item in normal if item != normal[-1]] + [number]),
                    recent_data,
                    region,
                    year,
                ).get("score", 0.0)
                for number in overall_rank[:max(pool_size * 2, 18)]
                if number not in normal
            }
        combo_rank = _rank_numbers(
            combo_candidates,
            candidates=list(combo_candidates.keys()),
        )
        if combo_rank and normal:
            normal = sorted(_dedupe_keep_order(normal[:-1] + [combo_rank[0]])[:6])
            combo_profile = _score_markov_combo_shape(normal, recent_data, region, year)
    if anchor_weight > 0 and anchor_profile.get("active"):
        anchor_normal_scores = anchor_profile.get("normal") or {}
        anchor_rank = sorted(
            [number for number in range(1, 50) if number not in normal],
            key=lambda number: (
                float(anchor_normal_scores.get(str(number), 0.5) or 0.5),
                number_scores.get(number, 0.0),
            ),
            reverse=True,
        )
        normal, anchor_combo_profile = _rebalance_local_combo_shape(
            normal,
            anchor_rank + overall_rank,
            number_scores,
            recent_data,
            region,
            year,
        )
        if anchor_combo_profile.get("adjusted"):
            combo_profile = anchor_combo_profile

    remaining_numbers = [number for number in range(1, 50) if number not in normal]
    special_rank = _rank_numbers(special_scores, candidates=remaining_numbers)
    if anchor_weight > 0 and anchor_profile.get("active"):
        anchor_special_scores = anchor_profile.get("special") or {}
        anchor_special_rank = sorted(
            remaining_numbers,
            key=lambda number: (
                special_scores.get(number, 0.0) +
                float(anchor_special_scores.get(str(number), 0.5) or 0.5) * anchor_weight,
                special_scores.get(number, 0.0),
            ),
            reverse=True,
        )
        special_rank = _dedupe_keep_order(anchor_special_rank[:max(special_pool_size, 8)] + special_rank)
    special_candidates = special_rank[:special_pool_size] or [number for number in overall_rank if number not in normal]
    special_pick = _take_personalized_ranked(special_candidates, 1, variation_key=variation_key, window_size=max(special_pool_size // 2, 2))
    special_num = special_pick[0] if special_pick else special_candidates[0]
    support_chains = (transition_profile.get("support_chains") or {}).get(str(special_num), [])
    explanation_bits = [
        "先看最近几期号码后面常接哪些号",
        "再看当前冷热阶段更像哪种走势",
        "同时参考号码所在区间、尾数、波色和单双",
        "并降低最近经常判断错的组合权重",
    ]
    if markov_guard.get("active"):
        explanation_bits.append("近期不稳时会自动保守一点" if markov_guard.get("mode") == "anchor_guard" else "近期表现好时会适当加大参考")
    if anchor_weight > 0:
        explanation_bits.append("并参考近期更稳策略的历史反馈")
    chain_text = ""
    if support_chains:
        chain_parts = []
        for item in support_chains[:3]:
            if item.get("type") == "two_step":
                source = item.get("source") or []
                if len(source) >= 2:
                    chain_parts.append(f"{source[0]}、{source[1]}连着出现后，历史上更容易靠近{special_num}")
            else:
                chain_parts.append(f"{item.get('source')}出现后，历史上更容易靠近{special_num}")
        if chain_parts:
            chain_text = "；另外参考了这些历史关联：" + "；".join(chain_parts)

    recommendation_text = _build_special_focus_text(
        str(special_num),
        normal,
        strategy_name=_get_strategy_label("markov"),
        accuracy=round(float(config.get("last_accuracy") or 0.0) * 100, 1),
        samples=max(int(feedback.get("samples") or 0), int(transition_profile.get("transition_samples") or 0)),
        confidence=round(max(number_scores.get(special_num, 0.0), special_scores.get(special_num, 0.0)) * 20, 1),
        extra_reason="本期主要" + "，".join(explanation_bits) + chain_text + "。",
    )
    return {
        "normal": sorted(normal),
        "special": {"number": str(special_num), "sno_zodiac": number_to_zodiac.get(str(special_num), "")},
        "recommendation_text": recommendation_text,
        "model_meta": {
            "markov_window": window,
            "markov_pool": pool_size,
            "markov_special_pool": special_pool_size,
            "markov_transition_decay": round(float(config.get("transition_decay") or 0.985), 4),
            "markov_transition_min_samples": transition_min_samples,
            "markov_source_special_weight": round(float(config.get("source_special_weight") or 1.28), 3),
            "markov_repeat_penalty": round(float(config.get("repeat_penalty", -0.18) or -0.18), 3),
            "markov_weights": dict(weights),
            "learning_adaptation_mode": config.get("learning_adaptation_mode", "balanced"),
            "markov_guard": dict(markov_guard),
            "markov_latest_sources": transition_profile.get("latest_sources") or [],
            "markov_latest_second_sources": transition_profile.get("latest_second_sources") or [],
            "markov_latest_phase": transition_profile.get("latest_phase") or "neutral",
            "markov_transition_samples": int(transition_profile.get("transition_samples") or 0),
            "markov_second_order_confidence": round(second_order_confidence * 100, 2),
            "markov_second_order_sample_total": transition_profile.get("second_order_sample_total") or 0,
            "markov_special_profile": {
                "direct_confidence": round(special_direct_confidence * 100, 2),
                "second_order_confidence": round(special_second_order_confidence * 100, 2),
                "direct_sample_total": special_profile.get("direct_sample_total") or 0,
                "second_order_sample_total": special_profile.get("second_order_sample_total") or 0,
                "latest_special": special_profile.get("latest_special") or 0,
                "latest_previous_special": special_profile.get("latest_previous_special") or 0,
            },
            "markov_failure_profile": {
                "samples": int(failure_profile.get("samples") or 0),
                "confidence": round(float(failure_profile.get("confidence") or 0.0) * 100, 2),
            },
            "markov_ml_distillation": {
                "active": bool(markov_guard.get("mode") == "anchor_guard"),
                "samples": int(ml_distillation.get("samples") or 0),
                "confidence": round(float(ml_distillation.get("confidence") or 0.0) * 100, 2),
            },
            "markov_anchor_profile": {
                "active": bool(anchor_weight > 0 and anchor_profile.get("active")),
                "weight": round(anchor_weight * 100, 2),
                "samples": int(anchor_profile.get("samples") or 0),
                "confidence": round(anchor_confidence * 100, 2),
                "strategies": anchor_profile.get("strategies") or [],
            },
            "markov_combo_profile": combo_profile,
            "markov_support_chains": support_chains,
            "special_candidates": list(special_candidates[:max(special_pool_size, 6)]),
            "markov_top_transitions": [
                {
                    "number": number,
                    "score": round(float(transition_norm.get(str(number), 0.0)) * 100, 2),
                    "lift_score": round(float(transition_lift_norm.get(str(number), 0.0)) * 100, 2),
                    "second_order_score": round(float(second_order_norm.get(str(number), 0.0)) * 100, 2),
                    "phase_score": round(float(phase_transition_norm.get(str(number), 0.0)) * 100, 2),
                }
                for number in overall_rank[:10]
            ],
        },
    }


def _resolve_local_strategy_phase_profile(data, config=None):
    profile = _classify_ai_market_phase(
        data,
        window=max(8, int((config or {}).get("window") or 12)),
    )
    return profile if isinstance(profile, dict) else {"label": "neutral", "confidence": 0.0, "adjustments": {}}


def _resolve_local_bucket_counts(base_counts, phase_label, top6_rate=0.0):
    low_count, mid_count, high_count = list(base_counts or [2, 2, 2])[:3]
    if phase_label == "concentrated":
        mid_count = min(3, mid_count + 1)
        if low_count >= high_count:
            low_count = max(1, low_count - 1)
        else:
            high_count = max(1, high_count - 1)
    elif phase_label == "dispersed":
        low_count = min(3, low_count + 1) if low_count <= high_count else low_count
        high_count = min(3, high_count + 1) if high_count <= low_count else high_count
        mid_count = max(1, 6 - low_count - high_count)
    elif phase_label == "hot" and top6_rate >= 0.12:
        mid_count = min(3, mid_count + 1)
        high_count = max(1, 6 - low_count - mid_count)
    total = low_count + mid_count + high_count
    if total != 6:
        high_count = max(1, 6 - low_count - mid_count)
    return [low_count, mid_count, high_count]


def _resolve_local_hybrid_mix(config, region, phase_label):
    base_mix = dict(config.get("mix") or {"hot": 2, "cold": 2, "trend": 2})
    template_mix = dict(((config.get("phase_runtime_templates") or {}).get(phase_label) or {}).get("mix") or {})
    if template_mix:
        base_mix.update(template_mix)
    hot_stats = _calculate_strategy_hit_rates(region, "hot", limit=36)
    cold_stats = _calculate_strategy_hit_rates(region, "cold", limit=36)
    trend_stats = _calculate_strategy_hit_rates(region, "trend", limit=36)
    scores = {
        "hot": (_safe_float(hot_stats.get("top1"), 0.0) * 1.2) + (_safe_float(hot_stats.get("top6"), 0.0) * 0.4),
        "cold": (_safe_float(cold_stats.get("top1"), 0.0) * 1.0) + (_safe_float(cold_stats.get("top6"), 0.0) * 0.5),
        "trend": (_safe_float(trend_stats.get("top1"), 0.0) * 1.1) + (_safe_float(trend_stats.get("top6"), 0.0) * 0.45),
    }
    anneal_profile = {}
    for child_strategy, stats in (("hot", hot_stats), ("cold", cold_stats), ("trend", trend_stats)):
        recent_stats = _calculate_strategy_hit_rates(region, child_strategy, limit=12)
        base_score = scores.get(child_strategy, 0.0)
        recent_score = (
            _safe_float(recent_stats.get("top1"), 0.0) * 1.15 +
            _safe_float(recent_stats.get("top6"), 0.0) * 0.45
        )
        sample_confidence = _clamp(int(recent_stats.get("total", 0) or 0) / 10.0, 0.25, 1.0)
        degrade = max(0.0, base_score - recent_score)
        anneal = _clamp(degrade * sample_confidence, 0.0, 0.16)
        if anneal > 0:
            scores[child_strategy] = max(0.0, base_score - anneal)
        anneal_profile[child_strategy] = {
            "base_score": round(base_score, 4),
            "recent_score": round(recent_score, 4),
            "anneal": round(anneal, 4),
            "recent_total": int(recent_stats.get("total", 0) or 0),
        }
    if phase_label == "hot":
        scores["hot"] += 0.08
        scores["trend"] += 0.03
    elif phase_label == "cold":
        scores["cold"] += 0.08
    elif phase_label == "concentrated":
        scores["trend"] += 0.06
    elif phase_label == "dispersed":
        scores["cold"] += 0.04
        scores["hot"] += 0.03

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    mix = {key: 1 for key in base_mix.keys()}
    remaining = 3
    mix[ranked[0][0]] += 1
    mix[ranked[1][0]] += 1
    remaining -= 2
    if remaining > 0:
        mix[ranked[0][0]] += remaining
    mix["_anneal_profile"] = anneal_profile
    return mix


def _resolve_local_phase_runtime(config, strategy, phase_profile):
    resolved = {
        "window": _clamp(int((config or {}).get("window") or 0), 8, 96),
        "pool": _clamp(int((config or {}).get("pool") or 16), 8, 24),
        "special_pool": _clamp(int((config or {}).get("special_pool") or 8), 6, 14),
        "trend_window": _clamp(int((config or {}).get("trend_window") or 15), 6, 30),
        "bucket_counts": list((config or {}).get("bucket_counts") or [2, 2, 2])[:3],
        "mix": dict((config or {}).get("mix") or {"hot": 2, "cold": 2, "trend": 2}),
    }
    phase_label = str((phase_profile or {}).get("label") or "neutral")
    template = dict((((config or {}).get("phase_runtime_templates") or {}).get(phase_label)) or {})
    if not template:
        return resolved
    samples = int(template.get("samples", 0) or 0)
    if samples < 6:
        apply_ratio = 0.35
    elif samples < 12:
        apply_ratio = 0.65
    else:
        apply_ratio = 1.0
    for key in ("window", "pool", "special_pool", "trend_window"):
        template_value = template.get(key)
        if template_value in (None, ""):
            continue
        base_value = int(resolved.get(key) or 0)
        blended_value = int(round((base_value * (1.0 - apply_ratio)) + (int(template_value) * apply_ratio)))
        if key == "window":
            resolved[key] = _clamp(blended_value, 8, 96)
        elif key == "pool":
            resolved[key] = _clamp(blended_value, 8, 24)
        elif key == "special_pool":
            resolved[key] = _clamp(blended_value, 6, 14)
        elif key == "trend_window":
            resolved[key] = _clamp(blended_value, 6, 30)
    if strategy == "balanced" and template.get("bucket_counts"):
        resolved["bucket_counts"] = list(template.get("bucket_counts") or resolved["bucket_counts"])[:3]
    if strategy == "hybrid" and template.get("mix"):
        resolved["mix"] = dict(template.get("mix") or resolved["mix"])
    resolved["phase_template_samples"] = samples
    resolved["phase_template_apply_ratio"] = round(apply_ratio, 4)
    return resolved


def _resolve_local_phase_weights(config, phase_profile):
    base_weights = dict((config or {}).get("weights") or {})
    phase_label = str((phase_profile or {}).get("label") or "neutral")
    learned = dict(((config or {}).get("phase_weight_learning") or {}).get(phase_label) or {})
    learned_weights = dict(learned.get("weights") or {})
    if not learned_weights:
        return base_weights
    resolved = dict(base_weights)
    resolved.update(learned_weights)
    return resolved


def _resolve_local_phase_strategy_handoff(strategy, region, phase_profile):
    phase_label = str((phase_profile or {}).get("label") or "neutral")
    if phase_label not in ("hot", "cold", "concentrated", "dispersed"):
        return {
            "requested_strategy": strategy,
            "delegate_strategy": strategy,
            "boost_map": {},
            "phase_label": phase_label,
            "active": False,
        }

    candidate_pool = {
        "hot": ("hot", "trend", "hybrid"),
        "cold": ("cold", "balanced", "hybrid"),
        "trend": ("trend", "hot", "hybrid"),
        "balanced": ("balanced", "hybrid", "trend"),
        "hybrid": ("hybrid", "trend", "balanced", "hot", "cold"),
    }.get(strategy, (strategy,))
    scored = []
    for candidate in candidate_pool:
        candidate_config = _load_strategy_config(candidate, region)
        phase_score, samples = _score_local_strategy_phase_strength(candidate_config, phase_label)
        scored.append({
            "strategy": candidate,
            "score": phase_score,
            "samples": samples,
        })
    scored.sort(key=lambda item: (item["score"], item["samples"]), reverse=True)
    requested_entry = next((item for item in scored if item["strategy"] == strategy), {"score": 0.0, "samples": 0})
    best_entry = scored[0] if scored else requested_entry
    handoff_active = (
        best_entry.get("strategy") != strategy and
        best_entry.get("samples", 0) >= 6 and
        best_entry.get("score", 0.0) >= requested_entry.get("score", 0.0) + 0.08
    )
    boost_map = {}
    if handoff_active:
        if best_entry["strategy"] == "hot":
            boost_map = {"hot": 0.08, "feedback": 0.05}
        elif best_entry["strategy"] == "cold":
            boost_map = {"cold": 0.08, "overdue": 0.06}
        elif best_entry["strategy"] == "trend":
            boost_map = {"trend": 0.09, "feedback": 0.04}
        elif best_entry["strategy"] == "balanced":
            boost_map = {"normal": 0.05, "parity": 0.05, "color": 0.04}
        elif best_entry["strategy"] == "hybrid":
            boost_map = {"hot": 0.04, "cold": 0.04, "trend": 0.05, "feedback": 0.04}
    return {
        "requested_strategy": strategy,
        "delegate_strategy": best_entry.get("strategy") or strategy,
        "delegate_score": round(_safe_float(best_entry.get("score"), 0.0), 4),
        "requested_score": round(_safe_float(requested_entry.get("score"), 0.0), 4),
        "boost_map": boost_map,
        "phase_label": phase_label,
        "active": handoff_active,
        "samples": int(best_entry.get("samples", 0) or 0),
    }


def _build_local_strategy_signal_profile(strategy, phase_profile, config=None):
    label = str((phase_profile or {}).get("label") or "neutral")
    confidence = _clamp(_safe_float((phase_profile or {}).get("confidence"), 0.0), 0.0, 1.0)
    profile = {
        "feedback_multiplier": 1.0,
        "attribute_multiplier": 1.0,
        "overheat_multiplier": 1.0,
        "special_focus_multiplier": 1.0,
        "trend_multiplier": 1.0,
        "cold_multiplier": 1.0,
        "hot_multiplier": 1.0,
    }
    if strategy == "hot":
        profile.update({"hot_multiplier": 1.18, "feedback_multiplier": 1.08, "special_focus_multiplier": 1.08})
        if label == "hot":
            profile["hot_multiplier"] += 0.12 * confidence
            profile["overheat_multiplier"] += 0.22 * confidence
        elif label == "cold":
            profile["hot_multiplier"] -= 0.08 * confidence
    elif strategy == "cold":
        profile.update({"cold_multiplier": 1.22, "attribute_multiplier": 0.94, "special_focus_multiplier": 0.96})
        if label == "cold":
            profile["cold_multiplier"] += 0.12 * confidence
            profile["overheat_multiplier"] -= 0.18 * confidence
        elif label == "hot":
            profile["overheat_multiplier"] += 0.08 * confidence
    elif strategy == "trend":
        profile.update({"trend_multiplier": 1.24, "feedback_multiplier": 1.06})
        if label in ("hot", "concentrated"):
            profile["trend_multiplier"] += 0.1 * confidence
    elif strategy == "hybrid":
        profile.update({"feedback_multiplier": 1.1, "attribute_multiplier": 1.02})
    elif strategy == "balanced":
        profile.update({"attribute_multiplier": 1.08, "special_focus_multiplier": 0.94})
        if label == "concentrated":
            profile["attribute_multiplier"] += 0.1 * confidence
    learned_profile = dict((((config or {}).get("phase_weight_learning") or {}).get(label) or {}).get("profile_adjustments") or {})
    for key, delta in learned_profile.items():
        if key not in profile:
            continue
        profile[key] = round(_clamp(profile[key] + _safe_float(delta, 0.0), 0.75, 1.45), 4)
    return profile


def _compute_local_special_score(
    strategy,
    number,
    feedback,
    feedback_confidence,
    weights,
    hot_norm,
    trend_norm,
    overdue_norm,
    attribute_score_fn,
    overheat_penalty_fn,
    preferred_parity,
    strategy_profile,
):
    key = str(number)
    base_special_feedback = (feedback.get("special", {}).get(key, 0.5) - 0.5) * feedback_confidence
    base_normal_feedback = (feedback.get("normal", {}).get(key, 0.5) - 0.5) * feedback_confidence
    attr = attribute_score_fn(number) * float(strategy_profile.get("attribute_multiplier", 1.0))
    penalty = overheat_penalty_fn(number) * float(strategy_profile.get("overheat_multiplier", 1.0))
    parity_bonus = 0.12 if preferred_parity and _get_parity_zh(number) == preferred_parity else -0.05

    if strategy == "hot":
        base = (
            hot_norm.get(key, 0.0) * 0.52 * float(strategy_profile.get("hot_multiplier", 1.0)) +
            trend_norm.get(key, 0.0) * 0.22 * float(strategy_profile.get("trend_multiplier", 1.0)) +
            overdue_norm.get(key, 0.0) * 0.05
        )
        feedback_term = (base_special_feedback * 1.28 + base_normal_feedback * 0.12) * float(strategy_profile.get("feedback_multiplier", 1.0))
    elif strategy == "cold":
        base = (
            (1.0 - hot_norm.get(key, 0.0)) * 0.42 * float(strategy_profile.get("cold_multiplier", 1.0)) +
            overdue_norm.get(key, 0.0) * 0.42 * float(strategy_profile.get("cold_multiplier", 1.0)) +
            trend_norm.get(key, 0.0) * 0.08
        )
        feedback_term = (base_special_feedback * 0.72 + base_normal_feedback * 0.08) * float(strategy_profile.get("feedback_multiplier", 1.0))
    elif strategy == "trend":
        base = (
            trend_norm.get(key, 0.0) * 0.58 * float(strategy_profile.get("trend_multiplier", 1.0)) +
            hot_norm.get(key, 0.0) * 0.16 +
            overdue_norm.get(key, 0.0) * 0.08
        )
        feedback_term = (base_special_feedback * 1.08 + base_normal_feedback * 0.18) * float(strategy_profile.get("feedback_multiplier", 1.0))
    elif strategy == "hybrid":
        base = (
            hot_norm.get(key, 0.0) * 0.28 +
            trend_norm.get(key, 0.0) * 0.24 +
            overdue_norm.get(key, 0.0) * 0.14 +
            (1.0 - hot_norm.get(key, 0.0)) * 0.12
        )
        feedback_term = (base_special_feedback * 1.12 + base_normal_feedback * 0.2) * float(strategy_profile.get("feedback_multiplier", 1.0))
    else:
        base = (
            hot_norm.get(key, 0.0) * 0.2 +
            trend_norm.get(key, 0.0) * 0.18 +
            overdue_norm.get(key, 0.0) * 0.12
        )
        feedback_term = (base_special_feedback * 0.96 + base_normal_feedback * 0.2) * float(strategy_profile.get("feedback_multiplier", 1.0))

    total = (
        base +
        feedback_term * float(weights.get("feedback", 1.0)) +
        attr +
        penalty +
        parity_bonus
    ) * float(strategy_profile.get("special_focus_multiplier", 1.0))

    if base_special_feedback > 0.1 and attr > 0.35 and penalty >= -0.05:
        total *= 1.18 if strategy in ("hot", "trend") else 1.1
    if strategy == "cold" and overdue_norm.get(key, 0.0) > 0.55 and hot_norm.get(key, 0.0) < 0.35:
        total *= 1.12
    if strategy == "balanced" and attr > 0.4 and penalty >= -0.05:
        total *= 1.08
    return round(total, 6)


def _build_default_baseline_prediction():
    normal = [4, 12, 19, 28, 35, 44]
    special_num = 49
    return {
        "normal": normal,
        "special": {"number": str(special_num), "sno_zodiac": ""},
        "recommendation_text": _build_special_focus_text(
            str(special_num),
            normal,
            extra_reason="当前历史数据不足，已返回基础保底组合。",
        ),
    }

def _build_ai_system_prompt():
    return (
        "你是一个严格遵守格式的彩票数据分析助手。"
        "你的目标不是泛泛分析，而是在给定历史数据、反馈和候选池中做克制的最终选择。"
        "你必须优先参考系统提供的近期回测表现、历史命中反馈、号码属性偏好和本地策略共识。"
        "不要输出免责声明。不要输出 1-49 以外的数字。不要重复数字。"
        "最终必须输出固定格式："
        "本期主推特码：[s]\\n参考平码：[n1, n2, n3, n4, n5, n6]\\n理由：..."
        "如果你愿意，也可以先额外输出一行 JSON："
        "{\"normal\":[n1,n2,n3,n4,n5,n6],\"special\":s}"
    )


def _build_ai_candidate_context(data, region):
    special_support = Counter()
    normal_support = Counter()
    strategy_lines = []
    for strategy in ("ml", "markov", "hybrid", "balanced", "trend"):
        try:
            result = get_local_recommendations(strategy, data, region)
        except Exception:
            continue
        special = str((result.get("special") or {}).get("number") or "").strip()
        normal = [int(n) for n in (result.get("normal") or []) if str(n).isdigit()]
        if special:
            special_support[special] += 1
        for number in normal:
            normal_support[str(number)] += 1
        if special or normal:
            strategy_lines.append(
                f"- {_get_strategy_label(strategy)}：平码 {', '.join(map(str, normal[:6])) if normal else '暂无'}；特码 {special or '暂无'}"
            )

    top_special = [
        number for number, _ in sorted(
            special_support.items(),
            key=lambda item: (item[1], -abs(int(item[0]) - 25), -int(item[0])),
            reverse=True
        )
    ][:8]
    top_normal = [int(number) for number, _ in normal_support.most_common(12)]

    lines = ["本地策略候选池："]
    if strategy_lines:
        lines.extend(strategy_lines)
    lines.append(f"- 共识特码候选：{', '.join(top_special) if top_special else '暂无'}")
    lines.append(f"- 共识平码候选：{', '.join(map(str, top_normal)) if top_normal else '暂无'}")
    lines.append("决策要求：优先从共识候选中挑选；若偏离共识，必须在理由中简短说明。")
    return "\n".join(lines)


def _build_ai_learning_context(data, region, history_window=10):
    recent_data = data[:history_window]
    year = _infer_draw_year(recent_data or data)
    feedback = _build_prediction_feedback(region, "ai")
    color_pref, zodiac_pref, parity_pref = _build_attribute_preferences(recent_data or data, region, feedback, year)
    recommended_strategy = _get_recommended_strategy(region)
    backtest_lines = []
    for strategy in ("hot", "cold", "trend", "hybrid", "balanced", "markov", "ml"):
        window20_accuracy, window20_total = _calculate_strategy_accuracy(region, strategy, limit=20)
        window50_accuracy, window50_total = _calculate_strategy_accuracy(region, strategy, limit=50)
        backtest_lines.append(
            f"- {_get_strategy_label(strategy)}：近20期 {round(window20_accuracy * 100, 1)}% ({window20_total}期)，"
            f"近50期 {round(window50_accuracy * 100, 1)}% ({window50_total}期)"
        )

    special_scores = feedback.get("special") or {}
    normal_scores = feedback.get("normal") or {}

    top_special_numbers = _rank_numbers(
        {number: special_scores.get(str(number), 0.0) for number in range(1, 50)}
    )[:6]
    top_normal_numbers = _rank_numbers(
        {number: normal_scores.get(str(number), 0.0) for number in range(1, 50)}
    )[:8]
    top_colors = sorted(color_pref.items(), key=lambda item: item[1], reverse=True)[:3]
    top_zodiacs = sorted(zodiac_pref.items(), key=lambda item: item[1], reverse=True)[:4]
    top_parities = sorted(parity_pref.items(), key=lambda item: item[1], reverse=True)[:2]

    lines = [
        "历史学习反馈摘要：",
        f"- 当前系统推荐优先参考：{recommended_strategy.get('label')}（综合评分 {recommended_strategy.get('score')}）",
        f"- AI历史学习样本：{feedback.get('samples', 0)} 期",
        f"- 历史反馈更偏好的特码候选：{', '.join(map(str, top_special_numbers)) if top_special_numbers else '暂无'}",
        f"- 历史反馈更偏好的平码候选：{', '.join(map(str, top_normal_numbers)) if top_normal_numbers else '暂无'}",
        f"- 历史反馈更偏好的波色：{'、'.join([f'{name}({round(score * 100, 1)}%)' for name, score in top_colors]) if top_colors else '暂无'}",
        f"- 历史反馈更偏好的单双：{'、'.join([f'{name}({round(score * 100, 1)}%)' for name, score in top_parities]) if top_parities else '暂无'}",
        f"- 历史反馈更偏好的生肖：{'、'.join([f'{name}({round(score * 100, 1)}%)' for name, score in top_zodiacs]) if top_zodiacs else '暂无'}",
        "各策略近期回测表现：",
        *backtest_lines,
        "请将以上学习反馈纳入分析，优先考虑历史反馈更强的号码、波色和生肖组合，但不要机械重复同一组号码。"
    ]
    return "\n".join(lines)


def _sigmoid(value):
    value = _clamp(value, -30.0, 30.0)
    return 1.0 / (1.0 + math.exp(-value))


def _build_ml_feature_table(history_data, region, feature_window=60, feedback=None):
    history = list(history_data or [])[:max(int(feature_window or 0), 10)]
    feedback = feedback or _build_prediction_feedback(region, "ml")
    cache_key = (
        str(region or "").strip().lower(),
        int(feature_window or 0),
        _runtime_draws_signature(history, limit=max(int(feature_window or 0), 10)),
        _runtime_json_signature(feedback or {}),
    )
    cached = _runtime_cache_get("ml_feature_table", cache_key)
    if cached is not None:
        return cached

    short_data = history[:min(len(history), 12)]
    medium_data = history[:min(len(history), 24)]
    long_data = history[:min(len(history), max(int(feature_window or 60), 30))]

    short_special = _normalize_metric_map(analyze_special_number_frequency(short_data))
    medium_special = _normalize_metric_map(analyze_special_number_frequency(medium_data))
    long_special = _normalize_metric_map(analyze_special_number_frequency(long_data))
    long_all = _normalize_metric_map(_build_number_frequency(long_data))
    overdue = _normalize_metric_map(_build_overdue_scores(long_data))
    recent_all = _normalize_metric_map(_build_number_frequency(short_data))
    year = _infer_draw_year(history)
    number_to_zodiac = _get_number_to_zodiac_map(year)
    color_pref, zodiac_pref, parity_pref = _build_attribute_preferences(
        long_data, region, feedback, year, apply_recent_zodiac_cooldown=True
    )
    recent_specials = [str(item.get("sno")) for item in short_data[:5] if item.get("sno")]
    recent_numbers = set()
    recent_number_hits = Counter()
    for item in short_data[:8]:
        for number in item.get("no", []):
            if number:
                recent_numbers.add(str(number))
                recent_number_hits[str(number)] += 1
        special = item.get("sno")
        if special:
            recent_numbers.add(str(special))
            recent_number_hits[str(special)] += 1

    recent_special_gap = _build_overdue_scores(short_data[:8] or short_data)
    recent_gap_norm = _normalize_metric_map(recent_special_gap)

    features = {}
    for number in range(1, 50):
        key = str(number)
        distance_sum = 0.0
        for recent_special in recent_specials:
            try:
                distance_sum += 1.0 / (1.0 + abs(number - int(recent_special)))
            except (TypeError, ValueError):
                continue
        bucket_low = 1.0 if number <= 16 else 0.0
        bucket_mid = 1.0 if 17 <= number <= 33 else 0.0
        bucket_high = 1.0 if number >= 34 else 0.0
        short_momentum = short_special.get(key, 0.0) - medium_special.get(key, 0.0)
        medium_momentum = medium_special.get(key, 0.0) - long_special.get(key, 0.0)
        neighbor_density = (
            recent_all.get(str(max(1, number - 1)), 0.0) * 0.25 +
            recent_all.get(key, 0.0) * 0.5 +
            recent_all.get(str(min(49, number + 1)), 0.0) * 0.25
        )
        repeat_pressure = min(1.0, recent_number_hits.get(key, 0) / 3.0)
        normal_support = max(0.0, recent_all.get(key, 0.0) - short_special.get(key, 0.0))
        recent_gap_score = recent_gap_norm.get(key, 0.0)
        interval_balance = 1.0 - min(1.0, abs(number - 25) / 24.0)
        features[key] = [
            short_special.get(key, 0.0),
            medium_special.get(key, 0.0),
            long_special.get(key, 0.0),
            long_all.get(key, 0.0),
            overdue.get(key, 0.0),
            1.0 - medium_special.get(key, 0.0),
            1.0 if key in recent_specials else 0.0,
            1.0 if key in recent_numbers else 0.0,
            round(distance_sum, 6),
            color_pref.get(_get_color_zh(number), 0.0),
            zodiac_pref.get(number_to_zodiac.get(key, ""), 0.0),
            parity_pref.get(_get_parity_zh(number), 0.0),
            bucket_low,
            bucket_mid,
            bucket_high,
            round(short_momentum, 6),
            round(medium_momentum, 6),
            recent_all.get(key, 0.0),
            round(neighbor_density, 6),
            round(repeat_pressure, 6),
            round(normal_support, 6),
            round(recent_gap_score, 6),
            round(interval_balance, 6),
        ]
    return _runtime_cache_set("ml_feature_table", cache_key, features)


ML_FEATURE_PROFILES = {
    "full": set(),
    "compact_attributes": {9, 10, 11},
    "compact_structure": {18, 19, 20, 21, 22},
    "compact_recency": {6, 7, 8, 15, 16},
}


def _apply_ml_feature_profile(feature_table, profile_name=None):
    profile_key = str(profile_name or "full").strip() or "full"
    disabled_indices = ML_FEATURE_PROFILES.get(profile_key, set())
    if not disabled_indices:
        return feature_table

    transformed = {}
    for key, features in (feature_table or {}).items():
        row = list(features)
        for idx in disabled_indices:
            if 0 <= idx < len(row):
                row[idx] = 0.0
        transformed[key] = row
    return transformed


def _build_ml_score_pairs(feature_table, weights=None, bias=0.0):
    score_pairs = []
    weights = weights or []
    for number in range(1, 50):
        key = str(number)
        features = feature_table.get(key)
        if not features:
            continue
        score = float(bias) + sum(
            weight * value for weight, value in zip(weights, features)
        )
        score_pairs.append((key, features, score))
    return score_pairs


def _ensure_ml_weight_vector(weights, feature_table):
    if weights is not None:
        return list(weights)
    first_features = next(iter(feature_table.values()), [])
    return [0.0] * len(first_features)


def _update_ml_weights(score_pairs, target_special, weights, bias, step, l2):
    probabilities = _softmax([item[2] for item in score_pairs])
    target_probability = 0.0
    for row_idx, (key, features, _) in enumerate(score_pairs):
        probability = probabilities[row_idx]
        label = 1.0 if key == target_special else 0.0
        error = label - probability
        if label > 0:
            target_probability = probability
        bias += step * error
        for feature_idx, feature_value in enumerate(features):
            weights[feature_idx] += step * (
                error * feature_value - (l2 * weights[feature_idx])
            )
    return weights, bias, probabilities, target_probability


def _build_ml_probability_map(score_pairs):
    probabilities = _softmax([item[2] for item in score_pairs])
    return {
        int(key): probabilities[row_idx]
        for row_idx, (key, _, _) in enumerate(score_pairs)
    }


def _get_ml_bucket_name(number):
    if number <= 16:
        return "low"
    if number <= 33:
        return "mid"
    return "high"


def _select_ml_normal_numbers(
    ranked_numbers,
    score_map,
    bucket_counts,
    variation_key=None,
    pool_size=18,
    parity_pref=None,
    recent_draw_number_counter=None,
    latest_draw_numbers=None,
):
    desired_counts = {
        "low": max(0, int(bucket_counts[0] if len(bucket_counts) > 0 else 2)),
        "mid": max(0, int(bucket_counts[1] if len(bucket_counts) > 1 else 2)),
        "high": max(0, int(bucket_counts[2] if len(bucket_counts) > 2 else 2)),
    }
    candidate_limit = max(int(pool_size) * 2, 18)
    base_candidates = list(ranked_numbers[:candidate_limit]) or list(ranked_numbers)
    candidates = _take_personalized_ranked(
        base_candidates,
        len(base_candidates),
        variation_key=variation_key,
        chunk_size=4,
        window_size=len(base_candidates),
    )
    bucket_usage = {"low": 0, "mid": 0, "high": 0}
    chosen = []

    while len(chosen) < 6:
        best_number = None
        best_score = None
        parity_targets = _build_parity_target_counts(parity_pref, total=6)
        parity_usage = Counter(_get_parity_zh(number) for number in chosen if _get_parity_zh(number))
        for number in candidates:
            if number in chosen:
                continue
            bucket = _get_ml_bucket_name(number)
            bucket_overflow = max(0, bucket_usage[bucket] - desired_counts.get(bucket, 0))
            bucket_gap = max(0, desired_counts.get(bucket, 0) - bucket_usage[bucket])
            adjusted_score = float(score_map.get(number, 0.0))
            adjusted_score += bucket_gap * 0.045
            adjusted_score -= bucket_overflow * 0.09
            adjusted_score -= bucket_usage[bucket] * 0.015
            number_parity = _get_parity_zh(number)
            parity_gap = max(0, parity_targets.get(number_parity, 0) - parity_usage.get(number_parity, 0))
            parity_overflow = max(0, parity_usage.get(number_parity, 0) - parity_targets.get(number_parity, 0))
            adjusted_score += parity_gap * 0.06
            adjusted_score -= parity_overflow * 0.08
            if latest_draw_numbers and number in latest_draw_numbers:
                adjusted_score -= 0.22
            draw_heat = int((recent_draw_number_counter or {}).get(number, 0) or 0)
            if draw_heat >= 2:
                adjusted_score -= 0.14
            elif draw_heat == 1:
                adjusted_score -= 0.05
            if (
                best_score is None or
                adjusted_score > best_score or
                (adjusted_score == best_score and (best_number is None or number > best_number))
            ):
                best_number = number
                best_score = adjusted_score

        if best_number is None:
            break

        chosen.append(best_number)
        bucket_usage[_get_ml_bucket_name(best_number)] += 1

    return _rebalance_selected_numbers_by_parity(chosen[:6], ranked_numbers, score_map, parity_pref, count=6)


def _build_ml_recent_selection_state(history_data, region, year=None):
    repeat_transition_profile = _build_repeat_transition_profile(
        history_data,
        region,
        year=year or _infer_draw_year(history_data),
    )
    recent_draw_number_counter = Counter()
    recent_draw_sets = []
    for item in list(history_data or [])[:6]:
        draw_numbers = []
        for raw_number in list(item.get("no") or []) + [item.get("sno")]:
            try:
                parsed = int(str(raw_number).strip())
            except (TypeError, ValueError):
                continue
            if 1 <= parsed <= 49:
                draw_numbers.append(parsed)
        deduped_draw = _dedupe_keep_order(draw_numbers)
        if deduped_draw:
            recent_draw_sets.append(set(deduped_draw))
            for parsed in deduped_draw:
                recent_draw_number_counter[parsed] += 1
    return {
        "repeat_transition_profile": repeat_transition_profile,
        "recent_draw_number_counter": recent_draw_number_counter,
        "recent_draw_sets": recent_draw_sets,
        "latest_draw_numbers": recent_draw_sets[0] if recent_draw_sets else set(),
    }


def _select_ml_special_number(
    special_ranked_numbers,
    special_score_map,
    normal_numbers,
    special_pool_size,
    preferred_special_parity="",
    recent_selection_state=None,
    number_to_zodiac=None,
    variation_key=None,
):
    recent_selection_state = dict(recent_selection_state or {})
    repeat_transition_profile = dict(recent_selection_state.get("repeat_transition_profile") or {})
    latest_special_repeat_probability = float(
        repeat_transition_profile.get("latest_special_repeat_probability") or 0.0
    )
    latest_zodiac_repeat_probability = float(
        repeat_transition_profile.get("latest_zodiac_repeat_probability") or 0.0
    )
    latest_special = repeat_transition_profile.get("latest_special")
    latest_zodiac = str(repeat_transition_profile.get("latest_zodiac") or "").strip()
    latest_draw_numbers = set(recent_selection_state.get("latest_draw_numbers") or set())
    recent_draw_number_counter = Counter(recent_selection_state.get("recent_draw_number_counter") or {})
    normal_set = {int(number) for number in (normal_numbers or [])}
    number_to_zodiac = number_to_zodiac or {}

    special_candidates = [
        number for number in list(special_ranked_numbers or [])[:max(int(special_pool_size or 0), 1) * 2]
        if number not in normal_set
    ]
    if not special_candidates:
        special_candidates = [
            number for number in list(special_ranked_numbers or [])
            if number not in normal_set
        ]
    if not special_candidates:
        special_candidates = [
            number for number in range(1, 50)
            if number not in normal_set
        ]
    special_candidates = sorted(
        special_candidates,
        key=lambda number: (
            special_score_map.get(number, 0.0) +
            (0.08 if preferred_special_parity and _get_parity_zh(number) == preferred_special_parity else 0.0) -
            ((0.16 + max(0.0, 0.24 - latest_special_repeat_probability)) if number == latest_special else 0.0) -
            ((0.12 + max(0.0, 0.20 - latest_zodiac_repeat_probability)) if latest_zodiac and number_to_zodiac.get(str(number), "") == latest_zodiac else 0.0) -
            (0.24 if number in latest_draw_numbers else 0.0) -
            (0.16 if int(recent_draw_number_counter.get(number, 0) or 0) >= 2 else 0.0) -
            (0.06 if int(recent_draw_number_counter.get(number, 0) or 0) == 1 else 0.0)
        ),
        reverse=True,
    )
    special_pick = _take_personalized_ranked(
        special_candidates,
        1,
        variation_key=variation_key,
        window_size=max(int(special_pool_size or 1) // 2, 2),
    )
    return (special_pick[0] if special_pick else special_candidates[0]), special_candidates


def _build_ml_special_selection_reason(
    special_num,
    special_candidates,
    special_score_map,
    special_votes,
    preferred_special_color="",
    preferred_special_parity="",
    recent_selection_state=None,
    number_to_zodiac=None,
):
    try:
        special_num = int(special_num)
    except (TypeError, ValueError):
        return ""

    special_candidates = list(special_candidates or [])
    special_votes = special_votes or {}
    recent_selection_state = dict(recent_selection_state or {})
    recent_draw_number_counter = Counter(recent_selection_state.get("recent_draw_number_counter") or {})
    latest_draw_numbers = set(recent_selection_state.get("latest_draw_numbers") or set())
    number_to_zodiac = number_to_zodiac or {}
    reasons = []

    if special_candidates:
        rank = special_candidates.index(special_num) + 1 if special_num in special_candidates else 0
        if rank and rank <= 3:
            reasons.append(f"综合排序第{rank}")
        elif rank:
            reasons.append("在候选范围内")

    score = _safe_float(special_score_map.get(special_num), 0.0)
    if score > 0:
        reasons.append(f"系统评分{round(score * 100, 1)}")

    vote_value = special_votes.get(special_num)
    if vote_value is None:
        vote_value = special_votes.get(str(special_num), 0.0)
    vote_value = _safe_float(vote_value, 0.0)
    if vote_value > 0:
        reasons.append(f"其它策略也给了{round(vote_value, 2)}票")
    else:
        reasons.append("不是靠策略票选出来的")

    attr_matches = []
    if preferred_special_color and _get_color_zh(special_num) == preferred_special_color:
        attr_matches.append(preferred_special_color)
    if preferred_special_parity and _get_parity_zh(special_num) == preferred_special_parity:
        attr_matches.append(preferred_special_parity)
    zodiac = number_to_zodiac.get(str(special_num), "")
    if zodiac:
        attr_matches.append(zodiac)
    if attr_matches:
        reasons.append("属性上参考了" + "、".join(attr_matches[:3]))

    recent_hits = int(recent_draw_number_counter.get(special_num, 0) or 0)
    if special_num in latest_draw_numbers:
        reasons.append("上期出现过，系统已经降了一点分")
    elif recent_hits <= 0:
        reasons.append("近期没怎么重复")
    elif recent_hits == 1:
        reasons.append("近期出现过1次，系统轻微降分")
    else:
        reasons.append(f"近期出现过{recent_hits}次，系统已经降分")

    return "为什么选这个特码：" + "；".join(reasons[:5])


def _estimate_ml_confidence(probability_map, blended_scores, special_num, model):
    ranked = sorted(blended_scores.items(), key=lambda item: item[1], reverse=True)
    top_score = float(blended_scores.get(special_num, 0.0))
    next_score = float(ranked[1][1]) if len(ranked) > 1 else 0.0
    margin = max(0.0, top_score - next_score)
    top1_rate = float(model.get("top1_hit_rate", 0.0))
    top6_rate = float(model.get("top6_hit_rate", 0.0))
    final_top1_rate = float(model.get("final_top1_hit_rate", 0.0))
    evaluation_draws = int(model.get("evaluation_draws", 0) or 0)
    target_probability = float(model.get("avg_target_probability", 0.0) or 0.0)
    calibration_score = float(model.get("calibration_score", 0.0) or 0.0)
    validation_ratio = float(model.get("validation_ratio", 1.0) or 1.0)
    raw_probability = float(probability_map.get(special_num, 0.0))
    sample_confidence = _clamp(evaluation_draws / 30.0, 0.35, 1.0)
    stability_bonus = min(max(final_top1_rate - top1_rate, 0.0), 0.08) * 60.0
    confidence = (
        28.0 +
        min(top_score, 1.0) * 32.0 +
        min(margin, 1.0) * 140.0 +
        top1_rate * 18.0 +
        top6_rate * 10.0 +
        min(raw_probability * 100.0, 8.0) +
        min(target_probability * 100.0, 5.0) +
        calibration_score * 10.0 +
        stability_bonus
    )
    confidence *= sample_confidence
    confidence *= _clamp(0.78 + validation_ratio * 0.22, 0.72, 1.04)
    return round(_clamp(confidence, 18.0, 92.0), 2)


def _get_ml_ensemble_weights(region, strategies=None):
    strategies = tuple(strategies or _select_ml_ensemble_strategies(region))
    windows = (20, 50, 100)
    raw_scores = {}
    diagnostics = {}
    confidence_values = []
    scored = _score_ml_ensemble_candidates(region, strategies=strategies)

    for idx, item in enumerate(scored):
        strategy = item["strategy"]
        base_score = max(0.08, float(item.get("score", 0.0)) / 100.0)
        # 按准确率排名拉开权重，避免归一化后长期显示为近似平均分配。
        rank_multiplier = max(0.72, 1.18 - idx * 0.16)
        recent_accuracy = max(0.0, float(item.get("recent_accuracy", 0.0)) / 100.0)
        accuracy_multiplier = 1.0 + (recent_accuracy * 0.35)
        raw_score = base_score * rank_multiplier * accuracy_multiplier
        raw_scores[strategy] = raw_score
        diagnostics[strategy] = {
            "score": round(float(item.get("score", 0.0)), 2),
            "samples": int(item.get("samples", 0) or 0),
            "bias": round(float(item.get("bias", 0.0)), 2),
            "recent_accuracy": round(float(item.get("recent_accuracy", 0.0)), 2),
            "overall_accuracy": round(float(item.get("overall_accuracy", 0.0)), 2),
            "overall_total": int(item.get("overall_total", 0) or 0),
            "overall_top6_accuracy": round(float(item.get("overall_top6_accuracy", 0.0)), 2),
            "window_accuracies": item.get("window_accuracies") or [],
            "fallback_reason": item.get("fallback_reason") or "",
            "rank_multiplier": round(rank_multiplier, 4),
            "accuracy_multiplier": round(accuracy_multiplier, 4),
            "weighted_score": round(raw_score * 100, 2),
        }
        for idx, window in enumerate(windows):
            _, total = _calculate_strategy_accuracy(region, strategy, limit=window)
            if total > 0:
                confidence_values.append(_clamp(total / 10.0, 0.25, 1.0) * max(0.45, 1.0 - idx * 0.22))

    score_total = sum(raw_scores.values())
    if score_total <= 0:
        equal_weight = round(1.0 / max(len(strategies), 1), 4)
        return {
            "weights": {strategy: equal_weight for strategy in strategies},
            "diagnostics": diagnostics,
            "confidence": 0.0,
        }

    weights = {
        strategy: round(raw_scores[strategy] / score_total, 4)
        for strategy in strategies
    }
    return {
        "weights": weights,
        "diagnostics": diagnostics,
        "confidence": round(
            (sum(confidence_values) / len(confidence_values)) if confidence_values else 0.0,
            4,
        ),
    }


def _build_ml_ensemble_signals(data, region):
    strategies = tuple(_select_ml_ensemble_strategies(region, persist=False))
    if not strategies:
        strategies = ("hybrid", "balanced", "trend")
    normal_votes = Counter()
    special_votes = Counter()
    strategy_results = {}
    weight_info = _get_ml_ensemble_weights(region, strategies)
    strategy_weights = weight_info.get("weights") or {}
    selected_strategies = tuple(strategy_weights.keys()) or strategies
    weight_scale = max(len(selected_strategies), 1)

    for strategy in selected_strategies:
        try:
            result = get_local_recommendations(strategy, data, region)
        except Exception:
            continue
        strategy_results[strategy] = result
        strategy_weight = float(strategy_weights.get(strategy, 0.0)) * weight_scale
        for number in result.get("normal", []) or []:
            try:
                normal_votes[int(number)] += strategy_weight
            except (TypeError, ValueError):
                continue
        special_number = ((result.get("special") or {}).get("number") or "").strip()
        if special_number.isdigit():
            special_votes[int(special_number)] += strategy_weight

    return {
        "normal_votes": normal_votes,
        "special_votes": special_votes,
        "strategy_results": strategy_results,
        "strategy_weights": strategy_weights,
        "selected_strategies": list(selected_strategies),
        "weight_diagnostics": weight_info.get("diagnostics") or {},
        "weight_confidence": weight_info.get("confidence", 0.0),
    }


def _build_ml_dual_score_maps(ranked_numbers, probability_map, heuristic_map, ensemble_signals):
    normal_votes = ensemble_signals.get("normal_votes") or Counter()
    special_votes = ensemble_signals.get("special_votes") or Counter()
    special_scores = {}
    normal_scores = {}

    for rank_idx, number in enumerate(ranked_numbers):
        prob_score = float(probability_map.get(number, 0.0))
        heuristic_score = float(heuristic_map.get(number, 0.0))
        special_vote = float(special_votes.get(number, 0))
        normal_vote = float(normal_votes.get(number, 0))
        rank_bonus = max(0.0, 1.0 - (rank_idx / 18.0))

        special_scores[number] = round(
            prob_score * 0.60 +
            heuristic_score * 0.22 +
            special_vote * 0.14 +
            normal_vote * 0.03 +
            rank_bonus * 0.05,
            6,
        )
        normal_scores[number] = round(
            heuristic_score * 0.36 +
            prob_score * 0.24 +
            normal_vote * 0.24 +
            special_vote * 0.04 +
            rank_bonus * 0.12,
            6,
        )

    return special_scores, normal_scores


def _score_ml_model(model):
    top1 = float(model.get("top1_hit_rate", 0.0))
    top6 = float(model.get("top6_hit_rate", 0.0))
    avg_target_probability = float(model.get("avg_target_probability", 0.0))
    calibration_score = float(model.get("calibration_score", 0.0) or 0.0)
    validation_ratio = float(model.get("validation_ratio", 1.0) or 1.0)
    evaluation_draws = int(model.get("evaluation_draws", 0) or 0)
    confidence = min(1.0, evaluation_draws / 20.0) if evaluation_draws > 0 else 0.0
    return round(
        ((top1 * 1.85) + (top6 * 1.0) + (avg_target_probability * 0.35) + (calibration_score * 0.08)) *
        max(confidence, 0.35) *
        _clamp(0.86 + validation_ratio * 0.14, 0.72, 1.02),
        6,
    )


def _optimize_ml_runtime_config(data, region, config):
    data_size = len(data or [])
    base_config = dict(config or {})
    if data_size < 80:
        model = _train_ml_number_model(data, region, base_config)
        model["runtime_config"] = base_config
        model["runtime_search"] = []
        model["runtime_profile"] = "base"
        model["runtime_score"] = _score_ml_model(model)
        return base_config, model

    base_history = _clamp(int(base_config.get("history_window") or 120), 80, 240)
    base_feature = _clamp(int(base_config.get("feature_window") or 60), 30, 90)
    base_eval = _clamp(int(base_config.get("evaluation_window") or 30), 12, 60)
    base_epochs = _clamp(int(base_config.get("epochs") or 18), 15, 30)
    base_learning_rate = float(base_config.get("learning_rate") or 0.035)
    base_l2 = float(base_config.get("l2") or 0.0025)
    base_pool = _clamp(int(base_config.get("pool") or 18), 12, 24)
    base_special_pool = _clamp(int(base_config.get("special_pool") or 8), 6, 12)
    primary_feature_profile = str(base_config.get("primary_feature_profile") or "full").strip() or "full"
    primary_runtime_profile = str(base_config.get("primary_runtime_profile") or "base").strip() or "base"
    preferred_feature_profiles = [
        str(item).strip()
        for item in (base_config.get("preferred_feature_profiles") or [])
        if str(item).strip()
    ]
    preferred_runtime_profiles = [
        str(item).strip()
        for item in (base_config.get("preferred_runtime_profiles") or [])
        if str(item).strip()
    ]
    learning_confidence = float(base_config.get("profile_learning_confidence") or 0.0)
    adaptation = _resolve_learning_adaptation(region, "ml")

    candidate_specs = [
        (primary_runtime_profile if primary_runtime_profile else "base", {"feature_profile": primary_feature_profile}),
        ("recent_bias", {
            "history_window": _clamp(base_history - 20, 80, 220),
            "feature_window": _clamp(base_feature - 8, 30, 80),
            "evaluation_window": _clamp(base_eval - 4, 12, 48),
            "learning_rate": round(_clamp(base_learning_rate + 0.008, 0.01, 0.08), 3),
            "feature_profile": "compact_structure",
        }),
        ("context_bias", {
            "history_window": _clamp(base_history + 25, 100, 240),
            "feature_window": _clamp(base_feature + 8, 36, 90),
            "evaluation_window": _clamp(base_eval + 4, 18, 60),
            "l2": round(_clamp(base_l2 + 0.0008, 0.001, 0.005), 4),
            "feature_profile": "compact_attributes",
        }),
        ("recency_trim", {
            "history_window": _clamp(base_history, 80, 240),
            "feature_window": _clamp(base_feature - 4, 30, 84),
            "evaluation_window": _clamp(base_eval, 12, 60),
            "feature_profile": "compact_recency",
        }),
        ("regularized", {
            "history_window": _clamp(base_history + 12, 90, 240),
            "feature_window": _clamp(base_feature, 30, 90),
            "evaluation_window": _clamp(base_eval + 6, 18, 60),
            "epochs": _clamp(base_epochs - 2, 15, 30),
            "learning_rate": round(_clamp(base_learning_rate - 0.006, 0.01, 0.08), 3),
            "l2": round(_clamp(base_l2 + 0.0012, 0.001, 0.006), 4),
            "blend_candidates": [0.45, 0.6, 0.74],
            "feature_profile": "full",
        }),
        ("blend_search", {
            "history_window": _clamp(base_history, 80, 240),
            "feature_window": _clamp(base_feature + 4, 30, 90),
            "evaluation_window": _clamp(base_eval, 12, 60),
            "blend_candidates": [0.5, 0.65, 0.78, 0.88],
            "pool": _clamp(base_pool + 1, 12, 24),
            "special_pool": _clamp(base_special_pool + 1, 6, 12),
            "feature_profile": primary_feature_profile,
        }),
    ]
    normalized_specs = []
    seen_profiles = set()
    for profile_name, overrides in candidate_specs:
        normalized_name = str(profile_name or "base").strip() or "base"
        candidate_feature = str(overrides.get("feature_profile") or "full").strip() or "full"
        dedupe_key = (normalized_name, candidate_feature)
        if dedupe_key in seen_profiles:
            continue
        seen_profiles.add(dedupe_key)
        normalized_specs.append((normalized_name, overrides))
    candidate_specs = normalized_specs
    top_feature_profile = preferred_feature_profiles[0] if preferred_feature_profiles else ""
    if top_feature_profile and top_feature_profile not in {item[1].get("feature_profile") for item in candidate_specs}:
        candidate_specs.append((
            "learned_feature_bias",
            {
                "history_window": _clamp(base_history + 8, 80, 240),
                "feature_window": _clamp(base_feature, 30, 90),
                "evaluation_window": _clamp(base_eval, 12, 60),
                "feature_profile": top_feature_profile,
            },
        ))

    evaluations = []
    best = None
    best_model = None
    best_config = base_config
    for profile_name, overrides in candidate_specs:
        candidate_config = {**base_config, **overrides}
        model = _train_ml_number_model(data, region, candidate_config)
        runtime_score = _score_ml_model(model)
        preference_bonus = 0.0
        candidate_feature_profile = str(candidate_config.get("feature_profile") or "full").strip() or "full"
        if candidate_feature_profile in preferred_feature_profiles:
            rank_idx = preferred_feature_profiles.index(candidate_feature_profile)
            preference_bonus += max(
                0.0,
                adaptation["ml_feature_bonus"] - rank_idx * adaptation["ml_feature_bonus_decay"],
            ) * learning_confidence
        if profile_name in preferred_runtime_profiles:
            rank_idx = preferred_runtime_profiles.index(profile_name)
            preference_bonus += max(
                0.0,
                adaptation["ml_runtime_bonus"] - rank_idx * adaptation["ml_runtime_bonus_decay"],
            ) * learning_confidence

        adjusted_runtime_score = runtime_score + preference_bonus
        model["runtime_score"] = adjusted_runtime_score
        model["runtime_profile"] = profile_name
        model["learning_adaptation_mode"] = adaptation["mode"]
        model["runtime_config"] = candidate_config
        evaluations.append({
            "profile": profile_name,
            "score": adjusted_runtime_score,
            "raw_score": runtime_score,
            "top1_hit_rate": round(float(model.get("top1_hit_rate", 0.0)) * 100, 2),
            "top6_hit_rate": round(float(model.get("top6_hit_rate", 0.0)) * 100, 2),
            "final_top1_hit_rate": round(float(model.get("final_top1_hit_rate", 0.0)) * 100, 2),
            "history_window": candidate_config.get("history_window"),
            "feature_window": candidate_config.get("feature_window"),
            "evaluation_window": candidate_config.get("evaluation_window"),
            "epochs": candidate_config.get("epochs"),
            "learning_rate": candidate_config.get("learning_rate"),
            "l2": candidate_config.get("l2"),
            "selected_blend": model.get("selected_blend"),
            "validation_ratio": round(float(model.get("validation_ratio", 1.0) or 1.0), 4),
            "feature_profile": candidate_config.get("feature_profile", "full"),
        })
        if best is None or adjusted_runtime_score > best:
            best = adjusted_runtime_score
            best_model = model
            best_config = candidate_config

    best_model["runtime_search"] = evaluations
    return best_config, best_model


def _train_ml_number_model(data, region, config):
    history_window = _clamp(int(config.get("history_window") or 120), 80, 240)
    feature_window = _clamp(int(config.get("feature_window") or 60), 30, 90)
    epochs = _clamp(int(config.get("epochs") or 15), 15, 30)
    learning_rate = float(config.get("learning_rate") or 0.035)
    l2 = float(config.get("l2") or 0.0025)
    evaluation_window = _clamp(int(config.get("evaluation_window") or 30), 12, 60)
    feature_profile = str(config.get("feature_profile") or "full").strip() or "full"
    early_stopping_patience = _clamp(int(config.get("early_stopping_patience") or 4), 2, 8)
    validation_floor = _clamp(float(config.get("validation_floor") or 0.88), 0.72, 0.98)

    recent_desc = list(data or [])[:history_window + feature_window + evaluation_window]
    chronological = list(reversed(recent_desc))
    configured_min_history = min(24, max(12, feature_window // 2))
    available_history = max(0, len(chronological) - 1)
    min_history = min(configured_min_history, max(1, available_history))
    feature_cache = {}
    blend_candidates = [
        float(candidate)
        for candidate in (config.get("blend_candidates") or [0.55, 0.7, 0.82])
        if 0.0 <= float(candidate) <= 1.0
    ]
    if not blend_candidates:
        blend_candidates = [0.7]
    blend_stats = {
        round(candidate, 4): {"top1": 0, "top6": 0}
        for candidate in blend_candidates
    }
    final_blend_stats = {
        round(candidate, 4): {"top1": 0}
        for candidate in blend_candidates
    }

    if len(chronological) <= 1:
        return {
            "weights": [],
            "bias": 0.0,
            "samples": 0,
            "draw_samples": 0,
            "evaluation_draws": 0,
            "gradient_updates": 0,
        }

    def get_feature_table(idx):
        target_draw = chronological[idx]
        cutoff_period = target_draw.get("id")
        cache_key = _normalize_period_value(cutoff_period)
        feature_table = feature_cache.get(cache_key)
        if feature_table is None:
            history_desc = list(reversed(chronological[:idx]))
            feedback = _build_prediction_feedback(
                region,
                "ml",
                cutoff_period=cutoff_period,
            )
            feature_table = _build_ml_feature_table(
                history_desc,
                region,
                feature_window=feature_window,
                feedback=feedback,
            )
            feature_table = _apply_ml_feature_profile(feature_table, feature_profile)
            feature_cache[cache_key] = feature_table
        return feature_table

    eval_start = max(min_history, len(chronological) - evaluation_window)
    eval_weights = None
    eval_bias = 0.0
    evaluation_steps = 0
    top1_hits = 0
    top6_hits = 0
    target_probability_sum = 0.0

    def evaluate_weight_snapshot(weights, bias):
        if not weights:
            return {"score": 0.0, "top1": 0, "top6": 0, "target_probability": 0.0, "steps": 0}
        steps = 0
        top1 = 0
        top6 = 0
        target_probability_total = 0.0
        for eval_idx in range(eval_start, len(chronological)):
            target_draw = chronological[eval_idx]
            target_special = str(target_draw.get("sno") or "").strip()
            if not target_special:
                continue
            feature_table = get_feature_table(eval_idx)
            snapshot_weights = _ensure_ml_weight_vector(list(weights), feature_table)
            score_pairs = _build_ml_score_pairs(feature_table, snapshot_weights, bias)
            if not score_pairs:
                continue
            target_number = int(target_special)
            probabilities = _softmax([item[2] for item in score_pairs])
            ranked_numbers = [
                int(item[0])
                for item in sorted(score_pairs, key=lambda item: item[2], reverse=True)
            ]
            if ranked_numbers and ranked_numbers[0] == target_number:
                top1 += 1
            if target_number in ranked_numbers[:6]:
                top6 += 1
            target_probability_total += next(
                (
                    probabilities[row_idx]
                    for row_idx, (key, _, _) in enumerate(score_pairs)
                    if key == target_special
                ),
                0.0,
            )
            steps += 1
        if steps <= 0:
            return {"score": 0.0, "top1": 0, "top6": 0, "target_probability": 0.0, "steps": 0}
        top1_rate = top1 / steps
        top6_rate = top6 / steps
        avg_target_probability = target_probability_total / steps
        return {
            "score": (top1_rate * 1.85) + top6_rate + (avg_target_probability * 0.35),
            "top1": top1,
            "top6": top6,
            "target_probability": avg_target_probability,
            "steps": steps,
        }

    for idx in range(min_history, len(chronological)):
        target_draw = chronological[idx]
        target_special = str(target_draw.get("sno") or "").strip()
        if not target_special:
            continue

        feature_table = get_feature_table(idx)
        eval_weights = _ensure_ml_weight_vector(eval_weights, feature_table)
        score_pairs = _build_ml_score_pairs(feature_table, eval_weights, eval_bias)
        if not score_pairs:
            continue

        if idx >= eval_start:
            target_number = int(target_special)
            probabilities = _softmax([item[2] for item in score_pairs])
            ranked_numbers = [
                int(item[0])
                for item in sorted(score_pairs, key=lambda item: item[2], reverse=True)
            ]
            if ranked_numbers and ranked_numbers[0] == target_number:
                top1_hits += 1
            if target_number in ranked_numbers[:6]:
                top6_hits += 1
            target_probability_sum += next(
                (
                    probabilities[row_idx]
                    for row_idx, (key, _, _) in enumerate(score_pairs)
                    if key == target_special
                ),
                0.0,
            )

            heuristic_map = _build_ml_heuristic_score_map(feature_table)
            probability_map = {
                int(key): probabilities[row_idx]
                for row_idx, (key, _, _) in enumerate(score_pairs)
            }
            for candidate in blend_candidates:
                blended_scores = _blend_ml_rankings(probability_map, heuristic_map, candidate)
                blended_rank = _rank_numbers(blended_scores)
                stats = blend_stats[round(candidate, 4)]
                if blended_rank and blended_rank[0] == target_number:
                    stats["top1"] += 1
                if target_number in blended_rank[:6]:
                    stats["top6"] += 1
                special_score_map, normal_score_map = _build_ml_dual_score_maps(
                    blended_rank,
                    probability_map,
                    heuristic_map,
                    {"normal_votes": Counter(), "special_votes": Counter()},
                )
                history_desc = list(reversed(chronological[:idx]))
                selection_year = _infer_draw_year(history_desc)
                selection_feedback = _build_prediction_feedback(
                    region,
                    "ml",
                    cutoff_period=target_draw.get("id"),
                )
                _, _, selection_parity_pref = _build_attribute_preferences(
                    history_desc[:feature_window],
                    region,
                    selection_feedback,
                    selection_year,
                    apply_recent_zodiac_cooldown=True,
                )
                preferred_selection_parity = (
                    max(selection_parity_pref.items(), key=lambda item: item[1])[0]
                    if selection_parity_pref else ""
                )
                selection_state = _build_ml_recent_selection_state(
                    history_desc,
                    region,
                    year=selection_year,
                )
                candidate_normal = _select_ml_normal_numbers(
                    _rank_numbers(normal_score_map),
                    normal_score_map,
                    config.get("bucket_counts") or [2, 2, 2],
                    pool_size=_clamp(int(config.get("pool") or 18), 12, 24),
                    parity_pref=selection_parity_pref,
                    recent_draw_number_counter=selection_state["recent_draw_number_counter"],
                    latest_draw_numbers=selection_state["latest_draw_numbers"],
                )
                candidate_special, _ = _select_ml_special_number(
                    _rank_numbers(special_score_map),
                    special_score_map,
                    candidate_normal,
                    _clamp(int(config.get("special_pool") or 8), 6, 12),
                    preferred_special_parity=preferred_selection_parity,
                    recent_selection_state=selection_state,
                    number_to_zodiac=_get_number_to_zodiac_map(selection_year),
                )
                if candidate_special == target_number:
                    final_blend_stats[round(candidate, 4)]["top1"] += 1
            evaluation_steps += 1

        eval_weights, eval_bias, _, _ = _update_ml_weights(
            score_pairs,
            target_special,
            eval_weights,
            eval_bias,
            learning_rate,
            l2,
        )

    fit_weights = None
    fit_bias = 0.0
    fit_steps = 0
    gradient_updates = 0
    train_start = max(min_history, 1)
    best_fit_weights = None
    best_fit_bias = 0.0
    best_validation_score = -1.0
    stale_epochs = 0
    learning_rate_scale = 1.0
    epochs_completed = 0
    stopped_early = False
    validation_history = []

    for epoch in range(epochs):
        step = learning_rate * learning_rate_scale * (0.94 ** epoch)
        for idx in range(train_start, len(chronological)):
            target_draw = chronological[idx]
            target_special = str(target_draw.get("sno") or "").strip()
            if not target_special:
                continue

            feature_table = get_feature_table(idx)
            fit_weights = _ensure_ml_weight_vector(fit_weights, feature_table)
            score_pairs = _build_ml_score_pairs(feature_table, fit_weights, fit_bias)
            if not score_pairs:
                continue

            fit_weights, fit_bias, _, _ = _update_ml_weights(
                score_pairs,
                target_special,
                fit_weights,
                fit_bias,
                step,
                l2,
            )
            fit_steps += 1
            gradient_updates += len(score_pairs)
        epochs_completed = epoch + 1
        validation_snapshot = evaluate_weight_snapshot(fit_weights, fit_bias)
        validation_score = float(validation_snapshot.get("score") or 0.0)
        validation_history.append({
            "epoch": epochs_completed,
            "score": round(validation_score, 6),
            "top1": int(validation_snapshot.get("top1") or 0),
            "top6": int(validation_snapshot.get("top6") or 0),
            "steps": int(validation_snapshot.get("steps") or 0),
            "learning_rate": round(step, 6),
        })
        if validation_score > best_validation_score + 0.00001:
            best_validation_score = validation_score
            best_fit_weights = list(fit_weights or [])
            best_fit_bias = fit_bias
            stale_epochs = 0
        else:
            stale_epochs += 1
            learning_rate_scale *= 0.72

        if best_validation_score > 0 and validation_score < best_validation_score * validation_floor:
            stale_epochs += 1
        if stale_epochs >= early_stopping_patience:
            stopped_early = True
            break

    if best_fit_weights is not None:
        fit_weights = best_fit_weights
        fit_bias = best_fit_bias

    selected_blend = blend_candidates[0]
    best_score = -1.0
    for candidate in blend_candidates:
        stats = blend_stats[round(candidate, 4)]
        candidate_score = (stats["top6"] * 1.0) + (stats["top1"] * 1.8)
        if candidate_score > best_score:
            best_score = candidate_score
            selected_blend = candidate
    selected_final_stats = final_blend_stats.get(round(selected_blend, 4), {})

    return {
        "weights": [round(weight, 6) for weight in (fit_weights or [])],
        "bias": round(fit_bias, 6),
        "samples": fit_steps,
        "draw_samples": fit_steps,
        "evaluation_draws": evaluation_steps,
        "gradient_updates": gradient_updates,
        "epochs_completed": epochs_completed,
        "stopped_early": stopped_early,
        "best_validation_score": round(max(best_validation_score, 0.0), 6),
        "validation_ratio": round(
            (
                (validation_history[-1]["score"] / best_validation_score)
                if validation_history and best_validation_score > 0 else 1.0
            ),
            6,
        ),
        "validation_history": validation_history[-6:],
        "history_window": history_window,
        "feature_window": feature_window,
        "evaluation_window": evaluation_window,
        "feature_profile": feature_profile,
        "avg_target_probability": round((target_probability_sum / evaluation_steps), 6) if evaluation_steps else 0.0,
        "calibration_score": round(
            _clamp(
                ((target_probability_sum / evaluation_steps) if evaluation_steps else 0.0) * 12.0 +
                ((top1_hits / evaluation_steps) if evaluation_steps else 0.0) * 0.45 +
                ((top6_hits / evaluation_steps) if evaluation_steps else 0.0) * 0.25,
                0.0,
                1.0,
            ),
            6,
        ),
        "top1_hit_rate": round((top1_hits / evaluation_steps), 6) if evaluation_steps else 0.0,
        "top6_hit_rate": round((top6_hits / evaluation_steps), 6) if evaluation_steps else 0.0,
        "final_top1_hit_rate": round((selected_final_stats.get("top1", 0) / evaluation_steps), 6) if evaluation_steps else 0.0,
        "selected_blend": round(selected_blend, 4),
        "blend_stats": {
            str(candidate): {
                "top1_hit_rate": round((blend_stats[round(candidate, 4)]["top1"] / evaluation_steps), 6) if evaluation_steps else 0.0,
                "top6_hit_rate": round((blend_stats[round(candidate, 4)]["top6"] / evaluation_steps), 6) if evaluation_steps else 0.0,
                "final_top1_hit_rate": round((final_blend_stats[round(candidate, 4)]["top1"] / evaluation_steps), 6) if evaluation_steps else 0.0,
            }
            for candidate in blend_candidates
        },
    }


def _build_ml_prediction_cache_key(region, data, config):
    normalized_region = str(region or "").strip().lower()
    head_periods = [
        str(item.get("id") or "").strip()
        for item in list(data or [])[:16]
    ]
    accuracy_signature = {}
    for strategy in ("hybrid", "balanced", "markov", "trend", "hot", "cold"):
        accuracy, total = _calculate_strategy_accuracy(normalized_region, strategy, limit=None)
        accuracy_signature[strategy] = {
            "accuracy": round(float(accuracy or 0.0), 6),
            "total": int(total or 0),
        }
    payload = {
        "cache_version": 4,
        "region": normalized_region,
        "backtest_cutoff_period": _current_backtest_cutoff_period(),
        "periods": head_periods,
        "draw_count": len(data or []),
        "updated_at": str(config.get("updated_at") or ""),
        "primary_runtime_profile": str(config.get("primary_runtime_profile") or ""),
        "primary_feature_profile": str(config.get("primary_feature_profile") or ""),
        "preferred_runtime_profiles": list(config.get("preferred_runtime_profiles") or []),
        "preferred_feature_profiles": list(config.get("preferred_feature_profiles") or []),
        "blend_candidates": list(config.get("blend_candidates") or []),
        "profile_learning_confidence": float(config.get("profile_learning_confidence") or 0.0),
        "history_window": int(config.get("history_window") or 0),
        "feature_window": int(config.get("feature_window") or 0),
        "evaluation_window": int(config.get("evaluation_window") or 0),
        "epochs": int(config.get("epochs") or 0),
        "learning_rate": float(config.get("learning_rate") or 0.0),
        "l2": float(config.get("l2") or 0.0),
        "early_stopping_patience": int(config.get("early_stopping_patience") or 0),
        "validation_floor": float(config.get("validation_floor") or 0.0),
        "pool": int(config.get("pool") or 0),
        "special_pool": int(config.get("special_pool") or 0),
        "bucket_counts": list(config.get("bucket_counts") or []),
        "ensemble_core_strategies": list(config.get("ensemble_core_strategies") or []),
        "ensemble_replace_margin": float(config.get("ensemble_replace_margin") or 0.0),
        "ensemble_replace_min_samples": int(config.get("ensemble_replace_min_samples") or 0),
        "accuracy_signature": accuracy_signature,
    }
    fingerprint = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(fingerprint.encode("utf-8")).hexdigest()


def _ml_prediction_cache_meta(region, data):
    return _prediction_cache_meta(region, data)


def _prune_stale_ml_prediction_cache(region, latest_period):
    normalized_region = str(region or "").strip().lower()
    latest_period = str(latest_period or "").strip()
    if not normalized_region or not latest_period:
        return
    with _ml_prediction_cache_lock:
        stale_keys = [
            key
            for key, item in _ml_prediction_cache.items()
            if str(item.get("region") or "").strip().lower() == normalized_region
            and str(item.get("latest_period") or "").strip() != latest_period
        ]
        for key in stale_keys:
            _ml_prediction_cache.pop(key, None)


def _clear_ml_prediction_cache(region=None):
    normalized_region = str(region or "").strip().lower()
    with _ml_prediction_cache_lock:
        if not normalized_region:
            _ml_prediction_cache.clear()
            for event in _ml_prediction_build_events.values():
                event.set()
            _ml_prediction_build_events.clear()
            return
        for key in [
            key
            for key, item in _ml_prediction_cache.items()
            if str(item.get("region") or "").strip().lower() == normalized_region
        ]:
            _ml_prediction_cache.pop(key, None)
        for event in _ml_prediction_build_events.values():
            event.set()
        _ml_prediction_build_events.clear()


def _get_cached_ml_prediction_artifacts(cache_key):
    now = time.time()
    ttl_seconds = _ml_prediction_cache_ttl_seconds()
    with _ml_prediction_cache_lock:
        cached = _ml_prediction_cache.get(cache_key)
        if not cached:
            return None
        cached_at = float(cached.get("cached_at") or 0.0)
        if ttl_seconds is not None and now - cached_at > ttl_seconds:
            _ml_prediction_cache.pop(cache_key, None)
            return None
        cached["cached_at"] = now
        return copy.deepcopy(cached.get("artifacts"))


def _ml_prediction_cache_ttl_seconds():
    return _prediction_cache_ttl_seconds(_ML_PREDICTION_CACHE_TTL_SECONDS)


def _store_cached_ml_prediction_artifacts(cache_key, artifacts, region=None, latest_period=None):
    now = time.time()
    ttl_seconds = _ml_prediction_cache_ttl_seconds()
    with _ml_prediction_cache_lock:
        if ttl_seconds is not None:
            expired_keys = [
                key
                for key, item in _ml_prediction_cache.items()
                if now - float(item.get("cached_at") or 0.0) > ttl_seconds
            ]
            for key in expired_keys:
                _ml_prediction_cache.pop(key, None)
        while len(_ml_prediction_cache) >= _ML_PREDICTION_CACHE_MAX_ITEMS:
            oldest_key = min(
                _ml_prediction_cache.keys(),
                key=lambda key: float(_ml_prediction_cache[key].get("cached_at") or 0.0),
            )
            _ml_prediction_cache.pop(oldest_key, None)
        _ml_prediction_cache[cache_key] = {
            "cached_at": now,
            "region": str(region or "").strip().lower(),
            "latest_period": str(latest_period or "").strip(),
            "artifacts": copy.deepcopy(artifacts),
        }


def _claim_ml_prediction_build(cache_key):
    with _ml_prediction_cache_lock:
        event = _ml_prediction_build_events.get(cache_key)
        if event is not None:
            return event, False
        event = threading.Event()
        _ml_prediction_build_events[cache_key] = event
        return event, True


def _finish_ml_prediction_build(cache_key):
    with _ml_prediction_cache_lock:
        event = _ml_prediction_build_events.pop(cache_key, None)
        if event is not None:
            event.set()


def _build_uncached_ml_prediction_artifacts(enriched_data, supplemental_draws, region, config):
    runtime_config, model = _optimize_ml_runtime_config(enriched_data, region, config)
    feature_window = _clamp(int(runtime_config.get("feature_window") or 60), 30, 90)
    year = _infer_draw_year(enriched_data)
    feedback = _build_prediction_feedback(region, "ml")
    color_pref, _, parity_pref = _build_attribute_preferences(
        enriched_data[:feature_window], region, feedback, year, apply_recent_zodiac_cooldown=True
    )
    feature_table = _build_ml_feature_table(enriched_data, region, feature_window=feature_window)
    feature_table = _apply_ml_feature_profile(
        feature_table,
        runtime_config.get("feature_profile") or model.get("feature_profile"),
    )
    score_pairs = _build_ml_score_pairs(
        feature_table,
        model.get("weights", []),
        model.get("bias", 0.0),
    )
    probability_map = _build_ml_probability_map(score_pairs)
    heuristic_map = _build_ml_heuristic_score_map(feature_table)
    blend_weight = float(model.get("selected_blend", 0.7) or 0.7)
    blended_scores = _blend_ml_rankings(probability_map, heuristic_map, blend_weight)
    ranked_numbers = _rank_numbers(blended_scores)
    ensemble_signals = _build_ml_ensemble_signals(enriched_data, region)
    special_score_map, normal_score_map = _build_ml_dual_score_maps(
        ranked_numbers,
        probability_map,
        heuristic_map,
        ensemble_signals,
    )

    return {
        "enriched_data": enriched_data,
        "supplemental_draws": supplemental_draws,
        "runtime_config": runtime_config,
        "model": model,
        "year": year,
        "color_pref": color_pref,
        "parity_pref": parity_pref,
        "probability_map": probability_map,
        "blend_weight": blend_weight,
        "ensemble_signals": ensemble_signals,
        "special_score_map": special_score_map,
        "normal_score_map": normal_score_map,
        "special_ranked_numbers": _rank_numbers(special_score_map),
        "normal_ranked_numbers": _rank_numbers(normal_score_map),
    }


def _build_ml_prediction_artifacts(data, region):
    enriched_data, supplemental_draws = _ensure_ml_prediction_history(data, region)
    config = _load_strategy_config("ml", region)
    cache_key = _build_ml_prediction_cache_key(region, enriched_data, config)
    cache_region, cache_latest_period = _ml_prediction_cache_meta(region, enriched_data)
    _prune_stale_ml_prediction_cache(cache_region, cache_latest_period)
    cached_artifacts = _get_cached_ml_prediction_artifacts(cache_key)
    if cached_artifacts is not None:
        return cached_artifacts

    build_event, should_build = _claim_ml_prediction_build(cache_key)
    if not should_build:
        build_event.wait()
        cached_artifacts = _get_cached_ml_prediction_artifacts(cache_key)
        if cached_artifacts is not None:
            return cached_artifacts
        build_event, should_build = _claim_ml_prediction_build(cache_key)

    try:
        cached_artifacts = _get_cached_ml_prediction_artifacts(cache_key)
        if cached_artifacts is not None:
            return cached_artifacts

        artifacts = _build_uncached_ml_prediction_artifacts(
            enriched_data,
            supplemental_draws,
            region,
            config,
        )
        _store_cached_ml_prediction_artifacts(
            cache_key,
            artifacts,
            region=cache_region,
            latest_period=cache_latest_period,
        )
        return artifacts
    finally:
        _finish_ml_prediction_build(cache_key)


def _predict_with_ml(data, region, variation_key=None):
    artifacts = _build_ml_prediction_artifacts(data, region)
    enriched_data = artifacts["enriched_data"]
    supplemental_draws = artifacts["supplemental_draws"]
    runtime_config = artifacts["runtime_config"]
    model = artifacts["model"]
    year = artifacts["year"]
    color_pref = artifacts.get("color_pref") or {}
    parity_pref = artifacts.get("parity_pref") or {}
    probability_map = artifacts.get("probability_map") or {}
    blend_weight = float(artifacts.get("blend_weight", 0.7) or 0.7)
    ensemble_signals = artifacts.get("ensemble_signals") or {}
    special_score_map = artifacts.get("special_score_map") or {}
    normal_score_map = artifacts.get("normal_score_map") or {}
    special_ranked_numbers = list(artifacts.get("special_ranked_numbers") or [])
    normal_ranked_numbers = list(artifacts.get("normal_ranked_numbers") or [])
    recent_selection_state = _build_ml_recent_selection_state(enriched_data, region, year=year)
    recent_draw_number_counter = recent_selection_state["recent_draw_number_counter"]
    latest_draw_numbers = recent_selection_state["latest_draw_numbers"]
    number_to_zodiac = _get_number_to_zodiac_map(year)

    pool_size = _clamp(int(runtime_config.get("pool") or 18), 12, 24)
    special_pool_size = _clamp(int(runtime_config.get("special_pool") or 8), 6, 12)
    bucket_counts = runtime_config.get("bucket_counts") or [2, 2, 2]
    normal = _select_ml_normal_numbers(
        normal_ranked_numbers,
        normal_score_map,
        bucket_counts,
        variation_key=variation_key,
        pool_size=pool_size,
        parity_pref=parity_pref,
        recent_draw_number_counter=recent_draw_number_counter,
        latest_draw_numbers=latest_draw_numbers,
    )

    preferred_special_color = max(color_pref.items(), key=lambda item: item[1])[0] if color_pref else ""
    preferred_special_parity = max(parity_pref.items(), key=lambda item: item[1])[0] if parity_pref else ""
    special_num, special_candidates = _select_ml_special_number(
        special_ranked_numbers,
        special_score_map,
        normal,
        special_pool_size,
        preferred_special_parity=preferred_special_parity,
        recent_selection_state=recent_selection_state,
        number_to_zodiac=number_to_zodiac,
        variation_key=variation_key,
    )
    special_probability = _estimate_ml_confidence(
        probability_map,
        special_score_map,
        special_num,
        model,
    )
    special_selection_reason = _build_ml_special_selection_reason(
        special_num,
        special_candidates,
        special_score_map,
        ensemble_signals.get("special_votes") or Counter(),
        preferred_special_color=preferred_special_color,
        preferred_special_parity=preferred_special_parity,
        recent_selection_state=recent_selection_state,
        number_to_zodiac=number_to_zodiac,
    )
    samples = int(model.get("samples", 0) or 0)
    history_window = int(model.get("history_window", 0) or 0)
    if samples > 0:
        extra_reason = f"基于最近{history_window}期历史样本训练生成。"
        if supplemental_draws > 0:
            extra_reason += f" 当前年度样本不足，已自动补入{supplemental_draws}期跨年历史。"
    else:
        extra_reason = "当前年度历史样本不足，机器学习训练未完成，已回退为统计特征融合推荐。"
        if supplemental_draws > 0:
            extra_reason += f" 已额外补入{supplemental_draws}期历史记录。"
    recommendation_text = _build_special_focus_text(
        str(special_num),
        normal,
        strategy_name="机器学习预测",
        samples=samples,
        confidence=special_probability,
        extra_reason=extra_reason,
    )
    model_meta = {
        "samples": samples,
        "draw_samples": model.get("draw_samples", 0),
        "evaluation_draws": model.get("evaluation_draws", 0),
        "gradient_updates": model.get("gradient_updates", 0),
        "epochs_completed": model.get("epochs_completed", 0),
        "stopped_early": bool(model.get("stopped_early", False)),
        "best_validation_score": round(float(model.get("best_validation_score", 0.0)) * 100, 2),
        "validation_ratio": round(float(model.get("validation_ratio", 1.0)) * 100, 2),
        "validation_history": model.get("validation_history", []),
        "history_window": history_window,
        "feature_window": model.get("feature_window", 0),
        "evaluation_window": model.get("evaluation_window", 0),
        "feature_profile": model.get("feature_profile", runtime_config.get("feature_profile", "full")),
        "runtime_profile": model.get("runtime_profile", "base"),
        "runtime_score": round(float(model.get("runtime_score", 0.0)) * 100, 2),
        "runtime_search": model.get("runtime_search", []),
        "special_probability": special_probability,
        "top1_hit_rate": round(float(model.get("top1_hit_rate", 0.0)) * 100, 2),
        "top6_hit_rate": round(float(model.get("top6_hit_rate", 0.0)) * 100, 2),
        "final_top1_hit_rate": round(float(model.get("final_top1_hit_rate", 0.0)) * 100, 2),
        "avg_target_probability": round(float(model.get("avg_target_probability", 0.0)) * 100, 2),
        "calibration_score": round(float(model.get("calibration_score", 0.0)) * 100, 2),
        "selected_blend": round(blend_weight * 100, 2),
        "normal_numbers": list(normal),
        "selected_special_number": str(special_num),
        "special_selection_reason": special_selection_reason,
        "preferred_feature_profiles": runtime_config.get("preferred_feature_profiles", []),
        "preferred_runtime_profiles": runtime_config.get("preferred_runtime_profiles", []),
        "profile_learning_confidence": round(
            float(runtime_config.get("profile_learning_confidence", 0.0)) * 100,
            2,
        ),
        "primary_feature_profile": runtime_config.get("primary_feature_profile", "full"),
        "primary_runtime_profile": runtime_config.get("primary_runtime_profile", "base"),
        "promotion_strength": runtime_config.get("promotion_strength", "hold"),
        "learning_adaptation_mode": runtime_config.get("learning_adaptation_mode") or model.get("learning_adaptation_mode", "balanced"),
        "blended_special_score": round(float(special_score_map.get(special_num, 0.0)) * 100, 2),
        "ensemble_strategy_weights": {
            key: round(float(value) * 100, 2)
            for key, value in (ensemble_signals.get("strategy_weights") or {}).items()
        },
        "ensemble_selected_strategies": list(ensemble_signals.get("selected_strategies") or []),
        "ensemble_weight_diagnostics": ensemble_signals.get("weight_diagnostics", {}),
        "ensemble_weight_confidence": round(
            float(ensemble_signals.get("weight_confidence", 0.0)) * 100,
            2,
        ),
        "ensemble_normal_votes": dict(sorted((ensemble_signals.get("normal_votes") or Counter()).items())),
        "ensemble_special_votes": dict(sorted((ensemble_signals.get("special_votes") or Counter()).items())),
        "special_candidates": list(special_candidates[:max(special_pool_size, 6)]),
        "supplemental_draws": supplemental_draws,
        "training_draws": len(enriched_data),
        "preferred_special_color": preferred_special_color,
        "color_preferences": {
            key: round(float(value) * 100, 2)
            for key, value in sorted((color_pref or {}).items())
        },
        "preferred_special_parity": preferred_special_parity,
        "parity_preferences": {
            key: round(float(value) * 100, 2)
            for key, value in sorted((parity_pref or {}).items())
        },
    }
    model_meta["display_copy"] = _build_ml_display_copy(model_meta)

    return {
        "normal": normal,
        "special": {"number": str(special_num), "sno_zodiac": number_to_zodiac.get(str(special_num), "")},
        "recommendation_text": recommendation_text,
        "model_meta": model_meta,
    }

def get_local_recommendations(strategy, data, region, variation_key=None):
    all_numbers = list(range(1, 50))
    if not data:
        return _build_default_baseline_prediction()
    elif strategy == 'ml':
        return _predict_with_ml(data, region, variation_key=variation_key)
    elif strategy == 'markov':
        return _predict_with_markov(data, region, variation_key=variation_key)
    else:
        try:
            config = _load_strategy_config(strategy, region)
            bootstrap_window = int(config.get("window") or 0)
            recent_data = data[:bootstrap_window] if bootstrap_window > 0 else data
            phase_profile = _resolve_local_strategy_phase_profile(recent_data, config)
            runtime_profile = _resolve_local_phase_runtime(config, strategy, phase_profile)
            window = int(runtime_profile.get("window") or bootstrap_window or len(data))
            pool_size = _clamp(int(runtime_profile.get("pool") or config.get("pool") or 16), 8, 24)
            special_pool_size = _clamp(int(runtime_profile.get("special_pool") or config.get("special_pool") or max(8, pool_size // 2)), 6, 14)
            recent_data = data[:window] if window > 0 else data
            trend_window = int(runtime_profile.get("trend_window") or config.get("trend_window") or min(15, len(data)))
            trend_data = data[:trend_window] if trend_window > 0 else recent_data
            phase_profile = _resolve_local_strategy_phase_profile(recent_data, config)
            strategy_profile = _build_local_strategy_signal_profile(strategy, phase_profile, config=config)
            strategy_handoff = _resolve_local_phase_strategy_handoff(strategy, region, phase_profile)
            layered_stats = dict(config.get("layered_hit_rates") or {})
            layered_aggregate = dict(layered_stats.get("aggregate") or {})
            local_top6 = _safe_float(layered_aggregate.get("top6"), 0.0)

            special_freq = analyze_special_number_frequency(recent_data)
            trend_freq = analyze_special_number_frequency(trend_data)
            short_freq = analyze_special_number_frequency(recent_data[:10] if len(recent_data) >= 10 else recent_data)
            medium_freq = analyze_special_number_frequency(recent_data[:24] if len(recent_data) >= 24 else recent_data)
            all_freq = _build_number_frequency(recent_data)
            overdue = _build_overdue_scores(recent_data)

            if not any(v > 0 for v in special_freq.values()):
                raise ValueError("No frequency data")

            hot_norm = _normalize_metric_map(special_freq)
            trend_norm = _normalize_metric_map(trend_freq)
            short_norm = _normalize_metric_map(short_freq)
            medium_norm = _normalize_metric_map(medium_freq)
            normal_norm = _normalize_metric_map(all_freq)
            overdue_norm = _normalize_metric_map(overdue)
            cold_norm = {str(i): round(1.0 - hot_norm.get(str(i), 0.0), 4) for i in range(1, 50)}

            year = _infer_draw_year(recent_data)
            number_to_zodiac = _get_number_to_zodiac_map(year)
            feedback = _build_prediction_feedback(region, strategy)
            repeat_transition_profile = _build_repeat_transition_profile(recent_data, region, year=year)
            color_pref, zodiac_pref, parity_pref = _build_attribute_preferences(
                recent_data,
                region,
                feedback,
                year,
                apply_recent_zodiac_cooldown=(strategy != "cold"),
            )
            feedback_confidence = float(feedback.get("confidence") or 0.0)
            trend_reversal_profile = _build_local_trend_reversal_profile(short_norm, medium_norm, hot_norm)
            trend_reversal_scores = trend_reversal_profile.get("scores") or {}
            cold_trap_profile = _build_local_cold_trap_profile(
                cold_norm,
                overdue_norm,
                medium_norm,
                normal_norm,
                feedback=feedback,
                feedback_confidence=feedback_confidence,
            )
            cold_trap_scores = cold_trap_profile.get("scores") or {}
            recent_draw_number_hits = Counter()
            recent_draw_sets = []
            for item in recent_data[:6]:
                draw_numbers = []
                for raw_number in list(item.get("no") or []) + [item.get("sno")]:
                    try:
                        parsed = int(str(raw_number).strip())
                    except (TypeError, ValueError):
                        continue
                    if 1 <= parsed <= 49:
                        draw_numbers.append(parsed)
                deduped_draw = _dedupe_keep_order(draw_numbers)
                if deduped_draw:
                    recent_draw_sets.append(set(deduped_draw))
                    for parsed in deduped_draw:
                        recent_draw_number_hits[parsed] += 1
            latest_draw_numbers = recent_draw_sets[0] if recent_draw_sets else set()
            latest_special = repeat_transition_profile.get("latest_special")
            latest_zodiac = str(repeat_transition_profile.get("latest_zodiac") or "").strip()
            latest_special_repeat_probability = float(repeat_transition_profile.get("latest_special_repeat_probability") or 0.0)
            latest_zodiac_repeat_probability = float(repeat_transition_profile.get("latest_zodiac_repeat_probability") or 0.0)

            weights = _resolve_local_phase_weights(config, phase_profile)
            for key, delta in dict(strategy_handoff.get("boost_map") or {}).items():
                weights[key] = round(_clamp(_safe_float(weights.get(key), 0.0) + _safe_float(delta, 0.0), 0.0, 1.8), 4)
            feedback_multiplier = float(strategy_profile.get("feedback_multiplier", 1.0))
            attribute_multiplier = float(strategy_profile.get("attribute_multiplier", 1.0))
            overheat_multiplier = float(strategy_profile.get("overheat_multiplier", 1.0))
            hot_multiplier = float(strategy_profile.get("hot_multiplier", 1.0))
            cold_multiplier = float(strategy_profile.get("cold_multiplier", 1.0))
            trend_multiplier = float(strategy_profile.get("trend_multiplier", 1.0))
            
            def overheat_penalty(number):
                if strategy != "cold":
                    number_zodiac = number_to_zodiac.get(str(number), "")
                    if latest_special is not None and int(number) == int(latest_special):
                        return -(0.18 + max(0.0, 0.24 - latest_special_repeat_probability))
                    if latest_zodiac and number_zodiac == latest_zodiac:
                        return -(0.12 + max(0.0, 0.20 - latest_zodiac_repeat_probability))
                    if number in latest_draw_numbers:
                        return -0.3
                    draw_heat = int(recent_draw_number_hits.get(int(number), 0) or 0)
                    if draw_heat >= 2:
                        return -0.22
                    if draw_heat == 1:
                        return -0.08
                count = short_freq.get(str(number), 0)
                if count >= 3: return -0.40
                if count == 2: return -0.15
                return 0.0

            def attribute_score(number):
                color_score = color_pref.get(_get_color_zh(number), 0.0)
                zodiac_score = zodiac_pref.get(number_to_zodiac.get(str(number), ""), 0.0)
                parity_score = parity_pref.get(_get_parity_zh(number), 0.0)
                
                base_score = (
                    float(weights.get("color", 0.0)) * color_score +
                    float(weights.get("zodiac", 0.0)) * zodiac_score +
                    float(weights.get("parity", 0.0)) * parity_score
                )
                
                # 共振加成：如果波色、生肖、单双均具有较高偏好度，给予 1.2 倍爆分乘数
                if color_score > 0.6 and zodiac_score > 0.6 and parity_score > 0.6:
                    base_score *= 1.2
                return base_score

            number_scores = {}
            for number in all_numbers:
                key = str(number)
                feedback_score = (
                    (feedback.get("special", {}).get(key, 0.5) - 0.5) * 0.72 +
                    (feedback.get("normal", {}).get(key, 0.5) - 0.5) * 0.28
                ) * feedback_confidence
                
                attr_score = attribute_score(number)
                penalty = overheat_penalty(number)
                trend_reversal_penalty = trend_reversal_scores.get(key, 0.0) if strategy in ("trend", "hybrid") else 0.0
                cold_trap_penalty = cold_trap_scores.get(key, 0.0) if strategy in ("cold", "hybrid") else 0.0
                
                score = (
                    float(weights.get("hot", 0.0)) * hot_norm.get(key, 0.0) * hot_multiplier +
                    float(weights.get("trend", 0.0)) * trend_norm.get(key, 0.0) * trend_multiplier +
                    float(weights.get("cold", 0.0)) * cold_norm.get(key, 0.0) * cold_multiplier +
                    float(weights.get("normal", 0.0)) * normal_norm.get(key, 0.0) +
                    float(weights.get("overdue", 0.0)) * overdue_norm.get(key, 0.0) +
                    float(weights.get("feedback", 0.0)) * feedback_score * feedback_multiplier +
                    (attr_score * attribute_multiplier) + (penalty * overheat_multiplier) -
                    (trend_reversal_penalty * 0.85) -
                    (cold_trap_penalty * 0.72)
                )
                
                # 趋势共振：反馈好且属性契合且未过热时，指数级放大其基础权重
                if feedback_score > 0.15 and attr_score > 0.4 and penalty == 0:
                    score *= 1.25
                number_scores[number] = round(score, 6)

            hot_rank = _rank_numbers({
                number: (
                    hot_norm.get(str(number), 0.0) +
                    trend_norm.get(str(number), 0.0) * 0.35 +
                    ((feedback.get("special", {}).get(str(number), 0.5) - 0.5) * feedback_confidence * 0.75) +
                    attribute_score(number) + overheat_penalty(number)
                )
                for number in all_numbers
            })
            cold_rank = _rank_numbers({
                number: (
                    cold_norm.get(str(number), 0.0) +
                    overdue_norm.get(str(number), 0.0) * 0.8 +
                    ((feedback.get("special", {}).get(str(number), 0.5) - 0.5) * feedback_confidence * 0.45) +
                    attribute_score(number) + (overheat_penalty(number) * 0.5) -
                    cold_trap_scores.get(str(number), 0.0)
                )
                for number in all_numbers
            })
            trend_rank = _rank_numbers({
                number: (
                    trend_norm.get(str(number), 0.0) * 1.1 +
                    hot_norm.get(str(number), 0.0) * 0.35 +
                    ((feedback.get("special", {}).get(str(number), 0.5) - 0.5) * feedback_confidence * 0.70) +
                    attribute_score(number) + overheat_penalty(number) -
                    trend_reversal_scores.get(str(number), 0.0)
                )
                for number in all_numbers
            })
            overall_rank = _rank_numbers(number_scores)

            if strategy == 'hot':
                normal = sorted(_take_personalized_ranked(
                    hot_rank[:max(pool_size, 6)], 6, variation_key=variation_key, window_size=max(pool_size, 12)
                ))
            elif strategy == 'cold':
                normal = sorted(_take_personalized_ranked(
                    cold_rank[:max(pool_size, 6)], 6, variation_key=variation_key, window_size=max(pool_size, 12)
                ))
            elif strategy == 'trend':
                normal = sorted(_take_personalized_ranked(
                    trend_rank[:max(pool_size, 6)], 6, variation_key=variation_key, window_size=max(pool_size, 12)
                ))
            elif strategy == 'hybrid':
                mix = _resolve_local_hybrid_mix(config, region, str(phase_profile.get("label") or "neutral"))
                hybrid_anneal_profile = dict(mix.pop("_anneal_profile", {}) or {})
                template_mix = dict(runtime_profile.get("mix") or {})
                if template_mix:
                    mix.update(template_mix)
                normal = []
                normal += _take_personalized_ranked(hot_rank[:pool_size], int(mix.get("hot", 2)), variation_key=variation_key, window_size=max(pool_size, 9))
                normal += _take_personalized_ranked(cold_rank[:pool_size], int(mix.get("cold", 2)), variation_key=variation_key, exclude=normal, window_size=max(pool_size, 9))
                normal += _take_personalized_ranked(trend_rank[:pool_size], int(mix.get("trend", 2)), variation_key=variation_key, exclude=normal, window_size=max(pool_size, 9))
                if len(normal) < 6:
                    normal += _take_personalized_ranked(
                        overall_rank[:pool_size * 2],
                        6 - len(normal),
                        variation_key=variation_key,
                        exclude=normal,
                        window_size=max(pool_size * 2, 12)
                    )
                normal = sorted(normal)
            else:
                hybrid_anneal_profile = {}
                bucket_counts = _resolve_local_bucket_counts(
                    runtime_profile.get("bucket_counts") or config.get("bucket_counts") or [2, 2, 2],
                    str(phase_profile.get("label") or "neutral"),
                    top6_rate=local_top6,
                )
                low_count, mid_count, high_count = bucket_counts
                low_bucket = [n for n in overall_rank if n <= 16]
                mid_bucket = [n for n in overall_rank if 17 <= n <= 33]
                high_bucket = [n for n in overall_rank if n >= 34]
                normal = []
                normal += _take_personalized_ranked(low_bucket, int(low_count), variation_key=variation_key)
                normal += _take_personalized_ranked(mid_bucket, int(mid_count), variation_key=variation_key, exclude=normal)
                normal += _take_personalized_ranked(high_bucket, int(high_count), variation_key=variation_key, exclude=normal)
                if len(normal) < 6:
                    normal += _take_personalized_ranked(
                        overall_rank[:pool_size * 2],
                        6 - len(normal),
                        variation_key=variation_key,
                        exclude=normal,
                        window_size=max(pool_size * 2, 12)
                    )
                normal = sorted(normal)

            normal = _rebalance_selected_numbers_by_parity(
                normal,
                overall_rank,
                number_scores,
                parity_pref,
                count=6,
            )
            combo_shape_profile = {"adjusted": False}
            if strategy == "balanced":
                normal, combo_shape_profile = _rebalance_local_combo_shape(
                    normal,
                    overall_rank,
                    number_scores,
                    recent_data,
                    region,
                    year,
                )
            remaining_numbers = [number for number in all_numbers if number not in normal]
            
            preferred_parity = max(parity_pref.items(), key=lambda item: item[1])[0] if parity_pref else ""

            def compute_special_score(number):
                return _compute_local_special_score(
                    strategy,
                    number,
                    feedback,
                    feedback_confidence,
                    weights,
                    hot_norm,
                    trend_norm,
                    overdue_norm,
                    attribute_score,
                    overheat_penalty,
                    preferred_parity,
                    strategy_profile,
                )

            special_rank = _rank_numbers(
                {
                    number: compute_special_score(number) for number in remaining_numbers
                },
                candidates=remaining_numbers
            )
            special_candidates = special_rank[:special_pool_size] or [n for n in overall_rank if n not in normal]
            special_pick = _take_personalized_ranked(
                special_candidates,
                1,
                variation_key=variation_key,
                window_size=max(special_pool_size // 2, 2)
            )
            special_num = special_pick[0] if special_pick else special_candidates[0]
            special_zodiac = number_to_zodiac.get(str(special_num), "")
            recommendation_text = _build_local_recommendation_text(strategy, config, normal, special_num, feedback)
            return {
                "normal": normal,
                "special": {"number": str(special_num), "sno_zodiac": special_zodiac},
                "recommendation_text": recommendation_text,
                "model_meta": {
                    "special_candidates": list(special_candidates[:max(special_pool_size, 6)]),
                    "local_phase_profile": phase_profile,
                    "local_runtime_profile": runtime_profile,
                    "local_strategy_profile": strategy_profile,
                    "local_phase_weight_profile": dict((((config or {}).get("phase_weight_learning") or {}).get(str(phase_profile.get("label") or "neutral")) or {})),
                    "local_strategy_handoff": strategy_handoff,
                    "local_trend_reversal_profile": {
                        "active": bool(trend_reversal_profile.get("active")),
                        "active_count": int(trend_reversal_profile.get("active_count") or 0),
                    },
                    "local_cold_trap_profile": {
                        "active": bool(cold_trap_profile.get("active")),
                        "active_count": int(cold_trap_profile.get("active_count") or 0),
                    },
                    "local_combo_shape_profile": combo_shape_profile,
                    "local_hybrid_anneal_profile": hybrid_anneal_profile if strategy == "hybrid" else {},
                },
            }
        except Exception as e:
            if _backtest_strict_strategy_enabled():
                raise
            print(f"{strategy} recommendation failed, falling back to balanced. Reason: {e}")
            if strategy == 'balanced':
                return _build_default_baseline_prediction()
            return get_local_recommendations('balanced', data, region, variation_key=variation_key)

def _build_ai_prompt(data, region, history_window=10):
    history_lines = []
    recent_data = data[:history_window]
    learning_context = _build_ai_learning_context(data, region, history_window=history_window)
    if region == 'hk':
        year = datetime.now().year
        if recent_data:
            try:
                year = int(str(recent_data[0].get('date', ''))[:4])
            except (TypeError, ValueError):
                pass
        number_to_zodiac = _get_number_to_zodiac_map(year)
        for d in recent_data:
            zodiac = number_to_zodiac.get(str(d.get('sno')), '')
            history_lines.append(
                f"日期: {d['date']}, 开奖号码: {', '.join(d['no'])}, 特别号码: {d.get('sno')}({zodiac})"
            )
        recent_history = "\n".join(history_lines)
        prompt = f"""你是一位精通香港六合彩数据分析的专家。请基于以下最近 {history_window} 期的开奖历史数据（包含号码和生肖）以及系统自动学习得到的历史命中反馈，为下一期提供一份详细的分析和号码推荐。

历史数据:
{recent_history}

{learning_context}

你的任务是：
1. 写一段详细的分析说明，解释你的推荐依据和分析过程。
2. 明确推荐一组号码（6平码1特码），格式为：
   推荐号码：[平码1, 平码2, 平码3, 平码4, 平码5, 平码6] 特码: [特码]
3. 请以友好、自然的语言风格进行回复。
 4. 确保你的回复中包含明确的号码推荐，便于系统提取。
 5. 请显式参考历史学习反馈，避免只做泛泛分析。"""
    else:
        for d in recent_data:
            all_numbers = d.get('no', []) + ([d.get('sno')] if d.get('sno') else [])
            history_lines.append(
                f"期号: {d['id']}, 开奖号码: {','.join(all_numbers)}, 波色: {d['raw_wave']}, 生肖: {d['raw_zodiac']}"
            )
        recent_history = "\n".join(history_lines)
        prompt = f"""你是一位精通澳门六合彩数据分析的专家。请基于以下最近 {history_window} 期的开奖历史数据（包含开奖号码、波色和生肖）以及系统自动学习得到的历史命中反馈，为下一期提供一份详细的分析和号码推荐。

历史数据:
{recent_history}

{learning_context}

你的任务是：
1. 写一段详细的分析说明，解释你的推荐依据和分析过程。
2. 明确推荐一组号码（6平码1特码），格式为：
   推荐号码：[平码1, 平码2, 平码3, 平码4, 平码5, 平码6] 特码: [特码]
3. 请以友好、自然的语言风格进行回复。
 4. 确保你的回复中包含明确的号码推荐，便于系统提取。
 5. 请显式参考历史学习反馈，避免只做泛泛分析。"""
    return prompt

def _build_ai_prompt_v2(data, region, history_window=10):
    history_lines = []
    recent_data = data[:history_window]
    learning_context = _build_ai_learning_context(data, region, history_window=history_window)
    candidate_context = _build_ai_candidate_context(data, region)

    if region == 'hk':
        year = datetime.now().year
        if recent_data:
            try:
                year = int(str(recent_data[0].get('date', ''))[:4])
            except (TypeError, ValueError):
                pass
        number_to_zodiac = _get_number_to_zodiac_map(year)
        for d in recent_data:
            zodiac = number_to_zodiac.get(str(d.get('sno')), '')
            history_lines.append(
                f"日期: {d['date']}, 开奖号码: {', '.join(d['no'])}, 特码: {d.get('sno')}({zodiac})"
            )
        recent_history = "\n".join(history_lines)
        return f"""请基于以下信息，为下一期香港六合彩给出一次更偏保守、优先参考候选池共识的预测。

历史数据：
{recent_history}

{learning_context}

{candidate_context}

你的任务：
1. 综合历史反馈、候选池共识、波色/生肖/单双偏好，再做最终选择。
2. 平码 6 个必须互不重复，且不能与特码重复。
3. 若多个候选接近，优先选择同时被多个本地策略支持的号码。
4. 回复结尾必须严格使用下面格式，方便系统提取：
推荐号码：[n1, n2, n3, n4, n5, n6]
特码：[s]
理由：用 2-4 句简要说明，重点说为什么选这个特码和这组平码。
5. 不要输出多组方案，不要输出候补方案。"""

    for d in recent_data:
        all_numbers = d.get('no', []) + ([d.get('sno')] if d.get('sno') else [])
        history_lines.append(
            f"期号: {d['id']}, 开奖号码: {','.join(all_numbers)}, 波色: {d['raw_wave']}, 生肖: {d['raw_zodiac']}"
        )
    recent_history = "\n".join(history_lines)
    return f"""请基于以下信息，为下一期澳门六合彩给出一次更偏保守、优先参考候选池共识的预测。

历史数据：
{recent_history}

{learning_context}

{candidate_context}

你的任务：
1. 综合历史反馈、候选池共识、波色/生肖/单双偏好，再做最终选择。
2. 平码 6 个必须互不重复，且不能与特码重复。
3. 若多个候选接近，优先选择同时被多个本地策略支持的号码。
4. 回复结尾必须严格使用下面格式，方便系统提取：
推荐号码：[n1, n2, n3, n4, n5, n6]
特码：[s]
理由：用 2-4 句简要说明，重点说为什么选这个特码和这组平码。
5. 不要输出多组方案，不要输出候补方案。"""


def _build_ai_prompt_v3(data, region, history_window=10):
    history_lines = []
    recent_data = data[:history_window]
    learning_context = _build_ai_learning_context(data, region, history_window=history_window)
    candidate_context = _build_ai_candidate_context(data, region)

    if region == 'hk':
        year = datetime.now().year
        if recent_data:
            try:
                year = int(str(recent_data[0].get('date', ''))[:4])
            except (TypeError, ValueError):
                pass
        number_to_zodiac = _get_number_to_zodiac_map(year)
        for d in recent_data:
            zodiac = number_to_zodiac.get(str(d.get('sno')), '')
            history_lines.append(
                f"日期: {d['date']}, 开奖号码: {', '.join(d['no'])}, 特码: {d.get('sno')}({zodiac})"
            )
        recent_history = "\n".join(history_lines)
        return f"""请基于以下信息，为下一期香港六合彩给出一次更偏保守、优先参考候选池共识的预测。

历史数据：
{recent_history}

{learning_context}

{candidate_context}

香港专用决策要求：
1. 特码优先参考近期回测更优策略的共识、历史反馈更强的特码候选，以及生肖匹配强度。
2. 如果近期 5 期内某个特码或同生肖号码过热，不要机械追热，除非它同时得到多个本地策略支持。
3. 平码尽量保持分散，避免 6 个号码过度集中在同一区间。
4. 若两个特码候选接近，优先选择生肖反馈更强、且未在最近 2 期直接开出的号码。
5. 回复结尾必须严格使用下面格式：
推荐号码：[n1, n2, n3, n4, n5, n6]
特码：[s]
理由：用 2-4 句简要说明，重点说为什么选这个特码、为什么没有追某个热门候选。"""

    for d in recent_data:
        all_numbers = d.get('no', []) + ([d.get('sno')] if d.get('sno') else [])
        history_lines.append(
            f"期号: {d['id']}, 开奖号码: {','.join(all_numbers)}, 波色: {d['raw_wave']}, 生肖: {d['raw_zodiac']}"
        )
    recent_history = "\n".join(history_lines)
    return f"""请基于以下信息，为下一期澳门六合彩给出一次更偏保守、优先参考候选池共识的预测。

历史数据：
{recent_history}

{learning_context}

{candidate_context}

澳门专用决策要求：
1. 特码优先参考波色、生肖、单双三类属性是否同时得到历史反馈支持，而不是只看单个热门号码。
2. 如果某个特码候选与当前更强的波色/生肖/单双结构明显冲突，降低它的优先级。
3. 平码 6 个尽量保持波色和单双分布均衡，不要过度扎堆。
4. 若两个特码候选接近，优先选择同时满足候选池共识和属性结构一致性的号码。
5. 回复结尾必须严格使用下面格式：
推荐号码：[n1, n2, n3, n4, n5, n6]
特码：[s]
理由：用 2-4 句简要说明，重点说为什么选这个特码，以及它和波色/生肖/单双结构如何匹配。"""


def _build_ai_sampling_temperatures(base_temperature, sample_count):
    count = max(1, int(sample_count or 1))
    base = float(base_temperature or 0.35)
    offsets = [0.0, 0.06, -0.05, 0.1, -0.09]
    values = []
    for index in range(count):
        offset = offsets[index] if index < len(offsets) else ((index % 2) * 0.08 - 0.04)
        values.append(round(_clamp(base + offset, 0.16, 0.52), 2))
    return values


def _call_ai_completion(ai_config, prompt, temperature=0.35):
    payload = {
        "model": ai_config['model'],
        "messages": [
            {"role": "system", "content": _build_ai_system_prompt()},
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature
    }
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=_ai_http_timeout())
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
        response.encoding = "utf-8"
    return response.json()['choices'][0]['message']['content']


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _build_ai_shortlist_context(data, region, config=None):
    config = config or {}
    special_limit = _clamp(int(config.get("special_shortlist") or 8), 6, 12)
    normal_limit = _clamp(int(config.get("normal_shortlist") or 18), 12, 24)
    artifacts = _build_ml_prediction_artifacts(data, region)
    year = int(artifacts.get("year") or _infer_draw_year(data))
    number_to_zodiac = _get_number_to_zodiac_map(year)
    ensemble_signals = artifacts.get("ensemble_signals") or {}
    special_votes = Counter(ensemble_signals.get("special_votes") or {})
    normal_votes = Counter(ensemble_signals.get("normal_votes") or {})

    special_ranked = [int(number) for number in (artifacts.get("special_ranked_numbers") or [])]
    normal_ranked = [int(number) for number in (artifacts.get("normal_ranked_numbers") or [])]
    special_score_map = {int(key): _safe_float(value) for key, value in (artifacts.get("special_score_map") or {}).items()}
    normal_score_map = {int(key): _safe_float(value) for key, value in (artifacts.get("normal_score_map") or {}).items()}
    probability_map = {int(key): _safe_float(value) for key, value in (artifacts.get("probability_map") or {}).items()}
    parity_pref = dict(artifacts.get("parity_pref") or {})
    color_pref = dict(artifacts.get("color_pref") or {})

    ai_feedback = _build_prediction_feedback(region, "ai")
    ml_feedback = _build_prediction_feedback(region, "ml")
    mix_weights = dict(config.get("feedback_mix_weights") or _resolve_ai_feedback_mix_weights(region))
    feedback = _blend_prediction_feedback_items(
        (ai_feedback, _safe_float(mix_weights.get("ai"), 0.58)),
        (ml_feedback, _safe_float(mix_weights.get("ml"), 0.42)),
    )
    ai_profile = _learn_ai_region_profile(region)
    _, zodiac_pref, _ = _build_attribute_preferences(
        data[:min(len(data), 24)], region, feedback, year, apply_recent_zodiac_cooldown=True
    )
    preferred_color = max(color_pref.items(), key=lambda item: item[1])[0] if color_pref else ""
    preferred_parity = max(parity_pref.items(), key=lambda item: item[1])[0] if parity_pref else ""
    preferred_zodiac = max(zodiac_pref.items(), key=lambda item: item[1])[0] if zodiac_pref else ""

    feedback_special_rank = _rank_numbers({
        number: (_safe_float((feedback.get("special") or {}).get(str(number), 0.5)) - 0.5) * 2
        for number in range(1, 50)
    })
    feedback_normal_rank = _rank_numbers({
        number: (_safe_float((feedback.get("normal") or {}).get(str(number), 0.5)) - 0.5) * 2
        for number in range(1, 50)
    })

    strategy_results = dict(ensemble_signals.get("strategy_results") or {})
    try:
        ml_result = _predict_with_ml(data, region)
        strategy_results["ml"] = ml_result
        ml_special = ((ml_result.get("special") or {}).get("number") or "").strip()
        if ml_special.isdigit():
            special_votes[int(ml_special)] += 1.35
        for number in ml_result.get("normal", []) or []:
            try:
                normal_votes[int(number)] += 1.0
            except (TypeError, ValueError):
                continue
    except Exception:
        pass

    heat_profile = _build_ai_recent_heat_profile(data, region, window=8)
    recent_specials = list(heat_profile.get("recent_specials") or [])
    phase_profile = _classify_ai_market_phase(data, window=max(8, int(config.get("history_window") or 12)))
    layered_shortlists = _build_ai_layered_shortlists(
        special_ranked,
        normal_ranked,
        feedback_special_rank,
        feedback_normal_rank,
        special_votes,
        normal_votes,
        special_limit,
        normal_limit,
    )
    special_shortlist = _dedupe_keep_order(
        layered_shortlists["special"]["recent"] +
        layered_shortlists["special"]["stable"] +
        layered_shortlists["special"]["explore"]
    )[:special_limit]
    normal_shortlist = _dedupe_keep_order(
        layered_shortlists["normal"]["recent"] +
        layered_shortlists["normal"]["stable"] +
        layered_shortlists["normal"]["explore"]
    )[:normal_limit]

    special_rows = []
    for number in special_shortlist:
        special_rows.append({
            "number": number,
            "score": round(special_score_map.get(number, 0.0), 6),
            "probability": round(probability_map.get(number, 0.0), 6),
            "votes": round(_safe_float(special_votes.get(number, 0.0)), 4),
            "color": _get_color_zh(number),
            "zodiac": number_to_zodiac.get(str(number), ""),
            "parity": _get_parity_zh(number),
        })

    normal_rows = []
    for number in normal_shortlist:
        normal_rows.append({
            "number": number,
            "score": round(normal_score_map.get(number, 0.0), 6),
            "votes": round(_safe_float(normal_votes.get(number, 0.0)), 4),
            "color": _get_color_zh(number),
            "zodiac": number_to_zodiac.get(str(number), ""),
            "parity": _get_parity_zh(number),
        })

    strategy_summary = []
    for strategy, result in strategy_results.items():
        special_number = ((result.get("special") or {}).get("number") or "").strip()
        strategy_summary.append({
            "strategy": strategy,
            "label": _get_strategy_label(strategy),
            "special": special_number,
            "normal": [int(item) for item in (result.get("normal") or []) if str(item).isdigit()][:6],
        })

    structured_payload = {
        "region": region,
        "special_shortlist": special_rows,
        "normal_shortlist": normal_rows,
        "special_layers": layered_shortlists["special"],
        "normal_layers": layered_shortlists["normal"],
        "preferred": {
            "color": preferred_color,
            "parity": preferred_parity,
            "zodiac": preferred_zodiac,
        },
        "recent_specials": recent_specials,
        "strategy_summary": strategy_summary,
        "feedback_sources": {
            "ai_samples": int(ai_feedback.get("samples", 0) or 0),
            "ml_samples": int(ml_feedback.get("samples", 0) or 0),
            "blended_confidence": round(float(feedback.get("confidence", 0.0)) * 100, 2),
            "ai_mix_weight": round(_safe_float(mix_weights.get("ai"), 0.0) * 100, 2),
            "ml_mix_weight": round(_safe_float(mix_weights.get("ml"), 0.0) * 100, 2),
        },
        "phase_profile": {
            "label": str(phase_profile.get("label") or "neutral"),
            "confidence": round(_safe_float(phase_profile.get("confidence"), 0.0) * 100, 2),
            "guidance": str(phase_profile.get("guidance") or ""),
        },
    }
    structure_guidance = _format_ai_structure_guidance(ai_profile)

    shortlist_prompt = (
        "候选约束(JSON)：\n"
        f"{json.dumps(structured_payload, ensure_ascii=False, separators=(',', ':'))}\n"
        "你必须优先从 special_shortlist 中选择 1 个特码，并从 normal_shortlist 中选择 6 个不重复平码；"
        "若偏离 shortlist，必须在理由中明确说明。\n"
        f"{structure_guidance}\n"
        "special_layers/normal_layers 分别代表近期强势池、稳定池、补充池，优先从 recent 与 stable 中选号。"
    )

    return {
        "year": year,
        "number_to_zodiac": number_to_zodiac,
        "special_score_map": special_score_map,
        "normal_score_map": normal_score_map,
        "probability_map": probability_map,
        "special_votes": special_votes,
        "normal_votes": normal_votes,
        "special_shortlist": special_shortlist,
        "normal_shortlist": normal_shortlist,
        "preferred_color": preferred_color,
        "preferred_parity": preferred_parity,
        "preferred_zodiac": preferred_zodiac,
        "recent_specials": recent_specials,
        "recent_special_counter": dict(heat_profile.get("special_counter") or {}),
        "recent_tail_counter": dict(heat_profile.get("tail_counter") or {}),
        "recent_color_counter": dict(heat_profile.get("color_counter") or {}),
        "recent_zodiacs": list(heat_profile.get("recent_zodiacs") or []),
        "recent_zodiac_counter": dict(heat_profile.get("zodiac_counter") or {}),
        "recent_draw_numbers": list(heat_profile.get("recent_draw_numbers") or []),
        "recent_draw_number_counter": dict(heat_profile.get("draw_number_counter") or {}),
        "recent_draw_sets": list(heat_profile.get("recent_draw_sets") or []),
        "repeat_transition_profile": dict(heat_profile.get("repeat_transition_profile") or {}),
        "layered_shortlists": layered_shortlists,
        "target_mode": str(config.get("target_mode") or "top1_safe"),
        "target_mode_stats": dict(config.get("target_mode_stats") or {}),
        "rerank_weights": _normalize_ai_rerank_weights(config.get("rerank_weights")),
        "rerank_learning_confidence": round(_safe_float(config.get("rerank_learning_confidence"), 0.0) * 100, 2),
        "quality_threshold": _resolve_ai_quality_threshold({
            "gate_profile": _build_ai_gate_profile(region),
            "target_mode": str(config.get("target_mode") or "top1_safe"),
            "structure_profile": {
                "confidence": round(_safe_float(ai_profile.get("confidence"), 0.0), 4),
            },
        }),
        "phase_profile": phase_profile,
        "feedback_mix_weights": mix_weights,
        "feedback_mix_stats": dict(config.get("feedback_mix_stats") or {}),
        "structure_profile": {
            "confidence": round(_safe_float(ai_profile.get("confidence"), 0.0), 4),
            "samples": int(ai_profile.get("samples", 0) or 0),
            "preferred_structures": list(ai_profile.get("preferred_structures") or []),
            "target_scores": dict(ai_profile.get("target_scores") or {}),
            "structure_scores": dict(ai_profile.get("structure_scores") or {}),
            "failure_scores": dict(ai_profile.get("failure_scores") or {}),
        },
        "shortlist_prompt": shortlist_prompt,
        "structure_guidance": structure_guidance,
        "structured_payload": structured_payload,
        "gate_profile": _build_ai_gate_profile(region),
    }


def _build_ai_prompt_v4(data, region, shortlist_context, history_window=10, candidate_count=3):
    base_prompt = _build_ai_prompt_v3(data, region, history_window=history_window)
    candidate_count = _clamp(int(candidate_count or 3), 2, 5)
    target_mode = str(shortlist_context.get("target_mode") or "top1_safe")
    target_hint = {
        "top1_strict": "本期更强调特码直接命中，普通号码只做必要配合。",
        "top1_safe": "本期优先选择更稳的特码，同时保证平码结构不要太激进。",
        "top6_cover": "本期更重视六码覆盖的整体稳定性，特码仍需和结构一致。",
    }.get(target_mode, "本期优先保持特码与平码结构的整体一致性。")
    structure_guidance = str(shortlist_context.get("structure_guidance") or "").strip()
    phase_profile = dict(shortlist_context.get("phase_profile") or {})
    phase_text = f"当前开奖阶段：{phase_profile.get('label', 'neutral')}，{phase_profile.get('guidance', '保持中性判断。')}"
    return (
        f"{base_prompt}\n\n"
        f"{shortlist_context.get('shortlist_prompt', '')}\n\n"
        f"当前目标模式：{target_mode}。{target_hint}\n"
        f"{phase_text}\n"
        f"{structure_guidance}\n\n"
        "请先输出一段严格 JSON，格式如下：\n"
        "{\"candidates\":[{\"special\":12,\"normal\":[1,2,3,4,5,6],\"confidence\":0.72,\"why\":\"...\"}]}\n"
        f"其中 candidates 数量必须为 {candidate_count} 组，按优先级从高到低排序。\n"
        "然后再输出简短分析。不要输出 shortlist 之外的大量号码。"
    )


def _extract_json_objects_from_text(text):
    content = str(text or "")
    objects = []
    for start in range(len(content)):
        if content[start] != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for end in range(start, len(content)):
            char = content[end]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = content[start:end + 1]
                    try:
                        objects.append(json.loads(snippet))
                    except Exception:
                        pass
                    break
    return objects


def _normalize_ai_candidate_entry(entry, region=None, source_text=""):
    if not isinstance(entry, dict):
        return None
    normal = entry.get("normal")
    special = entry.get("special")
    if isinstance(special, dict):
        special = special.get("number") or special.get("value")
    if isinstance(normal, str):
        normal = [int(item) for item in re.findall(r"\d{1,2}", normal)]
    elif isinstance(normal, (list, tuple)):
        parsed = []
        for item in normal:
            try:
                parsed.append(int(str(item).strip()))
            except (TypeError, ValueError):
                continue
        normal = parsed
    else:
        normal = []

    try:
        special_value = int(str(special).strip())
    except (TypeError, ValueError):
        return None

    normal = [number for number in normal if 1 <= int(number) <= 49 and int(number) != special_value]
    normal = _dedupe_keep_order([int(number) for number in normal])[:6]
    if len(normal) < 6 or not (1 <= special_value <= 49):
        return None

    return {
        "special": special_value,
        "normal": normal,
        "confidence": _safe_float(entry.get("confidence"), 0.0),
        "why": str(entry.get("why") or entry.get("reason") or "").strip(),
        "source_text": str(source_text or "").strip(),
    }


def _build_ai_recent_heat_profile(data, region, window=8):
    recent_specials = []
    special_counter = Counter()
    tail_counter = Counter()
    color_counter = Counter()
    zodiac_counter = Counter()
    recent_zodiacs = []
    recent_draw_numbers = []
    draw_number_counter = Counter()
    recent_draw_sets = []
    year = _infer_draw_year(data)
    number_to_zodiac = _get_number_to_zodiac_map(year)
    repeat_transition_profile = _build_repeat_transition_profile(data, region, year=year)

    for record in list(data or [])[:max(1, int(window or 8))]:
        draw_numbers = []
        for raw_number in list(record.get("no") or []) + [record.get("sno")]:
            try:
                parsed = int(str(raw_number).strip())
            except (TypeError, ValueError):
                continue
            if 1 <= parsed <= 49:
                draw_numbers.append(parsed)
        deduped_draw_numbers = _dedupe_keep_order(draw_numbers)
        if deduped_draw_numbers:
            recent_draw_sets.append(deduped_draw_numbers)
            recent_draw_numbers.extend(deduped_draw_numbers)
            for parsed in deduped_draw_numbers:
                draw_number_counter[parsed] += 1
        try:
            number = int(str(record.get("sno") or "").strip())
        except (TypeError, ValueError):
            continue
        if not (1 <= number <= 49):
            continue
        recent_specials.append(number)
        special_counter[number] += 1
        tail_counter[number % 10] += 1
        color = _get_color_zh(number)
        if color:
            color_counter[color] += 1
        zodiac = number_to_zodiac.get(str(number), "") if number_to_zodiac else ""
        if not zodiac:
            zodiac = str(record.get("sno_zodiac") or "").strip()
        if zodiac:
            recent_zodiacs.append(zodiac)
            zodiac_counter[zodiac] += 1

    return {
        "recent_specials": recent_specials,
        "special_counter": special_counter,
        "tail_counter": tail_counter,
        "color_counter": color_counter,
        "recent_zodiacs": recent_zodiacs,
        "zodiac_counter": zodiac_counter,
        "recent_draw_numbers": recent_draw_numbers,
        "draw_number_counter": draw_number_counter,
        "recent_draw_sets": recent_draw_sets,
        "repeat_transition_profile": repeat_transition_profile,
    }


def _classify_ai_market_phase(data, window=12):
    recent = list(data or [])[:max(4, int(window or 12))]
    specials = []
    for record in recent:
        try:
            number = int(str(record.get("sno") or "").strip())
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 49:
            specials.append(number)

    if not specials:
        return {
            "label": "neutral",
            "confidence": 0.0,
            "metrics": {},
            "adjustments": {},
            "guidance": "当前阶段信号不足，保持中性判断。",
        }

    special_counter = Counter(specials)
    tail_counter = Counter(number % 10 for number in specials)
    color_counter = Counter(_get_color_zh(number) for number in specials if _get_color_zh(number))
    zones = [1 if number <= 16 else 2 if number <= 33 else 3 for number in specials]
    zone_counter = Counter(zones)

    repeat_ratio = max(special_counter.values()) / max(len(specials), 1)
    tail_focus = max(tail_counter.values()) / max(len(specials), 1)
    color_focus = max(color_counter.values()) / max(len(specials), 1) if color_counter else 0.0
    zone_focus = max(zone_counter.values()) / max(len(zones), 1)
    unique_ratio = len(set(specials)) / max(len(specials), 1)

    label = "neutral"
    confidence = 0.4
    adjustments = {}
    guidance = "当前阶段较中性，保持特码与结构平衡。"

    if repeat_ratio >= 0.28 or tail_focus >= 0.34 or color_focus >= 0.56:
        label = "hot"
        confidence = max(repeat_ratio, tail_focus, color_focus)
        adjustments = {
            "overheat_penalty": 0.12,
            "repeat_penalty": 0.08,
            "base_special": -0.04,
            "diversity_bonus": 0.05,
        }
        guidance = "当前更像热号阶段，谨慎追逐近期重复信号，优先规避过热特码与尾数。"
    elif unique_ratio >= 0.92 and repeat_ratio <= 0.12 and tail_focus <= 0.22:
        label = "cold"
        confidence = unique_ratio
        adjustments = {
            "overheat_penalty": -0.05,
            "base_special": 0.04,
            "special_vote": -0.03,
            "structure_bonus": 0.04,
        }
        guidance = "当前更像冷号阶段，允许更分散的特码尝试，不必过度依赖近期热度。"
    elif zone_focus >= 0.62 or color_focus >= 0.62:
        label = "concentrated"
        confidence = max(zone_focus, color_focus)
        adjustments = {
            "shape_score": 0.08,
            "structure_bonus": 0.07,
            "diversity_bonus": 0.06,
            "avg_normal": 0.04,
        }
        guidance = "当前更像结构集中阶段，优先修正区间、波色和单双失衡。"
    elif len(set(zones)) == 3 and unique_ratio >= 0.8 and color_focus <= 0.45:
        label = "dispersed"
        confidence = max(0.45, unique_ratio)
        adjustments = {
            "base_special": 0.05,
            "special_vote": 0.04,
            "overheat_penalty": -0.03,
            "shape_score": -0.03,
        }
        guidance = "当前更像结构分散阶段，可适度提高特码主导性，少做过强约束。"

    return {
        "label": label,
        "confidence": round(_clamp(confidence, 0.2, 1.0), 4),
        "metrics": {
            "repeat_ratio": round(repeat_ratio, 4),
            "tail_focus": round(tail_focus, 4),
            "color_focus": round(color_focus, 4),
            "zone_focus": round(zone_focus, 4),
            "unique_ratio": round(unique_ratio, 4),
        },
        "adjustments": adjustments,
        "guidance": guidance,
    }


def _candidate_signature(candidate):
    if not candidate:
        return ""
    special = int(candidate.get("special") or 0)
    normal = [int(number) for number in (candidate.get("normal") or []) if str(number).isdigit()]
    return f"{special}|{','.join(map(str, normal))}"


def _count_ai_unique_candidates(ai_responses, region=None):
    signatures = set()
    for response_text in ai_responses or []:
        for candidate in _extract_ai_candidate_entries(response_text, region=region):
            signature = _candidate_signature(candidate)
            if signature:
                signatures.add(signature)
    return len(signatures)


def _repair_ai_response_text(response_text, region=None):
    text = str(response_text or "").strip()
    if not text:
        return text

    normalized = _normalize_ai_response_v2(text)
    if _extract_ai_candidate_entries(normalized, region=region):
        return normalized

    special_match = re.search(r'"special"\s*:\s*\{[^{}]*?"number"\s*:\s*"?(?P<num>\d{1,2})"?', normalized, flags=re.IGNORECASE | re.DOTALL)
    normal_match = re.search(r'"normal"\s*:\s*\[(?P<nums>[^\]]+)\]', normalized, flags=re.IGNORECASE | re.DOTALL)
    if special_match and normal_match:
        fixed = json.dumps({
            "candidates": [{
                "special": int(special_match.group("num")),
                "normal": [int(n) for n in re.findall(r'\d{1,2}', normal_match.group("nums"))[:6]],
                "confidence": 0.45,
                "why": text[:280],
            }]
        }, ensure_ascii=False)
        if _extract_ai_candidate_entries(fixed, region=region):
            return fixed

    normal_numbers, special_number = _extract_ai_numbers_v2(normalized, region=region)
    if normal_numbers and special_number:
        fixed = json.dumps({
            "candidates": [{
                "special": int(special_number),
                "normal": [int(n) for n in normal_numbers[:6]],
                "confidence": 0.4,
                "why": text[:280],
            }]
        }, ensure_ascii=False)
        if _extract_ai_candidate_entries(fixed, region=region):
            return fixed

    return normalized


def _call_ai_completion_with_retries(ai_config, prompt, temperature=0.35, max_attempts=2, region=None):
    last_error = None
    for attempt in range(max(1, int(max_attempts or 1))):
        try:
            response_text = _call_ai_completion(ai_config, prompt, temperature=temperature)
            repaired = _repair_ai_response_text(response_text, region=region)
            if str(repaired or "").strip():
                return repaired
        except Exception as exc:
            last_error = exc
        temperature = round(_clamp(float(temperature or 0.35) + 0.05, 0.16, 0.58), 2)

    if last_error:
        raise last_error
    return ""


def _resolve_ai_latency_budget_seconds(tuned=None, stream_mode=False):
    config = dict(tuned or {})
    default_budget = 14.0 if stream_mode else 18.0
    raw_budget = config.get("latency_budget_seconds")
    if raw_budget in (None, ""):
        raw_budget = config.get("time_budget_seconds")
    try:
        budget = float(raw_budget)
    except (TypeError, ValueError):
        budget = default_budget
    return float(_clamp(budget, 6.0, 45.0))


def _ai_budget_remaining(started_at, budget_seconds):
    elapsed = max(0.0, time.perf_counter() - float(started_at or 0.0))
    return max(0.0, float(budget_seconds or 0.0) - elapsed)


def _ai_budget_exhausted(started_at, budget_seconds):
    return _ai_budget_remaining(started_at, budget_seconds) <= 0.0


def _build_ai_local_fallback_candidates(context, desired_count=3):
    desired = max(1, int(desired_count or 1))
    special_shortlist = [int(number) for number in (context.get("special_shortlist") or []) if str(number).isdigit()]
    normal_shortlist = [int(number) for number in (context.get("normal_shortlist") or []) if str(number).isdigit()]
    if len(normal_shortlist) < 6:
        return []

    candidates = []
    for idx, special in enumerate(special_shortlist[:max(desired * 2, desired)]):
        normal = []
        preferred_tail = special % 10
        for number in normal_shortlist:
            if number == special or number in normal:
                continue
            if len(normal) < 2 and number % 10 == preferred_tail:
                continue
            normal.append(number)
            if len(normal) >= 6:
                break
        if len(normal) < 6:
            for number in normal_shortlist:
                if number == special or number in normal:
                    continue
                normal.append(number)
                if len(normal) >= 6:
                    break
        candidate = _normalize_ai_candidate_entry(
            {
                "special": special,
                "normal": normal[:6],
                "confidence": max(0.22, 0.38 - idx * 0.03),
                "why": "local shortlist fallback",
            },
            source_text="local shortlist fallback",
        )
        if candidate:
            candidates.append(candidate)
        if len(candidates) >= desired:
            break
    return candidates


def _assess_ai_candidate_quality(candidate, context):
    if not candidate:
        return -999.0, {"quality_score": -999.0}

    special = int(candidate.get("special") or 0)
    normal = [int(number) for number in (candidate.get("normal") or []) if str(number).isdigit()]
    if not (1 <= special <= 49) or len(normal) < 6:
        return -999.0, {"quality_score": -999.0}

    numbers = normal + [special]
    zones = Counter(1 if number <= 16 else 2 if number <= 33 else 3 for number in numbers)
    tails = Counter(number % 10 for number in numbers)
    colors = Counter(_get_color_zh(number) for number in numbers if _get_color_zh(number))
    parities = Counter(_get_parity_zh(number) for number in numbers if _get_parity_zh(number))
    special_shortlist = set(context.get("special_shortlist") or [])
    normal_shortlist = set(context.get("normal_shortlist") or [])

    shortlist_hits = sum(1 for number in normal if number in normal_shortlist) + (1 if special in special_shortlist else 0)
    shortlist_component = max(-0.18, (shortlist_hits - 4) * 0.05)
    zone_component = 0.08 if len(zones) == 3 else -0.1 if max(zones.values()) >= 5 else -0.04 if max(zones.values()) >= 4 else 0.0
    tail_component = -0.14 if max(tails.values()) >= 3 else 0.03 if len(tails) >= 6 else 0.0
    color_component = -0.12 if colors and max(colors.values()) >= 5 else 0.04 if len(colors) == 3 else 0.0
    parity_component = -0.08 if parities and max(parities.values()) >= 6 else 0.03 if len(parities) == 2 else 0.0
    confidence_component = _clamp(float(candidate.get("confidence") or 0.0), 0.0, 1.0) * 0.08
    duplicate_penalty = -0.16 if len(set(numbers)) < 7 else 0.0

    total = round(
        shortlist_component +
        zone_component +
        tail_component +
        color_component +
        parity_component +
        confidence_component +
        duplicate_penalty,
        6,
    )
    return total, {
        "shortlist_component": round(shortlist_component, 4),
        "zone_component": round(zone_component, 4),
        "tail_component": round(tail_component, 4),
        "color_component": round(color_component, 4),
        "parity_component": round(parity_component, 4),
        "confidence_component": round(confidence_component, 4),
        "duplicate_penalty": round(duplicate_penalty, 4),
        "quality_score": total,
    }


def _filter_ai_candidate_entries(candidates, context, minimum_quality=None):
    if minimum_quality is None:
        minimum_quality = _safe_float((context or {}).get("quality_threshold"), -0.12)
    filtered = []
    for candidate in candidates or []:
        quality_score, diagnostics = _assess_ai_candidate_quality(candidate, context)
        enriched = dict(candidate)
        enriched["quality_score"] = round(quality_score, 6)
        enriched["quality_diagnostics"] = diagnostics
        if quality_score < float(minimum_quality):
            continue
        filtered.append(enriched)
    return filtered


def _ensure_ai_candidate_coverage(ai_responses, region, context, desired_count=3):
    responses = list(ai_responses or [])
    signatures = {
        _candidate_signature(item): item
        for item in _filter_ai_candidate_entries(
            _build_ai_local_fallback_candidates(context, desired_count=desired_count),
            context,
            minimum_quality=-0.24,
        )
    }
    existing = {}
    for response_text in responses:
        for candidate in _filter_ai_candidate_entries(
            _extract_ai_candidate_entries(response_text, region=region),
            context,
        ):
            signature = _candidate_signature(candidate)
            if signature:
                existing[signature] = candidate

    missing = max(0, int(desired_count or 0) - len(existing))
    if missing <= 0:
        return responses

    fallback_candidates = []
    for signature, candidate in signatures.items():
        if signature in existing:
            continue
        fallback_candidates.append(candidate)
        if len(fallback_candidates) >= missing:
            break

    if fallback_candidates:
        responses.append(json.dumps({"candidates": fallback_candidates}, ensure_ascii=False))
    return responses


def _extend_ai_responses_for_coverage(ai_config, prompt, responses, region, target_candidates, base_temperature=0.35, max_extra_calls=2):
    responses = list(responses or [])
    target = max(2, int(target_candidates or 2))
    for extra_index in range(max(0, int(max_extra_calls or 0))):
        if _count_ai_unique_candidates(responses, region=region) >= target:
            break
        extra_temp = round(_clamp(float(base_temperature or 0.35) + 0.12 + extra_index * 0.05, 0.18, 0.58), 2)
        try:
            responses.append(
                _call_ai_completion_with_retries(
                    ai_config,
                    prompt,
                    temperature=extra_temp,
                    max_attempts=2,
                    region=region,
                )
            )
        except Exception:
            continue
    return responses


def _attach_ai_prediction_metadata(result, tuned, region, elapsed_seconds=None, budget_seconds=None, budget_exhausted=False):
    if not isinstance(result, dict):
        return result

    feedback = _build_prediction_feedback(region, "ai")
    model_meta = dict(result.get("model_meta") or {})
    model_meta["ai_tuned_accuracy"] = round(float((tuned or {}).get("last_accuracy") or 0.0) * 100, 2)
    model_meta["ai_feedback_samples"] = int(feedback.get("samples", 0) or 0)
    model_meta["ai_feedback_confidence"] = round(float(feedback.get("confidence") or 0.0) * 100, 2)
    offline_profile = dict((tuned or {}).get("offline_rerank_profile") or {})
    if offline_profile:
        model_meta["ai_offline_rerank_profile"] = {
            "confidence": round(_safe_float(offline_profile.get("confidence"), 0.0), 4),
            "samples": int(offline_profile.get("samples", 0) or 0),
            "weight_adjustments": dict(offline_profile.get("weight_adjustments") or {}),
            "mode_adjustments": dict(offline_profile.get("mode_adjustments") or {}),
            "mode_window_adjustments": dict(offline_profile.get("mode_window_adjustments") or {}),
            "ranking_signal_scores": dict(offline_profile.get("ranking_signal_scores") or {}),
        }

    if elapsed_seconds is not None:
        model_meta["ai_elapsed_ms"] = int(max(0.0, float(elapsed_seconds)) * 1000)
    if budget_seconds is not None:
        model_meta["ai_latency_budget_ms"] = int(max(0.0, float(budget_seconds)) * 1000)
        model_meta["ai_budget_exhausted"] = bool(budget_exhausted)

    result["model_meta"] = model_meta
    return result


def _run_ai_prediction_pipeline(
    data,
    region,
    tuned,
    ai_config,
    shortlist_context,
    prompt,
    temperature=0.35,
    sample_count=3,
    candidate_count=3,
    initial_response_text="",
    stream_mode=False,
):
    started_at = time.perf_counter()
    budget_seconds = _resolve_ai_latency_budget_seconds(tuned, stream_mode=stream_mode)
    use_cache = not stream_mode and not str(initial_response_text or "").strip()
    cache_key = ""
    cache_region, cache_latest_period = _prediction_cache_meta(region, data)
    if use_cache:
        cache_key = _build_ai_prediction_cache_key(
            region,
            data,
            tuned,
            ai_config,
            prompt,
            temperature,
            sample_count,
            candidate_count,
        )
        _prune_stale_ai_prediction_cache(cache_region, cache_latest_period)
        cached_result = _get_cached_ai_prediction(cache_key)
        if cached_result is not None:
            return cached_result

    responses = []

    initial_text = str(initial_response_text or "").strip()
    if initial_text:
        repaired = _repair_ai_response_text(initial_text, region=region)
        if repaired:
            responses.append(repaired)

    temperatures = list(_build_ai_sampling_temperatures(temperature, sample_count))
    if stream_mode and responses:
        temperatures = []
    elif responses and temperatures:
        temperatures = temperatures[1:]

    for temp in temperatures:
        if _ai_budget_exhausted(started_at, budget_seconds):
            break
        try:
            responses.append(
                _call_ai_completion_with_retries(
                    ai_config,
                    prompt,
                    temperature=temp,
                    max_attempts=2,
                    region=region,
                )
            )
        except Exception:
            continue

    budget_exhausted = _ai_budget_exhausted(started_at, budget_seconds)
    if not budget_exhausted:
        remaining_seconds = _ai_budget_remaining(started_at, budget_seconds)
        extra_calls = 0
        if stream_mode and responses:
            extra_calls = 0
        elif remaining_seconds >= 8:
            extra_calls = 2
        elif remaining_seconds >= 4:
            extra_calls = 1
        if extra_calls > 0:
            responses = _extend_ai_responses_for_coverage(
                ai_config,
                prompt,
                responses,
                region,
                candidate_count,
                base_temperature=temperature,
                max_extra_calls=extra_calls,
            )
        budget_exhausted = _ai_budget_exhausted(started_at, budget_seconds)

    responses = _ensure_ai_candidate_coverage(
        responses,
        region,
        shortlist_context,
        desired_count=candidate_count,
    )
    result, error = _finalize_ai_multi_sample_result(responses, region, shortlist_context)
    elapsed_seconds = time.perf_counter() - started_at
    if error:
        return {"error": error}

    result = _blend_ai_with_anchor_strategy(
        result,
        data,
        region,
        shortlist_context=shortlist_context,
    )
    result = _attach_ai_prediction_metadata(
        result,
        tuned,
        region,
        elapsed_seconds=elapsed_seconds,
        budget_seconds=budget_seconds,
        budget_exhausted=budget_exhausted,
    )
    if use_cache and cache_key:
        result = _store_cached_ai_prediction(
            cache_key,
            result,
            region=cache_region,
            latest_period=cache_latest_period,
        )
    return result


def _candidate_similarity_penalty(candidate, selected_candidates):
    if not selected_candidates:
        return 0.0

    normal = {int(number) for number in (candidate.get("normal") or []) if str(number).isdigit()}
    if not normal:
        return 0.0

    candidate_special = int(candidate.get("special") or 0)
    candidate_tail = candidate_special % 10 if candidate_special else None
    candidate_color = _get_color_zh(candidate_special) if candidate_special else ""
    max_penalty = 0.0

    for selected in selected_candidates:
        selected_normal = {int(number) for number in (selected.get("normal") or []) if str(number).isdigit()}
        overlap = len(normal & selected_normal)
        penalty = 0.0
        if overlap >= 5:
            penalty += 0.18
        elif overlap >= 4:
            penalty += 0.1
        elif overlap >= 3:
            penalty += 0.05

        selected_special = int(selected.get("special") or 0)
        if candidate_special and selected_special:
            if candidate_special == selected_special:
                penalty += 0.28
            elif candidate_tail is not None and candidate_tail == (selected_special % 10):
                penalty += 0.05
            if candidate_color and candidate_color == _get_color_zh(selected_special):
                penalty += 0.03

        max_penalty = max(max_penalty, penalty)

    return round(max_penalty, 6)


def _diversify_ai_ranked_candidates(ranked):
    pool = [dict(item) for item in (ranked or [])]
    diversified = []
    while pool:
        best_index = 0
        best_score = None
        for idx, item in enumerate(pool):
            penalty = _candidate_similarity_penalty(item, diversified)
            adjusted = round(float(item.get("aggregate_score", 0.0)) - penalty, 6)
            if best_score is None or adjusted > best_score:
                best_score = adjusted
                best_index = idx
        chosen = pool.pop(best_index)
        chosen["diversity_penalty"] = round(
            _candidate_similarity_penalty(chosen, diversified),
            6,
        )
        chosen["diversified_score"] = round(
            float(chosen.get("aggregate_score", 0.0)) - float(chosen.get("diversity_penalty", 0.0)),
            6,
        )
        diversified.append(chosen)
    return diversified


def _extract_ai_candidate_entries(ai_response, region=None):
    candidates = []
    for obj in _extract_json_objects_from_text(ai_response):
        if isinstance(obj, dict) and isinstance(obj.get("candidates"), list):
            for item in obj.get("candidates") or []:
                normalized = _normalize_ai_candidate_entry(item, region=region, source_text=ai_response)
                if normalized:
                    candidates.append(normalized)
        else:
            normalized = _normalize_ai_candidate_entry(obj, region=region, source_text=ai_response)
            if normalized:
                candidates.append(normalized)

    if candidates:
        return candidates

    normal_numbers, special_number = _extract_ai_numbers_v2(ai_response, region=region)
    if not normal_numbers or not special_number:
        return []
    normalized = _normalize_ai_candidate_entry(
        {
            "special": special_number,
            "normal": normal_numbers,
            "why": str(ai_response or "").strip(),
        },
        region=region,
        source_text=ai_response,
    )
    return [normalized] if normalized else []


def _score_ai_combination_shape(normal, special, context):
    numbers = [int(number) for number in (normal or []) if str(number).isdigit()]
    if special is not None:
        try:
            numbers.append(int(special))
        except (TypeError, ValueError):
            pass
    if len(numbers) < 7:
        return -0.2, {"shape_score": -0.2}

    zone_counts = Counter(1 if number <= 16 else 2 if number <= 33 else 3 for number in numbers)
    zone_penalty = 0.0
    for count in zone_counts.values():
        if count >= 5:
            zone_penalty -= 0.18
        elif count == 4:
            zone_penalty -= 0.08
    covered_zones = len(zone_counts)
    zone_bonus = 0.06 if covered_zones == 3 else -0.05

    tails = [number % 10 for number in numbers]
    tail_counts = Counter(tails)
    tail_penalty = 0.0
    for count in tail_counts.values():
        if count >= 3:
            tail_penalty -= 0.12

    colors = [_get_color_zh(number) for number in numbers if _get_color_zh(number)]
    color_counts = Counter(colors)
    color_penalty = 0.0
    if color_counts and max(color_counts.values()) >= 5:
        color_penalty -= 0.12
    elif len(color_counts) == 3:
        color_penalty += 0.05

    parities = [_get_parity_zh(number) for number in numbers if _get_parity_zh(number)]
    parity_counts = Counter(parities)
    parity_penalty = -0.06 if parity_counts and max(parity_counts.values()) >= 6 else 0.03 if len(parity_counts) == 2 else 0.0

    special_shortlist = set(context.get("special_shortlist") or [])
    normal_shortlist = set(context.get("normal_shortlist") or [])
    shortlist_coverage = sum(1 for number in normal if int(number) in normal_shortlist)
    if special in special_shortlist:
        shortlist_coverage += 1
    shortlist_bonus = max(0.0, (shortlist_coverage - 4) * 0.03)

    total = round(zone_penalty + zone_bonus + tail_penalty + color_penalty + parity_penalty + shortlist_bonus, 6)
    return total, {
        "zone_penalty": round(zone_penalty, 4),
        "zone_bonus": round(zone_bonus, 4),
        "tail_penalty": round(tail_penalty, 4),
        "color_penalty": round(color_penalty, 4),
        "parity_penalty": round(parity_penalty, 4),
        "shortlist_coverage_bonus": round(shortlist_bonus, 4),
        "shape_score": total,
    }


def _score_ai_structure_alignment(normal, special, context):
    profile = dict(context.get("structure_profile") or {})
    structure_scores = dict(profile.get("structure_scores") or {})
    confidence = _clamp(_safe_float(profile.get("confidence"), 0.0), 0.0, 1.0)
    if confidence <= 0.0:
        return 0.0, {"structure_bonus": 0.0, "structure_confidence": 0.0}

    numbers = [int(number) for number in (normal or []) if str(number).isdigit()]
    if not numbers:
        return 0.0, {"structure_bonus": 0.0, "structure_confidence": round(confidence, 4)}

    zone_spread = len(set(1 if n <= 16 else 2 if n <= 33 else 3 for n in numbers))
    tail_spread = len(set(n % 10 for n in numbers))
    all_numbers = numbers + ([int(special)] if special is not None else [])
    color_spread = len(set(_get_color_zh(n) for n in all_numbers if _get_color_zh(n)))
    odd_count = sum(1 for n in all_numbers if n % 2 == 1)
    even_count = len(all_numbers) - odd_count
    parity_key = "parity:balanced" if abs(odd_count - even_count) <= 1 else "parity:skewed"
    special_zone = "small" if int(special) <= 16 else "mid" if int(special) <= 33 else "large"
    special_color = _get_color_zh(int(special)) or "unknown"

    total = 0.0
    total += _safe_float(structure_scores.get(f"zone_spread:{zone_spread}"), 0.0) * 0.18
    total += _safe_float(structure_scores.get(f"color_spread:{color_spread}"), 0.0) * 0.14
    total += _safe_float(structure_scores.get(parity_key), 0.0) * 0.12
    total += _safe_float(structure_scores.get(f"special_zone:{special_zone}"), 0.0) * 0.18
    total += _safe_float(structure_scores.get(f"special_color:{special_color}"), 0.0) * 0.12
    if tail_spread >= 5:
        total += _safe_float(structure_scores.get("tail_spread:wide"), 0.0) * 0.16
    elif tail_spread >= 4:
        total += _safe_float(structure_scores.get("tail_spread:balanced"), 0.0) * 0.16
    else:
        total += _safe_float(structure_scores.get("tail_spread:tight"), 0.0) * 0.16

    structure_bonus = round((total - 0.45) * max(0.35, confidence), 6)
    return structure_bonus, {
        "structure_bonus": structure_bonus,
        "structure_confidence": round(confidence, 4),
        "zone_spread": zone_spread,
        "tail_spread": tail_spread,
        "color_spread": color_spread,
        "parity_key": parity_key,
        "special_zone": special_zone,
        "special_color": special_color,
    }


def _score_ai_candidate(candidate, context):
    special = int(candidate.get("special"))
    normal = [int(number) for number in candidate.get("normal") or []]
    if len(normal) < 6:
        return -999.0, {}

    special_score_map = context.get("special_score_map") or {}
    normal_score_map = context.get("normal_score_map") or {}
    special_votes = context.get("special_votes") or Counter()
    normal_votes = context.get("normal_votes") or Counter()
    number_to_zodiac = context.get("number_to_zodiac") or {}
    special_shortlist = set(context.get("special_shortlist") or [])
    normal_shortlist = set(context.get("normal_shortlist") or [])
    recent_specials = list(context.get("recent_specials") or [])
    recent_special_counter = Counter(context.get("recent_special_counter") or {})
    recent_tail_counter = Counter(context.get("recent_tail_counter") or {})
    recent_color_counter = Counter(context.get("recent_color_counter") or {})
    recent_zodiacs = list(context.get("recent_zodiacs") or [])
    recent_zodiac_counter = Counter(context.get("recent_zodiac_counter") or {})
    recent_draw_numbers = list(context.get("recent_draw_numbers") or [])
    recent_draw_number_counter = Counter(context.get("recent_draw_number_counter") or {})
    recent_draw_sets = list(context.get("recent_draw_sets") or [])
    repeat_transition_profile = dict(context.get("repeat_transition_profile") or {})
    target_mode = str(context.get("target_mode") or "top1_safe")

    base_special = special_score_map.get(special, 0.0)
    avg_normal = sum(normal_score_map.get(number, 0.0) for number in normal) / max(len(normal), 1)
    special_vote = _safe_float(special_votes.get(special, 0.0))
    normal_vote_avg = sum(_safe_float(normal_votes.get(number, 0.0)) for number in normal) / max(len(normal), 1)
    shortlist_bonus = (0.16 if special in special_shortlist else -0.18)
    shortlist_bonus += sum(0.028 if number in normal_shortlist else -0.04 for number in normal)
    rerank_weights = _normalize_ai_rerank_weights(context.get("rerank_weights"))

    special_color = _get_color_zh(special)
    special_parity = _get_parity_zh(special)
    special_zodiac = number_to_zodiac.get(str(special), "")
    attr_bonus = 0.0
    if context.get("preferred_color") and special_color == context.get("preferred_color"):
        attr_bonus += 0.08
    if context.get("preferred_parity") and special_parity == context.get("preferred_parity"):
        attr_bonus += 0.06
    if context.get("preferred_zodiac") and special_zodiac == context.get("preferred_zodiac"):
        attr_bonus += 0.08

    zones = set(1 if number <= 16 else 2 if number <= 33 else 3 for number in normal)
    diversity_bonus = len(zones) * 0.04
    parity_mix = len(set(_get_parity_zh(number) for number in normal if _get_parity_zh(number)))
    diversity_bonus += 0.04 if parity_mix >= 2 else -0.03

    repeat_penalty = 0.0
    latest_special_repeat_probability = float(repeat_transition_profile.get("latest_special_repeat_probability") or 0.0)
    latest_zodiac_repeat_probability = float(repeat_transition_profile.get("latest_zodiac_repeat_probability") or 0.0)
    latest_special = repeat_transition_profile.get("latest_special")
    latest_zodiac = str(repeat_transition_profile.get("latest_zodiac") or "").strip()
    if latest_special is not None and special == int(latest_special):
        repeat_penalty -= 0.18 + max(0.0, 0.24 - latest_special_repeat_probability)
    if latest_zodiac and special_zodiac == latest_zodiac:
        repeat_penalty -= 0.14 + max(0.0, 0.20 - latest_zodiac_repeat_probability)
    if recent_specials[:2] and special in recent_specials[:2]:
        repeat_penalty -= 0.2
    elif special in recent_specials:
        repeat_penalty -= 0.08
    if recent_zodiacs[:1] and special_zodiac == recent_zodiacs[0]:
        repeat_penalty -= 0.22
    elif recent_zodiacs[:2] and special_zodiac in recent_zodiacs[:2]:
        repeat_penalty -= 0.1
    if recent_draw_sets:
        latest_draw_set = {int(number) for number in (recent_draw_sets[0] or [])}
        if special in latest_draw_set:
            repeat_penalty -= 0.26
        elif any(special in {int(number) for number in (draw_set or [])} for draw_set in recent_draw_sets[:3]):
            repeat_penalty -= 0.12

    overheat_penalty = 0.0
    special_heat = int(recent_special_counter.get(special, 0) or 0)
    if special_heat >= 2:
        overheat_penalty -= 0.18
    elif special_heat == 1:
        overheat_penalty -= 0.05

    special_tail = special % 10
    tail_heat = int(recent_tail_counter.get(special_tail, 0) or 0)
    if tail_heat >= 3:
        overheat_penalty -= 0.1
    elif tail_heat == 2:
        overheat_penalty -= 0.04

    special_color_heat = int(recent_color_counter.get(special_color, 0) or 0) if special_color else 0
    if special_color_heat >= 4:
        overheat_penalty -= 0.08
    elif special_color_heat == 3:
        overheat_penalty -= 0.03
    zodiac_heat = int(recent_zodiac_counter.get(special_zodiac, 0) or 0) if special_zodiac else 0
    if zodiac_heat >= 2:
        overheat_penalty -= 0.14
    elif zodiac_heat == 1:
        overheat_penalty -= 0.05
    draw_heat = int(recent_draw_number_counter.get(special, 0) or 0)
    if draw_heat >= 2:
        overheat_penalty -= 0.18
    elif draw_heat == 1:
        overheat_penalty -= 0.07

    normal_repeat_penalty = 0.0
    if recent_draw_sets:
        latest_draw_set = {int(number) for number in (recent_draw_sets[0] or [])}
        latest_repeat_count = sum(1 for number in normal if number in latest_draw_set)
        normal_repeat_penalty -= latest_repeat_count * 0.06
        recent_three_sets = [
            {int(number) for number in (draw_set or [])}
            for draw_set in recent_draw_sets[:3]
        ]
        for number in normal:
            appearances = int(recent_draw_number_counter.get(number, 0) or 0)
            if appearances >= 2:
                normal_repeat_penalty -= 0.035
            elif appearances == 1:
                normal_repeat_penalty -= 0.012
            if any(number in draw_set for draw_set in recent_three_sets):
                normal_repeat_penalty -= 0.01

    confidence_bonus = _clamp(candidate.get("confidence", 0.0), 0.0, 1.0) * 0.08
    shape_score, shape_diagnostics = _score_ai_combination_shape(normal, special, context)
    structure_bonus, structure_diagnostics = _score_ai_structure_alignment(normal, special, context)
    phase_profile = dict(context.get("phase_profile") or {})
    phase_adjustments = dict(phase_profile.get("adjustments") or {})
    gate_profile = dict(context.get("gate_profile") or {})
    gate_adjustment = 0.0
    if gate_profile.get("status") == "guarded":
        gate_adjustment -= 0.08
    elif gate_profile.get("status") == "fallback":
        gate_adjustment -= 0.2
    phase_confidence = _clamp(_safe_float(phase_profile.get("confidence"), 0.0), 0.0, 1.0)
    base_special += _safe_float(phase_adjustments.get("base_special"), 0.0) * phase_confidence
    avg_normal += _safe_float(phase_adjustments.get("avg_normal"), 0.0) * phase_confidence
    special_vote += _safe_float(phase_adjustments.get("special_vote"), 0.0) * phase_confidence
    diversity_bonus += _safe_float(phase_adjustments.get("diversity_bonus"), 0.0) * phase_confidence
    repeat_penalty -= _safe_float(phase_adjustments.get("repeat_penalty"), 0.0) * phase_confidence
    overheat_penalty -= _safe_float(phase_adjustments.get("overheat_penalty"), 0.0) * phase_confidence
    shape_score += _safe_float(phase_adjustments.get("shape_score"), 0.0) * phase_confidence
    structure_bonus += _safe_float(phase_adjustments.get("structure_bonus"), 0.0) * phase_confidence
    total = (
        base_special * rerank_weights.get("base_special", 0.9) +
        avg_normal * rerank_weights.get("avg_normal", 0.55) +
        special_vote * rerank_weights.get("special_vote", 0.18) +
        normal_vote_avg * rerank_weights.get("normal_vote_avg", 0.1) +
        shortlist_bonus * rerank_weights.get("shortlist_bonus", 1.0) +
        attr_bonus * rerank_weights.get("attr_bonus", 1.0) +
        diversity_bonus * rerank_weights.get("diversity_bonus", 1.0) +
        repeat_penalty * rerank_weights.get("repeat_penalty", 1.0) +
        normal_repeat_penalty * 0.8 +
        overheat_penalty * rerank_weights.get("overheat_penalty", 1.0) +
        confidence_bonus * rerank_weights.get("confidence_bonus", 1.0) +
        shape_score * rerank_weights.get("shape_score", 1.0) +
        structure_bonus * rerank_weights.get("structure_bonus", 1.0) +
        gate_adjustment * rerank_weights.get("gate_adjustment", 1.0)
    )
    structure_profile = dict(context.get("structure_profile") or {})
    failure_scores = dict(structure_profile.get("failure_scores") or {})
    if target_mode == "top6_cover":
        cover_bonus = 0.0
        if len(set(1 if number <= 16 else 2 if number <= 33 else 3 for number in normal)) == 3:
            cover_bonus += 0.05
        if len(set(number % 10 for number in normal)) >= 5:
            cover_bonus += 0.04
        total += cover_bonus
    else:
        special_focus_bonus = 0.0
        if special in special_shortlist:
            special_focus_bonus += 0.05
        if special_vote > 0:
            special_focus_bonus += min(0.05, special_vote * 0.04)
        if target_mode == "top1_strict":
            special_focus_bonus += 0.04
        elif target_mode == "top1_safe":
            special_focus_bonus += 0.015
        total += special_focus_bonus

    failure_penalty = 0.0
    zone_spread = len(set(1 if number <= 16 else 2 if number <= 33 else 3 for number in normal))
    tail_spread = len(set(number % 10 for number in normal))
    all_numbers = normal + [special]
    color_spread = len(set(_get_color_zh(number) for number in all_numbers if _get_color_zh(number)))
    odd_count = sum(1 for number in all_numbers if number % 2 == 1)
    even_count = len(all_numbers) - odd_count
    parity_key = "parity:balanced" if abs(odd_count - even_count) <= 1 else "parity:skewed"
    special_zone = "small" if special <= 16 else "mid" if special <= 33 else "large"
    failure_penalty -= _safe_float(failure_scores.get(f"zone_spread:{zone_spread}"), 0.0) * 0.045
    failure_penalty -= _safe_float(failure_scores.get(f"color_spread:{color_spread}"), 0.0) * 0.035
    failure_penalty -= _safe_float(failure_scores.get(parity_key), 0.0) * 0.03
    failure_penalty -= _safe_float(failure_scores.get(f"special_zone:{special_zone}"), 0.0) * 0.04
    if tail_spread >= 5:
        failure_penalty -= _safe_float(failure_scores.get("tail_spread:wide"), 0.0) * 0.04
    elif tail_spread >= 4:
        failure_penalty -= _safe_float(failure_scores.get("tail_spread:balanced"), 0.0) * 0.04
    else:
        failure_penalty -= _safe_float(failure_scores.get("tail_spread:tight"), 0.0) * 0.04
    total += failure_penalty
    diagnostics = {
        "rerank_weights": rerank_weights,
        "target_mode": target_mode,
        "base_special": round(base_special, 6),
        "avg_normal": round(avg_normal, 6),
        "special_vote": round(special_vote, 4),
        "normal_vote_avg": round(normal_vote_avg, 4),
        "shortlist_bonus": round(shortlist_bonus, 4),
        "attr_bonus": round(attr_bonus, 4),
        "diversity_bonus": round(diversity_bonus, 4),
        "repeat_penalty": round(repeat_penalty, 4),
        "normal_repeat_penalty": round(normal_repeat_penalty, 4),
        "overheat_penalty": round(overheat_penalty, 4),
        "confidence_bonus": round(confidence_bonus, 4),
        "shape_score": round(shape_score, 4),
        "structure_bonus": round(structure_bonus, 4),
        "failure_penalty": round(failure_penalty, 4),
        "phase_label": str(phase_profile.get("label") or "neutral"),
        "phase_confidence": round(phase_confidence, 4),
        "gate_adjustment": round(gate_adjustment, 4),
        "total": round(total, 6),
    }
    diagnostics.update(shape_diagnostics)
    diagnostics.update(structure_diagnostics)
    return round(total, 6), diagnostics


def _build_ai_selection_recommendation(best_candidate, context, ranked_candidates):
    special = best_candidate.get("special")
    normal = best_candidate.get("normal") or []
    raw_text = _build_ai_dynamic_selection_text(best_candidate, context, ranked_candidates)
    return _compose_ai_recommendation_text(raw_text, str(special), normal, region=context.get("structured_payload", {}).get("region"))
    special_votes = context.get("special_votes") or Counter()
    normal_votes = context.get("normal_votes") or Counter()
    rationale = str(best_candidate.get("why") or "").strip()
    consensus_text = (
        f"系统重排得分：{best_candidate.get('rerank_score', 0.0):.4f}；"
        f"特码票数：{round(_safe_float(special_votes.get(int(special), 0.0)), 2)}；"
        f"六码平均票数："
        f"{round(sum(_safe_float(normal_votes.get(int(number), 0.0)) for number in normal) / max(len(normal), 1), 2)}。"
    )
    top_alternatives = "、".join(
        f"{item['special']}({item['aggregate_score']:.3f})"
        for item in ranked_candidates[:3]
    )
    raw_text = "\n".join(
        part for part in (
            rationale,
            f"补充说明：{consensus_text}" if consensus_text else "",
            f"候选排序：{top_alternatives}" if top_alternatives else "",
        ) if part
    )
    return _compose_ai_recommendation_text(raw_text, str(special), normal, region=context.get("structured_payload", {}).get("region"))


def _finalize_ai_multi_sample_result(ai_responses, region, context):
    rerank_weights = _normalize_ai_rerank_weights(context.get("rerank_weights"))
    appearance_vote_weight = rerank_weights.get("appearance_vote", 0.24)
    signature_votes = Counter()
    best_by_signature = {}

    for sample_index, response_text in enumerate(ai_responses or []):
        entries = _filter_ai_candidate_entries(
            _extract_ai_candidate_entries(response_text, region=region),
            context,
        )
        for rank_index, candidate in enumerate(entries):
            signature = f"{candidate['special']}|{','.join(map(str, candidate['normal']))}"
            appearance_weight = max(0.35, 1.0 - rank_index * 0.18) * max(0.6, 1.0 - sample_index * 0.08)
            signature_votes[signature] += appearance_weight
            score, diagnostics = _score_ai_candidate(candidate, context)
            enriched = {
                **candidate,
                "signature": signature,
                "rerank_score": score,
                "score_diagnostics": diagnostics,
            }
            previous = best_by_signature.get(signature)
            if previous is None or enriched["rerank_score"] > previous["rerank_score"]:
                best_by_signature[signature] = enriched

    if not best_by_signature:
        return None, "无法从AI回复中生成有效候选组合"

    ranked = sorted(
        [
            {
                **candidate,
                "appearance_votes": round(signature_votes[candidate["signature"]], 4),
                "aggregate_score": round(candidate["rerank_score"] + signature_votes[candidate["signature"]] * appearance_vote_weight, 6),
            }
            for candidate in best_by_signature.values()
        ],
        key=lambda item: (item["aggregate_score"], item["rerank_score"], item["appearance_votes"]),
        reverse=True,
    )
    ranked = _diversify_ai_ranked_candidates(ranked)
    best = ranked[0]

    result = {
        "normal": [int(number) for number in best.get("normal") or []],
        "special": {
            "number": str(best.get("special")),
            "sno_zodiac": "",
        },
        "recommendation_text": _build_ai_selection_recommendation(best, context, ranked),
        "model_meta": {
            "ai_sampling_count": len(ai_responses or []),
            "ai_unique_candidates": len(ranked),
            "ai_selected_score": round(best.get("aggregate_score", 0.0), 6),
            "ai_gate_profile": dict(context.get("gate_profile") or {}),
        "ai_target_mode": str(context.get("target_mode") or "top1_safe"),
            "ai_target_mode_stats": dict(context.get("target_mode_stats") or {}),
            "ai_rerank_weights": rerank_weights,
            "ai_rerank_learning_confidence": round(_safe_float(context.get("rerank_learning_confidence"), 0.0), 2),
            "ai_feedback_mix_weights": dict(context.get("feedback_mix_weights") or {}),
            "ai_phase_profile": dict(context.get("phase_profile") or {}),
            "ai_structure_profile": {
                "confidence": round(_safe_float(((context.get("structure_profile") or {}).get("confidence")), 0.0), 4),
                "samples": int(((context.get("structure_profile") or {}).get("samples")) or 0),
                "preferred_structures": list(((context.get("structure_profile") or {}).get("preferred_structures")) or []),
            },
            "ai_candidate_ranking": [
                {
                    "special": item["special"],
                    "normal": item["normal"],
                    "score": round(item["aggregate_score"], 6),
                    "diversified_score": round(item.get("diversified_score", item.get("aggregate_score", 0.0)), 6),
                    "diversity_penalty": round(item.get("diversity_penalty", 0.0), 6),
                    "quality_score": round(item.get("quality_score", 0.0), 6),
                    "votes": round(item["appearance_votes"], 4),
                    "score_diagnostics": dict(item.get("score_diagnostics") or {}),
                }
                for item in ranked[:5]
            ],
            "special_candidates": _normalize_special_candidate_numbers(
                [item.get("special") for item in ranked]
            ),
        },
    }
    return result, None


def _safe_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)


def _estimate_ai_decision_confidence(ai_result):
    meta = dict((ai_result or {}).get("model_meta") or {})
    ranking = list(meta.get("ai_candidate_ranking") or [])
    selected_score = _safe_float(meta.get("ai_selected_score"), 0.0)
    first_quality = _safe_float((ranking[0] or {}).get("quality_score"), -0.5) if ranking else -0.5
    candidate_count = max(1, len(ranking))
    unique_candidates = max(candidate_count, int(meta.get("ai_unique_candidates") or 0))
    quality_component = _clamp((first_quality + 0.12) / 0.3, 0.0, 1.0)
    score_component = _clamp(selected_score / 2.2, 0.0, 1.0)
    variety_component = _clamp(unique_candidates / 4.0, 0.35, 1.0)
    confidence = round(
        (quality_component * 0.45) +
        (score_component * 0.4) +
        (variety_component * 0.15),
        4,
    )
    return confidence, {
        "quality_component": round(quality_component, 4),
        "score_component": round(score_component, 4),
        "variety_component": round(variety_component, 4),
    }


def _blend_ai_with_anchor_strategy(ai_result, data, region, shortlist_context=None):
    if not ai_result or ai_result.get("error"):
        return ai_result

    shortlist_context = shortlist_context or {}
    gate_profile = dict((ai_result.get("model_meta") or {}).get("ai_gate_profile") or shortlist_context.get("gate_profile") or {})
    anchor_strategy = str(gate_profile.get("anchor_strategy") or "hybrid")
    if anchor_strategy == "ai":
        anchor_strategy = "hybrid"

    try:
        anchor_result = get_local_recommendations(anchor_strategy, data, region)
    except Exception:
        anchor_result = None
    if not anchor_result or anchor_result.get("error"):
        return ai_result

    ai_meta = dict(ai_result.get("model_meta") or {})
    anchor_meta = dict(anchor_result.get("model_meta") or {})
    ai_confidence, confidence_parts = _estimate_ai_decision_confidence(ai_result)
    ai_special = _safe_int((ai_result.get("special") or {}).get("number"), 0)
    anchor_special = _safe_int((anchor_result.get("special") or {}).get("number"), 0)
    special_score_map = dict(shortlist_context.get("special_score_map") or {})
    ai_special_score = _safe_float(special_score_map.get(ai_special), 0.0)
    anchor_special_score = _safe_float(special_score_map.get(anchor_special), 0.0)
    score_gap = _safe_float(gate_profile.get("score_gap"), 0.0)
    gate_status = str(gate_profile.get("status") or "active")
    ai_selected_score = _safe_float(ai_meta.get("ai_selected_score"), 0.0)
    ranking = list(ai_meta.get("ai_candidate_ranking") or [])
    ai_top_quality = _safe_float((ranking[0] or {}).get("quality_score"), -0.5) if ranking else -0.5
    anchor_bonus = 0.1 if anchor_special in [ _safe_int(item.get("special"), 0) for item in ranking[:3] ] else 0.0
    target_mode = str(ai_meta.get("ai_target_mode") or shortlist_context.get("target_mode") or "top1_safe")

    ai_decision_score = (
        ai_confidence * 0.48 +
        _clamp(ai_selected_score / 2.2, 0.0, 1.0) * 0.22 +
        _clamp((ai_top_quality + 0.15) / 0.35, 0.0, 1.0) * 0.18 +
        _clamp(ai_special_score, 0.0, 1.0) * 0.12
    )
    anchor_decision_score = (
        _clamp(anchor_special_score, 0.0, 1.0) * 0.44 +
        _clamp(score_gap / 6.0, 0.0, 1.0) * 0.26 +
        anchor_bonus +
        (0.16 if gate_status == "guarded" else 0.0)
    )
    if target_mode == "top6_cover":
        anchor_normal = [int(number) for number in (anchor_result.get("normal") or []) if str(number).isdigit()][:6]
        ai_normal = [int(number) for number in (ai_result.get("normal") or []) if str(number).isdigit()][:6]
        ai_zone_spread = len(set(1 if number <= 16 else 2 if number <= 33 else 3 for number in ai_normal))
        anchor_zone_spread = len(set(1 if number <= 16 else 2 if number <= 33 else 3 for number in anchor_normal))
        ai_decision_score += ai_zone_spread * 0.03
        anchor_decision_score += anchor_zone_spread * 0.035
    else:
        if ai_special == anchor_special and ai_special:
            ai_decision_score += 0.05

    decision_mode = "ai_primary"
    if gate_status == "guarded" or ai_confidence < 0.72 or anchor_bonus > 0:
        decision_mode = "blended"
    if ai_confidence < 0.52 or ai_top_quality < -0.08:
        decision_mode = "ai_low_confidence"

    selected = ai_result
    selected_meta = dict((selected.get("model_meta") or {}))
    selected_meta["ai_soft_fusion"] = {
        "decision_mode": decision_mode,
        "target_mode": target_mode,
        "anchor_strategy": anchor_strategy,
        "gate_status": gate_status,
        "ai_confidence": round(ai_confidence * 100, 2),
        "ai_decision_score": round(ai_decision_score, 4),
        "anchor_decision_score": round(anchor_decision_score, 4),
        "ai_special": str(ai_special) if ai_special else "",
        "anchor_special": str(anchor_special) if anchor_special else "",
        "confidence_parts": confidence_parts,
    }
    selected_meta["ai_gate_profile"] = gate_profile
    if decision_mode != "ai_primary":
        selected_meta["ai_anchor_reference"] = {
            "strategy": anchor_strategy,
            "special": str(anchor_special) if anchor_special else "",
            "normal": [int(number) for number in (anchor_result.get("normal") or [])[:6]],
            "special_score": round(anchor_special_score, 4),
        }
    selected["model_meta"] = selected_meta
    note_lines = []
    if decision_mode == "ai_low_confidence":
        note_lines.append("提示：本期 AI 置信度偏低，当前结果更偏保守，建议作为重点参考而非强结论。")
    elif decision_mode == "blended":
        note_lines.append(f"提示：本期 AI 处于联合判断模式，已参考 { _get_strategy_label(anchor_strategy) } 的结构信号来校正风险。")
    elif gate_status == "guarded":
        note_lines.append("提示：本期 AI 处于谨慎状态，已优先选择波动更小的候选组合。")

    ai_confidence_text = f"本期 AI 置信度：{round(ai_confidence * 100, 1)}%"
    if note_lines:
        note_lines.append(ai_confidence_text)
        existing_text = str(selected.get("recommendation_text") or "").strip()
        selected["recommendation_text"] = "\n".join(note_lines + ([existing_text] if existing_text else []))
    elif not str(selected.get("recommendation_text") or "").strip():
        selected["recommendation_text"] = ai_confidence_text
    return selected


def _finalize_ai_prediction_result(
    data,
    region,
    full_text="",
    ai_config=None,
    prompt="",
    temperature=0.35,
    sample_count=3,
    candidate_count=3,
    shortlist_context=None,
    tuned=None,
    stream_mode=True,
):
    tuned = dict(tuned or _load_strategy_config("ai", region))
    shortlist_context = shortlist_context or _build_ai_shortlist_context(data, region, config=tuned)
    ai_config = ai_config or get_ai_config()
    if not prompt:
        prompt = _build_ai_prompt_v4(
            data,
            region,
            shortlist_context,
            history_window=int(tuned.get("history_window") or 12),
            candidate_count=candidate_count,
        )
    return _run_ai_prediction_pipeline(
        data,
        region,
        tuned,
        ai_config,
        shortlist_context,
        prompt,
        temperature=temperature,
        sample_count=sample_count,
        candidate_count=candidate_count,
        initial_response_text=full_text,
        stream_mode=stream_mode,
    )


def predict_with_ai(data, region, config_override=None):
    ai_config = get_ai_config()
    if not ai_config['api_key'] or "你的" in ai_config['api_key']:
        return {"error": "AI API Key 未配置"}
    tuned = _load_strategy_config("ai", region)
    if config_override:
        tuned = {**tuned, **dict(config_override)}
    history_window = int(tuned.get("history_window") or 12)
    temperature = float(tuned.get("temperature") or 0.35)
    sample_count = _clamp(int(tuned.get("sample_count") or 3), 1, 5)
    candidate_count = _clamp(int(tuned.get("candidate_count") or 3), 2, 5)
    try:
        shortlist_context = _build_ai_shortlist_context(data, region, config=tuned)
        prompt = _build_ai_prompt_v4(
            data,
            region,
            shortlist_context,
            history_window=history_window,
            candidate_count=candidate_count,
        )
        return _run_ai_prediction_pipeline(
            data,
            region,
            tuned,
            ai_config,
            shortlist_context,
            prompt,
            temperature=temperature,
            sample_count=sample_count,
            candidate_count=candidate_count,
            stream_mode=False,
        )
    except Exception as e:
        return {"error": f"调用AI API时出错: {e}"}

def _iter_ai_stream(ai_config, prompt, temperature=0.8):
    payload = {
        "model": ai_config["model"],
        "messages": [
            {"role": "system", "content": _build_ai_system_prompt()},
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature,
        "stream": True
    }
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    response = requests.post(ai_config["api_url"], json=payload, headers=headers, stream=True, timeout=_ai_http_timeout())
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
        response.encoding = "utf-8"
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content is None:
            content = choices[0].get("message", {}).get("content")
        if content is None:
            content = choices[0].get("text")
        if content:
            yield content

# --- Flask 路由 ---
@app.route('/')
def index():
    # 检查用户登录状态，如果未登录则重定向到登录页面
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    # 检查用户是否激活，如果未激活则显示提示
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        flash('登录状态已失效，请重新登录。', 'warning')
        return redirect(url_for('auth.login'))
    
    # 检查激活状态是否过期
    if user and user.is_activation_expired():
        user.is_active = False
        db.session.commit()
        session['is_active'] = False
        flash('您的账号激活已过期，请使用新的激活码重新激活。', 'warning')
    
    if not user.is_active:
        flash('您的账号尚未激活，部分功能受限。请先激活账号。', 'warning')
    
    return render_template('index.html', user=user, show_normal_numbers=bool(user.show_normal_numbers))

def get_yearly_data(region, year):
    print(f"获取年度数据: 地区={region}, 年份={year}")
    
    # 处理"全部"年份的情况
    if year == 'all':
        year = str(datetime.now().year)
        print(f"年份为'全部'，使用当前年份: {year}")
    
    # 首先尝试从数据库获取数据
    try:
        # 查询数据库中的开奖记录
        query = LotteryDraw.query.filter_by(region=region)
        if year != 'all':
            query = query.filter(LotteryDraw.draw_date.like(f"{year}%"))
        
        db_records = query.order_by(LotteryDraw.draw_date.desc()).all()
        
        if db_records:
            print(f"从数据库获取到{len(db_records)}条{region}地区{year}年的数据")
            # 将数据库记录转换为API格式
            return [record.to_dict() for record in db_records]
    except Exception as e:
        print(f"从数据库获取数据失败: {e}")
    
    # 如果数据库中没有数据，则从API获取
    if region == 'hk':
        filtered_data = sync_draws_from_api('hk', year, force=True)
        print(f"从API获取香港数据: 过滤后={len(filtered_data)}")
        return filtered_data
    if region == 'macau':
        macau_data = sync_draws_from_api('macau', year, force=True)
        print(f"从API获取澳门数据: 总数={len(macau_data)}")
        return macau_data
    print(f"未知地区: {region}")
    return []


def _apply_draw_year_filter(query, year):
    if year and year != 'all':
        year_text = str(year)
        if len(year_text) == 4 and year_text.isdigit():
            return query.filter(
                LotteryDraw.draw_date >= f"{year_text}-01-01",
                LotteryDraw.draw_date < f"{int(year_text) + 1}-01-01",
            )
        return query.filter(LotteryDraw.draw_date.like(f"{year_text}%"))
    return query


def _load_draw_page_from_db(region, year, page, page_size):
    query = LotteryDraw.query.filter_by(region=region)
    query = _apply_draw_year_filter(query, year)
    records = (
        query.order_by(LotteryDraw.draw_date.desc(), LotteryDraw.draw_id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [record.to_dict() for record in records]


def _get_draws_cache(cache_key):
    with _draws_api_cache_lock:
        cached = _draws_api_cache.get(cache_key)
        if cached:
            if time.time() - cached.get('created_at', 0) > _DRAWS_API_CACHE_TTL:
                _draws_api_cache.pop(cache_key, None)
                return None
            return copy.deepcopy(cached['data'])
    return None


def _set_draws_cache(cache_key, data):
    with _draws_api_cache_lock:
        if len(_draws_api_cache) > 128:
            _draws_api_cache.clear()
        _draws_api_cache[cache_key] = {
            'created_at': time.time(),
            'data': copy.deepcopy(data),
        }


def _clear_draws_cache(region=None):
    normalized_region = str(region or "").strip().lower()
    with _draws_api_cache_lock:
        if not normalized_region:
            _draws_api_cache.clear()
            return
        for key in [
            key
            for key in _draws_api_cache.keys()
            if isinstance(key, tuple) and str(key[0] or "").strip().lower() == normalized_region
        ]:
            _draws_api_cache.pop(key, None)


def _clear_draw_dependent_caches(region=None):
    _clear_draws_cache(region)
    _clear_ai_prediction_cache(region)
    _clear_ml_prediction_cache(region)
    _clear_runtime_analysis_caches()
    try:
        from user import clear_user_runtime_caches
        clear_user_runtime_caches()
    except Exception:
        pass


def _filter_draws_by_zodiac_year(draws, zodiac_year):
    try:
        from models import ZodiacSetting
    except Exception:
        return list(draws or [])

    filtered = []
    for draw in draws or []:
        if hasattr(draw, "to_dict"):
            normalized = draw.to_dict()
        elif isinstance(draw, dict):
            normalized = dict(draw)
        else:
            continue

        draw_date = normalized.get("date", "")
        if ZodiacSetting.get_zodiac_year_for_date(draw_date) == zodiac_year:
            filtered.append(normalized)

    filtered.sort(
        key=lambda item: (str(item.get("date") or ""), _period_sort_key(item.get("id"))),
        reverse=True,
    )
    return filtered


def _resolve_prediction_zodiac_year(year):
    from models import ZodiacSetting

    current_zodiac_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
    current_gregorian_year = datetime.now().year
    raw_year = str(year or "").strip().lower()

    if not raw_year or raw_year == "all":
        return current_zodiac_year

    try:
        parsed_year = int(raw_year)
    except (TypeError, ValueError):
        return current_zodiac_year

    # 前端当前传的是公历年份；当它等于当前公历年时，预测应切换到当前农历生肖年。
    if parsed_year == current_gregorian_year:
        return current_zodiac_year
    return parsed_year


def _get_prediction_data(region, year):
    target_zodiac_year = _resolve_prediction_zodiac_year(year)
    candidate_years = [str(target_zodiac_year), str(target_zodiac_year + 1)]

    draw_groups = []
    for index, candidate_year in enumerate(candidate_years):
        year_records = []
        try:
            year_records = (
                LotteryDraw.query.filter_by(region=region)
                .filter(LotteryDraw.draw_date.like(f"{candidate_year}%"))
                .all()
            )
        except Exception as e:
            print(f"从数据库获取 {region} 地区 {candidate_year} 年数据失败: {e}")

        if year_records:
            draw_groups.append(year_records)
            continue

        # 预测阶段只主动补拉目标年份的数据；下一公历年的数据仅使用库内已有记录，
        # 避免在年中请求尚未产生开奖的下一年接口。
        if index == 0:
            try:
                remote_records = sync_draws_from_api(region, candidate_year, force=True)
                if remote_records:
                    draw_groups.append(remote_records)
            except Exception as e:
                print(f"同步 {region} 地区 {candidate_year} 年数据失败: {e}")

    merged = _merge_draw_history_desc(*draw_groups)
    filtered = _filter_draws_by_zodiac_year(merged, target_zodiac_year)
    return filtered, target_zodiac_year


def _latest_draw_cache_marker(region):
    try:
        record = (
            LotteryDraw.query.filter_by(region=region)
            .order_by(LotteryDraw.draw_date.desc(), LotteryDraw.draw_id.desc())
            .first()
        )
    except Exception:
        return None
    if not record:
        return None
    return (
        str(record.draw_id or "").strip(),
        str(record.draw_date or "").strip(),
        str(record.normal_numbers or "").strip(),
        str(record.special_number or "").strip(),
        str(record.special_zodiac or "").strip(),
    )


def save_draws_to_database(draws, region):
    """保存开奖记录到数据库"""
    try:
        before_latest = _latest_draw_cache_marker(region)
        count = 0
        for draw in draws:
            # 调用LotteryDraw模型的save_draw方法保存记录
            if LotteryDraw.save_draw(region, draw):
                count += 1
                settled = settle_pending_manual_bets(region, draw.get('id'))
                if settled:
                    print(f"已自动结算{settled}条手动下注记录，期号: {draw.get('id')}")
        
        after_latest = _latest_draw_cache_marker(region)
        if count and before_latest != after_latest:
            _clear_draw_dependent_caches(region)
        print(f"成功保存{count}条{region}地区的开奖记录到数据库")
    except Exception as e:
        print(f"保存开奖记录到数据库失败: {e}")
        db.session.rollback()

AUTO_BACKTEST_STRATEGIES = ("ml", "markov", "hybrid", "balanced", "trend", "hot", "cold")
AUTO_BACKTEST_MIN_HISTORY = 10
AUTO_BACKTEST_LIMIT = 240
AI_BACKTEST_MAX_PERIODS = 24
POSTPROCESS_BACKTEST_STRATEGIES = ("ml", "markov", "hybrid", "balanced", "trend", "hot", "cold")
POSTPROCESS_TUNING_STRATEGIES = POSTPROCESS_BACKTEST_STRATEGIES
POSTPROCESS_BACKTEST_LIMIT = 120


def _ai_backtest_enabled():
    ai_config = get_ai_config()
    api_key = str(ai_config.get("api_key") or "").strip()
    return bool(api_key and "你的" not in api_key)


def _filter_backtest_strategies(strategies):
    return [strategy for strategy in (strategies or []) if strategy != "ai"]


def _backtest_draw_sort_key(draw):
    return (str(draw.get("date") or ""), _period_sort_key(draw.get("id")))


def _normalize_backtest_draws(draws, limit=None):
    normalized = []
    for draw in draws or []:
        if hasattr(draw, "to_dict"):
            normalized.append(draw.to_dict())
        elif isinstance(draw, dict):
            normalized.append(draw)
    normalized.sort(key=_backtest_draw_sort_key)
    if limit and limit > 0 and len(normalized) > limit:
        normalized = normalized[-limit:]
    return normalized


def _merge_draw_history_desc(*draw_groups, limit=None):
    merged = []
    seen_periods = set()
    for group in draw_groups:
        for draw in group or []:
            if hasattr(draw, "to_dict"):
                normalized = draw.to_dict()
            elif isinstance(draw, dict):
                normalized = dict(draw)
            else:
                continue
            period = _normalize_period_value(normalized.get("id"))
            if not period or period in seen_periods:
                continue
            seen_periods.add(period)
            merged.append(normalized)
    merged.sort(
        key=lambda item: (str(item.get("date") or ""), _period_sort_key(item.get("id"))),
        reverse=True,
    )
    if limit and limit > 0:
        return merged[:limit]
    return merged


LEARNING_SCOPE_MIN_SAMPLES = 36
LEARNING_SCOPE_DRAW_LIMIT = 720


def _resolve_learning_scope_zodiac_year(region, cutoff_period=None, fallback_data=None):
    try:
        from models import ZodiacSetting
    except Exception:
        return _infer_draw_year(fallback_data) if fallback_data else datetime.now().year

    cutoff_period = _normalize_period_value(cutoff_period or _current_backtest_cutoff_period())
    if cutoff_period:
        try:
            draw = LotteryDraw.query.filter_by(region=region, draw_id=cutoff_period).first()
            if draw and draw.draw_date:
                return ZodiacSetting.get_zodiac_year_for_date(draw.draw_date)
        except Exception:
            pass

    for draw in fallback_data or []:
        try:
            draw_date = (draw.to_dict() if hasattr(draw, "to_dict") else draw).get("date", "")
            if draw_date:
                return ZodiacSetting.get_zodiac_year_for_date(draw_date)
        except Exception:
            continue

    try:
        return ZodiacSetting.get_zodiac_year_for_date(datetime.now())
    except Exception:
        return datetime.now().year


def _build_learning_draw_year_map(region):
    cache_key = (str(region or "").strip().lower(), LEARNING_SCOPE_DRAW_LIMIT)
    cached = _runtime_cache_get("learning_draw_year_map", cache_key)
    if cached is not None:
        return cached

    try:
        from models import ZodiacSetting
    except Exception:
        return {}

    try:
        draw_records = (
            LotteryDraw.query.filter_by(region=region)
            .order_by(LotteryDraw.draw_date.desc(), LotteryDraw.draw_id.desc())
            .limit(LEARNING_SCOPE_DRAW_LIMIT)
            .all()
        )
    except Exception as e:
        print(f"加载{region}学习样本开奖范围失败: {e}")
        return {}

    draw_year_map = {}
    for draw in draw_records:
        if hasattr(draw, "to_dict"):
            normalized = draw.to_dict()
        elif isinstance(draw, dict):
            normalized = dict(draw)
        else:
            continue
        period = _normalize_period_value(normalized.get("id"))
        draw_date = normalized.get("date", "")
        if not period or not draw_date:
            continue
        try:
            draw_year_map[period] = ZodiacSetting.get_zodiac_year_for_date(draw_date)
        except Exception:
            continue

    return _runtime_cache_set("learning_draw_year_map", cache_key, draw_year_map)


def _load_learning_scope_predictions(region, strategy, limit=None, minimum_samples=LEARNING_SCOPE_MIN_SAMPLES, cutoff_period=None):
    query = PredictionRecord.query.filter_by(
        region=region,
        strategy=strategy,
        is_result_updated=True,
    ).filter(PredictionRecord.actual_special_number != None)
    predictions = query.order_by(PredictionRecord.created_at.desc()).all()
    if not predictions:
        return []

    if cutoff_period:
        predictions = [
            prediction for prediction in predictions
            if _is_period_before(prediction.period, cutoff_period)
        ]

    scoped = _apply_lunar_learning_scope_to_predictions(
        predictions,
        region,
        minimum_samples=minimum_samples,
        cutoff_period=cutoff_period,
    )
    if limit:
        return scoped[:limit]
    return scoped


def _apply_lunar_learning_scope_to_predictions(predictions, region, minimum_samples=LEARNING_SCOPE_MIN_SAMPLES, cutoff_period=None):
    try:
        from models import ZodiacSetting
    except Exception:
        return list(predictions or [])
    current_zodiac_year = _resolve_learning_scope_zodiac_year(region, cutoff_period=cutoff_period)
    draw_year_map = _build_learning_draw_year_map(region)

    current_year_predictions = []
    previous_year_predictions = []
    fallback_predictions = []

    for pred in predictions or []:
        period = _normalize_period_value(getattr(pred, "period", ""))
        zodiac_year = draw_year_map.get(period)
        if zodiac_year is None:
            created_at = getattr(pred, "created_at", None)
            if created_at:
                try:
                    zodiac_year = ZodiacSetting.get_zodiac_year_for_date(created_at)
                except Exception:
                    zodiac_year = None

        if zodiac_year == current_zodiac_year:
            current_year_predictions.append(pred)
        elif zodiac_year == current_zodiac_year - 1:
            previous_year_predictions.append(pred)
        else:
            fallback_predictions.append(pred)

    scoped = list(current_year_predictions)
    if len(scoped) < minimum_samples:
        scoped.extend(previous_year_predictions)

    return scoped or fallback_predictions or list(predictions or [])


def _ensure_ml_prediction_history(data, region, minimum_draws=36, target_draws=240):
    cutoff_period = _current_backtest_cutoff_period()
    current_zodiac_year = _resolve_learning_scope_zodiac_year(
        region,
        cutoff_period=cutoff_period,
        fallback_data=data,
    )

    current_year_data = _filter_draws_by_zodiac_year(data, current_zodiac_year)
    primary = _merge_draw_history_desc(current_year_data, limit=target_draws)
    if len(primary) >= minimum_draws:
        return primary, 0

    try:
        db_records = (
            LotteryDraw.query.filter_by(region=region)
            .order_by(LotteryDraw.draw_date.desc(), LotteryDraw.draw_id.desc())
            .limit(max(target_draws * 3, 720))
            .all()
        )
    except Exception as e:
        print(f"补充{region}机器学习历史样本失败: {e}")
        return primary, 0

    current_year_db_records = _filter_draws_by_zodiac_year(db_records, current_zodiac_year)
    if cutoff_period:
        current_year_db_records = [
            record for record in current_year_db_records
            if _is_period_before(
                (record.to_dict() if hasattr(record, "to_dict") else record).get("id"),
                cutoff_period,
            )
        ]
    merged = _merge_draw_history_desc(primary, current_year_db_records, limit=target_draws)

    if len(merged) < minimum_draws:
        previous_year_db_records = _filter_draws_by_zodiac_year(db_records, current_zodiac_year - 1)
        if cutoff_period:
            previous_year_db_records = [
                record for record in previous_year_db_records
                if _is_period_before(
                    (record.to_dict() if hasattr(record, "to_dict") else record).get("id"),
                    cutoff_period,
                )
            ]
        merged = _merge_draw_history_desc(merged, previous_year_db_records, limit=target_draws)

    supplemental = max(0, len(merged) - len(primary))
    return merged, supplemental


def _load_backtest_draws_from_db(region, limit=AUTO_BACKTEST_LIMIT):
    records = (
        LotteryDraw.query.filter_by(region=region)
        .order_by(LotteryDraw.draw_date.asc(), LotteryDraw.draw_id.asc())
        .all()
    )
    return _normalize_backtest_draws(records, limit=limit)


def _evaluate_backtest_prediction(result, draw):
    actual_special = str(draw.get("sno") or "").strip()
    actual_zodiac = str(draw.get("sno_zodiac") or "").strip()
    predicted_special = str((result.get("special") or {}).get("number") or "").strip()
    predicted_zodiac = str((result.get("special") or {}).get("sno_zodiac") or "").strip()
    normal_numbers = [str(number) for number in (result.get("normal") or [])]
    return {
        "exact_hit": bool(actual_special and predicted_special == actual_special),
        "top6_hit": bool(actual_special and actual_special in normal_numbers),
        "zodiac_hit": bool(actual_zodiac and predicted_zodiac and actual_zodiac == predicted_zodiac),
        "predicted_special": predicted_special,
        "actual_special": actual_special,
    }


def _summarize_backtest_window(entries, window):
    sample = entries[-window:] if window and len(entries) > window else list(entries)
    total = len(sample)
    if total <= 0:
        return {"window": window, "total": 0, "top1_hit_rate": 0.0, "top6_hit_rate": 0.0, "zodiac_hit_rate": 0.0}
    top1 = sum(1 for item in sample if item.get("exact_hit"))
    top6 = sum(1 for item in sample if item.get("top6_hit"))
    zodiac = sum(1 for item in sample if item.get("zodiac_hit"))
    return {
        "window": window,
        "total": total,
        "top1_hit_rate": round(top1 / total * 100, 2),
        "top6_hit_rate": round(top6 / total * 100, 2),
        "zodiac_hit_rate": round(zodiac / total * 100, 2),
    }


def _summarize_backtest_entries(entries):
    total = len(entries)
    if total <= 0:
        return {"total": 0, "top1_hit_rate": 0.0, "top6_hit_rate": 0.0, "zodiac_hit_rate": 0.0, "windows": []}
    top1 = sum(1 for item in entries if item.get("exact_hit"))
    top6 = sum(1 for item in entries if item.get("top6_hit"))
    zodiac = sum(1 for item in entries if item.get("zodiac_hit"))
    return {
        "total": total,
        "top1_hit_rate": round(top1 / total * 100, 2),
        "top6_hit_rate": round(top6 / total * 100, 2),
        "zodiac_hit_rate": round(zodiac / total * 100, 2),
        "windows": [
            _summarize_backtest_window(entries, 20),
            _summarize_backtest_window(entries, 50),
            _summarize_backtest_window(entries, 100),
        ],
    }


def _build_backtest_snapshot_payload(region, draws, strategies=None, min_history=AUTO_BACKTEST_MIN_HISTORY):
    strategies = _filter_backtest_strategies(list(strategies or AUTO_BACKTEST_STRATEGIES))
    chronological = _normalize_backtest_draws(draws, limit=AUTO_BACKTEST_LIMIT)
    strategy_logs = {strategy: [] for strategy in strategies}
    detail_rows = []
    effective_min_history = min(max(1, int(min_history or 1)), max(1, len(chronological) - 1))
    ai_start_idx = max(effective_min_history, len(chronological) - AI_BACKTEST_MAX_PERIODS)
    if len(chronological) <= 1:
        return {
            "region": region,
            "strategies": strategies,
            "periods_evaluated": 0,
            "latest_period": chronological[-1].get("id") if chronological else "",
            "strategy_results": {},
            "ranking": [],
            "details": [],
        }

    for idx in range(effective_min_history, len(chronological)):
        target_draw = chronological[idx]
        history_desc = list(reversed(chronological[:idx]))
        for strategy in strategies:
            if strategy == "ai" and idx < ai_start_idx:
                continue
            resolved_strategy = strategy
            try:
                if strategy == "ai":
                    result = predict_with_ai(
                        history_desc,
                        region,
                        config_override={
                            "sample_count": 1,
                            "candidate_count": 2,
                            "history_window": 10,
                            "special_shortlist": 6,
                            "normal_shortlist": 14,
                        },
                    )
                else:
                    with _temporary_backtest_cutoff_period(target_draw.get("id")):
                        with _temporary_strict_backtest_strategy():
                            result = get_local_recommendations(resolved_strategy, history_desc, region)
                if result.get("error"):
                    raise ValueError(result.get("error"))
            except Exception as e:
                strategy_logs[strategy].append({
                    "period": target_draw.get("id"),
                    "error": str(e),
                    "exact_hit": False,
                    "top6_hit": False,
                    "zodiac_hit": False,
                })
                continue

            evaluation = _evaluate_backtest_prediction(result, target_draw)
            row = {
                "period": target_draw.get("id"),
                "strategy": strategy,
                "resolved_strategy": resolved_strategy,
                **evaluation,
            }
            strategy_logs[strategy].append(row)
            detail_rows.append(row)

    strategy_results = {
        strategy: _summarize_backtest_entries(entries)
        for strategy, entries in strategy_logs.items()
    }
    ranking = sorted(
        [
            {
                "strategy": strategy,
                "top1_hit_rate": summary.get("top1_hit_rate", 0.0),
                "top6_hit_rate": summary.get("top6_hit_rate", 0.0),
                "zodiac_hit_rate": summary.get("zodiac_hit_rate", 0.0),
                "composite_score": round(
                    _safe_float(summary.get("top1_hit_rate"), 0.0) +
                    _safe_float(summary.get("top6_hit_rate"), 0.0) * 0.35 +
                    _safe_float(summary.get("zodiac_hit_rate"), 0.0) * 0.15,
                    4,
                ),
                "total": summary.get("total", 0),
            }
            for strategy, summary in strategy_results.items()
        ],
        key=lambda item: (item["top1_hit_rate"], item["top6_hit_rate"], item["total"]),
        reverse=True,
    )
    return {
        "region": region,
        "strategies": strategies,
        "periods_evaluated": max(0, len(chronological) - effective_min_history),
        "latest_period": chronological[-1].get("id") if chronological else "",
        "strategy_results": strategy_results,
        "ranking": ranking,
        "details": detail_rows,
    }


def _persist_backtest_snapshot(region, payload):
    latest_period = str(payload.get("latest_period") or "").strip() or "unknown"
    name = f"auto-{region}-{latest_period}"
    existing = BacktestRun.query.filter_by(region=region, name=name).first()
    if existing:
        existing.strategies = ",".join(payload.get("strategies") or [])
        existing.periods_evaluated = int(payload.get("periods_evaluated") or 0)
        existing.payload = json.dumps(payload, ensure_ascii=False)
        existing.created_at = datetime.now()
        db.session.commit()
        return existing

    record = BacktestRun(
        name=name,
        region=region,
        strategies=",".join(payload.get("strategies") or []),
        periods_evaluated=int(payload.get("periods_evaluated") or 0),
        payload=json.dumps(payload, ensure_ascii=False),
    )
    db.session.add(record)
    db.session.commit()
    return record


def refresh_auto_backtest_snapshot(region, draws=None, force=False, strategies=None, limit=AUTO_BACKTEST_LIMIT):
    region = (region or "").strip().lower()
    if region not in ("hk", "macau"):
        return None
    source_draws = _normalize_backtest_draws(
        draws if draws is not None else _load_backtest_draws_from_db(region, limit=limit),
        limit=limit,
    )
    if not source_draws:
        return None

    latest_period = str(source_draws[-1].get("id") or "").strip()
    name = f"auto-{region}-{latest_period}"
    existing = BacktestRun.query.filter_by(region=region, name=name).first()
    if existing and not force:
        return existing

    payload = _build_backtest_snapshot_payload(region, source_draws, strategies=strategies)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    record = _persist_backtest_snapshot(region, payload)
    try:
        _promote_ml_region_profile(region, persist=True)
    except Exception as e:
        print(f"ML auto-promotion after backtest failed for {region}: {e}")
    try:
        _promote_markov_region_profile(region, persist=True)
    except Exception as e:
        print(f"Markov auto-promotion after backtest failed for {region}: {e}")
    return record


def refresh_auto_backtest_snapshots(regions=None, force=False):
    refreshed = []
    for region in (regions or ("hk", "macau")):
        try:
            record = refresh_auto_backtest_snapshot(region, force=force)
            if record:
                refreshed.append(record)
        except Exception as e:
            print(f"Auto backtest snapshot failed for {region}: {e}")
            db.session.rollback()
    return refreshed


AUTO_OPTIMIZE_STRATEGIES = ("hot", "cold", "trend", "balanced", "hybrid", "markov", "ml")
AUTO_OPTIMIZE_BACKTEST_PERIODS = 72


def _auto_optimize_enabled():
    raw = str(SystemConfig.get_config("auto_optimize_enabled", "false")).strip().lower()
    return raw in {"true", "1", "yes", "on"}


def _auto_optimize_level():
    level = str(SystemConfig.get_config("auto_optimize_level", "balanced")).strip().lower()
    return level if level in {"mild", "balanced", "aggressive"} else "balanced"


def _auto_optimize_min_gain():
    try:
        value = float(SystemConfig.get_config("auto_optimize_min_gain", "0.6"))
    except (TypeError, ValueError):
        value = 0.6
    return round(_clamp(value, 0.1, 8.0), 2)


def _score_auto_optimize_summary(summary):
    summary = dict(summary or {})
    windows = list(summary.get("windows") or [])
    base_score = (
        _safe_float(summary.get("top1_hit_rate"), 0.0) * 1.0 +
        _safe_float(summary.get("top6_hit_rate"), 0.0) * 0.35 +
        _safe_float(summary.get("zodiac_hit_rate"), 0.0) * 0.15
    )
    recency_bonus = 0.0
    for idx, window in enumerate(windows[:3]):
        weight = max(0.2, 0.55 - idx * 0.15)
        recency_bonus += (
            _safe_float(window.get("top1_hit_rate"), 0.0) * 0.22 +
            _safe_float(window.get("top6_hit_rate"), 0.0) * 0.08
        ) * weight
    return round(base_score + recency_bonus, 4)


def _score_auto_optimize_window(window):
    window = dict(window or {})
    return round(
        _safe_float(window.get("top1_hit_rate"), 0.0) * 1.0 +
        _safe_float(window.get("top6_hit_rate"), 0.0) * 0.35 +
        _safe_float(window.get("zodiac_hit_rate"), 0.0) * 0.15,
        4,
    )


def _passes_markov_window_consistency_gate(baseline_summary, candidate_summary, min_gain):
    baseline_windows = list((baseline_summary or {}).get("windows") or [])
    candidate_windows = list((candidate_summary or {}).get("windows") or [])
    paired_windows = [
        (baseline, candidate)
        for baseline, candidate in zip(baseline_windows, candidate_windows)
        if int(baseline.get("total") or 0) >= 10 and int(candidate.get("total") or 0) >= 10
    ]
    if len(paired_windows) < 2:
        return True

    gains = [
        _score_auto_optimize_window(candidate) - _score_auto_optimize_window(baseline)
        for baseline, candidate in paired_windows
    ]
    required_improved_windows = min(2, len(gains))
    improved_windows = sum(1 for gain in gains if gain >= 0.05)
    worst_allowed_drop = -max(float(min_gain or 0.0), 0.8)
    return improved_windows >= required_improved_windows and min(gains) >= worst_allowed_drop


def _dedupe_auto_optimize_candidates(candidates):
    deduped = []
    seen = set()
    for candidate in candidates or []:
        normalized = json.dumps(candidate, sort_keys=True, ensure_ascii=True)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(candidate)
    return deduped


def _build_auto_optimize_candidates(strategy, config, level="balanced"):
    level_map = {
        "mild": {"window": 4, "pool": 1, "special_pool": 1, "trend_window": 2, "history": 12, "feature": 6, "eval": 4, "lr": 0.006, "l2": 0.0006},
        "balanced": {"window": 8, "pool": 2, "special_pool": 1, "trend_window": 3, "history": 20, "feature": 10, "eval": 6, "lr": 0.01, "l2": 0.001},
        "aggressive": {"window": 12, "pool": 3, "special_pool": 2, "trend_window": 4, "history": 28, "feature": 14, "eval": 8, "lr": 0.014, "l2": 0.0014},
    }
    delta = level_map.get(level, level_map["balanced"])
    candidates = []

    if strategy in {"hot", "cold", "trend", "balanced", "hybrid", "markov"}:
        base_window = int(config.get("window") or _default_strategy_config(strategy).get("window") or 12)
        base_pool = int(config.get("pool") or _default_strategy_config(strategy).get("pool") or 16)
        base_special = int(config.get("special_pool") or _default_strategy_config(strategy).get("special_pool") or 8)
        window_high = 160 if strategy == "markov" else 96
        candidates.extend([
            {
                "window": _clamp(base_window - delta["window"], 8, window_high),
                "pool": _clamp(base_pool + delta["pool"], 8, 24),
                "special_pool": _clamp(base_special, 6, 14),
            },
            {
                "window": _clamp(base_window + delta["window"], 8, window_high),
                "pool": _clamp(base_pool - delta["pool"], 8, 24),
                "special_pool": _clamp(base_special + delta["special_pool"], 6, 14),
            },
            {
                "window": _clamp(base_window, 8, window_high),
                "pool": _clamp(base_pool, 8, 24),
                "special_pool": _clamp(base_special - delta["special_pool"], 6, 14),
            },
        ])
        if strategy == "markov":
            base_decay = float(config.get("transition_decay") or 0.985)
            base_source_weight = float(config.get("source_special_weight") or 1.28)
            base_min_samples = int(config.get("transition_min_samples") or 3)
            base_repeat_penalty = float(config.get("repeat_penalty", -0.18) or -0.18)
            base_weights = dict(config.get("weights") or {})
            base_transition = float(base_weights.get("transition") or 1.35)
            base_transition_lift = float(base_weights.get("transition_lift") or 0.32)
            base_special_transition = float(base_weights.get("special_transition") or 1.1)
            base_special_transition_lift = float(base_weights.get("special_transition_lift") or 0.28)
            base_special_chain = float(base_weights.get("special_chain") or 0.62)
            base_special_attribute = float(base_weights.get("special_attribute") or 0.28)
            base_second = float(base_weights.get("second_order") or 0.72)
            base_phase = float(base_weights.get("phase_transition") or 0.55)
            base_attribute = float(base_weights.get("attribute_transition") or 0.42)
            base_failure = float(base_weights.get("failure") or 0.48)
            candidates.extend([
                {"transition_decay": round(_clamp(base_decay - 0.006, 0.965, 0.995), 4), "window": _clamp(base_window, 24, 160), "pool": _clamp(base_pool + 1, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
                {"transition_decay": round(_clamp(base_decay + 0.006, 0.965, 0.995), 4), "window": _clamp(base_window + delta["window"], 24, 160), "pool": _clamp(base_pool, 8, 24), "special_pool": _clamp(base_special + 1, 6, 14)},
                {"source_special_weight": round(_clamp(base_source_weight + 0.12, 1.0, 1.7), 3), "window": _clamp(base_window, 24, 160), "pool": _clamp(base_pool, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
                {"source_special_weight": round(_clamp(base_source_weight - 0.12, 1.0, 1.7), 3), "transition_min_samples": _clamp(base_min_samples + 1, 1, 12)},
                {"transition_min_samples": _clamp(base_min_samples - 1, 1, 12), "repeat_penalty": round(_clamp(base_repeat_penalty + 0.04, -0.35, -0.04), 3)},
                {"repeat_penalty": round(_clamp(base_repeat_penalty - 0.05, -0.35, -0.04), 3), "weights": {"feedback": round(_clamp(float(base_weights.get("feedback") or 0.85) + 0.08, 0.45, 1.45), 2)}},
                {"weights": {"transition": round(_clamp(base_transition + 0.12, 0.65, 1.9), 2), "special_transition": round(_clamp(base_special_transition + 0.1, 0.65, 1.65), 2)}},
                {"weights": {"transition": round(_clamp(base_transition - 0.12, 0.65, 1.9), 2), "special_transition": round(_clamp(base_special_transition - 0.08, 0.65, 1.65), 2), "normal": round(_clamp(float(base_weights.get("normal") or 0.22) + 0.06, 0.12, 0.55), 2)}},
                {"weights": {"transition_lift": round(_clamp(base_transition_lift + 0.12, 0.0, 0.9), 2), "special_transition_lift": round(_clamp(base_special_transition_lift + 0.1, 0.0, 0.85), 2)}},
                {"weights": {"transition_lift": round(_clamp(base_transition_lift - 0.1, 0.0, 0.9), 2), "special_transition_lift": round(_clamp(base_special_transition_lift - 0.08, 0.0, 0.85), 2), "transition": round(_clamp(base_transition + 0.06, 0.65, 1.9), 2)}},
                {"weights": {"special_chain": round(_clamp(base_special_chain + 0.12, 0.0, 1.2), 2), "special_attribute": round(_clamp(base_special_attribute + 0.08, 0.0, 0.8), 2)}},
                {"weights": {"special_chain": round(_clamp(base_special_chain - 0.1, 0.0, 1.2), 2), "special_transition": round(_clamp(base_special_transition + 0.08, 0.65, 1.65), 2)}},
                {"weights": {"second_order": round(_clamp(base_second + 0.16, 0.0, 1.35), 2), "phase_transition": round(_clamp(base_phase + 0.1, 0.0, 1.25), 2)}},
                {"weights": {"attribute_transition": round(_clamp(base_attribute + 0.12, 0.0, 1.1), 2), "failure": round(_clamp(base_failure + 0.12, 0.0, 1.2), 2)}},
                {"weights": {"second_order": round(_clamp(base_second - 0.12, 0.0, 1.35), 2), "failure": round(_clamp(base_failure + 0.18, 0.0, 1.2), 2)}},
            ])
        if strategy == "trend":
            candidates.append({
                "window": _clamp(base_window - max(2, delta["window"] // 2), 8, 48),
                "pool": _clamp(base_pool + 1, 8, 22),
                "special_pool": _clamp(base_special, 6, 14),
            })
        if strategy == "balanced":
            base_bucket = list(config.get("bucket_counts") or [2, 2, 2])
            candidates.extend([
                {"bucket_counts": [1, 2, 3], "window": _clamp(base_window + 4, 24, 96), "pool": _clamp(base_pool, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
                {"bucket_counts": [3, 2, 1], "window": _clamp(base_window + 4, 24, 96), "pool": _clamp(base_pool, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
                {"bucket_counts": base_bucket, "window": _clamp(base_window - 4, 24, 96), "pool": _clamp(base_pool + 1, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
            ])
        if strategy == "hybrid":
            base_trend = int(config.get("trend_window") or 15)
            candidates.extend([
                {"trend_window": _clamp(base_trend - delta["trend_window"], 8, 30), "window": _clamp(base_window, 24, 96), "pool": _clamp(base_pool + 1, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
                {"trend_window": _clamp(base_trend + delta["trend_window"], 8, 30), "window": _clamp(base_window + 4, 24, 96), "pool": _clamp(base_pool, 8, 24), "special_pool": _clamp(base_special + 1, 6, 14)},
                {"mix": {"hot": 3, "cold": 1, "trend": 2}, "window": _clamp(base_window, 24, 96), "pool": _clamp(base_pool, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
                {"mix": {"hot": 1, "cold": 2, "trend": 3}, "window": _clamp(base_window, 24, 96), "pool": _clamp(base_pool, 8, 24), "special_pool": _clamp(base_special, 6, 14)},
            ])
    elif strategy == "ml":
        base_history = int(config.get("history_window") or 120)
        base_feature = int(config.get("feature_window") or 60)
        base_eval = int(config.get("evaluation_window") or 30)
        base_pool = int(config.get("pool") or 18)
        base_special = int(config.get("special_pool") or 8)
        base_epochs = int(config.get("epochs") or 18)
        base_lr = float(config.get("learning_rate") or 0.035)
        base_l2 = float(config.get("l2") or 0.0025)
        base_patience = int(config.get("early_stopping_patience") or 4)
        base_validation_floor = float(config.get("validation_floor") or 0.88)
        candidates.extend([
            {
                "history_window": _clamp(base_history - delta["history"], 80, 240),
                "feature_window": _clamp(base_feature - delta["feature"], 30, 90),
                "evaluation_window": _clamp(base_eval - delta["eval"], 12, 60),
                "pool": _clamp(base_pool + 1, 12, 24),
                "special_pool": _clamp(base_special, 6, 12),
                "epochs": _clamp(base_epochs - 2, 12, 30),
                "learning_rate": round(_clamp(base_lr + delta["lr"], 0.01, 0.08), 3),
                "l2": round(_clamp(base_l2 - delta["l2"], 0.001, 0.005), 4),
            },
            {
                "history_window": _clamp(base_history + delta["history"], 80, 240),
                "feature_window": _clamp(base_feature + delta["feature"], 30, 90),
                "evaluation_window": _clamp(base_eval + delta["eval"], 12, 60),
                "pool": _clamp(base_pool, 12, 24),
                "special_pool": _clamp(base_special + 1, 6, 12),
                "epochs": _clamp(base_epochs + 3, 12, 30),
                "learning_rate": round(_clamp(base_lr - delta["lr"], 0.01, 0.08), 3),
                "l2": round(_clamp(base_l2 + delta["l2"], 0.001, 0.005), 4),
            },
            {
                "history_window": _clamp(base_history, 80, 240),
                "feature_window": _clamp(base_feature, 30, 90),
                "evaluation_window": _clamp(base_eval, 12, 60),
                "pool": _clamp(base_pool + 2, 12, 24),
                "special_pool": _clamp(base_special - 1, 6, 12),
                "epochs": _clamp(base_epochs, 12, 30),
                "learning_rate": round(_clamp(base_lr, 0.01, 0.08), 3),
                "l2": round(_clamp(base_l2, 0.001, 0.005), 4),
            },
            {
                "history_window": _clamp(base_history + max(8, delta["history"] // 2), 80, 240),
                "feature_window": _clamp(base_feature, 30, 90),
                "evaluation_window": _clamp(base_eval + max(4, delta["eval"] // 2), 12, 60),
                "epochs": _clamp(base_epochs - 1, 15, 30),
                "learning_rate": round(_clamp(base_lr - delta["lr"] * 0.6, 0.01, 0.08), 3),
                "l2": round(_clamp(base_l2 + delta["l2"] * 1.2, 0.001, 0.006), 4),
                "blend_candidates": [0.45, 0.6, 0.74],
                "early_stopping_patience": _clamp(base_patience - 1, 2, 8),
                "validation_floor": round(_clamp(base_validation_floor + 0.03, 0.72, 0.98), 2),
            },
            {
                "history_window": _clamp(base_history, 80, 240),
                "feature_window": _clamp(base_feature + max(3, delta["feature"] // 2), 30, 90),
                "evaluation_window": _clamp(base_eval, 12, 60),
                "pool": _clamp(base_pool + 1, 12, 24),
                "special_pool": _clamp(base_special + 1, 6, 12),
                "blend_candidates": [0.5, 0.65, 0.78, 0.88],
                "early_stopping_patience": _clamp(base_patience + 1, 2, 8),
                "validation_floor": round(_clamp(base_validation_floor - 0.04, 0.72, 0.98), 2),
            },
        ])
    elif strategy == "ai":
        base_history = int(config.get("history_window") or 12)
        base_temperature = float(config.get("temperature") or 0.35)
        base_sample_count = int(config.get("sample_count") or 3)
        base_candidate_count = int(config.get("candidate_count") or 3)
        base_special_shortlist = int(config.get("special_shortlist") or 8)
        base_normal_shortlist = int(config.get("normal_shortlist") or 18)
        candidates.extend([
            {
                "history_window": _clamp(base_history - max(1, delta["eval"] // 2), 8, 18),
                "temperature": round(_clamp(base_temperature - 0.05, 0.18, 0.45), 2),
                "sample_count": _clamp(base_sample_count + 1, 1, 5),
                "candidate_count": _clamp(base_candidate_count, 2, 5),
                "special_shortlist": _clamp(base_special_shortlist - 1, 6, 10),
                "normal_shortlist": _clamp(base_normal_shortlist - 2, 12, 22),
            },
            {
                "history_window": _clamp(base_history + max(1, delta["eval"] // 2), 8, 18),
                "temperature": round(_clamp(base_temperature + 0.04, 0.18, 0.45), 2),
                "sample_count": _clamp(base_sample_count, 1, 5),
                "candidate_count": _clamp(base_candidate_count + 1, 2, 5),
                "special_shortlist": _clamp(base_special_shortlist + 1, 6, 10),
                "normal_shortlist": _clamp(base_normal_shortlist + 2, 12, 22),
            },
            {
                "history_window": _clamp(base_history, 8, 18),
                "temperature": round(_clamp(base_temperature - 0.02, 0.18, 0.45), 2),
                "sample_count": _clamp(base_sample_count + 1, 1, 5),
                "candidate_count": _clamp(base_candidate_count + 1, 2, 5),
                "special_shortlist": _clamp(base_special_shortlist, 6, 10),
                "normal_shortlist": _clamp(base_normal_shortlist, 12, 22),
            },
        ])

    return _dedupe_auto_optimize_candidates(candidates)


def _build_strategy_backtest_summary(region, strategy, draws=None, config_override=None, min_history=AUTO_BACKTEST_MIN_HISTORY, max_periods=AUTO_OPTIMIZE_BACKTEST_PERIODS):
    if strategy == "ai":
        return {"total": 0, "top1_hit_rate": 0.0, "top6_hit_rate": 0.0, "zodiac_hit_rate": 0.0, "windows": [], "periods_evaluated": 0}

    source_draws = _normalize_backtest_draws(
        draws if draws is not None else _load_backtest_draws_from_db(region, limit=AUTO_BACKTEST_LIMIT),
        limit=AUTO_BACKTEST_LIMIT,
    )
    chronological = list(source_draws or [])
    if len(chronological) <= 1:
        return {"total": 0, "top1_hit_rate": 0.0, "top6_hit_rate": 0.0, "zodiac_hit_rate": 0.0, "windows": []}

    cache_key = (
        str(region or "").strip().lower(),
        str(strategy or "").strip(),
        _runtime_draws_signature(chronological, limit=AUTO_BACKTEST_LIMIT),
        _runtime_json_signature(_load_strategy_config(strategy, region)),
        _runtime_json_signature(config_override or {}),
        int(min_history or 0),
        int(max_periods or 0),
    )
    cached = _runtime_cache_get("strategy_backtest_summary", cache_key)
    if cached is not None:
        return cached

    effective_min_history = min(max(1, int(min_history or 1)), max(1, len(chronological) - 1))
    start_idx = effective_min_history
    if max_periods:
        start_idx = max(effective_min_history, len(chronological) - int(max_periods))

    entries = []
    with _temporary_strategy_config_override(region, strategy, config_override):
        for idx in range(start_idx, len(chronological)):
            target_draw = chronological[idx]
            history_desc = list(reversed(chronological[:idx]))
            try:
                if strategy == "ai":
                    result = predict_with_ai(history_desc, region, config_override=config_override)
                else:
                    with _temporary_backtest_cutoff_period(target_draw.get("id")):
                        with _temporary_strict_backtest_strategy():
                            result = get_local_recommendations(strategy, history_desc, region)
                if result.get("error"):
                    entries.append({
                        "exact_hit": False,
                        "top6_hit": False,
                        "zodiac_hit": False,
                    })
                    continue
            except Exception:
                entries.append({
                    "exact_hit": False,
                    "top6_hit": False,
                    "zodiac_hit": False,
                })
                continue
            entries.append(_evaluate_backtest_prediction(result, target_draw))
    summary = _summarize_backtest_entries(entries)
    summary["periods_evaluated"] = len(entries)
    return _runtime_cache_set("strategy_backtest_summary", cache_key, summary)


def _apply_auto_optimized_config(region, strategy, base_config, override, baseline_summary, best_summary, baseline_score, best_score, source="manual"):
    updated = dict(base_config or {})
    changed_fields = {}
    for key, value in dict(override or {}).items():
        current_value = updated.get(key)
        if isinstance(current_value, dict) and isinstance(value, dict):
            merged_value = {**current_value, **value}
        else:
            merged_value = value
        if current_value == merged_value:
            continue
        changed_fields[key] = {"from": current_value, "to": merged_value}
        updated[key] = merged_value

    if not changed_fields:
        return False, updated

    now_text = datetime.now().isoformat(timespec="seconds")
    gain = round(best_score - baseline_score, 4)
    updated["auto_optimize_last_run_at"] = now_text
    updated["auto_optimize_last_source"] = source
    updated["auto_optimize_last_applied"] = True
    updated["auto_optimize_last_score"] = round(best_score, 4)
    updated["auto_optimize_last_baseline_score"] = round(baseline_score, 4)
    updated["auto_optimize_last_gain"] = gain
    updated["auto_optimize_last_periods"] = int(best_summary.get("periods_evaluated", 0) or 0)
    updated["rollback_guard"] = {
        "active": True,
        "previous_config": _build_config_rollback_snapshot(base_config),
        "baseline_score": round(baseline_score, 4),
        "applied_score": round(best_score, 4),
        "applied_gain": gain,
        "applied_at": now_text,
        "source": source,
        "patience": int(updated.get("rollback_patience") or 3),
        "min_samples": int(updated.get("rollback_min_samples") or 8),
        "drop_tolerance": round(float(updated.get("rollback_drop_tolerance") or 0.8), 4),
        "consecutive_degrade": 0,
        "last_checked_at": "",
    }
    history = list(updated.get("auto_optimize_history") or [])
    history.insert(0, {
        "timestamp": now_text,
        "region": region,
        "strategy": strategy,
        "source": source,
        "gain": gain,
        "baseline_score": round(baseline_score, 4),
        "best_score": round(best_score, 4),
        "baseline_top1": round(_safe_float(baseline_summary.get("top1_hit_rate"), 0.0), 2),
        "best_top1": round(_safe_float(best_summary.get("top1_hit_rate"), 0.0), 2),
        "changed_fields": changed_fields,
    })
    updated["auto_optimize_history"] = history[:8]
    _save_strategy_config(strategy, region, updated)
    return True, updated


def auto_optimize_strategy(region, strategy, draws=None, source="manual"):
    if strategy not in AUTO_OPTIMIZE_STRATEGIES:
        return {"strategy": strategy, "region": region, "updated": False, "reason": "unsupported"}

    config = _load_strategy_config(strategy, region)
    level = _auto_optimize_level()
    min_gain = _auto_optimize_min_gain()
    baseline_summary = _build_strategy_backtest_summary(region, strategy, draws=draws)
    baseline_score = _score_auto_optimize_summary(baseline_summary)
    best_score = baseline_score
    best_summary = baseline_summary
    best_override = None

    for candidate in _build_auto_optimize_candidates(strategy, config, level=level):
        summary = _build_strategy_backtest_summary(region, strategy, draws=draws, config_override=candidate)
        score = _score_auto_optimize_summary(summary)
        if strategy == "markov" and not _passes_markov_window_consistency_gate(baseline_summary, summary, min_gain):
            continue
        if score > best_score:
            best_score = score
            best_summary = summary
            best_override = candidate

    gain = round(best_score - baseline_score, 4)
    if not best_override or gain < min_gain:
        config["auto_optimize_last_run_at"] = datetime.now().isoformat(timespec="seconds")
        config["auto_optimize_last_source"] = source
        config["auto_optimize_last_applied"] = False
        config["auto_optimize_last_score"] = round(best_score, 4)
        config["auto_optimize_last_baseline_score"] = round(baseline_score, 4)
        config["auto_optimize_last_gain"] = gain
        _save_strategy_config(strategy, region, config)
        return {
            "strategy": strategy,
            "region": region,
            "updated": False,
            "gain": gain,
            "baseline_score": round(baseline_score, 4),
            "best_score": round(best_score, 4),
        }

    updated, final_config = _apply_auto_optimized_config(
        region,
        strategy,
        config,
        best_override,
        baseline_summary,
        best_summary,
        baseline_score,
        best_score,
        source=source,
    )
    return {
        "strategy": strategy,
        "region": region,
        "updated": updated,
        "gain": gain,
        "baseline_score": round(baseline_score, 4),
        "best_score": round(best_score, 4),
        "config": final_config,
    }


def auto_optimize_strategy_configs(regions=None, source="manual"):
    if not _auto_optimize_enabled():
        return []

    optimized = []
    for region in (regions or ("hk", "macau")):
        try:
            draws = _load_backtest_draws_from_db(region, limit=AUTO_BACKTEST_LIMIT)
        except Exception as e:
            print(f"Auto optimize skipped for {region}: {e}")
            db.session.rollback()
            continue
        if not draws:
            continue
        for strategy in AUTO_OPTIMIZE_STRATEGIES:
            try:
                optimized.append(auto_optimize_strategy(region, strategy, draws=draws, source=source))
            except Exception as e:
                print(f"Auto optimize failed for {strategy} ({region}): {e}")
                db.session.rollback()
    return optimized


def run_auto_strategy_optimization_job(regions=None, source="scheduler"):
    with app.app_context():
        try:
            results = auto_optimize_strategy_configs(regions=regions, source=source)
            applied = [item for item in results if item.get("updated")]
            print(f"Auto strategy optimization finished: total={len(results)} applied={len(applied)} source={source}")
            return results
        except Exception as e:
            print(f"Auto strategy optimization failed: {e}")
            db.session.rollback()
            return []


def sync_draws_from_api(region, year=None, force=False):
    """从远程接口同步开奖记录并保存到数据库"""
    now = datetime.now()
    if not force and not _is_within_sync_window(now):
        global _last_sync_window_skip_date
        today = now.date()
        if _last_sync_window_skip_date != today:
            print("当前不在开奖同步时间窗内，跳过同步。")
            _last_sync_window_skip_date = today
        return []
    last_sync = _last_draw_sync_times.get(region)
    if not force and last_sync and now - last_sync < _DRAW_SYNC_INTERVAL:
        return []
    if force:
        print(f"Force sync enabled for {region}; bypassing sync window and interval limits.")

    if year is None or str(year).lower() == 'all':
        year_str = str(now.year)
    else:
        year_str = str(year).strip()

    remote_draws = []
    if region == 'hk':
        remote_data = load_hk_data(force_refresh=True)
        remote_draws = [rec for rec in remote_data if rec.get('date', '').startswith(year_str)]
    elif region == 'macau':
        remote_draws = get_macau_data(year_str, force_api=True)
    else:
        return []

    _last_draw_sync_times[region] = now

    if not remote_draws:
        print(f"{region}地区未获取到{year_str}年记录，跳过同步。")
        return []

    print(f"同步{region}地区{year_str}年开奖数据：{len(remote_draws)}条")
    save_draws_to_database(remote_draws, region)
    return remote_draws

@app.route('/api/draws')
def draws_api():
    region = request.args.get('region', 'hk')
    year = request.args.get('year', str(datetime.now().year))
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(int(request.args.get('pageSize', 50)), 100))
    except (TypeError, ValueError):
        page_size = 50
    
    print(f"API请求: 地区={region}, 年份={year}, 页码={page}, 每页数量={page_size}")
    
    # 处理"全部"年份的情况
    if year == 'all':
        year = str(datetime.now().year)
        print(f"年份为'全部'，使用当前年份: {year}")
    
    cache_key = (region, str(year), page, page_size)
    cached = _get_draws_cache(cache_key)
    if cached is not None:
        return jsonify(cached)

    data = _load_draw_page_from_db(region, year, page, page_size)
    if not data and page == 1:
        data = get_yearly_data(region, year)[:page_size]
    print(f"获取到{len(data)}条数据")
    
    # 获取澳门数据，用于提取生肖信息（优先数据库）
    macau_data = _load_draw_page_from_db('macau', year, 1, 80) if region == 'hk' else []
    print(f"获取到{len(macau_data)}条澳门数据用于生肖映射")
    
    # 创建号码到生肖的映射（澳门数据作为兜底）
    fallback_number_to_zodiac = {}
    try:
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        fallback_number_to_zodiac[num] = zodiacs[i]
    except Exception as e:
        print(f"获取澳门生肖映射失败: {e}")

    zodiac_map_cache = {}
    try:
        from models import ZodiacSetting
    except Exception:
        ZodiacSetting = None
    
    if region == 'hk':
        for record in data:
            mapping = fallback_number_to_zodiac
            if ZodiacSetting:
                zodiac_year = ZodiacSetting.get_zodiac_year_for_date(record.get('date'))
                mapping = zodiac_map_cache.get(zodiac_year)
                if mapping is None:
                    mapping = ZodiacSetting.get_all_settings_for_year(zodiac_year) or {}
                    zodiac_map_cache[zodiac_year] = mapping
                if not mapping:
                    mapping = fallback_number_to_zodiac

            normalized_mapping = {str(key): value for key, value in mapping.items()}
            sno = record.get('sno')
            record['sno_zodiac'] = normalized_mapping.get(str(sno), '')
            
            normal_numbers = record.get('no', [])
            normal_zodiacs = []
            for num in normal_numbers:
                normal_zodiacs.append(normalized_mapping.get(str(num), ''))
            record['raw_zodiac'] = ','.join(normal_zodiacs + [normalized_mapping.get(str(sno), '')])
            
            details_breakdown = []
            all_numbers = record.get('no', []) + [record.get('sno')]
            for i, num_str in enumerate(all_numbers):
                if not num_str: 
                    continue
                color_en = _get_hk_number_color(num_str)
                details_breakdown.append({
                    "position": f"平码 {i + 1}" if i < 6 else "特码", "number": num_str,
                    "color_en": color_en, "color_zh": COLOR_MAP_EN_TO_ZH.get(color_en, ''),
                    "zodiac": mapping.get(num_str, '')
                })
            record['details_breakdown'] = details_breakdown
        data = sorted(data, key=lambda x: x.get('date', ''), reverse=True)
        
        # 更新预测准确率
        pass
    else:
        # 更新澳门预测准确率
        pass
    
    # 分页处理
    _set_draws_cache(cache_key, data)
    return jsonify(data)
    
    # 如果是第一页，返回前50条数据，否则返回分页数据
    return jsonify(data)

def update_prediction_accuracy(data, region, trigger_auto_predictions=True, tune_strategy_configs=True):
    """更新预测准确率 - 只比较特码和生肖"""
    try:
        if data:
            _clear_draws_cache(region)

        # 获取所有该地区的预测记录
        predictions = PredictionRecord.query.filter_by(region=region).all()
        
        # 创建期数到开奖结果的映射
        draw_results = {}
        for draw in data:
            period = draw.get('id')
            if not period:
                continue
            
            special_number = str(draw.get('sno', ''))
            # 获取特码生肖 - 所有地区都使用澳门API返回的生肖数据
            special_zodiac = draw.get('sno_zodiac', '')
            
            if special_number:
                draw_results[period] = {
                    'special': special_number,
                    'special_zodiac': special_zodiac
                }
        
        user_hits = {}
        
        # 更新每条预测记录的准确率
        for pred in predictions:
            # 检查是否已经更新过准确率
            if pred.is_result_updated:
                continue
                
            # 查找对应期数的开奖结果
            result = draw_results.get(pred.period)
            if not result:
                continue
                
            # 获取预测特码和生肖
            pred_special = pred.special_number
            pred_zodiac = pred.special_zodiac
            
            # 特码号码是否命中
            special_hit = 1 if pred_special == result['special'] else 0

            # 准确率只按特码是否命中计算
            accuracy = 1.0 if special_hit == 1 else 0.0
            
            # 更新预测记录
            pred.actual_normal_numbers = ''  # 不再需要保存正码
            pred.actual_special_number = result['special']
            pred.actual_special_zodiac = result['special_zodiac']
            pred.accuracy_score = accuracy
            pred.is_result_updated = True
            
            # 如果预测成功（特码命中），收集到待发送列表以便发送合并通知邮件
            if special_hit == 1:
                if pred.user_id not in user_hits:
                    user_hits[pred.user_id] = []
                user_hits[pred.user_id].append(pred)
        
        # 提交更改
        db.session.commit()

        # 统一发送合并后的中奖邮件
        for user_id, hit_preds in user_hits.items():
            try:
                user = User.query.get(user_id)
                if user and user.email:
                    draw_data = next((d for d in data if d.get('id') == hit_preds[0].period), None)
                    send_combined_winning_email(user, hit_preds, region, draw_data)
            except Exception as e:
                print(f"发送合并中奖邮件失败: {e}")

        # 根据最新准确率调整策略参数。开奖同步接口会把这一步放到后台，避免慢回测/AI 调用阻塞请求。
        if tune_strategy_configs:
            update_strategy_configs(region)

        # 仅在原有前台链路中继续触发自动预测；后台更新链会单独处理。
        if trigger_auto_predictions and data and len(data) > 0:
            generate_auto_predictions(data, region)
        
    except Exception as e:
        print(f"更新预测准确率时出错: {e}")
        db.session.rollback()

def generate_auto_predictions(data, region):
    """为每期自动生成预测（排除 AI 策略）"""
    try:
        latest_draw = data[0] if data else None
        if not latest_draw:
            return

        latest_period = latest_draw.get('id', '')
        next_period = _get_next_period(region, latest_period)

        if not next_period:
            print("自动预测失败：无法确定下一期期数")
            return

        raw_users = User.query.filter(
            User.is_active == True,
            User.auto_prediction_enabled == True
        ).all()

        auto_predict_users = []
        changed = False
        for u in raw_users:
            if u.is_activation_expired():
                u.is_active = False
                u.auto_prediction_enabled = False
                changed = True
            else:
                auto_predict_users.append(u)
        
        if changed:
            db.session.commit()

        for user in auto_predict_users:
            strategies = user.auto_prediction_strategies.split(',') if user.auto_prediction_strategies else list(LOCAL_STRATEGY_KEYS)
            strategies = [strategy for strategy in strategies if strategy in LOCAL_STRATEGY_KEYS]
            if not strategies:
                strategies = list(LOCAL_STRATEGY_KEYS)
            regions = user.auto_prediction_regions.split(',') if hasattr(user, 'auto_prediction_regions') and user.auto_prediction_regions else ['hk', 'macau']

            if region not in regions:
                continue

            user_predictions = []
            has_new_predictions = False
            seen_strategies = set()

            for strategy in strategies:
                if strategy == 'ai':
                    continue
                resolved_strategy = strategy

                if resolved_strategy in seen_strategies:
                    continue
                seen_strategies.add(resolved_strategy)

                existing = PredictionRecord.query.filter_by(
                    user_id=user.id,
                    region=region,
                    period=next_period,
                    strategy=resolved_strategy
                ).first()

                if not existing:
                    pred = generate_prediction_for_user(user, region, next_period, resolved_strategy, data)
                    if pred:
                        user_predictions.append(pred)
                        has_new_predictions = True
                else:
                    user_predictions.append(existing)
            
            # 如果生成了全新的预测，合并发送一封汇总邮件
            if has_new_predictions and user.email:
                try:
                    send_combined_prediction_email(user, user_predictions, region, next_period, latest_draw)
                except Exception as e:
                    print(f"自动发送合并预测邮件给 {user.username} 失败：{e}")
    except Exception as e:
        print(f"自动预测出错：{e}")
        db.session.rollback()

def generate_prediction_for_user(user, region, period, strategy, data):
    """为指定用户生成预测（排除 AI 策略）"""
    try:
        if strategy == 'ai':
            print(f"已跳过用户 {user.username} 的AI自动预测")
            return None

        variation_key = None
        if _personalized_predictions_enabled():
            variation_key = f"user:{user.id}|region:{region}|period:{period}|strategy:{strategy}"
        result = get_local_recommendations(strategy, data, region, variation_key=variation_key)
        result = _ensure_period_unique_special(
            result,
            strategy,
            region,
            period,
            user_id=user.id,
            prediction_zodiac_year=_infer_draw_year(data),
        )

        if result.get('error'):
            print(f"用户 {user.username} 的自动预测失败：{result.get('error')}")
            return None

        prediction = PredictionRecord(
            user_id=user.id,
            region=region,
            strategy=strategy,
            period=period,
            normal_numbers=','.join(map(str, result.get('normal', []))),
            special_number=str(result.get('special', {}).get('number', '')),
            special_zodiac=result.get('special', {}).get('sno_zodiac', ''),
            prediction_metadata=_serialize_prediction_metadata(result.get('model_meta')),
            prediction_text=result.get('recommendation_text', '')
        )
        db.session.add(prediction)
        db.session.commit()
        print(f"自动预测成功：为用户 {user.username} 的{region}地区第{period}期生成了{strategy}策略的预测")
        return prediction
    except Exception as e:
        duplicate_hint = str(e).lower()
        if 'unique' in duplicate_hint or 'duplicate' in duplicate_hint:
            db.session.rollback()
            existing = (
                PredictionRecord.query.filter_by(
                    user_id=user.id,
                    region=region,
                    period=period,
                    strategy=strategy
                )
                .order_by(PredictionRecord.id.desc())
                .first()
            )
            if existing:
                print(f"检测到自动预测重复记录，已复用现有记录：user={user.username}, region={region}, period={period}, strategy={strategy}")
                return existing
        print(f"为用户 {user.username} 生成预测时出错：{e}")
        db.session.rollback()
        return None

@app.route('/api/predict')
def unified_predict_api():
    user, auth_error = _require_active_session_json()
    if auth_error:
        return auth_error

    # 预测按当前农历生肖年取数；生肖映射仍由 /api/get_zodiacs 单独处理。
    region, strategy, year = request.args.get('region', 'hk'), request.args.get('strategy', 'balanced'), request.args.get('year', str(datetime.now().year))
    stream_response = request.args.get('stream') == '1'

    def _sse_event(payload):
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    data, prediction_zodiac_year = _get_prediction_data(region, year)
    if not data: return jsonify({"error": f"无法加载{year}年的数据"}), 404
    resolved_strategy = strategy
    
    # 检查用户是否登录和激活（对于需要保存记录的功能）
    user_id = user.id
    is_active = True
    
    # 获取下一期期数（使用最近一期的下一期）
    if data:
        try:
            latest_period = data[0].get('id', '')
            current_period = _get_next_period(region, latest_period)
        except (IndexError, ValueError) as e:
            print(f"计算下一期期数时出错: {e}")
            current_period = _default_period(region)
    else:
        current_period = _default_period(region)
    
    # 检查用户是否已经为当前期和当前策略生成过预测
    if user_id and is_active:
        existing = PredictionRecord.query.filter_by(
            user_id=user_id,
            region=region,
            period=current_period,
            strategy=resolved_strategy  # 添加策略作为过滤条件
        ).first()
        
        if existing:
            # 返回已存在的预测结果
            sno_zodiac = existing.special_zodiac
            # 不再在本地计算生肖，所有地区都使用澳门API返回的生肖数据
            
            result = {
                "normal": existing.normal_numbers.split(','),
                "special": {
                    "number": existing.special_number,
                    "sno_zodiac": sno_zodiac
                }
            }
            existing_meta = _deserialize_prediction_metadata(
                getattr(existing, "prediction_metadata", "")
            )
            refreshed_text = _hydrate_prediction_recommendation_text(
                resolved_strategy,
                existing.prediction_text,
                data,
                region,
                special_number=existing.special_number,
                normal_numbers=existing.normal_numbers,
                existing_meta=existing_meta,
            )
            if refreshed_text:
                result["recommendation_text"] = refreshed_text
            result["model_meta"] = _hydrate_prediction_model_meta(
                resolved_strategy,
                existing_meta,
                data,
                region,
            )
            result = _ensure_period_unique_special(
                result,
                resolved_strategy,
                region,
                current_period,
                user_id=user_id,
                prediction_zodiac_year=prediction_zodiac_year,
            )
            adjusted_special = str((result.get("special") or {}).get("number") or "").strip()
            original_special = str(existing.special_number or "").strip()
            if adjusted_special and adjusted_special != original_special:
                refreshed_text = _refresh_special_recommendation_text(
                    resolved_strategy,
                    result.get("recommendation_text", ""),
                    adjusted_special,
                    result.get("normal", []),
                    region=region,
                )
                if refreshed_text:
                    result["recommendation_text"] = refreshed_text
                result["model_meta"] = dict(result.get("model_meta") or {})
                existing.special_number = adjusted_special
                existing.special_zodiac = (result.get("special") or {}).get("sno_zodiac", "")
                existing.prediction_metadata = _serialize_prediction_metadata(result.get("model_meta"))
                existing.prediction_text = _decorate_recommendation_text(
                    strategy,
                    resolved_strategy,
                    result.get("recommendation_text", ""),
                )
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            result["strategy"] = resolved_strategy
            result["requested_strategy"] = strategy
            result["prediction_zodiac_year"] = prediction_zodiac_year
            result = _attach_prediction_display_copy(result, strategy, resolved_strategy)
            if stream_response and resolved_strategy == 'ai':
                payload = {
                    "type": "done",
                    "region": region,
                    "strategy": resolved_strategy,
                    "requested_strategy": strategy,
                    "period": current_period,
                    "saved": True,
                    **result
                }
                def generate_existing():
                    yield _sse_event(payload)
                return Response(
                    stream_with_context(generate_existing()),
                    mimetype='text/event-stream',
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
                )
            return jsonify(result)
    
    # 生成新的预测
    if resolved_strategy == 'ai':
        if stream_response:
            def generate_stream():
                ai_config = get_ai_config()
                if not ai_config['api_key'] or "你的" in ai_config['api_key']:
                    yield _sse_event({"type": "error", "error": "AI API Key 未配置"})
                    return
                tuned = _load_strategy_config("ai", region)
                history_window = int(tuned.get("history_window") or 12)
                temperature = float(tuned.get("temperature") or 0.35)
                sample_count = _clamp(int(tuned.get("sample_count") or 3), 1, 5)
                candidate_count = _clamp(int(tuned.get("candidate_count") or 3), 2, 5)
                shortlist_context = _build_ai_shortlist_context(data, region, config=tuned)
                gate_profile = dict(shortlist_context.get("gate_profile") or {})
                prompt = _build_ai_prompt_v4(
                    data,
                    region,
                    shortlist_context,
                    history_window=history_window,
                    candidate_count=candidate_count,
                )
                full_text = ""
                try:
                    for chunk in _iter_ai_stream(ai_config, prompt, temperature=temperature):
                        full_text += chunk
                        yield _sse_event({
                            "type": "content",
                            "content": chunk,
                            "full_text": full_text
                        })
                except Exception as e:
                    yield _sse_event({"type": "error", "error": f"调用AI API时出错: {e}"})
                    return

                yield _sse_event({
                    "type": "status",
                    "stage": "postprocess",
                    "message": "模型输出完成，正在整理最终候选..."
                })

                try:
                    result = _finalize_ai_prediction_result(
                        data,
                        region,
                        full_text=full_text,
                        ai_config=ai_config,
                        prompt=prompt,
                        temperature=temperature,
                        sample_count=sample_count,
                        candidate_count=candidate_count,
                        shortlist_context=shortlist_context,
                        tuned=tuned,
                        stream_mode=True,
                    )
                except Exception as e:
                    yield _sse_event({"type": "error", "error": f"AI预测处理失败: {e}"})
                    return
                if result.get("error"):
                    yield _sse_event({"type": "error", "error": result.get("error")})
                    return

                yield _sse_event({
                    "type": "status",
                    "stage": "finalize",
                    "message": "候选已整理完成，正在生成最终结果..."
                })

                try:
                    if user_id and is_active:
                        result = _ensure_period_unique_special(
                            result,
                            resolved_strategy,
                            region,
                            current_period,
                            user_id=user_id,
                            prediction_zodiac_year=prediction_zodiac_year,
                        )

                    result.update({
                        "type": "done",
                        "region": region,
                        "strategy": resolved_strategy,
                        "requested_strategy": strategy,
                        "period": current_period,
                        "prediction_zodiac_year": prediction_zodiac_year,
                    })
                    result = _attach_prediction_display_copy(result, strategy, resolved_strategy)

                    if user_id and is_active:
                        try:
                            yield _sse_event({
                                "type": "status",
                                "stage": "save",
                                "message": "正在保存预测记录..."
                            })
                            prediction = PredictionRecord(
                                user_id=user_id,
                                region=region,
                                strategy=resolved_strategy,
                                period=current_period,
                                normal_numbers=','.join(map(str, result.get('normal', []))),
                                special_number=str(result.get('special', {}).get('number', '')),
                                special_zodiac=result.get('special', {}).get('sno_zodiac', ''),
                                prediction_metadata=_serialize_prediction_metadata(result.get('model_meta')),
                                prediction_text=_decorate_recommendation_text(
                                    strategy,
                                    resolved_strategy,
                                    result.get('recommendation_text', '')
                                )
                            )
                            db.session.add(prediction)
                            db.session.commit()
                            result["saved"] = True
                        except Exception as e:
                            db.session.rollback()
                            result["saved"] = False
                            result["save_error"] = str(e)

                    yield _sse_event(result)
                except Exception as e:
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    yield _sse_event({"type": "error", "error": f"AI结果收尾失败: {e}"})
                    return

            return Response(
                stream_with_context(generate_stream()),
                mimetype='text/event-stream',
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )

        result = predict_with_ai(data, region)
        # 检查AI预测是否失败
        if result.get('error'):
            # 返回详细的错误信息
            error_message = result.get('error')
            return jsonify({
                "error": error_message,
                "error_type": "ai_prediction_failed",
                "message": f"AI预测失败：{error_message}，请稍后再试或联系管理员检查AI API配置。"
            }), 400
        if user_id and is_active:
            result = _ensure_period_unique_special(
                result,
                resolved_strategy,
                region,
                current_period,
                user_id=user_id,
                prediction_zodiac_year=prediction_zodiac_year,
            )
    else:
        variation_key = None
        if user_id and is_active and _personalized_predictions_enabled():
            variation_key = f"user:{user_id}|region:{region}|period:{current_period}|strategy:{resolved_strategy}"
        try:
            result = get_local_recommendations(resolved_strategy, data, region, variation_key=variation_key)
            if user_id and is_active:
                result = _ensure_period_unique_special(
                    result,
                    resolved_strategy,
                    region,
                    current_period,
                    user_id=user_id,
                    prediction_zodiac_year=prediction_zodiac_year,
                )
        except Exception as e:
            print(f"Prediction failed for region={region}, strategy={resolved_strategy}, year={year}: {e}")
            return jsonify({
                "error": f"预测失败：{str(e)}",
                "error_type": "prediction_failed",
            }), 500
    
    # 保存预测记录（仅对已激活用户）
    if user_id and is_active and not result.get('error'):
        try:
            prediction = PredictionRecord(
                user_id=user_id,
                region=region,
                strategy=resolved_strategy,
                period=current_period,
                normal_numbers=','.join(map(str, result.get('normal', []))),
                special_number=str(result.get('special', {}).get('number', '')),
                special_zodiac=result.get('special', {}).get('sno_zodiac', ''),
                prediction_metadata=_serialize_prediction_metadata(result.get('model_meta')),
                prediction_text=_decorate_recommendation_text(
                    strategy,
                    resolved_strategy,
                    result.get('recommendation_text', '')
                )
            )
            db.session.add(prediction)
            db.session.commit()
        except Exception as e:
            duplicate_hint = str(e).lower()
            if 'unique' in duplicate_hint or 'duplicate' in duplicate_hint:
                db.session.rollback()
                existing = (
                    PredictionRecord.query.filter_by(
                        user_id=user_id,
                        region=region,
                        period=current_period,
                        strategy=resolved_strategy
                    )
                    .order_by(PredictionRecord.id.desc())
                    .first()
                )
                if existing:
                    result["saved"] = True
                    result["duplicate_ignored"] = True
                    result["normal"] = existing.normal_numbers.split(',')
                    result["special"] = {
                        "number": existing.special_number,
                        "sno_zodiac": existing.special_zodiac,
                    }
                    existing_meta = _deserialize_prediction_metadata(
                        getattr(existing, "prediction_metadata", "")
                    )
                    result["model_meta"] = _hydrate_prediction_model_meta(
                        resolved_strategy,
                        existing_meta,
                        data,
                        region,
                    )
                    refreshed_text = _hydrate_prediction_recommendation_text(
                        resolved_strategy,
                        existing.prediction_text,
                        data,
                        region,
                        special_number=existing.special_number,
                        normal_numbers=existing.normal_numbers,
                        existing_meta=existing_meta,
                    )
                    if refreshed_text:
                        result["recommendation_text"] = refreshed_text
                    print(f"检测到接口重复预测记录，已返回现有记录：user={user_id}, region={region}, period={current_period}, strategy={resolved_strategy}")
                    result = _attach_prediction_display_copy(result, strategy, resolved_strategy)
                    return jsonify(result)
            db.session.rollback()
            print(f"保存预测记录失败: {e}")
            return jsonify({
                "error": str(e),
                "error_type": "database_error",
                "message": "保存预测记录失败，请稍后再试。"
            }), 500
    
    result["strategy"] = resolved_strategy
    result["requested_strategy"] = strategy
    result["prediction_zodiac_year"] = prediction_zodiac_year
    result = _attach_prediction_display_copy(result, strategy, resolved_strategy)
    return jsonify(result)

def _log_draw_update(message, source="manual", region=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    region_text = f" region={region}" if region else ""
    log_line = f"[DRAW-UPDATE][{timestamp}][{source}]{region_text} {message}"
    print(log_line)


def _run_draw_postprocess_for_region(region, current_year, source="manual-postprocess"):
    with app.app_context():
        try:
            prediction_data, _ = _get_prediction_data(region, current_year)
            if not prediction_data:
                _log_draw_update("未获取到可用于自动预测的数据", source=source, region=region)
                return

            _log_draw_update(f"开始生成自动预测 draw_count={len(prediction_data)}", source=source, region=region)
            generate_auto_predictions(prediction_data, region)
            _log_draw_update("自动预测已完成", source=source, region=region)
            _log_draw_update("开始刷新本地策略学习参数", source=source, region=region)
            tuning_started_at = time.time()
            update_strategy_configs(region, strategies=POSTPROCESS_TUNING_STRATEGIES)
            tuning_elapsed = round(time.time() - tuning_started_at, 2)
            _log_draw_update(f"本地策略学习参数已刷新 elapsed={tuning_elapsed}s", source=source, region=region)
            _log_draw_update("开始刷新回测快照", source=source, region=region)
            backtest_started_at = time.time()
            refresh_auto_backtest_snapshot(
                region,
                draws=prediction_data,
                force=True,
                strategies=POSTPROCESS_BACKTEST_STRATEGIES,
                limit=POSTPROCESS_BACKTEST_LIMIT,
            )
            backtest_elapsed = round(time.time() - backtest_started_at, 2)
            _log_draw_update(f"回测快照已完成 elapsed={backtest_elapsed}s", source=source, region=region)
        except Exception as e:
            _log_draw_update(f"自动预测/回测失败 error={e}", source=source, region=region)
            import traceback
            traceback.print_exc()


def _start_draw_postprocess_async(regions, current_year, source="manual-postprocess"):
    normalized_regions = tuple(region for region in (regions or []) if region in ("hk", "macau"))
    if not normalized_regions:
        return False

    _log_draw_update(f"后台后处理任务已启动 year={current_year}", source=source, region=",".join(normalized_regions))
    for region in normalized_regions:
        def _runner(target_region=region):
            _log_draw_update("后台后处理分区任务开始", source=source, region=target_region)
            _run_draw_postprocess_for_region(target_region, current_year, source=source)
            _log_draw_update("后台后处理分区任务完成", source=source, region=target_region)

        threading.Thread(
            target=_runner,
            name=f"draw-postprocess-{source}-{region}-{current_year}",
            daemon=True,
        ).start()
    return True


@app.route('/api/update_data', methods=['POST'])
def update_data_api():
    _, auth_error = _require_admin_session_json()
    if auth_error:
        return auth_error

    payload = request.get_json(silent=True) or {}
    region = payload.get('region', 'all')
    try:
        current_year = str(datetime.now().year)
        _log_draw_update(f"开始手动更新开奖数据 current_year={current_year}", source="manual", region=region)
        updated_regions = []
        updated_region_keys = []
        failed_regions = []

        if region == 'all' or region == 'hk':
            try:
                _log_draw_update("开始拉取香港开奖数据", source="manual", region="hk")
                hk_data = load_hk_data(force_refresh=True)
                hk_filtered = [rec for rec in hk_data if rec.get('date', '').startswith(current_year)]
                save_draws_to_database(hk_filtered, 'hk')
                updated_regions.append(f"香港{len(hk_filtered)}条")
                updated_region_keys.append("hk")
                _log_draw_update(f"香港开奖数据已保存 count={len(hk_filtered)}", source="manual", region="hk")
                update_prediction_accuracy(hk_filtered, 'hk', trigger_auto_predictions=False, tune_strategy_configs=False)
                _log_draw_update("香港预测结算已完成", source="manual", region="hk")
                update_hk_next_draw_time_cache(force=True)
                _log_draw_update("香港下期时间缓存已刷新", source="manual", region="hk")
            except Exception as region_error:
                failed_regions.append(f"香港: {region_error}")
                _log_draw_update(f"香港开奖更新失败 error={region_error}", source="manual", region="hk")

        if region == 'all' or region == 'macau':
            try:
                _log_draw_update("开始拉取澳门开奖数据", source="manual", region="macau")
                macau_data = get_macau_data(current_year, force_api=True)
                save_draws_to_database(macau_data, 'macau')
                updated_regions.append(f"澳门{len(macau_data)}条")
                updated_region_keys.append("macau")
                _log_draw_update(f"澳门开奖数据已保存 count={len(macau_data)}", source="manual", region="macau")
                update_prediction_accuracy(macau_data, 'macau', trigger_auto_predictions=False, tune_strategy_configs=False)
                _log_draw_update("澳门预测结算已完成", source="manual", region="macau")
            except Exception as region_error:
                failed_regions.append(f"澳门: {region_error}")
                _log_draw_update(f"澳门开奖更新失败 error={region_error}", source="manual", region="macau")

        if updated_regions:
            message = f"开奖数据更新完成：{'，'.join(updated_regions)}"
            if failed_regions:
                message += f"；未完成：{'；'.join(failed_regions)}"
            _start_draw_postprocess_async(updated_region_keys, current_year, source="manual-postprocess")
            _log_draw_update(message, source="manual", region=region)
            return jsonify({
                "success": True,
                "message": message
            })

        message = "更新失败"
        if failed_regions:
            message = f"{message}：{'；'.join(failed_regions)}"
        _log_draw_update(message, source="manual", region=region)
        return jsonify({
            "success": False,
            "message": message
        }), 500
    except Exception as e:
        _log_draw_update(f"手动更新开奖数据失败 error={e}", source="manual", region=region)
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": f"更新失败: {str(e)}"
        }), 500

@app.route('/api/number_frequency')
def number_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data, _ = _get_prediction_data(region, year)
    return jsonify(analyze_special_number_frequency(data))

@app.route('/api/special_zodiac_frequency')
def special_zodiac_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data, prediction_zodiac_year = _get_prediction_data(region, year)
    return jsonify(analyze_special_zodiac_frequency(data, region, prediction_zodiac_year))

@app.route('/api/special_color_frequency')
def special_color_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data, _ = _get_prediction_data(region, year)
    return jsonify(analyze_special_color_frequency(data, region))

@app.route('/api/get_zodiacs')
def get_zodiacs_api():
    numbers = request.args.get('numbers', '').split(',')
    if not numbers or not numbers[0]:
        return jsonify({'normal_zodiacs': [], 'special_zodiac': ''})
    
    # 获取生肖年份（按农历新年切换）
    from models import ZodiacSetting
    zodiac_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
    number_to_zodiac = {}
    
    try:
        # 使用ZodiacSetting模型获取生肖设置
        for number in range(1, 50):
            zodiac = ZodiacSetting.get_zodiac_for_number(zodiac_year, number)
            if zodiac:
                number_to_zodiac[str(number)] = zodiac
    except Exception as e:
        print(f"获取生肖设置失败: {e}")
        # 如果出错，使用澳门API返回的生肖数据
        macau_data = get_macau_data(str(zodiac_year))
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        number_to_zodiac[num] = zodiacs[i]
    
    # 获取每个号码对应的生肖
    normal_zodiacs = []
    for num in numbers[:-1]:  # 除了最后一个数字（特码）
        normal_zodiacs.append(number_to_zodiac.get(num, ''))
    
    # 获取特码生肖
    special_zodiac = number_to_zodiac.get(numbers[-1], '') if len(numbers) > 0 else ''
    
    return jsonify({
        'normal_zodiacs': normal_zodiacs,
        'special_zodiac': special_zodiac
    })

@app.route('/api/search_draws')
def search_draws_api():
    region, year, term = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year)), request.args.get('term', '').strip().lower()
    if not term: return jsonify([])
    data, results = get_yearly_data(region, year), []
    number_to_zodiac = _get_number_to_zodiac_map(year) if region == 'hk' else {}
    for record in data:
        if region == 'hk':
            sno_zodiac_display = number_to_zodiac.get(str(record.get('sno', '')), record.get('sno_zodiac', ''))
        else:
            sno_zodiac_display = record.get('sno_zodiac', '')
        if term == record.get('sno', '') or term in sno_zodiac_display.lower():
            if 'details_breakdown' not in record and region == 'hk':
                 record['sno_zodiac'] = sno_zodiac_display
            results.append(record)
    return jsonify(results[:20])

@app.route('/chat')
def chat_page():
    # 检查用户登录状态
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    from models import ZodiacSetting
    current_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
    
    # 创建号码到生肖的映射
    number_to_zodiac = {}
    
    # 首先尝试从ZodiacSetting获取当前年份的生肖设置
    try:
        zodiac_settings = ZodiacSetting.get_all_settings_for_year(current_year)
        
        if zodiac_settings:
            # 使用数据库中的生肖设置
            for number, zodiac in zodiac_settings.items():
                number_to_zodiac[str(number)] = zodiac
        else:
            # 如果数据库中没有设置，则使用澳门API返回的生肖数据
            macau_data = get_macau_data(str(current_year))
            for record in macau_data:
                all_numbers = record.get('no', []) + [record.get('sno')]
                zodiacs = record.get('raw_zodiac', '').split(',')
                if len(all_numbers) == len(zodiacs):
                    for i, num in enumerate(all_numbers):
                        if num:
                            number_to_zodiac[num] = zodiacs[i]
    except Exception as e:
        print(f"获取生肖设置失败，使用澳门API数据: {e}")
        # 如果出错，使用澳门API返回的生肖数据
        macau_data = get_macau_data(str(current_year))
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        number_to_zodiac[num] = zodiacs[i]
    
    hk_all_yearly_data = get_yearly_data('hk', current_year)
    hk_data_sorted = sorted(hk_all_yearly_data, key=lambda x: x.get('date', ''), reverse=True)
    hk_latest_10 = hk_data_sorted[:10]
    for record in hk_latest_10:
        # 使用澳门的生肖对应关系
        sno = record.get('sno')
        record['sno_zodiac'] = number_to_zodiac.get(sno, '')

    macau_latest_10 = get_yearly_data('macau', current_year)[:10]

    ball_colors = {
        'red': RED_BALLS,
        'blue': BLUE_BALLS,
        'green': GREEN_BALLS
    }

    return render_template('chat.html', 
                           hk_results=hk_latest_10, 
                           macau_results=macau_latest_10,
                           ball_colors=json.dumps(ball_colors))

@app.route('/api/chat', methods=['POST'])
def handle_chat():
    _, auth_error = _require_active_session_json()
    if auth_error:
        return auth_error

    ai_config = get_ai_config()
    if not ai_config['api_key'] or "你的" in ai_config['api_key']:
        return jsonify({"reply": "错误：管理员尚未配置AI API Key，无法使用聊天功能。"}), 400
    user_message = (request.get_json(silent=True) or {}).get("message")
    if not user_message:
        return jsonify({"reply": "错误：未能获取到您发送的消息。"}), 400
    system_prompt = "你是一个精通香港和澳门六合彩数据分析的AI助手，知识渊博，回答友好。请根据用户的提问，提供相关的历史知识、数据规律或普遍性建议。不要提供具体的投资建议。"
    payload = {"model": ai_config['model'], "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], "temperature": 0.7}
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    try:
        response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
            response.encoding = "utf-8"
        ai_reply = response.json()['choices'][0]['message']['content']
        return jsonify({"reply": ai_reply})
    except Exception as e:
        print(f"Error calling AI chat API: {e}")
        return jsonify({"reply": f"抱歉，调用AI时遇到错误，请稍后再试。"}), 500

def send_winning_notification_email(user, prediction, region):
    """发送预测命中通知邮件"""
    site_name = SystemConfig.get_config('site_name', 'AI数据分析预测系统')
    region_name = '香港' if region == 'hk' else '澳门'
    strategy_name = _get_email_strategy_display(prediction)
    
    subject = f"恭喜您！{region_name}第{prediction.period}期特码预测命中"
    text_content = (
        f"尊敬的 {user.username}：\n"
        f"恭喜您！您使用{strategy_name}对{region_name}六合彩第{prediction.period}期的特码预测已经命中。\n"
        f"预测期数：{prediction.period}\n"
        f"预测策略：{strategy_name}\n"
        f"预测特码：{prediction.special_number}\n"
        f"开奖特码：{prediction.actual_special_number}\n"
        f"预测时间：{prediction.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    # 构建HTML邮件内容
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #4CAF50; color: white; padding: 10px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9f9f9; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #777; }}
            .highlight {{ color: #e53935; font-weight: bold; }}
            .info-row {{ margin-bottom: 10px; }}
            .btn {{ display: inline-block; background-color: #4CAF50; color: white; padding: 10px 20px; 
                   text-decoration: none; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>恭喜您！预测命中通知</h2>
            </div>
            <div class="content">
                <p>尊敬的 <strong>{user.username}</strong>：</p>
                <p>恭喜您！您使用<strong>{strategy_name}</strong>对{region_name}六合彩第{prediction.period}期的特码预测已经<span class="highlight">命中</span>！</p>
                
                <div class="info-row"><strong>预测期数：</strong> {prediction.period}</div>
                <div class="info-row"><strong>预测策略：</strong> {strategy_name}</div>
                <div class="info-row"><strong>预测特码：</strong> <span class="highlight">{prediction.special_number}</span></div>
                <div class="info-row"><strong>开奖特码：</strong> <span class="highlight">{prediction.actual_special_number}</span></div>
                <div class="info-row"><strong>预测时间：</strong> {prediction.created_at.strftime('%Y-%m-%d %H:%M:%S')}</div>
                
                <p>您可以登录系统查看更多预测详情和历史记录。</p>
                <p style="text-align: center; margin-top: 20px;">
                    <a href="#" class="btn">查看详情</a>
                </p>
            </div>
            <div class="footer">
                <p>此邮件由系统自动发送，请勿回复。</p>
                <p>© {datetime.now().year} {site_name} - 所有权利保留</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    notify_user(
        user,
        subject,
        text_content,
        html_content=html_content,
        event_type='winning',
        email_subject=subject,
    )
    print(f"成功发送预测命中通知给用户 {user.username} ({user.email})")

def send_combined_prediction_email(user, predictions, region, period, latest_draw=None):
    """发送新一期多策略预测合并的汇总推送邮件"""
    site_name = SystemConfig.get_config('site_name', 'AI数据分析预测系统')
    show_normal_numbers = bool(getattr(user, 'show_normal_numbers', False))
    
    region_name = '香港' if region == 'hk' else '澳门'
    subject = f"{site_name} - {region_name}六合彩第{period}期预测汇总已生成"
    
    cards_html = ""
    text_rows = []
    for pred in predictions:
        strategy_name = _get_email_strategy_display(pred)
        if show_normal_numbers:
            text_rows.append(
                f"{strategy_name}: 平码 {pred.normal_numbers}; 特码 {pred.special_number} ({pred.special_zodiac})"
            )
        else:
            text_rows.append(
                f"{strategy_name}: 特码 {pred.special_number} ({pred.special_zodiac})"
            )
        cards_html += _prediction_notice_card_html(
            strategy_name,
            pred.normal_numbers,
            pred.special_number,
            pred.special_zodiac,
            show_normal_numbers=show_normal_numbers,
        )
        
    latest_draw_html = ""
    if latest_draw:
        draw_period = latest_draw.get('id', '')
        special_num = latest_draw.get('sno', '')
        special_zodiac = latest_draw.get('sno_zodiac', '')
        draw_zodiacs = _prediction_notice_zodiac_list(latest_draw.get('raw_zodiac', ''))
        if len(draw_zodiacs) >= 7:
            special_zodiac = draw_zodiacs[6] or special_zodiac
        latest_normal_html, latest_special_html = _prediction_notice_balls_html(
            latest_draw.get('no', []),
            special_num,
            special_zodiac,
            normal_zodiacs=draw_zodiacs[:6],
        )
        latest_draw_html = f'''
        <div style="background-color: #eff6ff; padding: 14px; border-radius: 8px; margin-bottom: 16px; border-left: 4px solid #2563eb;">
            <h3 style="margin-top: 0; color: #0d47a1; font-size: 16px; margin-bottom: 8px;">上期 ({draw_period}期) 开奖结果</h3>
            {_prediction_notice_number_table_html(latest_normal_html, latest_special_html)}
        </div>
        '''

    text_content = (
        f"尊敬的 {user.username}：\n"
        f"系统已为您生成{region_name}六合彩第{period}期预测汇总。\n"
        + "\n".join(text_rows)
    )

    html_content = _prediction_notice_wrapper_html(
        f"第 {period} 期预测汇总",
        f"{region_name}六合彩最新推荐",
        f'''
        <p style="font-size:16px;margin-top:0;">尊敬的 <strong>{escape(user.username)}</strong>：</p>
        {latest_draw_html}
        <p style="color:#64748b;">系统已为您自动生成本期预测，号码已标注生肖和红绿蓝波属性。</p>
        {cards_html}
        ''',
        footer_note=f"此通知由 {site_name} 自动生成并发送，请勿回复。",
        tone='blue',
    )
    
    notify_user(
        user,
        subject,
        text_content,
        html_content=html_content,
        event_type='prediction_generated',
        email_subject=subject,
    )

def send_combined_winning_email(user, predictions, region, draw_data=None):
    """发送合并后的特码命中通知邮件（如果有多个策略同时命中）"""
    site_name = SystemConfig.get_config('site_name', 'AI数据分析预测系统')
    show_normal_numbers = bool(getattr(user, 'show_normal_numbers', False))
    
    region_name = '香港' if region == 'hk' else '澳门'
    period = predictions[0].period
    subject = f"恭喜您！{region_name}第{period}期特码预测命中"
    
    cards_html = ""
    text_rows = []
    for pred in predictions:
        strategy_name = _get_email_strategy_display(pred)
        text_rows.append(f"{strategy_name}: 命中特码 {pred.special_number} ({pred.special_zodiac})")
        cards_html += _prediction_notice_card_html(
            strategy_name,
            pred.normal_numbers,
            pred.special_number,
            pred.special_zodiac,
            accent='#16a34a',
            show_normal_numbers=show_normal_numbers,
        )
        
    draw_html = ""
    if draw_data:
        draw_zodiacs = _prediction_notice_zodiac_list(draw_data.get('raw_zodiac', ''))
        draw_special_zodiac = draw_data.get('sno_zodiac', '')
        if len(draw_zodiacs) >= 7:
            draw_special_zodiac = draw_zodiacs[6] or draw_special_zodiac
        draw_normal_html, draw_special_html = _prediction_notice_balls_html(
            draw_data.get('no', []),
            draw_data.get('sno', ''),
            draw_special_zodiac,
            normal_zodiacs=draw_zodiacs[:6],
        )
        draw_html = f'''
        <div style="background-color: #fefce8; padding: 14px; border-radius: 8px; margin-bottom: 16px; border-left: 4px solid #facc15;">
            <h3 style="margin-top: 0; color: #f57f17; font-size: 16px; margin-bottom: 8px;">第 {period} 期 完整开奖结果</h3>
            {_prediction_notice_number_table_html(draw_normal_html, draw_special_html)}
        </div>
        '''

    text_content = (
        f"尊敬的 {user.username}：\n"
        f"恭喜您！您定制的{region_name}六合彩第{period}期预测命中特码 "
        f"{predictions[0].actual_special_number} ({predictions[0].actual_special_zodiac})。\n"
        + "\n".join(text_rows)
    )

    html_content = _prediction_notice_wrapper_html(
        "预测命中通知",
        f"{region_name}第 {period} 期",
        f'''
        <p style="font-size:16px;margin-top:0;">尊敬的 <strong>{escape(user.username)}</strong>：</p>
        <p style="color:#64748b;">恭喜您！本期预测命中特码 <strong>{escape(predictions[0].actual_special_number)} ({escape(predictions[0].actual_special_zodiac)})</strong>。</p>
        {draw_html}
        {cards_html}
        ''',
        footer_note=f"此通知由 {site_name} 自动生成并发送，请勿回复。",
        tone='green',
    )
    
    notify_user(
        user,
        subject,
        text_content,
        html_content=html_content,
        event_type='winning',
        email_subject=subject,
    )

# 全局请求前处理器，检查用户激活状态
@app.before_request
def check_user_activation():
    # 跳过静态文件和认证相关路由
    if request.endpoint and (request.endpoint.startswith('static') or 
                           request.endpoint.startswith('auth.')):
        return
    
    # 检查用户是否登录
    if 'user_id' in session:
        try:
            user = User.query.get(session['user_id'])
            if user:
                session['is_active'] = bool(user.is_active)
                # 检查用户激活状态是否过期
                if user.activation_expires_at and datetime.now() > user.activation_expires_at:
                    # 激活已过期，更新状态
                    user.is_active = False
                    db.session.commit()
                    session['is_active'] = False
                    if not request.path.startswith('/auth/activate'):
                        flash('您的账号激活已过期，请使用新的激活码重新激活。', 'warning')
        except Exception as e:
            print(f"检查用户激活状态时出错: {e}")
            # 如果出错，跳过检查
            pass

# 创建数据库表和初始管理员账号
def init_database():
    with app.app_context():
        db.create_all()
        
        # 自动检查并更新数据库结构（邀请系统）
        from auto_update_db import check_and_update_database
        try:
            check_and_update_database()
        except Exception as e:
            print(f"自动更新数据库结构时出错: {e}")
        
        admin = User.query.filter_by(is_admin=True).first()
        
        # 初始化系统配置
        configs = [
            ('ai_api_key', '', 'AI API密钥'),
            ('ai_api_url', 'https://api.deepseek.com/v1/chat/completions', 'AI API地址'),
            ('ai_model', 'gemini-2.0-flash', 'AI模型'),
            ('smtp_server', '', 'SMTP服务器'),
            ('smtp_port', '587', 'SMTP端口'),
            ('smtp_username', '', 'SMTP用户名'),
            ('smtp_password', '', 'SMTP密码'),
            ('notify_email_enabled', 'true', '启用邮件推送'),
        ]
        
        for key, value, description in configs:
            if not SystemConfig.query.filter_by(key=key).first():
                config = SystemConfig(key=key, value=value, description=description)
                db.session.add(config)
        
        db.session.commit()
        
        # 为管理员创建示例邀请码
        try:
            if admin:
                existing_codes = InviteCode.query.filter_by(created_by=admin.username).count()
            else:
                existing_codes = 0
            if admin and existing_codes == 0:
                from datetime import timedelta
                for i in range(3):
                    invite_code = InviteCode()
                    invite_code.code = InviteCode.generate_code()
                    invite_code.created_by = admin.username
                    invite_code.expires_at = datetime.now() + timedelta(days=30)
                    db.session.add(invite_code)
                db.session.commit()
                if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
                    print("✅ 已为首个管理员预置示例邀请码")
        except Exception as e:
            print(f"创建示例邀请码时出错: {e}")

_scheduler = None
_scheduler_lock_path = None
_scheduler_lock_acquired = False

def _try_acquire_scheduler_lock():
    import tempfile
    global _scheduler_lock_path, _scheduler_lock_acquired
    if _scheduler_lock_acquired:
        return True
    lock_path = os.path.join(tempfile.gettempdir(), "mark-six-scheduler.lock")
    pid = os.getpid()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(pid))
        _scheduler_lock_path = lock_path
        _scheduler_lock_acquired = True
        return True
    except FileExistsError:
        try:
            with open(lock_path, "r") as f:
                existing_pid = int((f.read() or "").strip() or "0")
        except Exception:
            existing_pid = 0
        if existing_pid and _pid_is_running(existing_pid):
            return False
        try:
            os.remove(lock_path)
        except OSError:
            return False
        return _try_acquire_scheduler_lock()

def _release_scheduler_lock():
    global _scheduler_lock_path, _scheduler_lock_acquired
    if not _scheduler_lock_acquired or not _scheduler_lock_path:
        return
    try:
        os.remove(_scheduler_lock_path)
    except OSError:
        pass
    _scheduler_lock_path = None
    _scheduler_lock_acquired = False

# 定时任务：每天21:40自动更新数据库中的开奖记录
def update_lottery_data():
    """定时任务：更新数据库中的开奖记录"""
    _log_draw_update("开始执行定时开奖更新任务", source="scheduler", region="all")

    with app.app_context():
        try:
            current_year = str(datetime.now().year)
            updated_region_keys = []
            updated_regions = []

            _log_draw_update(f"开始同步香港开奖数据 current_year={current_year}", source="scheduler", region="hk")
            hk_data = sync_draws_from_api('hk', current_year, force=True)
            _log_draw_update(f"香港开奖数据同步完成 count={len(hk_data)}", source="scheduler", region="hk")
            updated_region_keys.append("hk")
            updated_regions.append(f"香港{len(hk_data)}条")
            update_prediction_accuracy(hk_data, 'hk', trigger_auto_predictions=False, tune_strategy_configs=False)
            _log_draw_update("香港预测结算已完成", source="scheduler", region="hk")
            update_hk_next_draw_time_cache(force=True)
            _log_draw_update("香港下期时间缓存已刷新", source="scheduler", region="hk")

            _log_draw_update(f"开始同步澳门开奖数据 current_year={current_year}", source="scheduler", region="macau")
            macau_data = sync_draws_from_api('macau', current_year, force=True)
            _log_draw_update(f"澳门开奖数据同步完成 count={len(macau_data)}", source="scheduler", region="macau")
            updated_region_keys.append("macau")
            updated_regions.append(f"澳门{len(macau_data)}条")
            update_prediction_accuracy(macau_data, 'macau', trigger_auto_predictions=False, tune_strategy_configs=False)
            _log_draw_update("澳门预测结算已完成", source="scheduler", region="macau")

            postprocess_started = _start_draw_postprocess_async(updated_region_keys, current_year, source="scheduler-postprocess")

            _log_draw_update(
                f"定时开奖更新任务完成 {'，'.join(updated_regions)}" + ("；自动预测和回测快照已转入后台继续处理" if postprocess_started else ""),
                source="scheduler",
                region="all",
            )

        except Exception as e:
            _log_draw_update(f"定时开奖更新任务失败 error={e}", source="scheduler", region="all")
            import traceback
            traceback.print_exc()

def warmup_auto_backtest_snapshots():
    """Ensure current backtest snapshots exist after app startup."""
    with app.app_context():
        try:
            refresh_auto_backtest_snapshots(force=False)
        except Exception as e:
            print(f"Auto backtest warmup failed: {e}")


_warmup_thread = None
_warmup_started = False


def start_async_backtest_warmup():
    """Run backtest warmup in a background thread so startup is non-blocking."""
    global _warmup_thread, _warmup_started
    enabled = os.environ.get("ENABLE_STARTUP_BACKTEST_WARMUP", "0").lower() in ("1", "true", "yes", "on")
    if not enabled or _warmup_started:
        return None

    def _runner():
        try:
            warmup_auto_backtest_snapshots()
        except Exception as e:
            print(f"Async backtest warmup failed: {e}")

    _warmup_thread = threading.Thread(
        target=_runner,
        name="mark-six-backtest-warmup",
        daemon=True,
    )
    _warmup_thread.start()
    _warmup_started = True
    return _warmup_thread


def start_scheduler(force=False):
    """Start the APScheduler job if enabled and not already running."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    enabled = os.environ.get("ENABLE_SCHEDULER", "1").lower() in ("1", "true", "yes", "on")
    if not enabled:
        if _should_log_startup():
            print("定时任务未启动：ENABLE_SCHEDULER=0")
        return None

    # Avoid double-start when Flask debug reloader spawns a parent process.
    if not force and app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None

    if not _try_acquire_scheduler_lock():
        if _should_log_startup():
            print("定时任务未启动：已有实例在运行")
        return None

    import atexit
    atexit.register(_release_scheduler_lock)

    _scheduler = BackgroundScheduler()

    def _log_scheduler_event(event):
        if event.code == EVENT_JOB_MISSED:
            print(f"定时任务补跑触发：{event.job_id} 原定时间已错过")
        elif event.code == EVENT_JOB_EXECUTED:
            if event.exception:
                print(f"定时任务执行失败：{event.job_id} {event.exception}")
            else:
                print(f"定时任务执行完成：{event.job_id}")

    _scheduler.add_listener(_log_scheduler_event, EVENT_JOB_MISSED | EVENT_JOB_EXECUTED)
    _scheduler.add_job(
        update_lottery_data,
        'cron',
        hour=21,
        minute=40,
        misfire_grace_time=300,
        coalesce=True
    )
    _scheduler.add_job(
        run_auto_strategy_optimization_job,
        'cron',
        hour=22,
        minute=5,
        kwargs={"regions": ("hk", "macau"), "source": "scheduler"},
        misfire_grace_time=600,
        coalesce=True
    )
    _scheduler.start()
    if _should_log_startup():
        print("定时任务已启动：每天21:40自动更新数据库中的开奖记录")
    return _scheduler

if os.environ.get("ENABLE_SCHEDULER", "1").lower() in ("1", "true", "yes", "on"):
    try:
        start_scheduler()
    except Exception as e:
        print(f"定时任务启动失败: {e}")

try:
    start_async_backtest_warmup()
except Exception as e:
    print(f"离线回测快照预热失败: {e}")

if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 设置定时任务
    scheduler = start_scheduler()
    
    try:
        # 启动Flask应用
        debug_enabled = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
        app.run(debug=debug_enabled, port=5000)
    except (KeyboardInterrupt, SystemExit):
        # 关闭定时任务
        if scheduler and scheduler.running:
            scheduler.shutdown()
