from flask import Flask, jsonify, render_template, request, session, redirect, url_for, flash
import json
import os
import random
import requests
from collections import Counter
from datetime import datetime

# 导入用户系统模块
from models import db, User, PredictionRecord, SystemConfig
from auth import auth_bp
from admin import admin_bp
from user import user_bp

# --- 配置信息 ---
app = Flask(__name__)
# 使用环境变量设置密钥，如果不存在则使用随机生成的密钥
import os
import secrets
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# 确保数据目录存在
data_dir = os.path.join(os.getcwd(), 'data')
os.makedirs(data_dir, exist_ok=True)

# 数据库配置
db_path = os.path.join(data_dir, 'lottery_system.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

print(f"数据库路径: {db_path}")
print(f"数据库URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
from flask import Flask, jsonify, render_template, request, session, redirect, url_for, flash
import json
import os
import random
import requests
from collections import Counter
from datetime import datetime

# 导入用户系统模块
from models import db, User, PredictionRecord, SystemConfig
from auth import auth_bp
from admin import admin_bp
from user import user_bp

# --- 配置信息 ---
app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'  # 请在生产环境中更改此密钥

from flask import Flask, jsonify, render_template, request, session, redirect, url_for, flash
import json
import os
import random
import requests
from collections import Counter
from datetime import datetime

# 导入用户系统模块
from models import db, User, PredictionRecord, SystemConfig
from auth import auth_bp
from admin import admin_bp
from user import user_bp

# --- 配置信息 ---
app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'  # 请在生产环境中更改此密钥

# 确保数据目录存在
import os
data_dir = os.path.join(os.getcwd(), 'data')
os.makedirs(data_dir, exist_ok=True)

# 数据库配置
db_path = os.path.join(data_dir, 'lottery_system.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

print(f"数据库路径: {db_path}")
print(f"数据库URI: {app.config['SQLALCHEMY_DATABASE_URI']}")

# 初始化数据库
db.init_app(app)

# 注册蓝图
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(admin_bp)
app.register_blueprint(user_bp)

# 获取AI配置的函数
def get_ai_config():
    return {
        'api_key': SystemConfig.get_config('ai_api_key', '你的_AI_API_KEY'),
        'api_url': SystemConfig.get_config('ai_api_url', 'https://api.deepseek.com/v1/chat/completions'),
        'model': SystemConfig.get_config('ai_model', 'gemini-2.0-flash')
    }
# 澳门数据API
MACAU_API_URL_TEMPLATE = "https://history.macaumarksix.com/history/macaujc2/y/{year}"
# 香港数据API
HK_DATA_SOURCE_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/icelam/mark-six-data-visualization/master/data/all.json"

# --- 号码属性计算与映射 ---
ZODIAC_MAPPING_SEQUENCE = ("虎", "兔", "龙", "蛇", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马")
RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46]
BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48]
GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]
COLOR_MAP_EN_TO_ZH = {'red': '红', 'blue': '蓝', 'green': '绿'}
ZODIAC_TRAD_TO_SIMP = {'鼠':'鼠','牛':'牛','虎':'虎','兔':'兔','龍':'龙','蛇':'蛇','馬':'马','羊':'羊','猴':'猴','雞':'鸡','狗':'狗','豬':'猪'}

def _get_hk_number_zodiac(number):
    try:
        num = int(number)
        if not 1 <= num <= 49: return ""
        return ZODIAC_MAPPING_SEQUENCE[(num - 1) % 12]
    except:
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

# --- 数据加载与处理 ---
def load_hk_data():
    # 直接从URL获取数据
    try:
        response = requests.get(HK_DATA_SOURCE_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"从URL获取香港数据失败: {e}")
        return []

def get_macau_data(year):
    url = MACAU_API_URL_TEMPLATE.format(year=year)
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        api_data = response.json()
        if not api_data or not api_data.get("data"): return []
        
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
        
        # --- 新增去重逻辑 ---
        unique_data = []
        seen_ids = set()
        for record in normalized_data:
            record_id = record.get("id")
            if record_id and record_id not in seen_ids:
                unique_data.append(record)
                seen_ids.add(record_id)
        # --- 去重逻辑结束 ---

        # 使用去重后的 unique_data 进行过滤和排序
        filtered_by_year = [rec for rec in unique_data if rec.get("date", "").startswith(str(year))]
        return sorted(filtered_by_year, key=lambda x: (x.get('date', ''), x.get('id', '')), reverse=True)
    except Exception as e:
        print(f"Error in get_macau_data for year {year}: {e}")
        return []

def analyze_special_number_frequency(data):
    special_numbers = []
    for r in data:
        if r.get('sno'):
            special_numbers.append(r.get('sno'))
    counts = Counter(special_numbers)
    return {str(i): counts.get(str(i), 0) for i in range(1, 50)}

def analyze_special_zodiac_frequency(data, region):
    zodiacs = []
    for r in data:
        sno = r.get('sno')
        if not sno: continue
        if region == 'hk':
            zodiacs.append(_get_hk_number_zodiac(sno))
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
    if strategy == 'random':
        normal = sorted(random.sample(all_numbers, 6))
    else:
        try:
            freq = analyze_special_number_frequency(data)
            if not any(v > 0 for v in freq.values()): raise ValueError("No frequency data")
            sorted_freq = sorted(freq.items(), key=lambda item: item[1])
            low_freq, mid_freq, high_freq = [int(k) for k, v in sorted_freq[:16]], [int(k) for k, v in sorted_freq[16:33]], [int(k) for k, v in sorted_freq[33:]]
            normal = sorted(random.sample(low_freq, 2) + random.sample(mid_freq, 2) + random.sample(high_freq, 2))
        except Exception as e:
            print(f"Balanced recommendation failed, falling back to random. Reason: {e}")
            return get_local_recommendations('random', data, region)
    special_num = random.choice([n for n in all_numbers if n not in normal])
    sno_zodiac_info = ""
    if region == 'hk':
        sno_zodiac_info = _get_hk_number_zodiac(special_num)
    else:
        # 为澳门预测也添加特码生肖
        sno_zodiac_info = _get_hk_number_zodiac(special_num)
    return {"normal": normal, "special": {"number": str(special_num), "sno_zodiac": sno_zodiac_info}}

def predict_with_ai(data, region):
    ai_config = get_ai_config()
    if not ai_config['api_key'] or "你的" in ai_config['api_key']: 
        return {"error": "AI API Key 未配置"}
    history_lines, prompt = [], ""
    recent_data = data[:10]
    if region == 'hk':
        for d in recent_data:
            zodiac = _get_hk_number_zodiac(d.get('sno'))
            history_lines.append(f"日期: {d['date']}, 开奖号码: {', '.join(d['no'])}, 特别号码: {d.get('sno')}({zodiac})")
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
            history_lines.append(f"期号: {d['id']}, 开奖号码: {','.join(all_numbers)}, 波色: {d['raw_wave']}, 生肖: {d['raw_zodiac']}")
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
    
    payload = {"model": ai_config['model'], "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    try:
        response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        ai_response = response.json()['choices'][0]['message']['content']
        
        # 从AI回复中提取号码
        import re
        normal_numbers = []
        special_number = ""
        
        # 尝试匹配格式化的推荐号码
        number_pattern = r'推荐号码：\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\s*特码:\s*\[(\d+)\]'
        match = re.search(number_pattern, ai_response)
        
        if match:
            normal_numbers = [int(match.group(i)) for i in range(1, 7)]
            special_number = match.group(7)
        else:
            # 如果没有找到格式化的推荐，尝试从文本中提取数字
            all_numbers = re.findall(r'\b\d{1,2}\b', ai_response)
            valid_numbers = [int(n) for n in all_numbers if 1 <= int(n) <= 49]
            
            if len(valid_numbers) >= 7:
                normal_numbers = sorted(valid_numbers[:6])
                special_number = str(valid_numbers[6])
            else:
                # 如果无法从AI回复中提取有效号码，使用本地推荐
                local_rec = get_local_recommendations('balanced', data, region)
                normal_numbers = local_rec['normal']
                special_number = local_rec['special']['number']
        
        # 确保所有号码都是有效的
        normal_numbers = [n for n in normal_numbers if 1 <= n <= 49]
        while len(normal_numbers) < 6:
            new_num = random.randint(1, 49)
            if new_num not in normal_numbers:
                normal_numbers.append(new_num)
        normal_numbers = sorted(normal_numbers)
        
        if not special_number or not (1 <= int(special_number) <= 49):
            special_number = str(random.randint(1, 49))
        
        # 获取特码生肖
        sno_zodiac = ""
        if region == 'hk':
            sno_zodiac = _get_hk_number_zodiac(special_number)
        else:
            # 澳门也使用相同的生肖计算方法
            sno_zodiac = _get_hk_number_zodiac(special_number)
        
        return {
            "recommendation_text": ai_response,
            "normal": normal_numbers,
            "special": {
                "number": special_number,
                "sno_zodiac": sno_zodiac
            }
        }
    except Exception as e:
        return {"error": f"调用AI API时出错: {e}"}

# --- Flask 路由 ---
@app.route('/')
def index():
    # 检查用户登录状态，如果未登录则重定向到登录页面
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    # 检查用户是否激活，如果未激活则显示提示
    user = User.query.get(session['user_id'])
    if not user.is_active:
        flash('您的账号尚未激活，部分功能受限。请先激活账号。', 'warning')
    
    return render_template('index.html', user=user)

def get_yearly_data(region, year):
    if region == 'hk':
        all_data = load_hk_data()
        return [rec for rec in all_data if rec.get('date', '').startswith(str(year))]
    if region == 'macau':
        return get_macau_data(year)
    return []

@app.route('/api/draws')
def draws_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    if region == 'hk':
        for record in data:
            record['sno_zodiac'] = _get_hk_number_zodiac(record.get('sno'))
            details_breakdown = []
            all_numbers = record.get('no', []) + [record.get('sno')]
            for i, num_str in enumerate(all_numbers):
                if not num_str: continue
                color_en = _get_hk_number_color(num_str)
                details_breakdown.append({
                    "position": f"平码 {i + 1}" if i < 6 else "特码", "number": num_str,
                    "color_en": color_en, "color_zh": COLOR_MAP_EN_TO_ZH.get(color_en, ''),
                    "zodiac": _get_hk_number_zodiac(num_str)
                })
            record['details_breakdown'] = details_breakdown
        data = sorted(data, key=lambda x: x.get('date', ''), reverse=True)
        
        # 更新预测准确率
        update_prediction_accuracy(data, 'hk')
    else:
        # 更新澳门预测准确率
        update_prediction_accuracy(data, 'macau')
        
    return jsonify(data[:20])

def update_prediction_accuracy(data, region):
    """更新预测准确率"""
    try:
        # 获取所有该地区的预测记录
        predictions = PredictionRecord.query.filter_by(region=region).all()
        
        # 创建期数到开奖结果的映射
        draw_results = {}
        for draw in data:
            period = draw.get('id')
            if not period:
                continue
            
            normal_numbers = [str(n) for n in draw.get('no', [])]
            special_number = str(draw.get('sno', ''))
            
            if normal_numbers and special_number:
                draw_results[period] = {
                    'normal': normal_numbers,
                    'special': special_number
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
                
            # 计算准确率
            pred_normal = pred.normal_numbers.split(',')
            pred_special = pred.special_number
            
            # 计算正码命中数
            normal_hits = len(set(pred_normal) & set(result['normal']))
            
            # 特码是否命中
            special_hit = 1 if pred_special == result['special'] else 0
            
            # 计算总准确率 (正码命中数 / 6 * 0.7 + 特码命中 * 0.3)
            accuracy = (normal_hits / 6 * 0.7) + (special_hit * 0.3)
            
            # 更新预测记录
            pred.actual_normal_numbers = ','.join(result['normal'])
            pred.actual_special_number = result['special']
            pred.accuracy_score = accuracy
            pred.is_result_updated = True
        
        # 提交更改
        db.session.commit()
        
    except Exception as e:
        print(f"更新预测准确率时出错: {e}")
        db.session.rollback()

@app.route('/api/predict')
def unified_predict_api():
    region, strategy, year = request.args.get('region', 'hk'), request.args.get('strategy', 'balanced'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    if not data: return jsonify({"error": f"无法加载{year}年的数据"}), 404
    
    # 检查用户是否登录和激活（对于需要保存记录的功能）
    user_id = session.get('user_id')
    is_active = session.get('is_active', False)
    
    # 获取下一期期数（使用最近一期的下一期）
    if data:
        try:
            if region == 'hk':
                # 香港六合彩期数格式为"年份/期数"，如"25/075"
                latest_period = data[0].get('id', '')
                if latest_period and '/' in latest_period:
                    year_part, num_part = latest_period.split('/')
                    next_num = int(num_part) + 1
                    # 如果期数超过120，年份加1，期数重置为1
                    if next_num > 120:
                        next_year = int(year_part) + 1
                        next_period = f"{next_year:02d}/001"
                    else:
                        next_period = f"{year_part}/{next_num:03d}"
                    current_period = next_period
                else:
                    current_year = datetime.now().strftime('%y')
                    current_period = f"{current_year}/001"
            else:
                # 澳门六合彩期数格式
                latest_period = data[0].get('id', '')
                if latest_period and latest_period.isdigit():
                    next_period = str(int(latest_period) + 1)
                    current_period = next_period
                else:
                    current_period = datetime.now().strftime('%Y%m%d')
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
            result = {
                "normal": existing.normal_numbers.split(','),
                "special": {
                    "number": existing.special_number,
                    "sno_zodiac": existing.special_zodiac
                }
            }
            if existing.prediction_text:
                result["recommendation_text"] = existing.prediction_text
            return jsonify(result)
    
    # 生成新的预测
    if strategy == 'ai': 
        result = predict_with_ai(data, region)
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
    
    return jsonify(result)

# 移除更新数据API

@app.route('/api/number_frequency')
def number_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_number_frequency(data))

@app.route('/api/special_zodiac_frequency')
def special_zodiac_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_zodiac_frequency(data, region))

@app.route('/api/special_color_frequency')
def special_color_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_color_frequency(data, region))

@app.route('/api/search_draws')
def search_draws_api():
    region, year, term = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year)), request.args.get('term', '').strip().lower()
    if not term: return jsonify([])
    data, results = get_yearly_data(region, year), []
    for record in data:
        sno_zodiac_display = _get_hk_number_zodiac(record.get('sno', '')) if region == 'hk' else record.get('sno_zodiac', '')
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
    current_year = str(datetime.now().year)
    
    hk_all_yearly_data = get_yearly_data('hk', current_year)
    hk_data_sorted = sorted(hk_all_yearly_data, key=lambda x: x.get('date', ''), reverse=True)
    hk_latest_10 = hk_data_sorted[:10]
    for record in hk_latest_10:
        record['sno_zodiac'] = _get_hk_number_zodiac(record.get('sno'))

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
        response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        ai_reply = response.json()['choices'][0]['message']['content']
        return jsonify({"reply": ai_reply})
    except Exception as e:
        print(f"Error calling AI chat API: {e}")
        return jsonify({"reply": f"抱歉，调用AI时遇到错误，请稍后再试。"}), 500

# 创建数据库表和初始管理员账号
def init_database():
    with app.app_context():
        db.create_all()
        
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

if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 不再需要检查本地数据文件
    
    app.run(debug=True, port=5000)
