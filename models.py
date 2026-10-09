# -*- coding: utf-8 -*-
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from sqlalchemy.dialects.mysql import MEDIUMTEXT
import uuid
import hashlib
import json

db = SQLAlchemy()
LargeText = db.Text().with_variant(MEDIUMTEXT(), 'mysql').with_variant(MEDIUMTEXT(), 'mariadb')

class User(db.Model):
    __table_args__ = (
        db.Index('ix_user_created_at', 'created_at'),
        db.Index('ix_user_activation_expires_at', 'activation_expires_at'),
        db.Index('ix_user_invited_by_created_at', 'invited_by', 'created_at'),
        db.Index('ix_user_active_auto_prediction', 'is_active', 'auto_prediction_enabled'),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    github_id = db.Column(db.String(64), unique=True)
    github_username = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    activation_expires_at = db.Column(db.DateTime)  # 激活到期时间
    
    # 登录相关字段
    last_login = db.Column(db.DateTime)  # 最后登录时间
    login_count = db.Column(db.Integer, default=0)  # 登录次数
    
    # 邀请相关字段
    invited_by = db.Column(db.String(80))  # 邀请人用户名
    invite_code_used = db.Column(db.String(32))  # 使用的邀请码
    invite_activated_at = db.Column(db.DateTime)  # 邀请激活时间
    
    # 自动预测相关字段
    auto_prediction_enabled = db.Column(db.Boolean, default=True)  # 是否启用自动预测
    auto_prediction_strategies = db.Column(db.String(100), default='hot,cold,trend,hybrid,balanced,markov,ml')  # 自动预测策略，多个策略用逗号分隔
    auto_prediction_regions = db.Column(db.String(20), default='hk,macau')  # 自动预测地区，多个地区用逗号分隔
    show_normal_numbers = db.Column(db.Boolean, default=False)  # 预测展示时是否显示平码

    def set_password(self, password):
        """设置密码"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """检查密码"""
        return check_password_hash(self.password_hash, password)
    
    def is_activation_expired(self):
        """检查激活是否过期"""
        if not self.activation_expires_at:
            return False  # 永久激活
        return datetime.now() > self.activation_expires_at
    
    def extend_activation(self, days):
        """延长激活有效期"""
        try:
            if hasattr(self, 'activation_expires_at') and self.activation_expires_at:
                # 如果已有有效期，在现有基础上延长
                self.activation_expires_at += timedelta(days=days)
            else:
                # 如果没有有效期，从当前时间开始计算
                self.activation_expires_at = datetime.now() + timedelta(days=days)
        except Exception as e:
            print(f"延长激活有效期时出错 {e}")
            # 如果出错，至少设置一个基本的有效期
            self.activation_expires_at = datetime.now() + timedelta(days=days)
    
    def set_permanent_activation(self):
        """设置永久激活"""
        self.activation_expires_at = None
        self.is_active = True
        self.auto_prediction_enabled = True
    
    def check_and_update_activation_status(self):
        """检查并更新激活状态，如果过期则设为未激活"""
        if self.is_activation_expired():
            changed = False
            if self.is_active or self.auto_prediction_enabled:
                self.is_active = False
                self.auto_prediction_enabled = False
                changed = True
            if changed:
                db.session.commit()
                ZodiacSetting._macau_year_match_cache.clear()
            return False  # 已过期
        return True  # 仍然有效

    def __repr__(self):
        return f'<User {self.username}>'

class ActivationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), unique=True, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    used_by = db.Column(db.String(80))  # 存储用户名而不是ID
    created_at = db.Column(db.DateTime, default=datetime.now)
    used_at = db.Column(db.DateTime)
    validity_type = db.Column(db.String(20), default='permanent')  # permanent, day, month, quarter, year
    expires_at = db.Column(db.DateTime)  # 激活码本身的过期时间

    @staticmethod
    def generate_code():
        """生成激活码"""
        return str(uuid.uuid4()).replace('-', '').upper()[:16]

    def set_validity(self, validity_type):
        """设置激活码有效期"""
        self.validity_type = validity_type
        if validity_type == 'day':
            self.expires_at = datetime.now() + timedelta(days=1)
        elif validity_type == 'month':
            self.expires_at = datetime.now() + timedelta(days=30)
        elif validity_type == 'quarter':
            self.expires_at = datetime.now() + timedelta(days=90)
        elif validity_type == 'year':
            self.expires_at = datetime.now() + timedelta(days=365)
        else:  # permanent
            self.expires_at = None

    def is_expired(self):
        """检查激活码是否过期"""
        if not self.expires_at:
            return False
        return datetime.now() > self.expires_at

    def use_code(self, user):
        """使用激活码"""
        if self.is_used or self.is_expired():
            if self.is_used:
                return False, "激活码已被使用"
            else:
                return False, "激活码已过期"
        
        # 标记激活码为已使用
        self.is_used = True
        self.used_by = user.username
        self.used_at = datetime.now()
        
        # 根据激活码类型延长用户激活时间
        if self.validity_type == 'permanent':
            user.set_permanent_activation()
        else:
            days_map = {
                'day': 1,
                'month': 30,
                'quarter': 90,
                'year': 365
            }
            days = days_map.get(self.validity_type, 0)
            if days > 0:
                user.extend_activation(days)
                user.is_active = True
                user.auto_prediction_enabled = True

        try:
            related_requests = ActivationCodeRequest.query.filter_by(
                user_id=user.id,
                issued_code=self.code
            ).all()
            for item in related_requests:
                item.status = 'used'
                item.processed_at = datetime.now()
        except Exception:
            pass
        
        return True, "激活成功"

    def __repr__(self):
        return f'<ActivationCode {self.code}>'


class ActivationCodeRequest(db.Model):
    __table_args__ = (
        db.Index('ix_activation_code_request_user_status', 'user_id', 'status'),
        db.Index('ix_activation_code_request_user_created_at', 'user_id', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    request_note = db.Column(db.String(255))
    status = db.Column(db.String(20), default='pending')  # pending, issued, rejected, used
    admin_note = db.Column(db.String(255))
    issued_code = db.Column(db.String(64))
    issued_validity_type = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.now)
    processed_at = db.Column(db.DateTime)

    @property
    def status_label(self):
        status_labels = {
            'pending': '待处理',
            'issued': '已发放',
            'rejected': '已驳回',
            'used': '已使用',
        }
        return status_labels.get(self.status, self.status or '待处理')

    @property
    def issued_validity_label(self):
        validity_labels = {
            'permanent': '永久',
            'day': '1天',
            'month': '1个月',
            'quarter': '3个月',
            'year': '1年',
        }
        return validity_labels.get(self.issued_validity_type, self.issued_validity_type or '')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'email': self.email,
            'request_note': self.request_note or '',
            'status': self.status or 'pending',
            'status_label': self.status_label,
            'admin_note': self.admin_note or '',
            'issued_code': self.issued_code or '',
            'issued_validity_type': self.issued_validity_type or '',
            'issued_validity_label': self.issued_validity_label,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else '',
            'processed_at': self.processed_at.strftime('%Y-%m-%d %H:%M:%S') if self.processed_at else '',
        }

    def __repr__(self):
        return f'<ActivationCodeRequest {self.username}-{self.status}>'

# 未开启差异化预测时，共享预测记录统一挂在这个哨兵 user_id 下
# （不指向任何真实用户，配合 uq_prediction_record_user_region_period_strategy
# 唯一约束，保证同一地区同一期同一策略全局只有一条共享记录）
SHARED_PREDICTION_USER_ID = -1

# 随机基线（49 选 6 + 1 特码），用于展示命中率时的对照口径：
# 特码 1/49≈2.0%，六码 6/49≈12.2%，生肖约 1/12≈8.3%，七码 7/49≈14.3%
PREDICTION_HIT_BASELINES = {
    'top1': 2.0,
    'top6': 12.2,
    'zodiac': 8.3,
    'coverage': 14.3,
}


def personalized_predictions_enabled():
    """差异化预测是否开启（与 app.py 中同名函数保持一致）"""
    raw = str(SystemConfig.get_config('enable_personalized_predictions', 'false')).strip().lower()
    return raw in {'true', '1', 'yes', 'on'}


def prediction_scope_user_ids(user_id):
    """该用户视角可见的预测记录 user_id 列表。

    差异化关闭时共享记录属于所有用户，查询需把哨兵 id 一并纳入；
    开启时各用户记录彼此独立，只查自己。
    """
    if personalized_predictions_enabled():
        return [user_id]
    return [user_id, SHARED_PREDICTION_USER_ID]


def build_coverage_recommendation(predictions):
    """把同一期多个策略的预测号码合并去重，给出“综合覆盖”号码与覆盖概率。

    覆盖概率 = 覆盖号码数 / 49：特码是 49 个号码里均匀的一个，
    所以覆盖 k 个号码时“特码落在其中”的概率正好是 k/49。
    """
    votes = {}
    for prediction in predictions or ():
        numbers = set()
        for value in str(getattr(prediction, 'normal_numbers', '') or '').split(','):
            value = value.strip()
            if value.isdigit():
                numbers.add(int(value))
        special = str(getattr(prediction, 'special_number', '') or '').strip()
        if special.isdigit():
            numbers.add(int(special))
        for number in numbers:
            votes[number] = votes.get(number, 0) + 1

    if not votes:
        return None

    items = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
    unique_count = len(items)
    return {
        'numbers': [{'number': number, 'votes': count} for number, count in items],
        'unique_count': unique_count,
        'picks_total': sum(votes.values()),
        'consensus_numbers': [number for number, count in items if count >= 2],
        'coverage_probability': round(unique_count / 49 * 100, 1),
        'seven_number_baseline': PREDICTION_HIT_BASELINES['coverage'],
    }


class PredictionRecord(db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'region',
            'period',
            'strategy',
            name='uq_prediction_record_user_region_period_strategy'
        ),
        db.Index(
            'ix_prediction_record_user_strategy_created_at',
            'user_id',
            'strategy',
            'created_at',
        ),
        db.Index(
            'ix_prediction_record_user_strategy_region_period',
            'user_id',
            'strategy',
            'region',
            'period',
        ),
        db.Index(
            'ix_prediction_record_user_created_at',
            'user_id',
            'created_at',
        ),
        db.Index(
            'ix_prediction_record_region_created_at',
            'region',
            'created_at',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    region = db.Column(db.String(10), nullable=False)  # 'hk' 或 'macau'
    strategy = db.Column(db.String(20), nullable=False)  # 'balanced', 'ml', 'ai'
    period = db.Column(db.String(20), nullable=False)  # 期数
    normal_numbers = db.Column(db.String(50), nullable=False)  # 正码，逗号分隔
    special_number = db.Column(db.String(10), nullable=False)  # 特码
    special_zodiac = db.Column(db.String(10))  # 特码生肖
    prediction_text = db.Column(LargeText)  # AI预测文本
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # 预测准确率相关字段
    actual_normal_numbers = db.Column(db.String(50))  # 实际开奖正码
    actual_special_number = db.Column(db.String(10))  # 实际开奖特码
    actual_special_zodiac = db.Column(db.String(10))  # 实际开奖特码生肖
    accuracy_score = db.Column(db.Float)  # 准确率分数(0-1)
    is_result_updated = db.Column(db.Boolean, default=False)  # 是否已更新开奖结果

    prediction_metadata = db.Column(LargeText)

    def __repr__(self):
        return f'<PredictionRecord {self.region}-{self.period}>'


class BacktestRun(db.Model):
    __tablename__ = 'backtest_runs'
    __table_args__ = (
        db.Index('ix_backtest_runs_region_name', 'region', 'name'),
        db.Index('ix_backtest_runs_region_created_at', 'region', 'created_at'),
        db.Index('ix_backtest_runs_created_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    region = db.Column(db.String(10))
    strategies = db.Column(db.String(255))
    periods_evaluated = db.Column(db.Integer, default=0)
    payload = db.Column(LargeText)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<BacktestRun {self.id}:{self.name}>'

class InviteCode(db.Model):
    """邀请码模型"""
    __table_args__ = (
        db.Index('ix_invite_code_created_by_used_created_at', 'created_by', 'is_used', 'created_at'),
        db.Index('ix_invite_code_created_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    created_by = db.Column(db.String(80), nullable=False)  # 创建者用户名
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_used = db.Column(db.Boolean, default=False)
    used_by = db.Column(db.String(80))  # 使用者用户名
    used_at = db.Column(db.DateTime)
    expires_at = db.Column(db.DateTime)  # 邀请码过期时间
    
    @staticmethod
    def generate_code():
        """生成邀请码"""
        return str(uuid.uuid4()).replace('-', '').upper()[:12]
    
    def is_expired(self):
        """检查邀请码是否过期"""
        if not self.expires_at:
            return False
        return datetime.utcnow() > self.expires_at
    
    def use_invite_code(self, user):
        """使用邀请码进行邀请注册"""
        if self.is_used:
            return False, "邀请码已被使用"
        
        if self.is_expired():
            return False, "邀请码已过期"
        
        # 注册时不需要检查是否是自己的邀请码，因为新用户不可能创建邀请码
        # 只有在已有账号的用户使用邀请码时才需要检查
        # if self.created_by == user.username:
        #     return False, "不能使用自己创建的邀请码"
        
        # 检查用户是否已经使用过邀请码
        if hasattr(user, 'invite_code_used') and user.invite_code_used:
            return False, "您已经使用过邀请码，每个用户只能使用一次"
        
        try:
            # 标记邀请码为已使用
            self.is_used = True
            self.used_by = user.username
            self.used_at = datetime.now()
            
            # 更新被邀请人信息（这些字段在User模型中已定义
            user.invited_by = self.created_by
            user.invite_code_used = self.code
            user.invite_activated_at = datetime.now()
            
            # 给被邀请人增加1天有效期并激活
            user.extend_activation(1)
            user.is_active = True
            user.auto_prediction_enabled = True
            
            try:
                # 查找邀请人并给予奖励
                inviter = User.query.filter_by(username=self.created_by).first()
                if inviter:
                    # 检查邀请人是否是永久用户
                    if inviter.activation_expires_at is None:
                        # 永久用户保持永久状态，不做任何改变
                        pass
                    else:
                        # 非永久用户，给邀请人增加1天有效期
                        inviter.extend_activation(1)
                        inviter.is_active = True
                        inviter.auto_prediction_enabled = True
                        
                        # 如果被邀请人有有效期，给予额外奖励
                        if user.activation_expires_at:
                            # 计算被邀请人的剩余有效期天数
                            try:
                                remaining_days = (user.activation_expires_at - datetime.now()).days
                                if remaining_days > 0:
                                    bonus_days = max(1, remaining_days // 2)  # 至少1天
                                    inviter.extend_activation(bonus_days)
                            except Exception as e:
                                print(f"计算额外奖励天数时出错 {e}")
                                # 出错时至少给邀请人1天奖励
                                inviter.extend_activation(1)
            except Exception as e:
                print(f"处理邀请人奖励时出错 {e}")
                # 即使处理邀请人奖励出错，也不影响被邀请人的注册
            
            return True, "邀请码使用成功，您和邀请人都获得了1天有效期"
            
        except Exception as e:
            db.session.rollback()
            return False, f"使用邀请码时出错 {str(e)}"
    
    def __repr__(self):
        return f'<InviteCode {self.code}>'

class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(LargeText)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    @staticmethod
    def get_config(key, default_value=''):
        """获取配置项"""
        config = SystemConfig.query.filter_by(key=key).first()
        return config.value if config else default_value

    @staticmethod
    def set_config(key, value, description=''):
        """设置配置项"""
        config = SystemConfig.query.filter_by(key=key).first()
        if config:
            config.value = value
            if description:
                config.description = description
        else:
            config = SystemConfig(key=key, value=value, description=description)
            db.session.add(config)
        db.session.commit()

    def __repr__(self):
        return f'<SystemConfig {self.key}>'


class UserNotification(db.Model):
    __tablename__ = 'user_notification'
    __table_args__ = (
        db.Index('ix_user_notification_user_created_at', 'user_id', 'created_at'),
        db.Index('ix_user_notification_user_read', 'user_id', 'is_read'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_type = db.Column(db.String(50), default='general')
    title = db.Column(db.String(160), nullable=False)
    content = db.Column(LargeText)
    link_url = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    read_at = db.Column(db.DateTime)

    user = db.relationship('User', backref=db.backref('notifications', lazy='dynamic'))

    def mark_read(self):
        self.is_read = True
        self.read_at = datetime.now()

    def __repr__(self):
        return f'<UserNotification {self.user_id}:{self.title}>'

class ZodiacSetting(db.Model):
    """生肖号码设置模型"""
    __tablename__ = 'zodiac_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)  # 年份
    zodiac = db.Column(db.String(10), nullable=False)  # 生肖
    numbers = db.Column(db.String(100), nullable=False)  # 号码组，逗号分隔
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 创建联合唯一索引，确保每个年份的每个生肖只有一条记录
    __table_args__ = (db.UniqueConstraint('year', 'zodiac', name='uix_year_zodiac'),)
    _MACAU_API_URL_TEMPLATE = "https://api.macaumarksix.com/history/macaujc2/y/{year}"
    _ZODIAC_TRAD_TO_SIMP = {
        '鼠': '鼠', '牛': '牛', '虎': '虎', '兔': '兔', '龍': '龙', '蛇': '蛇',
        '馬': '马', '羊': '羊', '猴': '猴', '雞': '鸡', '狗': '狗', '豬': '猪'
    }
    _macau_zodiac_cache = {}
    _macau_year_match_cache = {}
    _LUNAR_NEW_YEAR_DATES = {
        2020: datetime(2020, 1, 25).date(),
        2021: datetime(2021, 2, 12).date(),
        2022: datetime(2022, 2, 1).date(),
        2023: datetime(2023, 1, 22).date(),
        2024: datetime(2024, 2, 10).date(),
        2025: datetime(2025, 1, 29).date(),
        2026: datetime(2026, 2, 17).date(),
        2027: datetime(2027, 2, 6).date(),
        2028: datetime(2028, 1, 26).date(),
        2029: datetime(2029, 2, 13).date(),
        2030: datetime(2030, 2, 3).date(),
        2031: datetime(2031, 1, 23).date(),
        2032: datetime(2032, 2, 11).date(),
        2033: datetime(2033, 1, 31).date(),
        2034: datetime(2034, 2, 19).date(),
        2035: datetime(2035, 2, 8).date(),
    }

    @staticmethod
    def get_zodiac_year_for_date(value):
        if value is None:
            return datetime.now().year

        dt = None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(value.strip(), fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                try:
                    dt = datetime.fromisoformat(value.strip())
                except ValueError:
                    dt = None

        if dt is None:
            return datetime.now().year

        try:
            from lunardate import LunarDate
        except Exception:
            LunarDate = None

        if LunarDate is not None:
            try:
                lunar = LunarDate.fromSolarDate(dt.year, dt.month, dt.day)
                return lunar.year
            except Exception:
                pass

        base_year = dt.year
        lunar_new_year = ZodiacSetting._LUNAR_NEW_YEAR_DATES.get(base_year)
        if lunar_new_year:
            return base_year - 1 if dt.date() < lunar_new_year else base_year

        # fallback: use Feb 4 as rough boundary when no table entry
        if (dt.month, dt.day) < (2, 4):
            return base_year - 1
        return base_year

    @staticmethod
    def _load_cached_macau_mapping(year):
        """从持久化缓存表读取澳门生肖映射（只读数据库，不发外网请求）"""
        try:
            row = ZodiacMappingCache.query.filter_by(year=int(year)).first()
            if not row or not row.mapping_json:
                return {}
            data = json.loads(row.mapping_json)
            mapping = {}
            for key, value in (data or {}).items():
                try:
                    mapping[int(key)] = value
                except (TypeError, ValueError):
                    continue
            return mapping
        except Exception as e:
            print(f"读取澳门生肖映射缓存失败: {e}")
            return {}

    @staticmethod
    def _save_cached_macau_mapping(year, mapping):
        """把澳门生肖映射写入持久化缓存表"""
        try:
            year = int(year)
            payload = json.dumps({str(k): v for k, v in (mapping or {}).items()}, ensure_ascii=False)
            row = ZodiacMappingCache.query.filter_by(year=year).first()
            if row is None:
                row = ZodiacMappingCache(year=year, mapping_json=payload, source='macau_api')
                db.session.add(row)
            else:
                row.mapping_json = payload
                row.source = 'macau_api'
                row.fetched_at = datetime.now()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"写入澳门生肖映射缓存失败: {e}")

    @staticmethod
    def refresh_macau_zodiac_mapping(year=None):
        """显式刷新澳门生肖映射（会访问外网）。

        仅供后台任务调用（启动预热、每日采集任务）。成功后会同步更新
        内存缓存与持久化缓存表，Web 渲染路径只读缓存、永不发外网请求。
        """
        try:
            year = int(year) if year is not None else ZodiacSetting.get_zodiac_year_for_date(datetime.now())
        except (TypeError, ValueError):
            year = datetime.now().year
        ZodiacSetting._macau_zodiac_cache.pop(year, None)
        ZodiacSetting._macau_year_match_cache.pop(year, None)
        return ZodiacSetting._get_macau_zodiac_mapping(year)

    @staticmethod
    def _get_macau_zodiac_mapping(year):
        try:
            year = int(year)
        except (TypeError, ValueError):
            return {}

        cached = ZodiacSetting._macau_zodiac_cache.get(year)
        if cached:
            return cached

        try:
            import requests
            url = ZodiacSetting._MACAU_API_URL_TEMPLATE.format(year=year)
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            api_data = response.json()
            records = api_data.get("data") or []
        except Exception as e:
            print(f"Failed to fetch Macau zodiac mapping: {e}")
            return {}

        number_to_zodiac = {}
        for record in records:
            raw_numbers = str(record.get("openCode", "")).split(',')
            raw_zodiacs = str(record.get("zodiac", "")).split(',')
            if len(raw_numbers) < 7 or len(raw_zodiacs) < 7:
                continue

            numbers = []
            for value in raw_numbers[:7]:
                try:
                    numbers.append(int(value))
                except (TypeError, ValueError):
                    numbers = []
                    break
            if not numbers:
                continue

            zodiacs = [
                ZodiacSetting._ZODIAC_TRAD_TO_SIMP.get(zodiac, zodiac)
                for zodiac in raw_zodiacs[:7]
            ]

            for num, zodiac in zip(numbers, zodiacs):
                if num not in number_to_zodiac and zodiac:
                    number_to_zodiac[num] = zodiac

            if len(number_to_zodiac) >= 49:
                break

        if number_to_zodiac:
            ZodiacSetting._macau_zodiac_cache[year] = number_to_zodiac
            ZodiacSetting._save_cached_macau_mapping(year, number_to_zodiac)
        return number_to_zodiac

    @staticmethod
    def _get_settings_mapping_for_year(year):
        settings = ZodiacSetting.query.filter_by(year=year).all()
        if not settings:
            return None

        mapping = {}
        for setting in settings:
            try:
                numbers = [int(n) for n in setting.numbers.split(',') if n.strip()]
            except ValueError:
                continue
            for number in numbers:
                mapping[number] = setting.zodiac
        return mapping

    @staticmethod
    def get_mapping_for_macau_year(year):
        """获取澳门年份对应的号码->生肖映射（只读缓存，不访问外网）。

        依次尝试：内存缓存 -> 持久化缓存表 -> 与本地生肖设置匹配；
        全部不可用时返回空映射，由调用方回退到默认规则。
        """
        try:
            year = int(year)
        except (TypeError, ValueError):
            return {}

        cached = ZodiacSetting._macau_year_match_cache.get(year)
        if cached is not None:
            return cached

        mapping = ZodiacSetting._macau_zodiac_cache.get(year)
        if not mapping:
            mapping = ZodiacSetting._load_cached_macau_mapping(year)
            if mapping:
                ZodiacSetting._macau_zodiac_cache[year] = mapping

        if len(mapping) < 49:
            # 空映射不写缓存，等后台任务补齐持久化缓存后可自动生效
            if mapping:
                ZodiacSetting._macau_year_match_cache[year] = mapping
            return mapping

        years = [row[0] for row in db.session.query(ZodiacSetting.year).distinct().all()]
        for candidate_year in years:
            settings_mapping = ZodiacSetting._get_settings_mapping_for_year(candidate_year)
            if not settings_mapping or len(settings_mapping) < 49:
                continue
            if all(mapping.get(number) == zodiac for number, zodiac in settings_mapping.items()):
                print(f"Matched Macau zodiac mapping to settings year {candidate_year} for Macau year {year}")
                ZodiacSetting._macau_year_match_cache[year] = settings_mapping
                return settings_mapping

        ZodiacSetting._macau_year_match_cache[year] = mapping
        return mapping
    @staticmethod
    def get_zodiac_for_number(year, number):
        """获取指定年份指定号码的生肖"""
        try:
            number = int(number)
            settings = ZodiacSetting.query.filter_by(year=year).all()
            for setting in settings:
                numbers = [int(n) for n in setting.numbers.split(',') if n.strip()]
                if number in numbers:
                    return setting.zodiac
            
            # 如果没有找到设置，使用默认规则
            return ZodiacSetting.get_default_zodiac_for_number(number, year)
        except Exception as e:
            print(f"获取生肖设置失败: {e}")
            return ZodiacSetting.get_default_zodiac_for_number(number, year)
    
    @staticmethod
    def get_all_settings_for_year(year):
        """获取指定年份的所有生肖设置，返回号码到生肖的映射"""
        try:
            settings = ZodiacSetting.query.filter_by(year=year).all()
            number_to_zodiac = {}
            
            # 如果数据库中有设置，使用数据库设置
            if settings:
                for setting in settings:
                    zodiac = setting.zodiac
                    numbers = [int(n) for n in setting.numbers.split(',') if n.strip()]
                    for number in numbers:
                        number_to_zodiac[number] = zodiac
            else:
                # 如果数据库中没有设置，使用默认规则生肖
                for number in range(1, 50):
                    zodiac = ZodiacSetting.get_default_zodiac_for_number(number, year)
                    if zodiac:
                        number_to_zodiac[number] = zodiac
            
            return number_to_zodiac
        except Exception as e:
            print(f"获取年份生肖设置失败: {e}")
            # 出错时使用默认规则
            number_to_zodiac = {}
            for number in range(1, 50):
                zodiac = ZodiacSetting.get_default_zodiac_for_number(number, year)
                if zodiac:
                    number_to_zodiac[number] = zodiac
            return number_to_zodiac
    
    @staticmethod
    def get_zodiac_settings(year):
        """获取指定年份的所有生肖设置，返回生肖到号码组的映射"""
        try:
            settings = ZodiacSetting.query.filter_by(year=year).all()
            
            # 如果数据库中有设置，使用数据库设置
            if settings:
                return {setting.zodiac: setting.numbers for setting in settings}
            else:
                # 如果数据库中没有设置，使用默认规则生肖
                default_settings = {}
                for zodiac in ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]:
                    default_settings[zodiac] = []
                
                for number in range(1, 50):
                    zodiac = ZodiacSetting.get_default_zodiac_for_number(number, year)
                    if zodiac and zodiac in default_settings:
                        default_settings[zodiac].append(str(number))
                
                # 将号码列表转换为逗号分隔的字符串
                for zodiac, numbers in default_settings.items():
                    default_settings[zodiac] = ','.join(numbers)
                
                return default_settings
        except Exception as e:
            print(f"获取生肖设置失败: {e}")
            return {}
    
    @staticmethod
    def batch_update_settings(year, settings_data):
        """批量更新生肖设置
        settings_data格式: {zodiac: numbers_str, ...}
        """
        try:
            # 先删除该年份的所有设置
            ZodiacSetting.query.filter_by(year=year).delete()
            
            # 添加新的设置
            for zodiac, numbers_str in settings_data.items():
                # 验证号码格式
                numbers = []
                for num_str in numbers_str.split(','):
                    try:
                        num = int(num_str.strip())
                        if 1 <= num <= 49:
                            numbers.append(str(num))
                    except ValueError:
                        continue
                
                if numbers:  # 只有当有有效号码时才添加设置
                    new_setting = ZodiacSetting(
                        year=year,
                        zodiac=zodiac,
                        numbers=','.join(numbers)
                    )
                    db.session.add(new_setting)
            
            db.session.commit()
            ZodiacSetting._macau_year_match_cache.clear()
            return True, "生肖设置更新成功"
        except Exception as e:
            db.session.rollback()
            return False, f"更新生肖设置失败: {str(e)}"
    
    @staticmethod
    def get_default_zodiac_for_number(number, year=None):
        """使用默认规则获取号码对应的生肖"""
        if year is None:
            year = datetime.now().year

        try:
            number = int(number)
        except (ValueError, TypeError):
            return None

        mapping = ZodiacSetting.get_mapping_for_macau_year(year)
        if mapping:
            zodiac = mapping.get(number)
            if zodiac:
                return zodiac
            
        # 基础生肖顺序（2025年龙年的顺序）
        base_zodiacs = ["蛇", "龙", "兔", "虎", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马"]
        
        # 计算年份差值（以2025年为基准）
        year_diff = year - 2025
        
        # 计算生肖偏移量（每年农历一月一日，末尾生肖调整到第一个，其他生肖整体后移）
        offset = year_diff % 12
        
        # 调整生肖顺序
        zodiacs = base_zodiacs[:]
        for _ in range(offset):
            # 将最后一个生肖移到第一位，其他生肖整体后移
            zodiacs.insert(0, zodiacs.pop())

        # 固定的号码分组（每个生肖对应4个号码，最后一个生肖只有1个号码）
        if 1 <= number <= 49:
            # Zodiac index: (number - 1) % 12
            zodiac_index = (number - 1) % 12
            return zodiacs[zodiac_index]            
        return None
    
    @staticmethod
    def get_zodiac_table_for_year(year):
        """获取指定年份的生肖号码对照表"""
        # 基础生肖顺序（2025年龙年的顺序）
        base_zodiacs = ["蛇", "龙", "兔", "虎", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马"]
        
        # 计算年份差值（以2025年为基准）
        year_diff = year - 2025
        
        # 计算生肖偏移量（每年农历一月一日，末尾生肖调整到第一个，其他生肖整体后移）
        offset = year_diff % 12
        
        # 调整生肖顺序
        zodiacs = base_zodiacs[:]
        for _ in range(offset):
            # 将最后一个生肖移到第一位，其他生肖整体后移
            zodiacs.insert(0, zodiacs.pop())
        mapping = ZodiacSetting.get_mapping_for_macau_year(year)
        if mapping:
            mapped_zodiacs = [mapping.get(number, "") for number in range(1, 13)]
            if all(mapped_zodiacs):
                zodiacs = mapped_zodiacs

        # 生成对照表
        table = {
            'zodiacs': zodiacs,
            'rows': []
        }
        
        # 生成4行数据，每行12个号码
        for row in range(4):
            row_data = []
            for col in range(12):
                number = row * 12 + col + 1
                if number <= 49:
                    row_data.append(number)
                else:
                    row_data.append(None)
            table['rows'].append(row_data)
        
        # 添加一行（只有49号）
        last_row = [None] * 12
        last_row[0] = 49
        table['rows'].append(last_row)
        
        return table


class ZodiacMappingCache(db.Model):
    """澳门生肖号码映射的持久化缓存表。

    页面渲染等 Web 请求路径只读这里的缓存，绝不直接访问外网；
    外部接口的拉取/刷新由后台任务（启动预热、每日采集任务）负责。
    """
    __tablename__ = 'zodiac_mapping_cache'

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, unique=True)
    mapping_json = db.Column(LargeText, nullable=False)  # {"号码": "生肖"} JSON
    source = db.Column(db.String(32), default='macau_api')
    fetched_at = db.Column(db.DateTime, default=datetime.now)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<ZodiacMappingCache {self.year}: {len(self.mapping_json or "")}b>'

class ManualBetRecord(db.Model):
    __tablename__ = 'manual_bet_records'
    __table_args__ = (
        db.Index('ix_manual_bet_records_user_region_created_at', 'user_id', 'region', 'created_at'),
        db.Index('ix_manual_bet_records_region_period_profit', 'region', 'period', 'total_profit'),
        db.Index(
            'ix_manual_bet_records_user_region_period_profit_created_at',
            'user_id',
            'region',
            'period',
            'total_profit',
            'created_at',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    region = db.Column(db.String(10), nullable=False)
    period = db.Column(db.String(20), nullable=False)
    bettor_name = db.Column(db.String(50))
    selected_numbers = db.Column(db.String(200))
    selected_zodiacs = db.Column(db.String(100))
    selected_colors = db.Column(db.String(50))
    selected_parity = db.Column(db.String(20))
    odds_number = db.Column(db.Float)
    odds_zodiac = db.Column(db.Float)
    odds_color = db.Column(db.Float)
    odds_parity = db.Column(db.Float)
    stake_special = db.Column(db.Float)
    stake_common = db.Column(db.Float)
    result_number = db.Column(db.Boolean)
    result_zodiac = db.Column(db.Boolean)
    result_color = db.Column(db.Boolean)
    result_parity = db.Column(db.Boolean)
    profit_number = db.Column(db.Float)
    profit_zodiac = db.Column(db.Float)
    profit_color = db.Column(db.Float)
    profit_parity = db.Column(db.Float)
    total_profit = db.Column(db.Float)
    total_stake = db.Column(db.Float)
    special_number = db.Column(db.String(10))
    special_zodiac = db.Column(db.String(10))
    special_color = db.Column(db.String(10))
    special_parity = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f'<ManualBetRecord {self.user_id}-{self.region}-{self.period}>'

class LotteryDraw(db.Model):
    """开奖记录模型"""
    __tablename__ = 'lottery_draws'
    
    id = db.Column(db.Integer, primary_key=True)
    region = db.Column(db.String(10), nullable=False)  # 'hk' 或 'macau'
    draw_id = db.Column(db.String(20), nullable=False)  # 期号
    draw_date = db.Column(db.String(20))  # 开奖日期
    normal_numbers = db.Column(db.String(50), nullable=False)  # 正码，逗号分隔
    special_number = db.Column(db.String(10), nullable=False)  # 特码
    special_zodiac = db.Column(db.String(10))  # 特码生肖
    raw_zodiac = db.Column(db.String(100))  # 所有号码的生肖，逗号分隔
    raw_wave = db.Column(db.String(100))  # 波色信息
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 创建联合唯一索引，确保每个地区的每期号码只有一条记录
    __table_args__ = (
        db.UniqueConstraint('region', 'draw_id', name='uix_region_draw_id'),
        db.Index('ix_lottery_draws_region_draw_date_draw_id', 'region', 'draw_date', 'draw_id'),
    )
    
    def to_dict(self):
        """将记录转换为字典，方便API返回"""
        return {
            "id": self.draw_id,
            "date": self.draw_date,
            "no": self.normal_numbers.split(','),
            "sno": self.special_number,
            "sno_zodiac": self.special_zodiac,
            "raw_zodiac": self.raw_zodiac,
            "raw_wave": self.raw_wave
        }
    
    @staticmethod
    def save_draw(region, draw_data):
        """保存开奖记录到数据库"""
        try:
            # 检查记录是否已存在
            existing = LotteryDraw.query.filter_by(
                region=region,
                draw_id=draw_data.get('id')
            ).first()
            
            # 获取生肖年份（按农历新年切换）
            draw_date = draw_data.get('date', '')
            current_year = ZodiacSetting.get_zodiac_year_for_date(draw_date)
            
            # 获取号码列表
            normal_numbers = draw_data.get('no', [])
            special_number = draw_data.get('sno', '')
            all_numbers = normal_numbers + [special_number] if special_number else normal_numbers
            
            # 尝试从ZodiacSetting获取生肖设置；设置覆盖不到的号码
            # 按该农历年（澳门号码生肖规则）兜底，任何情况下都不再透传空值
            zodiac_settings = ZodiacSetting.get_all_settings_for_year(current_year) or {}

            def _fallback_zodiac(num_int):
                try:
                    return ZodiacSetting.get_default_zodiac_for_number(int(num_int), current_year) or ''
                except (TypeError, ValueError):
                    return ''

            # 数据源返回的生肖（可能是空串），作为兜底的第二顺位
            source_zodiacs = str(draw_data.get('raw_zodiac', '') or '').split(',')
            source_special_zodiac = str(draw_data.get('sno_zodiac', '') or '').strip()

            # 特码生肖：设置 > 源数据 > 默认规则
            if special_number:
                try:
                    special_number_int = int(special_number)
                    special_zodiac = (
                        zodiac_settings.get(special_number_int, '')
                        or source_special_zodiac
                        or _fallback_zodiac(special_number_int)
                    )
                except (ValueError, TypeError):
                    special_zodiac = source_special_zodiac
            else:
                special_zodiac = source_special_zodiac

            # 全部号码的生肖：设置 > 源数据对应位 > 默认规则
            raw_zodiacs = []
            for index, num in enumerate(all_numbers):
                try:
                    num_int = int(num)
                except (ValueError, TypeError):
                    raw_zodiacs.append('')
                    continue
                source_z = source_zodiacs[index].strip() if index < len(source_zodiacs) else ''
                zodiac = zodiac_settings.get(num_int, '') or source_z or _fallback_zodiac(num_int)
                raw_zodiacs.append(zodiac)
            
            raw_zodiac = ','.join(raw_zodiacs)
            
            if existing:
                # 更新现有记录
                existing.draw_date = draw_date
                existing.normal_numbers = ','.join(normal_numbers)
                existing.special_number = special_number
                existing.special_zodiac = special_zodiac
                existing.raw_zodiac = raw_zodiac
                existing.raw_wave = draw_data.get('raw_wave', '')
                existing.updated_at = datetime.now()
            else:
                # 创建新记录
                new_draw = LotteryDraw(
                    region=region,
                    draw_id=draw_data.get('id', ''),
                    draw_date=draw_date,
                    normal_numbers=','.join(normal_numbers),
                    special_number=special_number,
                    special_zodiac=special_zodiac,
                    raw_zodiac=raw_zodiac,
                    raw_wave=draw_data.get('raw_wave', '')
                )
                db.session.add(new_draw)
            
            db.session.commit()
            ZodiacSetting._macau_year_match_cache.clear()
            return True
        except Exception as e:
            print(f"保存开奖记录失败 {e}")
            db.session.rollback()
            return False
    
    def __repr__(self):
        return f'<LotteryDraw {self.region}-{self.draw_id}>'


class MacauCollectedData(db.Model):
    __tablename__ = 'macau_collected_data'

    id = db.Column(db.Integer, primary_key=True)
    region = db.Column(db.String(10), nullable=False, default='macau')
    year = db.Column(db.Integer, nullable=False)
    source_period = db.Column(db.String(10), nullable=False)
    period = db.Column(db.String(20), nullable=False)
    numbers = db.Column(db.String(100))
    zodiacs = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('region', 'period', name='uix_macau_collected_region_period'),
        db.Index('ix_macau_collected_region_period', 'region', 'period'),
        db.Index('ix_macau_collected_year_source_period', 'year', 'source_period'),
        db.Index('ix_macau_collected_created_at', 'created_at'),
    )
