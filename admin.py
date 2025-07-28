from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, send_file, Response
from models import db, User, ActivationCode, SystemConfig, Prediction
from werkzeug.security import generate_password_hash
import uuid
import csv
import json
import io
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
    try:
        # 统计数据
        total_users = User.query.count()
        active_users = User.query.filter_by(is_active=True).count()
        inactive_users = total_users - active_users
        total_predictions = Prediction.query.count()
        unused_codes = ActivationCode.query.filter_by(is_used=False).count()
        
        stats = {
            'total_users': total_users,
            'active_users': active_users,
            'inactive_users': inactive_users,
            'total_predictions': total_predictions,
            'unused_codes': unused_codes
        }
        
        return render_template('admin/dashboard.html', stats=stats)
    except Exception as e:
        flash(f'加载控制台数据时出错: {str(e)}', 'error')
        return render_template('admin/dashboard.html', stats={
            'total_users': 0,
            'active_users': 0,
            'inactive_users': 0,
            'total_predictions': 0,
            'unused_codes': 0
        })

@admin_bp.route('/users')
@admin_required
def users():
    try:
        page = request.args.get('page', 1, type=int)
        users = User.query.paginate(
            page=page, per_page=20, error_out=False
        )
        return render_template('admin/users.html', users=users)
    except Exception as e:
        flash(f'加载用户列表时出错: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@admin_bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    try:
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
    except Exception as e:
        flash(f'编辑用户时出错: {str(e)}', 'error')
        return redirect(url_for('admin.users'))

@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        
        if user.is_admin and User.query.filter_by(is_admin=True).count() == 1:
            flash('不能删除最后一个管理员账号', 'error')
            return redirect(url_for('admin.users'))
        
        try:
            # 删除用户的预测记录
            Prediction.query.filter_by(user_id=user_id).delete()
            db.session.delete(user)
            db.session.commit()
            flash('用户删除成功', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'删除失败：{str(e)}', 'error')
        
        return redirect(url_for('admin.users'))
    except Exception as e:
        flash(f'删除用户时出错: {str(e)}', 'error')
        return redirect(url_for('admin.users'))

@admin_bp.route('/activation_codes')
@admin_required
def activation_codes():
    try:
        page = request.args.get('page', 1, type=int)
        codes = ActivationCode.query.order_by(ActivationCode.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        # 为每个激活码添加使用者用户名
        for code in codes.items:
            if code.used_by:
                user = User.query.get(code.used_by)
                code.used_by_username = user.username if user else '未知用户'
            else:
                code.used_by_username = None
        
        return render_template('admin/activation_codes.html', codes=codes)
    except Exception as e:
        flash(f'加载激活码列表时出错: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@admin_bp.route('/generate_codes', methods=['POST'])
@admin_required
def generate_codes():
    try:
        count = request.form.get('count', 1, type=int)
        validity_type = request.form.get('validity_type', 'permanent')
        
        if count < 1 or count > 100:
            flash('生成数量必须在1-100之间', 'error')
            return redirect(url_for('admin.activation_codes'))
        
        generated_codes = []
        for i in range(count):
            # 生成唯一的激活码
            max_attempts = 10
            attempts = 0
            while attempts < max_attempts:
                try:
                    new_code = str(uuid.uuid4()).replace('-', '').upper()[:16]
                    
                    # 检查是否已存在
                    existing = ActivationCode.query.filter_by(code=new_code).first()
                    if not existing:
                        break
                    attempts += 1
                except Exception as e:
                    attempts += 1
            
            if attempts >= max_attempts:
                raise Exception("无法生成唯一的激活码，请稍后重试")
            
            # 创建激活码对象
            code = ActivationCode(code=new_code, validity_type=validity_type)
            
            # 设置过期时间
            if validity_type == 'day':
                from datetime import timedelta
                code.expires_at = datetime.utcnow() + timedelta(days=1)
            elif validity_type == 'month':
                from datetime import timedelta
                code.expires_at = datetime.utcnow() + timedelta(days=30)
            elif validity_type == 'quarter':
                from datetime import timedelta
                code.expires_at = datetime.utcnow() + timedelta(days=90)
            elif validity_type == 'year':
                from datetime import timedelta
                code.expires_at = datetime.utcnow() + timedelta(days=365)
            # permanent类型不设置过期时间
            
            db.session.add(code)
            generated_codes.append(new_code)
        
        # 提交到数据库
        db.session.commit()
        
        validity_text = {
            'day': '1天',
            'month': '1个月',
            'quarter': '3个月',
            'year': '1年',
            'permanent': '永久'
        }.get(validity_type, '永久')
        
        flash(f'成功生成 {count} 个激活码（有效期：{validity_text}）', 'success')
        
    except Exception as e:
        try:
            db.session.rollback()
        except:
            pass
        flash(f'生成失败：{str(e)}', 'error')
    
    return redirect(url_for('admin.activation_codes'))

@admin_bp.route('/system_config', methods=['GET', 'POST'])
@admin_required
def system_config():
    try:
        if request.method == 'POST':
            try:
                # AI配置
                ai_api_key = request.form.get('ai_api_key', '')
                ai_api_url = request.form.get('ai_api_url', '')
                ai_model = request.form.get('ai_model', '')
                
                # 邮箱配置
                smtp_server = request.form.get('smtp_server', '')
                smtp_port = request.form.get('smtp_port', '587')
                smtp_username = request.form.get('smtp_username', '')
                smtp_password = request.form.get('smtp_password', '')
                
                # 更新或创建配置
                configs_to_update = [
                    ('ai_api_key', ai_api_key),
                    ('ai_api_url', ai_api_url),
                    ('ai_model', ai_model),
                    ('smtp_server', smtp_server),
                    ('smtp_port', smtp_port),
                    ('smtp_username', smtp_username),
                    ('smtp_password', smtp_password),
                ]
                
                for key, value in configs_to_update:
                    config = SystemConfig.query.filter_by(key=key).first()
                    if config:
                        config.value = value
                        config.updated_at = datetime.utcnow()
                    else:
                        config = SystemConfig(key=key, value=value)
                        db.session.add(config)
                
                db.session.commit()
                flash('系统配置更新成功', 'success')
                return redirect(url_for('admin.system_config'))
            except Exception as e:
                db.session.rollback()
                flash(f'配置更新失败：{str(e)}', 'error')
        
        # 获取所有配置
        configs = {}
        try:
            all_configs = SystemConfig.query.all()
            for config in all_configs:
                configs[config.key] = config.value
        except Exception as e:
            pass
        
        # 设置默认值
        default_configs = {
            'ai_api_key': '',
            'ai_api_url': 'https://api.deepseek.com/v1/chat/completions',
            'ai_model': 'deepseek-chat',
            'smtp_server': '',
            'smtp_port': '587',
            'smtp_username': '',
            'smtp_password': '',
        }
        
        for key, default_value in default_configs.items():
            if key not in configs:
                configs[key] = default_value
        
        return render_template('admin/system_config.html', configs=configs)
    except Exception as e:
        flash(f'加载系统配置时出错: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@admin_bp.route('/predictions')
@admin_required
def predictions():
    try:
        page = request.args.get('page', 1, type=int)
        predictions = Prediction.query.order_by(Prediction.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        return render_template('admin/predictions.html', predictions=predictions)
    except Exception as e:
        flash(f'加载预测记录时出错: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

# 导出用户信息为CSV
@admin_bp.route('/export_users')
@admin_required
def export_users():
    try:
        # 创建CSV内容
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 写入表头
        writer.writerow([
            'ID', '用户名', '邮箱', '激活状态', '管理员状态', 
            '注册时间', '激活到期时间', '预测记录数'
        ])
        
        # 写入用户数据
        users = User.query.all()
        for user in users:
            prediction_count = Prediction.query.filter_by(user_id=user.id).count()
            writer.writerow([
                user.id,
                user.username,
                user.email,
                '是' if user.is_active else '否',
                '是' if user.is_admin else '否',
                user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else '',
                user.activation_expires_at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(user, 'activation_expires_at') and user.activation_expires_at else '永久',
                prediction_count
            ])
        
        # 创建响应
        output.seek(0)
        filename = f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
        
    except Exception as e:
        flash(f'导出失败：{str(e)}', 'error')
        return redirect(url_for('admin.users'))

# 导出用户信息为JSON
@admin_bp.route('/export_users_json')
@admin_required
def export_users_json():
    try:
        users_data = []
        users = User.query.all()
        
        for user in users:
            # 获取用户的预测记录
            predictions = Prediction.query.filter_by(user_id=user.id).all()
            predictions_data = []
            
            for pred in predictions:
                predictions_data.append({
                    'region': pred.region,
                    'strategy': pred.strategy,
                    'period': pred.period,
                    'normal_numbers': pred.normal_numbers,
                    'special_number': pred.special_number,
                    'special_zodiac': getattr(pred, 'special_zodiac', ''),
                    'prediction_text': getattr(pred, 'prediction_text', ''),
                    'created_at': pred.created_at.isoformat() if pred.created_at else None,
                    'accuracy_score': getattr(pred, 'accuracy_score', None)
                })
            
            user_data = {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_admin': user.is_admin,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'activation_expires_at': user.activation_expires_at.isoformat() if hasattr(user, 'activation_expires_at') and user.activation_expires_at else None,
                'predictions': predictions_data
            }
            users_data.append(user_data)
        
        # 创建JSON响应
        filename = f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        return Response(
            json.dumps(users_data, ensure_ascii=False, indent=2),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
        
    except Exception as e:
        flash(f'导出失败：{str(e)}', 'error')
        return redirect(url_for('admin.users'))

# 导入用户页面
@admin_bp.route('/import_users', methods=['GET', 'POST'])
@admin_required
def import_users():
    if request.method == 'POST':
        try:
            if 'file' not in request.files:
                flash('请选择文件', 'error')
                return redirect(request.url)
            
            file = request.files['file']
            if file.filename == '':
                flash('请选择文件', 'error')
                return redirect(request.url)
            
            # 检查文件类型
            if not file.filename.lower().endswith(('.csv', '.json')):
                flash('只支持CSV和JSON格式的文件', 'error')
                return redirect(request.url)
            
            success_count = 0
            error_count = 0
            errors = []
            
            if file.filename.lower().endswith('.csv'):
                # 处理CSV文件
                content = file.read().decode('utf-8')
                csv_reader = csv.DictReader(io.StringIO(content))
                
                for row in csv_reader:
                    try:
                        # 检查用户名和邮箱是否已存在
                        if User.query.filter_by(username=row['用户名']).first():
                            errors.append(f"用户名 {row['用户名']} 已存在")
                            error_count += 1
                            continue
                        
                        if User.query.filter_by(email=row['邮箱']).first():
                            errors.append(f"邮箱 {row['邮箱']} 已存在")
                            error_count += 1
                            continue
                        
                        # 创建新用户
                        user = User(
                            username=row['用户名'],
                            email=row['邮箱'],
                            is_active=row.get('激活状态', '否') == '是',
                            is_admin=row.get('管理员状态', '否') == '是'
                        )
                        user.set_password('123456')  # 默认密码
                        
                        db.session.add(user)
                        success_count += 1
                        
                    except Exception as e:
                        errors.append(f"导入用户 {row.get('用户名', 'Unknown')} 失败: {str(e)}")
                        error_count += 1
            
            elif file.filename.lower().endswith('.json'):
                # 处理JSON文件
                content = file.read().decode('utf-8')
                users_data = json.loads(content)
                
                for user_data in users_data:
                    try:
                        # 检查用户名和邮箱是否已存在
                        if User.query.filter_by(username=user_data['username']).first():
                            errors.append(f"用户名 {user_data['username']} 已存在")
                            error_count += 1
                            continue
                        
                        if User.query.filter_by(email=user_data['email']).first():
                            errors.append(f"邮箱 {user_data['email']} 已存在")
                            error_count += 1
                            continue
                        
                        # 创建新用户
                        user = User(
                            username=user_data['username'],
                            email=user_data['email'],
                            is_active=user_data.get('is_active', False),
                            is_admin=user_data.get('is_admin', False)
                        )
                        user.set_password('123456')  # 默认密码
                        
                        # 设置激活到期时间
                        if user_data.get('activation_expires_at'):
                            try:
                                user.activation_expires_at = datetime.fromisoformat(user_data['activation_expires_at'])
                            except:
                                pass
                        
                        db.session.add(user)
                        db.session.flush()  # 获取用户ID
                        
                        # 导入预测记录
                        for pred_data in user_data.get('predictions', []):
                            try:
                                prediction = Prediction(
                                    user_id=user.id,
                                    region=pred_data['region'],
                                    strategy=pred_data['strategy'],
                                    period=pred_data['period'],
                                    normal_numbers=pred_data['normal_numbers'],
                                    special_number=pred_data['special_number']
                                )
                                if pred_data.get('created_at'):
                                    prediction.created_at = datetime.fromisoformat(pred_data['created_at'])
                                
                                db.session.add(prediction)
                            except Exception as e:
                                pass  # 忽略预测记录导入错误
                        
                        success_count += 1
                        
                    except Exception as e:
                        errors.append(f"导入用户 {user_data.get('username', 'Unknown')} 失败: {str(e)}")
                        error_count += 1
            
            # 提交数据库更改
            db.session.commit()
            
            # 显示结果
            if success_count > 0:
                flash(f'成功导入 {success_count} 个用户', 'success')
            if error_count > 0:
                flash(f'导入失败 {error_count} 个用户', 'error')
                for error in errors[:5]:  # 只显示前5个错误
                    flash(error, 'error')
            
            return redirect(url_for('admin.users'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'导入失败：{str(e)}', 'error')
    
    return render_template('admin/import_users.html')