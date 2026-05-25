from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, session
from sqlalchemy import desc

from models import ActivationCode, ActivationCodeRequest, User, db


activation_code_bp = Blueprint('activation_code', __name__, url_prefix='/admin/activation_codes')
VALIDITY_TYPES = {'permanent', 'day', 'month', 'quarter', 'year'}


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
            return jsonify({'success': False, 'message': '需要管理员权限'}), 403
        return f(*args, **kwargs)

    return decorated_function


def _validity_label(value):
    labels = {
        'permanent': '永久',
        'day': '1天',
        'month': '1个月',
        'quarter': '3个月',
        'year': '1年',
    }
    return labels.get(value, value or '未知')


@activation_code_bp.route('/generate', methods=['POST'])
@admin_required
def generate_activation_codes():
    try:
        data = request.get_json(silent=True) or {}
        count = int(data.get('count', 1))
        validity_type = str(data.get('validity_type', 'permanent') or 'permanent').strip()
        if validity_type not in VALIDITY_TYPES:
            return jsonify({'success': False, 'message': '无效的激活码类型'})

        if count < 1 or count > 100:
            return jsonify({'success': False, 'message': '生成数量必须在 1 到 100 之间'})

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
            'codes': generated_codes,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'生成失败：{str(e)}'})


@activation_code_bp.route('/list', methods=['GET'])
@admin_required
def list_activation_codes():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)

        query = ActivationCode.query.order_by(desc(ActivationCode.created_at))
        total = query.count()
        codes = query.limit(per_page).offset((page - 1) * per_page).all()

        result = []
        for code in codes:
            user = User.query.filter_by(username=code.used_by).first() if code.used_by else None
            result.append({
                'id': code.id,
                'code': code.code,
                'is_used': code.is_used,
                'used_by': code.used_by,
                'created_at': code.created_at.strftime('%Y-%m-%d %H:%M:%S') if code.created_at else None,
                'used_at': code.used_at.strftime('%Y-%m-%d %H:%M:%S') if code.used_at else None,
                'validity_type': code.validity_type,
                'validity_label': _validity_label(code.validity_type),
                'expires_at': code.expires_at.strftime('%Y-%m-%d %H:%M:%S') if code.expires_at else None,
                'is_expired': code.is_expired(),
                'user_email': user.email if user else None,
            })

        return jsonify({
            'success': True,
            'data': result,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page,
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取激活码列表失败：{str(e)}'})


@activation_code_bp.route('/requests', methods=['GET'])
@admin_required
def list_activation_code_requests():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        status = str(request.args.get('status', '') or '').strip()
        days = request.args.get('days', type=int)

        query = ActivationCodeRequest.query.order_by(
            ActivationCodeRequest.status.asc(),
            ActivationCodeRequest.created_at.desc(),
        )
        if status == 'history':
            query = query.filter(ActivationCodeRequest.status != 'pending')
        elif status:
            query = query.filter_by(status=status)

        if days and days > 0:
            cutoff = datetime.now() - timedelta(days=days)
            query = query.filter(ActivationCodeRequest.created_at >= cutoff)

        total = query.count()
        rows = query.limit(per_page).offset((page - 1) * per_page).all()

        return jsonify({
            'success': True,
            'data': [item.to_dict() for item in rows],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page,
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取申请记录失败：{str(e)}'})


@activation_code_bp.route('/requests/<int:request_id>/issue', methods=['POST'])
@admin_required
def issue_activation_code_request(request_id):
    try:
        payload = request.get_json(silent=True) or {}
        validity_type = str(payload.get('validity_type', 'month') or 'month').strip()
        if validity_type not in VALIDITY_TYPES:
            return jsonify({'success': False, 'message': '无效的发放类型'})
        admin_note = str(payload.get('admin_note', '') or '').strip()

        request_record = ActivationCodeRequest.query.get_or_404(request_id)
        if request_record.status not in {'pending', 'rejected'}:
            return jsonify({'success': False, 'message': '该申请当前不能再次发放'})

        user = User.query.get(request_record.user_id)
        if not user:
            return jsonify({'success': False, 'message': '申请用户不存在'})

        code = ActivationCode()
        code.code = ActivationCode.generate_code()
        code.set_validity(validity_type)
        db.session.add(code)

        request_record.status = 'issued'
        request_record.admin_note = admin_note
        request_record.issued_code = code.code
        request_record.issued_validity_type = validity_type
        request_record.processed_at = datetime.now()

        success, message = code.use_code(user)
        if not success:
            db.session.rollback()
            return jsonify({'success': False, 'message': message})

        request_record.status = 'used'

        db.session.commit()
        return jsonify({
            'success': True,
            'message': '激活码发放成功，用户已自动激活',
            'code': code.code,
            'validity_label': _validity_label(validity_type),
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'发放失败：{str(e)}'})


@activation_code_bp.route('/requests/<int:request_id>/reject', methods=['POST'])
@admin_required
def reject_activation_code_request(request_id):
    try:
        payload = request.get_json(silent=True) or {}
        admin_note = str(payload.get('admin_note', '') or '').strip()

        request_record = ActivationCodeRequest.query.get_or_404(request_id)
        if request_record.status not in {'pending', 'issued'}:
            return jsonify({'success': False, 'message': '该申请当前不能驳回'})

        request_record.status = 'rejected'
        request_record.admin_note = admin_note
        request_record.processed_at = datetime.now()
        db.session.commit()
        return jsonify({'success': True, 'message': '申请已驳回'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'驳回失败：{str(e)}'})


@activation_code_bp.route('/<code>/delete', methods=['DELETE'])
@admin_required
def delete_activation_code(code):
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
