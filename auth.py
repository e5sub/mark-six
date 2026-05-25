from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, abort
from models import db, User, ActivationCode, ActivationCodeRequest, SystemConfig, InviteCode
from werkzeug.security import generate_password_hash
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import secrets

auth_bp = Blueprint('auth', __name__)


def _is_config_enabled(key, default='false'):
    raw = str(SystemConfig.get_config(key, default)).strip().lower()
    return raw in {'true', '1', 'yes', 'on'}


def _has_smtp_config():
    smtp_server = SystemConfig.get_config('smtp_server')
    smtp_username = SystemConfig.get_config('smtp_username')
    smtp_password = SystemConfig.get_config('smtp_password')
    return all([smtp_server, smtp_username, smtp_password])


def _email_verification_required():
    return _is_config_enabled('require_email_verification', 'false')


def _has_admin_account():
    return db.session.query(User.id).filter(User.is_admin.is_(True)).first() is not None


def _render_auth_template(template_name, **context):
    context.setdefault('require_email_verification', _email_verification_required())
    context.setdefault('admin_setup_required', not _has_admin_account())
    return render_template(template_name, **context)


def _email_verification_status_key(user_id):
    return f"email_verified_{user_id}"


def _email_verification_token_key(user_id):
    return f"email_verify_token_{user_id}"


def _is_email_verified(user):
    if not user or getattr(user, 'is_admin', False):
        return True
    status = str(
        SystemConfig.get_config(_email_verification_status_key(user.id), 'verified')
    ).strip().lower()
    return status in {'verified', 'true', '1', 'yes', 'on'}


def _mark_email_verification_pending(user):
    SystemConfig.set_config(
        _email_verification_status_key(user.id),
        'pending',
        f'Email verification status for user {user.id}'
    )


def _mark_email_verified(user):
    SystemConfig.set_config(
        _email_verification_status_key(user.id),
        'verified',
        f'Email verification status for user {user.id}'
    )
    token_config = SystemConfig.query.filter_by(
        key=_email_verification_token_key(user.id)
    ).first()
    if token_config:
        db.session.delete(token_config)
        db.session.commit()


def _create_email_verification_token(user, ttl_hours=24):
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    payload = f"{token}|{expires_at.isoformat()}|{user.email}"
    SystemConfig.set_config(
        _email_verification_token_key(user.id),
        payload,
        f'Email verification token for user {user.id}'
    )
    return token


def _resolve_email_verification_user(token):
    for config in SystemConfig.query.filter(SystemConfig.key.like('email_verify_token_%')).all():
        try:
            stored_token, expires_str, email = (config.value or '').split('|', 2)
            expires_at = datetime.fromisoformat(expires_str)
            if stored_token != token or datetime.utcnow() >= expires_at:
                continue
            user_id = int(config.key.replace('email_verify_token_', ''))
            user = User.query.get(user_id)
            if user and str(user.email or '').strip().lower() == str(email or '').strip().lower():
                return user
        except Exception:
            continue
    return None


def _send_html_email(email, subject, html_body):
    smtp_server = SystemConfig.get_config('smtp_server')
    smtp_port = int(SystemConfig.get_config('smtp_port', '587'))
    smtp_username = SystemConfig.get_config('smtp_username')
    smtp_password = SystemConfig.get_config('smtp_password')

    if not all([smtp_server, smtp_username, smtp_password]):
        raise Exception('邮件服务未配置，请联系管理员')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_username
    msg['To'] = email
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(smtp_username, smtp_password)
    server.send_message(msg)
    server.quit()


def _get_admin_notification_emails():
    raw_emails = str(
        SystemConfig.get_config('activation_request_notify_emails', '')
    ).strip()
    recipients = []

    if raw_emails:
        normalized = raw_emails.replace(';', ',')
        for chunk in normalized.replace(';', ',').split(','):
            email = chunk.strip()
            if email and email not in recipients:
                recipients.append(email)

    if recipients:
        return recipients

    admin_users = User.query.filter(
        User.is_admin.is_(True),
        User.email.isnot(None),
        User.email != ''
    ).all()
    for admin in admin_users:
        email = str(admin.email or '').strip()
        if email and email not in recipients:
            recipients.append(email)
    return recipients


def send_activation_request_notification(request_record):
    if not _has_smtp_config():
        return False

    recipients = _get_admin_notification_emails()
    if not recipients:
        return False

    site_name = SystemConfig.get_config('site_name', 'AI数据分析预测系统')
    admin_url = url_for('admin.activation_codes', _external=True)
    created_at = getattr(request_record, 'created_at', None)
    created_at_text = (
        created_at.strftime('%Y-%m-%d %H:%M:%S UTC')
        if created_at else
        datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    )
    request_note = (getattr(request_record, 'request_note', '') or '').strip() or 'N/A'
    subject = f'{site_name} - New activation code request'
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #28a745;">New activation code request received</h2>
            <p>A user has just submitted an activation code request. Please review it soon.</p>
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr><td style="padding: 8px; border: 1px solid #eee; width: 140px;"><strong>Request ID</strong></td><td style="padding: 8px; border: 1px solid #eee;">{request_record.id}</td></tr>
                <tr><td style="padding: 8px; border: 1px solid #eee;"><strong>Username</strong></td><td style="padding: 8px; border: 1px solid #eee;">{request_record.username}</td></tr>
                <tr><td style="padding: 8px; border: 1px solid #eee;"><strong>Email</strong></td><td style="padding: 8px; border: 1px solid #eee;">{request_record.email}</td></tr>
                <tr><td style="padding: 8px; border: 1px solid #eee;"><strong>Status</strong></td><td style="padding: 8px; border: 1px solid #eee;">{request_record.status}</td></tr>
                <tr><td style="padding: 8px; border: 1px solid #eee;"><strong>Requested At</strong></td><td style="padding: 8px; border: 1px solid #eee;">{created_at_text}</td></tr>
                <tr><td style="padding: 8px; border: 1px solid #eee;"><strong>Note</strong></td><td style="padding: 8px; border: 1px solid #eee; white-space: pre-wrap;">{request_note}</td></tr>
            </table>
            <div style="margin: 30px 0; text-align: center;">
                <a href="{admin_url}"
                   style="background: #28a745; color: white; text-decoration: none; padding: 12px 24px; border-radius: 6px; display: inline-block;">
                    Open admin panel
                </a>
            </div>
            <p style="color: #999; font-size: 12px; text-align: center;">
                This email was sent automatically by {site_name}. Please do not reply.
            </p>
        </div>
    </body>
    </html>
    """

    for email in recipients:
        _send_html_email(email, subject, html_body)
    return True


def send_verification_email(user, token):
    site_name = SystemConfig.get_config('site_name', 'AI数据分析预测系统')
    verify_url = url_for('auth.verify_email', token=token, _external=True)
    subject = f'{site_name} - 邮箱验证'
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #667eea;">验证您的邮箱</h2>
            <p>亲爱的 {user.username}：</p>
            <p>请点击下面的按钮完成邮箱验证，验证通过后即可正常登录使用系统。</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{verify_url}"
                   style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                          color: white;
                          padding: 12px 30px;
                          text-decoration: none;
                          border-radius: 25px;
                          display: inline-block;">
                    立即验证邮箱
                </a>
            </div>
            <p>如果按钮无法点击，请复制以下链接到浏览器打开：</p>
            <p style="word-break: break-all; background: #f5f5f5; padding: 10px; border-radius: 5px;">
                {verify_url}
            </p>
            <p style="color: #666; font-size: 14px;">
                此链接将在 24 小时后失效。如非本人操作，请忽略此邮件。
            </p>
        </div>
    </body>
    </html>
    """
    _send_html_email(user.email, subject, html_body)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if not _is_config_enabled('allow_registration', 'true'):
        flash('当前已关闭新用户注册，如需开通请联系管理员', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        first_admin = not _has_admin_account()
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        invite_code = request.form.get('invite_code', '').strip()
        require_email_verification = _email_verification_required()
        
        # 验证输入
        if not all([username, email, password, confirm_password]):
            flash('所有字段都是必填的', 'error')
            return _render_auth_template('auth/register.html')
        
        if password != confirm_password:
            flash('两次输入的密码不一致', 'error')
            return _render_auth_template('auth/register.html')
        
        if len(password) < 6:
            flash('密码长度至少6位', 'error')
            return _render_auth_template('auth/register.html')
        
        # 检查用户名和邮箱是否已存在
        if User.query.filter_by(username=username).first():
            flash('用户名已存在', 'error')
            return _render_auth_template('auth/register.html')
        
        if User.query.filter_by(email=email).first():
            flash('邮箱已被注册', 'error')
            return _render_auth_template('auth/register.html')

        if require_email_verification and not _has_smtp_config():
            flash('当前已开启邮箱验证，但邮件服务未配置完成，请联系管理员', 'error')
            return _render_auth_template('auth/register.html')
        
        # 首个注册用户自动成为管理员
        user = User(username=username, email=email, is_admin=first_admin)
        user.set_password(password)
        
        db.session.add(user)
        db.session.flush()  # 获取用户ID但不提交事务
        
        # 处理邀请码
        invite_success = False
        if invite_code:
            try:
                invite_record = InviteCode.query.filter_by(code=invite_code).first()
                if invite_record:
                    success, message = invite_record.use_invite_code(user)
                    if success:
                        invite_success = True
                        flash(f'注册成功！{message}，您已获得1天有效期。', 'success')
                    else:
                        flash(f'邀请码错误：{message}', 'error')
                        db.session.rollback()
                        return _render_auth_template('auth/register.html')
                else:
                    flash('邀请码无效', 'error')
                    db.session.rollback()
                    return _render_auth_template('auth/register.html')
            except Exception as e:
                print(f"邀请码处理错误: {e}")
                flash('邀请码处理时出现错误，请稍后重试', 'error')
                db.session.rollback()
                return _render_auth_template('auth/register.html')
        
        if first_admin or not invite_code:
            user.extend_activation(7)
            user.is_active = True
            user.auto_prediction_enabled = True

        db.session.commit()

        if first_admin:
            flash('注册成功，您已成为首个管理员账号', 'success')
        elif require_email_verification:
            try:
                _mark_email_verification_pending(user)
                verification_token = _create_email_verification_token(user)
                send_verification_email(user, verification_token)
                flash('注册成功，请先到邮箱完成验证后再登录', 'success')
            except Exception as e:
                flash(f'注册成功，但验证邮件发送失败：{str(e)}', 'warning')
        elif not invite_success:
            flash('注册成功！账号已自动激活 7 天。', 'success')
        
        return redirect(url_for('auth.login'))
    
    return _render_auth_template('auth/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_or_email = request.form.get('username')
        password = request.form.get('password')
        
        if not username_or_email or not password:
            flash('请输入用户名/邮箱和密码', 'error')
            return _render_auth_template('auth/login.html')
        
        # 支持用户名或邮箱登录
        user = User.query.filter((User.username == username_or_email) | 
                                (User.email == username_or_email)).first()
        
        if user and user.check_password(password):
            if _email_verification_required() and not _is_email_verified(user):
                try:
                    verification_token = _create_email_verification_token(user)
                    send_verification_email(user, verification_token)
                    flash('您的邮箱还未验证，验证邮件已重新发送，请查收后再登录', 'warning')
                except Exception as e:
                    flash(f'您的邮箱还未验证，且补发验证邮件失败：{str(e)}', 'error')
                return _render_auth_template('auth/login.html')

            user.check_and_update_activation_status()
            # 更新登录统计信息
            user.last_login = datetime.utcnow()
            user.login_count = (user.login_count or 0) + 1
            db.session.commit()
            
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            session['is_active'] = user.is_active
            session.permanent = True
            
            if user.is_admin:
                return redirect(url_for('admin.dashboard'))
            else:
                return redirect(url_for('user.dashboard'))
        else:
            flash('用户名/邮箱或密码错误', 'error')
    
    return _render_auth_template('auth/login.html')


@auth_bp.route('/setup-admin', methods=['GET', 'POST'])
def setup_admin():
    abort(404)

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('已成功退出登录', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/verify_email/<token>')
def verify_email(token):
    user = _resolve_email_verification_user(token)
    if not user:
        flash('邮箱验证链接无效或已过期，请重新登录后获取新的验证邮件', 'error')
        return redirect(url_for('auth.login'))

    _mark_email_verified(user)
    flash('邮箱验证成功，现在可以正常登录了', 'success')
    return redirect(url_for('auth.login'))

@auth_bp.route('/activate', methods=['GET', 'POST'])
def activate():
    if 'user_id' not in session:
        flash('请先登录', 'error')
        return redirect(url_for('auth.login'))
    
    user = User.query.get(session['user_id'])

    def _render_activate_page():
        requests = ActivationCodeRequest.query.filter_by(user_id=user.id).order_by(
            ActivationCodeRequest.created_at.desc()
        ).limit(10).all()
        has_pending_request = any(item.status == 'pending' for item in requests)
        return render_template(
            'auth/activate.html',
            activation_requests=requests,
            has_pending_request=has_pending_request,
        )

    if user.is_active:
        flash('您的账号已经激活', 'info')
        return redirect(url_for('user.dashboard'))
    
    if request.method == 'POST':
        activation_code = request.form.get('activation_code')
        
        if not activation_code:
            flash('请输入激活码', 'error')
            return _render_activate_page()
        
        # 只检查管理员生成的激活码
        code_record = ActivationCode.query.filter_by(code=activation_code).first()
        if code_record:
            # 使用激活码验证方法，传入用户对象
            success, message = code_record.use_code(user)
            if success:
                db.session.commit()
                session['is_active'] = True
                flash('账号激活成功！现在可以使用全部功能了。', 'success')
                return redirect(url_for('user.dashboard'))
            else:
                flash(message, 'error')
                return _render_activate_page()
        
        flash('激活码无效或已被使用', 'error')

    return _render_activate_page()


@auth_bp.route('/request_activation_code', methods=['POST'])
def request_activation_code():
    if 'user_id' not in session:
        flash('请先登录', 'error')
        return redirect(url_for('auth.login'))

    user = User.query.get(session['user_id'])
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('auth.login'))

    if user.is_active:
        flash('账号已激活，无需申请激活码', 'info')
        return redirect(url_for('user.dashboard'))

    pending_request = ActivationCodeRequest.query.filter_by(
        user_id=user.id,
        status='pending'
    ).first()
    if pending_request:
        flash('您已有待处理的激活码申请，请等待管理员发放', 'info')
        return redirect(url_for('auth.activate'))

    request_note = (request.form.get('request_note') or '').strip()
    request_record = ActivationCodeRequest(
        user_id=user.id,
        username=user.username,
        email=user.email,
        request_note=request_note,
        status='pending',
    )
    db.session.add(request_record)
    db.session.commit()
    flash('激活码申请已提交，管理员处理后会显示在本页', 'success')
    return redirect(url_for('auth.activate'))

@auth_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        
        if not email:
            flash('请输入邮箱地址', 'error')
            return render_template('auth/forgot_password.html')
        
        user = User.query.filter_by(email=email).first()
        if not user:
            flash('该邮箱地址未注册', 'error')
            return render_template('auth/forgot_password.html')
        
        # 生成重置令牌
        reset_token = secrets.token_urlsafe(32)
        
        # 将令牌存储到系统配置中，设置1小时过期
        token_key = f"reset_token_{user.id}"
        token_data = f"{reset_token}|{(datetime.utcnow() + timedelta(hours=1)).isoformat()}"
        SystemConfig.set_config(token_key, token_data, "密码重置令牌")
        
        # 发送邮件
        try:
            send_reset_email(user.email, user.username, reset_token)
            flash('重置密码链接已发送到您的邮箱，请查收', 'success')
        except Exception as e:
            flash(f'邮件发送失败：{str(e)}', 'error')
            return render_template('auth/forgot_password.html')
        
        return redirect(url_for('auth.login'))
    
    return render_template('auth/forgot_password.html')

@auth_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # 验证令牌
    user = None
    for config in SystemConfig.query.filter(SystemConfig.key.like('reset_token_%')).all():
        try:
            stored_token, expires_str = config.value.split('|')
            expires_at = datetime.fromisoformat(expires_str)
            
            if stored_token == token and datetime.utcnow() < expires_at:
                user_id = int(config.key.replace('reset_token_', ''))
                user = User.query.get(user_id)
                break
        except:
            continue
    
    if not user:
        flash('重置链接无效或已过期', 'error')
        return redirect(url_for('auth.forgot_password'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not password or not confirm_password:
            flash('请填写所有字段', 'error')
            return render_template('auth/reset_password.html')
        
        if password != confirm_password:
            flash('两次输入的密码不一致', 'error')
            return render_template('auth/reset_password.html')
        
        if len(password) < 6:
            flash('密码长度至少6位', 'error')
            return render_template('auth/reset_password.html')
        
        # 更新密码
        user.set_password(password)
        db.session.commit()
        
        # 删除重置令牌
        token_key = f"reset_token_{user.id}"
        config = SystemConfig.query.filter_by(key=token_key).first()
        if config:
            db.session.delete(config)
            db.session.commit()
        
        flash('密码重置成功，请使用新密码登录', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/reset_password.html')

def send_reset_email(email, username, token):
    """发送密码重置邮件"""
    site_name = SystemConfig.get_config('site_name', 'AI数据分析预测系统')

    # 构建重置链接
    reset_url = url_for('auth.reset_password', token=token, _external=True)
    
    # 邮件内容
    subject = f'{site_name} - 密码重置'
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #667eea;">密码重置请求</h2>
            <p>亲爱的 {username}，</p>
            <p>您请求重置 {site_name} 的密码。请点击下面的链接来重置您的密码：</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{reset_url}" 
                   style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                          color: white; 
                          padding: 12px 30px; 
                          text-decoration: none; 
                          border-radius: 25px; 
                          display: inline-block;">
                    重置密码
                </a>
            </div>
            <p>如果按钮无法点击，请复制以下链接到浏览器地址栏：</p>
            <p style="word-break: break-all; background: #f5f5f5; padding: 10px; border-radius: 5px;">
                {reset_url}
            </p>
            <p style="color: #666; font-size: 14px;">
                此链接将在1小时后过期。如果您没有请求重置密码，请忽略此邮件。
            </p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
            <p style="color: #999; font-size: 12px; text-align: center;">
                {site_name} 自动发送，请勿回复此邮件
            </p>
        </div>
    </body>
    </html>
    """
    
    _send_html_email(email, subject, html_body)
