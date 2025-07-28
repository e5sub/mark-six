    # 检查用户是否登录
        # 检查用户是否登录
        if 'user_id' not in session:
            flash('请先登录', 'error')
            return redirect(url_for('auth.login'))
        
        # 检查用户是否是管理员
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
