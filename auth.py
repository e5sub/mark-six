from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, User, ActivationCode, SystemConfig
from werkzeug.security import generate_password_hash
import uuid
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # 验证输入
        if not all([username, email, password, confirm_password]):
            flash('所有字段都是必填的', 'error')
            return render_template('auth/register.html')
        
        if password != confirm_password:
            flash('两次输入的密码不一致', 'error')
            return render_template('auth/register.html')
        
        if len(password) < 6:
            flash('密码长度至少6位', 'error')
            return render_template('auth/register.html')
        
        # 检查用户名和邮箱是否已存在
        if User.query.filter_by(username=username).first():
            flash('用户名已存在', 'error')
            return render_template('auth/register.html')
        
        if User.query.filter_by(email=email).first():
            flash('邮箱已被注册', 'error')
            return render_template('auth/register.html')
        
        # 创建用户（默认为普通用户，非管理员）
        user = User(username=username, email=email, is_admin=False)
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        flash('注册成功！请使用管理员提供的激活码激活您的账号。', 'success')
        
        return redirect(url_for('auth.login'))
    
    return render_template('auth/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('请输入用户名和密码', 'error')
            return render_template('auth/login.html')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            session['is_active'] = user.is_active
            
            if user.is_admin:
                return redirect(url_for('admin.dashboard'))
            else:
                return redirect(url_for('user.dashboard'))
        else:
            flash('用户名或密码错误', 'error')
    
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('已成功退出登录', 'success')
    return redirect(url_for('auth.login'))

@auth_bp.route('/activate', methods=['GET', 'POST'])
def activate():
    if 'user_id' not in session:
        flash('请先登录', 'error')
        return redirect(url_for('auth.login'))
    
    user = User.query.get(session['user_id'])
    if user.is_active:
        flash('您的账号已经激活', 'info')
        return redirect(url_for('user.dashboard'))
    
    if request.method == 'POST':
        activation_code = request.form.get('activation_code')
        
        if not activation_code:
            flash('请输入激活码', 'error')
            return render_template('auth/activate.html')
        
        # 只检查管理员生成的激活码
        code_record = ActivationCode.query.filter_by(code=activation_code).first()
        if code_record:
            # 使用激活码验证方法
            success, message = code_record.use_code(user.id)
            if success:
                user.is_active = True
                db.session.commit()
                session['is_active'] = True
                flash('账号激活成功！现在可以使用全部功能了。', 'success')
                return redirect(url_for('user.dashboard'))
            else:
                flash(message, 'error')
                return render_template('auth/activate.html')
        
        flash('激活码无效或已被使用', 'error')
    
    return render_template('auth/activate.html')