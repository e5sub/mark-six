from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, User, ActivationCode, SystemConfig, PredictionRecord
from werkzeug.security import generate_password_hash
import uuid
from datetime import datetime

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    """管理员权限装饰器"""
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            flash('需要管理员权限', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    # 统计数据
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    inactive_users = total_users - active_users
    total_predictions = PredictionRecord.query.count()
    unused_codes = ActivationCode.query.filter_by(is_used=False).count()
    
    stats = {
        'total_users': total_users,
        'active_users': active_users,
        'inactive_users': inactive_users,
        'total_predictions': total_predictions,
        'unused_codes': unused_codes
    }
    
    return render_template('admin/dashboard.html', stats=stats)

@admin_bp.route('/users')
@admin_required
def users():
    page = request.args.get('page', 1, type=int)
    users = User.query.paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template('admin/users.html', users=users)

@admin_bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.username = request.form.get('username')
        user.email = request.form.get('email')
        
        new_password = request.form.get('new_password')
        if new_password:
            user.set_password(new_password)
        
        user.is_active = 'is_active' in request.form
        user.is_admin = 'is_admin' in request.form
        
        try:
            db.session.commit()
            flash('用户信息更新成功', 'success')
            return redirect(url_for('admin.users'))
        except Exception as e:
            db.session.rollback()
            flash(f'更新失败：{str(e)}', 'error')
    
    return render_template('admin/edit_user.html', user=user)

@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.is_admin and User.query.filter_by(is_admin=True).count() == 1:
        flash('不能删除最后一个管理员账号', 'error')
        return redirect(url_for('admin.users'))
    
    try:
        # 删除用户的预测记录
        PredictionRecord.query.filter_by(user_id=user_id).delete()
        db.session.delete(user)
        db.session.commit()
        flash('用户删除成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除失败：{str(e)}', 'error')
    
    return redirect(url_for('admin.users'))

@admin_bp.route('/activation-codes')
@admin_required
def activation_codes():
    page = request.args.get('page', 1, type=int)
    codes = ActivationCode.query.order_by(ActivationCode.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template('admin/activation_codes.html', codes=codes)

@admin_bp.route('/generate-codes', methods=['POST'])
@admin_required
def generate_codes():
    try:
        count = request.form.get('count', 1, type=int)
        validity_type = request.form.get('validity_type', 'permanent')
        
        print(f"接收到的参数: count={count}, validity_type={validity_type}")  # 调试信息
        
        if count < 1 or count > 100:
            flash('生成数量必须在1-100之间', 'error')
            return redirect(url_for('admin.activation_codes'))
        
        generated_codes = []
        for i in range(count):
            print(f"正在生成第 {i+1} 个激活码...")  # 调试信息
            
            # 生成唯一的激活码
            max_attempts = 10
            attempts = 0
            while attempts < max_attempts:
                try:
                    new_code = ActivationCode.generate_code()
                    print(f"生成的激活码: {new_code}")  # 调试信息
                    
                    # 检查是否已存在
                    existing = ActivationCode.query.filter_by(code=new_code).first()
                    if not existing:
                        break
                    attempts += 1
                except Exception as e:
                    print(f"生成激活码时出错: {e}")
                    attempts += 1
            
            if attempts >= max_attempts:
                raise Exception("无法生成唯一的激活码，请稍后重试")
            
            # 创建激活码对象
            try:
                code = ActivationCode(code=new_code)
                print(f"创建激活码对象成功: {new_code}")  # 调试信息
                
                code.set_validity(validity_type)
                print(f"设置有效期成功: {validity_type}")  # 调试信息
                
                db.session.add(code)
                generated_codes.append(new_code)
                print(f"添加到数据库会话成功")  # 调试信息
            except Exception as e:
                print(f"创建激活码对象时出错: {e}")
                raise e
        
        # 提交到数据库
        print("正在提交到数据库...")  # 调试信息
        db.session.commit()
        print("数据库提交成功")  # 调试信息
        
        validity_text = {
            'day': '1天',
            'month': '1个月',
            'quarter': '3个月',
            'year': '1年',
            'permanent': '永久'
        }.get(validity_type, '永久')
        
        flash(f'成功生成 {count} 个激活码（有效期：{validity_text}）', 'success')
        
    except Exception as e:
        print(f"生成激活码过程中出现错误: {e}")  # 详细错误信息
        import traceback
        print(f"错误堆栈: {traceback.format_exc()}")  # 打印完整错误堆栈
        
        try:
            db.session.rollback()
            print("数据库回滚成功")
        except Exception as rollback_error:
            print(f"数据库回滚失败: {rollback_error}")
        
        flash(f'生成失败：{str(e)}', 'error')
    
    return redirect(url_for('admin.activation_codes'))

@admin_bp.route('/system-config')
@admin_required
def system_config():
    configs = {
        'ai_api_key': SystemConfig.get_config('ai_api_key', ''),
        'ai_api_url': SystemConfig.get_config('ai_api_url', 'https://api.deepseek.com/v1/chat/completions'),
        'ai_model': SystemConfig.get_config('ai_model', 'gemini-2.0-flash'),
        'smtp_server': SystemConfig.get_config('smtp_server', ''),
        'smtp_port': SystemConfig.get_config('smtp_port', '587'),
        'smtp_username': SystemConfig.get_config('smtp_username', ''),
        'smtp_password': SystemConfig.get_config('smtp_password', ''),
    }
    return render_template('admin/system_config.html', configs=configs)

@admin_bp.route('/update-config', methods=['POST'])
@admin_required
def update_config():
    try:
        # AI配置
        SystemConfig.set_config('ai_api_key', request.form.get('ai_api_key', ''), 'AI API密钥')
        SystemConfig.set_config('ai_api_url', request.form.get('ai_api_url', ''), 'AI API地址')
        SystemConfig.set_config('ai_model', request.form.get('ai_model', ''), 'AI模型')
        
        # 邮箱配置
        SystemConfig.set_config('smtp_server', request.form.get('smtp_server', ''), 'SMTP服务器')
        SystemConfig.set_config('smtp_port', request.form.get('smtp_port', '587'), 'SMTP端口')
        SystemConfig.set_config('smtp_username', request.form.get('smtp_username', ''), 'SMTP用户名')
        SystemConfig.set_config('smtp_password', request.form.get('smtp_password', ''), 'SMTP密码')
        
        flash('系统配置更新成功', 'success')
    except Exception as e:
        flash(f'配置更新失败：{str(e)}', 'error')
    
    return redirect(url_for('admin.system_config'))

@admin_bp.route('/predictions')
@admin_required
def predictions():
    page = request.args.get('page', 1, type=int)
    predictions = PredictionRecord.query.order_by(PredictionRecord.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    
    # 为澳门预测记录补充生肖信息
    from app import _get_hk_number_zodiac
    
    for prediction in predictions.items:
        if prediction.region == 'macau' and not prediction.special_zodiac and prediction.special_number:
            # 为澳门预测记录计算生肖
            prediction.special_zodiac = _get_hk_number_zodiac(prediction.special_number)
            try:
                db.session.commit()
            except Exception as e:
                print(f"更新澳门预测记录生肖失败: {e}")
                db.session.rollback()
    
    return render_template('admin/predictions.html', predictions=predictions)
