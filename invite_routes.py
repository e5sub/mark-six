from flask import Blueprint, redirect, url_for, request, flash

invite_bp = Blueprint('invite', __name__)

@invite_bp.route('/<code>')
def invite_with_code(code):
    """处理邀请链接 /invite/CODE 格式"""
    from models import InviteCode
    
    # 验证邀请码是否存在
    invite_code = InviteCode.query.filter_by(code=code).first()
    
    if not invite_code:
        flash('邀请码无效或已过期', 'error')
        return redirect(url_for('auth.register'))
    
    if invite_code.is_used:
        flash('此邀请码已被使用', 'warning')
        return redirect(url_for('auth.register'))
    
    if invite_code.is_expired():
        flash('此邀请码已过期', 'warning')
        return redirect(url_for('auth.register'))
    
    # 重定向到注册页面并带上邀请码参数
    return redirect(url_for('auth.register', invite_code=code))

@invite_bp.route('/code/<code>')
def invite_with_code_alt(code):
    """处理邀请链接 /invite/code/CODE 格式（兼容旧格式）"""
    return invite_with_code(code)
