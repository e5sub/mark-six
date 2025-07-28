from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, User, PredictionRecord, SystemConfig
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
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    
    # 获取用户预测统计
    total_predictions = PredictionRecord.query.filter_by(user_id=user.id).count()
    recent_predictions = PredictionRecord.query.filter_by(user_id=user.id)\
        .order_by(PredictionRecord.created_at.desc()).limit(5).all()
    
    # 计算准确率
    accurate_predictions = PredictionRecord.query.filter_by(user_id=user.id)\
        .filter(PredictionRecord.accuracy_score != None)\
        .filter(PredictionRecord.accuracy_score > 0).count()
    accuracy_rate = (accurate_predictions / total_predictions * 100) if total_predictions > 0 else 0
    
    stats = {
        'total_predictions': total_predictions,
        'accuracy_rate': round(accuracy_rate, 2),
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
        
        # 更新邮箱
        if new_email and new_email != user.email:
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