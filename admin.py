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

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    try:
        # 获取统计数据
        total_users = User.query.count()
        active_users = User.query.filter_by(is_active=True).count()
        inactive_users = total_users - active_users
        total_codes = ActivationCode.query.count()
        used_codes = ActivationCode.query.filter_by(is_used=True).count()
        unused_codes = total_codes - used_codes
        total_predictions = PredictionRecord.query.count()
        
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
        
        random_accuracy = calculate_accuracy('random')
        balanced_accuracy = calculate_accuracy('balanced')
        ai_accuracy = calculate_accuracy('ai')
        
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
            'inactive_users': inactive_users,
            'total_codes': total_codes,
            'used_codes': used_codes,
            'unused_codes': unused_codes,
            'total_predictions': total_predictions,
            'avg_accuracy': avg_accuracy,
            'random_accuracy': random_accuracy,
            'balanced_accuracy': balanced_accuracy,
            'ai_accuracy': ai_accuracy,
            'recent_users': recent_users,
            'recent_predictions': recent_predictions
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
            'random_accuracy': 0.0,
            'balanced_accuracy': 0.0,
            'ai_accuracy': 0.0,
            'recent_users': [],
            'recent_predictions': []
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
        flash(f'加载用户数据失败: {str(e)}', 'error')
        # 创建空的分页对象
        from flask_sqlalchemy import Pagination
        empty_users = Pagination(query=None, page=1, per_page=20, total=0, items=[])
        return render_template('admin/users.html', users=empty_users)

@admin_bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        
        if request.method == 'POST':
            user.username = request.form.get('username')
            user.email = request.form.get('email')
            user.is_active = 'is_active' in request.form
            user.is_admin = 'is_admin' in request.form
            
            # 处理激活时间延长
            extend_type = request.form.get('extend_activation')
            if extend_type and extend_type != 'none':
                if extend_type == 'day':
                    user.activation_expires_at = datetime.utcnow() + timedelta(days=1)
                elif extend_type == 'month':
                    user.activation_expires_at = datetime.utcnow() + timedelta(days=30)
                elif extend_type == 'quarter':
                    user.activation_expires_at = datetime.utcnow() + timedelta(days=90)
                elif extend_type == 'year':
                    user.activation_expires_at = datetime.utcnow() + timedelta(days=365)
                elif extend_type == 'permanent':
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

@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
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
    try:
        page = request.args.get('page', 1, type=int)
        codes = ActivationCode.query.order_by(ActivationCode.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        # 为激活码添加使用者用户名
        for code in codes.items:
            if code.used_by:
                # used_by现在存储的是用户名，不是用户ID
                code.used_by_username = code.used_by
            else:
                code.used_by_username = None
        
        return render_template('admin/activation_codes.html', codes=codes)
    except Exception as e:
        flash(f'加载激活码数据失败: {str(e)}', 'error')
        # 创建空的分页对象
        from flask_sqlalchemy import Pagination
        empty_codes = Pagination(query=None, page=1, per_page=20, total=0, items=[])
        return render_template('admin/activation_codes.html', codes=empty_codes)

@admin_bp.route('/generate_codes', methods=['POST'])
@admin_required
def generate_codes():
    try:
        count = int(request.form.get('count', 1))
        validity_type = request.form.get('validity_type', 'permanent')
        
        if count < 1 or count > 100:
            flash('生成数量必须在1-100之间', 'error')
            return redirect(url_for('admin.activation_codes'))
        
        generated_codes = []
        for _ in range(count):
            code = ActivationCode()
            code.code = ActivationCode.generate_code()
            code.set_validity(validity_type)
            db.session.add(code)
            generated_codes.append(code.code)
        
        db.session.commit()
        
        flash(f'成功生成 {count} 个激活码', 'success')
    except Exception as e:
        flash(f'生成激活码失败: {str(e)}', 'error')
    
    return redirect(url_for('admin.activation_codes'))

@admin_bp.route('/system_config', methods=['GET', 'POST'])
@admin_required
def system_config():
    try:
        if request.method == 'POST':
            # 更新配置
            configs = {
                'ai_api_key': request.form.get('ai_api_key', ''),
                'ai_api_url': request.form.get('ai_api_url', 'https://api.deepseek.com/v1/chat/completions'),
                'ai_model': request.form.get('ai_model', 'deepseek-chat'),
                'smtp_server': request.form.get('smtp_server', ''),
                'smtp_port': request.form.get('smtp_port', '587'),
                'smtp_username': request.form.get('smtp_username', ''),
                'smtp_password': request.form.get('smtp_password', ''),
                'site_name': request.form.get('site_name', '六合彩预测系统'),
                'site_description': request.form.get('site_description', ''),
            }
            
            try:
                for key, value in configs.items():
                    SystemConfig.set_config(key, value)
                flash('系统配置更新成功', 'success')
            except Exception as e:
                flash(f'配置更新失败: {str(e)}', 'error')
            
            return redirect(url_for('admin.system_config'))
        
        # 获取当前配置
        configs = {
            'ai_api_key': SystemConfig.get_config('ai_api_key', ''),
            'ai_api_url': SystemConfig.get_config('ai_api_url', 'https://api.deepseek.com/v1/chat/completions'),
            'ai_model': SystemConfig.get_config('ai_model', 'deepseek-chat'),
            'smtp_server': SystemConfig.get_config('smtp_server', ''),
            'smtp_port': SystemConfig.get_config('smtp_port', '587'),
            'smtp_username': SystemConfig.get_config('smtp_username', ''),
            'smtp_password': SystemConfig.get_config('smtp_password', ''),
            'site_name': SystemConfig.get_config('site_name', '六合彩预测系统'),
            'site_description': SystemConfig.get_config('site_description', ''),
        }
        
        return render_template('admin/system_config.html', configs=configs)
    except Exception as e:
        flash(f'加载系统配置失败: {str(e)}', 'error')
        return render_template('admin/system_config.html', configs={
            'ai_api_key': '',
            'ai_api_url': 'https://api.deepseek.com/v1/chat/completions',
            'ai_model': 'deepseek-chat',
            'smtp_server': '',
            'smtp_port': '587',
            'smtp_username': '',
            'smtp_password': '',
            'site_name': '六合彩预测系统',
            'site_description': '',
        })

@admin_bp.route('/predictions')
@admin_required
def predictions():
    try:
        page = request.args.get('page', 1, type=int)
        predictions = PredictionRecord.query.order_by(PredictionRecord.created_at.desc()).paginate(
            page=page, per_page=20, error_out=False
        )
        
        # 为预测记录添加用户名
        for pred in predictions.items:
            if pred.user_id:
                user = User.query.get(pred.user_id)
                pred.username = user.username if user else '已删除用户'
            else:
                pred.username = '未知用户'
        
        return render_template('admin/predictions.html', predictions=predictions)
    except Exception as e:
        flash(f'加载预测记录失败: {str(e)}', 'error')
        # 创建空的分页对象
        from flask_sqlalchemy import Pagination
        empty_predictions = Pagination(query=None, page=1, per_page=20, total=0, items=[])
        return render_template('admin/predictions.html', predictions=empty_predictions)

@admin_bp.route('/prediction/<int:prediction_id>/delete', methods=['POST'])
@admin_required
def delete_prediction(prediction_id):
    try:
        prediction = PredictionRecord.query.get_or_404(prediction_id)
        db.session.delete(prediction)
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
        
        deleted_count = 0
        for pred_id in prediction_ids:
            prediction = PredictionRecord.query.get(int(pred_id))
            if prediction:
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