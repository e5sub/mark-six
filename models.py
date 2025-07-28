from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import uuid
import hashlib

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    activation_expires_at = db.Column(db.DateTime)  # 激活到期时间
    
    # 邀请相关字段
    invited_by = db.Column(db.String(80))  # 邀请人用户名
    invite_code_used = db.Column(db.String(32))  # 使用的邀请码
    invite_activated_at = db.Column(db.DateTime)  # 邀请激活时间

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
        return datetime.utcnow() > self.activation_expires_at
    
    def extend_activation(self, days):
        """延长激活有效期"""
        try:
            if hasattr(self, 'activation_expires_at') and self.activation_expires_at:
                # 如果已有有效期，在现有基础上延长
                self.activation_expires_at += timedelta(days=days)
            else:
                # 如果没有有效期，从当前时间开始计算
                self.activation_expires_at = datetime.utcnow() + timedelta(days=days)
        except Exception as e:
            print(f"延长激活有效期时出错: {e}")
            # 如果出错，至少设置一个基本的有效期
            self.activation_expires_at = datetime.utcnow() + timedelta(days=days)
    
    def set_permanent_activation(self):
        """设置永久激活"""
        self.activation_expires_at = None
        self.is_active = True
    
    def check_and_update_activation_status(self):
        """检查并更新激活状态，如果过期则设为未激活"""
        if self.is_activation_expired():
            self.is_active = False
            return False  # 已过期
        return True  # 仍然有效

    def __repr__(self):
        return f'<User {self.username}>'

class ActivationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), unique=True, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    used_by = db.Column(db.String(80))  # 存储用户名而不是ID
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
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
            self.expires_at = datetime.utcnow() + timedelta(days=1)
        elif validity_type == 'month':
            self.expires_at = datetime.utcnow() + timedelta(days=30)
        elif validity_type == 'quarter':
            self.expires_at = datetime.utcnow() + timedelta(days=90)
        elif validity_type == 'year':
            self.expires_at = datetime.utcnow() + timedelta(days=365)
        else:  # permanent
            self.expires_at = None

    def is_expired(self):
        """检查激活码是否过期"""
        if not self.expires_at:
            return False
        return datetime.utcnow() > self.expires_at

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
        self.used_at = datetime.utcnow()
        
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
        
        return True, "激活成功"

    def __repr__(self):
        return f'<ActivationCode {self.code}>'

class PredictionRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    region = db.Column(db.String(10), nullable=False)  # 'hk' 或 'macau'
    strategy = db.Column(db.String(20), nullable=False)  # 'random', 'balanced', 'ai'
    period = db.Column(db.String(20), nullable=False)  # 期数
    normal_numbers = db.Column(db.String(50), nullable=False)  # 正码，逗号分隔
    special_number = db.Column(db.String(10), nullable=False)  # 特码
    special_zodiac = db.Column(db.String(10))  # 特码生肖
    prediction_text = db.Column(db.Text)  # AI预测文本
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 预测准确率相关字段
    actual_normal_numbers = db.Column(db.String(50))  # 实际开奖正码
    actual_special_number = db.Column(db.String(10))  # 实际开奖特码
    accuracy_score = db.Column(db.Float)  # 准确率分数 (0-1)
    is_result_updated = db.Column(db.Boolean, default=False)  # 是否已更新开奖结果

    def __repr__(self):
        return f'<PredictionRecord {self.region}-{self.period}>'

class InviteCode(db.Model):
    """邀请码模型"""
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    created_by = db.Column(db.String(80), nullable=False)  # 创建者用户名
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
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
        
        if self.created_by == user.username:
            return False, "不能使用自己创建的邀请码"
        
        # 检查用户是否已经使用过邀请码
        if hasattr(user, 'invite_code_used') and user.invite_code_used:
            return False, "您已经使用过邀请码，每个用户只能使用一次"
        
        try:
            # 标记邀请码为已使用
            self.is_used = True
            self.used_by = user.username
            self.used_at = datetime.utcnow()
            
            # 更新被邀请人信息（这些字段在User模型中已定义）
            user.invited_by = self.created_by
            user.invite_code_used = self.code
            user.invite_activated_at = datetime.utcnow()
            
            # 给被邀请人增加1天有效期并激活
            user.extend_activation(1)
            user.is_active = True
            
            # 查找邀请人并给予奖励
            inviter = User.query.filter_by(username=self.created_by).first()
            if inviter:
                # 给邀请人增加1天有效期
                inviter.extend_activation(1)
                
                # 如果被邀请人有有效期且邀请人不是永久用户，给予额外奖励
                if user.activation_expires_at and inviter.activation_expires_at:
                    # 计算被邀请人的剩余有效期天数
                    remaining_days = (user.activation_expires_at - datetime.utcnow()).days
                    if remaining_days > 0:
                        bonus_days = max(1, remaining_days // 2)  # 至少1天
                        inviter.extend_activation(bonus_days)
            
            return True, "邀请码使用成功，您和邀请人都获得了1天有效期"
            
        except Exception as e:
            db.session.rollback()
            return False, f"使用邀请码时出错: {str(e)}"
    
    def __repr__(self):
        return f'<InviteCode {self.code}>'

class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get_config(key, default_value=''):
        """获取配置值"""
        config = SystemConfig.query.filter_by(key=key).first()
        return config.value if config else default_value

    @staticmethod
    def set_config(key, value, description=''):
        """设置配置值"""
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
