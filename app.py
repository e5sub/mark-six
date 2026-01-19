from flask import Flask, jsonify, render_template, request, session, redirect, url_for, flash
from flask import Response, stream_with_context
from flask_login import LoginManager, current_user
import json
import os
import random
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import Counter
import re
from urllib.parse import quote_plus
from datetime import datetime, timedelta
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED

# 导入用户系统模块
from models import db, User, PredictionRecord, SystemConfig, InviteCode, LotteryDraw, ManualBetRecord
from auth import auth_bp
from admin import admin_bp
from user import user_bp
from activation_code_routes import activation_code_bp
from invite_routes import invite_bp
from api_mobile import mobile_api_bp

# --- 配置信息 ---
app = Flask(__name__)
# 使用环境变量设置密钥，如果不存在则使用随机生成的密钥
import secrets
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# 确保数据目录存在
data_dir = os.path.join(os.getcwd(), 'data')
os.makedirs(data_dir, exist_ok=True)

_startup_log_lock_path = None
_startup_log_lock_acquired = False

def _pid_is_running(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

def _try_acquire_startup_log_lock():
    import tempfile
    global _startup_log_lock_path, _startup_log_lock_acquired
    if _startup_log_lock_acquired:
        return True
    lock_path = os.path.join(tempfile.gettempdir(), "mark-six-startup.log.lock")
    pid = os.getpid()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(pid))
        _startup_log_lock_path = lock_path
        _startup_log_lock_acquired = True
        return True
    except FileExistsError:
        try:
            with open(lock_path, "r") as f:
                existing_pid = int((f.read() or "").strip() or "0")
        except Exception:
            existing_pid = 0
        if existing_pid and _pid_is_running(existing_pid):
            return False
        try:
            os.remove(lock_path)
        except OSError:
            return False
        return _try_acquire_startup_log_lock()

def _release_startup_log_lock():
    global _startup_log_lock_path, _startup_log_lock_acquired
    if not _startup_log_lock_acquired or not _startup_log_lock_path:
        return
    try:
        os.remove(_startup_log_lock_path)
    except OSError:
        pass
    _startup_log_lock_path = None
    _startup_log_lock_acquired = False

def _should_log_startup():
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        return False
    if not _try_acquire_startup_log_lock():
        return False
    import atexit
    atexit.register(_release_startup_log_lock)
    return True

def _build_database_uri(db_path):
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return db_url

    db_type = os.environ.get("DB_TYPE", "sqlite").lower()
    if db_type in ("mysql", "mariadb"):
        host = os.environ.get("DB_HOST", "localhost")
        port = os.environ.get("DB_PORT", "3306")
        name = os.environ.get("DB_NAME", "mark_six")
        user = os.environ.get("DB_USER", "root")
        password = quote_plus(os.environ.get("DB_PASSWORD", ""))
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"

    return f"sqlite:///{db_path}"

def _mask_db_uri(uri):
    return re.sub(r'//([^:/@]+):([^@]+)@', r'//\1:***@', uri)

# 数据库配置
db_path = os.path.join(data_dir, 'lottery_system.db')
app.config['SQLALCHEMY_DATABASE_URI'] = _build_database_uri(db_path)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 只在主进程中打印一次
if _should_log_startup():
    print(f"数据库路径: {db_path}")
    print(f"数据库URI: {_mask_db_uri(app.config['SQLALCHEMY_DATABASE_URI'])}")

# 初始化数据库
# 初始化数据库
db.init_app(app)

# 初始化Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录以访问此页面。'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 注册蓝图
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(admin_bp)
app.register_blueprint(user_bp)
app.register_blueprint(activation_code_bp)
app.register_blueprint(invite_bp, url_prefix='/invite')
app.register_blueprint(mobile_api_bp)

# 获取AI配置的函数
def get_ai_config():
    return {
        'api_key': SystemConfig.get_config('ai_api_key', '你的_AI_API_KEY'),
        'api_url': SystemConfig.get_config('ai_api_url', 'https://api.deepseek.com/v1/chat/completions'),
        'model': SystemConfig.get_config('ai_model', 'gemini-2.0-flash')
    }
# 澳门数据API
# 原始API可能不可访问，使用备用API
# MACAU_API_URL_TEMPLATE = "https://history.macaumarksix.com/history/macaujc2/y/{year}"
MACAU_API_URL_TEMPLATE = "https://api.macaumarksix.com/history/macaujc2/y/{year}"
# 只在主进程中打印一次
if _should_log_startup():
    print(f"澳门API模板: {MACAU_API_URL_TEMPLATE}")
# 香港数据API
HK_DATA_SOURCE_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/icelam/mark-six-data-visualization/master/data/all.json"

# --- 号码属性计算与映射 ---
ZODIAC_MAPPING_SEQUENCE = ("虎", "兔", "龙", "蛇", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马")
RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46]
BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48]
GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]
COLOR_MAP_EN_TO_ZH = {'red': '红', 'blue': '蓝', 'green': '绿'}
ZODIAC_TRAD_TO_SIMP = {'鼠':'鼠','牛':'牛','虎':'虎','兔':'兔','龍':'龙','蛇':'蛇','馬':'马','羊':'羊','猴':'猴','雞':'鸡','狗':'狗','豬':'猪'}

# 此函数已不再使用，保留是为了兼容性
def _get_hk_number_zodiac(number):
    """
    此函数已不再使用，香港数据也应使用澳门接口返回的生肖数据
    保留此函数仅为兼容性考虑
    """
    return ""

def _get_hk_number_color(number):
    try:
        num = int(number)
        if num in RED_BALLS: return 'red'
        if num in BLUE_BALLS: return 'blue'
        if num in GREEN_BALLS: return 'green'
        return ""
    except:
        return ""

def _get_color_zh(number):
    try:
        num = int(number)
    except (TypeError, ValueError):
        return ""
    if num in RED_BALLS:
        return "红"
    if num in BLUE_BALLS:
        return "蓝"
    if num in GREEN_BALLS:
        return "绿"
    return ""

def _parse_csv_list(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(',') if item.strip()]

def _parse_number_stakes_from_string(value):
    stakes = {}
    if not value:
        return stakes
    for chunk in str(value).split(','):
        part = chunk.strip()
        if not part or ':' not in part:
            continue
        num_str, stake_str = part.split(':', 1)
        try:
            number = int(num_str.strip())
            amount = float(stake_str.strip())
        except (TypeError, ValueError):
            continue
        if number > 0 and amount > 0:
            stakes[number] = amount
    return stakes


def _parse_common_stake_entries(value):
    if not value:
        return []
    entries = []
    for part in str(value).split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            continue
        key, amount_text = piece.split(":", 1)
        key = key.strip()
        try:
            amount = float(amount_text.strip())
        except (TypeError, ValueError):
            continue
        if key and amount > 0:
            entries.append((key, amount))
    return entries

def _dedupe_keep_order(values):
    seen = set()
    result = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result

_DRAW_SYNC_INTERVAL = timedelta(minutes=5)
_last_draw_sync_times = {
    'hk': datetime.min,
    'macau': datetime.min
}
_last_sync_window_skip_date = None

def _is_within_sync_window(now):
    if now.hour != 21:
        return False
    return 32 <= now.minute <= 40

def _finalize_ai_result(ai_response):
    normal_numbers, special_number = _extract_ai_numbers(ai_response)
    if not normal_numbers or not special_number:
        return None, "无法从AI回复中提取有效号码"

    normal_numbers = [n for n in normal_numbers if 1 <= n <= 49]
    if len(normal_numbers) < 6:
        return None, "AI生成的平码数量不足"

    try:
        special_num_value = int(special_number)
    except (TypeError, ValueError):
        special_num_value = None
    if special_num_value is not None:
        normal_numbers = [n for n in normal_numbers if n != special_num_value]
    normal_numbers = _dedupe_keep_order(normal_numbers)[:6]

    if not special_number or not (1 <= int(special_number) <= 49):
        return None, "AI生成的特码无效"

    sno_zodiac = ""
    return {
        "recommendation_text": ai_response,
        "normal": normal_numbers,
        "special": {
            "number": special_number,
            "sno_zodiac": sno_zodiac
        }
    }, None

def _extract_ai_numbers(ai_response):
    if not ai_response:
        return None, None

    normalized = (
        str(ai_response)
        .replace("：", ":")
        .replace("，", ",")
        .replace("、", ",")
        .replace("【", "[")
        .replace("】", "]")
        .replace("特碼", "特码")
    )

    list_patterns = [
        r'推荐号码\s*[:：]\s*\[\s*([0-9\s,，]{5,})\s*\]',
        r'号码推荐\s*[:：]\s*\[\s*([0-9\s,，]{5,})\s*\]',
        r'推荐号码\s*[:：]\s*([0-9\s,，]{5,})',
        r'号码推荐\s*[:：]\s*([0-9\s,，]{5,})',
    ]
    special_patterns = [
        r'特?码\s*[:：]\s*\[\s*(\d{1,2})(?:\s*[^\d\]]+)?\s*\]',
        r'特?码\s*[:：]\s*(\d{1,2})(?:\s*[^\d]+)?',
    ]

    normal_numbers = None
    special_number = None

    for pattern in list_patterns:
        matches = list(re.finditer(pattern, normalized, flags=re.IGNORECASE))
        if not matches:
            continue
        match = matches[-1]
        normal_numbers = [int(n) for n in re.findall(r'\d{1,2}', match.group(1))]
        break

    for pattern in special_patterns:
        matches = list(re.finditer(pattern, normalized, flags=re.IGNORECASE))
        if not matches:
            continue
        special_number = matches[-1].group(1)
        break

    if normal_numbers and special_number:
        return normal_numbers, special_number

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    candidate_lines = [
        line for line in lines
        if any(k in line for k in ("推荐号码", "号码推荐", "特码"))
    ]

    for line in candidate_lines:
        if "特码" not in line:
            continue
        parts = re.split(r'特码', line, maxsplit=1)
        normal_numbers = [int(n) for n in re.findall(r'\d{1,2}', parts[0])]
        special_candidates = re.findall(r'\d{1,2}', parts[1]) if len(parts) > 1 else []
        if len(normal_numbers) >= 6 and special_candidates:
            return normal_numbers[:6], special_candidates[0]

    scoped_numbers = []
    for line in candidate_lines:
        scoped_numbers.extend(re.findall(r'\d{1,2}', line))
    valid_numbers = [int(n) for n in scoped_numbers if 1 <= int(n) <= 49]
    if len(valid_numbers) >= 7:
        return valid_numbers[:6], str(valid_numbers[6])

    return None, None

def _settle_manual_bet_record(record, draw):
    raw_zodiacs = _parse_csv_list(draw.raw_zodiac)
    special_zodiac = draw.special_zodiac or ""
    if raw_zodiacs:
        special_zodiac = raw_zodiacs[-1] or special_zodiac

    special_number = draw.special_number or ""
    special_color = _get_color_zh(special_number)
    special_parity = ""
    try:
        special_parity = "双" if int(special_number) % 2 == 0 else "单"
    except (TypeError, ValueError):
        special_parity = ""

    number_stakes = _parse_number_stakes_from_string(record.selected_numbers)
    if number_stakes:
        selected_numbers = list(number_stakes.keys())
    else:
        selected_numbers = [int(n) for n in _parse_csv_list(record.selected_numbers) if n.isdigit()]
    zodiac_entries = _parse_common_stake_entries(record.selected_zodiacs)
    color_entries = _parse_common_stake_entries(record.selected_colors)
    parity_entries = _parse_common_stake_entries(record.selected_parity)
    selected_zodiacs = (
        [value for value, _ in zodiac_entries]
        if zodiac_entries
        else _parse_csv_list(record.selected_zodiacs)
    )
    selected_colors = (
        [value for value, _ in color_entries]
        if color_entries
        else _parse_csv_list(record.selected_colors)
    )
    selected_parity = (
        [value for value, _ in parity_entries]
        if parity_entries
        else _parse_csv_list(record.selected_parity)
    )

    stake_special = record.stake_special or 0
    stake_common = record.stake_common or 0
    odds_number = record.odds_number or 0
    odds_zodiac = record.odds_zodiac or 0
    odds_color = record.odds_color or 0
    odds_parity = record.odds_parity or 0

    result_number = None
    result_zodiac = None
    result_color = None
    result_parity = None
    profit_number = None
    profit_zodiac = None
    profit_color = None
    profit_parity = None
    total_profit = 0

    if selected_numbers:
        result_number = special_number.isdigit() and int(special_number) in selected_numbers
        if number_stakes:
            hit_stake = number_stakes.get(int(special_number), 0) if special_number.isdigit() else 0
            total_stake_number = sum(number_stakes.values())
            profit_number = hit_stake * odds_number - total_stake_number
            total_profit += profit_number
        else:
            profit_number = (
                stake_special * odds_number - stake_special
                if result_number
                else -stake_special
            )
            total_profit += profit_number

    if selected_zodiacs:
        if zodiac_entries:
            result_zodiac = any(value == special_zodiac for value, _ in zodiac_entries)
            profit_zodiac = 0
            for value, amount in zodiac_entries:
                if value == special_zodiac:
                    profit_zodiac += amount * odds_zodiac - amount
                else:
                    profit_zodiac += -amount
        else:
            result_zodiac = special_zodiac in selected_zodiacs
            profit_zodiac = (
                stake_common * odds_zodiac - stake_common
                if result_zodiac
                else -stake_common
            )
        total_profit += profit_zodiac

    if selected_colors:
        if color_entries:
            result_color = any(value == special_color for value, _ in color_entries)
            profit_color = 0
            for value, amount in color_entries:
                if value == special_color:
                    profit_color += amount * odds_color - amount
                else:
                    profit_color += -amount
        else:
            result_color = special_color in selected_colors
            profit_color = (
                stake_common * odds_color - stake_common
                if result_color
                else -stake_common
            )
        total_profit += profit_color

    if selected_parity:
        if parity_entries:
            result_parity = any(value == special_parity for value, _ in parity_entries)
            profit_parity = 0
            for value, amount in parity_entries:
                if value == special_parity:
                    profit_parity += amount * odds_parity - amount
                else:
                    profit_parity += -amount
        else:
            result_parity = special_parity in selected_parity
            profit_parity = (
                stake_common * odds_parity - stake_common
                if result_parity
                else -stake_common
            )
        total_profit += profit_parity

    record.result_number = result_number
    record.result_zodiac = result_zodiac
    record.result_color = result_color
    record.result_parity = result_parity
    record.profit_number = profit_number
    record.profit_zodiac = profit_zodiac
    record.profit_color = profit_color
    record.profit_parity = profit_parity
    record.total_profit = total_profit
    if number_stakes and record.total_stake is None:
        total_stake_number = sum(number_stakes.values())
        extra_common = 0
        if zodiac_entries:
            extra_common += sum(amount for _, amount in zodiac_entries)
        elif selected_zodiacs:
            extra_common += stake_common
        if color_entries:
            extra_common += sum(amount for _, amount in color_entries)
        elif selected_colors:
            extra_common += stake_common
        if parity_entries:
            extra_common += sum(amount for _, amount in parity_entries)
        elif selected_parity:
            extra_common += stake_common
        record.total_stake = total_stake_number + extra_common
    record.special_number = special_number
    record.special_zodiac = special_zodiac
    record.special_color = special_color
    record.special_parity = special_parity

def settle_pending_manual_bets(region, draw_id):
    if not draw_id:
        return 0
    draw = LotteryDraw.query.filter_by(region=region, draw_id=draw_id).first()
    if not draw:
        return 0
    pending_records = ManualBetRecord.query.filter_by(
        region=region, period=draw_id
    ).filter(ManualBetRecord.total_profit.is_(None)).all()
    if not pending_records:
        return 0
    for record in pending_records:
        _settle_manual_bet_record(record, draw)
    db.session.commit()
    return len(pending_records)

# --- 数据加载与处理 ---
def load_hk_data(force_refresh=False):
    # 直接从URL获取数据
    try:
        params = {"_": int(time.time())} if force_refresh else None
        response = requests.get(HK_DATA_SOURCE_URL, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"从URL获取香港数据失败: {e}")
        return []

def _fetch_macau_data_from_api(year):
    url = MACAU_API_URL_TEMPLATE.format(year=year)
    try:
        print(f"正在获取澳门数据，URL: {url}")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        api_data = response.json()
        if not api_data or not api_data.get("data"): 
            print(f"澳门API返回空数据或格式错误: {api_data}")
            return []
        
        print(f"澳门API返回数据条数: {len(api_data['data'])}")
        
        normalized_data = []
        for record in api_data["data"]:
            raw_numbers_str = record.get("openCode", "").split(',')
            try:
                numbers = [str(int(n)) for n in raw_numbers_str]
            except (ValueError, TypeError):
                continue
            traditional_zodiacs = record.get("zodiac", "").split(',')
            if len(numbers) < 7: continue

            simplified_zodiacs = [ZODIAC_TRAD_TO_SIMP.get(z, z) for z in traditional_zodiacs]
            
            normalized_data.append({
                "id": record.get("expect"), "date": record.get("openTime"), "no": numbers[:6], "sno": numbers[6],
                "sno_zodiac": simplified_zodiacs[6] if len(simplified_zodiacs) >= 7 else "",
                "raw_wave": record.get("wave", ""), "raw_zodiac": ",".join(simplified_zodiacs)
            })
        
        print(f"标准化后的数据条数: {len(normalized_data)}")
        
        # --- 新增去重逻辑 ---
        unique_data = []
        seen_ids = set()
        for record in normalized_data:
            record_id = record.get("id")
            if record_id and record_id not in seen_ids:
                unique_data.append(record)
                seen_ids.add(record_id)
        # --- 去重逻辑结束 ---
        
        print(f"去重后的数据条数: {len(unique_data)}")

        # 使用去重后的 unique_data 进行过滤和排序
        filtered_by_year = [rec for rec in unique_data if rec.get("date", "").startswith(str(year))]
        print(f"按年份过滤后的数据条数: {len(filtered_by_year)}")
        
        result = sorted(filtered_by_year, key=lambda x: (x.get('date', ''), x.get('id', '')), reverse=True)
        print(f"最终返回的数据条数: {len(result)}")
        
        if len(result) > 0:
            print(f"示例数据: {result[0]}")
        
        return result
    except Exception as e:
        print(f"Error in get_macau_data for year {year}: {e}")
        return []

def get_macau_data(year, force_api=False):
    if not force_api:
        try:
            query = LotteryDraw.query.filter_by(region='macau')
            if year != 'all':
                query = query.filter(LotteryDraw.draw_date.like(f"{year}%"))
            db_records = query.order_by(LotteryDraw.draw_date.desc()).all()
            if db_records:
                print(f"从数据库获取到{len(db_records)}条澳门{year}年数据")
                return [record.to_dict() for record in db_records]
        except Exception as e:
            print(f"从数据库获取澳门数据失败: {e}")

    return _fetch_macau_data_from_api(year)

def analyze_special_number_frequency(data):
    special_numbers = []
    for r in data:
        if r.get('sno'):
            special_numbers.append(r.get('sno'))
    counts = Counter(special_numbers)
    return {str(i): counts.get(str(i), 0) for i in range(1, 50)}

def _clamp(value, low, high):
    return max(low, min(high, value))

def _strategy_config_key(region, strategy):
    return f"strategy_config_{region}_{strategy}"

def _default_strategy_config(strategy):
    defaults = {
        "hot": {"window": 50, "pool": 16, "last_accuracy": 0.0, "last_total": 0},
        "cold": {"window": 50, "pool": 16, "last_accuracy": 0.0, "last_total": 0},
        "trend": {"window": 15, "pool": 18, "last_accuracy": 0.0, "last_total": 0},
        "balanced": {"window": 60, "pool": 16, "bucket_counts": [2, 2, 2], "last_accuracy": 0.0, "last_total": 0},
        "hybrid": {
            "window": 50,
            "pool": 16,
            "trend_window": 15,
            "mix": {"hot": 2, "cold": 2, "trend": 2},
            "last_accuracy": 0.0,
            "last_total": 0
        },
    }
    return defaults.get(strategy, {})

def _load_strategy_config(strategy, region):
    key = _strategy_config_key(region, strategy)
    raw = SystemConfig.get_config(key, "")
    stored = {}
    if raw:
        try:
            stored = json.loads(raw)
        except Exception:
            stored = {}
    default = _default_strategy_config(strategy)
    merged = {**default, **stored}
    if "updated_at" not in merged:
        merged["updated_at"] = datetime.now().isoformat()
    return merged

def _save_strategy_config(strategy, region, config):
    key = _strategy_config_key(region, strategy)
    payload = json.dumps(config, ensure_ascii=True)
    SystemConfig.set_config(key, payload, f"Auto-tuned config for {strategy} ({region})")

def _calculate_strategy_accuracy(region, strategy, limit=200):
    query = PredictionRecord.query.filter_by(
        region=region,
        strategy=strategy,
        is_result_updated=True
    ).filter(PredictionRecord.actual_special_number != None)
    query = query.order_by(PredictionRecord.created_at.desc())
    if limit:
        query = query.limit(limit)
    predictions = query.all()
    if not predictions:
        return 0.0, 0

    correct = 0
    for pred in predictions:
        actual = pred.actual_special_number
        if not actual:
            continue
        if pred.special_number == actual:
            correct += 1
            continue
        if pred.normal_numbers:
            normal_numbers = [n.strip() for n in pred.normal_numbers.split(',') if n.strip()]
            if actual in normal_numbers:
                correct += 1
    total = len(predictions)
    return (correct / total) if total else 0.0, total

def _tune_strategy_config(strategy, region):
    accuracy, total = _calculate_strategy_accuracy(region, strategy)
    config = _load_strategy_config(strategy, region)

    config["last_accuracy"] = round(accuracy, 4)
    config["last_total"] = total
    config["updated_at"] = datetime.now().isoformat()

    if total <= 0:
        _save_strategy_config(strategy, region, config)
        return

    if strategy in ("hot", "cold"):
        config["window"] = _clamp(int(30 + accuracy * 40), 20, 80)
        config["pool"] = _clamp(int(12 + accuracy * 10), 10, 24)
    elif strategy == "trend":
        config["window"] = _clamp(int(8 + accuracy * 20), 8, 30)
        config["pool"] = _clamp(int(10 + accuracy * 8), 8, 20)
    elif strategy == "balanced":
        high_count = _clamp(int(2 + accuracy * 2), 1, 4)
        low_count = _clamp(int(2 + (1 - accuracy) * 2), 1, 4)
        mid_count = 6 - high_count - low_count
        if mid_count < 1:
            mid_count = 1
            if high_count >= low_count:
                high_count = 6 - low_count - mid_count
            else:
                low_count = 6 - high_count - mid_count
        config["bucket_counts"] = [low_count, mid_count, high_count]
        config["window"] = _clamp(int(40 + accuracy * 40), 30, 90)
        config["pool"] = _clamp(int(12 + accuracy * 10), 10, 24)
    elif strategy == "hybrid":
        hot_count = _clamp(int(2 + accuracy * 2), 1, 4)
        cold_count = _clamp(int(2 + (1 - accuracy) * 2), 1, 4)
        trend_count = 6 - hot_count - cold_count
        if trend_count < 1:
            trend_count = 1
            if hot_count >= cold_count:
                hot_count = 6 - cold_count - trend_count
            else:
                cold_count = 6 - hot_count - trend_count
        config["mix"] = {"hot": hot_count, "cold": cold_count, "trend": trend_count}
        config["window"] = _clamp(int(40 + accuracy * 40), 30, 90)
        config["pool"] = _clamp(int(12 + accuracy * 10), 10, 24)
        config["trend_window"] = _clamp(int(8 + accuracy * 20), 8, 30)

    _save_strategy_config(strategy, region, config)

def update_strategy_configs(region):
    strategies = ["hot", "cold", "trend", "balanced", "hybrid"]
    for strategy in strategies:
        try:
            _tune_strategy_config(strategy, region)
        except Exception as e:
            print(f"Strategy tuning failed for {strategy} ({region}): {e}")

def _get_number_to_zodiac_map(year):
    number_to_zodiac = {}
    try:
        from models import ZodiacSetting
        mapping = ZodiacSetting.get_mapping_for_macau_year(year)
        if mapping:
            number_to_zodiac = {str(number): zodiac for number, zodiac in mapping.items()}
    except Exception as e:
        print(f"Failed to build zodiac mapping: {e}")

    if not number_to_zodiac:
        macau_data = get_macau_data(str(year))
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        number_to_zodiac[num] = zodiacs[i]

    return number_to_zodiac

def _get_next_period(region, latest_period):
    if region == 'hk':
        if latest_period and '/' in latest_period:
            parts = latest_period.split('/')
            if len(parts) == 2:
                year_part, num_part = parts
                try:
                    next_num = int(num_part) + 1
                    year_num = int(year_part)
                    year_width = max(2, len(year_part))
                    if next_num > 120:
                        next_year = year_num + 1
                        return f"{str(next_year).zfill(year_width)}/001"
                    return f"{year_part}/{next_num:03d}"
                except (ValueError, TypeError):
                    pass
        current_year = datetime.now().strftime('%y')
        return f"{current_year}/001"

    if latest_period and latest_period.isdigit():
        if len(latest_period) >= 7 and latest_period[:4].isdigit():
            year_part = latest_period[:4]
            seq_part = latest_period[4:]
            if seq_part.isdigit():
                seq = int(seq_part)
                next_seq = seq + 1
                if len(seq_part) == 3 and next_seq > 999:
                    next_year = int(year_part) + 1
                    return f"{next_year}001"
                return f"{year_part}{str(next_seq).zfill(len(seq_part))}"
        return str(int(latest_period) + 1)
    return datetime.now().strftime('%Y%m%d')

def analyze_special_zodiac_frequency(data, region, year=None):
    zodiacs = []
    if year is None:
        year = datetime.now().year

    number_to_zodiac = {}
    if region == 'hk':
        number_to_zodiac = _get_number_to_zodiac_map(year)
    for r in data:
        sno = r.get('sno')
        if not sno: continue
        if region == 'hk':
            zodiacs.append(number_to_zodiac.get(str(sno), r.get('sno_zodiac')))
        else:
            zodiacs.append(r.get('sno_zodiac'))
    return Counter(z for z in zodiacs if z)

def analyze_special_color_frequency(data, region):
    colors = []
    for r in data:
        sno = r.get('sno')
        if not sno: continue
        if region == 'hk':
            color_en = _get_hk_number_color(sno)
            colors.append(COLOR_MAP_EN_TO_ZH.get(color_en))
        else:
            try:
                color_en = r.get('raw_wave', '').split(',')[-1]
                colors.append(COLOR_MAP_EN_TO_ZH.get(color_en))
            except IndexError:
                continue
    return Counter(c for c in colors if c)

def get_local_recommendations(strategy, data, region):
    all_numbers = list(range(1, 50))
    if not data:
        normal = sorted(random.sample(all_numbers, 6))
    elif strategy == 'random':
        normal = sorted(random.sample(all_numbers, 6))
    else:
        try:
            config = _load_strategy_config(strategy, region)
            window = int(config.get("window") or 0)
            if window > 0:
                recent_data = data[:window]
            else:
                recent_data = data

            freq = analyze_special_number_frequency(recent_data)
            if not any(v > 0 for v in freq.values()):
                raise ValueError("No frequency data")

            sorted_freq = sorted(freq.items(), key=lambda item: item[1])
            pool_size = int(config.get("pool") or 16)
            pool_size = _clamp(pool_size, 8, 24)

            low_pool = [int(k) for k, _ in sorted_freq[:pool_size]]
            high_pool = [int(k) for k, _ in sorted_freq[-pool_size:]]
            mid_pool = [int(k) for k, _ in sorted_freq[pool_size:-pool_size]]
            if not mid_pool:
                mid_pool = [n for n in all_numbers if n not in low_pool and n not in high_pool]

            def sample_pool(pool, count, exclude=None):
                if exclude:
                    pool = [n for n in pool if n not in exclude]
                if len(pool) < count:
                    raise ValueError("Pool too small")
                return random.sample(pool, count)

            if strategy == 'hot':
                normal = sorted(sample_pool(high_pool, 6))
            elif strategy == 'cold':
                normal = sorted(sample_pool(low_pool, 6))
            elif strategy == 'trend':
                normal = sorted(sample_pool(high_pool, 6))
            elif strategy == 'hybrid':
                mix = config.get("mix") or {"hot": 2, "cold": 2, "trend": 2}
                trend_window = int(config.get("trend_window") or window or 15)
                trend_data = data[:trend_window] if data else []
                trend_freq = analyze_special_number_frequency(trend_data) if trend_data else {}
                if not trend_freq:
                    raise ValueError("No trend data")
                trend_sorted = sorted(trend_freq.items(), key=lambda item: item[1])
                trend_pool = [int(k) for k, _ in trend_sorted[-pool_size:]]
                normal = []
                normal += sample_pool(high_pool, int(mix.get("hot", 2)))
                normal += sample_pool(low_pool, int(mix.get("cold", 2)), exclude=normal)
                normal += sample_pool(trend_pool, int(mix.get("trend", 2)), exclude=normal)
                normal = sorted(normal)
            else:
                bucket_counts = config.get("bucket_counts") or [2, 2, 2]
                low_count, mid_count, high_count = bucket_counts
                normal = []
                normal += sample_pool(low_pool, int(low_count))
                normal += sample_pool(mid_pool, int(mid_count), exclude=normal)
                normal += sample_pool(high_pool, int(high_count), exclude=normal)
                normal = sorted(normal)
        except Exception as e:
            print(f"{strategy} recommendation failed, falling back to random. Reason: {e}")
            return get_local_recommendations('random', data, region)
    special_num = random.choice([n for n in all_numbers if n not in normal])
    # 不再计算生肖，所有地区都使用澳门API返回的生肖数据
    # 生肖信息将在API返回数据后更新
    sno_zodiac_info = ""
    return {"normal": normal, "special": {"number": str(special_num), "sno_zodiac": sno_zodiac_info}}

def _build_ai_prompt(data, region):
    history_lines = []
    recent_data = data[:10]
    if region == 'hk':
        year = datetime.now().year
        if recent_data:
            try:
                year = int(str(recent_data[0].get('date', ''))[:4])
            except (TypeError, ValueError):
                pass
        number_to_zodiac = _get_number_to_zodiac_map(year)
        for d in recent_data:
            zodiac = number_to_zodiac.get(str(d.get('sno')), '')
            history_lines.append(
                f"日期: {d['date']}, 开奖号码: {', '.join(d['no'])}, 特别号码: {d.get('sno')}({zodiac})"
            )
        recent_history = "\n".join(history_lines)
        prompt = f"""你是一位精通香港六合彩数据分析的专家。请基于以下最近10期的开奖历史数据（包含号码和生肖），为下一期提供一份详细的分析和号码推荐。

历史数据:
{recent_history}

你的任务是：
1. 写一段详细的分析说明，解释你的推荐依据和分析过程。
2. 明确推荐一组号码（6平码1特码），格式为：
   推荐号码：[平码1, 平码2, 平码3, 平码4, 平码5, 平码6] 特码: [特码]
3. 请以友好、自然的语言风格进行回复。
4. 确保你的回复中包含明确的号码推荐，便于系统提取。"""
    else:
        for d in recent_data:
            all_numbers = d.get('no', []) + ([d.get('sno')] if d.get('sno') else [])
            history_lines.append(
                f"期号: {d['id']}, 开奖号码: {','.join(all_numbers)}, 波色: {d['raw_wave']}, 生肖: {d['raw_zodiac']}"
            )
        recent_history = "\n".join(history_lines)
        prompt = f"""你是一位精通澳门六合彩数据分析的专家。请基于以下最近10期的开奖历史数据（包含开奖号码、波色和生肖），为下一期提供一份详细的分析和号码推荐。

历史数据:
{recent_history}

你的任务是：
1. 写一段详细的分析说明，解释你的推荐依据和分析过程。
2. 明确推荐一组号码（6平码1特码），格式为：
   推荐号码：[平码1, 平码2, 平码3, 平码4, 平码5, 平码6] 特码: [特码]
3. 请以友好、自然的语言风格进行回复。
4. 确保你的回复中包含明确的号码推荐，便于系统提取。"""
    return prompt

def predict_with_ai(data, region):
    ai_config = get_ai_config()
    if not ai_config['api_key'] or "你的" in ai_config['api_key']:
        return {"error": "AI API Key 未配置"}
    prompt = _build_ai_prompt(data, region)
    payload = {"model": ai_config['model'], "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    try:
        response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
            response.encoding = "utf-8"
        ai_response = response.json()['choices'][0]['message']['content']
        
        result, error = _finalize_ai_result(ai_response)
        if error:
            return {"error": error}
        return result
    except Exception as e:
        return {"error": f"调用AI API时出错: {e}"}

def _iter_ai_stream(ai_config, prompt):
    payload = {
        "model": ai_config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "stream": True
    }
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    response = requests.post(ai_config["api_url"], json=payload, headers=headers, stream=True, timeout=120)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
        response.encoding = "utf-8"
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content is None:
            content = choices[0].get("message", {}).get("content")
        if content is None:
            content = choices[0].get("text")
        if content:
            yield content

# --- Flask 路由 ---
@app.route('/')
def index():
    # 检查用户登录状态，如果未登录则重定向到登录页面
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    # 检查用户是否激活，如果未激活则显示提示
    user = User.query.get(session['user_id'])
    
    # 检查激活状态是否过期
    if user and user.is_activation_expired():
        user.is_active = False
        db.session.commit()
        session['is_active'] = False
        flash('您的账号激活已过期，请使用新的激活码重新激活。', 'warning')
    
    if not user.is_active:
        flash('您的账号尚未激活，部分功能受限。请先激活账号。', 'warning')
    
    return render_template('index.html', user=user)

def get_yearly_data(region, year):
    print(f"获取年度数据: 地区={region}, 年份={year}")
    
    # 处理"全部"年份的情况
    if year == 'all':
        year = str(datetime.now().year)
        print(f"年份为'全部'，使用当前年份: {year}")
    
    # 首先尝试从数据库获取数据
    try:
        # 查询数据库中的开奖记录
        query = LotteryDraw.query.filter_by(region=region)
        if year != 'all':
            query = query.filter(LotteryDraw.draw_date.like(f"{year}%"))
        
        db_records = query.order_by(LotteryDraw.draw_date.desc()).all()
        
        if db_records:
            print(f"从数据库获取到{len(db_records)}条{region}地区{year}年的数据")
            # 将数据库记录转换为API格式
            synced_draws = sync_draws_from_api(region, year, force=False)
            if synced_draws:
                db_records = query.order_by(LotteryDraw.draw_date.desc()).all()
            return [record.to_dict() for record in db_records]
    except Exception as e:
        print(f"从数据库获取数据失败: {e}")
    
    # 如果数据库中没有数据，则从API获取
    if region == 'hk':
        filtered_data = sync_draws_from_api('hk', year, force=True)
        print(f"从API获取香港数据: 过滤后={len(filtered_data)}")
        return filtered_data
    if region == 'macau':
        macau_data = sync_draws_from_api('macau', year, force=True)
        print(f"从API获取澳门数据: 总数={len(macau_data)}")
        return macau_data
    print(f"未知地区: {region}")
    return []

def save_draws_to_database(draws, region):
    """保存开奖记录到数据库"""
    try:
        count = 0
        for draw in draws:
            # 调用LotteryDraw模型的save_draw方法保存记录
            if LotteryDraw.save_draw(region, draw):
                count += 1
                settled = settle_pending_manual_bets(region, draw.get('id'))
                if settled:
                    print(f"已自动结算{settled}条手动下注记录，期号: {draw.get('id')}")
        
        print(f"成功保存{count}条{region}地区的开奖记录到数据库")
    except Exception as e:
        print(f"保存开奖记录到数据库失败: {e}")
        db.session.rollback()

def sync_draws_from_api(region, year=None, force=False):
    """从远程接口同步开奖记录并保存到数据库"""
    now = datetime.now()
    if not _is_within_sync_window(now):
        global _last_sync_window_skip_date
        today = now.date()
        if _last_sync_window_skip_date != today:
            print("当前不在开奖同步时间窗内，跳过同步。")
            _last_sync_window_skip_date = today
        return []
    last_sync = _last_draw_sync_times.get(region)
    if not force and last_sync and now - last_sync < _DRAW_SYNC_INTERVAL:
        return []

    if year is None or str(year).lower() == 'all':
        year_str = str(now.year)
    else:
        year_str = str(year).strip()

    remote_draws = []
    if region == 'hk':
        remote_data = load_hk_data(force_refresh=True)
        remote_draws = [rec for rec in remote_data if rec.get('date', '').startswith(year_str)]
    elif region == 'macau':
        remote_draws = get_macau_data(year_str, force_api=True)
    else:
        return []

    _last_draw_sync_times[region] = now

    if not remote_draws:
        print(f"{region}地区未获取到{year_str}年记录，跳过同步。")
        return []

    print(f"同步{region}地区{year_str}年开奖数据：{len(remote_draws)}条")
    save_draws_to_database(remote_draws, region)
    return remote_draws

@app.route('/api/draws')
def draws_api():
    region = request.args.get('region', 'hk')
    year = request.args.get('year', str(datetime.now().year))
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    
    print(f"API请求: 地区={region}, 年份={year}, 页码={page}, 每页数量={page_size}")
    
    # 处理"全部"年份的情况
    if year == 'all':
        year = str(datetime.now().year)
        print(f"年份为'全部'，使用当前年份: {year}")
    
    data = get_yearly_data(region, year)
    print(f"获取到{len(data)}条数据")
    
    # 获取澳门数据，用于提取生肖信息（优先数据库）
    macau_data = get_yearly_data('macau', year)
    print(f"获取到{len(macau_data)}条澳门数据用于生肖映射")
    
    # 创建号码到生肖的映射（澳门数据作为兜底）
    fallback_number_to_zodiac = {}
    try:
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        fallback_number_to_zodiac[num] = zodiacs[i]
    except Exception as e:
        print(f"获取澳门生肖映射失败: {e}")

    zodiac_map_cache = {}
    try:
        from models import ZodiacSetting
    except Exception:
        ZodiacSetting = None
    
    if region == 'hk':
        for record in data:
            mapping = fallback_number_to_zodiac
            if ZodiacSetting:
                zodiac_year = ZodiacSetting.get_zodiac_year_for_date(record.get('date'))
                mapping = zodiac_map_cache.get(zodiac_year)
                if mapping is None:
                    mapping = ZodiacSetting.get_all_settings_for_year(zodiac_year) or {}
                    zodiac_map_cache[zodiac_year] = mapping
                if not mapping:
                    mapping = fallback_number_to_zodiac

            normalized_mapping = {str(key): value for key, value in mapping.items()}
            sno = record.get('sno')
            record['sno_zodiac'] = normalized_mapping.get(str(sno), '')
            
            normal_numbers = record.get('no', [])
            normal_zodiacs = []
            for num in normal_numbers:
                normal_zodiacs.append(normalized_mapping.get(str(num), ''))
            record['raw_zodiac'] = ','.join(normal_zodiacs + [normalized_mapping.get(str(sno), '')])
            
            details_breakdown = []
            all_numbers = record.get('no', []) + [record.get('sno')]
            for i, num_str in enumerate(all_numbers):
                if not num_str: 
                    continue
                color_en = _get_hk_number_color(num_str)
                details_breakdown.append({
                    "position": f"平码 {i + 1}" if i < 6 else "特码", "number": num_str,
                    "color_en": color_en, "color_zh": COLOR_MAP_EN_TO_ZH.get(color_en, ''),
                    "zodiac": mapping.get(num_str, '')
                })
            record['details_breakdown'] = details_breakdown
        data = sorted(data, key=lambda x: x.get('date', ''), reverse=True)
        
        # 更新预测准确率
        update_prediction_accuracy(data, 'hk')
    else:
        # 更新澳门预测准确率
        update_prediction_accuracy(data, 'macau')
    
    # 分页处理
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    
    # 如果是第一页，返回前50条数据，否则返回分页数据
    if page == 1:
        return jsonify(data[:50])
    else:
        return jsonify(data[start_idx:end_idx])

def update_prediction_accuracy(data, region):
    """更新预测准确率 - 只比较特码和生肖"""
    try:
        # 获取所有该地区的预测记录
        predictions = PredictionRecord.query.filter_by(region=region).all()
        
        # 创建期数到开奖结果的映射
        draw_results = {}
        for draw in data:
            period = draw.get('id')
            if not period:
                continue
            
            special_number = str(draw.get('sno', ''))
            # 获取特码生肖 - 所有地区都使用澳门API返回的生肖数据
            special_zodiac = draw.get('sno_zodiac', '')
            
            if special_number:
                draw_results[period] = {
                    'special': special_number,
                    'special_zodiac': special_zodiac
                }
        
        # 更新每条预测记录的准确率
        for pred in predictions:
            # 检查是否已经更新过准确率
            if pred.is_result_updated:
                continue
                
            # 查找对应期数的开奖结果
            result = draw_results.get(pred.period)
            if not result:
                continue
                
            # 获取预测特码和生肖
            pred_special = pred.special_number
            pred_zodiac = pred.special_zodiac
            
            # 特码号码是否命中
            special_hit = 1 if pred_special == result['special'] else 0
            
            # 计算准确率
            accuracy = 0
            
            # 只有特码命中才算命中
            if special_hit == 1:
                accuracy = 100
            # 检查平码是否包含开奖特码
            elif pred.normal_numbers:
                normal_numbers = pred.normal_numbers.split(',')
                if result['special'] in normal_numbers:
                    accuracy = 50
            
            # 更新预测记录
            pred.actual_normal_numbers = ''  # 不再需要保存正码
            pred.actual_special_number = result['special']
            pred.actual_special_zodiac = result['special_zodiac']
            pred.accuracy_score = accuracy
            pred.is_result_updated = True
            
            # 如果预测成功（特码命中），发送中奖通知邮件
            if special_hit == 1:
                try:
                    # 获取用户信息
                    user = User.query.get(pred.user_id)
                    if user and user.email:
                        send_winning_notification_email(user, pred, region)
                except Exception as e:
                    print(f"发送中奖通知邮件失败: {e}")
        
        # 提交更改
        db.session.commit()

        # 根据最新准确率调整策略参数
        update_strategy_configs(region)

        # 触发自动预测（排除 AI 策略）
        if data and len(data) > 0:
            generate_auto_predictions(data, region)
        
    except Exception as e:
        print(f"更新预测准确率时出错: {e}")
        db.session.rollback()

def generate_auto_predictions(data, region):
    """为每期自动生成预测（排除 AI 策略）"""
    try:
        latest_draw = data[0] if data else None
        if not latest_draw:
            return

        latest_period = latest_draw.get('id', '')
        next_period = _get_next_period(region, latest_period)

        if not next_period:
            print("自动预测失败：无法确定下一期期数")
            return

        auto_predict_users = User.query.filter_by(
            is_active=True,
            auto_prediction_enabled=True
        ).all()

        for user in auto_predict_users:
            strategies = user.auto_prediction_strategies.split(',') if user.auto_prediction_strategies else ['balanced']
            regions = user.auto_prediction_regions.split(',') if hasattr(user, 'auto_prediction_regions') and user.auto_prediction_regions else ['hk', 'macau']

            if region not in regions:
                continue

            for strategy in strategies:
                if strategy == 'ai':
                    continue

                existing = PredictionRecord.query.filter_by(
                    user_id=user.id,
                    region=region,
                    period=next_period,
                    strategy=strategy
                ).first()

                if not existing:
                    generate_prediction_for_user(user, region, next_period, strategy, data)
    except Exception as e:
        print(f"自动预测出错：{e}")
        db.session.rollback()

def generate_prediction_for_user(user, region, period, strategy, data):
    """为指定用户生成预测（排除 AI 策略）"""
    try:
        if strategy == 'ai':
            print(f"已跳过用户 {user.username} 的AI自动预测")
            return

        result = get_local_recommendations(strategy, data, region)

        if result.get('error'):
            print(f"用户 {user.username} 的自动预测失败：{result.get('error')}")
            return

        prediction = PredictionRecord(
            user_id=user.id,
            region=region,
            strategy=strategy,
            period=period,
            normal_numbers=','.join(map(str, result.get('normal', []))),
            special_number=str(result.get('special', {}).get('number', '')),
            special_zodiac=result.get('special', {}).get('sno_zodiac', ''),
            prediction_text=result.get('recommendation_text', '')
        )
        db.session.add(prediction)
        db.session.commit()
        print(f"自动预测成功：为用户 {user.username} 的{region}地区第{period}期生成了{strategy}策略的预测")
    except Exception as e:
        print(f"为用户 {user.username} 生成预测时出错：{e}")
        db.session.rollback()

@app.route('/api/predict')
def unified_predict_api():
    region, strategy, year = request.args.get('region', 'hk'), request.args.get('strategy', 'balanced'), request.args.get('year', str(datetime.now().year))
    stream_response = request.args.get('stream') == '1'
    data = get_yearly_data(region, year)
    if not data: return jsonify({"error": f"无法加载{year}年的数据"}), 404
    
    # 检查用户是否登录和激活（对于需要保存记录的功能）
    user_id = session.get('user_id')
    is_active = session.get('is_active', False)
    
    # 获取下一期期数（使用最近一期的下一期）
    if data:
        try:
            latest_period = data[0].get('id', '')
            current_period = _get_next_period(region, latest_period)
        except (IndexError, ValueError) as e:
            print(f"计算下一期期数时出错: {e}")
            current_year = datetime.now().strftime('%y')
            current_period = f"{current_year}/001"
    else:
        current_year = datetime.now().strftime('%y')
        current_period = f"{current_year}/001"
    
    # 检查用户是否已经为当前期和当前策略生成过预测
    if user_id and is_active:
        existing = PredictionRecord.query.filter_by(
            user_id=user_id,
            region=region,
            period=current_period,
            strategy=strategy  # 添加策略作为过滤条件
        ).first()
        
        if existing:
            # 返回已存在的预测结果
            sno_zodiac = existing.special_zodiac
            # 不再在本地计算生肖，所有地区都使用澳门API返回的生肖数据
            
            result = {
                "normal": existing.normal_numbers.split(','),
                "special": {
                    "number": existing.special_number,
                    "sno_zodiac": sno_zodiac
                }
            }
            if existing.prediction_text:
                result["recommendation_text"] = existing.prediction_text
            if stream_response and strategy == 'ai':
                payload = {
                    "type": "done",
                    "region": region,
                    "strategy": strategy,
                    "period": current_period,
                    "saved": True,
                    **result
                }
                def generate_existing():
                    yield json.dumps(payload, ensure_ascii=False) + "\n\n"
                return Response(stream_with_context(generate_existing()), mimetype='text/event-stream')
            return jsonify(result)
    
    # 生成新的预测
    if strategy == 'ai':
        if stream_response:
            def generate_stream():
                ai_config = get_ai_config()
                if not ai_config['api_key'] or "你的" in ai_config['api_key']:
                    yield json.dumps({"type": "error", "error": "AI API Key 未配置"}, ensure_ascii=False) + "\n\n"
                    return
                prompt = _build_ai_prompt(data, region)
                full_text = ""
                try:
                    for chunk in _iter_ai_stream(ai_config, prompt):
                        full_text += chunk
                        yield json.dumps({
                            "type": "content",
                            "content": chunk,
                            "full_text": full_text
                        }, ensure_ascii=False) + "\n\n"
                except Exception as e:
                    yield json.dumps({"type": "error", "error": f"调用AI API时出错: {e}"}, ensure_ascii=False) + "\n\n"
                    return

                if not full_text:
                    fallback = predict_with_ai(data, region)
                    if fallback.get("error"):
                        yield json.dumps({"type": "error", "error": fallback.get("error")}, ensure_ascii=False) + "\n\n"
                        return
                    result = fallback
                else:
                    result, error = _finalize_ai_result(full_text)
                    if error:
                        yield json.dumps({"type": "error", "error": error}, ensure_ascii=False) + "\n\n"
                        return

                result.update({
                    "type": "done",
                    "region": region,
                    "strategy": strategy,
                    "period": current_period
                })

                if user_id and is_active:
                    try:
                        prediction = PredictionRecord(
                            user_id=user_id,
                            region=region,
                            strategy=strategy,
                            period=current_period,
                            normal_numbers=','.join(map(str, result.get('normal', []))),
                            special_number=str(result.get('special', {}).get('number', '')),
                            special_zodiac=result.get('special', {}).get('sno_zodiac', ''),
                            prediction_text=result.get('recommendation_text', '')
                        )
                        db.session.add(prediction)
                        db.session.commit()
                        result["saved"] = True
                    except Exception as e:
                        db.session.rollback()
                        result["saved"] = False
                        result["save_error"] = str(e)

                yield json.dumps(result, ensure_ascii=False) + "\n\n"

            return Response(stream_with_context(generate_stream()), mimetype='text/event-stream')

        result = predict_with_ai(data, region)
        # 检查AI预测是否失败
        if result.get('error'):
            # 返回详细的错误信息
            error_message = result.get('error')
            return jsonify({
                "error": error_message,
                "error_type": "ai_prediction_failed",
                "message": f"AI预测失败：{error_message}，请稍后再试或联系管理员检查AI API配置。"
            }), 400
    else:
        result = get_local_recommendations(strategy, data, region)
    
    # 保存预测记录（仅对已激活用户）
    if user_id and is_active and not result.get('error'):
        try:
            prediction = PredictionRecord(
                user_id=user_id,
                region=region,
                strategy=strategy,
                period=current_period,
                normal_numbers=','.join(map(str, result.get('normal', []))),
                special_number=str(result.get('special', {}).get('number', '')),
                special_zodiac=result.get('special', {}).get('sno_zodiac', ''),
                prediction_text=result.get('recommendation_text', '')
            )
            db.session.add(prediction)
            db.session.commit()
        except Exception as e:
            print(f"保存预测记录失败: {e}")
            return jsonify({
                "error": str(e),
                "error_type": "database_error",
                "message": "保存预测记录失败，请稍后再试。"
            }), 500
    
    return jsonify(result)

# 手动更新数据API
@app.route('/api/update_data', methods=['POST'])
def update_data_api():
    try:
        region = request.json.get('region', 'all')
        current_year = str(datetime.now().year)
        
        if region == 'all' or region == 'hk':
            # 更新香港数据
            hk_data = load_hk_data(force_refresh=True)
            hk_filtered = [rec for rec in hk_data if rec.get('date', '').startswith(current_year)]
            save_draws_to_database(hk_filtered, 'hk')
            print(f"手动更新：成功更新香港数据{len(hk_filtered)}条")
        
        if region == 'all' or region == 'macau':
            # 更新澳门数据
            macau_data = get_macau_data(current_year, force_api=True)
            save_draws_to_database(macau_data, 'macau')
            print(f"手动更新：成功更新澳门数据{len(macau_data)}条")
        
        return jsonify({
            "success": True, 
            "message": f"数据更新成功，香港和澳门数据已更新至最新"
        })
    except Exception as e:
        print(f"手动更新数据失败: {e}")
        return jsonify({
            "success": False,
            "message": f"更新失败: {str(e)}"
        }), 500

@app.route('/api/number_frequency')
def number_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_number_frequency(data))

@app.route('/api/special_zodiac_frequency')
def special_zodiac_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_zodiac_frequency(data, region, year))

@app.route('/api/special_color_frequency')
def special_color_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_color_frequency(data, region))

@app.route('/api/get_zodiacs')
def get_zodiacs_api():
    numbers = request.args.get('numbers', '').split(',')
    if not numbers or not numbers[0]:
        return jsonify({'normal_zodiacs': [], 'special_zodiac': ''})
    
    # 获取生肖年份（按农历新年切换）
    from models import ZodiacSetting
    zodiac_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
    number_to_zodiac = {}
    
    try:
        # 使用ZodiacSetting模型获取生肖设置
        for number in range(1, 50):
            zodiac = ZodiacSetting.get_zodiac_for_number(zodiac_year, number)
            if zodiac:
                number_to_zodiac[str(number)] = zodiac
    except Exception as e:
        print(f"获取生肖设置失败: {e}")
        # 如果出错，使用澳门API返回的生肖数据
        macau_data = get_macau_data(str(zodiac_year))
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        number_to_zodiac[num] = zodiacs[i]
    
    # 获取每个号码对应的生肖
    normal_zodiacs = []
    for num in numbers[:-1]:  # 除了最后一个数字（特码）
        normal_zodiacs.append(number_to_zodiac.get(num, ''))
    
    # 获取特码生肖
    special_zodiac = number_to_zodiac.get(numbers[-1], '') if len(numbers) > 0 else ''
    
    return jsonify({
        'normal_zodiacs': normal_zodiacs,
        'special_zodiac': special_zodiac
    })

@app.route('/api/search_draws')
def search_draws_api():
    region, year, term = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year)), request.args.get('term', '').strip().lower()
    if not term: return jsonify([])
    data, results = get_yearly_data(region, year), []
    number_to_zodiac = _get_number_to_zodiac_map(year) if region == 'hk' else {}
    for record in data:
        if region == 'hk':
            sno_zodiac_display = number_to_zodiac.get(str(record.get('sno', '')), record.get('sno_zodiac', ''))
        else:
            sno_zodiac_display = record.get('sno_zodiac', '')
        if term == record.get('sno', '') or term in sno_zodiac_display.lower():
            if 'details_breakdown' not in record and region == 'hk':
                 record['sno_zodiac'] = sno_zodiac_display
            results.append(record)
    return jsonify(results[:20])

@app.route('/chat')
def chat_page():
    # 检查用户登录状态
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    from models import ZodiacSetting
    current_year = ZodiacSetting.get_zodiac_year_for_date(datetime.now())
    
    # 创建号码到生肖的映射
    number_to_zodiac = {}
    
    # 首先尝试从ZodiacSetting获取当前年份的生肖设置
    try:
        zodiac_settings = ZodiacSetting.get_all_settings_for_year(current_year)
        
        if zodiac_settings:
            # 使用数据库中的生肖设置
            for number, zodiac in zodiac_settings.items():
                number_to_zodiac[str(number)] = zodiac
        else:
            # 如果数据库中没有设置，则使用澳门API返回的生肖数据
            macau_data = get_macau_data(str(current_year))
            for record in macau_data:
                all_numbers = record.get('no', []) + [record.get('sno')]
                zodiacs = record.get('raw_zodiac', '').split(',')
                if len(all_numbers) == len(zodiacs):
                    for i, num in enumerate(all_numbers):
                        if num:
                            number_to_zodiac[num] = zodiacs[i]
    except Exception as e:
        print(f"获取生肖设置失败，使用澳门API数据: {e}")
        # 如果出错，使用澳门API返回的生肖数据
        macau_data = get_macau_data(str(current_year))
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        number_to_zodiac[num] = zodiacs[i]
    
    hk_all_yearly_data = get_yearly_data('hk', current_year)
    hk_data_sorted = sorted(hk_all_yearly_data, key=lambda x: x.get('date', ''), reverse=True)
    hk_latest_10 = hk_data_sorted[:10]
    for record in hk_latest_10:
        # 使用澳门的生肖对应关系
        sno = record.get('sno')
        record['sno_zodiac'] = number_to_zodiac.get(sno, '')

    macau_latest_10 = get_yearly_data('macau', current_year)[:10]

    ball_colors = {
        'red': RED_BALLS,
        'blue': BLUE_BALLS,
        'green': GREEN_BALLS
    }

    return render_template('chat.html', 
                           hk_results=hk_latest_10, 
                           macau_results=macau_latest_10,
                           ball_colors=json.dumps(ball_colors))

@app.route('/api/chat', methods=['POST'])
def handle_chat():
    ai_config = get_ai_config()
    if not ai_config['api_key'] or "你的" in ai_config['api_key']:
        return jsonify({"reply": "错误：管理员尚未配置AI API Key，无法使用聊天功能。"}), 400
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"reply": "错误：未能获取到您发送的消息。"}), 400
    system_prompt = "你是一个精通香港和澳门六合彩数据分析的AI助手，知识渊博，回答友好。请根据用户的提问，提供相关的历史知识、数据规律或普遍性建议。不要提供具体的投资建议。"
    payload = {"model": ai_config['model'], "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], "temperature": 0.7}
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    try:
        response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "latin-1"):
            response.encoding = "utf-8"
        ai_reply = response.json()['choices'][0]['message']['content']
        return jsonify({"reply": ai_reply})
    except Exception as e:
        print(f"Error calling AI chat API: {e}")
        return jsonify({"reply": f"抱歉，调用AI时遇到错误，请稍后再试。"}), 500

def send_winning_notification_email(user, prediction, region):
    """发送预测命中通知邮件"""
    # 获取SMTP配置
    smtp_server = SystemConfig.get_config('smtp_server')
    smtp_port = int(SystemConfig.get_config('smtp_port', '587'))
    smtp_username = SystemConfig.get_config('smtp_username')
    smtp_password = SystemConfig.get_config('smtp_password')
    site_name = SystemConfig.get_config('site_name', 'AI预测系统')
    
    # 检查SMTP配置是否完整
    if not all([smtp_server, smtp_username, smtp_password]):
        raise Exception('邮件服务未配置，请联系管理员')
    
    # 准备邮件内容
    region_name = '香港' if region == 'hk' else '澳门'
    strategy_name = {
        'random': '随机预测',
        'balanced': '均衡预测',
        'ai': 'AI智能预测'
    }.get(prediction.strategy, '未知策略')
    
    subject = f"恭喜您！{region_name}第{prediction.period}期特码预测命中"
    
    # 构建HTML邮件内容
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #4CAF50; color: white; padding: 10px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9f9f9; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #777; }}
            .highlight {{ color: #e53935; font-weight: bold; }}
            .info-row {{ margin-bottom: 10px; }}
            .btn {{ display: inline-block; background-color: #4CAF50; color: white; padding: 10px 20px; 
                   text-decoration: none; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>恭喜您！预测命中通知</h2>
            </div>
            <div class="content">
                <p>尊敬的 <strong>{user.username}</strong>：</p>
                <p>恭喜您！您使用<strong>{strategy_name}</strong>对{region_name}六合彩第{prediction.period}期的特码预测已经<span class="highlight">命中</span>！</p>
                
                <div class="info-row"><strong>预测期数：</strong> {prediction.period}</div>
                <div class="info-row"><strong>预测策略：</strong> {strategy_name}</div>
                <div class="info-row"><strong>预测特码：</strong> <span class="highlight">{prediction.special_number}</span></div>
                <div class="info-row"><strong>开奖特码：</strong> <span class="highlight">{prediction.actual_special_number}</span></div>
                <div class="info-row"><strong>预测时间：</strong> {prediction.created_at.strftime('%Y-%m-%d %H:%M:%S')}</div>
                
                <p>您可以登录系统查看更多预测详情和历史记录。</p>
                <p style="text-align: center; margin-top: 20px;">
                    <a href="#" class="btn">查看详情</a>
                </p>
            </div>
            <div class="footer">
                <p>此邮件由系统自动发送，请勿回复。</p>
                <p>© {datetime.now().year} {site_name} - 所有权利保留</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # 创建邮件对象
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = smtp_username
    msg['To'] = user.email
    
    # 添加HTML内容
    msg.attach(MIMEText(html_content, 'html'))
    
    # 发送邮件
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()
        print(f"成功发送预测命中通知邮件给用户 {user.username} ({user.email})")
    except Exception as e:
        print(f"发送邮件失败: {e}")
        raise

# 全局请求前处理器，检查用户激活状态
@app.before_request
def check_user_activation():
    # 跳过静态文件和认证相关路由
    if request.endpoint and (request.endpoint.startswith('static') or 
                           request.endpoint.startswith('auth.')):
        return
    
    # 检查用户是否登录
    if 'user_id' in session:
        try:
            user = User.query.get(session['user_id'])
            if user:
                # 检查用户激活状态是否过期
                if user.activation_expires_at and datetime.now() > user.activation_expires_at:
                    # 激活已过期，更新状态
                    user.is_active = False
                    db.session.commit()
                    session['is_active'] = False
                    if not request.path.startswith('/auth/activate'):
                        flash('您的账号激活已过期，请使用新的激活码重新激活。', 'warning')
        except Exception as e:
            print(f"检查用户激活状态时出错: {e}")
            # 如果出错，跳过检查
            pass

# 创建数据库表和初始管理员账号
def init_database():
    with app.app_context():
        db.create_all()
        
        # 自动检查并更新数据库结构（邀请系统）
        from auto_update_db import check_and_update_database
        try:
            check_and_update_database()
        except Exception as e:
            print(f"自动更新数据库结构时出错: {e}")
        
        # 检查是否存在管理员账号，如果不存在则创建默认管理员
        admin = User.query.filter_by(is_admin=True).first()
        if not admin:
            admin = User(
                username='admin',
                email='admin@example.com',
                is_active=True,
                is_admin=True
            )
            admin.set_password('admin123')  # 默认密码，请在首次登录后修改
            db.session.add(admin)
            db.session.commit()
            if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
                print("已创建默认管理员账号: admin / admin123")
        
        # 初始化系统配置
        configs = [
            ('ai_api_key', '', 'AI API密钥'),
            ('ai_api_url', 'https://api.deepseek.com/v1/chat/completions', 'AI API地址'),
            ('ai_model', 'gemini-2.0-flash', 'AI模型'),
            ('smtp_server', '', 'SMTP服务器'),
            ('smtp_port', '587', 'SMTP端口'),
            ('smtp_username', '', 'SMTP用户名'),
            ('smtp_password', '', 'SMTP密码'),
        ]
        
        for key, value, description in configs:
            if not SystemConfig.query.filter_by(key=key).first():
                config = SystemConfig(key=key, value=value, description=description)
                db.session.add(config)
        
        db.session.commit()
        
        # 为管理员创建示例邀请码
        try:
            existing_codes = InviteCode.query.filter_by(created_by='admin').count()
            if existing_codes == 0:
                from datetime import timedelta
                for i in range(3):
                    invite_code = InviteCode()
                    invite_code.code = InviteCode.generate_code()
                    invite_code.created_by = 'admin'
                    invite_code.expires_at = datetime.now() + timedelta(days=30)
                    db.session.add(invite_code)
                db.session.commit()
                if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
                    print("✅ 为管理员创建了3个示例邀请码")
        except Exception as e:
            print(f"创建示例邀请码时出错: {e}")

_scheduler = None
_scheduler_lock_path = None
_scheduler_lock_acquired = False

def _try_acquire_scheduler_lock():
    import tempfile
    global _scheduler_lock_path, _scheduler_lock_acquired
    if _scheduler_lock_acquired:
        return True
    lock_path = os.path.join(tempfile.gettempdir(), "mark-six-scheduler.lock")
    pid = os.getpid()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(pid))
        _scheduler_lock_path = lock_path
        _scheduler_lock_acquired = True
        return True
    except FileExistsError:
        try:
            with open(lock_path, "r") as f:
                existing_pid = int((f.read() or "").strip() or "0")
        except Exception:
            existing_pid = 0
        if existing_pid and _pid_is_running(existing_pid):
            return False
        try:
            os.remove(lock_path)
        except OSError:
            return False
        return _try_acquire_scheduler_lock()

def _release_scheduler_lock():
    global _scheduler_lock_path, _scheduler_lock_acquired
    if not _scheduler_lock_acquired or not _scheduler_lock_path:
        return
    try:
        os.remove(_scheduler_lock_path)
    except OSError:
        pass
    _scheduler_lock_path = None
    _scheduler_lock_acquired = False

# 定时任务：每天21:40自动更新数据库中的开奖记录
def update_lottery_data():
    """定时任务：更新数据库中的开奖记录"""
    print(f"开始执行定时任务：更新数据库中的开奖记录，时间：{datetime.now()}")
    
    # 在应用上下文中执行数据库操作
    with app.app_context():
        try:
            current_year = str(datetime.now().year)
            
            # 更新香港数据
            print("正在同步香港数据...")
            hk_data = sync_draws_from_api('hk', current_year, force=True)
            print(f"香港数据更新完成：{len(hk_data)}条")
            
            # 更新澳门数据
            print("正在同步澳门数据...")
            macau_data = sync_draws_from_api('macau', current_year, force=True)
            print(f"澳门数据更新完成：{len(macau_data)}条")

            # 触发自动预测功能（排除 AI 策略）
            print("正在生成自动预测...")
            if hk_data:
                generate_auto_predictions(hk_data, 'hk')
            if macau_data:
                generate_auto_predictions(macau_data, 'macau')
            
            print(f"定时任务执行完成：成功更新香港数据{len(hk_filtered)}条，澳门数据{len(macau_data)}条")
            
        except Exception as e:
            print(f"定时任务执行失败：{e}")
            import traceback
            traceback.print_exc()

def start_scheduler(force=False):
    """Start the APScheduler job if enabled and not already running."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    enabled = os.environ.get("ENABLE_SCHEDULER", "1").lower() in ("1", "true", "yes", "on")
    if not enabled:
        if _should_log_startup():
            print("定时任务未启动：ENABLE_SCHEDULER=0")
        return None

    # Avoid double-start when Flask debug reloader spawns a parent process.
    if not force and app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None

    if not _try_acquire_scheduler_lock():
        if _should_log_startup():
            print("定时任务未启动：已有实例在运行")
        return None

    import atexit
    atexit.register(_release_scheduler_lock)

    _scheduler = BackgroundScheduler()

    def _log_scheduler_event(event):
        if event.code == EVENT_JOB_MISSED:
            print(f"定时任务补跑触发：{event.job_id} 原定时间已错过")
        elif event.code == EVENT_JOB_EXECUTED:
            if event.exception:
                print(f"定时任务执行失败：{event.job_id} {event.exception}")
            else:
                print(f"定时任务执行完成：{event.job_id}")

    _scheduler.add_listener(_log_scheduler_event, EVENT_JOB_MISSED | EVENT_JOB_EXECUTED)
    _scheduler.add_job(
        update_lottery_data,
        'cron',
        hour=21,
        minute=40,
        misfire_grace_time=300,
        coalesce=True
    )
    _scheduler.start()
    if _should_log_startup():
        print("定时任务已启动：每天21:40自动更新数据库中的开奖记录")
    return _scheduler

if os.environ.get("ENABLE_SCHEDULER", "1").lower() in ("1", "true", "yes", "on"):
    try:
        start_scheduler()
    except Exception as e:
        print(f"定时任务启动失败: {e}")

if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 设置定时任务
    scheduler = start_scheduler()
    
    try:
        # 启动Flask应用
        app.run(debug=True, port=5000)
    except (KeyboardInterrupt, SystemExit):
        # 关闭定时任务
        if scheduler and scheduler.running:
            scheduler.shutdown()


