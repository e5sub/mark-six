from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, User, PredictionRecord, SystemConfig, InviteCode
from datetime import datetime
import json

user_bp = Blueprint('user', __name__, url_prefix='/user')

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
        if not session.get('is_active'):
            flash('请先激活账号才能使用此功能', 'warning')
            return redirect(url_for('auth.activate'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

@user_bp.route('/dashboard')
@user_bp.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    
    # 获取用户预测统计
    total_predictions = PredictionRecord.query.filter_by(user_id=user.id).count()
    recent_predictions = PredictionRecord.query.filter_by(user_id=user.id)\
        .order_by(PredictionRecord.created_at.desc()).limit(5).all()
    
    # 计算不同策略的准确率（对比特码和生肖）
    def calculate_user_accuracy(strategy=None):
        query = PredictionRecord.query.filter_by(user_id=user.id, is_result_updated=True)
        if strategy:
            query = query.filter_by(strategy=strategy)
        
        predictions = query.all()
        if not predictions:
            return 0.0
        
        total_score = 0.0
        total_count = 0
        
        for pred in predictions:
            if pred.actual_special_number and pred.special_number:
                total_count += 1
                
                # 特码号码是否命中
                special_hit = 1 if pred.special_number == pred.actual_special_number else 0
                
                # 特码生肖是否命中
                zodiac_hit = 0
                if hasattr(pred, 'special_zodiac') and hasattr(pred, 'actual_special_zodiac') and pred.special_zodiac and pred.actual_special_zodiac:
                    zodiac_hit = 1 if pred.special_zodiac == pred.actual_special_zodiac else 0
                
                # 计算该预测的准确率 (特码命中 * 0.7 + 生肖命中 * 0.3)
                accuracy = (special_hit * 0.7) + (zodiac_hit * 0.3)
                total_score += accuracy
        
        # 返回平均准确率
        return round((total_score / total_count) * 100, 1) if total_count > 0 else 0.0
    
    # 计算各种准确率
    avg_accuracy = calculate_user_accuracy()
    random_accuracy = calculate_user_accuracy('random')
    balanced_accuracy = calculate_user_accuracy('balanced')
    ai_accuracy = calculate_user_accuracy('ai')
    
    stats = {
        'total_predictions': total_predictions,
        'avg_accuracy': avg_accuracy,
        'random_accuracy': random_accuracy,
        'balanced_accuracy': balanced_accuracy,
        'ai_accuracy': ai_accuracy,
        'recent_predictions': recent_predictions
    }
    
    return render_template('user/dashboard.html', 
                          user=user, 
                          stats=stats,
                          get_number_color=get_number_color,
                          get_number_zodiac=get_number_zodiac)

# 号码属性计算函数
RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46]
BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48]
GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]

# 生肖对照表将从澳门接口返回的JSON数据中获取
# 不再在此处定义静态映射

def get_number_zodiac(number):
    """
    此函数已不再使用静态映射表
    生肖信息应直接从澳门接口返回的JSON数据中获取
    此函数仅作为兼容性保留
    """
    # 返回空字符串，实际生肖信息应从API数据中获取
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
            # 设置为当天的结束时间
            end_date_obj = end_date_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(PredictionRecord.created_at <= end_date_obj)
        except ValueError:
            flash('结束日期格式不正确', 'error')
    
    # 添加预测策略筛选
    if strategy:
        query = query.filter_by(strategy=strategy)
    
    # 添加预测结果筛选
    if result:
        if result == 'special_hit':
            query = query.filter(PredictionRecord.is_result_updated == True, 
                                PredictionRecord.special_number == PredictionRecord.actual_special_number)
        elif result == 'normal_hit':
            query = query.filter(PredictionRecord.is_result_updated == True, 
                                PredictionRecord.special_number != PredictionRecord.actual_special_number,
                                PredictionRecord.normal_numbers.contains(db.cast(PredictionRecord.actual_special_number, db.String)))
        elif result == 'wrong':
            query = query.filter(PredictionRecord.is_result_updated == True, 
                                PredictionRecord.accuracy_score <= 0)
        elif result == 'pending':
            query = query.filter(PredictionRecord.is_result_updated == False)
    
    predictions = query.order_by(PredictionRecord.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    
    # 计算总体预测准确率
    total_predictions = PredictionRecord.query.filter_by(user_id=session['user_id']).count()
    
    # 特码命中的预测
    special_hit_predictions = PredictionRecord.query.filter_by(
        user_id=session['user_id'],
        is_result_updated=True
    ).filter(
        PredictionRecord.special_number == PredictionRecord.actual_special_number
    ).count()
    
    # 平码命中的预测（不包括特码命中的）
    normal_hit_predictions = PredictionRecord.query.filter_by(
        user_id=session['user_id'],
        is_result_updated=True
    ).filter(
        PredictionRecord.special_number != PredictionRecord.actual_special_number
    ).filter(
        PredictionRecord.normal_numbers.contains(db.cast(PredictionRecord.actual_special_number, db.String))
    ).count()
    
    # 总命中数（特码命中 + 平码命中）
    accurate_predictions = special_hit_predictions + normal_hit_predictions
    
    # 未命中的预测
    wrong_predictions = PredictionRecord.query.filter_by(
        user_id=session['user_id'],
        is_result_updated=True
    ).filter(
        PredictionRecord.accuracy_score <= 0
    ).count()
    
    # 计算准确率
    accuracy_rate = (accurate_predictions / total_predictions * 100) if total_predictions > 0 else 0
    
    return render_template('user/predictions.html', 
                          predictions=predictions, 
                          region=region, 
                          period=period, 
                          zodiac=zodiac,
                          start_date=start_date,
                          end_date=end_date,
                          strategy=strategy,
                          result=result,
                          get_number_color=get_number_color,
                          get_number_zodiac=get_number_zodiac,
                          correct_predictions=accurate_predictions,
                          special_hit_count=special_hit_predictions,
                          normal_hit_count=normal_hit_predictions,
                          wrong_predictions=wrong_predictions,
                          accuracy=round(accuracy_rate, 2))

@user_bp.route('/save-prediction', methods=['POST'])
@login_required
@active_required
def save_prediction():
    """保存预测记录"""
    try:
        data = request.get_json()
        
        # 检查用户是否已经为当前期生成过预测
        existing = PredictionRecord.query.filter_by(
            user_id=session['user_id'],
            region=data['region'],
            period=data['period']
        ).first()
        
        if existing:
            return jsonify({
                'success': False,
                'message': '您已经为本期生成过预测，不能重复生成'
            })
        
        # 创建预测记录
        prediction = PredictionRecord(
            user_id=session['user_id'],
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
    """检查用户是否已经为当前期生成过预测"""
    region = request.args.get('region')
    period = request.args.get('period')
    
    if not region or not period:
        return jsonify({'exists': False})
    
    existing = PredictionRecord.query.filter_by(
        user_id=session['user_id'],
        region=region,
        period=period
    ).first()
    
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
        # 更新用户信息
        new_email = request.form.get('email')
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # 验证当前密码
        if not user.check_password(current_password):
            flash('当前密码错误', 'error')
            return render_template('user/profile.html', user=user)
            
        # 更新邮箱（仅管理员可修改）
        if new_email and new_email != user.email:
            if not user.is_admin:
                flash('普通用户无权修改邮箱地址，如需修改请联系管理员', 'error')
                return render_template('user/profile.html', user=user)
            if User.query.filter_by(email=new_email).first():
                flash('邮箱已被其他用户使用', 'error')
                return render_template('user/profile.html', user=user)
            user.email = new_email
        
        # 更新密码
        if new_password:
            if new_password != confirm_password:
                flash('两次输入的新密码不一致', 'error')
                return render_template('user/profile.html', user=user)
            if len(new_password) < 6:
                flash('新密码长度至少6位', 'error')
                return render_template('user/profile.html', user=user)
            user.set_password(new_password)
        
        try:
            db.session.commit()
            flash('个人信息更新成功', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'更新失败：{str(e)}', 'error')
    
    return render_template('user/profile.html', user=user)

@user_bp.route('/save_prediction_settings', methods=['POST'])
@login_required
@active_required
def save_prediction_settings():
    """保存用户预测设置"""
    user = User.query.get(session['user_id'])
    
    # 获取表单数据
    auto_prediction_enabled = 'auto_prediction_enabled' in request.form
    auto_prediction_strategies = request.form.getlist('auto_prediction_strategies')
    auto_prediction_regions = request.form.getlist('auto_prediction_regions')
    
    # 验证策略是否有效
    valid_strategies = []
    for strategy in auto_prediction_strategies:
        if strategy in ['random', 'balanced', 'ai']:
            valid_strategies.append(strategy)
    
    # 如果没有选择任何有效策略，默认使用均衡策略
    if not valid_strategies:
        valid_strategies = ['balanced']
    
    # 验证地区是否有效
    valid_regions = []
    for region in auto_prediction_regions:
        if region in ['hk', 'macau']:
            valid_regions.append(region)
    
    # 如果没有选择任何有效地区，默认使用香港
    if not valid_regions:
        valid_regions = ['hk']
    
    # 更新用户设置
    user.auto_prediction_enabled = auto_prediction_enabled
    user.auto_prediction_strategies = ','.join(valid_strategies)
    user.auto_prediction_regions = ','.join(valid_regions)
    
    try:
        db.session.commit()
        flash('预测设置保存成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'保存失败：{str(e)}', 'error')
    
    return redirect(url_for('user.profile'))

@user_bp.route('/invite')
@login_required
@active_required
def invite():
    """邀请好友页面（重定向到invite_codes）"""
    return redirect(url_for('user.invite_codes'))

@user_bp.route('/invite_codes')
@login_required
@active_required
def invite_codes():
    """用户邀请码管理"""
    user = User.query.get(session['user_id'])
    
    # 获取用户创建的未使用邀请码
    page = request.args.get('page', 1, type=int)
    invite_codes = InviteCode.query.filter_by(created_by=user.username, is_used=False)\
        .order_by(InviteCode.created_at.desc()).all()
    
    # 获取邀请统计
    total_invites = InviteCode.query.filter_by(created_by=user.username, is_used=True).count()
    active_invites = User.query.filter_by(invited_by=user.username, is_active=True).count()
    total_generated = InviteCode.query.filter_by(created_by=user.username).count()
    
    # 获取被邀请的用户列表
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
        
        # 检查用户是否有权限生成邀请码（可以根据需要添加限制）
        # 例如：限制每个用户最多只能生成10个邀请码
        total_codes = InviteCode.query.filter_by(created_by=user.username).count()
        
        if total_codes >= 10:
            return jsonify({
                'success': False,
                'message': '您已达到邀请码生成上限（10个）'
            })
        
        # 创建邀请码
        invite_code = InviteCode()
        invite_code.code = InviteCode.generate_code()
        invite_code.created_by = user.username
        
        # 设置7天过期
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
        
        # 验证当前密码
        if not user.check_password(current_password):
            flash('当前密码错误', 'error')
            return redirect(url_for('user.dashboard'))
        
        # 验证新密码
        if new_password != confirm_password:
            flash('两次输入的新密码不一致', 'error')
            return redirect(url_for('user.dashboard'))
        
        if len(new_password) < 6:
            flash('新密码长度至少6位', 'error')
            return redirect(url_for('user.dashboard'))
        
        # 更新密码
        user.set_password(new_password)
        db.session.commit()
        flash('密码修改成功', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'密码修改失败：{str(e)}', 'error')
    
    return redirect(url_for('user.dashboard'))

@user_bp.route('/update_auto_prediction', methods=['POST'])
@login_required
@active_required
def update_auto_prediction():
    """更新自动预测设置"""
    try:
        user = User.query.get(session['user_id'])
        
        # 获取表单数据
        auto_prediction_enabled = 'auto_prediction_enabled' in request.form
        auto_prediction_strategies = request.form.getlist('auto_prediction_strategies')
        auto_prediction_regions = request.form.getlist('auto_prediction_regions')
        
        # 验证策略是否有效
        valid_strategies = []
        for strategy in auto_prediction_strategies:
            if strategy in ['random', 'balanced', 'ai']:
                valid_strategies.append(strategy)
        
        # 如果没有选择任何有效策略，默认使用均衡策略
        if not valid_strategies:
            valid_strategies = ['balanced']
        
        # 验证地区是否有效
        valid_regions = []
        for region in auto_prediction_regions:
            if region in ['hk', 'macau']:
                valid_regions.append(region)
        
        # 如果没有选择任何有效地区，默认使用香港和澳门
        if not valid_regions:
            valid_regions = ['hk', 'macau']
        
        # 更新用户设置
        user.auto_prediction_enabled = auto_prediction_enabled
        user.auto_prediction_strategies = ','.join(valid_strategies)
        user.auto_prediction_regions = ','.join(valid_regions)
        
        db.session.commit()
        flash('自动预测设置保存成功', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'设置保存失败：{str(e)}', 'error')
    
    return redirect(url_for('user.dashboard'))

@user_bp.route('/analytics')
@login_required
@active_required
def analytics():
    """用户统计分析页面"""
    user = User.query.get(session['user_id'])
    
    # 获取用户预测统计
    total_predictions = PredictionRecord.query.filter_by(user_id=user.id).count()
    
    # 特码命中的预测
    special_hit_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.special_number == PredictionRecord.actual_special_number
    ).count()
    
    # 平码命中的预测（不包括特码命中的）
    normal_hit_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.special_number != PredictionRecord.actual_special_number
    ).filter(
        PredictionRecord.normal_numbers.contains(db.cast(PredictionRecord.actual_special_number, db.String))
    ).count()
    
    # 总命中数（特码命中 + 平码命中）
    accurate_predictions = special_hit_predictions + normal_hit_predictions
    
    # 未命中的预测
    wrong_predictions = PredictionRecord.query.filter_by(
        user_id=user.id,
        is_result_updated=True
    ).filter(
        PredictionRecord.accuracy_score <= 0
    ).count()
    
    # 计算不同策略的准确率
    def calculate_strategy_stats(strategy=None):
        query = PredictionRecord.query.filter_by(user_id=user.id)
        if strategy:
            query = query.filter_by(strategy=strategy)
        
        total = query.count()
        updated = query.filter_by(is_result_updated=True).count()
        
        # 特码命中的预测
        special_hit = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.special_number == PredictionRecord.actual_special_number
        ).count()
        
        # 平码命中的预测（不包括特码命中的）
        normal_hit = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.special_number != PredictionRecord.actual_special_number,
            PredictionRecord.normal_numbers.contains(db.cast(PredictionRecord.actual_special_number, db.String))
        ).count()
        
        # 总命中数（特码命中 + 平码命中）
        correct = special_hit + normal_hit
        
        accuracy = (correct / updated * 100) if updated > 0 else 0
        
        return {
            'total': total,
            'updated': updated,
            'correct': correct,
            'accuracy': round(accuracy, 1)
        }
    
    # 计算不同地区的准确率
    def calculate_region_stats(region):
        query = PredictionRecord.query.filter_by(user_id=user.id, region=region)
        
        total = query.count()
        updated = query.filter_by(is_result_updated=True).count()
        
        # 特码命中的预测
        special_hit = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.special_number == PredictionRecord.actual_special_number
        ).count()
        
        # 平码命中的预测（不包括特码命中的）
        normal_hit = query.filter(
            PredictionRecord.is_result_updated == True,
            PredictionRecord.special_number != PredictionRecord.actual_special_number,
            PredictionRecord.normal_numbers.contains(db.cast(PredictionRecord.actual_special_number, db.String))
        ).count()
        
        # 总命中数（特码命中 + 平码命中）
        correct = special_hit + normal_hit
        
        accuracy = (correct / updated * 100) if updated > 0 else 0
        
        return {
            'total': total,
            'updated': updated,
            'correct': correct,
            'accuracy': round(accuracy, 1)
        }
    
    # 计算总体统计
    stats = calculate_strategy_stats()
    
    # 添加特码命中和平码命中的统计
    stats['total_predictions'] = total_predictions
    stats['special_hit_count'] = special_hit_predictions
    stats['normal_hit_count'] = normal_hit_predictions
    stats['wrong_predictions'] = wrong_predictions
    stats['accuracy'] = (accurate_predictions / total_predictions * 100) if total_predictions > 0 else 0
    
    # 计算各策略统计
    strategy_stats = {
        'random': calculate_strategy_stats('random'),
        'balanced': calculate_strategy_stats('balanced'),
        'ai': calculate_strategy_stats('ai')
    }
    
    # 计算各地区统计
    region_stats = {
        'hk': calculate_region_stats('hk'),
        'macau': calculate_region_stats('macau')
    }
    
    # 获取最近预测记录
    recent_predictions = PredictionRecord.query.filter_by(user_id=user.id)\
        .order_by(PredictionRecord.created_at.desc()).limit(10).all()
    
    # 获取预测趋势数据（最近7天）
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
                          region_stats=region_stats,
                          recent_predictions=recent_predictions,
                          trend_data=trend_data,
                          get_number_color=get_number_color)
