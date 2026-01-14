# -*- coding: utf-8 -*-
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
    auto_prediction_enabled = db.Column(db.Boolean, default=False)  # 是否启用自动预测
    auto_prediction_strategies = db.Column(db.String(100), default='balanced')  # 自动预测策略，多个策略用逗号分隔
    auto_prediction_regions = db.Column(db.String(20), default='hk,macau')  # 自动预测地区，多个地区用逗号分隔

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
    
    def check_and_update_activation_status(self):
        """检查并更新激活状态，如果过期则设为未激活"""
        if self.is_activation_expired():
            self.is_active = False
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
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # 预测准确率相关字段
    actual_normal_numbers = db.Column(db.String(50))  # 实际开奖正码
    actual_special_number = db.Column(db.String(10))  # 实际开奖特码
    actual_special_zodiac = db.Column(db.String(10))  # 实际开奖特码生肖
    accuracy_score = db.Column(db.Float)  # 准确率分数(0-1)
    is_result_updated = db.Column(db.Boolean, default=False)  # 是否已更新开奖结果

    def __repr__(self):
        return f'<PredictionRecord {self.region}-{self.period}>'

class InviteCode(db.Model):
    """邀请码模型"""
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
    value = db.Column(db.Text)
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

        ZodiacSetting._macau_zodiac_cache[year] = number_to_zodiac
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
        try:
            year = int(year)
        except (TypeError, ValueError):
            return {}

        cached = ZodiacSetting._macau_year_match_cache.get(year)
        if cached is not None:
            return cached
        mapping = ZodiacSetting._get_macau_zodiac_mapping(year)
        if len(mapping) < 49:
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

class ManualBetRecord(db.Model):
    __tablename__ = 'manual_bet_records'

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
    __table_args__ = (db.UniqueConstraint('region', 'draw_id', name='uix_region_draw_id'),)
    
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
            
            # 尝试从ZodiacSetting获取生肖设置
            zodiac_settings = ZodiacSetting.get_all_settings_for_year(current_year)
            
            # 如果有生肖设置，使用设置的生肖
            if zodiac_settings:
                # 更新特码生肖
                if special_number:
                    try:
                        special_number_int = int(special_number)
                        special_zodiac = zodiac_settings.get(special_number_int, draw_data.get('sno_zodiac', ''))
                    except (ValueError, TypeError):
                        special_zodiac = draw_data.get('sno_zodiac', '')
                else:
                    special_zodiac = draw_data.get('sno_zodiac', '')
                
                # 更新所有号码的生肖
                raw_zodiacs = []
                for num in all_numbers:
                    try:
                        num_int = int(num)
                        zodiac = zodiac_settings.get(num_int, '')
                        raw_zodiacs.append(zodiac)
                    except (ValueError, TypeError):
                        raw_zodiacs.append('')
                
                raw_zodiac = ','.join(raw_zodiacs)
            else:
                # 如果没有设置，使用原始数据
                special_zodiac = draw_data.get('sno_zodiac', '')
                raw_zodiac = draw_data.get('raw_zodiac', '')
            
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






















