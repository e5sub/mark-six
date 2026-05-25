from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, abort
from decimal import Decimal, InvalidOperation
import json
from models import db, User, ActivationCode, ActivationCodeRequest, SystemConfig, InviteCode, PaymentOrder
from werkzeug.security import generate_password_hash
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import secrets
try:
    from alipay_f2f import (
        AlipayConfigError,
        build_client as build_alipay_client,
        build_payment_page_payload,
        precreate_trade,
        query_trade,
        verify_rsa2,
    )
    _ALIPAY_IMPORT_ERROR = ''
except Exception as exc:
    _ALIPAY_IMPORT_ERROR = str(exc)

    class AlipayConfigError(Exception):
        pass

    def build_alipay_client(*args, **kwargs):
        raise RuntimeError(f'支付宝支付依赖未安装: {_ALIPAY_IMPORT_ERROR}')

    def build_payment_page_payload(*args, **kwargs):
        raise RuntimeError(f'支付宝支付依赖未安装: {_ALIPAY_IMPORT_ERROR}')

    def precreate_trade(*args, **kwargs):
        raise RuntimeError(f'支付宝支付依赖未安装: {_ALIPAY_IMPORT_ERROR}')

    def query_trade(*args, **kwargs):
        raise RuntimeError(f'支付宝支付依赖未安装: {_ALIPAY_IMPORT_ERROR}')

    def verify_rsa2(*args, **kwargs):
        raise RuntimeError(f'支付宝支付依赖未安装: {_ALIPAY_IMPORT_ERROR}')

try:
    from wechat_native import (
        WechatPayConfigError,
        build_client as build_wechat_client,
        build_payment_page_payload as build_wechat_payment_page_payload,
        native_prepay as wechat_native_prepay,
        query_by_out_trade_no as wechat_query_by_out_trade_no,
        verify_callback_headers as verify_wechat_callback_headers,
        decrypt_callback_resource as decrypt_wechat_callback_resource,
    )
    _WECHAT_IMPORT_ERROR = ''
except Exception as exc:
    _WECHAT_IMPORT_ERROR = str(exc)

    class WechatPayConfigError(Exception):
        pass

    def build_wechat_client(*args, **kwargs):
        raise RuntimeError(f'微信支付依赖未安装: {_WECHAT_IMPORT_ERROR}')

    def build_wechat_payment_page_payload(*args, **kwargs):
        raise RuntimeError(f'微信支付依赖未安装: {_WECHAT_IMPORT_ERROR}')

    def wechat_native_prepay(*args, **kwargs):
        raise RuntimeError(f'微信支付依赖未安装: {_WECHAT_IMPORT_ERROR}')

    def wechat_query_by_out_trade_no(*args, **kwargs):
        raise RuntimeError(f'微信支付依赖未安装: {_WECHAT_IMPORT_ERROR}')

    def verify_wechat_callback_headers(*args, **kwargs):
        raise RuntimeError(f'微信支付依赖未安装: {_WECHAT_IMPORT_ERROR}')

    def decrypt_wechat_callback_resource(*args, **kwargs):
        raise RuntimeError(f'微信支付依赖未安装: {_WECHAT_IMPORT_ERROR}')

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


def _alipay_enabled():
    return _is_config_enabled('alipay_f2f_enabled', 'false')


def _alipay_runtime_error():
    return _ALIPAY_IMPORT_ERROR


def _wechat_enabled():
    return _is_config_enabled('wechat_native_enabled', 'false')


def _wechat_runtime_error():
    return _WECHAT_IMPORT_ERROR


def _payment_enabled():
    return _alipay_enabled() or _wechat_enabled()


def _alipay_config_map():
    return {
        'gateway': SystemConfig.get_config('alipay_gateway', 'https://openapi.alipay.com/gateway.do'),
        'app_id': SystemConfig.get_config('alipay_app_id', ''),
        'app_private_key': SystemConfig.get_config('alipay_app_private_key', ''),
        'alipay_public_key': SystemConfig.get_config('alipay_public_key', ''),
        'notify_url': SystemConfig.get_config('alipay_notify_url', ''),
        'product_name': SystemConfig.get_config('alipay_activation_product_name', '账号激活服务'),
    }


def _wechat_config_map():
    return {
        'gateway': SystemConfig.get_config('wechat_gateway', 'https://api.mch.weixin.qq.com'),
        'mchid': SystemConfig.get_config('wechat_mchid', ''),
        'appid': SystemConfig.get_config('wechat_appid', ''),
        'private_key': SystemConfig.get_config('wechat_private_key', ''),
        'serial_no': SystemConfig.get_config('wechat_serial_no', ''),
        'api_v3_key': SystemConfig.get_config('wechat_api_v3_key', ''),
        'platform_public_key': SystemConfig.get_config('wechat_platform_public_key', ''),
        'platform_public_key_id': SystemConfig.get_config('wechat_platform_public_key_id', ''),
        'notify_url': SystemConfig.get_config('wechat_notify_url', ''),
        'product_name': SystemConfig.get_config('wechat_activation_product_name', '账号激活服务'),
    }


def _parse_price_config(key, default_value):
    raw = str(SystemConfig.get_config(key, default_value)).strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        value = Decimal(default_value)
    if value < 0:
        value = Decimal(default_value)
    return value.quantize(Decimal('0.01'))


def _activation_packages():
    return [
        {'key': 'day', 'label': '1天', 'price': _parse_price_config('alipay_price_day', '1.00')},
        {'key': 'month', 'label': '1个月', 'price': _parse_price_config('alipay_price_month', '9.90')},
        {'key': 'quarter', 'label': '3个月', 'price': _parse_price_config('alipay_price_quarter', '26.00')},
        {'key': 'year', 'label': '1年', 'price': _parse_price_config('alipay_price_year', '88.00')},
        {'key': 'permanent', 'label': '永久', 'price': _parse_price_config('alipay_price_permanent', '199.00')},
    ]


def _find_activation_package(validity_type):
    for item in _activation_packages():
        if item['key'] == validity_type:
            return item
    return None


def _build_notify_url(channel):
    if channel == 'wechat_native':
        configured = str(SystemConfig.get_config('wechat_notify_url', '')).strip()
        if configured:
            return configured
        return url_for('auth.wechat_notify', _external=True)

    configured = str(SystemConfig.get_config('alipay_notify_url', '')).strip()
    if configured:
        return configured
    return url_for('auth.alipay_notify', _external=True)


def _new_payment_order_no(channel='alipay_f2f'):
    prefix_map = {
        'alipay_f2f': 'ALI',
        'wechat_native': 'WX',
    }
    prefix = prefix_map.get(channel, 'PAY')
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:10].upper()}"


def _payment_order_payload(order):
    channel_map = {
        'alipay_f2f': '支付宝',
        'wechat_native': '微信支付',
    }
    payload = {
        'order_no': order.order_no,
        'channel': order.channel,
        'channel_label': channel_map.get(order.channel, order.channel or ''),
        'status': order.status,
        'amount': order.amount_text,
        'validity_label': order.validity_label,
        'issued_code': order.issued_code or '',
        'paid_at': order.paid_at.strftime('%Y-%m-%d %H:%M:%S') if order.paid_at else '',
    }
    if order.qr_code:
        if order.channel == 'wechat_native':
            payload.update(build_wechat_payment_page_payload(order.qr_code))
        else:
            payload.update(build_payment_page_payload(order.qr_code))
    return payload


def _mark_order_closed(order, status):
    if order.status == 'success':
        return
    order.status = status


def _activate_user_from_paid_order(order, alipay_trade_no='', buyer_logon_id='', notify_payload=None):
    user = User.query.get(order.user_id)
    if not user:
        raise ValueError('订单关联用户不存在')

    code_record = None
    if order.issued_code:
        code_record = ActivationCode.query.filter_by(code=order.issued_code).first()

    if not code_record:
        code_record = ActivationCode(code=ActivationCode.generate_code())
        code_record.set_validity(order.validity_type)
        db.session.add(code_record)
        db.session.flush()
        order.issued_code = code_record.code

    if not code_record.is_used:
        success, message = code_record.use_code(user)
        if not success:
            raise ValueError(message)

    order.status = 'success'
    order.alipay_trade_no = alipay_trade_no or order.alipay_trade_no
    order.buyer_logon_id = buyer_logon_id or order.buyer_logon_id
    order.paid_at = order.paid_at or datetime.now()
    order.activated_at = order.activated_at or datetime.now()
    if notify_payload:
        order.raw_notify_payload = json.dumps(notify_payload, ensure_ascii=False)
    return user


def _refresh_payment_order(order):
    if not order or order.status == 'success':
        return order

    if order.channel == 'wechat_native':
        client = build_wechat_client(_wechat_config_map())
        result = wechat_query_by_out_trade_no(client, order.order_no)
        order.raw_response_payload = json.dumps(result, ensure_ascii=False)
        trade_status = str(result.get('trade_state') or '').strip().upper()

        if trade_status == 'SUCCESS':
            _activate_user_from_paid_order(
                order,
                alipay_trade_no=result.get('transaction_id') or '',
                notify_payload=result,
            )
            db.session.commit()
        elif trade_status in {'NOTPAY', 'USERPAYING'}:
            order.status = 'pending'
            db.session.commit()
        elif trade_status in {'CLOSED', 'PAYERROR', 'REVOKED'}:
            _mark_order_closed(order, 'closed')
            db.session.commit()
        return order

    client = build_alipay_client(_alipay_config_map())
    payload, result = query_trade(client, order.order_no)
    order.raw_response_payload = json.dumps(payload, ensure_ascii=False)

    code = str(result.get('code') or '').strip()
    trade_status = str(result.get('trade_status') or '').strip().upper()

    if code != '10000':
        return order

    if trade_status in {'TRADE_SUCCESS', 'TRADE_FINISHED'}:
        _activate_user_from_paid_order(
            order,
            alipay_trade_no=result.get('trade_no') or '',
            buyer_logon_id=result.get('buyer_logon_id') or '',
            notify_payload=result,
        )
        db.session.commit()
    elif trade_status in {'WAIT_BUYER_PAY'}:
        order.status = 'pending'
        db.session.commit()
    elif trade_status in {'TRADE_CLOSED'}:
        _mark_order_closed(order, 'closed')
        db.session.commit()

    return order


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
        recent_orders = PaymentOrder.query.filter_by(user_id=user.id).order_by(
            PaymentOrder.created_at.desc()
        ).limit(5).all()
        has_pending_request = any(item.status == 'pending' for item in requests)
        payment_packages = []
        for item in _activation_packages():
            package = dict(item)
            package['price_text'] = f"{item['price']:.2f}"
            payment_packages.append(package)
        return render_template(
            'auth/activate.html',
            activation_requests=requests,
            has_pending_request=has_pending_request,
            alipay_enabled=_alipay_enabled(),
            alipay_runtime_error=_alipay_runtime_error(),
            wechat_enabled=_wechat_enabled(),
            wechat_runtime_error=_wechat_runtime_error(),
            payment_enabled=_payment_enabled(),
            payment_packages=payment_packages,
            recent_payment_orders=recent_orders,
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

@auth_bp.route('/alipay/create_activation_order', methods=['POST'])
def create_alipay_activation_order():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'}), 404

    if not _alipay_enabled():
        return jsonify({'success': False, 'message': '支付宝当面付尚未开启'}), 400
    if _alipay_runtime_error():
        return jsonify({'success': False, 'message': f'支付依赖未安装: {_alipay_runtime_error()}'}), 500

    payload = request.get_json(silent=True) or request.form
    validity_type = str(payload.get('validity_type') or '').strip().lower()
    package = _find_activation_package(validity_type)
    if not package:
        return jsonify({'success': False, 'message': '无效的激活套餐'}), 400
    if package['price'] <= 0:
        return jsonify({'success': False, 'message': '当前套餐价格未配置'}), 400

    config_map = _alipay_config_map()
    try:
        client = build_alipay_client(config_map)
    except AlipayConfigError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    subject = f"{config_map.get('product_name', '账号激活服务')} - {package['label']}"
    order = PaymentOrder(
        user_id=user.id,
        order_no=_new_payment_order_no(),
        channel='alipay_f2f',
        purpose='activation',
        subject=subject,
        amount=package['price'],
        validity_type=package['key'],
        status='created',
        expires_at=datetime.now() + timedelta(minutes=15),
    )
    db.session.add(order)
    db.session.flush()

    notify_url = _build_notify_url()
    try:
        raw_payload, result = precreate_trade(
            client=client,
            order_no=order.order_no,
            amount=package['price'],
            subject=subject,
            notify_url=notify_url,
        )
        order.raw_request_payload = json.dumps(
            {'notify_url': notify_url, 'subject': subject, 'amount': f"{package['price']:.2f}"},
            ensure_ascii=False,
        )
        order.raw_response_payload = json.dumps(raw_payload, ensure_ascii=False)
        if str(result.get('code') or '').strip() != '10000' or not result.get('qr_code'):
            order.status = 'failed'
            db.session.commit()
            return jsonify({
                'success': False,
                'message': result.get('sub_msg') or result.get('msg') or '支付宝下单失败',
            }), 400

        order.status = 'pending'
        order.qr_code = result.get('qr_code')
        db.session.commit()
    except Exception as exc:
        order.status = 'failed'
        order.raw_response_payload = json.dumps({'error': str(exc)}, ensure_ascii=False)
        db.session.commit()
        return jsonify({'success': False, 'message': f'创建支付订单失败: {str(exc)}'}), 500

    result_payload = _payment_order_payload(order)
    result_payload.update({
        'success': True,
        'message': '订单创建成功，请使用支付宝扫码支付',
        'subject': order.subject,
    })
    return jsonify(result_payload)


@auth_bp.route('/alipay/order/<order_no>/status')
def alipay_order_status(order_no):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    order = PaymentOrder.query.filter_by(order_no=order_no, user_id=session['user_id']).first()
    if not order:
        return jsonify({'success': False, 'message': '订单不存在'}), 404
    if order.channel != 'alipay_f2f':
        return jsonify({'success': False, 'message': '订单渠道不匹配'}), 400

    if _alipay_enabled() and not _alipay_runtime_error() and order.status in {'created', 'pending'}:
        try:
            order = _refresh_payment_order(order)
        except Exception:
            db.session.rollback()

    current_user = User.query.get(session['user_id'])
    payload = _payment_order_payload(order)
    payload.update({
        'success': True,
        'is_paid': order.status == 'success',
        'is_active': bool(current_user and current_user.is_active),
    })
    return jsonify(payload)


@auth_bp.route('/wechat/create_activation_order', methods=['POST'])
def create_wechat_activation_order():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'}), 404

    if not _wechat_enabled():
        return jsonify({'success': False, 'message': '微信支付尚未开启'}), 400
    if _wechat_runtime_error():
        return jsonify({'success': False, 'message': f'支付依赖未安装: {_wechat_runtime_error()}'}), 500

    payload = request.get_json(silent=True) or request.form
    validity_type = str(payload.get('validity_type') or '').strip().lower()
    package = _find_activation_package(validity_type)
    if not package:
        return jsonify({'success': False, 'message': '无效的激活套餐'}), 400
    if package['price'] <= 0:
        return jsonify({'success': False, 'message': '当前套餐价格未配置'}), 400

    config_map = _wechat_config_map()
    try:
        client = build_wechat_client(config_map)
    except WechatPayConfigError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    subject = f"{config_map.get('product_name', '账号激活服务')} - {package['label']}"
    order = PaymentOrder(
        user_id=user.id,
        order_no=_new_payment_order_no('wechat_native'),
        channel='wechat_native',
        purpose='activation',
        subject=subject,
        amount=package['price'],
        validity_type=package['key'],
        status='created',
        expires_at=datetime.now() + timedelta(minutes=15),
    )
    db.session.add(order)
    db.session.flush()

    notify_url = _build_notify_url('wechat_native')
    amount_fen = int(package['price'] * 100)
    try:
        request_body, result = wechat_native_prepay(
            client=client,
            order_no=order.order_no,
            amount_fen=amount_fen,
            description=subject,
            notify_url=notify_url,
        )
        order.raw_request_payload = json.dumps(request_body, ensure_ascii=False)
        order.raw_response_payload = json.dumps(result, ensure_ascii=False)
        code_url = result.get('code_url')
        if not code_url:
            order.status = 'failed'
            db.session.commit()
            return jsonify({'success': False, 'message': result.get('message') or '微信支付下单失败'}), 400

        order.status = 'pending'
        order.qr_code = code_url
        db.session.commit()
    except Exception as exc:
        order.status = 'failed'
        order.raw_response_payload = json.dumps({'error': str(exc)}, ensure_ascii=False)
        db.session.commit()
        return jsonify({'success': False, 'message': f'创建微信支付订单失败: {str(exc)}'}), 500

    result_payload = _payment_order_payload(order)
    result_payload.update({
        'success': True,
        'message': '订单创建成功，请使用微信扫码支付',
        'subject': order.subject,
    })
    return jsonify(result_payload)


@auth_bp.route('/alipay/notify', methods=['POST'])
def alipay_notify():
    params = {}
    for key in request.form.keys():
        params[key] = request.form.get(key)

    sign = params.get('sign')
    if not sign:
        return 'failure'
    if _alipay_runtime_error():
        return 'failure'

    try:
        client = build_alipay_client(_alipay_config_map())
        verify_rsa2(params, sign, client['alipay_public_key'])
    except Exception:
        return 'failure'

    order_no = str(params.get('out_trade_no') or '').strip()
    if not order_no:
        return 'failure'

    order = PaymentOrder.query.filter_by(order_no=order_no).first()
    if not order:
        return 'failure'

    trade_status = str(params.get('trade_status') or '').strip().upper()
    try:
        if trade_status in {'TRADE_SUCCESS', 'TRADE_FINISHED'}:
            _activate_user_from_paid_order(
                order,
                alipay_trade_no=params.get('trade_no') or '',
                buyer_logon_id=params.get('buyer_logon_id') or '',
                notify_payload=params,
            )
        elif trade_status == 'TRADE_CLOSED':
            _mark_order_closed(order, 'closed')
            order.raw_notify_payload = json.dumps(params, ensure_ascii=False)
        else:
            order.status = 'pending'
            order.raw_notify_payload = json.dumps(params, ensure_ascii=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return 'failure'

    return 'success'


@auth_bp.route('/wechat/order/<order_no>/status')
def wechat_order_status(order_no):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    order = PaymentOrder.query.filter_by(order_no=order_no, user_id=session['user_id']).first()
    if not order:
        return jsonify({'success': False, 'message': '订单不存在'}), 404
    if order.channel != 'wechat_native':
        return jsonify({'success': False, 'message': '订单渠道不匹配'}), 400

    if _wechat_enabled() and not _wechat_runtime_error() and order.status in {'created', 'pending'}:
        try:
            order = _refresh_payment_order(order)
        except Exception:
            db.session.rollback()

    current_user = User.query.get(session['user_id'])
    payload = _payment_order_payload(order)
    payload.update({
        'success': True,
        'is_paid': order.status == 'success',
        'is_active': bool(current_user and current_user.is_active),
    })
    return jsonify(payload)


@auth_bp.route('/wechat/notify', methods=['POST'])
def wechat_notify():
    if _wechat_runtime_error():
        return jsonify({'code': 'FAIL', 'message': 'dependency missing'}), 500

    body_text = request.get_data(as_text=True)
    timestamp = request.headers.get('Wechatpay-Timestamp', '')
    nonce = request.headers.get('Wechatpay-Nonce', '')
    signature = request.headers.get('Wechatpay-Signature', '')
    serial = request.headers.get('Wechatpay-Serial', '')

    try:
        client = build_wechat_client(_wechat_config_map())
        verify_wechat_callback_headers(client, timestamp, nonce, body_text, signature, serial)
        notify_data = json.loads(body_text or '{}')
        resource_data = decrypt_wechat_callback_resource(client['api_v3_key'], notify_data.get('resource') or {})
    except Exception:
        return jsonify({'code': 'FAIL', 'message': 'invalid notify'}), 400

    order_no = str(resource_data.get('out_trade_no') or '').strip()
    if not order_no:
        return jsonify({'code': 'FAIL', 'message': 'missing out_trade_no'}), 400

    order = PaymentOrder.query.filter_by(order_no=order_no).first()
    if not order:
        return jsonify({'code': 'FAIL', 'message': 'order not found'}), 404

    trade_state = str(resource_data.get('trade_state') or '').strip().upper()
    try:
        if trade_state == 'SUCCESS':
            _activate_user_from_paid_order(
                order,
                alipay_trade_no=resource_data.get('transaction_id') or '',
                notify_payload=resource_data,
            )
        elif trade_state in {'CLOSED', 'PAYERROR', 'REVOKED'}:
            _mark_order_closed(order, 'closed')
            order.raw_notify_payload = json.dumps(resource_data, ensure_ascii=False)
        else:
            order.status = 'pending'
            order.raw_notify_payload = json.dumps(resource_data, ensure_ascii=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'code': 'FAIL', 'message': 'process failed'}), 500

    return jsonify({'code': 'SUCCESS', 'message': '成功'})


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
