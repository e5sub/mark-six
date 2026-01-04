from flask import Flask, jsonify, render_template, request, session, redirect, url_for, flash, Response, stream_with_context
from flask_login import LoginManager, current_user
import json
import os
import random
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import Counter
from datetime import datetime
import time
from apscheduler.schedulers.background import BackgroundScheduler

# 导入用户系统模块
from models import db, User, PredictionRecord, SystemConfig, InviteCode, LotteryDraw
from auth import auth_bp
from admin import admin_bp
from user import user_bp
from activation_code_routes import activation_code_bp
from invite_routes import invite_bp

# --- 配置信息 ---
app = Flask(__name__)
# 使用环境变量设置密钥，如果不存在则使用随机生成的密钥
import secrets
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# 确保数据目录存在
data_dir = os.path.join(os.getcwd(), 'data')
os.makedirs(data_dir, exist_ok=True)

# 数据库配置
db_path = os.path.join(data_dir, 'lottery_system.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 只在主进程中打印一次
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
    print(f"数据库路径: {db_path}")
    print(f"数据库URI: {app.config['SQLALCHEMY_DATABASE_URI']}")

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
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
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

# --- 数据加载与处理 ---
def load_hk_data():
    # 直接从URL获取数据，添加重试机制
    max_retries = 3
    retry_delay = 2  # 秒

    for attempt in range(max_retries):
        try:
            print(f"正在获取香港数据，URL: {HK_DATA_SOURCE_URL} (尝试 {attempt + 1}/{max_retries})")
            response = requests.get(HK_DATA_SOURCE_URL, timeout=30)
            response.raise_for_status()
            print(f"成功获取香港数据")
            return response.json()

        except requests.exceptions.Timeout:
            print(f"获取香港数据超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                import time as t
                t.sleep(retry_delay)
            else:
                print(f"获取香港数据超时，已重试 {max_retries} 次后失败")
                return []

        except requests.exceptions.ConnectionError as e:
            print(f"获取香港数据连接错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                import time as t
                t.sleep(retry_delay)
            else:
                print(f"获取香港数据连接失败，已重试 {max_retries} 次后失败")
                return []

        except Exception as e:
            print(f"从URL获取香港数据失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                import time as t
                t.sleep(retry_delay)
            else:
                print(f"获取香港数据失败，已重试 {max_retries} 次后失败")
                return []

    return []

def get_macau_data(year):
    url = MACAU_API_URL_TEMPLATE.format(year=year)
    max_retries = 3
    retry_delay = 2  # 秒

    for attempt in range(max_retries):
        try:
            print(f"正在获取澳门数据，URL: {url} (尝试 {attempt + 1}/{max_retries})")
            response = requests.get(url, timeout=30)
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

        except requests.exceptions.Timeout:
            print(f"获取澳门数据超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                import time as t
                t.sleep(retry_delay)
            else:
                print(f"获取澳门数据超时，已重试 {max_retries} 次后失败")
                return []

        except requests.exceptions.ConnectionError as e:
            print(f"获取澳门数据连接错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                import time as t
                t.sleep(retry_delay)
            else:
                print(f"获取澳门数据连接失败，已重试 {max_retries} 次后失败")
                return []

        except Exception as e:
            print(f"Error in get_macau_data for year {year}: {e}")
            if attempt < max_retries - 1:
                import time as t
                t.sleep(retry_delay)
            else:
                print(f"获取澳门数据失败，已重试 {max_retries} 次后失败")
                return []

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
    # 不再计算生肖，所有地区都使用澳门API返回的生肖数据
    # 生肖信息将在API返回数据后更新
    sno_zodiac_info = ""
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

    payload = {"model": ai_config['model'], "messages": [{"role": "user", "content": prompt}], "temperature": 0.8, "stream": True}
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}
    try:
        response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=120, stream=True)
        response.raise_for_status()

        def generate():
            full_response = ""
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data_str = line[6:]
                        if data_str == '[DONE]':
                            break
                        try:
                            data = json.loads(data_str)
                            if 'choices' in data and len(data['choices']) > 0:
                                delta = data['choices'][0].get('delta', {})
                                content = delta.get('content', '')
                                if content:
                                    full_response += content
                                    yield json.dumps({
                                        'type': 'content',
                                        'content': content,
                                        'full_text': full_response
                                    }) + '\n\n'
                        except json.JSONDecodeError:
                            continue

            # 流式传输完成后，提取号码
            import re
            normal_numbers = []
            special_number = ""

            # 尝试匹配格式化的推荐号码
            number_pattern = r'推荐号码：\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\s*特码:\s*\[(\d+)\]'
            match = re.search(number_pattern, full_response)

            if match:
                normal_numbers = [int(match.group(i)) for i in range(1, 7)]
                special_number = match.group(7)
            else:
                # 如果没有找到格式化的推荐，尝试从文本中提取数字
                all_numbers = re.findall(r'\b\d{1,2}\b', full_response)
                valid_numbers = [int(n) for n in all_numbers if 1 <= int(n) <= 49]

                if len(valid_numbers) >= 7:
                    normal_numbers = sorted(valid_numbers[:6])
                    special_number = str(valid_numbers[6])
                else:
                    # 如果无法从AI回复中提取有效号码，返回错误
                    yield json.dumps({
                        'type': 'error',
                        'error': "无法从AI回复中提取有效号码"
                    }) + '\n\n'
                    return

            # 确保所有号码都是有效的
            normal_numbers = [n for n in normal_numbers if 1 <= n <= 49]
            if len(normal_numbers) < 6:
                yield json.dumps({
                    'type': 'error',
                    'error': "AI生成的平码数量不足"
                }) + '\n\n'
                return

            normal_numbers = sorted(normal_numbers[:6])

            if not special_number or not (1 <= int(special_number) <= 49):
                yield json.dumps({
                    'type': 'error',
                    'error': "AI生成的特码无效"
                }) + '\n\n'
                return

            # 不再计算生肖，所有地区都使用澳门API返回的生肖数据
            # 生肖信息将在API返回数据后更新
            sno_zodiac = ""

            # 返回最终结果
            yield json.dumps({
                'type': 'done',
                'recommendation_text': full_response,
                'normal': normal_numbers,
                'special': {
                    'number': special_number,
                    'sno_zodiac': sno_zodiac
                }
            }) + '\n\n'

        return Response(stream_with_context(generate()), mimetype='text/event-stream')

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
            return [record.to_dict() for record in db_records]
    except Exception as e:
        print(f"从数据库获取数据失败: {e}")
    
    # 如果数据库中没有数据，则从API获取
    if region == 'hk':
        all_data = load_hk_data()
        filtered_data = [rec for rec in all_data if rec.get('date', '').startswith(str(year))]
        print(f"从API获取香港数据: 总数={len(all_data)}, 过滤后={len(filtered_data)}")
        
        # 保存数据到数据库
        save_draws_to_database(filtered_data, 'hk')
        
        return filtered_data
    if region == 'macau':
        macau_data = get_macau_data(year)
        print(f"从API获取澳门数据: 总数={len(macau_data)}")
        
        # 保存数据到数据库
        save_draws_to_database(macau_data, 'macau')
        
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
        
        print(f"成功保存{count}条{region}地区的开奖记录到数据库")
    except Exception as e:
        print(f"保存开奖记录到数据库失败: {e}")
        db.session.rollback()

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
    
    # 获取澳门数据，用于提取生肖信息
    macau_data = get_macau_data(year)
    print(f"获取到{len(macau_data)}条澳门数据用于生肖映射")
    
    # 创建号码到生肖的映射
    number_to_zodiac = {}
    
    # 从ZodiacSetting获取当前年份的生肖设置
    try:
        from models import ZodiacSetting
        current_year = int(year)
        
        # 使用ZodiacSetting模型获取生肖设置
        for number in range(1, 50):
            zodiac = ZodiacSetting.get_zodiac_for_number(current_year, number)
            if zodiac:
                number_to_zodiac[str(number)] = zodiac
        
        if not number_to_zodiac:  # 如果没有获取到生肖设置，则使用澳门API数据
            print(f"未找到{current_year}年的生肖设置，使用澳门API数据")
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
        for record in macau_data:
            all_numbers = record.get('no', []) + [record.get('sno')]
            zodiacs = record.get('raw_zodiac', '').split(',')
            if len(all_numbers) == len(zodiacs):
                for i, num in enumerate(all_numbers):
                    if num:
                        number_to_zodiac[num] = zodiacs[i]
    
    if region == 'hk':
        for record in data:
            # 使用澳门的生肖对应关系
            sno = record.get('sno')
            record['sno_zodiac'] = number_to_zodiac.get(sno, '')
            
            # 为平码添加生肖信息
            normal_numbers = record.get('no', [])
            normal_zodiacs = []
            for num in normal_numbers:
                normal_zodiacs.append(number_to_zodiac.get(num, ''))
            record['raw_zodiac'] = ','.join(normal_zodiacs + [number_to_zodiac.get(sno, '')])
            
            details_breakdown = []
            all_numbers = record.get('no', []) + [record.get('sno')]
            for i, num_str in enumerate(all_numbers):
                if not num_str: continue
                color_en = _get_hk_number_color(num_str)
                details_breakdown.append({
                    "position": f"平码 {i + 1}" if i < 6 else "特码", "number": num_str,
                    "color_en": color_en, "color_zh": COLOR_MAP_EN_TO_ZH.get(color_en, ''),
                    "zodiac": number_to_zodiac.get(num_str, '')
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
        
        # 强制触发自动预测功能，确保每次获取数据时都会检查是否需要生成预测
        if data and len(data) > 0:
            # 生成新的预测
            generate_auto_predictions(data, region)
        
    except Exception as e:
        print(f"更新预测准确率时出错: {e}")
        db.session.rollback()

@app.route('/api/predict')
def unified_predict_api():
    region, strategy, year = request.args.get('region', 'hk'), request.args.get('strategy', 'balanced'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    if not data:
        return jsonify({"error": f"无法加载{year}年的数据"}), 404

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
    if user_id and is_active and strategy != 'ai':
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
            return jsonify(result)

    # 生成新的预测
    if strategy == 'ai':
        # AI预测使用流式输出
        ai_response = predict_with_ai(data, region)

        # 如果返回的是Response对象（流式），直接返回
        if isinstance(ai_response, Response):
            return ai_response
        else:
            # 如果返回的是字典（错误信息），返回错误
            error_message = ai_response.get('error')
            return jsonify({
                "error": error_message,
                "error_type": "ai_prediction_failed",
                "message": f"AI预测失败：{error_message}，请稍后再试或联系管理员检查AI API配置。"
            }), 400
    else:
        result = get_local_recommendations(strategy, data, region)

    # 保存预测记录（仅对已激活用户，非AI预测）
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
            hk_data = load_hk_data()
            hk_filtered = [rec for rec in hk_data if rec.get('date', '').startswith(current_year)]
            save_draws_to_database(hk_filtered, 'hk')
            print(f"手动更新：成功更新香港数据{len(hk_filtered)}条")
        
        if region == 'all' or region == 'macau':
            # 更新澳门数据
            macau_data = get_macau_data(current_year)
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
    return jsonify(analyze_special_zodiac_frequency(data, region))

@app.route('/api/special_color_frequency')
def special_color_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_color_frequency(data, region))

def generate_auto_predictions(data, region):
    """为每期自动生成预测"""
    try:
        # 获取最新一期数据
        latest_draw = data[0] if data else None
        if not latest_draw:
            return
            
        # 计算下一期期数
        next_period = None
        if region == 'hk':
            # 香港六合彩期数格式为"年份/期数"，如"25/075"
            latest_period = latest_draw.get('id', '')
            if latest_period and '/' in latest_period:
                year_part, num_part = latest_period.split('/')
                next_num = int(num_part) + 1
                # 如果期数超过120，年份加1，期数重置为1
                if next_num > 120:
                    next_year = int(year_part) + 1
                    next_period = f"{next_year:02d}/001"
                else:
                    next_period = f"{year_part}/{next_num:03d}"
            else:
                current_year = datetime.now().strftime('%y')
                next_period = f"{current_year}/001"
        else:
            # 澳门六合彩期数格式
            latest_period = latest_draw.get('id', '')
            if latest_period and latest_period.isdigit():
                next_period = str(int(latest_period) + 1)
            else:
                next_period = datetime.now().strftime('%Y%m%d')
        
        if not next_period:
            print("自动预测失败：无法确定下一期期数")
            return
        
        # 处理用户级自动预测
        # 查找所有启用了自动预测的活跃用户
        auto_predict_users = User.query.filter_by(
            is_active=True,
            auto_prediction_enabled=True
        ).all()
        
        for user in auto_predict_users:
            # 获取用户的预测策略列表
            strategies = user.auto_prediction_strategies.split(',') if user.auto_prediction_strategies else ['balanced']
            
            # 获取用户的预测地区列表
            regions = user.auto_prediction_regions.split(',') if hasattr(user, 'auto_prediction_regions') and user.auto_prediction_regions else ['hk', 'macau']
            
            # 检查当前地区是否在用户选择的地区列表中
            if region not in regions:
                continue
            
            # 为每个策略生成预测
            for strategy in strategies:
                # 检查是否已经为下一期生成过该策略的预测
                existing = PredictionRecord.query.filter_by(
                    user_id=user.id,
                    region=region,
                    period=next_period,
                    strategy=strategy
                ).first()
                
                if not existing:
                    # 生成用户级预测
                    generate_prediction_for_user(user, region, next_period, strategy, data)
                
    except Exception as e:
        print(f"自动预测出错：{e}")
        db.session.rollback()

def generate_prediction_for_user(user, region, period, strategy, data):
    """为指定用户生成预测"""
    try:
        # 生成预测
        if strategy == 'ai':
            # 强制调用AI API进行预测
            ai_config = get_ai_config()
            if not ai_config['api_key'] or "你的" in ai_config['api_key']:
                print(f"用户 {user.username} 的AI预测失败：AI API Key未配置")
                # AI API Key未配置，直接返回错误，不进行预测
                return
            else:
                # 确保调用AI API
                result = predict_with_ai(data, region)
                # 如果AI预测失败，直接返回，不使用均衡预测
                if result.get('error'):
                    print(f"用户 {user.username} 的AI预测失败：{result.get('error')}")
                    return
        else:
            result = get_local_recommendations(strategy, data, region)
            
        if result.get('error'):
            print(f"用户 {user.username} 的自动预测失败：{result.get('error')}")
            return
            
        # 保存预测记录
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

@app.route('/api/get_zodiacs')
def get_zodiacs_api():
    numbers = request.args.get('numbers', '').split(',')
    if not numbers or not numbers[0]:
        return jsonify({'normal_zodiacs': [], 'special_zodiac': ''})
    
    # 获取当前年份
    current_year = datetime.now().year
    
    # 从ZodiacSetting获取当前年份的生肖设置
    from models import ZodiacSetting
    number_to_zodiac = {}
    
    try:
        # 使用ZodiacSetting模型获取生肖设置
        for number in range(1, 50):
            zodiac = ZodiacSetting.get_zodiac_for_number(current_year, number)
            if zodiac:
                number_to_zodiac[str(number)] = zodiac
    except Exception as e:
        print(f"获取生肖设置失败: {e}")
        # 如果出错，使用澳门API返回的生肖数据
        macau_data = get_macau_data(str(current_year))
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
    current_year = datetime.now().year
    
    # 创建号码到生肖的映射
    number_to_zodiac = {}
    
    # 首先尝试从ZodiacSetting获取当前年份的生肖设置
    try:
        from models import ZodiacSetting
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
    payload = {"model": ai_config['model'], "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], "temperature": 0.7, "stream": True}
    headers = {"Authorization": f"Bearer {ai_config['api_key']}", "Content-Type": "application/json"}

    def generate():
        try:
            response = requests.post(ai_config['api_url'], json=payload, headers=headers, timeout=60, stream=True)
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data_str = line[6:]
                        if data_str.strip() == '[DONE]':
                            break
                        try:
                            data = json.loads(data_str)
                            if 'choices' in data and len(data['choices']) > 0:
                                delta = data['choices'][0].get('delta', {})
                                content = delta.get('content', '')
                                if content:
                                    yield f"data: {json.dumps({'content': content})}\n\n"
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            print(f"Error calling AI chat API: {e}")
            yield f"data: {json.dumps({'error': '抱歉，调用AI时遇到错误，请稍后再试。'})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

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
                <h2>🎉 恭喜您！预测命中通知 🎉</h2>
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

# 定时任务：每天22:00自动更新数据库中的开奖记录
def update_lottery_data():
    """定时任务：更新数据库中的开奖记录"""
    print(f"开始执行定时任务：更新数据库中的开奖记录，时间：{datetime.now()}")
    
    # 在应用上下文中执行数据库操作
    with app.app_context():
        try:
            current_year = str(datetime.now().year)
            
            # 更新香港数据
            print("正在获取香港数据...")
            hk_data = load_hk_data()
            hk_filtered = [rec for rec in hk_data if rec.get('date', '').startswith(current_year)]
            save_draws_to_database(hk_filtered, 'hk')
            print(f"香港数据更新完成：{len(hk_filtered)}条")
            
            # 更新澳门数据
            print("正在获取澳门数据...")
            macau_data = get_macau_data(current_year)
            save_draws_to_database(macau_data, 'macau')
            print(f"澳门数据更新完成：{len(macau_data)}条")
            
            # 触发自动预测功能
            print("正在生成自动预测...")
            if hk_filtered:
                generate_auto_predictions(hk_filtered, 'hk')
            if macau_data:
                generate_auto_predictions(macau_data, 'macau')
            
            print(f"定时任务执行完成：成功更新香港数据{len(hk_filtered)}条，澳门数据{len(macau_data)}条")
            
        except Exception as e:
            print(f"定时任务执行失败：{e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 设置定时任务
    scheduler = BackgroundScheduler()
    scheduler.add_job(update_lottery_data, 'cron', hour=22, minute=0)
    scheduler.start()
    # 只在主进程中打印一次
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print("定时任务已启动：每天22:00自动更新数据库中的开奖记录")
    
    try:
        # 启动Flask应用
        app.run(debug=True, port=5000)
    except (KeyboardInterrupt, SystemExit):
        # 关闭定时任务
        scheduler.shutdown()
