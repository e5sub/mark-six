from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, Response
from functools import wraps
from models import (
    db,
    User,
    ActivationCode,
    ActivationCodeRequest,
    PredictionRecord,
    SystemConfig,
    InviteCode,
    ZodiacSetting,
    ManualBetRecord,
    LotteryDraw,
    BacktestRun,
)
from datetime import datetime, timedelta
import csv
import json
import io
from collections import OrderedDict
from sqlalchemy import func, case, or_

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

DATA_EXPORT_MODELS = [
    ('users', User),
    ('activation_codes', ActivationCode),
    ('activation_code_requests', ActivationCodeRequest),
    ('prediction_records', PredictionRecord),
    ('backtest_runs', BacktestRun),
    ('invite_codes', InviteCode),
    ('system_configs', SystemConfig),
    ('zodiac_settings', ZodiacSetting),
    ('manual_bet_records', ManualBetRecord),
    ('lottery_draws', LotteryDraw),
]

DATA_EXPORT_LABELS = {
    'users': '用户',
    'activation_codes': '激活码',
    'activation_code_requests': '激活码申请',
    'prediction_records': '预测记录',
    'backtest_runs': '回测记录',
    'invite_codes': '邀请码',
    'system_configs': '系统配置',
    'zodiac_settings': '生肖设置',
    'manual_bet_records': '下注记录',
    'lottery_draws': '开奖数据',
}

LEARNING_PANEL_TERM_LABELS = {
    'hot': '热门',
    'cold': '冷门',
    'trend': '走势',
    'balanced': '均衡',
    'hybrid': '综合',
    'ml': '机器学习',
    'ai': 'AI智能',
    'feedback': '反馈',
    'color': '波色',
    'normal': '平码',
    'overdue': '遗漏',
    'parity': '单双',
    'zodiac': '生肖',
}

PREDICTION_STRATEGY_LABELS = {
    'hot': '热门预测',
    'cold': '冷门预测',
    'trend': '走势预测',
    'balanced': '均衡预测',
    'hybrid': '综合预测',
    'ml': '机器学习预测',
    'ai': 'AI预测',
}

def _normalize_visual_weights(weight_map):
    cleaned = OrderedDict()
    total = 0.0
    for label, value in (weight_map or {}).items():
        try:
            numeric = max(0.0, float(value))
        except Exception:
            numeric = 0.0
        cleaned[label] = numeric
        total += numeric

    if total <= 0:
        return []

    items = []
    for label, numeric in cleaned.items():
        percent = round((numeric / total) * 100, 1)
        value = f"{int(percent)}%" if float(percent).is_integer() else f"{percent}%"
        items.append({
            'key': label,
            'label': label,
            'value': value,
        })
    return items


def _build_ml_visual_weights(config):
    runtime_profile = str(config.get('primary_runtime_profile') or 'base').strip()
    feature_profile = str(config.get('primary_feature_profile') or 'full').strip()

    weight_map = OrderedDict([
        ('历史样本', 26),
        ('近期走势', 18),
        ('策略共识', 18),
        ('单双参考', 13),
        ('波色参考', 13),
        ('生肖参考', 12),
    ])

    if runtime_profile == 'recent_bias':
        weight_map['近期走势'] += 8
        weight_map['历史样本'] -= 4
        weight_map['策略共识'] -= 4
    elif runtime_profile == 'context_bias':
        weight_map['单双参考'] += 4
        weight_map['波色参考'] += 4
        weight_map['生肖参考'] += 4
        weight_map['历史样本'] -= 6
        weight_map['近期走势'] -= 3
        weight_map['策略共识'] -= 3
    elif runtime_profile == 'recency_trim':
        weight_map['近期走势'] += 6
        weight_map['历史样本'] -= 6
    elif runtime_profile == 'learned_feature_bias':
        weight_map['策略共识'] += 5
        weight_map['单双参考'] += 2
        weight_map['波色参考'] += 2
        weight_map['历史样本'] -= 5
        weight_map['近期走势'] -= 2
        weight_map['生肖参考'] -= 2

    if feature_profile == 'compact_attributes':
        weight_map['单双参考'] += 3
        weight_map['波色参考'] += 3
        weight_map['生肖参考'] += 2
        weight_map['历史样本'] -= 4
        weight_map['近期走势'] -= 2
        weight_map['策略共识'] -= 2
    elif feature_profile == 'compact_structure':
        weight_map['策略共识'] += 4
        weight_map['近期走势'] += 2
        weight_map['历史样本'] += 1
        weight_map['单双参考'] -= 2
        weight_map['波色参考'] -= 2
        weight_map['生肖参考'] -= 3
    elif feature_profile == 'compact_recency':
        weight_map['近期走势'] += 6
        weight_map['历史样本'] -= 4
        weight_map['策略共识'] -= 2

    return _normalize_visual_weights(weight_map)


def _build_ai_visual_weights(config):
    history_window = max(1, int(config.get('history_window') or 12))
    temperature = max(0.0, float(config.get('temperature') or 0.35))

    weight_map = OrderedDict([
        ('历史样本', 30),
        ('近期走势', 18),
        ('单双参考', 14),
        ('波色参考', 14),
        ('生肖参考', 10),
        ('策略共识', 14),
    ])

    if history_window >= 18:
        weight_map['历史样本'] += 6
        weight_map['策略共识'] += 2
        weight_map['近期走势'] -= 3
        weight_map['波色参考'] -= 2
        weight_map['生肖参考'] -= 1
        weight_map['单双参考'] -= 2
    elif history_window <= 8:
        weight_map['近期走势'] += 5
        weight_map['单双参考'] += 2
        weight_map['波色参考'] += 2
        weight_map['历史样本'] -= 5
        weight_map['策略共识'] -= 4

    if temperature <= 0.3:
        weight_map['策略共识'] += 4
        weight_map['历史样本'] += 2
        weight_map['近期走势'] -= 2
        weight_map['生肖参考'] -= 2
        weight_map['波色参考'] -= 1
        weight_map['单双参考'] -= 1
    elif temperature >= 0.7:
        weight_map['近期走势'] += 4
        weight_map['生肖参考'] += 2
        weight_map['波色参考'] += 2
        weight_map['历史样本'] -= 4
        weight_map['策略共识'] -= 4

    return _normalize_visual_weights(weight_map)


def _build_strategy_visual_weights(strategy, config):
    weights = config.get('weights') or {}
    if weights:
        return [
            {
                'key': key,
                'label': LEARNING_PANEL_TERM_LABELS.get(key, key),
                'value': value
            }
            for key, value in sorted(weights.items())
        ]
    if strategy == 'ml':
        return _build_ml_visual_weights(config)
    if strategy == 'ai':
        return _build_ai_visual_weights(config)
    return []


def _serialize_model_row(instance):
    payload = {}
    for column in instance.__table__.columns:
        value = getattr(instance, column.name)
        if isinstance(value, datetime):
            payload[column.name] = value.isoformat()
        else:
            payload[column.name] = value
    return payload


def _parse_datetime_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间字段: {value}")


def _deserialize_model_row(model, row):
    values = {}
    for column in model.__table__.columns:
        name = column.name
        if name not in row:
            continue
        value = row.get(name)
        if value is None:
            values[name] = None
            continue
        python_type = None
        try:
            python_type = column.type.python_type
        except Exception:
            python_type = None
        if python_type is datetime:
            values[name] = _parse_datetime_value(value)
        elif python_type is bool:
            if isinstance(value, str):
                values[name] = value.strip().lower() in ("1", "true", "yes", "y", "on")
            else:
                values[name] = bool(value)
        elif python_type is int:
            values[name] = int(value)
        elif python_type is float:
            values[name] = float(value)
        else:
            values[name] = value
    return model(**values)


def _build_data_export_payload():
    exported_at = datetime.now().isoformat()
    data = {}
    counts = {}
    for key, model in DATA_EXPORT_MODELS:
        rows = model.query.order_by(model.id.asc()).all()
        data[key] = [_serialize_model_row(row) for row in rows]
        counts[key] = len(data[key])
    return {
        "meta": {
            "exported_at": exported_at,
            "version": 1,
        },
        "counts": counts,
        "data": data,
    }


def _validate_import_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("导入文件格式不正确")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("导入文件缺少 data 节点")

    users = data.get("users") or []
    if not any(bool(item.get("is_admin")) for item in users):
        raise ValueError("导入数据里至少需要保留一个管理员账号")


def _clear_all_data():
    delete_order = [
        ManualBetRecord,
        PredictionRecord,
        ActivationCodeRequest,
        ActivationCode,
        InviteCode,
        LotteryDraw,
        ZodiacSetting,
        SystemConfig,
        BacktestRun,
        User,
    ]
    for model in delete_order:
        db.session.query(model).delete()


def _import_data_payload(payload, mode):
    _validate_import_payload(payload)
    data = payload["data"]
    imported_counts = {}

    if mode == "replace":
        _clear_all_data()

    for key, model in DATA_EXPORT_MODELS:
        rows = data.get(key) or []
        imported_counts[key] = len(rows)
        for row in rows:
            instance = _deserialize_model_row(model, row)
            db.session.merge(instance)

    db.session.commit()
    ZodiacSetting._macau_year_match_cache.clear()
    return imported_counts


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            # 检查用户是否登录
            if 'user_id' not in session:
                flash('请先登录', 'error')
                return redirect(url_for('auth.login'))
            
            # 检查用户是否是管理员
            user = User.query.get(session['user_id'])
            if not user or not user.is_admin:
                flash('需要管理员权限才能访问此页面', 'error')
                return redirect(url_for('auth.login'))
            return f(*args, **kwargs)
        except Exception as e:
            flash(f'权限检查失败: {str(e)}', 'error')
            return redirect(url_for('auth.login'))
    return decorated_function

def _strategy_learning_panel_data():
    from app import _load_strategy_config, _get_strategy_label

    regions = [('hk', '香港'), ('macau', '澳门')]
    strategies = ['hot', 'cold', 'trend', 'balanced', 'hybrid', 'ml', 'ai']
    panel = []

    for region_key, region_label in regions:
        items = []
        for strategy in strategies:
            config = _load_strategy_config(strategy, region_key)
            weight_items = _build_strategy_visual_weights(strategy, config)
            mix = config.get('mix') or {}
            mix_items = [
                {
                    'key': key,
                    'label': LEARNING_PANEL_TERM_LABELS.get(key, key),
                    'value': value
                }
                for key, value in mix.items()
            ]
            items.append({
                'key': strategy,
                'display_key': LEARNING_PANEL_TERM_LABELS.get(strategy, strategy),
                'label': _get_strategy_label(strategy),
                'updated_at': config.get('updated_at', ''),
                'last_accuracy': round(float(config.get('last_accuracy') or 0.0) * 100, 1),
                'last_total': int(config.get('last_total') or 0),
                'accuracy_delta': round(float(config.get('accuracy_delta') or 0.0) * 100, 1),
                'window': config.get('window'),
                'trend_window': config.get('trend_window'),
                'history_window': config.get('history_window'),
                'feature_window': config.get('feature_window'),
                'pool': config.get('pool'),
                'special_pool': config.get('special_pool'),
                'epochs': config.get('epochs'),
                'learning_rate': config.get('learning_rate'),
                'bucket_counts': config.get('bucket_counts') or [],
                'mix': mix,
                'mix_items': mix_items,
                'weights': weight_items,
            })
        panel.append({
            'region_key': region_key,
            'region_label': region_label,
            'items': items,
        })
    return panel

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    try:
        # 获取统计数据
        now = datetime.now()
        week_ago = now - timedelta(days=7)
        expiring_cutoff = now + timedelta(days=3)

        total_users = User.query.count()
        active_users = User.query.filter_by(is_active=True).count()
        inactive_users = total_users - active_users
        total_codes = ActivationCode.query.count()
        used_codes = ActivationCode.query.filter_by(is_used=True).count()
        unused_codes = total_codes - used_codes
        total_predictions = PredictionRecord.query.count()
        recent_signups_7d = User.query.filter(User.created_at >= week_ago).count()
        recent_predictions_7d = PredictionRecord.query.filter(PredictionRecord.created_at >= week_ago).count()
        pending_predictions = PredictionRecord.query.filter(
            (PredictionRecord.is_result_updated.is_(False)) | (PredictionRecord.is_result_updated.is_(None))
        ).count()
        expiring_users_count = User.query.filter(
            User.is_active.is_(True),
            User.activation_expires_at.isnot(None),
            User.activation_expires_at >= now,
            User.activation_expires_at <= expiring_cutoff
        ).count()
        expired_active_users = User.query.filter(
            User.is_active.is_(True),
            User.activation_expires_at.isnot(None),
            User.activation_expires_at < now
        ).count()
        
        # 计算不同策略的准确率（只对比特码）
        def calculate_accuracy(strategy):
            predictions = PredictionRecord.query.filter_by(strategy=strategy, is_result_updated=True).all()
            if not predictions:
                return 0.0
            
            correct_count = 0
            total_count = 0
            
            for pred in predictions:
                if pred.actual_special_number and pred.special_number:
                    total_count += 1
                    if pred.special_number == pred.actual_special_number:
                        correct_count += 1
            
            return round(correct_count / total_count * 100, 1) if total_count > 0 else 0.0
        
        # 计算平均准确率（只对比特码）
        all_predictions = PredictionRecord.query.filter_by(is_result_updated=True).all()
        if all_predictions:
            correct_count = 0
            total_count = 0
            
            for pred in all_predictions:
                if pred.actual_special_number and pred.special_number:
                    total_count += 1
                    if pred.special_number == pred.actual_special_number:
                        correct_count += 1
            
            avg_accuracy = round(correct_count / total_count * 100, 1) if total_count > 0 else 0.0
        else:
            avg_accuracy = 0.0
        
        balanced_accuracy = calculate_accuracy('balanced')
        ai_accuracy = calculate_accuracy('ai')
        
        # 最近注册的用户
        recent_users = User.query.order_by(User.created_at.desc()).limit(6).all()
        
        # 最近的预测记录
        recent_predictions = PredictionRecord.query.order_by(PredictionRecord.created_at.desc()).limit(6).all()
        
        # 为预测记录添加用户名
        for pred in recent_predictions:
            if pred.user_id:
                user = User.query.get(pred.user_id)
                pred.username = user.username if user else '已删除用户'
            else:
                pred.username = '未知用户'
        
        # 获取邀请统计数据
        expiring_users = User.query.filter(
            User.is_active.is_(True),
            User.activation_expires_at.isnot(None),
            User.activation_expires_at >= now,
            User.activation_expires_at <= expiring_cutoff
        ).order_by(User.activation_expires_at.asc()).limit(6).all()

        total_invite_codes = InviteCode.query.count()
        used_invite_codes = InviteCode.query.filter_by(is_used=True).count()
        unused_invite_codes = total_invite_codes - used_invite_codes
        actionable_cards = [
            {
                'title': '待开奖预测',
                'value': pending_predictions,
                'hint': '优先检查开奖同步和结果回填',
                'url': url_for('admin.predictions'),
                'tone': 'warning',
            },
            {
                'title': '3天内到期用户',
                'value': expiring_users_count,
                'hint': '适合主动提醒续费或重新激活',
                'url': url_for('admin.users'),
                'tone': 'danger' if expiring_users_count else 'success',
            },
            {
                'title': '失效但仍激活',
                'value': expired_active_users,
                'hint': '大于 0 时建议尽快核查账号状态',
                'url': url_for('admin.users'),
                'tone': 'danger' if expired_active_users else 'success',
            },
            {
                'title': '未使用邀请码',
                'value': unused_invite_codes,
                'hint': '可直接用于拉新或补充库存',
                'url': url_for('admin.invite_codes'),
                'tone': 'info',
            },
        ]
        total_invites = User.query.filter(User.invited_by.isnot(None)).count()
        
        invite_stats = {
            'total_invite_codes': total_invite_codes,
            'used_invite_codes': used_invite_codes,
            'unused_invite_codes': unused_invite_codes,
            'total_invites': total_invites
        }
        
        stats = {
            'total_users': total_users,
            'active_users': active_users,
            'inactive_users': inactive_users,
            'total_codes': total_codes,
            'used_codes': used_codes,
            'unused_codes': unused_codes,
            'total_predictions': total_predictions,
            'avg_accuracy': avg_accuracy,
            'balanced_accuracy': balanced_accuracy,
            'ai_accuracy': ai_accuracy,
            'recent_signups_7d': recent_signups_7d,
            'recent_predictions_7d': recent_predictions_7d,
            'pending_predictions': pending_predictions,
            'expiring_users_count': expiring_users_count,
            'expired_active_users': expired_active_users,
            'total_invite_codes': total_invite_codes,
            'used_invite_codes': used_invite_codes,
            'unused_invite_codes': unused_invite_codes,
            'recent_users': recent_users,
            'recent_predictions': recent_predictions,
            'expiring_users': expiring_users,
            'actionable_cards': actionable_cards,
            'invite_stats': invite_stats
        }
        
        return render_template('admin/dashboard.html', stats=stats)
    except Exception as e:
        flash(f'加载控制台数据失败: {str(e)}', 'error')
        return render_template('admin/dashboard.html', stats={
            'total_users': 0,
            'active_users': 0,
            'inactive_users': 0,
            'total_codes': 0,
            'used_codes': 0,
            'unused_codes': 0,
            'total_predictions': 0,
            'avg_accuracy': 0.0,
            'balanced_accuracy': 0.0,
            'ai_accuracy': 0.0,
            'recent_signups_7d': 0,
            'recent_predictions_7d': 0,
            'pending_predictions': 0,
            'expiring_users_count': 0,
            'expired_active_users': 0,
            'total_invite_codes': 0,
            'used_invite_codes': 0,
            'unused_invite_codes': 0,
            'recent_users': [],
            'recent_predictions': [],
            'expiring_users': [],
            'actionable_cards': [],
            'invite_stats': {
                'total_invite_codes': 0,
                'used_invite_codes': 0,
                'unused_invite_codes': 0,
                'total_invites': 0
            }
        })


@admin_bp.route('/data_transfer')
@admin_required
def data_transfer():
    summary = []
    for key, model in DATA_EXPORT_MODELS:
        try:
            count = model.query.count()
        except Exception:
            count = 0
        summary.append({
            'key': key,
            'label': DATA_EXPORT_LABELS.get(key, key),
            'count': count,
        })
    return render_template('admin/data_transfer.html', summary=summary)


@admin_bp.route('/data_transfer/export')
@admin_required
def export_all_data():
    try:
        payload = _build_data_export_payload()
        exported_at = datetime.now().strftime('%Y%m%d_%H%M%S')
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            mimetype='application/json',
            headers={
                'Content-Disposition': f'attachment; filename=mark_six_backup_{exported_at}.json'
            }
        )
    except Exception as e:
        flash(f'导出全部数据失败: {str(e)}', 'error')
        return redirect(url_for('admin.data_transfer'))


@admin_bp.route('/data_transfer/import', methods=['POST'])
@admin_required
def import_all_data():
    try:
        upload = request.files.get('file')
        if not upload or not upload.filename:
            flash('请选择要导入的 JSON 备份文件', 'error')
            return redirect(url_for('admin.data_transfer'))

        payload = json.load(upload.stream)
        mode = str(request.form.get('import_mode') or 'merge').strip().lower()
        if mode not in ('merge', 'replace'):
            mode = 'merge'

        imported_counts = _import_data_payload(payload, mode)
        flash(
            f"全部数据导入成功，模式：{'覆盖现有数据' if mode == 'replace' else '按主键合并'}。",
            'success'
        )
        flash(
            "；".join(f"{key} {count} 条" for key, count in imported_counts.items()),
            'success'
        )
    except Exception as e:
        db.session.rollback()
        flash(f'导入全部数据失败: {str(e)}', 'error')
    return redirect(url_for('admin.data_transfer'))

@admin_bp.route('/users')
@admin_required
def users():
    try:
        page = request.args.get('page', 1, type=int)
        search_query = request.args.get('search', '')
        
        # 构建查询
        query = User.query
        
        # 如果有搜索关键词，添加搜索条件
        if search_query:
            search_term = f"%{search_query}%"
            query = query.filter(
                (User.username.like(search_term)) | 
                (User.email.like(search_term))
            )
        
        # 分页
        users = query.paginate(
            page=page, per_page=20, error_out=False
        )
        
        return render_template('admin/users.html', users=users, search_query=search_query)
    except Exception as e:
        flash(f'加载用户数据失败: {str(e)}', 'error')
        # 创建空的分页对象
        # 创建空的分页对象
        class EmptyPagination:
            def __init__(self):
                self.items = []
                self.page = 1
                self.per_page = 20
                self.total = 0
                self.pages = 0
                self.has_prev = False
                self.has_next = False
                self.prev_num = None
                self.next_num = None
        
        empty_users = EmptyPagination()
        return render_template('admin/users.html', users=empty_users)

@admin_bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        
        if request.method == 'POST':
            # 获取表单数据
            new_username = request.form.get('username')
            new_email = request.form.get('email')
            new_password = request.form.get('new_password')
            is_active = 'is_active' in request.form
            is_admin = 'is_admin' in request.form

            # 保存原始用户名，用于判断是否是admin账号
            original_username = user.username

            print(f"DEBUG: original_username={original_username}, is_active={is_active}, user.is_active={user.is_active}")

            # 对于admin账号，强制保持激活状态
            if original_username == 'admin':
                is_active = True
                print(f"DEBUG: 设置admin用户is_active=True")

            # 防止停用admin账号
            if original_username == 'admin' and not is_active:
                flash('不能停用admin账号', 'error')
                return render_template('admin/edit_user.html', user=user)

            # 更新用户信息
            user.username = new_username
            user.email = new_email
            
            # 如果由未激活状态变为激活状态，默认开启预测
            if is_active and not user.is_active:
                user.auto_prediction_enabled = True
            user.is_active = is_active

            # 如果是admin账号，保持管理员权限
            if original_username == 'admin':
                user.is_admin = True
            else:
                user.is_admin = is_admin
            
            # 如果提供了新密码，则更新密码
            if new_password:
                user.set_password(new_password)
            
            # 处理激活过期时间
            activation_expires_at = request.form.get('activation_expires_at')
            if activation_expires_at:
                try:
                    user.activation_expires_at = datetime.strptime(activation_expires_at, '%Y-%m-%dT%H:%M')
                except ValueError:
                    flash('激活过期时间格式无效', 'error')
                    return render_template('admin/edit_user.html', user=user)
            else:
                # 如果用户是激活状态，则设置为永久有效期，否则不设置有效期
                if user.is_active:
                    user.activation_expires_at = None
                else:
                    # 未激活用户不应该有有效期
                    user.activation_expires_at = None
            
            try:
                db.session.commit()
                flash('用户信息更新成功', 'success')
                return redirect(url_for('admin.users'))
            except Exception as e:
                db.session.rollback()
                flash(f'更新失败: {str(e)}', 'error')
        
        return render_template('admin/edit_user.html', user=user)
    except Exception as e:
        flash(f'编辑用户失败: {str(e)}', 'error')
        return redirect(url_for('admin.users'))

@admin_bp.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    """添加新用户"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的数据格式'})

        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '')
        is_admin = data.get('is_admin', False)

        # 验证输入
        if not username:
            return jsonify({'success': False, 'message': '用户名不能为空'})
        if not email:
            return jsonify({'success': False, 'message': '邮箱不能为空'})
        if not password or len(password) < 6:
            return jsonify({'success': False, 'message': '密码长度不能少于6个字符'})

        # 检查用户名是否已存在
        if User.query.filter_by(username=username).first():
            return jsonify({'success': False, 'message': '用户名已存在'})

        # 检查邮箱是否已存在
        if User.query.filter_by(email=email).first():
            return jsonify({'success': False, 'message': '邮箱已被使用'})

        # 创建新用户
        user = User(username=username, email=email, is_active=True, is_admin=is_admin)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        return jsonify({'success': True, 'message': '用户添加成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@admin_bp.route('/users/<int:user_id>/activate', methods=['POST'])
@admin_required
def activate_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        user.is_active = True
        user.auto_prediction_enabled = True
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@admin_bp.route('/users/<int:user_id>/deactivate', methods=['POST'])
@admin_required
def deactivate_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        
        # 防止停用admin账号
        if user.username == 'admin':
            return jsonify({'success': False, 'message': '不能停用admin账号'})
        
        user.is_active = False
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@admin_bp.route('/users/<int:user_id>/reset_password', methods=['POST'])
@admin_required
def reset_user_password(user_id):
    try:
        user = User.query.get_or_404(user_id)
        data = request.get_json()
        
        if not data or 'new_password' not in data:
            return jsonify({'success': False, 'message': '缺少新密码参数'})
        
        new_password = data['new_password']
        if not new_password or len(new_password) < 6:
            return jsonify({'success': False, 'message': '密码长度不能少于6个字符'})
        
        user.set_password(new_password)
        db.session.commit()
        return jsonify({'success': True, 'message': '密码重置成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

@admin_bp.route('/users/<int:user_id>/delete', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        
        # 防止删除管理员账号
        if user.is_admin:
            flash('不能删除管理员账号', 'error')
            return redirect(url_for('admin.users'))
        
        db.session.delete(user)
        db.session.commit()
        flash('用户删除成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除用户失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.users'))

@admin_bp.route('/activation_codes')
@admin_required
def activation_codes():
    """激活码管理页面 - 使用AJAX加载数据"""
    try:
        # 不再在这里加载数据，而是通过JavaScript从API获取
        return render_template('admin/activation_codes.html', now=datetime.utcnow())
    except Exception as e:
        flash(f'加载激活码页面失败: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

# 删除generate_codes函数，因为已经在activation_code_routes.py中实现

SYSTEM_CONFIG_DEFAULTS = {
    'ai_api_key': '',
    'ai_api_url': 'https://api.deepseek.com/v1/chat/completions',
    'ai_model': 'deepseek-chat',
    'smtp_server': '',
    'smtp_port': '587',
    'smtp_username': '',
    'smtp_password': '',
    'activation_request_notify_emails': '',
    'site_name': 'AI数据分析预测系统',
    'site_description': '',
    'invite_daily_limit': '3',
    'invite_code_validity_days': '7',
    'system_name': 'AI数据分析预测系统',
    'system_description': '',
    'allow_registration': 'true',
    'require_email_verification': 'false',
    'enable_personalized_predictions': 'false',
}

@admin_bp.route('/system_config', methods=['GET', 'POST'])
@admin_required
def system_config():
    try:
        if request.method == 'POST':
            configs = {
                key: request.form.get(key, default)
                for key, default in SYSTEM_CONFIG_DEFAULTS.items()
            }

            try:
                for key, value in configs.items():
                    SystemConfig.set_config(key, value)
                flash('系统配置更新成功', 'success')
            except Exception as e:
                flash(f'配置更新失败: {str(e)}', 'error')

            return redirect(url_for('admin.system_config'))

        configs = {
            key: SystemConfig.get_config(key, default)
            for key, default in SYSTEM_CONFIG_DEFAULTS.items()
        }

        return render_template(
            'admin/system_config.html',
            configs=configs,
            learning_panel=_strategy_learning_panel_data(),
        )
    except Exception as e:
        flash(f'加载系统配置失败: {str(e)}', 'error')
        return render_template(
            'admin/system_config.html',
            configs=SYSTEM_CONFIG_DEFAULTS,
            learning_panel=[],
        )

@admin_bp.route('/system_config/save', methods=['POST'])
@admin_required
def save_system_config():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'message': '无效的数据格式'})

        for key, value in data.items():
            SystemConfig.set_config(key, value)

        return jsonify({'success': True, 'message': '配置保存成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@admin_bp.route('/system_config/retrain_learning', methods=['POST'])
@admin_required
def retrain_learning_configs():
    try:
        from app import update_strategy_configs

        payload = request.get_json(silent=True) or {}
        region = (payload.get('region') or 'all').strip()
        targets = ['hk', 'macau'] if region in ('', 'all') else [region]
        allowed = {'hk', 'macau'}
        targets = [item for item in targets if item in allowed]
        if not targets:
            return jsonify({'success': False, 'message': '无效的地区参数'})

        refreshed = []
        for item in targets:
            update_strategy_configs(item)
            refreshed.append(item)

        return jsonify({
            'success': True,
            'message': f"已重算 {', '.join(refreshed)} 的学习参数",
            'learning_panel': _strategy_learning_panel_data(),
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@admin_bp.route('/predictions')
@admin_required
def predictions():
    try:
        page = request.args.get('page', 1, type=int)
        user_query = request.args.get('user', '').strip()
        region_filter = request.args.get('region', '').strip()
        strategy_filter = request.args.get('strategy', '').strip()
        period_filter = request.args.get('period', '').strip()

        filters = []
        if user_query:
            if user_query.isdigit():
                filters.append(PredictionRecord.user_id == int(user_query))
            else:
                search_term = f"%{user_query}%"
                user_ids = User.query.filter(
                    (User.username.like(search_term)) | (User.email.like(search_term))
                ).with_entities(User.id)
                filters.append(PredictionRecord.user_id.in_(user_ids))

        if region_filter:
            filters.append(PredictionRecord.region == region_filter)

        if strategy_filter:
            filters.append(PredictionRecord.strategy == strategy_filter)

        if period_filter:
            filters.append(PredictionRecord.period.contains(period_filter))

        groups_query = db.session.query(
            PredictionRecord.region.label('region'),
            PredictionRecord.period.label('period'),
            func.max(PredictionRecord.created_at).label('latest_created_at'),
            func.count(PredictionRecord.id).label('record_count'),
            func.count(func.distinct(PredictionRecord.user_id)).label('user_count')
        )
        if filters:
            groups_query = groups_query.filter(*filters)

        predictions = groups_query.group_by(
            PredictionRecord.region,
            PredictionRecord.period
        ).order_by(
            func.max(PredictionRecord.created_at).desc()
        ).paginate(
            page=page, per_page=10, error_out=False
        )

        group_keys = [(item.region, item.period) for item in predictions.items]

        page_records = []
        if group_keys:
            group_conditions = [
                ((PredictionRecord.region == region) & (PredictionRecord.period == period))
                for region, period in group_keys
            ]
            page_query = PredictionRecord.query.filter(or_(*group_conditions))
            if filters:
                page_query = page_query.filter(*filters)
            page_records = page_query.order_by(
                PredictionRecord.created_at.desc(),
                PredictionRecord.id.desc()
            ).all()

        regions = {
            item.region
            for item in predictions.items
            if item.region
        }

        prediction_summary_cards = []
        for region in regions:
            history_query = PredictionRecord.query.filter(PredictionRecord.region == region)
            if filters:
                history_query = history_query.filter(*filters)
            history_records = history_query.order_by(
                PredictionRecord.created_at.asc(),
                PredictionRecord.id.asc()
            ).all()

            period_results = OrderedDict()

            for record in history_records:
                period_key = record.period
                if period_key not in period_results:
                    period_results[period_key] = {
                        'has_result': False,
                        'is_hit': False,
                    }

                if (
                    record.is_result_updated
                    and record.special_number
                    and record.actual_special_number
                ):
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

            prediction_summary_cards.append({
                'region': region,
                'region_label': '香港' if region == 'hk' else '澳门' if region == 'macau' else region,
                'hit_periods': total_special_hits,
                'miss_streak': consecutive_special_misses,
                'max_hit_streak': max_consecutive_special_hits,
                'max_miss_streak': max_consecutive_special_misses,
                'resolved_periods': resolved_periods,
            })

        prediction_summary_cards.sort(
            key=lambda item: 0 if item['region'] == 'hk' else 1 if item['region'] == 'macau' else 2
        )
        
        pending_updates = []
        # 为预测记录添加用户名，并兜底补齐缺失生肖
        strategy_order = {
            'hot': 1,
            'cold': 2,
            'trend': 3,
            'hybrid': 4,
            'balanced': 5,
            'ml': 6,
            'ai': 7,
        }
        
        personalized_enabled = str(SystemConfig.get_config('enable_personalized_predictions', 'false')).strip().lower() == 'true'

        for pred in page_records:
            if personalized_enabled:
                if pred.user_id:
                    user = User.query.get(pred.user_id)
                    pred.username = user.username if user else '已删除用户'
                else:
                    pred.username = '未知用户'
            else:
                pred.username = ''

            pred.strategy_label = PREDICTION_STRATEGY_LABELS.get(pred.strategy, pred.strategy)
            pred.strategy_sort = strategy_order.get(pred.strategy, 99)

            pred.display_special_zodiac = (pred.special_zodiac or '').strip()
            if not pred.display_special_zodiac and pred.special_number:
                try:
                    zodiac_year = ZodiacSetting.get_zodiac_year_for_date(
                        pred.created_at or datetime.now()
                    )
                    pred.display_special_zodiac = (
                        ZodiacSetting.get_zodiac_for_number(
                            zodiac_year, pred.special_number
                        ) or ''
                    ).strip()
                    if pred.display_special_zodiac:
                        pred.special_zodiac = pred.display_special_zodiac
                        pending_updates.append(pred)
                except Exception:
                    pred.display_special_zodiac = ''

            pred.display_actual_special_zodiac = (pred.actual_special_zodiac or '').strip()
            if not pred.display_actual_special_zodiac and pred.actual_special_number:
                try:
                    zodiac_year = ZodiacSetting.get_zodiac_year_for_date(
                        pred.created_at or datetime.now()
                    )
                    pred.display_actual_special_zodiac = (
                        ZodiacSetting.get_zodiac_for_number(
                            zodiac_year, pred.actual_special_number
                        ) or ''
                    ).strip()
                    if pred.display_actual_special_zodiac:
                        pred.actual_special_zodiac = pred.display_actual_special_zodiac
                        pending_updates.append(pred)
                except Exception:
                    pred.display_actual_special_zodiac = ''

            normal_numbers = [
                value.strip()
                for value in str(pred.normal_numbers or '').split(',')
                if value.strip()
            ]
            pred.normal_numbers_list = normal_numbers
            actual_special = str(pred.actual_special_number or '').strip()
            pred.is_special_hit = bool(
                pred.is_result_updated
                and actual_special
                and str(pred.special_number or '').strip() == actual_special
            )
            pred.is_normal_number_hit = bool(
                pred.is_result_updated
                and actual_special
                and not pred.is_special_hit
                and actual_special in normal_numbers
            )
            pred.is_zodiac_hit = bool(
                pred.is_result_updated
                and actual_special
                and not pred.is_special_hit
                and not pred.is_normal_number_hit
                and pred.display_special_zodiac
                and pred.display_actual_special_zodiac
                and pred.display_special_zodiac == pred.display_actual_special_zodiac
            )
            if pred.is_special_hit:
                pred.result_label = '特码命中'
                pred.result_class = 'hit'
            elif pred.is_normal_number_hit:
                pred.result_label = '平码命中'
                pred.result_class = 'partial'
            elif pred.is_zodiac_hit:
                pred.result_label = '生肖命中'
                pred.result_class = 'partial'
            elif pred.is_result_updated:
                pred.result_label = '未命中'
                pred.result_class = 'miss'
            else:
                pred.result_label = '待开奖'
                pred.result_class = 'pending'

        if pending_updates:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        prediction_groups_map = OrderedDict()
        group_meta_map = {
            f"{item.region}-{item.period}": item
            for item in predictions.items
        }

        for pred in page_records:
            group_key = f"{pred.region}-{pred.period}"
            if group_key not in prediction_groups_map:
                group_meta = group_meta_map.get(group_key)
                prediction_groups_map[group_key] = {
                    'key': group_key,
                    'region': pred.region,
                    'period': pred.period,
                    'actual_special_number': pred.actual_special_number,
                    'display_actual_special_zodiac': pred.display_actual_special_zodiac,
                    'created_at': getattr(group_meta, 'latest_created_at', None) or pred.created_at,
                    'record_count': int(getattr(group_meta, 'record_count', 0) or 0),
                    'user_count': int(getattr(group_meta, 'user_count', 0) or 0),
                    'items': [],
                    '_items_by_signature': {},
                    '_seen_strategies': set(),
                    '_seen_prediction_signatures': set(),
                    '_users': set(),
                }

            group = prediction_groups_map[group_key]

            prediction_signature = (
                str(pred.strategy or '').strip(),
                str(pred.special_number or '').strip(),
                ','.join(pred.normal_numbers_list),
            )
            if prediction_signature in group['_seen_prediction_signatures']:
                existing_item = group['_items_by_signature'].get(prediction_signature)
                if existing_item:
                    existing_item.merged_record_ids.append(pred.id)
                    if pred.username and pred.username not in existing_item.merged_usernames:
                        existing_item.merged_usernames.append(pred.username)
                    existing_item.merged_user_count = len(existing_item.merged_usernames)
                continue
            group['_seen_prediction_signatures'].add(prediction_signature)
            
            if not personalized_enabled:
                if pred.strategy in group['_seen_strategies']:
                    continue
                group['_seen_strategies'].add(pred.strategy)

            if pred.actual_special_number and not group['actual_special_number']:
                group['actual_special_number'] = pred.actual_special_number
            if pred.display_actual_special_zodiac and not group['display_actual_special_zodiac']:
                group['display_actual_special_zodiac'] = pred.display_actual_special_zodiac
            if pred.created_at and (group['created_at'] is None or pred.created_at > group['created_at']):
                group['created_at'] = pred.created_at
            if pred.username:
                group['_users'].add(pred.username)
            pred.merged_record_ids = [pred.id]
            pred.merged_usernames = [pred.username] if pred.username else []
            pred.merged_user_count = len(pred.merged_usernames)
            group['items'].append(pred)
            group['_items_by_signature'][prediction_signature] = pred

        for group in prediction_groups_map.values():
            group['items'].sort(
                key=lambda item: (
                    item.username or '',
                    item.strategy_sort,
                    -(item.id or 0)
                )
            )
            for i, item in enumerate(group['items']):
                item.is_first_in_group = (i == 0)
                item.group_rowspan = len(group['items'])
            group['hit_count'] = sum(1 for item in group['items'] if item.is_special_hit)
            group['partial_count'] = sum(1 for item in group['items'] if item.is_normal_number_hit or item.is_zodiac_hit)
            group['pending_count'] = sum(1 for item in group['items'] if not item.is_result_updated)
            group['miss_count'] = sum(1 for item in group['items'] if item.is_result_updated and item.result_class == 'miss')
            group['strategy_count'] = len(group['items'])
            group['usernames'] = sorted(group['_users'])
            group['top_usernames'] = group['usernames'][:3]
            group['more_user_count'] = max(0, len(group['usernames']) - len(group['top_usernames']))
            if group['actual_special_number']:
                group['group_result_class'] = 'hit' if group['hit_count'] > 0 else 'miss'
                group['group_result_label'] = '本期有策略命中特码' if group['hit_count'] > 0 else '本期未命中特码'
            else:
                group['group_result_class'] = 'pending'
                group['group_result_label'] = '待开奖'
            del group['_users']
            del group['_items_by_signature']
            del group['_seen_prediction_signatures']

        prediction_groups = [
            prediction_groups_map[f"{region}-{period}"]
            for region, period in group_keys
            if f"{region}-{period}" in prediction_groups_map
        ]

        return render_template(
            'admin/predictions.html',
            predictions=predictions,
            prediction_groups=prediction_groups,
            prediction_summary_cards=prediction_summary_cards,
            user_query=user_query,
            region_filter=region_filter,
            strategy_filter=strategy_filter,
            period_filter=period_filter,
        )
    except Exception as e:
        flash(f'加载预测记录失败: {str(e)}', 'error')
        # 创建空的分页对象
        class EmptyPagination:
            def __init__(self):
                self.items = []
                self.page = 1
                self.per_page = 20
                self.total = 0
                self.pages = 0
                self.has_prev = False
                self.has_next = False
                self.prev_num = None
                self.next_num = None
        
        empty_predictions = EmptyPagination()
        return render_template(
            'admin/predictions.html',
            predictions=empty_predictions,
            prediction_groups=[],
            prediction_summary_cards=[],
            user_query='',
            region_filter='',
            strategy_filter='',
            period_filter='',
        )

@admin_bp.route('/bets')
@admin_required
def bets():
    try:
        page = request.args.get('page', 1, type=int)
        user_query = request.args.get('user', '').strip()
        region = request.args.get('region', '').strip()
        period = request.args.get('period', '').strip()
        status = request.args.get('status', '').strip()
        start_date = request.args.get('start_date', '').strip()
        end_date = request.args.get('end_date', '').strip()

        query = ManualBetRecord.query
        filters = []

        if user_query:
            if user_query.isdigit():
                filters.append(ManualBetRecord.user_id == int(user_query))
            else:
                search_term = f"%{user_query}%"
                user_ids = User.query.filter(
                    (User.username.like(search_term)) | (User.email.like(search_term))
                ).with_entities(User.id)
                filters.append(ManualBetRecord.user_id.in_(user_ids))

        if region:
            filters.append(ManualBetRecord.region == region)

        if period:
            filters.append(ManualBetRecord.period.contains(period))

        if start_date:
            try:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
                filters.append(ManualBetRecord.created_at >= start_date_obj)
            except ValueError:
                flash('开始日期格式不正确', 'error')

        if end_date:
            try:
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
                end_date_obj = end_date_obj.replace(hour=23, minute=59, second=59)
                filters.append(ManualBetRecord.created_at <= end_date_obj)
            except ValueError:
                flash('结束日期格式不正确', 'error')

        if status:
            if status == 'pending':
                filters.append(ManualBetRecord.total_profit.is_(None))
            elif status == 'settled':
                filters.append(ManualBetRecord.total_profit.isnot(None))
            elif status == 'win':
                filters.append(ManualBetRecord.total_profit > 0)
            elif status == 'lose':
                filters.append(ManualBetRecord.total_profit < 0)
            elif status == 'draw':
                filters.append(ManualBetRecord.total_profit == 0)

        if filters:
            query = query.filter(*filters)

        stats_row = db.session.query(
            func.count(ManualBetRecord.id),
            func.sum(ManualBetRecord.total_stake),
            func.sum(ManualBetRecord.total_profit),
            func.sum(case((ManualBetRecord.total_profit > 0, 1), else_=0)),
            func.sum(case((ManualBetRecord.total_profit < 0, 1), else_=0)),
            func.sum(case((ManualBetRecord.total_profit == 0, 1), else_=0)),
            func.sum(case((ManualBetRecord.total_profit.is_(None), 1), else_=0))
        )
        if filters:
            stats_row = stats_row.filter(*filters)

        stats = stats_row.one()

        bets = query.order_by(ManualBetRecord.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )

        user_ids = {record.user_id for record in bets.items}
        user_map = {}
        if user_ids:
            users = User.query.filter(User.id.in_(user_ids)).all()
            user_map = {user.id: user.username for user in users}

        summary = {
            'total': stats[0] or 0,
            'total_stake': stats[1] or 0,
            'total_profit': stats[2] or 0,
            'win_count': stats[3] or 0,
            'lose_count': stats[4] or 0,
            'draw_count': stats[5] or 0,
            'pending_count': stats[6] or 0
        }

        return render_template(
            'admin/bets.html',
            bets=bets,
            user_map=user_map,
            summary=summary,
            user_query=user_query,
            region=region,
            period=period,
            status=status,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        flash(f'加载下注记录失败: {str(e)}', 'error')
        class EmptyPagination:
            def __init__(self):
                self.items = []
                self.page = 1
                self.per_page = 20
                self.total = 0
                self.pages = 0
                self.has_prev = False
                self.has_next = False
                self.prev_num = None
                self.next_num = None

        empty_bets = EmptyPagination()
        return render_template(
            'admin/bets.html',
            bets=empty_bets,
            user_map={},
            summary={
                'total': 0,
                'total_stake': 0,
                'total_profit': 0,
                'win_count': 0,
                'lose_count': 0,
                'draw_count': 0,
                'pending_count': 0
            },
            user_query=user_query,
            region=region,
            period=period,
            status=status,
            start_date=start_date,
            end_date=end_date
        )

@admin_bp.route('/bets/<int:bet_id>/delete', methods=['POST'])
@admin_required
def delete_bet(bet_id):
    try:
        record = ManualBetRecord.query.get(bet_id)
        if not record:
            flash('下注记录不存在', 'error')
            return redirect(request.referrer or url_for('admin.bets'))
        db.session.delete(record)
        db.session.commit()
        flash('下注记录删除成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除下注记录失败: {str(e)}', 'error')
    return redirect(request.referrer or url_for('admin.bets'))

@admin_bp.route('/prediction/<int:prediction_id>/delete', methods=['POST'])
@admin_required
def delete_prediction(prediction_id):
    try:
        prediction = PredictionRecord.query.get_or_404(prediction_id)
        duplicate_predictions = PredictionRecord.query.filter(
            PredictionRecord.region == prediction.region,
            PredictionRecord.period == prediction.period,
            PredictionRecord.strategy == prediction.strategy,
            PredictionRecord.special_number == prediction.special_number,
            PredictionRecord.normal_numbers == prediction.normal_numbers,
        ).all()
        deleted_count = 0
        for item in duplicate_predictions:
            db.session.delete(item)
            deleted_count += 1
        db.session.commit()
        flash('预测记录删除成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除预测记录失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.predictions'))

@admin_bp.route('/predictions/delete_batch', methods=['POST'])
@admin_required
def delete_predictions_batch():
    try:
        prediction_ids = request.form.getlist('prediction_ids')
        if not prediction_ids:
            flash('请选择要删除的预测记录', 'error')
            return redirect(url_for('admin.predictions'))
        
        signature_filters = set()
        for pred_id in prediction_ids:
            prediction = PredictionRecord.query.get(int(pred_id))
            if prediction:
                signature_filters.add((
                    prediction.region,
                    prediction.period,
                    prediction.strategy,
                    prediction.special_number,
                    prediction.normal_numbers,
                ))

        deleted_count = 0
        for region, period, strategy, special_number, normal_numbers in signature_filters:
            matched_predictions = PredictionRecord.query.filter(
                PredictionRecord.region == region,
                PredictionRecord.period == period,
                PredictionRecord.strategy == strategy,
                PredictionRecord.special_number == special_number,
                PredictionRecord.normal_numbers == normal_numbers,
            ).all()
            for prediction in matched_predictions:
                db.session.delete(prediction)
                deleted_count += 1
        
        db.session.commit()
        flash(f'成功删除 {deleted_count} 条预测记录', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'批量删除失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.predictions'))

@admin_bp.route('/predictions/clear_all', methods=['POST'])
@admin_required
def clear_all_predictions():
    try:
        count = PredictionRecord.query.count()
        PredictionRecord.query.delete()
        db.session.commit()
        flash(f'成功清空所有预测记录，共删除 {count} 条记录', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'清空预测记录失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.predictions'))

@admin_bp.route('/export_users')
@admin_required
def export_users():
    try:
        users = User.query.all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 写入标题行
        writer.writerow(['ID', '用户名', '邮箱', '是否激活', '是否管理员', '注册时间', '激活过期时间'])
        
        # 写入数据行
        for user in users:
            writer.writerow([
                user.id,
                user.username,
                user.email,
                '是' if user.is_active else '否',
                '是' if user.is_admin else '否',
                user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else '',
                user.activation_expires_at.strftime('%Y-%m-%d %H:%M:%S') if user.activation_expires_at else '永久'
            ])
        
        output.seek(0)
        
        from flask import Response
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=users.csv'}
        )
    except Exception as e:
        flash(f'导出用户数据失败: {str(e)}', 'error')
        return redirect(url_for('admin.users'))

@admin_bp.route('/import_users', methods=['GET', 'POST'])
@admin_required
def import_users():
    if request.method == 'POST':
        try:
            if 'file' not in request.files:
                flash('请选择文件', 'error')
                return redirect(url_for('admin.import_users'))
            
            file = request.files['file']
            if file.filename == '':
                flash('请选择文件', 'error')
                return redirect(url_for('admin.import_users'))
            
            if not file.filename.endswith('.csv'):
                flash('请上传CSV文件', 'error')
                return redirect(url_for('admin.import_users'))
            
            # 读取CSV文件
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_input = csv.reader(stream)
            
            # 跳过标题行
            next(csv_input)
            
            imported_count = 0
            for row in csv_input:
                if len(row) >= 3:  # 至少需要用户名、邮箱、密码
                    username, email, password = row[0], row[1], row[2]
                    
                    # 检查用户是否已存在
                    if not User.query.filter_by(username=username).first():
                        user = User(username=username, email=email)
                        user.set_password(password)
                        db.session.add(user)
                        imported_count += 1
            
            db.session.commit()
            flash(f'成功导入 {imported_count} 个用户', 'success')
            return redirect(url_for('admin.users'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'导入用户失败: {str(e)}', 'error')
    
    return render_template('admin/import_users.html')

# 邀请码管理路由
@admin_bp.route('/invite_codes')
@admin_required
def invite_codes():
    try:
        page = request.args.get('page', 1, type=int)
        codes = InviteCode.query.order_by(InviteCode.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        return render_template('admin/invite_codes.html', codes=codes)
    except Exception as e:
        flash(f'加载邀请码数据失败: {str(e)}', 'error')
        class EmptyPagination:
            def __init__(self):
                self.items = []
                self.page = 1
                self.per_page = 20
                self.total = 0
                self.pages = 0
                self.has_prev = False
                self.has_next = False
                self.prev_num = None
                self.next_num = None
        
        empty_codes = EmptyPagination()
        return render_template('admin/invite_codes.html', codes=empty_codes)

@admin_bp.route('/generate_invite_codes', methods=['POST'])
@admin_required
def generate_invite_codes():
    try:
        count = int(request.form.get('count', 1))
        expires_days = request.form.get('expires_days', '')
        
        if count < 1 or count > 50:
            flash('生成数量必须在1-50之间', 'error')
            return redirect(url_for('admin.invite_codes'))
        
        # 获取当前管理员用户名
        admin_user = User.query.get(session['user_id'])
        admin_username = admin_user.username if admin_user else 'admin'
        
        generated_codes = []
        for _ in range(count):
            code = InviteCode()
            code.code = InviteCode.generate_code()
            code.created_by = admin_username
            
            # 设置过期时间
            if expires_days and expires_days.isdigit():
                days = int(expires_days)
                if days > 0:
                    code.expires_at = datetime.utcnow() + timedelta(days=days)
            
            db.session.add(code)
            generated_codes.append(code.code)
        
        db.session.commit()
        flash(f'成功生成 {count} 个邀请码', 'success')
        
    except Exception as e:
        flash(f'生成邀请码失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.invite_codes'))

@admin_bp.route('/invite_code/<int:code_id>/delete', methods=['POST'])
@admin_required
def delete_invite_code(code_id):
    try:
        code = InviteCode.query.get_or_404(code_id)
        db.session.delete(code)
        db.session.commit()
        flash('邀请码删除成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除邀请码失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.invite_codes'))

@admin_bp.route('/user_invites')
@admin_required
def user_invites():
    """查看用户邀请关系"""
    try:
        page = request.args.get('page', 1, type=int)
        
        # 查询所有通过邀请码注册的用户
        invited_users = User.query.filter(User.invited_by.isnot(None)).order_by(User.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        # 统计邀请数据
        invite_stats = {}
        for user in User.query.filter(User.invited_by.isnot(None)).all():
            inviter = user.invited_by
            if inviter not in invite_stats:
                invite_stats[inviter] = {
                    'total_invites': 0,
                    'active_invites': 0
                }
            invite_stats[inviter]['total_invites'] += 1
            if user.is_active:
                invite_stats[inviter]['active_invites'] += 1
        
        return render_template('admin/user_invites.html', 
                             invited_users=invited_users, 
                             invite_stats=invite_stats,
                             stats={'invite_stats': invite_stats})
    except Exception as e:
        flash(f'加载邀请数据失败: {str(e)}', 'error')
        class EmptyPagination:
            def __init__(self):
                self.items = []
                self.page = 1
                self.per_page = 20
                self.total = 0
                self.pages = 0
                self.has_prev = False
                self.has_next = False
                self.prev_num = None
                self.next_num = None
        
        empty_users = EmptyPagination()
        return render_template('admin/user_invites.html', 
                             invited_users=empty_users, 
                             invite_stats={},
                             stats={'invite_stats': {}})

# 生肖设置相关路由
@admin_bp.route('/zodiac_settings')
@admin_required
def zodiac_settings():
    """生肖号码对照表页面"""
    try:
        # 获取当前年份（按农历新年切换）
        zodiac_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
        current_year = request.args.get('year', zodiac_year, type=int)
        
        # 获取当前年份的生肖对照表
        zodiac_table = ZodiacSetting.get_zodiac_table_for_year(current_year)
        
        # 获取当前年份的生肖设置（用于编辑表单）
        zodiac_settings = ZodiacSetting.get_zodiac_settings(current_year)
        
        return render_template('admin/zodiac_settings.html', 
                              current_year=current_year,
                              zodiac_table=zodiac_table,
                              zodiac_settings=zodiac_settings)
    except Exception as e:
        flash(f'加载生肖对照表失败: {str(e)}', 'error')
        return render_template('admin/zodiac_settings.html', 
                              current_year=datetime.now().year,
                              zodiac_table={},
                              zodiac_settings={})

@admin_bp.route('/zodiac_settings/save', methods=['POST'])
@admin_required
def save_zodiac_settings():
    """保存生肖号码设置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的数据格式'})
        
        year = data.get('year')
        settings = data.get('settings')
        
        if not year or not settings:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        
        # 验证年份
        try:
            year = int(year)
            if year < 2020 or year > 2050:
                return jsonify({'success': False, 'message': '年份必须在2020-2050之间'})
        except ValueError:
            return jsonify({'success': False, 'message': '无效的年份格式'})
        
        # 验证设置数据
        if not isinstance(settings, dict):
            return jsonify({'success': False, 'message': '设置数据格式错误'})
        
        # 验证生肖名称
        valid_zodiacs = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
        for zodiac in settings.keys():
            if zodiac not in valid_zodiacs:
                return jsonify({'success': False, 'message': f'无效的生肖名称: {zodiac}'})
        
        # 验证号码范围和重复性
        all_numbers = set()
        for zodiac, numbers_str in settings.items():
            if not numbers_str or not numbers_str.strip():
                continue
                
            try:
                numbers = [int(n.strip()) for n in numbers_str.split(',') if n.strip()]
                for num in numbers:
                    if num < 1 or num > 49:
                        return jsonify({'success': False, 'message': f'号码必须在1-49之间: {num}'})
                    if num in all_numbers:
                        return jsonify({'success': False, 'message': f'号码重复: {num}'})
                    all_numbers.add(num)
            except ValueError:
                return jsonify({'success': False, 'message': f'{zodiac}的号码格式错误'})
        
        # 保存设置
        success, message = ZodiacSetting.batch_update_settings(year, settings)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'message': message})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'})

@admin_bp.route('/zodiac_settings/reset', methods=['POST'])
@admin_required
def reset_zodiac_settings():
    """重置生肖设置为默认值"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的数据格式'})
        
        year = data.get('year')
        if not year:
            return jsonify({'success': False, 'message': '缺少年份参数'})
        
        try:
            year = int(year)
        except ValueError:
            return jsonify({'success': False, 'message': '无效的年份格式'})
        
        # 删除该年份的所有自定义设置，系统将自动使用默认规则
        ZodiacSetting.query.filter_by(year=year).delete()
        db.session.commit()
        
        return jsonify({'success': True, 'message': '生肖设置已重置为默认值'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'重置失败: {str(e)}'})
