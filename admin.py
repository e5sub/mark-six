from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from models import db, User, ActivationCode, SystemConfig, PredictionRecord
from functools import wraps
from datetime import datetime, timedelta
import secrets
import string
import csv
import json
import io
import os

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask_login import current_user
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('需要管理员权限', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    try:
        # 统计数据
        total_users = User.query.count()
        active_users = User.query.filter_by(is_active=True).count()
        total_codes = ActivationCode.query.count()
        used_codes = ActivationCode.query.filter_by(is_used=True).count()
        total_predictions = PredictionRecord.query.count()
        
        # 最近注册的用户
        recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
        
        # 最近使用的激活码
        recent_codes = ActivationCode.query.filter_by(is_used=True).order_by(ActivationCode.used_at.desc()).limit(5).all()
        
        return render_template('admin/dashboard.html', 
                             total_users=total_users,
                             active_users=active_users,
                             total_codes=total_codes,
                             used_codes=used_codes,
                             total_predictions=total_predictions,
                             recent_users=recent_users,
                             recent_codes=recent_codes)
    except Exception as e:
        flash(f'加载控制台数据失败: {str(e)}', 'error')
        return render_template('admin/dashboard.html')

@admin_bp.route('/users')
@admin_required
def users():
    try:
        page = request.args.get('page', 1, type=int)
        users = User.query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        return render_template('admin/users.html', users=users)
    except Exception as e:
        flash(f'加载用户列表失败: {str(e)}', 'error')
        return render_template('admin/users.html', users=None)

@admin_bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        try:
            user.username = request.form.get('username')
            user.email = request.form.get('email')
            
            # 更新密码（如果提供）
            new_password = request.form.get('new_password')
            if new_password:
                user.set_password(new_password)
            
            user.is_active = 'is_active' in request.form
            user.is_admin = 'is_admin' in request.form
            
            # 处理激活过期时间
            activation_expires_at = request.form.get('activation_expires_at')
            if activation_expires_at:
                try:
                    user.activation_expires_at = datetime.strptime(activation_expires_at, '%Y-%m-%dT%H:%M')
                except ValueError:
                    flash('激活过期时间格式不正确', 'error')
                    return redirect(url_for('admin.edit_user', user_id=user_id))
            else:
                user.activation_expires_at = None
            
            db.session.commit()
            flash('用户信息已更新', 'success')
            return redirect(url_for('admin.users'))
        except Exception as e:
            db.session.rollback()
            flash(f'更新用户失败: {str(e)}', 'error')
    
    return render_template('admin/edit_user.html', user=user)

@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        
        # 防止删除最后一个管理员
        if user.is_admin:
            admin_count = User.query.filter_by(is_admin=True).count()
            if admin_count <= 1:
                flash('不能删除最后一个管理员账户', 'error')
                return redirect(url_for('admin.users'))
        
        # 删除用户的预测记录
        PredictionRecord.query.filter_by(user_id=user_id).delete()
        
        # 删除用户
        db.session.delete(user)
        db.session.commit()
        
        flash(f'用户 {user.username} 已删除', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除用户失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.users'))

@admin_bp.route('/activation_codes')
@admin_required
def activation_codes():
    try:
        page = request.args.get('page', 1, type=int)
        codes = ActivationCode.query.order_by(ActivationCode.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        # 为每个激活码添加用户名信息
        for code in codes.items:
            if code.used_by:
                user = User.query.get(code.used_by)
                code.used_by_username = user.username if user else '已删除用户'
            else:
                code.used_by_username = None
        
        return render_template('admin/activation_codes.html', codes=codes)
    except Exception as e:
        flash(f'加载激活码列表失败: {str(e)}', 'error')
        return render_template('admin/activation_codes.html', codes=None)

@admin_bp.route('/generate_codes', methods=['POST'])
@admin_required
def generate_codes():
    try:
        count = int(request.form.get('count', 1))
        validity_type = request.form.get('validity_type', 'permanent')
        
        if count < 1 or count > 100:
            flash('生成数量必须在1-100之间', 'error')
            return redirect(url_for('admin.activation_codes'))
        
        # 计算过期时间
        expires_at = None
        if validity_type != 'permanent':
            now = datetime.utcnow()
            if validity_type == 'day':
                expires_at = now + timedelta(days=1)
            elif validity_type == 'month':
                expires_at = now + timedelta(days=30)
            elif validity_type == 'quarter':
                expires_at = now + timedelta(days=90)
            elif validity_type == 'year':
                expires_at = now + timedelta(days=365)
        
        # 生成激活码
        for _ in range(count):
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))
            activation_code = ActivationCode(
                code=code,
                validity_type=validity_type,
                expires_at=expires_at
            )
            db.session.add(activation_code)
        
        db.session.commit()
        flash(f'成功生成 {count} 个激活码', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'生成激活码失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.activation_codes'))

@admin_bp.route('/system_config', methods=['GET', 'POST'])
@admin_required
def system_config():
    if request.method == 'POST':
        try:
            # 更新配置
            config_keys = [
                'ai_api_key', 'ai_api_url', 'ai_model',
                'smtp_server', 'smtp_port', 'smtp_username', 'smtp_password',
                'site_name', 'max_predictions_per_day', 'enable_registration', 'maintenance_mode'
            ]
            
            for key in config_keys:
                value = request.form.get(key, '')
                config = SystemConfig.query.filter_by(key=key).first()
                if config:
                    config.value = value
                    # 如果字段存在则更新时间戳
                    if hasattr(config, 'updated_at'):
                        config.updated_at = datetime.utcnow()
                else:
                    config = SystemConfig(key=key, value=value)
                    # 如果字段存在则设置时间戳
                    if hasattr(config, 'created_at'):
                        config.created_at = datetime.utcnow()
                    if hasattr(config, 'updated_at'):
                        config.updated_at = datetime.utcnow()
                    db.session.add(config)
            
            db.session.commit()
            flash('系统配置已更新', 'success')
            return redirect(url_for('admin.system_config'))
        except Exception as e:
            db.session.rollback()
            flash(f'配置更新失败: {str(e)}', 'error')
    
    # 获取所有配置
    configs = {}
    try:
        all_configs = SystemConfig.query.all()
        for config in all_configs:
            configs[config.key] = config.value
    except Exception as e:
        flash(f'加载配置失败: {str(e)}', 'error')
    
    return render_template('admin/system_config.html', configs=configs)

@admin_bp.route('/predictions')
@admin_required
def predictions():
    try:
        page = request.args.get('page', 1, type=int)
        predictions = PredictionRecord.query.order_by(PredictionRecord.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        # 为每个预测记录添加用户名信息
        for prediction in predictions.items:
            user = User.query.get(prediction.user_id)
            prediction.username = user.username if user else '已删除用户'
        
        return render_template('admin/predictions.html', predictions=predictions)
    except Exception as e:
        flash(f'加载预测记录失败: {str(e)}', 'error')
        return render_template('admin/predictions.html', predictions=None)

@admin_bp.route('/export_users')
@admin_required
def export_users():
    try:
        users = User.query.all()
        
        # 创建CSV内容
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 写入标题行
        writer.writerow(['ID', '用户名', '邮箱', '激活状态', '管理员', '注册时间', '激活过期时间'])
        
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
        
        # 创建响应
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'users_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        flash(f'导出用户失败: {str(e)}', 'error')
        return redirect(url_for('admin.users'))

@admin_bp.route('/export_users_json')
@admin_required
def export_users_json():
    try:
        users = User.query.all()
        users_data = []
        
        for user in users:
            # 获取用户的预测记录
            predictions = PredictionRecord.query.filter_by(user_id=user.id).all()
            predictions_data = []
            
            for pred in predictions:
                predictions_data.append({
                    'lottery_type': pred.lottery_type,
                    'numbers': pred.numbers,
                    'created_at': pred.created_at.isoformat() if pred.created_at else None
                })
            
            users_data.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_admin': user.is_admin,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'activation_expires_at': user.activation_expires_at.isoformat() if user.activation_expires_at else None,
                'predictions': predictions_data
            })
        
        # 创建JSON响应
        json_data = json.dumps(users_data, ensure_ascii=False, indent=2)
        
        return send_file(
            io.BytesIO(json_data.encode('utf-8')),
            mimetype='application/json',
            as_attachment=True,
            download_name=f'users_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        )
    except Exception as e:
        flash(f'导出用户JSON失败: {str(e)}', 'error')
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
            
            # 检查文件类型
            if file.filename.endswith('.csv'):
                return import_csv_users(file)
            elif file.filename.endswith('.json'):
                return import_json_users(file)
            else:
                flash('只支持CSV和JSON格式文件', 'error')
                return redirect(url_for('admin.import_users'))
        except Exception as e:
            flash(f'导入失败: {str(e)}', 'error')
            return redirect(url_for('admin.import_users'))
    
    return render_template('admin/import_users.html')

def import_csv_users(file):
    try:
        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))
        
        imported_count = 0
        skipped_count = 0
        
        for row in reader:
            username = row.get('用户名') or row.get('username')
            email = row.get('邮箱') or row.get('email')
            
            if not username or not email:
                skipped_count += 1
                continue
            
            # 检查用户是否已存在
            if User.query.filter((User.username == username) | (User.email == email)).first():
                skipped_count += 1
                continue
            
            # 创建新用户
            user = User(username=username, email=email)
            user.set_password('123456')  # 默认密码
            user.is_active = True
            
            db.session.add(user)
            imported_count += 1
        
        db.session.commit()
        flash(f'成功导入 {imported_count} 个用户，跳过 {skipped_count} 个重复用户', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'CSV导入失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.users'))

def import_json_users(file):
    try:
        content = file.read().decode('utf-8')
        users_data = json.loads(content)
        
        imported_count = 0
        skipped_count = 0
        
        for user_data in users_data:
            username = user_data.get('username')
            email = user_data.get('email')
            
            if not username or not email:
                skipped_count += 1
                continue
            
            # 检查用户是否已存在
            if User.query.filter((User.username == username) | (User.email == email)).first():
                skipped_count += 1
                continue
            
            # 创建新用户
            user = User(username=username, email=email)
            user.set_password('123456')  # 默认密码
            user.is_active = user_data.get('is_active', True)
            user.is_admin = user_data.get('is_admin', False)
            
            # 处理激活过期时间
            if user_data.get('activation_expires_at'):
                try:
                    user.activation_expires_at = datetime.fromisoformat(user_data['activation_expires_at'].replace('Z', '+00:00'))
                except:
                    pass
            
            db.session.add(user)
            db.session.flush()  # 获取用户ID
            
            # 导入预测记录
            predictions_data = user_data.get('predictions', [])
            for pred_data in predictions_data:
                prediction = PredictionRecord(
                    user_id=user.id,
                    lottery_type=pred_data.get('lottery_type', 'hk'),
                    numbers=pred_data.get('numbers', '')
                )
                if pred_data.get('created_at'):
                    try:
                        prediction.created_at = datetime.fromisoformat(pred_data['created_at'].replace('Z', '+00:00'))
                    except:
                        pass
                db.session.add(prediction)
            
            imported_count += 1
        
        db.session.commit()
        flash(f'成功导入 {imported_count} 个用户，跳过 {skipped_count} 个重复用户', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'JSON导入失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.users'))