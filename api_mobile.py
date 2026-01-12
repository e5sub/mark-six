from datetime import datetime

from flask import Blueprint, jsonify, request, session
from sqlalchemy import func, case

from models import ActivationCode, InviteCode, PredictionRecord, User, db


mobile_api_bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile")

STRATEGY_KEYS = ["hot", "cold", "trend", "hybrid", "balanced", "random", "ai"]


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

    items = []
    for record in records:
        items.append(
            {
                "id": record.id,
                "region": record.region,
                "strategy": record.strategy,
                "period": record.period,
                "normal_numbers": record.normal_numbers.split(",")
                if record.normal_numbers
                else [],
                "special_number": record.special_number,
                "special_zodiac": record.special_zodiac,
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
