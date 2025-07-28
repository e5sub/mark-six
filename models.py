from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import uuid

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    activation_expires_at = db.Column(db.DateTime)  # 激活到期时间
    
    # 关联预测记录
    predictions = db.relationship('PredictionRecord', backref='user', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def is_activation_expired(self):
        """检查激活是否过期"""
        if not self.activation_expires_at:
            return False  # 永久激活
        return datetime.utcnow() > self.activation_expires_at
    
    def extend_activation(self, days):
        """延长激活时间"""
        if days == 0:  # 永久激活
            self.activation_expires_at = None
        else:
            if self.activation_expires_at and self.activation_expires_at > datetime.utcnow():
                # 如果已有到期时间且未过期，在此基础上延长
                self.activation_expires_at += timedelta(days=days)
            else:
                # 如果没有到期时间或已过期，从现在开始计算
                self.activation_expires_at = datetime.utcnow() + timedelta(days=days)
        
        # 确保用户处于激活状态
        self.is_active = True
    
    def check_and_update_activation_status(self):
        """检查并更新激活状态"""
        if self.is_activation_expired():
            self.is_active = False
            return False
        return True

class ActivationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    used_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used_at = db.Column(db.DateTime, nullable=True)
    # 新增有效期相关字段
    validity_type = db.Column(db.String(10), default='permanent')  # 'day', 'month', 'quarter', 'year', 'permanent'
    validity_days = db.Column(db.Integer, default=0)  # 有效天数
    expires_at = db.Column(db.DateTime, nullable=True)  # 过期时间
    
    @staticmethod
    def generate_code():
        """生成激活码"""
        return str(uuid.uuid4()).replace('-', '')[:16].upper()
    
    def set_validity(self, validity_type='permanent'):
        """设置有效期"""
        if validity_type == 'day':
            self.validity_days = 1
            self.expires_at = datetime.utcnow() + timedelta(days=1)
        elif validity_type == 'month':
            self.validity_days = 30
            self.expires_at = datetime.utcnow() + timedelta(days=30)
        elif validity_type == 'quarter':
            self.validity_days = 90
            self.expires_at = datetime.utcnow() + timedelta(days=90)
        elif validity_type == 'year':
            self.validity_days = 365
            self.expires_at = datetime.utcnow() + timedelta(days=365)
        else:
            self.validity_days = 0
            self.expires_at = None
        
        self.validity_type = validity_type
    
    def is_expired(self):
        """检查是否过期"""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
    
    def is_valid(self):
        """检查激活码是否有效"""
        return not self.is_used and not self.is_expired()
    
    def use_code(self, user_id):
        """使用激活码并延长用户激活时间"""
        if self.is_used:
            return False, "激活码已被使用"
        
        if self.is_expired():
            return False, "激活码已过期"
        
        # 获取用户
        user = User.query.get(user_id)
        if not user:
            return False, "用户不存在"
        
        # 标记激活码为已使用
        self.is_used = True
        self.used_by = user_id
        self.used_at = datetime.utcnow()
        
        # 根据激活码类型延长用户激活时间
        if self.validity_type == 'permanent':
            user.extend_activation(0)  # 永久激活
        elif self.validity_type == 'day':
            user.extend_activation(1)
        elif self.validity_type == 'month':
            user.extend_activation(30)
        elif self.validity_type == 'quarter':
            user.extend_activation(90)
        elif self.validity_type == 'year':
            user.extend_activation(365)
        
        return True, "激活码使用成功，账号激活时间已延长"

class PredictionRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    region = db.Column(db.String(10), nullable=False)  # 'hk' 或 'macau'
    strategy = db.Column(db.String(20), nullable=False)  # 'random', 'balanced', 'ai'
    period = db.Column(db.String(50), nullable=False)  # 期数
    normal_numbers = db.Column(db.String(50), nullable=False)  # 正码，逗号分隔
    special_number = db.Column(db.String(10), nullable=False)  # 特码
    special_zodiac = db.Column(db.String(10), nullable=True)  # 特码生肖
    prediction_text = db.Column(db.Text, nullable=True)  # AI预测文本
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # 开奖结果（用于计算准确率）
    actual_normal_numbers = db.Column(db.String(50), nullable=True)
    actual_special_number = db.Column(db.String(10), nullable=True)
    accuracy_score = db.Column(db.Float, nullable=True)  # 准确率分数
    is_result_updated = db.Column(db.Boolean, default=False)

class SystemConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @staticmethod
    def get_config(key, default=None):
        config = SystemConfig.query.filter_by(key=key).first()
        return config.value if config else default
    
    @staticmethod
    def set_config(key, value, description=None):
        config = SystemConfig.query.filter_by(key=key).first()
        if config:
            config.value = value
            config.updated_at = datetime.utcnow()
            if description:
                config.description = description
        else:
            config = SystemConfig(key=key, value=value, description=description)
            db.session.add(config)
        db.session.commit()
        return config