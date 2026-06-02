from datetime import datetime, timedelta
import json
import math
import time
from collections import OrderedDict
import secrets
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request, session, url_for
from sqlalchemy import func, case, or_
from sqlalchemy.orm import load_only

from models import (
    ActivationCode,
    ActivationCodeRequest,
    InviteCode,
    LotteryDraw,
    ManualBetRecord,
    PredictionRecord,
    SystemConfig,
    User,
    ZodiacSetting,
    db,
)
from auth import (
    _github_login_enabled,
    _github_oauth_config,
    _mobile_github_state_key,
    _turnstile_site_key,
    _verify_turnstile_response,
    send_reset_email,
    send_activation_request_notification,
)


mobile_api_bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile")

STRATEGY_KEYS = ["hot", "cold", "trend", "hybrid", "balanced", "markov", "ml", "ai"]
LOCAL_STRATEGIES = ["hot", "cold", "trend", "hybrid", "balanced", "markov", "ml"]
_RED_BALLS = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
_BLUE_BALLS = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
_RATE_LIMITS = {}
_MOBILE_CSRF_EXEMPT_ENDPOINTS = {
    "mobile_api.api_auth_config",
    "mobile_api.api_github_auth_url",
    "mobile_api.api_github_success",
    "mobile_api.api_github_complete",
    "mobile_api.api_login",
    "mobile_api.api_logout",
    "mobile_api.api_forgot_password",
    "mobile_api.api_register",
}


def _safe_json_loads(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _hydrate_mobile_prediction_metadata(record):
    metadata = _safe_json_loads(getattr(record, "prediction_metadata", None))
    if getattr(record, "strategy", "") != "ml":
        return metadata

    try:
        from app import _get_prediction_data, _hydrate_prediction_model_meta

        data, _ = _get_prediction_data(
            getattr(record, "region", ""),
            datetime.now().year,
        )
        return _hydrate_prediction_model_meta(
            getattr(record, "strategy", ""),
            metadata,
            data,
            getattr(record, "region", ""),
        )
    except Exception as e:
        print(f"移动端补齐机器学习预测诊断信息失败: {e}")
        return metadata


def _hydrate_mobile_prediction_text(record):
    text = getattr(record, "prediction_text", "") or ""
    if getattr(record, "strategy", "") != "ml":
        return text

    try:
        from app import _get_prediction_data, _hydrate_prediction_recommendation_text

        data, _ = _get_prediction_data(
            getattr(record, "region", ""),
            datetime.now().year,
        )
        metadata = _safe_json_loads(getattr(record, "prediction_metadata", None))
        return _hydrate_prediction_recommendation_text(
            getattr(record, "strategy", ""),
            text,
            data,
            getattr(record, "region", ""),
            special_number=getattr(record, "special_number", ""),
            normal_numbers=getattr(record, "normal_numbers", ""),
            existing_meta=metadata,
        )
    except Exception as e:
        print(f"移动端补齐机器学习预测文案失败: {e}")
        return text


def _get_color_zh(number):
    try:
        num = int(number)
    except (TypeError, ValueError):
        return ""
    if num in _RED_BALLS:
        return "红"
    if num in _BLUE_BALLS:
        return "蓝"
    return "绿"


def _parse_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _parse_int_list(value):
    items = _parse_list(value)
    result = []
    for item in items:
        try:
            result.append(int(item))
        except ValueError:
            continue
    return result


def _parse_number_stakes(value):
    if not value:
        return {}
    if isinstance(value, dict):
        result = {}
        for key, stake in value.items():
            try:
                number = int(str(key).strip())
                amount = float(stake)
            except (TypeError, ValueError):
                continue
            if number > 0 and 0 < amount <= 100000.0 and math.isfinite(amount):
                result[number] = amount
        return result
    if isinstance(value, list):
        result = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                number = int(str(item.get("number", "")).strip())
                amount = float(item.get("stake"))
            except (TypeError, ValueError):
                continue
            if number > 0 and 0 < amount <= 100000.0 and math.isfinite(amount):
                result[number] = amount
        return result
    if isinstance(value, str):
        result = {}
        for chunk in value.split(","):
            part = chunk.strip()
            if not part:
                continue
            if ":" not in part:
                continue
            num_str, stake_str = part.split(":", 1)
            try:
                number = int(num_str.strip())
                amount = float(stake_str.strip())
            except (TypeError, ValueError):
                continue
            if number > 0 and 0 < amount <= 100000.0 and math.isfinite(amount):
                result[number] = amount
        return result
    return {}


def _parse_common_stake_entries(value):
    if not value:
        return []
    if isinstance(value, str):
        entries = []
        for part in value.split(","):
            piece = part.strip()
            if not piece or ":" not in piece:
                continue
            key, amount_text = piece.split(":", 1)
            key = key.strip()
            try:
                amount = float(amount_text.strip())
            except (TypeError, ValueError):
                continue
            if key and 0 < amount <= 100000.0 and math.isfinite(amount):
                entries.append((key, amount))
        return entries
    return []


def _serialize_common_stakes(entries):
    return ",".join(f"{key}:{amount}" for key, amount in entries)


def _build_common_stakes(items, amount):
    try:
        stake = float(amount)
    except (TypeError, ValueError):
        return []
    if stake <= 0 or stake > 100000.0 or not math.isfinite(stake):
        return []
    entries = []
    for item in items:
        value = str(item).strip()
        if value:
            entries.append((value, stake))
    return entries


def _calc_common_entries(entries, match_value, odds):
    profit = 0
    total = 0
    win = False
    for value, amount in entries:
        total += amount
        if value == match_value:
            win = True
            profit += amount * odds - amount
        else:
            profit += -amount
    return win, profit, total


def _odds_match(existing_value, incoming_value):
    try:
        existing = float(existing_value or 0)
    except (TypeError, ValueError):
        existing = 0
    try:
        incoming = float(incoming_value or 0)
    except (TypeError, ValueError):
        incoming = 0
    return abs(existing - incoming) < 0.0001


def _serialize_number_stakes(number_stakes):
    parts = []
    for number, amount in number_stakes.items():
        parts.append(f"{number}:{amount}")
    return ",".join(parts)


def _validate_bet_payload(
    bet_number,
    bet_zodiac,
    bet_color,
    bet_parity,
    selected_numbers,
    selected_zodiacs,
    selected_colors,
    selected_parity,
    number_stakes,
    stake_special,
    stake_common,
):
    if not (bet_number or bet_zodiac or bet_color or bet_parity):
        return "请选择下注类型"
    if bet_number:
        if number_stakes:
            if not selected_numbers:
                return "请选择号码"
            if any(amount <= 0 for amount in number_stakes.values()):
                return "每个号码的下注金额需大于0"
        else:
            if not selected_numbers:
                return "请选择号码"
            if stake_special <= 0:
                return "请输入有效的特码下注金额"
    if bet_zodiac and not selected_zodiacs:
        return "请选择生肖"
    if bet_color and not selected_colors:
        return "请选择波色"
    if bet_parity and not selected_parity:
        return "请选择单双"
    if (bet_zodiac or bet_color or bet_parity) and stake_common <= 0:
        return "请输入有效的共用下注金额"
    return ""


def _json_error(message, status=400, code="bad_request"):
    return jsonify({"success": False, "error": code, "message": message}), status


def _client_ip():
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    return request.headers.get("CF-Connecting-IP") or forwarded or request.remote_addr or "unknown"


def _rate_limited(key, limit, window_seconds):
    now = time.time()
    cutoff = now - window_seconds
    attempts = [item for item in _RATE_LIMITS.get(key, []) if item >= cutoff]
    if len(attempts) >= limit:
        _RATE_LIMITS[key] = attempts
        return True
    attempts.append(now)
    _RATE_LIMITS[key] = attempts
    if len(_RATE_LIMITS) > 5000:
        for existing_key in list(_RATE_LIMITS.keys())[:1000]:
            _RATE_LIMITS[existing_key] = [
                item for item in _RATE_LIMITS[existing_key] if item >= cutoff
            ]
            if not _RATE_LIMITS[existing_key]:
                _RATE_LIMITS.pop(existing_key, None)
    return False


def _get_mobile_csrf_token():
    token = session.get("_mobile_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_mobile_csrf_token"] = token
    return token


def _mobile_csrf_valid():
    expected = session.get("_mobile_csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or ""
    return bool(expected and supplied and secrets.compare_digest(str(expected), str(supplied)))


@mobile_api_bp.before_request
def _mobile_api_security_guards():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if request.endpoint in _MOBILE_CSRF_EXEMPT_ENDPOINTS:
        return None
    if not _mobile_csrf_valid():
        return _json_error("CSRF token invalid or missing", status=403, code="csrf_blocked")
    return None


def _parse_int_arg(name, default, minimum=None, maximum=None):
    raw = request.args.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is invalid")
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _parse_float_payload(payload, name, default=0.0, minimum=0.0, maximum=1000000.0):
    raw = payload.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is invalid")
    if not math.isfinite(value):
        raise ValueError(f"{name} is invalid")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def _parse_bool_payload(payload, name, default=False):
    value = payload.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(default)


def _validate_text_length(value, label, minimum=0, maximum=255, required=False):
    text = str(value or "").strip()
    if required and not text:
        return text, f"{label} is required"
    if text and len(text) < minimum:
        return text, f"{label} must be at least {minimum} characters"
    if len(text) > maximum:
        return text, f"{label} must be at most {maximum} characters"
    return text, ""


def _user_payload(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "show_normal_numbers": bool(user.show_normal_numbers),
    }


def _start_user_session(user):
    user.check_and_update_activation_status()
    user.last_login = datetime.utcnow()
    user.login_count = (user.login_count or 0) + 1
    db.session.commit()

    session["user_id"] = user.id
    session["username"] = user.username
    session["is_admin"] = user.is_admin
    session["is_active"] = user.is_active
    session.permanent = True


def _config_key(prefix, token):
    return f"{prefix}{token}"


def _pop_config_json(key):
    config = SystemConfig.query.filter_by(key=key).first()
    if not config:
        return None
    raw = config.value or ""
    db.session.delete(config)
    db.session.commit()
    try:
        return json.loads(raw)
    except Exception:
        return None


def _store_config_json(key, payload, description):
    SystemConfig.set_config(
        key,
        json.dumps(payload, ensure_ascii=False),
        description,
    )


def _github_mobile_html(title, message):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title></head>"
        "<body style='font-family:system-ui,sans-serif;padding:24px;"
        "text-align:center;color:#111827'>"
        f"<h3>{title}</h3><p>{message}</p>"
        "</body></html>"
    )


@mobile_api_bp.route("/auth_config", methods=["GET"])
def api_auth_config():
    return jsonify({
        "success": True,
        "turnstile_site_key": _turnstile_site_key(),
        "github_login_enabled": _github_login_enabled(),
    })


@mobile_api_bp.route("/github/auth_url", methods=["GET"])
def api_github_auth_url():
    if _rate_limited(f"github_auth:{_client_ip()}", 10, 3600):
        return _json_error("too many GitHub login attempts", status=429, code="rate_limited")

    config = _github_oauth_config()
    if not config:
        return _json_error("GitHub 登录尚未配置", status=404, code="github_not_configured")

    state = secrets.token_urlsafe(24)
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    _store_config_json(
        _mobile_github_state_key(state),
        {"state": state, "expires_at": expires_at.isoformat()},
        "Mobile GitHub OAuth state",
    )
    query = urlencode({
        "client_id": config["client_id"],
        "redirect_uri": url_for("auth.github_callback", _external=True),
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    })
    return jsonify({
        "success": True,
        "auth_url": f"https://github.com/login/oauth/authorize?{query}",
        "state": state,
    })


@mobile_api_bp.route("/github/success", methods=["GET"])
def api_github_success():
    return _github_mobile_html("授权完成", "正在返回应用，请稍候...")


@mobile_api_bp.route("/github/complete", methods=["POST"])
def api_github_complete():
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token") or "").strip()
    if not token:
        return _json_error("missing GitHub login token")

    login_payload = _pop_config_json(_config_key("mobile_github_login_", token))
    try:
        expires_at = datetime.fromisoformat((login_payload or {}).get("expires_at", ""))
    except Exception:
        expires_at = datetime.utcnow() - timedelta(seconds=1)
    if not login_payload or datetime.utcnow() >= expires_at:
        return _json_error("GitHub login token expired", status=401, code="github_token_expired")

    user = User.query.get(login_payload.get("user_id"))
    if not user:
        return _json_error("GitHub user not found", status=404, code="user_not_found")

    _start_user_session(user)
    return jsonify({
        "success": True,
        "message": "login successful",
        "csrf_token": _get_mobile_csrf_token(),
        "user": _user_payload(user),
    })


def _get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def _require_user():
    user = _get_current_user()
    if not user:
        return None, _json_error("authentication required", status=401, code="auth_required")
    is_active = user.check_and_update_activation_status()
    session["is_active"] = bool(is_active and user.is_active)
    return user, None


def _require_active_user():
    user, error = _require_user()
    if error:
        return None, error
    if not user.is_active:
        return None, _json_error("activation required", status=403, code="activation_required")
    return user, None


@mobile_api_bp.route("/register", methods=["POST"])
def api_register():
    payload = request.get_json(silent=True) or {}
    if _rate_limited(f"register:{_client_ip()}", 10, 3600):
        return _json_error("too many registration attempts", status=429, code="rate_limited")

    username, error = _validate_text_length(
        payload.get("username"), "username", minimum=3, maximum=80, required=True
    )
    if error:
        return _json_error(error)
    email, error = _validate_text_length(
        payload.get("email"), "email", minimum=6, maximum=120, required=True
    )
    if error:
        return _json_error(error)
    password = payload.get("password") or ""
    confirm_password = payload.get("confirm_password") or ""
    invite_code, error = _validate_text_length(
        payload.get("invite_code"), "invite_code", maximum=64
    )
    if error:
        return _json_error(error)
    turnstile_ok, turnstile_message = _verify_turnstile_response(
        payload.get("turnstile_token") or ""
    )
    if not turnstile_ok:
        return _json_error(turnstile_message, status=403, code="turnstile_failed")

    if not all([username, email, password, confirm_password]):
        return _json_error("missing required fields")
    if password != confirm_password:
        return _json_error("passwords do not match")
    if len(password) < 6:
        return _json_error("password must be at least 6 characters")

    if User.query.filter_by(username=username).first():
        return _json_error("username already exists")
    if User.query.filter_by(email=email).first():
        return _json_error("email already exists")

    user = User(username=username, email=email, is_admin=False)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()

    invite_success = False
    if invite_code:
        invite_record = InviteCode.query.filter_by(code=invite_code).first()
        if not invite_record:
            db.session.rollback()
            return _json_error("invalid invite code")
        success, message = invite_record.use_invite_code(user)
        if not success:
            db.session.rollback()
            return _json_error(message)
        invite_success = True
    else:
        user.extend_activation(7)
        user.is_active = True

    db.session.commit()

    message = "registered successfully"
    if not invite_success:
        message = "registered successfully, activated for 7 days"

    return jsonify(
        {
            "success": True,
            "message": message,
            "user": _user_payload(user),
        }
    )


@mobile_api_bp.route("/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or {}
    username_or_email, error = _validate_text_length(
        payload.get("username"), "username", minimum=3, maximum=120, required=True
    )
    if error:
        return _json_error(error)
    password = payload.get("password") or ""
    if _rate_limited(f"login:{_client_ip()}", 10, 3600):
        return _json_error("too many login attempts", status=429, code="rate_limited")

    turnstile_ok, turnstile_message = _verify_turnstile_response(
        payload.get("turnstile_token") or ""
    )
    if not turnstile_ok:
        return _json_error(turnstile_message, status=403, code="turnstile_failed")

    if not username_or_email or not password:
        return _json_error("missing username/email or password")

    user = User.query.filter(
        (User.username == username_or_email) | (User.email == username_or_email)
    ).first()
    if not user or not user.check_password(password):
        return _json_error("invalid credentials", status=401, code="invalid_credentials")

    _start_user_session(user)

    return jsonify(
        {
            "success": True,
            "message": "login successful",
            "csrf_token": _get_mobile_csrf_token(),
            "user": _user_payload(user),
        }
    )


@mobile_api_bp.route("/forgot_password", methods=["POST"])
def api_forgot_password():
    payload = request.get_json(silent=True) or {}
    if _rate_limited(f"forgot_password:{_client_ip()}", 10, 3600):
        return _json_error("too many password reset attempts", status=429, code="rate_limited")

    email, error = _validate_text_length(
        payload.get("email"), "email", minimum=6, maximum=120, required=True
    )
    if error:
        return _json_error(error)

    turnstile_ok, turnstile_message = _verify_turnstile_response(
        payload.get("turnstile_token") or ""
    )
    if not turnstile_ok:
        return _json_error(turnstile_message, status=403, code="turnstile_failed")

    generic_message = "如果该邮箱已注册，重置密码链接将发送到邮箱"
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"success": True, "message": generic_message})

    reset_token = secrets.token_urlsafe(32)
    token_key = f"reset_token_{user.id}"
    token_data = f"{reset_token}|{(datetime.utcnow() + timedelta(hours=1)).isoformat()}"
    SystemConfig.set_config(token_key, token_data, "密码重置令牌")

    try:
        send_reset_email(user.email, user.username, reset_token)
    except Exception as e:
        print(f"Mobile password reset email failed: {e}")
        return _json_error("password reset email failed", status=500, code="email_failed")

    return jsonify({"success": True, "message": generic_message})


@mobile_api_bp.route("/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True, "message": "logged out"})


@mobile_api_bp.route("/change_password", methods=["POST"])
def api_change_password():
    user, error = _require_user()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password") or ""
    new_password = payload.get("new_password") or ""
    confirm_password = payload.get("confirm_password") or ""

    if not all([current_password, new_password, confirm_password]):
        return _json_error("missing required fields")
    if not user.check_password(current_password):
        return _json_error("invalid current password", status=401, code="invalid_credentials")
    if new_password != confirm_password:
        return _json_error("passwords do not match")
    if len(new_password) < 6:
        return _json_error("password must be at least 6 characters")

    user.set_password(new_password)
    db.session.commit()

    return jsonify({"success": True, "message": "password updated"})


@mobile_api_bp.route("/activate", methods=["POST"])
def api_activate():
    user, error = _require_user()
    if error:
        return error
    if _rate_limited(f"activate:{_client_ip()}", 10, 3600):
        return _json_error("too many activation attempts", status=429, code="rate_limited")

    if user.is_active:
        return jsonify({"success": True, "message": "already activated", "is_active": True})

    payload = request.get_json(silent=True) or {}
    activation_code, error = _validate_text_length(
        payload.get("activation_code"), "activation_code", maximum=64, required=True
    )
    if error:
        return _json_error(error)
    if not activation_code:
        return _json_error("activation_code is required")

    code_record = ActivationCode.query.filter_by(code=activation_code).first()
    if not code_record:
        return _json_error("invalid activation code")

    success, message = code_record.use_code(user)
    if not success:
        return _json_error(message)

    db.session.commit()
    session["is_active"] = True
    return jsonify({"success": True, "message": message, "is_active": True})


@mobile_api_bp.route("/activation_requests", methods=["GET"])
def api_activation_requests():
    user, error = _require_user()
    if error:
        return error

    rows = ActivationCodeRequest.query.filter_by(user_id=user.id).order_by(
        ActivationCodeRequest.created_at.desc()
    ).limit(10).all()
    return jsonify({
        "success": True,
        "requests": [item.to_dict() for item in rows],
    })


@mobile_api_bp.route("/activation_requests", methods=["POST"])
def api_request_activation_code():
    user, error = _require_user()
    if error:
        return error
    if _rate_limited(f"activation_request:{_client_ip()}", 10, 3600):
        return _json_error("too many activation requests", status=429, code="rate_limited")

    if user.is_active:
        return _json_error("account already activated")

    pending_request = ActivationCodeRequest.query.filter_by(
        user_id=user.id,
        status='pending'
    ).first()
    if pending_request:
        return _json_error("pending activation request already exists")

    payload = request.get_json(silent=True) or {}
    request_note, error = _validate_text_length(
        payload.get("request_note"), "request_note", maximum=500
    )
    if error:
        return _json_error(error)

    request_record = ActivationCodeRequest(
        user_id=user.id,
        username=user.username,
        email=user.email,
        request_note=request_note,
        status='pending',
    )
    db.session.add(request_record)
    db.session.commit()

    try:
        send_activation_request_notification(request_record)
    except Exception as e:
        print(f"Failed to send activation request admin notification: {e}")

    return jsonify({
        "success": True,
        "message": "activation request submitted",
        "request": request_record.to_dict(),
    })


@mobile_api_bp.route("/manual_bets", methods=["POST"])
def api_manual_bets():
    user, error = _require_active_user()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    settle = _parse_bool_payload(payload, "settle", True)
    record_id = payload.get("record_id")
    if record_id is not None:
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            return _json_error("record_id is invalid")
    region, error = _validate_text_length(
        payload.get("region") or "hk", "region", maximum=10, required=True
    )
    if error:
        return _json_error(error)
    if region not in {"hk", "macau"}:
        return _json_error("region is invalid")
    period, error = _validate_text_length(
        payload.get("period"), "period", maximum=20, required=True
    )
    if error:
        return _json_error(error)
    bettor_name, error = _validate_text_length(
        payload.get("bettor_name"), "bettor_name", maximum=50
    )
    if error:
        return _json_error(error)

    bet_number = _parse_bool_payload(payload, "bet_number")
    bet_zodiac = _parse_bool_payload(payload, "bet_zodiac")
    bet_color = _parse_bool_payload(payload, "bet_color")
    bet_parity = _parse_bool_payload(payload, "bet_parity")

    number_stakes = (
        _parse_number_stakes(payload.get("number_stakes"))
        if bet_number
        else {}
    )
    selected_numbers = (
        sorted(number_stakes.keys())
        if number_stakes
        else (_parse_int_list(payload.get("numbers")) if bet_number else [])
    )
    selected_zodiacs = (
        _parse_list(payload.get("zodiacs")) if bet_zodiac else []
    )
    selected_colors = (
        _parse_list(payload.get("colors")) if bet_color else []
    )
    selected_parity = (
        _parse_list(payload.get("parity")) if bet_parity else []
    )

    try:
        stake_special = _parse_float_payload(payload, "stake_special", maximum=100000.0)
        stake_common = _parse_float_payload(payload, "stake_common", maximum=100000.0)
        odds_number = _parse_float_payload(payload, "odds_number", maximum=10000.0)
        odds_zodiac = _parse_float_payload(payload, "odds_zodiac", maximum=10000.0)
        odds_color = _parse_float_payload(payload, "odds_color", maximum=10000.0)
        odds_parity = _parse_float_payload(payload, "odds_parity", maximum=10000.0)
    except ValueError as e:
        return _json_error(str(e))

    validation_error = _validate_bet_payload(
        bet_number,
        bet_zodiac,
        bet_color,
        bet_parity,
        selected_numbers,
        selected_zodiacs,
        selected_colors,
        selected_parity,
        number_stakes,
        stake_special,
        stake_common,
    )
    if validation_error:
        return _json_error(validation_error)

    total_stake = 0
    if bet_number:
        if number_stakes:
            total_stake += sum(number_stakes.values())
            stake_special = total_stake
        else:
            total_stake += stake_special

    zodiac_entries = _build_common_stakes(selected_zodiacs, stake_common) if bet_zodiac else []
    color_entries = _build_common_stakes(selected_colors, stake_common) if bet_color else []
    parity_entries = _build_common_stakes(selected_parity, stake_common) if bet_parity else []
    common_total = (
        sum(amount for _, amount in zodiac_entries)
        + sum(amount for _, amount in color_entries)
        + sum(amount for _, amount in parity_entries)
    )
    total_stake += common_total
    if common_total > 0:
        stake_common = common_total

    if not settle:
        if bet_number and number_stakes and not (bet_zodiac or bet_color or bet_parity):
            merge_query = ManualBetRecord.query.filter_by(
                user_id=user.id,
                region=region,
                period=period,
            ).filter(
                ManualBetRecord.total_profit.is_(None),
                or_(
                    ManualBetRecord.selected_zodiacs.is_(None),
                    ManualBetRecord.selected_zodiacs == "",
                ),
                or_(
                    ManualBetRecord.selected_colors.is_(None),
                    ManualBetRecord.selected_colors == "",
                ),
                or_(
                    ManualBetRecord.selected_parity.is_(None),
                    ManualBetRecord.selected_parity == "",
                ),
            )
            if bettor_name:
                merge_query = merge_query.filter(
                    ManualBetRecord.bettor_name == bettor_name
                )
            else:
                merge_query = merge_query.filter(
                    or_(
                        ManualBetRecord.bettor_name.is_(None),
                        ManualBetRecord.bettor_name == "",
                    )
                )
            existing = None
            for candidate in merge_query.order_by(ManualBetRecord.created_at.desc()).all():
                if _odds_match(candidate.odds_number, odds_number):
                    existing = candidate
                    break
            if existing:
                existing_stakes = _parse_number_stakes(existing.selected_numbers)
                if not existing_stakes:
                    fallback_numbers = _parse_int_list(existing.selected_numbers)
                    if fallback_numbers and float(existing.stake_special or 0) > 0:
                        each_amount = float(existing.stake_special or 0) / len(
                            fallback_numbers
                        )
                        existing_stakes = {
                            number: each_amount for number in fallback_numbers
                        }
                if existing_stakes:
                    for number, amount in number_stakes.items():
                        existing_stakes[number] = existing_stakes.get(number, 0) + amount
                    merged_total = sum(existing_stakes.values())
                    existing.selected_numbers = _serialize_number_stakes(existing_stakes)
                    existing.odds_number = odds_number
                    existing.stake_special = merged_total
                    existing.total_stake = merged_total
                    db.session.commit()
                    return jsonify({"success": True, "record_id": existing.id})

        if bet_zodiac and not (bet_number or bet_color or bet_parity):
            merge_query = ManualBetRecord.query.filter_by(
                user_id=user.id,
                region=region,
                period=period,
            ).filter(
                ManualBetRecord.total_profit.is_(None),
                or_(
                    ManualBetRecord.selected_numbers.is_(None),
                    ManualBetRecord.selected_numbers == "",
                ),
                or_(
                    ManualBetRecord.selected_colors.is_(None),
                    ManualBetRecord.selected_colors == "",
                ),
                or_(
                    ManualBetRecord.selected_parity.is_(None),
                    ManualBetRecord.selected_parity == "",
                ),
            )
            if bettor_name:
                merge_query = merge_query.filter(
                    ManualBetRecord.bettor_name == bettor_name
                )
            else:
                merge_query = merge_query.filter(
                    or_(
                        ManualBetRecord.bettor_name.is_(None),
                        ManualBetRecord.bettor_name == "",
                    )
                )
            existing = None
            for candidate in merge_query.order_by(ManualBetRecord.created_at.desc()).all():
                if _odds_match(candidate.odds_zodiac, odds_zodiac):
                    existing = candidate
                    break
            if existing:
                existing_entries = _parse_common_stake_entries(existing.selected_zodiacs)
                if not existing_entries:
                    existing_items = _parse_list(existing.selected_zodiacs)
                    if existing_items and float(existing.stake_common or 0) > 0:
                        existing_entries = _build_common_stakes(
                            existing_items,
                            float(existing.stake_common or 0),
                        )
                new_entries = _build_common_stakes(selected_zodiacs, stake_common)
                merged_entries = existing_entries + new_entries
                merged_total = sum(amount for _, amount in merged_entries)
                existing.selected_zodiacs = _serialize_common_stakes(merged_entries)
                existing.odds_zodiac = odds_zodiac
                existing.stake_common = merged_total
                existing.total_stake = merged_total
                db.session.commit()
                return jsonify({"success": True, "record_id": existing.id})

        if bet_color and not (bet_number or bet_zodiac or bet_parity):
            merge_query = ManualBetRecord.query.filter_by(
                user_id=user.id,
                region=region,
                period=period,
            ).filter(
                ManualBetRecord.total_profit.is_(None),
                or_(
                    ManualBetRecord.selected_numbers.is_(None),
                    ManualBetRecord.selected_numbers == "",
                ),
                or_(
                    ManualBetRecord.selected_zodiacs.is_(None),
                    ManualBetRecord.selected_zodiacs == "",
                ),
                or_(
                    ManualBetRecord.selected_parity.is_(None),
                    ManualBetRecord.selected_parity == "",
                ),
            )
            if bettor_name:
                merge_query = merge_query.filter(
                    ManualBetRecord.bettor_name == bettor_name
                )
            else:
                merge_query = merge_query.filter(
                    or_(
                        ManualBetRecord.bettor_name.is_(None),
                        ManualBetRecord.bettor_name == "",
                    )
                )
            existing = None
            for candidate in merge_query.order_by(ManualBetRecord.created_at.desc()).all():
                if _odds_match(candidate.odds_color, odds_color):
                    existing = candidate
                    break
            if existing:
                existing_entries = _parse_common_stake_entries(existing.selected_colors)
                if not existing_entries:
                    existing_items = _parse_list(existing.selected_colors)
                    if existing_items and float(existing.stake_common or 0) > 0:
                        existing_entries = _build_common_stakes(
                            existing_items,
                            float(existing.stake_common or 0),
                        )
                new_entries = _build_common_stakes(selected_colors, stake_common)
                merged_entries = existing_entries + new_entries
                merged_total = sum(amount for _, amount in merged_entries)
                existing.selected_colors = _serialize_common_stakes(merged_entries)
                existing.odds_color = odds_color
                existing.stake_common = merged_total
                existing.total_stake = merged_total
                db.session.commit()
                return jsonify({"success": True, "record_id": existing.id})

        if bet_parity and not (bet_number or bet_zodiac or bet_color):
            merge_query = ManualBetRecord.query.filter_by(
                user_id=user.id,
                region=region,
                period=period,
            ).filter(
                ManualBetRecord.total_profit.is_(None),
                or_(
                    ManualBetRecord.selected_numbers.is_(None),
                    ManualBetRecord.selected_numbers == "",
                ),
                or_(
                    ManualBetRecord.selected_zodiacs.is_(None),
                    ManualBetRecord.selected_zodiacs == "",
                ),
                or_(
                    ManualBetRecord.selected_colors.is_(None),
                    ManualBetRecord.selected_colors == "",
                ),
            )
            if bettor_name:
                merge_query = merge_query.filter(
                    ManualBetRecord.bettor_name == bettor_name
                )
            else:
                merge_query = merge_query.filter(
                    or_(
                        ManualBetRecord.bettor_name.is_(None),
                        ManualBetRecord.bettor_name == "",
                    )
                )
            existing = None
            for candidate in merge_query.order_by(ManualBetRecord.created_at.desc()).all():
                if _odds_match(candidate.odds_parity, odds_parity):
                    existing = candidate
                    break
            if existing:
                existing_entries = _parse_common_stake_entries(existing.selected_parity)
                if not existing_entries:
                    existing_items = _parse_list(existing.selected_parity)
                    if existing_items and float(existing.stake_common or 0) > 0:
                        existing_entries = _build_common_stakes(
                            existing_items,
                            float(existing.stake_common or 0),
                        )
                new_entries = _build_common_stakes(selected_parity, stake_common)
                merged_entries = existing_entries + new_entries
                merged_total = sum(amount for _, amount in merged_entries)
                existing.selected_parity = _serialize_common_stakes(merged_entries)
                existing.odds_parity = odds_parity
                existing.stake_common = merged_total
                existing.total_stake = merged_total
                db.session.commit()
                return jsonify({"success": True, "record_id": existing.id})

        record_numbers = (
            _serialize_number_stakes(number_stakes)
            if number_stakes
            else ",".join(str(n) for n in selected_numbers)
        )
        record_zodiacs = (
            _serialize_common_stakes(zodiac_entries)
            if zodiac_entries
            else ",".join(selected_zodiacs)
        )
        record_colors = (
            _serialize_common_stakes(color_entries)
            if color_entries
            else ",".join(selected_colors)
        )
        record_parity = (
            _serialize_common_stakes(parity_entries)
            if parity_entries
            else ",".join(selected_parity)
        )
        record = ManualBetRecord(
            user_id=user.id,
            region=region,
            period=period,
            bettor_name=bettor_name or None,
            selected_numbers=record_numbers,
            selected_zodiacs=record_zodiacs,
            selected_colors=record_colors,
            selected_parity=record_parity,
            odds_number=odds_number,
            odds_zodiac=odds_zodiac,
            odds_color=odds_color,
            odds_parity=odds_parity,
            stake_special=stake_special,
            stake_common=stake_common,
            total_stake=total_stake,
        )
        db.session.add(record)
        db.session.commit()
        return jsonify({"success": True, "record_id": record.id})

    draw = LotteryDraw.query.filter_by(region=region, draw_id=period).first()
    if not draw:
        return _json_error("draw not found", status=404, code="draw_not_found")

    record = None
    if record_id is not None:
        record = ManualBetRecord.query.filter_by(
            id=record_id, user_id=user.id
        ).first()
        if not record:
            return _json_error("record not found", status=404, code="record_not_found")
        if record.total_profit is not None:
            return _json_error("record already settled", status=400, code="record_settled")

        number_stakes = _parse_number_stakes(record.selected_numbers)
        selected_numbers = (
            sorted(number_stakes.keys())
            if number_stakes
            else _parse_int_list(record.selected_numbers)
        )
        zodiac_entries = _parse_common_stake_entries(record.selected_zodiacs)
        color_entries = _parse_common_stake_entries(record.selected_colors)
        parity_entries = _parse_common_stake_entries(record.selected_parity)
        selected_zodiacs = (
            [value for value, _ in zodiac_entries]
            if zodiac_entries
            else _parse_list(record.selected_zodiacs)
        )
        selected_colors = (
            [value for value, _ in color_entries]
            if color_entries
            else _parse_list(record.selected_colors)
        )
        selected_parity = (
            [value for value, _ in parity_entries]
            if parity_entries
            else _parse_list(record.selected_parity)
        )
        bet_number = bool(selected_numbers)
        bet_zodiac = bool(zodiac_entries or selected_zodiacs)
        bet_color = bool(color_entries or selected_colors)
        bet_parity = bool(parity_entries or selected_parity)
        stake_special = record.stake_special or 0
        stake_common = record.stake_common or 0
        odds_number = record.odds_number or 0
        odds_zodiac = record.odds_zodiac or 0
        odds_color = record.odds_color or 0
        odds_parity = record.odds_parity or 0
        if record.total_stake is not None:
            total_stake = record.total_stake
        elif number_stakes:
            total_stake = sum(number_stakes.values())
        else:
            total_stake = total_stake

    raw_zodiacs = draw.raw_zodiac.split(",") if draw.raw_zodiac else []
    special_zodiac = draw.special_zodiac or ""
    if raw_zodiacs:
        special_zodiac = raw_zodiacs[-1] or special_zodiac

    special_number = draw.special_number or ""
    special_color = _get_color_zh(special_number)
    special_parity = "双"
    try:
        special_parity = "双" if int(special_number) % 2 == 0 else "单"
    except (TypeError, ValueError):
        special_parity = ""

    result_number = None
    result_zodiac = None
    result_color = None
    result_parity = None
    profit_number = None
    profit_zodiac = None
    profit_color = None
    profit_parity = None
    total_profit = 0

    total_number_stake = 0
    common_total = 0

    if bet_number:
        result_number = special_number.isdigit() and int(
            special_number
        ) in selected_numbers
        if number_stakes:
            total_number_stake = sum(number_stakes.values())
            hit_stake = number_stakes.get(int(special_number), 0)
            profit_number = hit_stake * odds_number - total_number_stake
        else:
            total_number_stake = stake_special
            profit_number = (
                stake_special * odds_number - stake_special
                if result_number
                else -stake_special
            )
        total_profit += profit_number

    if bet_zodiac:
        if zodiac_entries:
            result_zodiac, profit_zodiac, zodiac_total = _calc_common_entries(
                zodiac_entries,
                special_zodiac,
                odds_zodiac,
            )
            common_total += zodiac_total
        else:
            result_zodiac = special_zodiac in selected_zodiacs
            profit_zodiac = (
                stake_common * odds_zodiac - stake_common
                if result_zodiac
                else -stake_common
            )
            common_total += stake_common
        total_profit += profit_zodiac

    if bet_color:
        if color_entries:
            result_color, profit_color, color_total = _calc_common_entries(
                color_entries,
                special_color,
                odds_color,
            )
            common_total += color_total
        else:
            result_color = special_color in selected_colors
            profit_color = (
                stake_common * odds_color - stake_common
                if result_color
                else -stake_common
            )
            common_total += stake_common
        total_profit += profit_color

    if bet_parity:
        if parity_entries:
            result_parity, profit_parity, parity_total = _calc_common_entries(
                parity_entries,
                special_parity,
                odds_parity,
            )
            common_total += parity_total
        else:
            result_parity = special_parity in selected_parity
            profit_parity = (
                stake_common * odds_parity - stake_common
                if result_parity
                else -stake_common
            )
            common_total += stake_common
        total_profit += profit_parity

    if total_number_stake or common_total:
        total_stake = total_number_stake + common_total

    if record is None:
        record_zodiacs = (
            _serialize_common_stakes(zodiac_entries)
            if zodiac_entries
            else ",".join(selected_zodiacs)
        )
        record_colors = (
            _serialize_common_stakes(color_entries)
            if color_entries
            else ",".join(selected_colors)
        )
        record_parity = (
            _serialize_common_stakes(parity_entries)
            if parity_entries
            else ",".join(selected_parity)
        )
        record_numbers = (
            _serialize_number_stakes(number_stakes)
            if number_stakes
            else ",".join(str(n) for n in selected_numbers)
        )
        record = ManualBetRecord(
            user_id=user.id,
            region=region,
            period=period,
            bettor_name=bettor_name or None,
            selected_numbers=record_numbers,
            selected_zodiacs=record_zodiacs,
            selected_colors=record_colors,
            selected_parity=record_parity,
            odds_number=odds_number,
            odds_zodiac=odds_zodiac,
            odds_color=odds_color,
            odds_parity=odds_parity,
            stake_special=stake_special,
            stake_common=stake_common,
            total_stake=total_stake,
        )
        db.session.add(record)

    if common_total:
        record.stake_common = common_total
    if total_stake:
        record.total_stake = total_stake

    record.result_number = result_number
    record.result_zodiac = result_zodiac
    record.result_color = result_color
    record.result_parity = result_parity
    record.profit_number = profit_number
    record.profit_zodiac = profit_zodiac
    record.profit_color = profit_color
    record.profit_parity = profit_parity
    record.total_profit = total_profit
    record.special_number = special_number
    record.special_zodiac = special_zodiac
    record.special_color = special_color
    record.special_parity = special_parity
    record.total_stake = total_stake

    db.session.commit()

    return jsonify(
        {
            "success": True,
            "record_id": record.id,
            "total_profit": total_profit,
            "total_stake": total_stake,
            "special_number": special_number,
            "special_zodiac": special_zodiac,
            "special_color": special_color,
            "special_parity": special_parity,
        }
    )


@mobile_api_bp.route("/manual_bets", methods=["GET"])
def api_manual_bets_list():
    user, error = _require_active_user()
    if error:
        return error

    region, error = _validate_text_length(request.args.get("region"), "region", maximum=10)
    if error:
        return _json_error(error)
    if region and region not in {"hk", "macau"}:
        return _json_error("region is invalid")
    status = (request.args.get("status") or "").strip()
    limit = request.args.get("limit") or "20"
    try:
        limit = max(1, min(int(limit), 50))
    except ValueError:
        limit = 20

    query = ManualBetRecord.query.filter_by(user_id=user.id)
    if region:
        query = query.filter_by(region=region)

    if status == "pending":
        query = query.filter(ManualBetRecord.total_profit.is_(None))
    elif status == "settled":
        query = query.filter(ManualBetRecord.total_profit.isnot(None))

    records = query.order_by(ManualBetRecord.created_at.desc()).limit(limit).all()
    items = []
    for record in records:
        record_status = "settled" if record.total_profit is not None else "pending"
        items.append(
            {
                "id": record.id,
                "region": record.region,
                "period": record.period,
                "bettor_name": record.bettor_name or "",
                "selected_numbers": record.selected_numbers or "",
                "selected_zodiacs": record.selected_zodiacs or "",
                "selected_colors": record.selected_colors or "",
                "selected_parity": record.selected_parity or "",
                "odds_number": record.odds_number,
                "odds_zodiac": record.odds_zodiac,
                "odds_color": record.odds_color,
                "odds_parity": record.odds_parity,
                "stake_special": record.stake_special,
                "stake_common": record.stake_common,
                "total_stake": record.total_stake,
                "total_profit": record.total_profit,
                "special_number": record.special_number,
                "special_zodiac": record.special_zodiac,
                "special_color": record.special_color,
                "special_parity": record.special_parity,
                "status": record_status,
                "created_at": record.created_at.strftime("%Y-%m-%d %H:%M:%S")
                if record.created_at
                else "",
            }
        )

    return jsonify({"success": True, "items": items})


@mobile_api_bp.route("/manual_bets/summary", methods=["GET"])
def api_manual_bets_summary():
    user, error = _require_active_user()
    if error:
        return error

    region, error = _validate_text_length(request.args.get("region"), "region", maximum=10)
    if error:
        return _json_error(error)
    if region and region not in {"hk", "macau"}:
        return _json_error("region is invalid")
    query = ManualBetRecord.query.filter_by(user_id=user.id)
    if region:
        query = query.filter_by(region=region)

    settled_query = query.filter(ManualBetRecord.total_profit.isnot(None))
    pending_count = query.filter(ManualBetRecord.total_profit.is_(None)).count()
    settled_count = settled_query.count()
    totals = settled_query.with_entities(
        func.coalesce(func.sum(ManualBetRecord.total_stake), 0),
        func.coalesce(func.sum(ManualBetRecord.total_profit), 0),
    ).first()
    total_stake = float(totals[0] or 0)
    total_profit = float(totals[1] or 0)
    win_count = settled_query.filter(ManualBetRecord.total_profit > 0).count()
    lose_count = settled_query.filter(ManualBetRecord.total_profit < 0).count()
    draw_count = settled_query.filter(ManualBetRecord.total_profit == 0).count()

    return jsonify(
        {
            "success": True,
            "summary": {
                "settled_count": settled_count,
                "pending_count": pending_count,
                "total_stake": total_stake,
                "total_profit": total_profit,
                "win_count": win_count,
                "lose_count": lose_count,
                "draw_count": draw_count,
            },
        }
    )


@mobile_api_bp.route("/manual_bets/<int:record_id>", methods=["DELETE"])
def api_manual_bets_delete(record_id):
    user, error = _require_active_user()
    if error:
        return error

    record = ManualBetRecord.query.filter_by(id=record_id, user_id=user.id).first()
    if not record:
        return _json_error("record not found", status=404, code="not_found")

    try:
        db.session.delete(record)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Mobile manual bet delete failed: {e}")
        return _json_error("delete failed", status=500, code="delete_failed")

    return jsonify({"success": True})


@mobile_api_bp.route("/me", methods=["GET"])
def api_me():
    user, error = _require_user()
    if error:
        return error

    return jsonify(
        {
            "success": True,
            "csrf_token": _get_mobile_csrf_token(),
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
                "show_normal_numbers": bool(user.show_normal_numbers),
                "activation_expires_at": user.activation_expires_at.isoformat()
                if user.activation_expires_at
                else None,
            },
        }
    )


@mobile_api_bp.route("/settings/prediction-display", methods=["POST"])
def api_update_prediction_display_settings():
    user, error = _require_user()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    user.show_normal_numbers = _parse_bool_payload(payload, "show_normal_numbers")
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "message": "settings updated",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
                "show_normal_numbers": bool(user.show_normal_numbers),
                "activation_expires_at": user.activation_expires_at.isoformat()
                if user.activation_expires_at
                else None,
            },
        }
    )


def _prediction_result(record):
    if not record.is_result_updated or not record.actual_special_number:
        return "pending"
    if record.special_number == record.actual_special_number:
        return "special_hit"
    return "wrong"


def _resolve_actual_special_zodiac(record, zodiac_map_cache, draw_cache):
    if record.actual_special_zodiac:
        return record.actual_special_zodiac
    if not record.actual_special_number:
        return ""

    cache_key = (record.region, record.period)
    draw = draw_cache.get(cache_key)
    if draw is None:
        draw = LotteryDraw.query.filter_by(
            region=record.region, draw_id=record.period
        ).first()
        draw_cache[cache_key] = draw or False
    if draw:
        if draw.special_zodiac:
            return draw.special_zodiac

    zodiac_year = ZodiacSetting.get_zodiac_year_for_date(
        draw.draw_date if draw else record.created_at or datetime.now()
    )
    zodiac_map = zodiac_map_cache.get(zodiac_year)
    if zodiac_map is None:
        zodiac_map = ZodiacSetting.get_all_settings_for_year(zodiac_year) or {}
        zodiac_map_cache[zodiac_year] = zodiac_map
    if not zodiac_map:
        return ""

    try:
        return zodiac_map.get(int(record.actual_special_number), "")
    except (TypeError, ValueError):
        return ""


def _mobile_secondary_hit_expr():
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect in ("mysql", "mariadb"):
        actual_in_normal = db.func.find_in_set(
            PredictionRecord.actual_special_number,
            PredictionRecord.normal_numbers,
        ) > 0
    else:
        actual_as_string = db.cast(PredictionRecord.actual_special_number, db.String)
        actual_in_normal = db.or_(
            PredictionRecord.normal_numbers.contains(
                "," + actual_as_string + ","
            ),
            PredictionRecord.normal_numbers.startswith(
                actual_as_string + ","
            ),
            PredictionRecord.normal_numbers.endswith(
                "," + actual_as_string
            ),
        )
    zodiac_hit = db.and_(
        PredictionRecord.special_zodiac.isnot(None),
        PredictionRecord.actual_special_zodiac.isnot(None),
        PredictionRecord.special_zodiac != "",
        PredictionRecord.actual_special_zodiac != "",
        PredictionRecord.special_zodiac == PredictionRecord.actual_special_zodiac,
    )
    return db.or_(actual_in_normal, zodiac_hit)


def _build_region_summaries(user_id, region_filter=None):
    query = PredictionRecord.query.filter_by(user_id=user_id)
    if region_filter:
        query = query.filter_by(region=region_filter)
    all_predictions = query.with_entities(
        PredictionRecord.region,
        PredictionRecord.period,
        PredictionRecord.special_number,
        PredictionRecord.actual_special_number,
        PredictionRecord.is_result_updated,
        PredictionRecord.created_at,
        PredictionRecord.id,
    ).order_by(
        PredictionRecord.created_at.asc(), PredictionRecord.id.asc()
    ).all()
    
    regions = {record.region for record in all_predictions if record.region}
    
    prediction_summary_cards = []
    for r in regions:
        region_records = [record for record in all_predictions if record.region == r]
        
        period_results = OrderedDict()
        for record in region_records:
            period_key = record.period
            if period_key not in period_results:
                period_results[period_key] = {
                    'has_result': False,
                    'is_hit': False,
                }
            if record.is_result_updated and record.special_number and record.actual_special_number:
                period_results[period_key]['has_result'] = True
                if str(record.special_number).strip() == str(record.actual_special_number).strip():
                    period_results[period_key]['is_hit'] = True

        total_special_hits = 0
        consecutive_special_misses = 0
        consecutive_special_hits = 0
        max_consecutive_special_hits = 0
        max_consecutive_special_misses = 0
        resolved_periods = 0

        for result in period_results.values():
            if not result['has_result']:
                continue
            resolved_periods += 1
            if result['is_hit']:
                total_special_hits += 1
                consecutive_special_hits += 1
                consecutive_special_misses = 0
                if consecutive_special_hits > max_consecutive_special_hits:
                    max_consecutive_special_hits = consecutive_special_hits
            else:
                consecutive_special_hits = 0
                consecutive_special_misses += 1
                if consecutive_special_misses > max_consecutive_special_misses:
                    max_consecutive_special_misses = consecutive_special_misses

        accuracy = round((total_special_hits / resolved_periods * 100), 1) if resolved_periods > 0 else 0.0

        if resolved_periods < 3:
            recommendation = "样本较少，建议观望"
            level = "neutral"
        elif accuracy >= 30.0 or consecutive_special_hits >= 2:
            recommendation = "胜率极佳，建议跟入"
            level = "positive"
        elif consecutive_special_misses >= 5:
            recommendation = "连漏偏高，随时反弹"
            level = "positive"
        elif accuracy >= 15.0:
            recommendation = "胜率稳定，可以参考"
            level = "positive"
        elif accuracy < 5.0 and resolved_periods >= 10:
            recommendation = "走势低迷，暂且观望"
            level = "negative"
        else:
            recommendation = "走势震荡，谨慎参考"
            level = "warning"

        prediction_summary_cards.append({
            'region': r,
            'region_label': '香港' if r == 'hk' else '澳门' if r == 'macau' else r,
            'hit_periods': total_special_hits,
            'miss_streak': consecutive_special_misses,
            'max_hit_streak': max_consecutive_special_hits,
            'max_miss_streak': max_consecutive_special_misses,
            'resolved_periods': resolved_periods,
            'total_predictions': len(region_records),
            'accuracy': accuracy,
            'recommendation': recommendation,
            'recommendation_level': level,
        })
    
    prediction_summary_cards.sort(
        key=lambda item: 0 if item['region'] == 'hk' else 1 if item['region'] == 'macau' else 2
    )
    return prediction_summary_cards


@mobile_api_bp.route("/prediction_summaries", methods=["GET"])
def api_prediction_summaries():
    user, error = _require_active_user()
    if error:
        return error

    region, error = _validate_text_length(request.args.get("region"), "region", maximum=10)
    if error:
        return _json_error(error)
    if region and region not in {"hk", "macau"}:
        return _json_error("region is invalid")
    return jsonify(
        {
            "success": True,
            "region_summaries": _build_region_summaries(user.id, region),
        }
    )


@mobile_api_bp.route("/predictions", methods=["GET"])
def api_predictions():
    user, error = _require_active_user()
    if error:
        return error

    try:
        page = _parse_int_arg("page", 1, minimum=1, maximum=10000)
        page_size = _parse_int_arg("page_size", 20, minimum=1, maximum=100)
    except ValueError as e:
        return _json_error(str(e))
    region, error = _validate_text_length(request.args.get("region"), "region", maximum=10)
    if error:
        return _json_error(error)
    if region and region not in {"hk", "macau"}:
        return _json_error("region is invalid")
    period, error = _validate_text_length(request.args.get("period"), "period", maximum=20)
    if error:
        return _json_error(error)
    strategy, error = _validate_text_length(request.args.get("strategy"), "strategy", maximum=20)
    if error:
        return _json_error(error)
    if strategy and strategy not in STRATEGY_KEYS:
        return _json_error("strategy is invalid")
    result, error = _validate_text_length(request.args.get("result"), "result", maximum=20)
    if error:
        return _json_error(error)
    if result and result not in {"special_hit", "normal_hit", "wrong", "pending"}:
        return _json_error("result is invalid")
    include_zodiacs = request.args.get("include_zodiacs", "").strip() == "1"
    include_summaries = request.args.get("include_summaries", "1").strip() != "0"
    include_details = request.args.get("include_details", "1").strip() != "0"
    include_total = request.args.get("include_total", "1").strip() != "0"
    year_param = request.args.get("year", "").strip()

    query = PredictionRecord.query.filter_by(user_id=user.id)
    if region:
        query = query.filter_by(region=region)
    if period:
        query = query.filter(PredictionRecord.period.contains(period))
    if strategy:
        query = query.filter_by(strategy=strategy)

    if result:
        if result == "special_hit":
            query = query.filter(
                PredictionRecord.is_result_updated.is_(True),
                PredictionRecord.actual_special_number.isnot(None),
                PredictionRecord.special_number == PredictionRecord.actual_special_number,
            )
        elif result == "normal_hit":
            query = query.filter(
                PredictionRecord.is_result_updated.is_(True),
                PredictionRecord.actual_special_number.isnot(None),
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                _mobile_secondary_hit_expr(),
            )
        elif result == "wrong":
            query = query.filter(
                PredictionRecord.is_result_updated.is_(True),
                PredictionRecord.actual_special_number.isnot(None),
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                ~_mobile_secondary_hit_expr(),
            )
        elif result == "pending":
            query = query.filter(PredictionRecord.is_result_updated.is_(False))

    total = query.count() if include_total else None

    if not include_details:
        query = query.options(
            load_only(
                PredictionRecord.id,
                PredictionRecord.user_id,
                PredictionRecord.region,
                PredictionRecord.strategy,
                PredictionRecord.period,
                PredictionRecord.normal_numbers,
                PredictionRecord.special_number,
                PredictionRecord.special_zodiac,
                PredictionRecord.actual_special_number,
                PredictionRecord.actual_special_zodiac,
                PredictionRecord.accuracy_score,
                PredictionRecord.is_result_updated,
                PredictionRecord.created_at,
            )
        )

    records = (
        query.order_by(PredictionRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    zodiac_map_cache = {}
    draw_cache = {}
    if include_zodiacs:
        try:
            _ = int(year_param) if year_param else None
        except (ValueError, TypeError):
            pass

    missing_draw_keys = {
        (record.region, record.period)
        for record in records
        if record.actual_special_number and not record.actual_special_zodiac
    }
    if missing_draw_keys:
        draw_filters = [
            db.and_(LotteryDraw.region == region_key, LotteryDraw.draw_id == period_key)
            for region_key, period_key in missing_draw_keys
        ]
        if draw_filters:
            draws = LotteryDraw.query.filter(db.or_(*draw_filters)).all()
            found_draws = {
                (draw.region, draw.draw_id): draw
                for draw in draws
            }
            draw_cache.update(found_draws)
            for key in missing_draw_keys:
                draw_cache.setdefault(key, False)

    items = []
    updated_records = []
    for record in records:
        normal_numbers = record.normal_numbers.split(",") if record.normal_numbers else []
        normal_zodiacs = []
        mapped_special = record.special_zodiac
        zodiac_map = None
        if include_zodiacs:
            zodiac_year = ZodiacSetting.get_zodiac_year_for_date(
                record.created_at or datetime.now()
            )
            zodiac_map = zodiac_map_cache.get(zodiac_year)
            if zodiac_map is None:
                zodiac_map = ZodiacSetting.get_all_settings_for_year(zodiac_year) or {}
                zodiac_map_cache[zodiac_year] = zodiac_map

        if zodiac_map and normal_numbers:
            for value in normal_numbers:
                try:
                    normal_zodiacs.append(zodiac_map.get(int(value), ""))
                except (TypeError, ValueError):
                    normal_zodiacs.append("")
        if zodiac_map and record.special_number:
            try:
                mapped_special = zodiac_map.get(
                    int(record.special_number), record.special_zodiac
                )
            except (TypeError, ValueError):
                mapped_special = record.special_zodiac
        actual_special_zodiac = _resolve_actual_special_zodiac(
            record, zodiac_map_cache, draw_cache
        )
        if actual_special_zodiac and not record.actual_special_zodiac:
            record.actual_special_zodiac = actual_special_zodiac
            updated_records.append(record)
        item = {
            "id": record.id,
            "region": record.region,
            "strategy": record.strategy,
            "period": record.period,
            "normal_numbers": normal_numbers,
            "normal_zodiacs": normal_zodiacs,
            "special_number": record.special_number,
            "special_zodiac": mapped_special,
            "actual_special_number": record.actual_special_number,
            "actual_special_zodiac": actual_special_zodiac,
            "accuracy_score": record.accuracy_score,
            "is_result_updated": record.is_result_updated,
            "result": _prediction_result(record),
            "created_at": record.created_at.isoformat()
            if record.created_at
            else None,
        }
        if include_details:
            item["prediction_text"] = _hydrate_mobile_prediction_text(record)
            item["prediction_metadata"] = _hydrate_mobile_prediction_metadata(record)
        items.append(item)

    if updated_records:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return jsonify(
        {
            "success": True,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
            "region_summaries": (
                _build_region_summaries(user.id, region) if include_summaries else []
            ),
        }
    )


def _calculate_accuracy(query):
    base_query = query.filter(
        PredictionRecord.is_result_updated.is_(True),
        PredictionRecord.actual_special_number.isnot(None),
    )

    rows = base_query.with_entities(
        PredictionRecord.special_number,
        PredictionRecord.actual_special_number,
        PredictionRecord.normal_numbers,
        PredictionRecord.special_zodiac,
        PredictionRecord.actual_special_zodiac,
    ).all()

    total = len(rows)
    special_hits = 0
    normal_hits = 0
    for row in rows:
        special_number = str(row.special_number or "").strip()
        actual_special = str(row.actual_special_number or "").strip()
        if not actual_special:
            continue
        if special_number == actual_special:
            special_hits += 1
            continue

        normal_numbers = {
            item.strip()
            for item in str(row.normal_numbers or "").split(",")
            if item.strip()
        }
        zodiac_hit = (
            bool(row.special_zodiac)
            and bool(row.actual_special_zodiac)
            and str(row.special_zodiac).strip() == str(row.actual_special_zodiac).strip()
        )
        if actual_special in normal_numbers or zodiac_hit:
            normal_hits += 1

    correct = special_hits + normal_hits
    special_hit_rate = round((special_hits / total) * 100, 1) if total else 0.0
    normal_hit_rate = round((normal_hits / total) * 100, 1) if total else 0.0
    combined_accuracy = round((correct / total) * 100, 1) if total else 0.0

    return {
        "total": total,
        "special_hits": special_hits,
        "normal_hits": normal_hits,
        "correct": correct,
        "accuracy": special_hit_rate,
        "special_hit_rate": special_hit_rate,
        "normal_hit_rate": normal_hit_rate,
        "combined_accuracy": combined_accuracy,
    }

def _strategy_config(region, strategy):
    raw = SystemConfig.get_config(f"strategy_config_{region}_{strategy}", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

def _calculate_accuracy_window(query, limit):
    ids = [item.id for item in query.order_by(PredictionRecord.created_at.desc()).limit(limit).all()]
    limited = PredictionRecord.query.filter(PredictionRecord.id.in_(ids)) if ids else PredictionRecord.query.filter(PredictionRecord.id == -1)
    summary = _calculate_accuracy(limited)
    summary["window"] = limit
    return summary

def _build_mobile_backtests(user_id):
    backtests = {}
    ranked = []
    for strategy in LOCAL_STRATEGIES:
        base_query = PredictionRecord.query.filter_by(user_id=user_id, strategy=strategy)
        windows = [_calculate_accuracy_window(base_query, window) for window in (20, 50, 100)]
        backtests[strategy] = windows

        weighted = []
        samples = 0
        for idx, item in enumerate(windows):
            if item["total"] <= 0:
                continue
            weight = max(0.4, 1.0 - idx * 0.2)
            confidence = min(1.0, item["total"] / 10.0)
            weighted.append(item["accuracy"] * weight * confidence)
            samples = max(samples, item["total"])
        if weighted:
            ranked.append({
                "strategy": strategy,
                "score": round(sum(weighted) / len(weighted), 2),
                "samples": samples,
            })
    ranked.sort(key=lambda item: (item["score"], item["samples"]), reverse=True)
    best = ranked[0] if ranked else {"strategy": "hybrid", "score": 0.0, "samples": 0}
    return backtests, best

def _learning_summary():
    payload = {}
    for region in ("hk", "macau"):
        region_data = {}
        for strategy in ["hot", "cold", "trend", "hybrid", "balanced", "markov", "ml"]:
            config = _strategy_config(region, strategy)
            if not config:
                continue
            region_data[strategy] = {
                "window": config.get("window"),
                "pool": config.get("pool"),
                "special_pool": config.get("special_pool"),
                "transition_decay": config.get("transition_decay"),
                "source_special_weight": config.get("source_special_weight"),
                "profile_learning_confidence": round(float(config.get("profile_learning_confidence") or 0.0) * 100, 1),
                "profile_learning_samples": config.get("profile_learning_samples", 0),
                "last_accuracy": round(float(config.get("last_accuracy") or 0.0) * 100, 1),
                "last_total": config.get("last_total", 0),
            }
        payload[region] = region_data
    return payload


@mobile_api_bp.route("/accuracy", methods=["GET"])
def api_accuracy():
    user, error = _require_active_user()
    if error:
        return error

    base_query = PredictionRecord.query.filter_by(user_id=user.id)
    overall = _calculate_accuracy(base_query)

    strategy_stats = {}
    for key in STRATEGY_KEYS:
        strategy_stats[key] = _calculate_accuracy(base_query.filter_by(strategy=key))
    backtests, best_strategy = _build_mobile_backtests(user.id)

    return jsonify(
        {
            "success": True,
            "overall": overall,
            "by_strategy": strategy_stats,
            "recommended_strategy": best_strategy,
            "backtests": backtests,
            "learning_summary": _learning_summary(),
        }
    )
