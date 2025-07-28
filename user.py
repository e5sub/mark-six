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
ZODIAC_MAPPING_SEQUENCE = ("虎", "兔", "龙", "蛇", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马")
RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46]
BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48]
GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]

def get_number_zodiac(number):
    try:
        num = int(number)
        if not 1 <= num <= 49: return ""
        return ZODIAC_MAPPING_SEQUENCE[(num - 1) % 12]
    except:
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
    
    query = PredictionRecord.query.filter_by(user_id=session['user_id'])
    
    # 筛选条件
    if region:
        query = query.filter_by(region=region)
    if period:
        query = query.filter(PredictionRecord.period.contains(period))
    if zodiac:
        query = query.filter(PredictionRecord.special_zodiac == zodiac)
    
    predictions = query.order_by(PredictionRecord.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    
    # 计算总体预测准确率
    total_predictions = PredictionRecord.query.filter_by(user_id=session['user_id']).count()
    accurate_predictions = PredictionRecord.query.filter_by(user_id=session['user_id'])\
        .filter(PredictionRecord.accuracy_score != None)\
        .filter(PredictionRecord.accuracy_score > 0).count()
    accuracy_rate = (accurate_predictions / total_predictions * 100) if total_predictions > 0 else 0
    
    return render_template('user/predictions.html', 
                          predictions=predictions, 
                          region=region, 
                          period=period, 
                          zodiac=zodiac,
                          get_number_color=get_number_color,
                          get_number_zodiac=get_number_zodiac,
                          accuracy_rate=round(accuracy_rate, 2))

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

@user_bp.route('/invite_codes')
@login_required
@active_required
def invite_codes():
    """用户邀请码管理"""
    user = User.query.get(session['user_id'])
    
    # 获取用户创建的邀请码
    page = request.args.get('page', 1, type=int)
    codes = InviteCode.query.filter_by(created_by=user.username)\
        .order_by(InviteCode.created_at.desc())\
        .paginate(page=page, per_page=10, error_out=False)
    
    # 获取邀请统计
    total_invites = InviteCode.query.filter_by(created_by=user.username, is_used=True).count()
    active_invites = User.query.filter_by(invited_by=user.username, is_active=True).count()
    
    # 获取被邀请的用户列表
    invited_users = User.query.filter_by(invited_by=user.username)\
        .order_by(User.created_at.desc()).limit(10).all()
    
    stats = {
        'total_invites': total_invites,
        'active_invites': active_invites,
        'success_rate': round(active_invites / total_invites * 100, 1) if total_invites > 0 else 0
    }
    
    return render_template('user/invite_codes.html', 
                          codes=codes, 
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
        # 例如：限制每个用户每天只能生成3个邀请码
        today_codes = InviteCode.query.filter_by(created_by=user.username)\
            .filter(InviteCode.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0))\
            .count()
        
        if today_codes >= 3:
            return jsonify({
                'success': False,
                'message': '每天最多只能生成3个邀请码'
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
        
        # 更新邮箱
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