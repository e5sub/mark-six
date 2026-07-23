from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, User, PredictionRecord, SystemConfig, InviteCode, BacktestRun, UserNotification, LotteryDraw, MacauCollectedData, ZodiacSetting
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from functools import wraps
from urllib.parse import urljoin
import html
import json
import re
import requests
import urllib3
import threading
import time
from collections import OrderedDict
from notification_service import cleanup_expired_station_notifications, get_user_notification_config, save_user_notification_config
from auth import _github_login_enabled

user_bp = Blueprint('user', __name__, url_prefix='/user')

_BACKTEST_REFRESH_INTERVAL_SECONDS = 180
_ML_STATS_CACHE_TTL_SECONDS = 60
_ML_STREAK_SCAN_LIMIT = 300
_backtest_refresh_lock = threading.Lock()
_backtest_refresh_state = {}
_ml_stats_cache_lock = threading.Lock()
_ml_stats_cache = {}

MACAU_COLLECTION_NUMBER_URL = 'https://162.218.28.228:1150/bbs/113.htm'
MACAU_COLLECTION_ZODIAC_URL = 'https://162.218.28.228:1150/bbs/180.htm'
MACAU_COLLECTION_URLS = {
    'numbers': MACAU_COLLECTION_NUMBER_URL,
    'zodiacs': MACAU_COLLECTION_ZODIAC_URL,
}
ZODIAC_NAMES = '鼠牛虎兔龙蛇马羊猴鸡狗猪'


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = str(data or '').strip()
        if text:
            self.parts.append(text)


def clear_user_runtime_caches():
    with _ml_stats_cache_lock:
        _ml_stats_cache.clear()
    with _backtest_refresh_lock:
        _backtest_refresh_state.clear()


def _count_distinct_prediction_periods(query):
    return query.with_entities(
        PredictionRecord.region,
        PredictionRecord.period,
    ).distinct().count()


def _current_lunar_year_special_stats():
    """Build homepage special-number statistics from the locally saved draw history."""
    lunar_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
    region_labels = (("macau", "澳门"), ("hk", "香港"))
    zodiac_names = list("鼠牛虎兔龙蛇马羊猴鸡狗猪")
    color_labels = (("red", "红波"), ("blue", "蓝波"), ("green", "绿波"))
    result = []

    for region, label in region_labels:
        number_counts = {number: 0 for number in range(1, 50)}
        zodiac_counts = {zodiac: 0 for zodiac in zodiac_names}
        color_counts = {color: 0 for color, _ in color_labels}
        parity_counts = {"单": 0, "双": 0}
        size_counts = {"大": 0, "小": 0}
        total = 0

        draws = LotteryDraw.query.filter_by(region=region).order_by(
            LotteryDraw.draw_date.desc(), LotteryDraw.draw_id.desc()
        ).all()
        for draw in draws:
            if ZodiacSetting.get_zodiac_year_for_date(draw.draw_date) != lunar_year:
                continue
            try:
                number = int(str(draw.special_number or "").strip())
            except (TypeError, ValueError):
                continue
            if not 1 <= number <= 49:
                continue

            total += 1
            number_counts[number] += 1
            zodiac = str(draw.special_zodiac or "").strip() or (
                ZodiacSetting.get_zodiac_for_number(lunar_year, number) or ""
            )
            if zodiac in zodiac_counts:
                zodiac_counts[zodiac] += 1
            color = get_number_color(number)
            if color in color_counts:
                color_counts[color] += 1
            parity_counts["双" if number % 2 == 0 else "单"] += 1
            size_counts["大" if number >= 25 else "小"] += 1

        result.append({
            "key": region,
            "label": label,
            "total": total,
            "numbers": [
                {
                    "number": number,
                    "count": count,
                    "color": get_number_color(number),
                    "percentage": round(count / total * 100, 1) if total else 0,
                }
                for number, count in number_counts.items()
            ],
            "zodiacs": [
                {"name": name, "count": count, "percentage": round(count / total * 100, 1) if total else 0}
                for name, count in zodiac_counts.items()
            ],
            "colors": [
                {
                    "key": color,
                    "name": name,
                    "count": color_counts[color],
                    "percentage": round(color_counts[color] / total * 100, 1) if total else 0,
                }
                for color, name in color_labels
            ],
            "parities": [
                {"name": name, "count": count, "percentage": round(count / total * 100, 1) if total else 0}
                for name, count in parity_counts.items()
            ],
            "sizes": [
                {"name": name, "count": count, "percentage": round(count / total * 100, 1) if total else 0}
                for name, count in size_counts.items()
            ],
        })
    return lunar_year, result


def _notification_event_label(event_type):
    labels = {
        'prediction_generated': '预测汇总',
        'activation_request': '激活申请',
        'admin': '管理员通知',
        'general': '系统通知',
    }
    return labels.get(event_type or 'general', '系统通知')


def _format_notification_item(item):
    raw_content = str(item.content or '')
    is_html_content = '<div' in raw_content and 'prediction-summary-notice' in raw_content
    lines = [
        str(line or '').strip()
        for line in raw_content.splitlines()
        if str(line or '').strip()
    ]
    summary = ''
    detail_lines = []
    prediction_lines = []

    for line in lines:
        if line.startswith('尊敬的'):
            continue
        if line.startswith('系统已为您生成'):
            summary = line
            continue
        if '预测:' in line:
            name, detail = line.split('预测:', 1)
            prediction_lines.append({
                'name': f'{name.strip()}预测',
                'detail': detail.strip(),
            })
            continue
        detail_lines.append(line)

    if not summary and detail_lines:
        summary = detail_lines.pop(0)

    title = str(item.title or '').strip()
    site_name = SystemConfig.get_config('site_name', '彩研所')
    for removable in (site_name, '彩研所', '六合彩'):
        if removable:
            title = title.replace(removable, '')
    title = re.sub(r'\s*[-－—|｜]\s*', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip(' -－—|｜')

    if is_html_content:
        summary = ''
        detail_lines = []
        prediction_lines = []

    return {
        'item': item,
        'title': title or item.title,
        'event_label': _notification_event_label(item.event_type),
        'summary': summary,
        'details': detail_lines,
        'predictions': prediction_lines,
        'html_content': raw_content if is_html_content else '',
    }


STRATEGY_META = [
    {"key": "hot", "label": "热门", "icon": "🔥"},
    {"key": "cold", "label": "冷门", "icon": "🧊"},
    {"key": "trend", "label": "走势", "icon": "📈"},
    {"key": "hybrid", "label": "综合", "icon": "♻️"},
    {"key": "balanced", "label": "均衡", "icon": "⚖️"},
    {"key": "ml", "label": "机器学习", "icon": "🧪"},
    {"key": "ai", "label": "AI", "icon": "🤖"},
]
STRATEGY_META.insert(5, {"key": "markov", "label": "马尔科夫", "icon": "🔗"})
STRATEGY_KEYS = [item["key"] for item in STRATEGY_META]
AUTO_STRATEGY_META = [item for item in STRATEGY_META if item["key"] != "ai"]

LOCAL_STRATEGIES = ["hot", "cold", "trend", "hybrid", "balanced", "markov", "ml"]

MARKOV_LEARNING_DEFAULT_CONFIG = {
    "window": 80,
    "pool": 16,
    "special_pool": 9,
    "transition_decay": 0.985,
    "transition_min_samples": 3,
    "source_special_weight": 1.28,
    "promotion_strength": "hold",
    "profile_learning_confidence": 0.0,
    "profile_learning_samples": 0,
    "last_accuracy": 0.0,
    "last_total": 0,
    "prev_accuracy": 0.0,
    "prev_total": 0,
    "accuracy_delta": 0.0,
    "weights": {
        "transition": 1.35,
        "second_order": 0.72,
        "phase_transition": 0.55,
        "attribute_transition": 0.42,
        "failure": 0.48,
        "feedback": 0.85,
    },
}

def _strategy_label_map():
    return {item["key"]: item["label"] for item in STRATEGY_META}

def _get_prediction_display_info(prediction):
    return {
        "key": prediction.strategy,
        "label": _strategy_label_map().get(prediction.strategy, prediction.strategy)
    }


def _format_datetime_ymdhm(value):
    return value.strftime("%Y-%m-%d %H:%M")


def _compute_next_hk_draw_time(now=None):
    now = now or datetime.now()
    draw_hour = 21
    draw_minute = 32
    draw_days = {1, 3, 5}
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
    draw_time = datetime(now.year, now.month, now.day, 21, 32)
    return draw_time if now < draw_time else draw_time + timedelta(days=1)


def _get_next_draw_time_label(region):
    normalized_region = str(region or "").strip().lower()
    now = datetime.now()
    if normalized_region == "hk":
        cached = SystemConfig.get_config("hk_next_draw_time", "").strip()
        return cached or _format_datetime_ymdhm(_compute_next_hk_draw_time(now))
    if normalized_region == "macau":
        return _format_datetime_ymdhm(_compute_next_macau_draw_time(now))
    return ""


def _sanitize_auto_prediction_strategies(user):
    raw = str(getattr(user, "auto_prediction_strategies", "") or "").strip()
    if not raw:
        return False
    allowed = {meta["key"] for meta in AUTO_STRATEGY_META}
    cleaned = [item for item in raw.split(",") if item in allowed]
    if not cleaned:
        cleaned = list(LOCAL_STRATEGIES)
    cleaned_csv = ",".join(cleaned)
    if cleaned_csv == raw:
        return False
    user.auto_prediction_strategies = cleaned_csv
    return True

def _strategy_config(region, strategy):
    raw = SystemConfig.get_config(f"strategy_config_{region}_{strategy}", "")
    if not raw:
        if strategy == "markov":
            return dict(MARKOV_LEARNING_DEFAULT_CONFIG)
        return {}
    try:
        parsed = json.loads(raw)
        if strategy == "markov":
            merged = dict(MARKOV_LEARNING_DEFAULT_CONFIG)
            merged.update(parsed if isinstance(parsed, dict) else {})
            return merged
        return parsed
    except Exception:
        if strategy == "markov":
            return dict(MARKOV_LEARNING_DEFAULT_CONFIG)
        return {}


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


def _hydrate_user_prediction_model_meta(prediction, prediction_data=None):
    metadata = _deserialize_prediction_metadata(
        getattr(prediction, 'prediction_metadata', '')
    )
    if getattr(prediction, 'strategy', '') != 'ml':
        return metadata

    try:
        from app import _get_prediction_data, _hydrate_prediction_model_meta

        data = prediction_data
        if data is None:
            data, _ = _get_prediction_data(
                getattr(prediction, 'region', ''),
                datetime.now().year,
            )
        return _hydrate_prediction_model_meta(
            getattr(prediction, 'strategy', ''),
            metadata,
            data,
            getattr(prediction, 'region', ''),
        )
    except Exception as e:
        print(f"用户侧补齐机器学习预测诊断信息失败: {e}")
        return metadata


def _hydrate_user_prediction_text(prediction, prediction_data=None):
    text = getattr(prediction, 'prediction_text', '') or ''
    if getattr(prediction, 'strategy', '') != 'ml':
        return text

    try:
        from app import (
            _get_prediction_data,
            _hydrate_prediction_recommendation_text,
        )

        data = prediction_data
        if data is None:
            data, _ = _get_prediction_data(
                getattr(prediction, 'region', ''),
                datetime.now().year,
            )
        metadata = _deserialize_prediction_metadata(
            getattr(prediction, 'prediction_metadata', '')
        )
        return _hydrate_prediction_recommendation_text(
            getattr(prediction, 'strategy', ''),
            text,
            data,
            getattr(prediction, 'region', ''),
            special_number=getattr(prediction, 'special_number', ''),
            normal_numbers=getattr(prediction, 'normal_numbers', ''),
            existing_meta=metadata,
        )
    except Exception as e:
        print(f"用户侧补齐机器学习预测文案失败: {e}")
        return text


def _translate_ml_runtime_profile(value):
    mapping = {
        "base": "标准模式",
        "recent_bias": "更看近期走势",
        "context_bias": "更看号码属性",
        "recency_trim": "少看复杂走势",
        "regularized": "更稳一点",
        "blend_search": "多种算法混合试算",
        "learned_feature_bias": "学习偏好模式",
    }
    key = str(value or "").strip()
    return mapping.get(key, key or "标准模式")


def _translate_ml_feature_profile(value):
    mapping = {
        "full": "综合参考全部因素",
        "compact_attributes": "少看波色生肖单双",
        "compact_structure": "少看整体结构",
        "compact_recency": "少看近期走势",
    }
    key = str(value or "").strip()
    return mapping.get(key, key or "综合参考全部因素")


def _translate_ml_promotion_strength(value):
    mapping = {
        "hold": "继续观察",
        "watch": "重点观察",
        "promoted": "已作为常用设置",
    }
    key = str(value or "").strip()
    return mapping.get(key, key or "观察中")


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _effective_markov_promotion_strength(item):
    strength = str((item or {}).get("promotion_strength") or (item or {}).get("strength") or "hold").strip() or "hold"
    top1 = _safe_float((item or {}).get("promotion_backtest_top1", (item or {}).get("top1_hit_rate")), 0.0)
    top6 = _safe_float((item or {}).get("promotion_backtest_top6", (item or {}).get("top6_hit_rate")), 0.0)
    rank = int(_safe_float((item or {}).get("promotion_backtest_rank", (item or {}).get("backtest_rank")), 0))
    score = top1 + top6 * 0.35
    if top1 <= 0.0 or score < 1.0 or rank >= 6:
        return "hold"
    return strength


def _decorate_ml_config_snapshot(config):
    snapshot = dict(config or {})
    snapshot["primary_runtime_profile_label"] = _translate_ml_runtime_profile(
        snapshot.get("primary_runtime_profile")
    )
    snapshot["primary_feature_profile_label"] = _translate_ml_feature_profile(
        snapshot.get("primary_feature_profile")
    )
    snapshot["promotion_strength_label"] = _translate_ml_promotion_strength(
        snapshot.get("promotion_strength")
    )

    history_items = []
    for item in list(snapshot.get("promotion_history") or [])[:8]:
        history_copy = dict(item or {})
        history_copy["strength_label"] = _translate_ml_promotion_strength(
            history_copy.get("strength")
        )
        history_copy["runtime_profile_label"] = _translate_ml_runtime_profile(
            history_copy.get("runtime_profile")
        )
        history_copy["feature_profile_label"] = _translate_ml_feature_profile(
            history_copy.get("feature_profile")
        )
        history_items.append(history_copy)
    snapshot["promotion_history"] = history_items
    snapshot["latest_promotion_time"] = (
        history_items[0].get("timestamp") if history_items else snapshot.get("promoted_at", "")
    )
    return snapshot

def _decorate_markov_config_snapshot(config):
    snapshot = dict(config or {})
    snapshot["promotion_strength_label"] = _translate_ml_promotion_strength(
        _effective_markov_promotion_strength(snapshot)
    )

    history_items = []
    for item in list(snapshot.get("promotion_history") or [])[:8]:
        history_copy = dict(item or {})
        history_copy["strength_label"] = _translate_ml_promotion_strength(
            _effective_markov_promotion_strength(history_copy)
        )
        preferred = dict(history_copy.get("preferred") or {})
        parts = []
        if preferred.get("window"):
            parts.append(f"参考{preferred.get('window')}期")
        if preferred.get("transition_decay"):
            parts.append(f"近期偏重{preferred.get('transition_decay')}")
        if preferred.get("source_special_weight"):
            parts.append(f"特号权重{preferred.get('source_special_weight')}")
        history_copy["profile_label"] = " / ".join(parts) or "马尔科夫配置"
        history_items.append(history_copy)
    snapshot["promotion_history"] = history_items
    return snapshot

def _actual_in_normal_expr():
    try:
        dialect = db.engine.dialect.name
    except Exception:
        dialect = ''
    if dialect in ('mysql', 'mariadb'):
        return db.func.find_in_set(
            PredictionRecord.actual_special_number,
            PredictionRecord.normal_numbers
        ) > 0

    actual_as_string = db.cast(PredictionRecord.actual_special_number, db.String)
    return db.or_(
        PredictionRecord.normal_numbers.contains(',' + actual_as_string + ','),
        PredictionRecord.normal_numbers.startswith(actual_as_string + ','),
        PredictionRecord.normal_numbers.endswith(',' + actual_as_string)
    )

def _zodiac_hit_expr():
    return db.and_(
        PredictionRecord.special_zodiac != None,
        PredictionRecord.actual_special_zodiac != None,
        PredictionRecord.special_zodiac != '',
        PredictionRecord.actual_special_zodiac != '',
        PredictionRecord.special_zodiac == PredictionRecord.actual_special_zodiac
    )

def _secondary_hit_expr():
    return db.or_(_actual_in_normal_expr(), _zodiac_hit_expr())


def _count_missed_prediction_periods(query):
    hit_expr = db.case(
        (
            PredictionRecord.special_number == PredictionRecord.actual_special_number,
            1,
        ),
        else_=0,
    )
    return query.filter(
        PredictionRecord.is_result_updated == True,
        PredictionRecord.actual_special_number != None,
    ).with_entities(
        PredictionRecord.region,
        PredictionRecord.period,
    ).group_by(
        PredictionRecord.region,
        PredictionRecord.period,
    ).having(
        db.func.sum(hit_expr) == 0
    ).count()


def _calculate_accuracy_summary(query):
    base_query = query.filter(
        PredictionRecord.is_result_updated.is_(True),
        PredictionRecord.actual_special_number != None
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
        special_number = str(row.special_number or '').strip()
        actual_special = str(row.actual_special_number or '').strip()
        if not actual_special:
            continue
        if special_number == actual_special:
            special_hits += 1
            continue

        normal_numbers = {
            item.strip()
            for item in str(row.normal_numbers or '').split(',')
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

    return {
        "total": total,
        "special_hits": special_hits,
        "normal_hits": normal_hits,
        "correct": correct,
        "accuracy": round((special_hits / total) * 100, 1) if total else 0.0,
        "special_hit_rate": round((special_hits / total) * 100, 1) if total else 0.0,
        "normal_hit_rate": round((normal_hits / total) * 100, 1) if total else 0.0,
    }

def _calculate_accuracy_window(query, limit):
    ids = [item.id for item in query.order_by(PredictionRecord.created_at.desc()).limit(limit).all()]
    limited = PredictionRecord.query.filter(PredictionRecord.id.in_(ids)) if ids else PredictionRecord.query.filter(PredictionRecord.id == -1)
    summary = _calculate_accuracy_summary(limited)
    summary["window"] = limit
    return summary

def _strategy_backtests(user_id):
    windows = [20, 50, 100]
    backtests = {}
    ranked = []
    labels = _strategy_label_map()
    for strategy in LOCAL_STRATEGIES:
        base_query = PredictionRecord.query.filter_by(user_id=user_id, strategy=strategy)
        window_stats = [_calculate_accuracy_window(base_query, window) for window in windows]
        backtests[strategy] = window_stats

        weighted_values = []
        samples = 0
        for idx, item in enumerate(window_stats):
            if item["total"] <= 0:
                continue
            weight = max(0.4, 1.0 - idx * 0.2)
            confidence = min(1.0, item["total"] / 10.0)
            weighted_values.append(item["accuracy"] * weight * confidence)
            samples = max(samples, item["total"])
        if weighted_values:
            ranked.append({
                "strategy": strategy,
                "label": labels.get(strategy, strategy),
                "score": round(sum(weighted_values) / len(weighted_values), 2),
                "samples": samples
            })

    ranked.sort(key=lambda item: (item["score"], item["samples"]), reverse=True)
    best = ranked[0] if ranked else {"strategy": "hybrid", "label": labels.get("hybrid", "hybrid"), "score": 0.0, "samples": 0}
    return backtests, best, ranked[:3]

def _learning_snapshot():
    snapshots = {}
    tracked = [item["key"] for item in STRATEGY_META if item["key"] in LOCAL_STRATEGIES]
    for region in ("hk", "macau"):
        region_data = {}
        for strategy in tracked:
            config = _strategy_config(region, strategy)
            if not config:
                continue
            region_data[strategy] = {
                "window": config.get("window"),
                "pool": config.get("pool"),
                "special_pool": config.get("special_pool"),
                "trend_window": config.get("trend_window"),
                "history_window": config.get("history_window"),
                "feature_window": config.get("feature_window"),
                "evaluation_window": config.get("evaluation_window"),
                "transition_decay": config.get("transition_decay"),
                "source_special_weight": config.get("source_special_weight"),
                "preferred_markov_config": config.get("preferred_markov_config", {}),
                "preferred_feature_profiles": config.get("preferred_feature_profiles", []),
                "preferred_runtime_profiles": config.get("preferred_runtime_profiles", []),
                "profile_learning_confidence": round(float(config.get("profile_learning_confidence") or 0.0) * 100, 1),
                "profile_learning_samples": config.get("profile_learning_samples", 0),
                "primary_feature_profile": config.get("primary_feature_profile", "full"),
                "primary_runtime_profile": config.get("primary_runtime_profile", "base"),
                "promotion_strength": config.get("promotion_strength", "hold"),
                "promotion_history": config.get("promotion_history", []),
                "epochs": config.get("epochs"),
                "learning_rate": config.get("learning_rate"),
                "l2": config.get("l2"),
                "last_accuracy": round(float(config.get("last_accuracy") or 0.0) * 100, 1),
                "last_total": config.get("last_total", 0),
                "prev_accuracy": round(float(config.get("prev_accuracy") or 0.0) * 100, 1),
                "prev_total": config.get("prev_total", 0),
                "accuracy_delta": round(float(config.get("accuracy_delta") or 0.0) * 100, 1),
                "weights": config.get("weights", {}),
                "updated_at": config.get("updated_at", ""),
            }
            if strategy == "ml":
                region_data[strategy] = _decorate_ml_config_snapshot(region_data[strategy])
            elif strategy == "markov":
                region_data[strategy] = _decorate_markov_config_snapshot(region_data[strategy])
        snapshots[region] = region_data
    return snapshots

def _build_learning_comparison():
    snapshots = _learning_snapshot()
    comparisons = {}
    tracked = [item["key"] for item in STRATEGY_META if item["key"] in LOCAL_STRATEGIES]

    def _ranking_map_from_backtest(record):
        if not record:
            return {}
        try:
            payload = json.loads(record.payload or "{}")
        except Exception:
            payload = {}
        ranking = payload.get("ranking") or []
        result = {}
        for item in ranking:
            strategy = str((item or {}).get("strategy") or "").strip()
            if not strategy:
                continue
            result[strategy] = {
                "top1": float((item or {}).get("top1_hit_rate") or 0.0),
                "top6": float((item or {}).get("top6_hit_rate") or 0.0),
                "total": int((item or {}).get("total") or record.periods_evaluated or 0),
            }
        return result

    def _latest_two_backtest_records(region):
        records = (
            BacktestRun.query.filter_by(region=region)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .limit(8)
            .all()
        )
        selected = []
        seen_names = set()
        for record in records:
            name = str(record.name or "").strip()
            if name in seen_names:
                continue
            seen_names.add(name)
            try:
                payload = json.loads(record.payload or "{}")
            except Exception:
                payload = {}
            if int(record.periods_evaluated or payload.get("periods_evaluated", 0) or 0) <= 0:
                continue
            if not payload.get("ranking"):
                continue
            selected.append(record)
            if len(selected) >= 2:
                break
        if not selected:
            _kickoff_backtest_snapshot_refresh(region)
        return selected

    for region, region_data in snapshots.items():
        items = []
        backtest_records = _latest_two_backtest_records(region)
        current_backtest = _ranking_map_from_backtest(backtest_records[0] if backtest_records else None)
        previous_backtest = _ranking_map_from_backtest(backtest_records[1] if len(backtest_records) > 1 else None)
        for strategy in tracked:
            config = region_data.get(strategy)
            current_stats = current_backtest.get(strategy, {})
            previous_stats = previous_backtest.get(strategy, {})
            if not config and not current_stats:
                continue

            current_accuracy = float(
                current_stats.get("top1")
                if current_stats
                else (config or {}).get("last_accuracy") or 0.0
            )
            previous_accuracy = float(
                previous_stats.get("top1")
                if previous_stats
                else (0.0 if current_stats else (config or {}).get("prev_accuracy") or 0.0)
            )
            current_total = int(
                current_stats.get("total")
                if current_stats
                else (config or {}).get("last_total") or 0
            )
            previous_total = int(
                previous_stats.get("total")
                if previous_stats
                else (0 if current_stats else (config or {}).get("prev_total") or 0)
            )
            delta = round(current_accuracy - previous_accuracy, 1) if previous_total > 0 else 0.0

            if current_total <= 0:
                trend = "暂无样本"
                trend_class = "neutral"
            elif previous_total <= 0:
                trend = "等待上次快照" if current_stats else "刚开始学习"
                trend_class = "neutral"
            elif delta >= 0.5:
                trend = "最近变强"
                trend_class = "up"
            elif delta <= -0.5:
                trend = "最近变弱"
                trend_class = "down"
            else:
                trend = "基本持平"
                trend_class = "flat"

            items.append({
                "strategy": strategy,
                "label": _strategy_label_map().get(strategy, strategy),
                "icon": next((meta["icon"] for meta in STRATEGY_META if meta["key"] == strategy), ""),
                "current_accuracy": round(current_accuracy, 1),
                "previous_accuracy": round(previous_accuracy, 1),
                "delta": delta,
                "current_total": current_total,
                "previous_total": previous_total,
                "trend": trend,
                "trend_class": trend_class,
            })

        items.sort(key=lambda item: (item["delta"], item["current_accuracy"]), reverse=True)
        comparisons[region] = items

    return comparisons


def _visible_backtest_top_items(ranking, limit=3):
    return [
        item for item in (ranking or [])
        if str((item or {}).get("strategy") or "").strip() in LOCAL_STRATEGIES
    ][:limit]


def _latest_backtest_summary(limit=6):
    items = []
    region_order = [("hk", "香港"), ("macau", "澳门")]
    seen_ids = set()

    for region_key, region_label in region_order:
        record = (
            BacktestRun.query.filter_by(region=region_key)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .first()
        )
        if not record:
            _kickoff_backtest_snapshot_refresh(region_key)
            items.append({
                "id": None,
                "name": f"auto-{region_key}",
                "display_name": f"{region_label}历史模拟测试",
                "region": region_key,
                "region_label": region_label,
                "created_at": None,
                "periods_evaluated": 0,
                "top_items": [],
                "has_data": False,
                "status_text": "暂无历史模拟快照",
            })
            continue

        seen_ids.add(record.id)
        try:
            payload = json.loads(record.payload or "{}")
        except Exception:
            payload = {}
        ranking = payload.get("ranking") or []
        top_items = _visible_backtest_top_items(ranking)
        periods_evaluated = int(record.periods_evaluated or payload.get("periods_evaluated", 0) or 0)
        if periods_evaluated <= 0 or not top_items:
            _kickoff_backtest_snapshot_refresh(region_key)
        items.append({
            "id": record.id,
            "name": record.name,
            "display_name": f"{region_label}历史模拟测试",
            "region": record.region or payload.get("region") or "",
            "region_label": region_label,
            "created_at": record.created_at,
            "periods_evaluated": periods_evaluated,
            "top_items": top_items,
            "has_data": periods_evaluated > 0 and bool(top_items),
            "status_text": f"{periods_evaluated} 期模拟" if periods_evaluated > 0 else "样本不足，暂未形成有效历史模拟",
        })

    extra_records = (
        BacktestRun.query.order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
        .limit(limit)
        .all()
    )
    for record in extra_records:
        if record.id in seen_ids:
            continue
        try:
            payload = json.loads(record.payload or "{}")
        except Exception:
            payload = {}
        ranking = payload.get("ranking") or []
        top_items = _visible_backtest_top_items(ranking)
        periods_evaluated = int(record.periods_evaluated or payload.get("periods_evaluated", 0) or 0)
        items.append({
            "id": record.id,
            "name": record.name,
            "display_name": f"{'香港' if (record.region or payload.get('region')) == 'hk' else '澳门' if (record.region or payload.get('region')) == 'macau' else (record.region or payload.get('region') or '')}历史模拟测试",
            "region": record.region or payload.get("region") or "",
            "region_label": "香港" if (record.region or payload.get("region")) == "hk" else "澳门" if (record.region or payload.get("region")) == "macau" else (record.region or payload.get("region") or ""),
            "created_at": record.created_at,
            "periods_evaluated": periods_evaluated,
            "top_items": top_items,
            "has_data": periods_evaluated > 0 and bool(top_items),
            "status_text": f"{periods_evaluated} 期模拟" if periods_evaluated > 0 else "样本不足，暂未形成有效历史模拟",
        })
        if len(items) >= limit:
            break
    return items


def _latest_unique_backtest_summary(limit=6):
    items = []
    region_order = [("hk", "香港"), ("macau", "澳门")]

    for region_key, region_label in region_order:
        record = (
            BacktestRun.query.filter_by(region=region_key)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .first()
        )
        if not record:
            _kickoff_backtest_snapshot_refresh(region_key)
            items.append({
                "id": None,
                "name": f"auto-{region_key}",
                "display_name": f"{region_label}历史模拟测试",
                "region": region_key,
                "region_label": region_label,
                "created_at": None,
                "periods_evaluated": 0,
                "top_items": [],
                "has_data": False,
                "status_text": "暂无历史模拟快照",
            })
            continue

        try:
            payload = json.loads(record.payload or "{}")
        except Exception:
            payload = {}

        ranking = payload.get("ranking") or []
        top_items = _visible_backtest_top_items(ranking)
        periods_evaluated = int(record.periods_evaluated or payload.get("periods_evaluated", 0) or 0)

        if periods_evaluated <= 0 or not top_items:
            _kickoff_backtest_snapshot_refresh(region_key)

        items.append({
            "id": record.id,
            "name": record.name,
            "display_name": f"{region_label}历史模拟测试",
            "region": record.region or payload.get("region") or region_key,
            "region_label": region_label,
            "created_at": record.created_at,
            "periods_evaluated": periods_evaluated,
            "top_items": top_items,
            "has_data": periods_evaluated > 0 and bool(top_items),
            "status_text": f"{periods_evaluated} 期模拟" if periods_evaluated > 0 else "样本不足，暂未形成有效历史模拟",
        })

    return items[:limit]


def _kickoff_backtest_snapshot_refresh(region):
    region_key = str(region or "").strip().lower()
    if region_key not in ("hk", "macau"):
        return

    now = time.time()
    with _backtest_refresh_lock:
        state = _backtest_refresh_state.get(region_key) or {}
        if state.get("running"):
            return
        last_started = float(state.get("started_at") or 0.0)
        if now - last_started < _BACKTEST_REFRESH_INTERVAL_SECONDS:
            return
        _backtest_refresh_state[region_key] = {
            "running": True,
            "started_at": now,
        }

    def _worker():
        try:
            from app import app, refresh_auto_backtest_snapshot
            with app.app_context():
                refresh_auto_backtest_snapshot(region_key, force=True)
        except Exception as e:
            print(f"async backtest snapshot refresh failed for {region_key}: {e}")
        finally:
            with _backtest_refresh_lock:
                _backtest_refresh_state[region_key] = {
                    "running": False,
                    "started_at": time.time(),
                }

    threading.Thread(
        target=_worker,
        name=f"backtest-refresh-{region_key}",
        daemon=True,
    ).start()

def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _get_session_user():
            flash('请先登录', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def _get_session_user(clear_invalid=True):
    user_id = session.get('user_id')
    if not user_id:
        return None

    user = User.query.get(user_id)
    if user:
        return user

    if clear_invalid:
        session.pop('user_id', None)
        session.pop('is_active', None)
    return None

def active_required(f):
    """激活验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = _get_session_user()
        if not user:
            flash('请先登录', 'error')
            return redirect(url_for('auth.login'))

        is_active = user.check_and_update_activation_status()
        session['is_active'] = bool(is_active and user.is_active)
        if not session['is_active']:
            flash('请先激活账号后再使用此功能', 'warning')
            return redirect(url_for('auth.activate'))
        return f(*args, **kwargs)
    return decorated_function


def _macau_collection_full_period(year, source_period):
    year_text = str(year or '').strip()
    source_text = str(source_period or '').strip()
    if not year_text or not source_text:
        return ''
    return f"{year_text}{source_text.zfill(3)}"


def _fetch_macau_collection_html(url):
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session_obj = requests.Session()
    session_obj.trust_env = False
    response = session_obj.get(
        url,
        timeout=20,
        verify=False,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        },
    )
    response.raise_for_status()
    return response.content.decode('gb2312', errors='ignore')


def _extract_text_from_html(raw_html):
    parser = _HTMLTextExtractor()
    parser.feed(raw_html or '')
    return '\n'.join(html.unescape(part) for part in parser.parts)


def _resolve_collection_content_url(url, raw_html):
    match = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', raw_html or '', re.I)
    if not match:
        return url
    return urljoin(url, match.group(1))


def _parse_collection_items(plain_text, value_type):
    items = {}
    pattern = re.compile(r'(\d{1,3})\s*期\s*:\s*.*?【(.*?)】\s*开\s*:', re.S)
    for match in pattern.finditer(plain_text or ''):
        source_period = match.group(1).strip()
        content = match.group(2)
        if value_type == 'numbers':
            values = [
                f"{int(number):02d}"
                for number in re.findall(r'\d{1,2}', content)
                if 1 <= int(number) <= 49
            ]
        else:
            values = [
                zodiac
                for zodiac in re.findall(f'[{ZODIAC_NAMES}]', content)
                if zodiac in ZODIAC_NAMES
            ]
        if values:
            items[source_period] = values[:8]
    return items


def _collect_macau_source_data(year):
    parsed_by_type = {}
    resolved_urls = {}
    for value_type, url in MACAU_COLLECTION_URLS.items():
        outer_html = _fetch_macau_collection_html(url)
        content_url = _resolve_collection_content_url(url, outer_html)
        content_html = outer_html if content_url == url else _fetch_macau_collection_html(content_url)
        resolved_urls[value_type] = content_url
        parsed_by_type[value_type] = _parse_collection_items(
            _extract_text_from_html(content_html),
            value_type,
        )

    source_periods = sorted(
        set(parsed_by_type.get('numbers', {}).keys()) | set(parsed_by_type.get('zodiacs', {}).keys()),
        key=lambda item: int(item),
    )
    return [
        {
            'source_period': source_period,
            'period': _macau_collection_full_period(year, source_period),
            'numbers': parsed_by_type.get('numbers', {}).get(source_period, []),
            'zodiacs': parsed_by_type.get('zodiacs', {}).get(source_period, []),
        }
        for source_period in source_periods
    ], resolved_urls


def _save_macau_collection_items(year, items):
    created_count = 0
    updated_count = 0
    skipped_count = 0
    for item in items:
        period = item.get('period')
        if not period:
            continue
        record = MacauCollectedData.query.filter_by(region='macau', period=period).first()
        numbers = ','.join(item.get('numbers') or [])
        zodiacs = ','.join(item.get('zodiacs') or [])
        if record:
            changed = False
            if not str(record.numbers or '').strip() and numbers:
                record.numbers = numbers
                changed = True
            if not str(record.zodiacs or '').strip() and zodiacs:
                record.zodiacs = zodiacs
                changed = True
            if changed:
                updated_count += 1
            else:
                skipped_count += 1
            continue
        else:
            db.session.add(MacauCollectedData(
                region='macau',
                year=int(year),
                source_period=str(item.get('source_period') or ''),
                period=period,
                numbers=numbers,
                zodiacs=zodiacs,
            ))
            created_count += 1
    db.session.commit()
    return created_count, updated_count, skipped_count


def _csv_items(value):
    return [
        item.strip()
        for item in str(value or '').split(',')
        if item and item.strip()
    ]


def _get_macau_collection_actual_map(records):
    periods = [
        str(record.period or '').strip()
        for record in records
        if str(record.period or '').strip()
    ]
    if not periods:
        return {}

    draws = LotteryDraw.query.filter(
        LotteryDraw.region == 'macau',
        LotteryDraw.draw_id.in_(periods),
    ).all()

    actual_map = {}
    for draw in draws:
        zodiacs = _csv_items(draw.raw_zodiac)
        special_zodiac = str(draw.special_zodiac or '').strip()
        if len(zodiacs) >= 7 and zodiacs[-1]:
            special_zodiac = zodiacs[-1]
        actual_map[str(draw.draw_id or '').strip()] = {
            'special_number': f"{int(draw.special_number):02d}" if str(draw.special_number or '').strip().isdigit() else str(draw.special_number or '').strip(),
            'special_zodiac': special_zodiac,
            'draw_date': str(draw.draw_date or '').strip(),
        }
    return actual_map


def _enrich_macau_collection_records(records):
    actual_map = _get_macau_collection_actual_map(records)
    enriched = []
    for record in records:
        record.numbers_list = _csv_items(record.numbers)
        record.zodiacs_list = _csv_items(record.zodiacs)
        actual = actual_map.get(str(record.period or '').strip(), {})
        record.actual_special_number = actual.get('special_number', '')
        record.actual_special_zodiac = actual.get('special_zodiac', '')
        record.draw_date = actual.get('draw_date', '')
        record.has_result = bool(record.actual_special_number)
        record.number_hit = record.has_result and record.actual_special_number in record.numbers_list
        record.zodiac_hit = record.has_result and bool(record.actual_special_zodiac) and record.actual_special_zodiac in record.zodiacs_list
        record.any_hit = bool(record.number_hit or record.zodiac_hit)
        if not record.has_result:
            record.result_key = 'pending'
            record.result_label = '待开奖'
            record.result_class = 'result-pending'
        elif record.number_hit and record.zodiac_hit:
            record.result_key = 'any_hit'
            record.result_label = '双项命中'
            record.result_class = 'result-success'
        elif record.number_hit:
            record.result_key = 'number_hit'
            record.result_label = '号码命中'
            record.result_class = 'result-success'
        elif record.zodiac_hit:
            record.result_key = 'zodiac_hit'
            record.result_label = '生肖命中'
            record.result_class = 'result-partial'
        else:
            record.result_key = 'wrong'
            record.result_label = '未命中'
            record.result_class = 'result-failed'
        enriched.append(record)
    return enriched


def _macau_collection_stats(records):
    enriched = _enrich_macau_collection_records(records)
    resolved = [record for record in enriched if record.has_result]
    pending = len(enriched) - len(resolved)
    number_hits = sum(1 for record in resolved if record.number_hit)
    zodiac_hits = sum(1 for record in resolved if record.zodiac_hit)
    any_hits = sum(1 for record in resolved if record.any_hit)
    wrong = sum(1 for record in resolved if not record.any_hit)

    current_hit_streak = 0
    current_miss_streak = 0
    for record in sorted(resolved, key=lambda item: int(item.period) if str(item.period or '').isdigit() else 0, reverse=True):
        if record.any_hit:
            if current_miss_streak:
                break
            current_hit_streak += 1
        else:
            if current_hit_streak:
                break
            current_miss_streak += 1

    resolved_count = len(resolved)
    return {
        'total': len(enriched),
        'resolved': resolved_count,
        'pending': pending,
        'number_hits': number_hits,
        'zodiac_hits': zodiac_hits,
        'any_hits': any_hits,
        'wrong': wrong,
        'current_hit_streak': current_hit_streak,
        'current_miss_streak': current_miss_streak,
        'number_hit_rate': round((number_hits / resolved_count * 100), 2) if resolved_count else 0,
        'zodiac_hit_rate': round((zodiac_hits / resolved_count * 100), 2) if resolved_count else 0,
        'any_hit_rate': round((any_hits / resolved_count * 100), 2) if resolved_count else 0,
    }


def _filter_macau_collection_records(records, period='', result=''):
    filtered = list(records or [])
    if period:
        filtered = [
            record for record in filtered
            if period in str(record.period or '') or period in str(record.source_period or '')
        ]
    if result:
        if result == 'number_hit':
            filtered = [record for record in filtered if record.number_hit]
        elif result == 'zodiac_hit':
            filtered = [record for record in filtered if record.zodiac_hit]
        elif result == 'any_hit':
            filtered = [record for record in filtered if record.any_hit]
        elif result == 'wrong':
            filtered = [record for record in filtered if record.has_result and not record.any_hit]
        elif result == 'pending':
            filtered = [record for record in filtered if not record.has_result]
    return filtered


@user_bp.route('/macau-collection')
@active_required
def macau_collection():
    user = _get_session_user()
    current_year = datetime.now().year
    selected_year = request.args.get('year', current_year, type=int)
    page = max(request.args.get('page', 1, type=int), 1)
    period = request.args.get('period', '').strip()
    result = request.args.get('result', '').strip()

    all_records = MacauCollectedData.query.filter_by(region='macau', year=selected_year).order_by(
        MacauCollectedData.period.desc()
    ).all()
    enriched_records = _enrich_macau_collection_records(all_records)
    stats = _macau_collection_stats(all_records)
    filtered_records = _filter_macau_collection_records(enriched_records, period=period, result=result)
    per_page = 12
    total = len(filtered_records)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start_index = (page - 1) * per_page
    records = filtered_records[start_index:start_index + per_page]
    pagination = SimpleNamespace(
        page=page,
        pages=total_pages,
        total=total,
        has_prev=page > 1,
        has_next=page < total_pages,
        prev_num=page - 1 if page > 1 else None,
        next_num=page + 1 if page < total_pages else None,
    )
    return render_template(
        'user/macau_collection_stats.html',
        user=user,
        selected_year=selected_year,
        current_year=current_year,
        period=period,
        result=result,
        records=records,
        pagination=pagination,
        stats=stats,
        source_urls=MACAU_COLLECTION_URLS,
        get_number_color=get_number_color,
    )


@user_bp.route('/macau-collection/collect', methods=['POST'])
@active_required
def collect_macau_data():
    current_year = datetime.now().year
    year = request.form.get('year', current_year, type=int)
    if year < 2000 or year > 2100:
        flash('年份不正确，请重新输入。', 'error')
        return redirect(url_for('user.macau_collection', year=current_year))

    try:
        items, _ = _collect_macau_source_data(year)
        items = [item for item in items if item.get('period')]
        created_count, updated_count, skipped_count = _save_macau_collection_items(
            year,
            items,
        )
        flash(
            f'采集完成：共解析 {len(items)} 期，新增 {created_count} 期，更新 {updated_count} 期，未变化 {skipped_count} 期。',
            'success',
        )
    except Exception as e:
        db.session.rollback()
        flash(f'采集失败：{e}', 'error')
    return redirect(url_for('user.macau_collection', year=year))

@user_bp.route('/dashboard')
@user_bp.route('/dashboard')
@login_required
def dashboard():
    user = _get_session_user()
    if not user:
        flash('请先登录', 'error')
        return redirect(url_for('auth.login'))
    if _sanitize_auto_prediction_strategies(user):
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    strategy_backtests, recommended_strategy, top_strategies = _strategy_backtests(user.id)
    learning_snapshot = _learning_snapshot()
    learning_comparison = _build_learning_comparison()
    latest_backtests = _latest_unique_backtest_summary()
    
    user_predictions_query = PredictionRecord.query.filter_by(user_id=user.id)
    total_predictions = _count_distinct_prediction_periods(user_predictions_query)
    updated_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None
    ).count()
    updated_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None
    ).count()
    updated_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None
    ).count()
    updated_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None
    ).count()
    updated_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None
    ).count()
    recent_predictions = PredictionRecord.query.filter_by(user_id=user.id)\
        .order_by(PredictionRecord.created_at.desc()).limit(5).all()
    
    def calculate_user_accuracy(strategy=None):
        query = PredictionRecord.query.filter_by(user_id=user.id, is_result_updated=True)
        if strategy:
            query = query.filter_by(strategy=strategy)

        base_query = query.filter(PredictionRecord.actual_special_number != None)

        special_hit_expr = case(
            (PredictionRecord.special_number == PredictionRecord.actual_special_number, 1),
            else_=0
        )
        agg = base_query.with_entities(
            func.count().label('total'),
            func.sum(special_hit_expr).label('special_hits'),
        ).first()

        total_count = agg.total or 0
        if total_count == 0:
            return 0.0

        special_hits = agg.special_hits or 0
        return round((special_hits / total_count) * 100, 1)

    # 计算各策略命中率
    avg_accuracy = calculate_user_accuracy()
    strategy_accuracy = {
        meta["key"]: calculate_user_accuracy(meta["key"])
        for meta in STRATEGY_META
    }

    updated_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None
    ).count()
    special_hit_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None,
        PredictionRecord.special_number == PredictionRecord.actual_special_number
    ).count()
    normal_hit_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None,
        PredictionRecord.special_number != PredictionRecord.actual_special_number,
        _secondary_hit_expr()
    ).count()
    special_hit_rate = (special_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    normal_hit_rate = (normal_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    
    stats = {
        'total_predictions': total_predictions,
        'avg_accuracy': avg_accuracy,
        'special_hit_rate': round(special_hit_rate, 1),
        'normal_hit_rate': round(normal_hit_rate, 1),
        'recent_predictions': recent_predictions
    }
    
    return render_template('user/dashboard.html', 
                          user=user, 
                          stats=stats,
                          strategy_meta=STRATEGY_META,
                          auto_strategy_meta=AUTO_STRATEGY_META,
                          strategy_label_map=_strategy_label_map(),
                          strategy_accuracy=strategy_accuracy,
                          strategy_backtests=strategy_backtests,
                          recommended_strategy=recommended_strategy,
                          top_strategies=top_strategies,
                          learning_snapshot=learning_snapshot,
                          learning_comparison=learning_comparison,
                          latest_backtests=latest_backtests,
                          get_number_color=get_number_color,
                          get_number_zodiac=get_number_zodiac,
                          github_login_enabled=_github_login_enabled())


@user_bp.route('/data-statistics')
@login_required
def data_statistics():
    """开奖数据统计：当前农历年的特码属性分布。"""
    user = _get_session_user()
    if not user:
        flash('请先登录', 'error')
        return redirect(url_for('auth.login'))

    lunar_year, special_year_stats = _current_lunar_year_special_stats()
    return render_template(
        'user/data_statistics.html',
        lunar_year=lunar_year,
        special_year_stats=special_year_stats,
    )


@user_bp.route('/notifications')
@login_required
def notifications():
    user = _get_session_user()
    page = max(request.args.get('page', 1, type=int), 1)
    cleanup_expired_station_notifications(user.id)
    pagination = UserNotification.query.filter_by(user_id=user.id).order_by(
        UserNotification.created_at.desc()
    ).paginate(page=page, per_page=20, error_out=False)
    unread_count = UserNotification.query.filter_by(user_id=user.id, is_read=False).count()
    return render_template(
        'user/notifications.html',
        user=user,
        notifications=pagination.items,
        notification_cards=[_format_notification_item(item) for item in pagination.items],
        pagination=pagination,
        unread_count=unread_count,
    )


@user_bp.route('/notifications/<int:notification_id>/read', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    user = _get_session_user()
    notification = UserNotification.query.filter_by(
        id=notification_id,
        user_id=user.id,
    ).first_or_404()
    notification.mark_read()
    db.session.commit()
    if request.is_json:
        return jsonify({'success': True})
    return redirect(url_for('user.notifications'))


@user_bp.route('/notifications/read_all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    user = _get_session_user()
    unread_items = UserNotification.query.filter_by(user_id=user.id, is_read=False).all()
    for item in unread_items:
        item.mark_read()
    db.session.commit()
    if request.is_json:
        return jsonify({'success': True, 'updated': len(unread_items)})
    return redirect(url_for('user.notifications'))

# 号码属性计算函数
RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46]
BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48]
GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]

# 生肖映射由接口数据和 ZodiacSetting 提供

def get_number_zodiac(number):
    """
    获取号码对应的生肖
    使用 ZodiacSetting 模型获取当前年份的生肖设置
    """
    try:
        from models import ZodiacSetting
        zodiac_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
        return ZodiacSetting.get_zodiac_for_number(zodiac_year, number) or ""
    except Exception as e:
        print(f"获取号码生肖失败: {e}")
        return ""

def get_number_color(number):
    try:
        num = int(number)
        if num in RED_BALLS: return 'red'
        if num in BLUE_BALLS: return 'blue'
        if num in GREEN_BALLS: return 'green'
        return ""
    except:
        return ""

def _get_ml_zodiac_map():
    try:
        from models import ZodiacSetting
        zodiac_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
        return ZodiacSetting.get_all_settings_for_year(zodiac_year) or {}
    except Exception as e:
        print(f"加载机器学习记录生肖映射失败: {e}")
        return {}

def _build_ml_prediction_query(user_id, region='', period='', result='', start_date='', end_date=''):
    return _build_strategy_prediction_query(
        user_id,
        'ml',
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
    )


def _build_strategy_prediction_query(user_id, strategy, region='', period='', result='', start_date='', end_date=''):
    query = PredictionRecord.query.filter_by(
        user_id=user_id,
        strategy=strategy,
    )

    if region:
        query = query.filter_by(region=region)
    if period:
        query = query.filter(PredictionRecord.period.contains(period))

    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(PredictionRecord.created_at >= start_date_obj)
        except ValueError:
            raise ValueError('开始日期格式不正确')

    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            end_date_obj = end_date_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(PredictionRecord.created_at <= end_date_obj)
        except ValueError:
            raise ValueError('结束日期格式不正确')

    if result:
        if result == 'special_hit':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number == PredictionRecord.actual_special_number
            )
        elif result == 'normal_hit':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                _secondary_hit_expr()
            )
        elif result == 'wrong':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                ~_secondary_hit_expr()
            )
        elif result == 'pending':
            query = query.filter(PredictionRecord.is_result_updated == False)

    return query

def _decorate_ml_prediction(prediction):
    prediction.display_actual_special_zodiac = (
        prediction.actual_special_zodiac or ''
    ).strip()
    prediction.is_zodiac_hit = bool(
        prediction.is_result_updated
        and prediction.actual_special_number
        and prediction.special_number != prediction.actual_special_number
        and prediction.special_zodiac
        and prediction.display_actual_special_zodiac
        and prediction.special_zodiac == prediction.display_actual_special_zodiac
    )
    prediction.is_normal_number_hit = bool(
        prediction.is_result_updated
        and prediction.actual_special_number
        and prediction.special_number != prediction.actual_special_number
        and prediction.normal_numbers
        and (
            prediction.normal_numbers.startswith(prediction.actual_special_number + ',')
            or prediction.normal_numbers.endswith(',' + prediction.actual_special_number)
            or (',' + prediction.actual_special_number + ',') in prediction.normal_numbers
        )
    )
    return prediction

def _get_ml_predictions_page(user_id, page=1, region='', period='', result='', start_date='', end_date=''):
    return _get_strategy_predictions_page(
        user_id,
        'ml',
        page=page,
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
    )


def _get_strategy_predictions_page(user_id, strategy, page=1, region='', period='', result='', start_date='', end_date=''):
    query = _build_strategy_prediction_query(
        user_id,
        strategy,
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
    )

    records_per_page = 12
    total_records = query.count()
    total_pages = max(1, (total_records + records_per_page - 1) // records_per_page)
    current_page = min(max(page, 1), total_pages)
    start_index = (current_page - 1) * records_per_page

    items = query.order_by(
        PredictionRecord.created_at.desc(),
        PredictionRecord.id.desc()
    ).offset(start_index).limit(records_per_page).all()

    items = [_decorate_ml_prediction(item) for item in items]
    return SimpleNamespace(
        items=items,
        page=current_page,
        per_page=records_per_page,
        total=total_records,
        pages=total_pages,
        has_prev=current_page > 1,
        has_next=current_page < total_pages,
        prev_num=current_page - 1 if current_page > 1 else None,
        next_num=current_page + 1 if current_page < total_pages else None,
    )

def _get_ml_stats(user_id):
    cache_key = int(user_id)
    now = time.time()
    with _ml_stats_cache_lock:
        cached = _ml_stats_cache.get(cache_key)
        if cached and now - cached.get('created_at', 0) < _ML_STATS_CACHE_TTL_SECONDS:
            return cached['data']

    def _row_exact_hit(row):
        return str(row.special_number or '').strip() == str(row.actual_special_number or '').strip()

    def _row_secondary_hit(row):
        actual_special = str(row.actual_special_number or '').strip()
        if not actual_special:
            return False
        normal_numbers = {
            item.strip()
            for item in str(row.normal_numbers or '').split(',')
            if item.strip()
        }
        zodiac_hit = (
            bool(row.special_zodiac)
            and bool(row.actual_special_zodiac)
            and str(row.special_zodiac).strip() == str(row.actual_special_zodiac).strip()
        )
        return actual_special in normal_numbers or zodiac_hit

    def _load_recent_resolved_rows(region=None, limit=_ML_STREAK_SCAN_LIMIT):
        base_query = PredictionRecord.query.filter(
            PredictionRecord.user_id == user_id,
            PredictionRecord.strategy == 'ml',
            PredictionRecord.is_result_updated.is_(True),
            PredictionRecord.actual_special_number != None,
        )
        if region:
            base_query = base_query.filter(PredictionRecord.region == region)

        deduped_ids = base_query.with_entities(
            func.max(PredictionRecord.id).label('id')
        ).group_by(
            PredictionRecord.region,
            PredictionRecord.period,
            PredictionRecord.strategy,
        ).subquery()

        return PredictionRecord.query.join(
            deduped_ids,
            PredictionRecord.id == deduped_ids.c.id
        ).with_entities(
            PredictionRecord.region,
            PredictionRecord.special_number,
            PredictionRecord.actual_special_number,
            PredictionRecord.normal_numbers,
            PredictionRecord.special_zodiac,
            PredictionRecord.actual_special_zodiac,
            PredictionRecord.created_at,
            PredictionRecord.id,
        ).order_by(
            PredictionRecord.created_at.desc(),
            PredictionRecord.id.desc()
        ).limit(limit).all()

    recent_rows = _load_recent_resolved_rows()

    def _calculate_current_special_hit_streak(region=None):
        rows = recent_rows if not region else [row for row in recent_rows if row.region == region]
        streak = 0
        for row in rows:
            if _row_exact_hit(row):
                streak += 1
            else:
                break
        return streak

    def _calculate_current_miss_streak(region=None):
        rows = recent_rows if not region else [row for row in recent_rows if row.region == region]
        streak = 0
        for row in rows:
            if _row_exact_hit(row) or _row_secondary_hit(row):
                break
            streak += 1
        return streak

    actual_special = PredictionRecord.actual_special_number
    special_number = PredictionRecord.special_number
    stats_row = db.session.query(
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, ~_secondary_hit_expr()), 1), else_=0))
    ).filter(
        PredictionRecord.user_id == user_id,
        PredictionRecord.strategy == 'ml',
    ).one()

    total_ml_predictions = stats_row[0] or 0
    updated_predictions = stats_row[1] or 0
    special_hit_predictions = stats_row[2] or 0
    normal_hit_predictions = stats_row[3] or 0
    wrong_predictions = stats_row[4] or 0
    current_special_hit_streak = _calculate_current_special_hit_streak()
    current_miss_streak = _calculate_current_miss_streak()
    pending_predictions = max(total_ml_predictions - updated_predictions, 0)
    special_hit_rate = (special_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    normal_hit_rate = (normal_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0

    region_label_map = {'hk': '香港', 'macau': '澳门'}
    region_rows = db.session.query(
        PredictionRecord.region,
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, ~_secondary_hit_expr()), 1), else_=0))
    ).filter(
        PredictionRecord.user_id == user_id,
        PredictionRecord.strategy == 'ml',
        PredictionRecord.region.in_(tuple(region_label_map.keys())),
    ).group_by(PredictionRecord.region).all()

    region_stats_map = {
        row[0]: {
            'total': row[1] or 0,
            'updated': row[2] or 0,
            'special_hits': row[3] or 0,
            'normal_hits': row[4] or 0,
            'wrong_predictions': row[5] or 0,
        }
        for row in region_rows
    }
    region_ml_stats = []
    for region_key, region_label in region_label_map.items():
        region_data = region_stats_map.get(region_key, {})
        region_total = region_data.get('total', 0)
        region_updated = region_data.get('updated', 0)
        region_special_hits = region_data.get('special_hits', 0)
        region_normal_hits = region_data.get('normal_hits', 0)
        region_wrong_predictions = region_data.get('wrong_predictions', 0)
        region_ml_stats.append({
            'region': region_key,
            'label': region_label,
            'total': region_total,
            'updated': region_updated,
            'special_hits': region_special_hits,
            'normal_hits': region_normal_hits,
            'wrong_predictions': region_wrong_predictions,
            'current_special_hit_streak': _calculate_current_special_hit_streak(region_key),
            'current_miss_streak': _calculate_current_miss_streak(region_key),
            'special_hit_rate': round((region_special_hits / region_updated * 100), 2) if region_updated > 0 else 0,
            'normal_hit_rate': round((region_normal_hits / region_updated * 100), 2) if region_updated > 0 else 0,
        })

    data = {
        'total_ml_predictions': total_ml_predictions,
        'updated_predictions': updated_predictions,
        'special_hit_count': special_hit_predictions,
        'normal_hit_count': normal_hit_predictions,
        'wrong_predictions': wrong_predictions,
        'current_special_hit_streak': current_special_hit_streak,
        'current_miss_streak': current_miss_streak,
        'pending_predictions': pending_predictions,
        'special_hit_rate': round(special_hit_rate, 2),
        'normal_hit_rate': round(normal_hit_rate, 2),
        'region_ml_stats': region_ml_stats,
    }
    with _ml_stats_cache_lock:
        _ml_stats_cache[cache_key] = {
            'created_at': now,
            'data': data,
        }
    return data


def _get_strategy_stats(user_id, strategy):
    def _row_exact_hit(row):
        return str(row.special_number or '').strip() == str(row.actual_special_number or '').strip()

    def _row_secondary_hit(row):
        actual_special = str(row.actual_special_number or '').strip()
        if not actual_special:
            return False
        normal_numbers = {
            item.strip()
            for item in str(row.normal_numbers or '').split(',')
            if item.strip()
        }
        zodiac_hit = (
            bool(row.special_zodiac)
            and bool(row.actual_special_zodiac)
            and str(row.special_zodiac).strip() == str(row.actual_special_zodiac).strip()
        )
        return actual_special in normal_numbers or zodiac_hit

    def _load_recent_resolved_rows(region=None, limit=_ML_STREAK_SCAN_LIMIT):
        base_query = PredictionRecord.query.filter(
            PredictionRecord.user_id == user_id,
            PredictionRecord.strategy == strategy,
            PredictionRecord.is_result_updated.is_(True),
            PredictionRecord.actual_special_number != None,
        )
        if region:
            base_query = base_query.filter(PredictionRecord.region == region)

        deduped_ids = base_query.with_entities(
            func.max(PredictionRecord.id).label('id')
        ).group_by(
            PredictionRecord.region,
            PredictionRecord.period,
            PredictionRecord.strategy,
        ).subquery()

        return PredictionRecord.query.join(
            deduped_ids,
            PredictionRecord.id == deduped_ids.c.id
        ).with_entities(
            PredictionRecord.region,
            PredictionRecord.special_number,
            PredictionRecord.actual_special_number,
            PredictionRecord.normal_numbers,
            PredictionRecord.special_zodiac,
            PredictionRecord.actual_special_zodiac,
            PredictionRecord.created_at,
            PredictionRecord.id,
        ).order_by(
            PredictionRecord.created_at.desc(),
            PredictionRecord.id.desc()
        ).limit(limit).all()

    recent_rows = _load_recent_resolved_rows()

    def _calculate_current_special_hit_streak(region=None):
        rows = recent_rows if not region else [row for row in recent_rows if row.region == region]
        streak = 0
        for row in rows:
            if _row_exact_hit(row):
                streak += 1
            else:
                break
        return streak

    def _calculate_current_miss_streak(region=None):
        rows = recent_rows if not region else [row for row in recent_rows if row.region == region]
        streak = 0
        for row in rows:
            if _row_exact_hit(row) or _row_secondary_hit(row):
                break
            streak += 1
        return streak

    actual_special = PredictionRecord.actual_special_number
    special_number = PredictionRecord.special_number
    stats_row = db.session.query(
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, ~_secondary_hit_expr()), 1), else_=0))
    ).filter(
        PredictionRecord.user_id == user_id,
        PredictionRecord.strategy == strategy,
    ).one()

    total_predictions = stats_row[0] or 0
    updated_predictions = stats_row[1] or 0
    special_hit_predictions = stats_row[2] or 0
    normal_hit_predictions = stats_row[3] or 0
    wrong_predictions = stats_row[4] or 0
    pending_predictions = max(total_predictions - updated_predictions, 0)

    region_label_map = {'hk': '香港', 'macau': '澳门'}
    region_rows = db.session.query(
        PredictionRecord.region,
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, ~_secondary_hit_expr()), 1), else_=0))
    ).filter(
        PredictionRecord.user_id == user_id,
        PredictionRecord.strategy == strategy,
        PredictionRecord.region.in_(tuple(region_label_map.keys())),
    ).group_by(PredictionRecord.region).all()

    region_stats_map = {
        row[0]: {
            'total': row[1] or 0,
            'updated': row[2] or 0,
            'special_hits': row[3] or 0,
            'normal_hits': row[4] or 0,
            'wrong_predictions': row[5] or 0,
        }
        for row in region_rows
    }
    region_strategy_stats = []
    for region_key, region_label in region_label_map.items():
        region_data = region_stats_map.get(region_key, {})
        region_total = region_data.get('total', 0)
        region_updated = region_data.get('updated', 0)
        region_special_hits = region_data.get('special_hits', 0)
        region_normal_hits = region_data.get('normal_hits', 0)
        region_strategy_stats.append({
            'region': region_key,
            'label': region_label,
            'total': region_total,
            'updated': region_updated,
            'special_hits': region_special_hits,
            'normal_hits': region_normal_hits,
            'wrong_predictions': region_data.get('wrong_predictions', 0),
            'current_special_hit_streak': _calculate_current_special_hit_streak(region_key),
            'current_miss_streak': _calculate_current_miss_streak(region_key),
            'special_hit_rate': round((region_special_hits / region_updated * 100), 2) if region_updated > 0 else 0,
            'normal_hit_rate': round((region_normal_hits / region_updated * 100), 2) if region_updated > 0 else 0,
        })

    return {
        'total_strategy_predictions': total_predictions,
        'updated_predictions': updated_predictions,
        'special_hit_count': special_hit_predictions,
        'normal_hit_count': normal_hit_predictions,
        'wrong_predictions': wrong_predictions,
        'current_special_hit_streak': _calculate_current_special_hit_streak(),
        'current_miss_streak': _calculate_current_miss_streak(),
        'pending_predictions': pending_predictions,
        'special_hit_rate': round((special_hit_predictions / updated_predictions * 100), 2) if updated_predictions > 0 else 0,
        'normal_hit_rate': round((normal_hit_predictions / updated_predictions * 100), 2) if updated_predictions > 0 else 0,
        'region_strategy_stats': region_strategy_stats,
    }


def _get_markov_panel_data():
    regions = {}
    try:
        records = (
            BacktestRun.query.filter(BacktestRun.name.like("auto-%"))
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .limit(12)
            .all()
        )
    except Exception:
        records = []

    for record in records:
        region = getattr(record, "region", "")
        if region in regions:
            continue
        try:
            payload = json.loads(record.payload or "{}")
        except Exception:
            payload = {}
        markov_summary = (payload.get("strategy_results") or {}).get("markov") or {}
        ranking = payload.get("ranking") or []
        rank = next((idx + 1 for idx, item in enumerate(ranking) if item.get("strategy") == "markov"), 0)
        regions[region] = {
            "region": region,
            "label": "香港" if region == "hk" else "澳门" if region == "macau" else region,
            "rank": rank,
            "total": markov_summary.get("total", 0),
            "top1": markov_summary.get("top1_hit_rate", 0.0),
            "top6": markov_summary.get("top6_hit_rate", 0.0),
            "zodiac": markov_summary.get("zodiac_hit_rate", 0.0),
            "windows": markov_summary.get("windows", []),
            "latest_period": payload.get("latest_period", ""),
            "generated_at": payload.get("generated_at", ""),
        }

    config_rows = []
    for region in ("hk", "macau"):
        config = _strategy_config(region, "markov")
        weights = config.get("weights") or {}
        effective_strength = _effective_markov_promotion_strength(config)
        config_rows.append({
            "region": region,
            "label": "香港" if region == "hk" else "澳门",
            "window": config.get("window"),
            "pool": config.get("pool"),
            "special_pool": config.get("special_pool"),
            "transition_decay": config.get("transition_decay"),
            "transition_min_samples": config.get("transition_min_samples"),
            "promotion_strength": config.get("promotion_strength", "hold"),
            "promotion_strength_label": _translate_ml_promotion_strength(
                effective_strength
            ),
            "learning_confidence": round(float(config.get("profile_learning_confidence") or 0.0) * 100, 1),
            "cooldown": config.get("promotion_next_allowed_at", ""),
            "weights": {
                "最近号码接续": weights.get("transition"),
                "连续两期参考": weights.get("second_order"),
                "冷热阶段": weights.get("phase_transition"),
                "号码形态": weights.get("attribute_transition"),
                "避开常错组合": weights.get("failure"),
                "历史反馈": weights.get("feedback"),
            },
        })

    return {
        "regions": [regions[key] for key in ("hk", "macau") if key in regions],
        "configs": config_rows,
    }


@user_bp.route('/predictions')
@login_required
@active_required
def predictions():
    user = User.query.get(session['user_id'])
    page = request.args.get('page', 1, type=int)
    region = request.args.get('region', '')
    period = request.args.get('period', '')
    zodiac = request.args.get('zodiac', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    strategy = request.args.get('strategy', '')
    result = request.args.get('result', '')
    
    query = PredictionRecord.query.filter_by(user_id=session['user_id'])
    
    # 筛选条件
    if region:
        query = query.filter_by(region=region)
    if period:
        query = query.filter(PredictionRecord.period.contains(period))
    if zodiac:
        query = query.filter(PredictionRecord.special_zodiac == zodiac)
    
    # 添加日期范围筛选
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(PredictionRecord.created_at >= start_date_obj)
        except ValueError:
            flash('开始日期格式不正确', 'error')
    
    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            end_date_obj = end_date_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(PredictionRecord.created_at <= end_date_obj)
        except ValueError:
            flash('结束日期格式不正确', 'error')
    
    if strategy:
        query = query.filter_by(strategy=strategy)
    
    if result:
        if result == 'special_hit':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number == PredictionRecord.actual_special_number
            )
        elif result == 'normal_hit':
            query = query.filter(
                PredictionRecord.is_result_updated == True, 
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                _secondary_hit_expr()
            )
        elif result == 'wrong':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                ~_secondary_hit_expr()
            )
        elif result == 'pending':
            query = query.filter(PredictionRecord.is_result_updated == False)
    
    grouped_predictions = []
    grouped_predictions_map = {}
    all_predictions = query.order_by(
        PredictionRecord.created_at.desc(),
        PredictionRecord.id.desc()
    ).all()
    deduped_predictions = []
    seen_prediction_keys = set()

    for prediction in all_predictions:
        unique_key = (prediction.region, prediction.period, prediction.strategy)
        if unique_key in seen_prediction_keys:
            continue
        seen_prediction_keys.add(unique_key)
        deduped_predictions.append(prediction)

    draw_date_map = {}
    lookup_regions = {str(item.region or '').strip() for item in deduped_predictions if item.region}
    lookup_periods = {str(item.period or '').strip() for item in deduped_predictions if item.period}
    if lookup_regions and lookup_periods:
        try:
            draw_rows = LotteryDraw.query.filter(
                LotteryDraw.region.in_(lookup_regions),
                LotteryDraw.draw_id.in_(lookup_periods),
            ).all()
            draw_date_map = {
                (str(row.region or '').strip(), str(row.draw_id or '').strip()): str(row.draw_date or '').strip()
                for row in draw_rows
            }
        except Exception as e:
            print(f"加载预测记录开奖时间失败: {e}")
            draw_date_map = {}

    for prediction in deduped_predictions:
        prediction.display_actual_special_zodiac = (
            prediction.actual_special_zodiac or ''
        ).strip()
        prediction.is_zodiac_hit = bool(
            prediction.is_result_updated
            and prediction.actual_special_number
            and prediction.special_number != prediction.actual_special_number
            and prediction.special_zodiac
            and prediction.display_actual_special_zodiac
            and prediction.special_zodiac == prediction.display_actual_special_zodiac
        )
        prediction.is_normal_number_hit = bool(
            prediction.is_result_updated
            and prediction.actual_special_number
            and prediction.special_number != prediction.actual_special_number
            and prediction.normal_numbers
            and (
                prediction.normal_numbers.startswith(prediction.actual_special_number + ',')
                or prediction.normal_numbers.endswith(',' + prediction.actual_special_number)
                or (',' + prediction.actual_special_number + ',') in prediction.normal_numbers
            )
        )
        period_key = f"{prediction.region}:{prediction.period}"
        if period_key not in grouped_predictions_map:
            group = {
                'grouper': prediction.period,
                'region': prediction.region,
                'draw_date': draw_date_map.get((str(prediction.region or '').strip(), str(prediction.period or '').strip()), ''),
                'next_draw_time': _get_next_draw_time_label(prediction.region),
                'list': []
            }
            grouped_predictions_map[period_key] = group
            grouped_predictions.append(group)
        grouped_predictions_map[period_key]['list'].append(prediction)

    for group in grouped_predictions:
        group['list'].sort(
            key=lambda item: (
                int(str(item.special_number).strip())
                if str(item.special_number or '').strip().isdigit()
                else 999
            )
        )

    groups_per_page = 4
    total_groups = len(grouped_predictions)
    total_pages = max(1, (total_groups + groups_per_page - 1) // groups_per_page)
    current_page = min(max(page, 1), total_pages)
    start_index = (current_page - 1) * groups_per_page
    end_index = start_index + groups_per_page
    paged_grouped_predictions = grouped_predictions[start_index:end_index]
    paged_items = [
        prediction
        for group in paged_grouped_predictions
        for prediction in group['list']
    ]
    predictions = SimpleNamespace(
        items=paged_items,
        page=current_page,
        per_page=groups_per_page,
        total=total_groups,
        pages=total_pages,
        has_prev=current_page > 1,
        has_next=current_page < total_pages,
        prev_num=current_page - 1 if current_page > 1 else None,
        next_num=current_page + 1 if current_page < total_pages else None,
    )

    try:
        from models import ZodiacSetting
        current_year = datetime.now().year
        zodiac_map = ZodiacSetting.get_all_settings_for_year(current_year) or {}
    except Exception as e:
        print(f"鑾峰彇鐢熻倴鏄犲皠澶辫触: {e}")
        zodiac_map = {}

    def get_number_zodiac_cached(number):
        try:
            return zodiac_map.get(int(number), "")
        except (TypeError, ValueError):
            return ""

    actual_special = PredictionRecord.actual_special_number
    special_number = PredictionRecord.special_number

    stats_row = db.session.query(
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, ~_secondary_hit_expr()), 1), else_=0))
    ).filter(PredictionRecord.user_id == session['user_id']).one()

    total_predictions = _count_distinct_prediction_periods(
        PredictionRecord.query.filter_by(user_id=session['user_id'])
    )
    updated_predictions = stats_row[1] or 0
    special_hit_predictions = stats_row[2] or 0
    normal_hit_predictions = stats_row[3] or 0
    wrong_predictions = _count_missed_prediction_periods(
        PredictionRecord.query.filter_by(user_id=session['user_id'])
    )

    accurate_predictions = special_hit_predictions
    
    accuracy_rate = (accurate_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    special_hit_rate = (special_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    normal_hit_rate = (normal_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    
    regions = {
        record.region
        for record in all_predictions
        if record.region
    }

    prediction_summary_cards = []
    for r in regions:
        region_records = [record for record in all_predictions if record.region == r]
        region_records.sort(key=lambda x: (x.created_at, x.id))
        
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
            'total_predictions': len({record.period for record in region_records}),
            'accuracy': accuracy,
            'recommendation': recommendation,
            'recommendation_level': level,
        })
    
    prediction_summary_cards.sort(
        key=lambda item: 0 if item['region'] == 'hk' else 1 if item['region'] == 'macau' else 2
    )

    return render_template('user/predictions.html', 
                          user=user,
                          predictions=predictions,
                          grouped_predictions=paged_grouped_predictions,
                          region=region, 
                          period=period, 
                          zodiac=zodiac,
                          start_date=start_date,
                          end_date=end_date,
                          strategy=strategy,
                          result=result,
                          get_number_color=get_number_color,
                          get_number_zodiac=get_number_zodiac_cached,
                          correct_predictions=accurate_predictions,
                          special_hit_count=special_hit_predictions,
                          normal_hit_count=normal_hit_predictions,
                          wrong_predictions=wrong_predictions,
                          total_predictions=total_predictions,
                          accuracy=round(accuracy_rate, 2),
                          special_hit_rate=round(special_hit_rate, 2),
                          normal_hit_rate=round(normal_hit_rate, 2),
                          prediction_summary_cards=prediction_summary_cards)


@user_bp.route('/ml-records')
@login_required
@active_required
def ml_records():
    user = User.query.get(session['user_id'])
    page = request.args.get('page', 1, type=int)
    region = request.args.get('region', '')
    period = request.args.get('period', '')
    result = request.args.get('result', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    initial_records_html = ""
    try:
        predictions = _get_ml_predictions_page(
            session['user_id'],
            page=page,
            region=region,
            period=period,
            result=result,
            start_date=start_date,
            end_date=end_date,
        )
        zodiac_map = _get_ml_zodiac_map()

        def get_number_zodiac_cached(number):
            try:
                return zodiac_map.get(int(number), "")
            except (TypeError, ValueError):
                return ""

        initial_records_html = render_template(
            'user/_ml_records_list.html',
            predictions=predictions,
            region=region,
            period=period,
            result=result,
            start_date=start_date,
            end_date=end_date,
            get_number_color=get_number_color,
            get_number_zodiac=get_number_zodiac_cached,
        )
    except ValueError as exc:
        flash(str(exc), 'error')

    return render_template(
        'user/ml_records.html',
        user=user,
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
        initial_page=page,
        initial_records_html=initial_records_html,
        **_get_ml_stats(session['user_id']),
    )

    query = PredictionRecord.query.filter_by(
        user_id=session['user_id'],
        strategy='ml',
    )

    if region:
        query = query.filter_by(region=region)
    if period:
        query = query.filter(PredictionRecord.period.contains(period))

    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(PredictionRecord.created_at >= start_date_obj)
        except ValueError:
            flash('开始日期格式不正确', 'error')

    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            end_date_obj = end_date_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(PredictionRecord.created_at <= end_date_obj)
        except ValueError:
            flash('结束日期格式不正确', 'error')

    if result:
        if result == 'special_hit':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number == PredictionRecord.actual_special_number
            )
        elif result == 'normal_hit':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                _secondary_hit_expr()
            )
        elif result == 'wrong':
            query = query.filter(
                PredictionRecord.is_result_updated == True,
                PredictionRecord.actual_special_number != None,
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                ~_secondary_hit_expr()
            )
        elif result == 'pending':
            query = query.filter(PredictionRecord.is_result_updated == False)

    deduped_prediction_ids = query.with_entities(
        func.max(PredictionRecord.id).label('id')
    ).group_by(
        PredictionRecord.region,
        PredictionRecord.period,
        PredictionRecord.strategy,
    ).subquery()

    records_per_page = 12
    total_records = db.session.query(func.count()).select_from(
        deduped_prediction_ids
    ).scalar() or 0
    total_pages = max(1, (total_records + records_per_page - 1) // records_per_page)
    current_page = min(max(page, 1), total_pages)
    start_index = (current_page - 1) * records_per_page

    paged_predictions = PredictionRecord.query.join(
        deduped_prediction_ids,
        PredictionRecord.id == deduped_prediction_ids.c.id
    ).order_by(
        PredictionRecord.created_at.desc(),
        PredictionRecord.id.desc()
    ).offset(start_index).limit(records_per_page).all()

    for prediction in paged_predictions:
        prediction.display_actual_special_zodiac = (
            prediction.actual_special_zodiac or ''
        ).strip()
        prediction.is_zodiac_hit = bool(
            prediction.is_result_updated
            and prediction.actual_special_number
            and prediction.special_number != prediction.actual_special_number
            and prediction.special_zodiac
            and prediction.display_actual_special_zodiac
            and prediction.special_zodiac == prediction.display_actual_special_zodiac
        )
        prediction.is_normal_number_hit = bool(
            prediction.is_result_updated
            and prediction.actual_special_number
            and prediction.special_number != prediction.actual_special_number
            and prediction.normal_numbers
            and (
                prediction.normal_numbers.startswith(prediction.actual_special_number + ',')
                or prediction.normal_numbers.endswith(',' + prediction.actual_special_number)
                or (',' + prediction.actual_special_number + ',') in prediction.normal_numbers
            )
        )
    predictions = SimpleNamespace(
        items=paged_predictions,
        page=current_page,
        per_page=records_per_page,
        total=total_records,
        pages=total_pages,
        has_prev=current_page > 1,
        has_next=current_page < total_pages,
        prev_num=current_page - 1 if current_page > 1 else None,
        next_num=current_page + 1 if current_page < total_pages else None,
    )

    try:
        from models import ZodiacSetting
        zodiac_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
        zodiac_map = ZodiacSetting.get_all_settings_for_year(zodiac_year) or {}
    except Exception as e:
        print(f"加载机器学习记录生肖映射失败: {e}")
        zodiac_map = {}

    def get_number_zodiac_cached(number):
        try:
            return zodiac_map.get(int(number), "")
        except (TypeError, ValueError):
            return ""

    actual_special = PredictionRecord.actual_special_number
    special_number = PredictionRecord.special_number
    stats_row = db.session.query(
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, ~_secondary_hit_expr()), 1), else_=0))
    ).filter(
        PredictionRecord.user_id == session['user_id'],
        PredictionRecord.strategy == 'ml',
    ).one()

    total_ml_predictions = stats_row[0] or 0
    updated_predictions = stats_row[1] or 0
    special_hit_predictions = stats_row[2] or 0
    normal_hit_predictions = stats_row[3] or 0
    wrong_predictions = stats_row[4] or 0
    pending_predictions = max(total_ml_predictions - updated_predictions, 0)
    special_hit_rate = (special_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    normal_hit_rate = (normal_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0

    region_label_map = {'hk': '香港', 'macau': '澳门'}
    region_rows = db.session.query(
        PredictionRecord.region,
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0))
    ).filter(
        PredictionRecord.user_id == session['user_id'],
        PredictionRecord.strategy == 'ml',
        PredictionRecord.region.in_(tuple(region_label_map.keys())),
    ).group_by(PredictionRecord.region).all()

    fast_region_ml_stats = []
    region_stats_map = {
        row[0]: {
            'total': row[1] or 0,
            'updated': row[2] or 0,
            'special_hits': row[3] or 0,
            'normal_hits': row[4] or 0,
        }
        for row in region_rows
    }
    for region_key, region_label in region_label_map.items():
        region_data = region_stats_map.get(region_key, {})
        region_total = region_data.get('total', 0)
        region_updated = region_data.get('updated', 0)
        region_special_hits = region_data.get('special_hits', 0)
        region_normal_hits = region_data.get('normal_hits', 0)
        fast_region_ml_stats.append({
            'region': region_key,
            'label': region_label,
            'total': region_total,
            'updated': region_updated,
            'special_hits': region_special_hits,
            'normal_hits': region_normal_hits,
            'special_hit_rate': round((region_special_hits / region_updated * 100), 2) if region_updated > 0 else 0,
            'normal_hit_rate': round((region_normal_hits / region_updated * 100), 2) if region_updated > 0 else 0,
        })

    region_ml_stats = []
    for region_key, region_label in ():
        region_row = db.session.query(
            db.func.count(PredictionRecord.id),
            db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
            db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
            db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, _secondary_hit_expr()), 1), else_=0))
        ).filter(
            PredictionRecord.user_id == session['user_id'],
            PredictionRecord.strategy == 'ml',
            PredictionRecord.region == region_key,
        ).one()

        region_total = region_row[0] or 0
        region_updated = region_row[1] or 0
        region_special_hits = region_row[2] or 0
        region_normal_hits = region_row[3] or 0
        region_ml_stats.append({
            'region': region_key,
            'label': region_label,
            'total': region_total,
            'updated': region_updated,
            'special_hits': region_special_hits,
            'normal_hits': region_normal_hits,
            'special_hit_rate': round((region_special_hits / region_updated * 100), 2) if region_updated > 0 else 0,
            'normal_hit_rate': round((region_normal_hits / region_updated * 100), 2) if region_updated > 0 else 0,
        })

    return render_template(
        'user/ml_records.html',
        user=user,
        predictions=predictions,
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
        get_number_color=get_number_color,
        get_number_zodiac=get_number_zodiac_cached,
        total_ml_predictions=total_ml_predictions,
        updated_predictions=updated_predictions,
        special_hit_count=special_hit_predictions,
        normal_hit_count=normal_hit_predictions,
        wrong_predictions=wrong_predictions,
        pending_predictions=pending_predictions,
        special_hit_rate=round(special_hit_rate, 2),
        normal_hit_rate=round(normal_hit_rate, 2),
        region_ml_stats=fast_region_ml_stats,
    )

@user_bp.route('/ml-records/list')
@login_required
@active_required
def ml_records_list():
    page = request.args.get('page', 1, type=int)
    region = request.args.get('region', '')
    period = request.args.get('period', '')
    result = request.args.get('result', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    try:
        predictions = _get_ml_predictions_page(
            session['user_id'],
            page=page,
            region=region,
            period=period,
            result=result,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    zodiac_map = _get_ml_zodiac_map()

    def get_number_zodiac_cached(number):
        try:
            return zodiac_map.get(int(number), "")
        except (TypeError, ValueError):
            return ""

    html = render_template(
        'user/_ml_records_list.html',
        predictions=predictions,
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
        get_number_color=get_number_color,
        get_number_zodiac=get_number_zodiac_cached,
    )
    return jsonify({
        'success': True,
        'html': html,
        'page': predictions.page,
        'pages': predictions.pages,
        'total': predictions.total,
    })


@user_bp.route('/markov-records')
@login_required
@active_required
def markov_records():
    user = User.query.get(session['user_id'])
    page = request.args.get('page', 1, type=int)
    region = request.args.get('region', '')
    period = request.args.get('period', '')
    result = request.args.get('result', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    initial_records_html = ""
    try:
        predictions = _get_strategy_predictions_page(
            session['user_id'],
            'markov',
            page=page,
            region=region,
            period=period,
            result=result,
            start_date=start_date,
            end_date=end_date,
        )
        zodiac_map = _get_ml_zodiac_map()

        def get_number_zodiac_cached(number):
            try:
                return zodiac_map.get(int(number), "")
            except (TypeError, ValueError):
                return ""

        initial_records_html = render_template(
            'user/_ml_records_list.html',
            predictions=predictions,
            region=region,
            period=period,
            result=result,
            start_date=start_date,
            end_date=end_date,
            get_number_color=get_number_color,
            get_number_zodiac=get_number_zodiac_cached,
            records_endpoint='user.markov_records',
            records_empty_title='暂无马尔科夫记录',
            records_empty_text='先去首页生成一次马尔科夫预测，这里会自动保存。',
            records_empty_icon='🔗',
        )
    except ValueError as exc:
        flash(str(exc), 'error')

    return render_template(
        'user/ml_records.html',
        user=user,
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
        initial_page=page,
        initial_records_html=initial_records_html,
        record_page_title='马尔科夫',
        record_page_icon='🔗',
        record_total_label='马尔科夫',
        record_region_suffix='马尔科夫',
        records_endpoint='user.markov_records',
        records_list_endpoint='user.markov_records_list',
        records_empty_title='暂无马尔科夫记录',
        records_empty_text='先去首页生成一次马尔科夫预测，这里就会自动保存。',
        records_loading_text='正在加载马尔科夫记录...',
        records_page_key='markov',
        markov_panel=_get_markov_panel_data(),
        **_get_strategy_stats(session['user_id'], 'markov'),
    )


@user_bp.route('/markov-records/list')
@login_required
@active_required
def markov_records_list():
    page = request.args.get('page', 1, type=int)
    region = request.args.get('region', '')
    period = request.args.get('period', '')
    result = request.args.get('result', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    try:
        predictions = _get_strategy_predictions_page(
            session['user_id'],
            'markov',
            page=page,
            region=region,
            period=period,
            result=result,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    zodiac_map = _get_ml_zodiac_map()

    def get_number_zodiac_cached(number):
        try:
            return zodiac_map.get(int(number), "")
        except (TypeError, ValueError):
            return ""

    html = render_template(
        'user/_ml_records_list.html',
        predictions=predictions,
        region=region,
        period=period,
        result=result,
        start_date=start_date,
        end_date=end_date,
        get_number_color=get_number_color,
        get_number_zodiac=get_number_zodiac_cached,
        records_endpoint='user.markov_records',
        records_empty_title='暂无马尔科夫记录',
        records_empty_text='先去首页生成一次马尔科夫预测，这里会自动保存。',
        records_empty_icon='🔗',
    )
    return jsonify({
        'success': True,
        'html': html,
        'page': predictions.page,
        'pages': predictions.pages,
        'total': predictions.total,
    })

@user_bp.route('/save-prediction', methods=['POST'])
@login_required
def save_prediction():
    """保存预测记录"""
    try:
        data = request.get_json()

        user = User.query.get(session['user_id'])
        if not user:
            return jsonify({
                'success': False,
                'message': '用户不存在'
            })

        user.check_and_update_activation_status()
        session['is_active'] = bool(user.is_active)

        if not user.is_active and data.get('strategy') != 'ai':
            return jsonify({
                'success': False,
                'message': '请先激活账号'
            })
        
        existing = PredictionRecord.query.filter_by(
            user_id=user.id,
            region=data['region'],
            period=data['period'],
            strategy=data['strategy']
        ).first()
        
        if existing:
            return jsonify({
                'success': False,
                'message': '您已经为本期的该策略生成过预测，不能重复生成'
            })
        
        prediction = PredictionRecord(
            user_id=user.id,
            region=data['region'],
            strategy=data['strategy'],
            period=data['period'],
            normal_numbers=','.join(map(str, data['normal_numbers'])),
            special_number=str(data['special_number']),
            special_zodiac=data.get('special_zodiac', ''),
            prediction_metadata=_serialize_prediction_metadata(data.get('model_meta')),
            prediction_text=data.get('prediction_text', '')
        )
        
        db.session.add(prediction)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '预测记录保存成功'
        })
        
    except Exception as e:
        duplicate_hint = str(e).lower()
        if 'unique' in duplicate_hint or 'duplicate' in duplicate_hint:
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': '您已经为本期的该策略生成过预测，不能重复生成'
            })
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'保存失败：{str(e)}'
        })

@user_bp.route('/check-prediction-exists')
@login_required
@active_required
def check_prediction_exists():
    """检查用户是否已为当前期生成预测"""
    region = request.args.get('region')
    period = request.args.get('period')
    strategy = request.args.get('strategy')

    if not region or not period:
        return jsonify({'exists': False})

    query = PredictionRecord.query.filter_by(
        user_id=session['user_id'],
        region=region,
        period=period
    )
    if strategy:
        query = query.filter_by(strategy=strategy)

    existing = query.first()
    
    if existing:
        return jsonify({
            'exists': True,
            'prediction': {
                'normal_numbers': existing.normal_numbers.split(','),
                'special_number': existing.special_number,
                'special_zodiac': existing.special_zodiac,
                'model_meta': _hydrate_user_prediction_model_meta(existing),
                'prediction_text': _hydrate_user_prediction_text(existing),
                'created_at': existing.created_at.strftime('%Y-%m-%d %H:%M:%S')
            }
        })
    
    return jsonify({'exists': False})

@user_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    return redirect(url_for('user.dashboard'))


@user_bp.route('/github/unbind', methods=['POST'])
@login_required
def unbind_github():
    user = User.query.get(session['user_id'])
    if not user:
        flash('登录状态已失效，请重新登录', 'error')
        return redirect(url_for('auth.login'))
    if not getattr(user, 'github_id', None):
        flash('当前账号还没有绑定 GitHub', 'error')
        return redirect(url_for('user.dashboard'))

    user.github_id = None
    user.github_username = None
    try:
        db.session.commit()
        flash('GitHub 账号已解绑', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'解绑失败：{str(e)}', 'error')
    return redirect(url_for('user.dashboard'))


@user_bp.route('/notification_settings', methods=['GET', 'POST'])
@login_required
def notification_settings():
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        try:
            save_user_notification_config(user, request.form)
            flash('推送设置已保存', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'推送设置保存失败：{str(e)}', 'error')
        return redirect(url_for('user.notification_settings'))

    return render_template(
        'user/notification_settings.html',
        user=user,
        notification_config=get_user_notification_config(user),
    )

@user_bp.route('/save_prediction_settings', methods=['POST'])
@login_required
@active_required
def save_prediction_settings():
    """保存用户预测设置"""
    user = User.query.get(session['user_id'])

    auto_prediction_enabled = 'auto_prediction_enabled' in request.form
    show_normal_numbers = 'show_normal_numbers' in request.form
    auto_prediction_strategies = request.form.getlist('auto_prediction_strategies')
    auto_prediction_regions = request.form.getlist('auto_prediction_regions')

    valid_strategies = []
    for strategy in auto_prediction_strategies:
        if strategy in STRATEGY_KEYS and strategy != 'ai':
            valid_strategies.append(strategy)

    if not valid_strategies:
        valid_strategies = ['hot', 'cold', 'trend', 'hybrid', 'balanced', 'markov', 'ml']

    valid_regions = []
    for region in auto_prediction_regions:
        if region in ['hk', 'macau']:
            valid_regions.append(region)

    if not valid_regions:
        valid_regions = ['hk']

    user.auto_prediction_enabled = auto_prediction_enabled
    user.auto_prediction_strategies = ','.join(valid_strategies)
    user.auto_prediction_regions = ','.join(valid_regions)
    user.show_normal_numbers = show_normal_numbers

    try:
        db.session.commit()
        flash('预测设置保存成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'保存失败：{str(e)}', 'error')

    return redirect(url_for('user.dashboard'))

@user_bp.route('/update_auto_prediction', methods=['POST'])
@login_required
@active_required
def update_auto_prediction():
    """更新自动预测设置"""
    try:
        user = User.query.get(session['user_id'])

        auto_prediction_enabled = 'auto_prediction_enabled' in request.form
        show_normal_numbers = 'show_normal_numbers' in request.form
        auto_prediction_strategies = request.form.getlist('auto_prediction_strategies')
        auto_prediction_regions = request.form.getlist('auto_prediction_regions')

        valid_strategies = []
        for strategy in auto_prediction_strategies:
            if strategy in STRATEGY_KEYS and strategy != 'ai':
                valid_strategies.append(strategy)

        if not valid_strategies:
            valid_strategies = ['hot', 'cold', 'trend', 'hybrid', 'balanced', 'markov', 'ml']

        valid_regions = []
        for region in auto_prediction_regions:
            if region in ['hk', 'macau']:
                valid_regions.append(region)

        if not valid_regions:
            valid_regions = ['hk', 'macau']

        user.auto_prediction_enabled = auto_prediction_enabled
        user.auto_prediction_strategies = ','.join(valid_strategies)
        user.auto_prediction_regions = ','.join(valid_regions)
        user.show_normal_numbers = show_normal_numbers

        db.session.commit()
        flash('自动预测设置保存成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'设置保存失败：{str(e)}', 'error')

    return redirect(url_for('user.dashboard'))

@user_bp.route('/invite')
@login_required
@active_required
def invite():
    """邀请好友页面，重定向到邀请码管理"""
    return redirect(url_for('user.invite_codes'))

@user_bp.route('/invite_codes')
@login_required
@active_required
def invite_codes():
    """用户邀请码管理"""
    user = User.query.get(session['user_id'])
    
    page = request.args.get('page', 1, type=int)
    invite_codes = InviteCode.query.filter_by(created_by=user.username, is_used=False)\
        .order_by(InviteCode.created_at.desc()).all()
    
    total_invites = InviteCode.query.filter_by(created_by=user.username, is_used=True).count()
    active_invites = User.query.filter_by(invited_by=user.username, is_active=True).count()
    total_generated = InviteCode.query.filter_by(created_by=user.username).count()
    
    invited_users = User.query.filter_by(invited_by=user.username)\
        .order_by(User.created_at.desc()).limit(10).all()
    
    stats = {
        'total_invites': total_invites,
        'active_invites': active_invites,
        'total_generated': total_generated,
        'success_rate': round(active_invites / total_invites * 100, 1) if total_invites > 0 else 0
    }
    
    return render_template('user/invite_codes.html', 
                          invite_codes=invite_codes, 
                          stats=stats, 
                          invited_users=invited_users)

@user_bp.route('/generate_invite_code', methods=['POST'])
@login_required
@active_required
def generate_invite_code():
    """生成邀请码"""
    try:
        user = User.query.get(session['user_id'])
        
        total_codes = InviteCode.query.filter_by(created_by=user.username).count()
        
        if total_codes >= 10:
            return jsonify({
                'success': False,
                'message': '您已达到邀请码生成上限（10 个）'
            })
        
        invite_code = InviteCode()
        invite_code.code = InviteCode.generate_code()
        invite_code.created_by = user.username
        
        from datetime import timedelta
        invite_code.expires_at = datetime.utcnow() + timedelta(days=7)
        
        db.session.add(invite_code)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '邀请码生成成功',
            'code': invite_code.code
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'生成失败：{str(e)}'
        })

@user_bp.route('/update_profile', methods=['POST'])
@login_required
@active_required
def update_profile():
    """更新个人基本信息"""
    try:
        user = User.query.get(session['user_id'])
        
        # 更新邮箱
        new_email = request.form.get('email')
        if new_email and new_email != user.email:
            # 检查邮箱是否已被使用
            existing_user = User.query.filter_by(email=new_email).first()
            if existing_user and existing_user.id != user.id:
                flash('该邮箱已被其他用户使用', 'error')
                return redirect(url_for('user.dashboard'))
            
            user.email = new_email
        
        db.session.commit()
        flash('个人信息更新成功', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'更新失败：{str(e)}', 'error')
    
    return redirect(url_for('user.dashboard'))

@user_bp.route('/change_password', methods=['POST'])
@login_required
@active_required
def change_password():
    """修改密码"""
    try:
        user = User.query.get(session['user_id'])
        
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not user.check_password(current_password):
            flash('当前密码错误', 'error')
            return redirect(url_for('user.dashboard'))
        
        # 验证新密码
        if new_password != confirm_password:
            flash('两次输入的新密码不一致', 'error')
            return redirect(url_for('user.dashboard'))
        
        if len(new_password) < 6:
            flash('新密码长度至少 6 位', 'error')
            return redirect(url_for('user.dashboard'))
        
        # 更新密码
        user.set_password(new_password)
        db.session.commit()
        flash('密码修改成功', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'密码修改失败：{str(e)}', 'error')
    
    return redirect(url_for('user.dashboard'))

@user_bp.route('/analytics')
@login_required
@active_required
def analytics():
    """用户统计分析页面"""
    user = _get_session_user()
    if not user:
        flash('请先登录', 'error')
        return redirect(url_for('auth.login'))
    strategy_backtests, recommended_strategy, top_strategies = _strategy_backtests(user.id)
    learning_snapshot = _learning_snapshot()
    learning_comparison = _build_learning_comparison()
    latest_backtests = _latest_unique_backtest_summary()
    
    user_predictions_query = PredictionRecord.query.filter_by(user_id=user.id)
    total_predictions = _count_distinct_prediction_periods(user_predictions_query)
    updated_predictions = _count_distinct_prediction_periods(user_predictions_query.filter(
        PredictionRecord.is_result_updated == True,
        PredictionRecord.actual_special_number != None
    ))
    
    special_hit_predictions = _count_distinct_prediction_periods(user_predictions_query.filter(
        PredictionRecord.is_result_updated == True,
        PredictionRecord.actual_special_number != None,
        PredictionRecord.special_number == PredictionRecord.actual_special_number
    ))
    
    normal_hit_predictions = _count_distinct_prediction_periods(user_predictions_query.filter(
        PredictionRecord.is_result_updated == True,
        PredictionRecord.actual_special_number != None,
        PredictionRecord.special_number != PredictionRecord.actual_special_number,
        _secondary_hit_expr()
    ))
    
    accurate_predictions = special_hit_predictions
    
    wrong_predictions = _count_missed_prediction_periods(
        PredictionRecord.query.filter_by(user_id=user.id)
    )
    
    # 计算不同策略的命中率
    def calculate_strategy_stats(strategy=None):
        query = PredictionRecord.query.filter_by(user_id=user.id)
        if strategy:
            query = query.filter_by(strategy=strategy)
        
        total = query.count()
        updated = query.filter_by(is_result_updated=True).filter(
            PredictionRecord.actual_special_number != None
        ).count()
        
        special_hit = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.special_number == PredictionRecord.actual_special_number
        ).count()
        
        normal_hit = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            PredictionRecord.special_number != PredictionRecord.actual_special_number,
            _secondary_hit_expr()
        ).count()

        zodiac_hit = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            _zodiac_hit_expr()
        ).count()
        
        wrong = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            (PredictionRecord.special_number != PredictionRecord.actual_special_number),
            ~_secondary_hit_expr()
        ).count()
        
        correct = special_hit
        
        accuracy = (special_hit / updated * 100) if updated > 0 else 0
        special_hit_rate = (special_hit / updated * 100) if updated > 0 else 0
        normal_hit_rate = (normal_hit / updated * 100) if updated > 0 else 0
        zodiac_hit_rate = (zodiac_hit / updated * 100) if updated > 0 else 0
        
        return {
            'total': total,
            'updated': updated,
            'correct': correct,
            'wrong': wrong,
            'special_hit': special_hit,
            'normal_hit': normal_hit,
            'zodiac_hit': zodiac_hit,
            'accuracy': round(accuracy, 1),
            'special_hit_rate': round(special_hit_rate, 1),
            'normal_hit_rate': round(normal_hit_rate, 1),
            'zodiac_hit_rate': round(zodiac_hit_rate, 1),
        }
    
    def calculate_region_stats(region):
        query = PredictionRecord.query.filter_by(user_id=user.id, region=region)

        def count_periods(period_query):
            return period_query.with_entities(PredictionRecord.period).distinct().count()
        
        total = count_periods(query)
        updated = count_periods(query.filter_by(is_result_updated=True).filter(
            PredictionRecord.actual_special_number != None
        ))
        
        special_hit = count_periods(query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.special_number == PredictionRecord.actual_special_number
        ))
        
        normal_hit = count_periods(query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            PredictionRecord.special_number != PredictionRecord.actual_special_number,
            _secondary_hit_expr()
        ))

        zodiac_hit = count_periods(query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            _zodiac_hit_expr()
        ))
        
        wrong = max(updated - special_hit, 0)
        
        correct = special_hit
        
        accuracy = (special_hit / updated * 100) if updated > 0 else 0
        special_hit_rate = (special_hit / updated * 100) if updated > 0 else 0
        normal_hit_rate = (normal_hit / updated * 100) if updated > 0 else 0
        zodiac_hit_rate = (zodiac_hit / updated * 100) if updated > 0 else 0
        
        return {
            'total': total,
            'updated': updated,
            'correct': correct,
            'wrong': wrong,
            'special_hit': special_hit,
            'normal_hit': normal_hit,
            'zodiac_hit': zodiac_hit,
            'accuracy': round(accuracy, 1),
            'special_hit_rate': round(special_hit_rate, 1),
            'normal_hit_rate': round(normal_hit_rate, 1),
            'zodiac_hit_rate': round(zodiac_hit_rate, 1),
        }
    
    stats = calculate_strategy_stats()
    
    stats['total_predictions'] = total_predictions
    stats['updated_predictions'] = updated_predictions
    stats['special_hit_count'] = special_hit_predictions
    stats['normal_hit_count'] = normal_hit_predictions
    stats['wrong_predictions'] = wrong_predictions
    stats['accuracy'] = (accurate_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    stats['special_hit_rate'] = (special_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    stats['normal_hit_rate'] = (normal_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    
    # 计算各策略统计
    strategy_stats = {
        meta["key"]: calculate_strategy_stats(meta["key"])
        for meta in STRATEGY_META
    }
    
    # 计算各地区统计
    region_stats = {
        'hk': calculate_region_stats('hk'),
        'macau': calculate_region_stats('macau')
    }

    best_strategy = None
    best_accuracy = -1
    for meta in STRATEGY_META:
        stats_entry = strategy_stats.get(meta["key"])
        if stats_entry and stats_entry.get("updated", 0) > 0:
            accuracy_value = stats_entry.get("accuracy", 0)
            if accuracy_value > best_accuracy:
                best_accuracy = accuracy_value
                best_strategy = meta
    
    recent_predictions = PredictionRecord.query.filter_by(user_id=user.id)\
        .order_by(PredictionRecord.created_at.desc()).limit(10).all()
    
    from datetime import timedelta
    
    trend_data = []
    for i in range(6, -1, -1):
        date = datetime.utcnow().date() - timedelta(days=i)
        date_start = datetime.combine(date, datetime.min.time())
        date_end = datetime.combine(date, datetime.max.time())
        
        day_query = PredictionRecord.query.filter(
            PredictionRecord.user_id == user.id,
            PredictionRecord.created_at >= date_start,
            PredictionRecord.created_at <= date_end
        )
        day_predictions = _count_distinct_prediction_periods(day_query)
        
        trend_data.append({
            'date': date.strftime('%m-%d'),
            'count': day_predictions
        })

    def calculate_trend_summary(start_at=None, end_at=None):
        query = PredictionRecord.query.filter(PredictionRecord.user_id == user.id)
        if start_at:
            query = query.filter(PredictionRecord.created_at >= start_at)
        if end_at:
            query = query.filter(PredictionRecord.created_at <= end_at)

        total = _count_distinct_prediction_periods(query)
        updated = _count_distinct_prediction_periods(query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None
        ))
        special_hit = _count_distinct_prediction_periods(query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            PredictionRecord.special_number == PredictionRecord.actual_special_number
        ))

        return {
            'total': total,
            'updated': updated,
            'accuracy': round((special_hit / updated * 100), 1) if updated > 0 else 0,
        }

    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())
    week_start = datetime.combine(today - timedelta(days=6), datetime.min.time())
    trend_summary = {
        'week': calculate_trend_summary(week_start, today_end),
        'today': calculate_trend_summary(today_start, today_end),
        'total': {
            'total': total_predictions,
            'updated': updated_predictions,
            'accuracy': round(stats['accuracy'], 1),
        },
    }
    
    return render_template('user/analytics.html',
                          user=user,
                          stats=stats,
                          strategy_stats=strategy_stats,
                          strategy_meta=STRATEGY_META,
                          strategy_label_map=_strategy_label_map(),
                          best_strategy=best_strategy,
                          recommended_strategy=recommended_strategy,
                          top_strategies=top_strategies,
                          strategy_backtests=strategy_backtests,
                          learning_snapshot=learning_snapshot,
                          learning_comparison=learning_comparison,
                          latest_backtests=latest_backtests,
                          region_stats=region_stats,
                          recent_predictions=recent_predictions,
                          trend_data=trend_data,
                          trend_summary=trend_summary,
                          get_number_color=get_number_color)
