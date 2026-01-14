from datetime import datetime

from flask import Blueprint, jsonify, request, session
from sqlalchemy import func, case, or_

from models import (
    ActivationCode,
    InviteCode,
    LotteryDraw,
    ManualBetRecord,
    PredictionRecord,
    User,
    ZodiacSetting,
    db,
)


mobile_api_bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile")

STRATEGY_KEYS = ["hot", "cold", "trend", "hybrid", "balanced", "random", "ai"]
_RED_BALLS = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
_BLUE_BALLS = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}


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
            if number > 0 and amount > 0:
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
            if number > 0 and amount > 0:
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
            if number > 0 and amount > 0:
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
            if key and amount > 0:
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
    if stake <= 0:
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


def _get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def _require_user():
    user = _get_current_user()
    if not user:
        return None, _json_error("authentication required", status=401, code="auth_required")
    return user, None


@mobile_api_bp.route("/register", methods=["POST"])
def api_register():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    confirm_password = payload.get("confirm_password") or ""
    invite_code = (payload.get("invite_code") or "").strip()

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

    db.session.commit()

    message = "registered successfully"
    if not invite_success:
        message = "registered successfully, activation required"

    return jsonify(
        {
            "success": True,
            "message": message,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
            },
        }
    )


@mobile_api_bp.route("/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or {}
    username_or_email = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username_or_email or not password:
        return _json_error("missing username/email or password")

    user = User.query.filter(
        (User.username == username_or_email) | (User.email == username_or_email)
    ).first()
    if not user or not user.check_password(password):
        return _json_error("invalid credentials", status=401, code="invalid_credentials")

    user.last_login = datetime.utcnow()
    user.login_count = (user.login_count or 0) + 1
    db.session.commit()

    session["user_id"] = user.id
    session["username"] = user.username
    session["is_admin"] = user.is_admin
    session["is_active"] = user.is_active

    return jsonify(
        {
            "success": True,
            "message": "login successful",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
            },
        }
    )


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

    if user.is_active:
        return jsonify({"success": True, "message": "already activated", "is_active": True})

    payload = request.get_json(silent=True) or {}
    activation_code = (payload.get("activation_code") or "").strip()
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


@mobile_api_bp.route("/manual_bets", methods=["POST"])
def api_manual_bets():
    user, error = _require_user()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    settle = payload.get("settle", True)
    record_id = payload.get("record_id")
    if record_id is not None:
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            return _json_error("record_id is invalid")
    region = (payload.get("region") or "hk").strip()
    period = (payload.get("period") or "").strip()
    bettor_name = (payload.get("bettor_name") or "").strip()
    if not period:
        return _json_error("period is required")

    bet_number = bool(payload.get("bet_number"))
    bet_zodiac = bool(payload.get("bet_zodiac"))
    bet_color = bool(payload.get("bet_color"))
    bet_parity = bool(payload.get("bet_parity"))

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

    stake_special = float(payload.get("stake_special") or 0)
    stake_common = float(payload.get("stake_common") or 0)
    odds_number = float(payload.get("odds_number") or 0)
    odds_zodiac = float(payload.get("odds_zodiac") or 0)
    odds_color = float(payload.get("odds_color") or 0)
    odds_parity = float(payload.get("odds_parity") or 0)

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
                ManualBetRecord.total_profit.is_(None)
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
            existing = merge_query.first()
            if existing and not any([
                (existing.selected_zodiacs or "").strip(),
                (existing.selected_colors or "").strip(),
                (existing.selected_parity or "").strip(),
            ]) and float(existing.odds_number or 0) == odds_number:
                existing_stakes = _parse_number_stakes(existing.selected_numbers)
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
                ManualBetRecord.total_profit.is_(None)
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
            existing = merge_query.first()
            if existing and not any([
                (existing.selected_numbers or "").strip(),
                (existing.selected_colors or "").strip(),
                (existing.selected_parity or "").strip(),
            ]) and float(existing.odds_zodiac or 0) == odds_zodiac:
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
                ManualBetRecord.total_profit.is_(None)
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
            existing = merge_query.first()
            if existing and not any([
                (existing.selected_numbers or "").strip(),
                (existing.selected_zodiacs or "").strip(),
                (existing.selected_parity or "").strip(),
            ]) and float(existing.odds_color or 0) == odds_color:
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
                ManualBetRecord.total_profit.is_(None)
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
            existing = merge_query.first()
            if existing and not any([
                (existing.selected_numbers or "").strip(),
                (existing.selected_zodiacs or "").strip(),
                (existing.selected_colors or "").strip(),
            ]) and float(existing.odds_parity or 0) == odds_parity:
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
    user, error = _require_user()
    if error:
        return error

    region = (request.args.get("region") or "").strip()
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
    user, error = _require_user()
    if error:
        return error

    region = (request.args.get("region") or "").strip()
    query = ManualBetRecord.query.filter_by(user_id=user.id)
    if region:
        query = query.filter_by(region=region)

    records = query.all()
    settled = [r for r in records if r.total_profit is not None]
    pending = [r for r in records if r.total_profit is None]

    total_stake = sum((r.total_stake or 0) for r in settled)
    total_profit = sum((r.total_profit or 0) for r in settled)
    win_count = sum(1 for r in settled if (r.total_profit or 0) > 0)
    lose_count = sum(1 for r in settled if (r.total_profit or 0) < 0)
    draw_count = sum(1 for r in settled if (r.total_profit or 0) == 0)

    return jsonify(
        {
            "success": True,
            "summary": {
                "settled_count": len(settled),
                "pending_count": len(pending),
                "total_stake": total_stake,
                "total_profit": total_profit,
                "win_count": win_count,
                "lose_count": lose_count,
                "draw_count": draw_count,
            },
        }
    )


@mobile_api_bp.route("/me", methods=["GET"])
def api_me():
    user, error = _require_user()
    if error:
        return error

    return jsonify(
        {
            "success": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
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
    normal_numbers = record.normal_numbers.split(",") if record.normal_numbers else []
    if record.actual_special_number in normal_numbers:
        return "normal_hit"
    return "wrong"


@mobile_api_bp.route("/predictions", methods=["GET"])
def api_predictions():
    user, error = _require_user()
    if error:
        return error

    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 20)), 1), 100)
    region = request.args.get("region", "").strip()
    period = request.args.get("period", "").strip()
    strategy = request.args.get("strategy", "").strip()
    result = request.args.get("result", "").strip()
    include_zodiacs = request.args.get("include_zodiacs", "").strip() == "1"
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
                db.or_(
                    PredictionRecord.normal_numbers.contains(
                        "," + db.cast(PredictionRecord.actual_special_number, db.String) + ","
                    ),
                    PredictionRecord.normal_numbers.startswith(
                        db.cast(PredictionRecord.actual_special_number, db.String) + ","
                    ),
                    PredictionRecord.normal_numbers.endswith(
                        "," + db.cast(PredictionRecord.actual_special_number, db.String)
                    ),
                ),
            )
        elif result == "wrong":
            query = query.filter(
                PredictionRecord.is_result_updated.is_(True),
                PredictionRecord.actual_special_number.isnot(None),
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                ~db.or_(
                    PredictionRecord.normal_numbers.contains(
                        "," + db.cast(PredictionRecord.actual_special_number, db.String) + ","
                    ),
                    PredictionRecord.normal_numbers.startswith(
                        db.cast(PredictionRecord.actual_special_number, db.String) + ","
                    ),
                    PredictionRecord.normal_numbers.endswith(
                        "," + db.cast(PredictionRecord.actual_special_number, db.String)
                    ),
                ),
            )
        elif result == "pending":
            query = query.filter(PredictionRecord.is_result_updated.is_(False))

    total = query.count()
    records = (
        query.order_by(PredictionRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    zodiac_map_cache = {}
    if include_zodiacs:
        try:
            _ = int(year_param) if year_param else None
        except (ValueError, TypeError):
            pass

    items = []
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
        items.append(
            {
                "id": record.id,
                "region": record.region,
                "strategy": record.strategy,
                "period": record.period,
                "normal_numbers": normal_numbers,
                "normal_zodiacs": normal_zodiacs,
                "special_number": record.special_number,
                "special_zodiac": mapped_special,
                "prediction_text": record.prediction_text,
                "actual_special_number": record.actual_special_number,
                "actual_special_zodiac": record.actual_special_zodiac,
                "accuracy_score": record.accuracy_score,
                "is_result_updated": record.is_result_updated,
                "result": _prediction_result(record),
                "created_at": record.created_at.isoformat()
                if record.created_at
                else None,
            }
        )

    return jsonify(
        {
            "success": True,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
        }
    )


def _calculate_accuracy(query):
    base_query = query.filter(
        PredictionRecord.is_result_updated.is_(True),
        PredictionRecord.actual_special_number.isnot(None),
    )

    special_hit_expr = case(
        (PredictionRecord.special_number == PredictionRecord.actual_special_number, 1),
        else_=0,
    )
    normal_hit_expr = case(
        (
            db.and_(
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                db.or_(
                    PredictionRecord.normal_numbers.contains(
                        "," + db.cast(PredictionRecord.actual_special_number, db.String) + ","
                    ),
                    PredictionRecord.normal_numbers.startswith(
                        db.cast(PredictionRecord.actual_special_number, db.String) + ","
                    ),
                    PredictionRecord.normal_numbers.endswith(
                        "," + db.cast(PredictionRecord.actual_special_number, db.String)
                    ),
                ),
            ),
            1,
        ),
        else_=0,
    )

    agg = base_query.with_entities(
        func.count().label("total"),
        func.sum(special_hit_expr).label("special_hits"),
        func.sum(normal_hit_expr).label("normal_hits"),
    ).first()

    total = agg.total or 0
    special_hits = agg.special_hits or 0
    normal_hits = agg.normal_hits or 0
    correct = special_hits + normal_hits
    accuracy = round((correct / total) * 100, 1) if total else 0.0

    return {
        "total": total,
        "special_hits": special_hits,
        "normal_hits": normal_hits,
        "correct": correct,
        "accuracy": accuracy,
    }


@mobile_api_bp.route("/accuracy", methods=["GET"])
def api_accuracy():
    user, error = _require_user()
    if error:
        return error

    base_query = PredictionRecord.query.filter_by(user_id=user.id)
    overall = _calculate_accuracy(base_query)

    strategy_stats = {}
    for key in STRATEGY_KEYS:
        strategy_stats[key] = _calculate_accuracy(base_query.filter_by(strategy=key))

    return jsonify(
        {
            "success": True,
            "overall": overall,
            "by_strategy": strategy_stats,
        }
    )
