from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, send_file, Response
from models import db, User, ActivationCode, SystemConfig, PredictionRecord
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
@admin_bp.route('/system-config', methods=['GET', 'POST'])
@admin_bp.route('/system_config', methods=['GET', 'POST'])
@admin_required
def system_config():
    if request.method == 'POST':
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
            return redirect(url_for('admin.system_config'))
        except Exception as e:
            flash(f'配置更新失败：{str(e)}', 'error')
    
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

# 导出用户信息为CSV
@admin_bp.route('/export-users')
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
            prediction_count = PredictionRecord.query.filter_by(user_id=user.id).count()
            writer.writerow([
                user.id,
                user.username,
                user.email,
                '是' if user.is_active else '否',
                '是' if user.is_admin else '否',
                user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else '',
                user.activation_expires_at.strftime('%Y-%m-%d %H:%M:%S') if user.activation_expires_at else '永久',
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
@admin_bp.route('/export-users-json')
@admin_required
def export_users_json():
    try:
        users_data = []
        users = User.query.all()
        
        for user in users:
            # 获取用户的预测记录
            predictions = PredictionRecord.query.filter_by(user_id=user.id).all()
            predictions_data = []
            
            for pred in predictions:
                predictions_data.append({
                    'region': pred.region,
                    'strategy': pred.strategy,
                    'period': pred.period,
                    'normal_numbers': pred.normal_numbers,
                    'special_number': pred.special_number,
                    'special_zodiac': pred.special_zodiac,
                    'prediction_text': pred.prediction_text,
                    'created_at': pred.created_at.isoformat() if pred.created_at else None,
                    'accuracy_score': pred.accuracy_score
                })
            
            user_data = {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_admin': user.is_admin,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'activation_expires_at': user.activation_expires_at.isoformat() if user.activation_expires_at else None,
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
@admin_bp.route('/import-users', methods=['GET', 'POST'])
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
                            user.activation_expires_at = datetime.fromisoformat(user_data['activation_expires_at'])
                        
                        db.session.add(user)
                        db.session.flush()  # 获取用户ID
                        
                        # 导入预测记录
                        for pred_data in user_data.get('predictions', []):
                            prediction = PredictionRecord(
                                user_id=user.id,
                                region=pred_data['region'],
                                strategy=pred_data['strategy'],
                                period=pred_data['period'],
                                normal_numbers=pred_data['normal_numbers'],
                                special_number=pred_data['special_number'],
                                special_zodiac=pred_data.get('special_zodiac', ''),
                                prediction_text=pred_data.get('prediction_text', ''),
                                accuracy_score=pred_data.get('accuracy_score')
                            )
                            if pred_data.get('created_at'):
                                prediction.created_at = datetime.fromisoformat(pred_data['created_at'])
                            
                            db.session.add(prediction)
                        
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