from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from functools import wraps
from models import db, User, ActivationCode, PredictionRecord, SystemConfig
from datetime import datetime, timedelta
import csv
import json
import io

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
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
    return decorated_function

@admin_bp.route('/dashboard')
@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    try:
        # 获取统计数据
        total_users = User.query.count()
        active_users = User.query.filter_by(is_active=True).count()
        total_codes = ActivationCode.query.count()
        used_codes = ActivationCode.query.filter_by(is_used=True).count()
        total_predictions = PredictionRecord.query.count()
        
        # 最近注册的用户
        recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
        
        # 最近的预测记录
        recent_predictions = PredictionRecord.query.order_by(PredictionRecord.created_at.desc()).limit(5).all()
        
        # 为预测记录添加用户名
        for pred in recent_predictions:
            if pred.user_id:
                user = User.query.get(pred.user_id)
                pred.username = user.username if user else '已删除用户'
            else:
                pred.username = '未知用户'
        
        stats = {
            'total_users': total_users,
            'active_users': active_users,
            'total_codes': total_codes,
            'used_codes': used_codes,
            'total_predictions': total_predictions,
            'recent_users': recent_users,
            'recent_predictions': recent_predictions
        }
        
        return render_template('admin/dashboard.html', stats=stats)
    except Exception as e:
        flash(f'加载控制台数据失败: {str(e)}', 'error')
        return render_template('admin/dashboard.html', stats={
            'total_users': 0,
            'active_users': 0,
            'total_codes': 0,
            'used_codes': 0,
            'total_predictions': 0,
            'recent_users': [],
            'recent_predictions': []
        })

@admin_bp.route('/users')
@admin_required
def users():
    page = request.args.get('page', 1, type=int)
    users = User.query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template('admin/users.html', users=users)

@admin_bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        # 更新用户信息
        user.username = request.form.get('username')
        user.email = request.form.get('email')
        user.is_active = 'is_active' in request.form
        user.is_admin = 'is_admin' in request.form
        
        # 处理激活时间
        activation_type = request.form.get('activation_type')
        if activation_type and user.is_active:
            if activation_type == 'permanent':
                user.activation_expires_at = None
            else:
                days_map = {'1day': 1, '1month': 30, '3months': 90, '1year': 365}
                if activation_type in days_map:
                    user.activation_expires_at = datetime.utcnow() + timedelta(days=days_map[activation_type])
        
        try:
            db.session.commit()
            flash(f'用户 {user.username} 信息已更新', 'success')
            return redirect(url_for('admin.users'))
        except Exception as e:
            db.session.rollback()
            flash(f'更新用户信息失败: {str(e)}', 'error')
    
    return render_template('admin/edit_user.html', user=user)

@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # 防止删除管理员账号
    if user.is_admin:
        flash('不能删除管理员账号', 'error')
        return redirect(url_for('admin.users'))
    
    try:
        # 删除用户相关的预测记录
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
    page = request.args.get('page', 1, type=int)
    codes = ActivationCode.query.order_by(ActivationCode.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    
    # 为每个激活码添加用户名信息
    for code in codes.items:
        if code.used_by:
            user = User.query.get(code.used_by)
            code.username = user.username if user else '已删除用户'
        else:
            code.username = None
    
    return render_template('admin/activation_codes.html', codes=codes)

@admin_bp.route('/activation_codes/generate', methods=['POST'])
@admin_required
def generate_activation_codes():
    count = request.form.get('count', type=int)
    duration_days = request.form.get('duration_days', type=int)
    
    if not count or count <= 0 or count > 100:
        flash('请输入有效的生成数量 (1-100)', 'error')
        return redirect(url_for('admin.activation_codes'))
    
    if not duration_days or duration_days <= 0:
        flash('请输入有效的有效期天数', 'error')
        return redirect(url_for('admin.activation_codes'))
    
    try:
        import secrets
        import string
        
        generated_codes = []
        for _ in range(count):
            # 生成16位随机激活码
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))
            
            activation_code = ActivationCode(
                code=code,
                duration_days=duration_days
            )
            db.session.add(activation_code)
            generated_codes.append(code)
        
        db.session.commit()
        flash(f'成功生成 {count} 个激活码', 'success')
        
        # 将生成的激活码存储在session中，用于显示
        session['generated_codes'] = generated_codes
        
    except Exception as e:
        db.session.rollback()
        flash(f'生成激活码失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.activation_codes'))

@admin_bp.route('/activation_codes/<int:code_id>/delete', methods=['POST'])
@admin_required
def delete_activation_code(code_id):
    code = ActivationCode.query.get_or_404(code_id)
    
    try:
        db.session.delete(code)
        db.session.commit()
        flash('激活码已删除', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除激活码失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.activation_codes'))

@admin_bp.route('/system_config', methods=['GET', 'POST'])
@admin_required
def system_config():
    if request.method == 'POST':
        # 更新配置
        configs = [
            'ai_api_key', 'ai_api_url', 'ai_model',
            'smtp_server', 'smtp_port', 'smtp_username', 'smtp_password'
        ]
        
        try:
            for key in configs:
                value = request.form.get(key, '')
                SystemConfig.set_config(key, value)
            
            flash('系统配置已更新', 'success')
        except Exception as e:
            flash(f'更新配置失败: {str(e)}', 'error')
    
    # 获取当前配置
    config_data = {}
    config_keys = [
        ('ai_api_key', 'AI API密钥'),
        ('ai_api_url', 'AI API地址'),
        ('ai_model', 'AI模型'),
        ('smtp_server', 'SMTP服务器'),
        ('smtp_port', 'SMTP端口'),
        ('smtp_username', 'SMTP用户名'),
        ('smtp_password', 'SMTP密码'),
    ]
    
    for key, description in config_keys:
        config_data[key] = {
            'value': SystemConfig.get_config(key, ''),
            'description': description
        }
    
    return render_template('admin/system_config.html', config_data=config_data)

@admin_bp.route('/predictions')
@admin_required
def predictions():
    page = request.args.get('page', 1, type=int)
    predictions = PredictionRecord.query.order_by(PredictionRecord.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    
    # 为每个预测记录添加用户名信息
    for pred in predictions.items:
        if pred.user_id:
            user = User.query.get(pred.user_id)
            pred.username = user.username if user else '已删除用户'
        else:
            pred.username = '未知用户'
    
    return render_template('admin/predictions.html', predictions=predictions)

@admin_bp.route('/prediction/<int:prediction_id>/delete', methods=['POST'])
@admin_required
def delete_prediction(prediction_id):
    prediction = PredictionRecord.query.get_or_404(prediction_id)
    
    try:
        db.session.delete(prediction)
        db.session.commit()
        flash(f'预测记录已删除', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'删除预测记录失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.predictions'))

@admin_bp.route('/predictions/delete_batch', methods=['POST'])
@admin_required
def delete_predictions_batch():
    prediction_ids = request.form.getlist('prediction_ids')
    
    if not prediction_ids:
        flash('请选择要删除的预测记录', 'error')
        return redirect(url_for('admin.predictions'))
    
    try:
        # 批量删除预测记录
        deleted_count = PredictionRecord.query.filter(PredictionRecord.id.in_(prediction_ids)).delete(synchronize_session=False)
        db.session.commit()
        flash(f'成功删除 {deleted_count} 条预测记录', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'批量删除预测记录失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.predictions'))

@admin_bp.route('/predictions/clear_all', methods=['POST'])
@admin_required
def clear_all_predictions():
    try:
        # 删除所有预测记录
        deleted_count = PredictionRecord.query.delete()
        db.session.commit()
        flash(f'成功清空所有预测记录，共删除 {deleted_count} 条记录', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'清空预测记录失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.predictions'))

@admin_bp.route('/users/export')
@admin_required
def export_users():
    format_type = request.args.get('format', 'csv')
    
    users = User.query.all()
    
    if format_type == 'csv':
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
    
    elif format_type == 'json':
        users_data = []
        for user in users:
            users_data.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_admin': user.is_admin,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'activation_expires_at': user.activation_expires_at.isoformat() if user.activation_expires_at else None
            })
        
        from flask import Response
        return Response(
            json.dumps(users_data, ensure_ascii=False, indent=2),
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=users.json'}
        )
    
    flash('不支持的导出格式', 'error')
    return redirect(url_for('admin.users'))

@admin_bp.route('/users/import', methods=['GET', 'POST'])
@admin_required
def import_users():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('请选择要导入的文件', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('请选择要导入的文件', 'error')
            return redirect(request.url)
        
        try:
            if file.filename.endswith('.csv'):
                # 处理CSV文件
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                csv_input = csv.reader(stream)
                
                # 跳过标题行
                next(csv_input)
                
                imported_count = 0
                for row in csv_input:
                    if len(row) >= 3:  # 至少需要用户名、邮箱、密码
                        username, email, password = row[0], row[1], row[2]
                        
                        # 检查用户是否已存在
                        if User.query.filter_by(username=username).first():
                            continue
                        
                        user = User(username=username, email=email)
                        user.set_password(password)
                        db.session.add(user)
                        imported_count += 1
                
                db.session.commit()
                flash(f'成功导入 {imported_count} 个用户', 'success')
                
            elif file.filename.endswith('.json'):
                # 处理JSON文件
                data = json.loads(file.stream.read().decode("UTF8"))
                
                imported_count = 0
                for user_data in data:
                    username = user_data.get('username')
                    email = user_data.get('email')
                    password = user_data.get('password')
                    
                    if username and email and password:
                        # 检查用户是否已存在
                        if User.query.filter_by(username=username).first():
                            continue
                        
                        user = User(username=username, email=email)
                        user.set_password(password)
                        user.is_active = user_data.get('is_active', False)
                        user.is_admin = user_data.get('is_admin', False)
                        db.session.add(user)
                        imported_count += 1
                
                db.session.commit()
                flash(f'成功导入 {imported_count} 个用户', 'success')
            
            else:
                flash('不支持的文件格式，请使用CSV或JSON文件', 'error')
                
        except Exception as e:
            db.session.rollback()
            flash(f'导入失败: {str(e)}', 'error')
        
        return redirect(url_for('admin.users'))
    
    return render_template('admin/import_users.html')