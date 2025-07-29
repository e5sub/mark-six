from flask import Blueprint, request, jsonify, session, render_template
from models import db, ActivationCode, User
from functools import wraps
from datetime import datetime
from sqlalchemy import desc
from flask import Blueprint, request, jsonify, session, render_template
from models import db, ActivationCode
from functools import wraps
from datetime import datetime
from sqlalchemy import desc
from flask import Blueprint, request, jsonify, session
from models import db, ActivationCode
from functools import wraps

activation_code_bp = Blueprint('activation_code', __name__, url_prefix='/admin/activation_codes')

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            return jsonify({'success': False, 'message': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated_function

@activation_code_bp.route('/generate', methods=['POST'])
@admin_required
def generate_activation_codes():
    """生成激活码"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '无效的数据格式'})
        
        count = int(data.get('count', 1))
        validity_type = data.get('validity_type', 'permanent')
        
        if count < 1 or count > 100:
            return jsonify({'success': False, 'message': '生成数量必须在1-100之间'})
        
        generated_codes = []
        for _ in range(count):
            code = ActivationCode()
            code.code = ActivationCode.generate_code()
            code.set_validity(validity_type)
            db.session.add(code)
            generated_codes.append(code.code)
        
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'成功生成 {count} 个激活码',
            'codes': generated_codes
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'生成失败：{str(e)}'})

@activation_code_bp.route('/list', methods=['GET'])
@admin_required
def list_activation_codes():
    """获取激活码列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        # 查询激活码，按创建时间降序排序
        query = ActivationCode.query.order_by(desc(ActivationCode.created_at))
        
        # 手动分页
        total = query.count()
        codes = query.limit(per_page).offset((page - 1) * per_page).all()
        
        # 格式化结果
        result = []
        for code in codes:
            user = None
            if code.used_by:
                user = User.query.filter_by(username=code.used_by).first()
            
            result.append({
                'id': code.id,
                'code': code.code,
                'is_used': code.is_used,
                'used_by': code.used_by,
                'created_at': code.created_at.strftime('%Y-%m-%d %H:%M:%S') if code.created_at else None,
                'used_at': code.used_at.strftime('%Y-%m-%d %H:%M:%S') if code.used_at else None,
                'validity_type': code.validity_type,
                'expires_at': code.expires_at.strftime('%Y-%m-%d %H:%M:%S') if code.expires_at else None,
                'is_expired': code.is_expired(),
                'user_email': user.email if user else None
            })
        
        return jsonify({
            'success': True,
            'data': result,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取激活码列表失败：{str(e)}'})

@activation_code_bp.route('/<code>/delete', methods=['DELETE'])
@admin_required
def delete_activation_code(code):
    """删除激活码"""
    try:
        activation_code = ActivationCode.query.filter_by(code=code).first()
        if not activation_code:
            return jsonify({'success': False, 'message': '激活码不存在'})
        
        if activation_code.is_used:
            return jsonify({'success': False, 'message': '已使用的激活码不能删除'})
        
        db.session.delete(activation_code)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '激活码删除成功'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'删除失败：{str(e)}'})
