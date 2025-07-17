from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
import random
import requests
from collections import Counter
from datetime import datetime

# --- 应用配置 ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)  # 用于session加密
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lottery_data.db' # SQLite数据库文件路径
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' # 如果用户未登录，将重定向到登录页面
login_manager.login_message = "请先登录以访问此页面。" # 自定义提示信息

# --- API及数据源常量 ---
AI_API_KEY = "你的_AI_API_KEY"  # 重要：请替换为你的DeepSeek API Key
AI_API_URL = "https://api.deepseek.com/v1/chat/completions"
HK_DATA_SOURCE_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/icelam/mark-six-data-visualization/master/data/all.json"
MACAU_API_URL_TEMPLATE = "https://history.macaumarksix.com/history/macaujc2/y/{year}"

# --- 号码属性常量 ---
ZODIAC_MAPPING_SEQUENCE = ("虎", "兔", "龙", "蛇", "牛", "鼠", "猪", "狗", "鸡", "猴", "羊", "马")
RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46]
BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48]
GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]
COLOR_MAP_EN_TO_ZH = {'red': '红', 'blue': '蓝', 'green': '绿'}
ZODIAC_TRAD_TO_SIMP = {'鼠':'鼠','牛':'牛','虎':'虎','兔':'兔','龍':'龙','蛇':'蛇','馬':'马','羊':'羊','猴':'猴','雞':'鸡','狗':'狗','豬':'猪'}


# --- 数据库模型定义 ---
class User(UserMixin, db.Model):
    """用户模型"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class HKDraw(db.Model):
    """香港开奖数据模型"""
    id = db.Column(db.String(20), primary_key=True) # 例如 '24/082'
    date = db.Column(db.String(20), nullable=False)
    no = db.Column(db.String(100), nullable=False) # 以逗号分隔的字符串存储
    sno = db.Column(db.String(2), nullable=False)
    inv = db.Column(db.String(50)); p1 = db.Column(db.String(50)); p1u = db.Column(db.String(50))
    p2 = db.Column(db.String(50)); p2u = db.Column(db.String(50)); p3 = db.Column(db.String(50))
    p3u = db.Column(db.String(50)); p4 = db.Column(db.String(50)); p4u = db.Column(db.String(50))
    p5 = db.Column(db.String(50)); p5u = db.Column(db.String(50)); p6 = db.Column(db.String(50))
    p6u = db.Column(db.String(50)); p7 = db.Column(db.String(50)); p7u = db.Column(db.String(50))

class MacauDraw(db.Model):
    """澳门开奖数据模型"""
    id = db.Column(db.String(20), primary_key=True) # 例如 '2024194'
    date = db.Column(db.String(20), nullable=False)
    no = db.Column(db.String(100), nullable=False)
    sno = db.Column(db.String(2), nullable=False)
    sno_zodiac = db.Column(db.String(5))
    raw_wave = db.Column(db.String(100))
    raw_zodiac = db.Column(db.String(100))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 数据处理及更新函数 ---
def _get_hk_number_zodiac(number):
    try:
        num = int(number); return ZODIAC_MAPPING_SEQUENCE[(num - 1) % 12] if 1 <= num <= 49 else ""
    except: return ""

def _get_hk_number_color(number):
    try:
        num = int(number)
        if num in RED_BALLS: return 'red'
        if num in BLUE_BALLS: return 'blue'
        if num in GREEN_BALLS: return 'green'
        return ""
    except: return ""

def update_hk_data_from_source():
    try:
        response = requests.get(HK_DATA_SOURCE_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        new_draws_count = 0
        for record in data:
            draw_id = record.get('id')
            if not draw_id or HKDraw.query.get(draw_id): continue
            new_draw = HKDraw(id=draw_id, date=record.get('date'), no=','.join(record.get('no', [])), sno=record.get('sno'), inv=record.get('inv'), p1=record.get('p1'), p1u=record.get('p1u'), p2=record.get('p2'), p2u=record.get('p2u'), p3=record.get('p3'), p3u=record.get('p3u'), p4=record.get('p4'), p4u=record.get('p4u'), p5=record.get('p5'), p5u=record.get('p5u'), p6=record.get('p6'), p6u=record.get('p6u'), p7=record.get('p7'), p7u=record.get('p7u'))
            db.session.add(new_draw)
            new_draws_count += 1
        db.session.commit()
        return {"success": True, "message": f"成功添加 {new_draws_count} 条香港新纪录。"}
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}

def update_macau_data_for_year(year):
    url = MACAU_API_URL_TEMPLATE.format(year=year)
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        api_data = response.json()
        if not api_data or not api_data.get("data"): return {"success": False, "message": "澳门API未返回数据"}
        new_draws_count = 0
        for record in api_data["data"]:
            draw_id = record.get("expect")
            if not draw_id or MacauDraw.query.get(draw_id): continue
            raw_numbers_str = record.get("openCode", "").split(',')
            if len(raw_numbers_str) < 7: continue
            simplified_zodiacs = [ZODIAC_TRAD_TO_SIMP.get(z, z) for z in record.get("zodiac", "").split(',')]
            new_draw = MacauDraw(id=draw_id, date=record.get("openTime"), no=','.join(raw_numbers_str[:6]), sno=raw_numbers_str[6], sno_zodiac=simplified_zodiacs[6] if len(simplified_zodiacs) >= 7 else "", raw_wave=record.get("wave", ""), raw_zodiac=",".join(simplified_zodiacs))
            db.session.add(new_draw)
            new_draws_count += 1
        db.session.commit()
        return {"success": True, "message": f"成功为 {year} 年添加 {new_draws_count} 条澳门新纪录。"}
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": str(e)}

def get_yearly_data(region, year):
    Model = HKDraw if region == 'hk' else MacauDraw
    query_result = Model.query.filter(Model.date.like(f"{year}%")).order_by(Model.date.desc()).all()
    data_list = []
    for r in query_result:
        record = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        record['no'] = record['no'].split(',')
        data_list.append(record)
    return data_list

# --- 用户认证路由 ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and user.check_password(request.form['password']):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('用户名或密码无效', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        if User.query.filter_by(username=request.form['username']).first():
            flash('该用户名已被注册。', 'danger')
            return redirect(url_for('register'))
        new_user = User(username=request.form['username'])
        new_user.set_password(request.form['password'])
        db.session.add(new_user)
        db.session.commit()
        flash('注册成功！请登录。', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- 主应用路由 ---
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/chat')
@login_required
def chat_page():
    current_year = str(datetime.now().year)
    hk_data_sorted = get_yearly_data('hk', current_year)
    macau_data_sorted = get_yearly_data('macau', current_year)
    for record in hk_data_sorted: record['sno_zodiac'] = _get_hk_number_zodiac(record.get('sno'))
    ball_colors = {'red': RED_BALLS, 'blue': BLUE_BALLS, 'green': GREEN_BALLS}
    return render_template('chat.html', hk_results=hk_data_sorted[:10], macau_results=macau_data_sorted[:10], ball_colors=json.dumps(ball_colors))

# --- 数据分析与推荐函数 ---
def analyze_special_number_frequency(data):
    counts = Counter(r.get('sno') for r in data if r.get('sno'))
    return {str(i): counts.get(str(i), 0) for i in range(1, 50)}

def analyze_special_zodiac_frequency(data, region):
    zodiacs = []
    for r in data:
        sno = r.get('sno')
        if not sno: continue
        zodiacs.append(_get_hk_number_zodiac(sno) if region == 'hk' else r.get('sno_zodiac'))
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
            try: colors.append(COLOR_MAP_EN_TO_ZH.get(r.get('raw_wave', '').split(',')[-1]))
            except IndexError: continue
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
    sno_zodiac_info = _get_hk_number_zodiac(special_num) if region == 'hk' else ""
    return {"normal": normal, "special": {"number": str(special_num), "sno_zodiac": sno_zodiac_info}}

def predict_with_ai(data, region):
    if not AI_API_KEY or "你的" in AI_API_KEY: return {"error": "AI API Key 未配置"}
    history_lines, prompt = [], ""
    recent_data = data[:10]
    if region == 'hk':
        for d in recent_data:
            zodiac = _get_hk_number_zodiac(d.get('sno'))
            history_lines.append(f"日期: {d['date']}, 开奖号码: {', '.join(d['no'])}, 特别号码: {d.get('sno')}({zodiac})")
        recent_history = "\n".join(history_lines)
        prompt = f"你是一位精通香港六合彩数据分析的专家。请基于以下最近10期的开奖历史数据（包含号码和生肖），为下一期提供一份详细的分析和号码推荐。\n\n历史数据:\n{recent_history}\n\n你的任务是：\n1. 写一段简短的分析说明模式。\n2. 推荐一组号码（6平码1特码）。\n3. 请以友好、自然的语言风格进行回复。"
    else:
        for d in recent_data:
            all_numbers = d.get('no', []) + ([d.get('sno')] if d.get('sno') else [])
            history_lines.append(f"期号: {d['id']}, 开奖号码: {','.join(all_numbers)}, 波色: {d['raw_wave']}, 生肖: {d['raw_zodiac']}")
        recent_history = "\n".join(history_lines)
        prompt = f"你是一位精通澳门六合彩数据分析的专家。请基于以下最近10期的开奖历史数据（包含开奖号码、波色和生肖），为下一期提供一份详细的分析和号码推荐。\n\n历史数据:\n{recent_history}\n\n你的任务是：\n1. 写一段简短的分析说明模式。\n2. 推荐一组号码（6平码1特码）。\n3. 请以友好、自然的语言风格进行回复。"
    
    payload = {"model": "gemini-pro", "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.post(AI_API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return {"recommendation_text": response.json()['choices'][0]['message']['content']}
    except Exception as e:
        return {"error": f"调用AI API时出错: {e}"}

# --- API 端点 ---
@app.route('/api/draws')
@login_required
def draws_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    if region == 'macau': update_macau_data_for_year(year)
    data = get_yearly_data(region, year)
    if region == 'hk':
        for record in data:
            record['sno_zodiac'] = _get_hk_number_zodiac(record.get('sno'))
            details_breakdown = []
            all_numbers = record.get('no', []) + ([record.get('sno')] if record.get('sno') else [])
            for i, num_str in enumerate(all_numbers):
                if not num_str: continue
                color_en = _get_hk_number_color(num_str)
                details_breakdown.append({"position": f"平码 {i + 1}" if i < 6 else "特码", "number": num_str, "color_en": color_en, "color_zh": COLOR_MAP_EN_TO_ZH.get(color_en, ''), "zodiac": _get_hk_number_zodiac(num_str)})
            record['details_breakdown'] = details_breakdown
    return jsonify(data[:20])

@app.route('/api/update_data', methods=['POST'])
@login_required
def update_data_api():
    result = update_hk_data_from_source()
    if result["success"]: return jsonify({"message": f"香港数据更新成功! {result['message']}"})
    return jsonify({"message": f"香港数据更新失败: {result['message']}"}), 500

@app.route('/api/number_frequency')
@login_required
def number_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_number_frequency(data))

@app.route('/api/special_zodiac_frequency')
@login_required
def special_zodiac_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_zodiac_frequency(data, region))

@app.route('/api/special_color_frequency')
@login_required
def special_color_frequency_api():
    region, year = request.args.get('region', 'hk'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    return jsonify(analyze_special_color_frequency(data, region))

@app.route('/api/predict')
@login_required
def unified_predict_api():
    region, strategy, year = request.args.get('region', 'hk'), request.args.get('strategy', 'balanced'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    if not data: return jsonify({"error": f"无法加载{year}年的数据"}), 404
    if strategy == 'ai': return jsonify(predict_with_ai(data, region))
    return jsonify(get_local_recommendations(strategy, data, region))

@app.route('/api/search_draws')
@login_required
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

@app.route('/api/chat', methods=['POST'])
@login_required
def handle_chat():
    if not AI_API_KEY or "你的" in AI_API_KEY: return jsonify({"reply": "错误：管理员尚未配置AI API Key，无法使用聊天功能。"}), 400
    user_message = request.json.get("message")
    if not user_message: return jsonify({"reply": "错误：未能获取到您发送的消息。"}), 400
    system_prompt = "你是一个精通香港和澳门六合彩数据分析的AI助手，知识渊博，回答友好。请根据用户的提问，提供相关的历史知识、数据规律或普遍性建议。不要提供具体的投资建议。"
    payload = {"model": "gemini-pro", "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], "temperature": 0.7}
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.post(AI_API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        ai_reply = response.json()['choices'][0]['message']['content']
        return jsonify({"reply": ai_reply})
    except Exception as e:
        print(f"Error calling AI chat API: {e}")
        return jsonify({"reply": f"抱歉，调用AI时遇到错误，请稍后再试。"}), 500

# --- 命令行工具 ---
@app.cli.command('init-db')
def init_db_command():
    """创建数据库表并填充初始数据"""
    with app.app_context():
        db.create_all()
    print('数据库表已创建。')
    print('正在从源更新香港数据...')
    with app.app_context():
        update_hk_data_from_source()
    print('正在更新当前年份的澳门数据...')
    with app.app_context():
        update_macau_data_for_year(datetime.now().year)
    print('数据库初始化完成。')


if __name__ == '__main__':
    app.run(debug=True, port=5000)