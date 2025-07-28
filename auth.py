    return render_template('auth/activate.html')

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
    smtp_server = SystemConfig.get_config('smtp_server')
    smtp_port = int(SystemConfig.get_config('smtp_port', '587'))
    smtp_username = SystemConfig.get_config('smtp_username')
    smtp_password = SystemConfig.get_config('smtp_password')
    site_name = SystemConfig.get_config('site_name', 'AI预测系统')
    
    if not all([smtp_server, smtp_username, smtp_password]):
        raise Exception('邮件服务未配置，请联系管理员')
    
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
    
    # 创建邮件
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_username
    msg['To'] = email
    
    html_part = MIMEText(html_body, 'html', 'utf-8')
    msg.attach(html_part)
    
    # 发送邮件
    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(smtp_username, smtp_password)
    server.send_message(msg)
    server.quit()
