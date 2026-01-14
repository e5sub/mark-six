from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from functools import wraps
from models import db, User, ActivationCode, PredictionRecord, SystemConfig, InviteCode, ZodiacSetting, ManualBetRecord
from datetime import datetime, timedelta
import csv
import json
import io
from sqlalchemy import func, case

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
        
        # 获取邀请统计数据
        total_invite_codes = InviteCode.query.count()
        used_invite_codes = InviteCode.query.filter_by(is_used=True).count()
        unused_invite_codes = total_invite_codes - used_invite_codes
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
            'random_accuracy': random_accuracy,
            'balanced_accuracy': balanced_accuracy,
            'ai_accuracy': ai_accuracy,
            'recent_users': recent_users,
            'recent_predictions': recent_predictions,
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
            'random_accuracy': 0.0,
            'balanced_accuracy': 0.0,
            'ai_accuracy': 0.0,
            'recent_users': [],
            'recent_predictions': [],
            'invite_stats': {
                'total_invite_codes': 0,
                'used_invite_codes': 0,
                'unused_invite_codes': 0,
                'total_invites': 0
            }
        })

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
            
            # 防止停用admin账号
            if user.username == 'admin' and not is_active:
                flash('不能停用admin账号', 'error')
                return render_template('admin/edit_user.html', user=user)
            
            # 更新用户信息
            user.username = new_username
            user.email = new_email
            user.is_active = is_active
            
            # 如果是admin账号，保持管理员权限
            if user.username == 'admin':
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

@admin_bp.route('/users/<int:user_id>/activate', methods=['POST'])
@admin_required
def activate_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        user.is_active = True
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
                'invite_daily_limit': request.form.get('invite_daily_limit', '3'),
                'invite_code_validity_days': request.form.get('invite_code_validity_days', '7'),
                'system_name': request.form.get('system_name', '六合彩预测系统'),
                'system_description': request.form.get('system_description', ''),
                'allow_registration': request.form.get('allow_registration', 'false'),
                'require_email_verification': request.form.get('require_email_verification', 'false'),
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
            'invite_daily_limit': '3',
            'invite_code_validity_days': '7',
            'system_name': '六合彩预测系统',
            'system_description': '',
            'allow_registration': 'true',
            'require_email_verification': 'false',
        })

@admin_bp.route('/system_config/save', methods=['POST'])
@admin_required
def save_system_config():
    try:
        # 获取JSON数据
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的数据格式'})
        
        # 保存配置
        for key, value in data.items():
            SystemConfig.set_config(key, value)
        
        return jsonify({'success': True, 'message': '配置保存成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

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
        return render_template('admin/predictions.html', predictions=empty_predictions)

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
        # 获取当前年份
        current_year = request.args.get('year', datetime.now().year, type=int)
        
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

