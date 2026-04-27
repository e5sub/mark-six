from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, User, PredictionRecord, SystemConfig, InviteCode
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from types import SimpleNamespace
import json

user_bp = Blueprint('user', __name__, url_prefix='/user')

STRATEGY_META = [
    {"key": "hot", "label": "热门", "icon": "🔥"},
    {"key": "cold", "label": "冷门", "icon": "🧊"},
    {"key": "trend", "label": "走势", "icon": "📈"},
    {"key": "hybrid", "label": "综合", "icon": "♻️"},
    {"key": "balanced", "label": "均衡", "icon": "⚖️"},
    {"key": "ml", "label": "机器学习", "icon": "🧪"},
    {"key": "ai", "label": "AI", "icon": "🤖"},
]
STRATEGY_KEYS = [item["key"] for item in STRATEGY_META]
AUTO_STRATEGY_META = [item for item in STRATEGY_META if item["key"] != "ai"]

STRATEGY_META.insert(0, {"key": "smart", "label": "智能优选", "icon": "🧠"})
STRATEGY_KEYS = [item["key"] for item in STRATEGY_META]
AUTO_STRATEGY_META = [item for item in STRATEGY_META if item["key"] != "ai"]
LOCAL_STRATEGIES = ["hot", "cold", "trend", "hybrid", "balanced", "ml"]
SMART_STRATEGY_PREFIX = '智能优选（本期采用：'

def _strategy_label_map():
    return {item["key"]: item["label"] for item in STRATEGY_META}

def _get_prediction_display_info(prediction):
    raw_text = (prediction.prediction_text or '').strip()
    if raw_text.startswith(SMART_STRATEGY_PREFIX):
        first_line = raw_text.splitlines()[0].strip()
        if first_line:
            return {
                "key": "smart",
                "label": first_line
            }
    return {
        "key": prediction.strategy,
        "label": _strategy_label_map().get(prediction.strategy, prediction.strategy)
    }

def _strategy_config(region, strategy):
    raw = SystemConfig.get_config(f"strategy_config_{region}_{strategy}", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

def _actual_in_normal_expr():
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

def _calculate_accuracy_summary(query):
    base_query = query.filter(
        PredictionRecord.is_result_updated.is_(True),
        PredictionRecord.actual_special_number != None
    )

    special_hit_expr = case(
        (PredictionRecord.special_number == PredictionRecord.actual_special_number, 1),
        else_=0
    )
    normal_hit_expr = case(
        (
            db.and_(
                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                _secondary_hit_expr(),
            ),
            1
        ),
        else_=0
    )

    agg = base_query.with_entities(
        func.count().label('total'),
        func.sum(special_hit_expr).label('special_hits'),
        func.sum(normal_hit_expr).label('normal_hits'),
    ).first()

    total = agg.total or 0
    special_hits = agg.special_hits or 0
    normal_hits = agg.normal_hits or 0
    correct = special_hits + normal_hits

    return {
        "total": total,
        "special_hits": special_hits,
        "normal_hits": normal_hits,
        "correct": correct,
        "accuracy": round((correct / total) * 100, 1) if total else 0.0,
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
    tracked = [item["key"] for item in STRATEGY_META if item["key"] in LOCAL_STRATEGIES or item["key"] == "ai"]
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
                "last_accuracy": round(float(config.get("last_accuracy") or 0.0) * 100, 1),
                "last_total": config.get("last_total", 0),
                "prev_accuracy": round(float(config.get("prev_accuracy") or 0.0) * 100, 1),
                "prev_total": config.get("prev_total", 0),
                "accuracy_delta": round(float(config.get("accuracy_delta") or 0.0) * 100, 1),
                "weights": config.get("weights", {}),
                "updated_at": config.get("updated_at", ""),
            }
        snapshots[region] = region_data
    return snapshots

def _build_learning_comparison():
    snapshots = _learning_snapshot()
    comparisons = {}
    tracked = [item["key"] for item in STRATEGY_META if item["key"] in LOCAL_STRATEGIES or item["key"] == "ai"]

    for region, region_data in snapshots.items():
        items = []
        for strategy in tracked:
            config = region_data.get(strategy)
            if not config:
                continue

            current_accuracy = float(config.get("last_accuracy") or 0.0)
            previous_accuracy = float(config.get("prev_accuracy") or 0.0)
            current_total = int(config.get("last_total") or 0)
            previous_total = int(config.get("prev_total") or 0)
            delta = round(current_accuracy - previous_accuracy, 1) if previous_total > 0 else 0.0

            if current_total <= 0:
                trend = "暂无样本"
                trend_class = "neutral"
            elif previous_total <= 0:
                trend = "刚开始学习"
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

def login_required(f):
    """登录验证装饰器"""
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def active_required(f):
    """激活验证装饰器"""
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            flash('请先登录', 'error')
            return redirect(url_for('auth.login'))

        user = User.query.get(user_id)
        if not user:
            session.clear()
            flash('请先登录', 'error')
            return redirect(url_for('auth.login'))

        is_active = user.check_and_update_activation_status()
        session['is_active'] = bool(is_active and user.is_active)
        if not session['is_active']:
            flash('请先激活账号后再使用此功能', 'warning')
            return redirect(url_for('auth.activate'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

@user_bp.route('/dashboard')
@user_bp.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    strategy_backtests, recommended_strategy, top_strategies = _strategy_backtests(user.id)
    learning_snapshot = _learning_snapshot()
    learning_comparison = _build_learning_comparison()
    
    total_predictions = PredictionRecord.query.filter_by(user_id=user.id).count()
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
        normal_hit_expr = case(
            (
                db.and_(
                    PredictionRecord.special_number != PredictionRecord.actual_special_number,
                    _secondary_hit_expr(),
                ),
                1,
            ),
            else_=0,
        )

        agg = base_query.with_entities(
            func.count().label('total'),
            func.sum(special_hit_expr).label('special_hits'),
            func.sum(normal_hit_expr).label('normal_hits'),
        ).first()

        total_count = agg.total or 0
        if total_count == 0:
            return 0.0

        special_hits = agg.special_hits or 0
        normal_hits = agg.normal_hits or 0
        total_hits = special_hits + normal_hits
        return round((total_hits / total_count) * 100, 1)

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
                          get_number_color=get_number_color,
                          get_number_zodiac=get_number_zodiac)

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
        PredictionRecord.created_at.desc()
    ).all()

    for prediction in all_predictions:
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
                'list': []
            }
            grouped_predictions_map[period_key] = group
            grouped_predictions.append(group)
        grouped_predictions_map[period_key]['list'].append(prediction)

    groups_per_page = 20
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
    secondary_hit = _secondary_hit_expr()

    stats_row = db.session.query(
        db.func.count(PredictionRecord.id),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number == actual_special), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, secondary_hit), 1), else_=0)),
        db.func.sum(db.case((db.and_(PredictionRecord.is_result_updated == True, actual_special != None, special_number != actual_special, ~secondary_hit), 1), else_=0))
    ).filter(PredictionRecord.user_id == session['user_id']).one()

    total_predictions = stats_row[0] or 0
    updated_predictions = stats_row[1] or 0
    special_hit_predictions = stats_row[2] or 0
    normal_hit_predictions = stats_row[3] or 0
    wrong_predictions = stats_row[4] or 0

    accurate_predictions = special_hit_predictions + normal_hit_predictions
    
    accuracy_rate = (accurate_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    special_hit_rate = (special_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    normal_hit_rate = (normal_hit_predictions / updated_predictions * 100) if updated_predictions > 0 else 0
    
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
                          normal_hit_rate=round(normal_hit_rate, 2))

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
            prediction_text=data.get('prediction_text', '')
        )
        
        db.session.add(prediction)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '预测记录保存成功'
        })
        
    except Exception as e:
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
                'prediction_text': existing.prediction_text,
                'created_at': existing.created_at.strftime('%Y-%m-%d %H:%M:%S')
            }
        })
    
    return jsonify({'exists': False})

@user_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        # 鏇存柊鐢ㄦ埛淇℃伅
        new_email = request.form.get('email')
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not user.check_password(current_password):
            flash('当前密码错误', 'error')
            return render_template('user/profile.html', user=user, strategy_meta=STRATEGY_META, auto_strategy_meta=AUTO_STRATEGY_META)
            
        if new_email and new_email != user.email:
            if not user.is_admin:
                flash('普通用户无权修改邮箱地址，如需修改请联系管理员', 'error')
                return render_template('user/profile.html', user=user, strategy_meta=STRATEGY_META, auto_strategy_meta=AUTO_STRATEGY_META)
            if User.query.filter_by(email=new_email).first():
                flash('邮箱已被其他用户使用', 'error')
                return render_template('user/profile.html', user=user, strategy_meta=STRATEGY_META, auto_strategy_meta=AUTO_STRATEGY_META)
            user.email = new_email
        
        # 更新密码
        if new_password:
            if new_password != confirm_password:
                flash('两次输入的新密码不一致', 'error')
                return render_template('user/profile.html', user=user, strategy_meta=STRATEGY_META, auto_strategy_meta=AUTO_STRATEGY_META)
            if len(new_password) < 6:
                flash('新密码长度至少 6 位', 'error')
                return render_template('user/profile.html', user=user, strategy_meta=STRATEGY_META, auto_strategy_meta=AUTO_STRATEGY_META)
            user.set_password(new_password)
        
        try:
            db.session.commit()
            flash('个人信息更新成功', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'更新失败：{str(e)}', 'error')
    
    return render_template('user/profile.html', user=user, strategy_meta=STRATEGY_META, auto_strategy_meta=AUTO_STRATEGY_META)

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
        valid_strategies = ['hybrid']

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

    return redirect(url_for('user.profile'))

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
            valid_strategies = ['hybrid']

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
    user = User.query.get(session['user_id'])
    strategy_backtests, recommended_strategy, top_strategies = _strategy_backtests(user.id)
    learning_snapshot = _learning_snapshot()
    learning_comparison = _build_learning_comparison()
    
    total_predictions = PredictionRecord.query.filter_by(user_id=user.id).count()
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
    
    accurate_predictions = special_hit_predictions + normal_hit_predictions
    
    wrong_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.actual_special_number != None,
        (PredictionRecord.special_number != PredictionRecord.actual_special_number),
        ~_secondary_hit_expr()
    ).count()
    
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
        
        wrong = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            (PredictionRecord.special_number != PredictionRecord.actual_special_number),
            ~_secondary_hit_expr()
        ).count()
        
        correct = special_hit + normal_hit
        
        accuracy = (correct / updated * 100) if updated > 0 else 0
        special_hit_rate = (special_hit / updated * 100) if updated > 0 else 0
        normal_hit_rate = (normal_hit / updated * 100) if updated > 0 else 0
        
        return {
            'total': total,
            'updated': updated,
            'correct': correct,
            'wrong': wrong,
            'special_hit': special_hit,
            'normal_hit': normal_hit,
            'accuracy': round(accuracy, 1),
            'special_hit_rate': round(special_hit_rate, 1),
            'normal_hit_rate': round(normal_hit_rate, 1)
        }
    
    def calculate_region_stats(region):
        query = PredictionRecord.query.filter_by(user_id=user.id, region=region)
        
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
        
        wrong = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.actual_special_number != None,
            (PredictionRecord.special_number != PredictionRecord.actual_special_number),
            ~_secondary_hit_expr()
        ).count()
        
        correct = special_hit + normal_hit
        
        accuracy = (correct / updated * 100) if updated > 0 else 0
        special_hit_rate = (special_hit / updated * 100) if updated > 0 else 0
        normal_hit_rate = (normal_hit / updated * 100) if updated > 0 else 0
        
        return {
            'total': total,
            'updated': updated,
            'correct': correct,
            'wrong': wrong,
            'special_hit': special_hit,
            'normal_hit': normal_hit,
            'accuracy': round(accuracy, 1),
            'special_hit_rate': round(special_hit_rate, 1),
            'normal_hit_rate': round(normal_hit_rate, 1)
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
        
        day_predictions = PredictionRecord.query.filter(
            PredictionRecord.user_id == user.id,
            PredictionRecord.created_at >= date_start,
            PredictionRecord.created_at <= date_end
        ).count()
        
        trend_data.append({
            'date': date.strftime('%m-%d'),
            'count': day_predictions
        })
    
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
                          region_stats=region_stats,
                          recent_predictions=recent_predictions,
                          trend_data=trend_data,
                          get_number_color=get_number_color)




