from flask import Flask, jsonify, render_template, request
import json
import os
import random
import requests
from collections import Counter
from datetime import datetime

# --- 配置信息 ---
AI_API_KEY = "你的_AI_API_KEY"
AI_API_URL = "https://api.deepseek.com/v1/chat/completions"
app = Flask(__name__)
HK_DATA_SOURCE_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/icelam/mark-six-data-visualization/master/data/all.json"
HK_DATA_FILE_PATH = "data/hk.json"
MACAU_API_URL_TEMPLATE = "https://history.macaumarksix.com/history/macaujc2/y/{year}"

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
    try:
        with open(HK_DATA_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        if update_hk_data_from_source():
            with open(HK_DATA_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

def update_hk_data_from_source():
    try:
        response = requests.get(HK_DATA_SOURCE_URL, timeout=15)
        response.raise_for_status()
        os.makedirs("data", exist_ok=True)
        with open(HK_DATA_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(response.json(), f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

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
        sno_zodiac_info = ""
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
    
    payload = {"model": "gemini-2.0-flash", "messages": [{"role": "user", "content": prompt}], "temperature": 0.8}
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.post(AI_API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return {"recommendation_text": response.json()['choices'][0]['message']['content']}
    except Exception as e:
        return {"error": f"调用AI API时出错: {e}"}

# --- Flask 路由 ---
@app.route('/')
def index():
    return render_template('index.html')

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
    return jsonify(data[:20])

@app.route('/api/predict')
def unified_predict_api():
    region, strategy, year = request.args.get('region', 'hk'), request.args.get('strategy', 'balanced'), request.args.get('year', str(datetime.now().year))
    data = get_yearly_data(region, year)
    if not data: return jsonify({"error": f"无法加载{year}年的数据"}), 404
    if strategy == 'ai': return jsonify(predict_with_ai(data, region))
    return jsonify(get_local_recommendations(strategy, data, region))

@app.route('/api/update_data', methods=['POST'])
def update_data_api():
    if update_hk_data_from_source(): return jsonify({"message": "香港数据已成功更新！"})
    return jsonify({"message": "香港数据更新失败。"}), 500

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
    if not AI_API_KEY or "你的" in AI_API_KEY:
        return jsonify({"reply": "错误：管理员尚未配置AI API Key，无法使用聊天功能。"}), 400
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"reply": "错误：未能获取到您发送的消息。"}), 400
    system_prompt = "你是一个精通香港和澳门六合彩数据分析的AI助手，知识渊博，回答友好。请根据用户的提问，提供相关的历史知识、数据规律或普遍性建议。不要提供具体的投资建议。"
    payload = {"model": "gemini-2.0-flash", "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], "temperature": 0.7}
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.post(AI_API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        ai_reply = response.json()['choices'][0]['message']['content']
        return jsonify({"reply": ai_reply})
    except Exception as e:
        print(f"Error calling AI chat API: {e}")
        return jsonify({"reply": f"抱歉，调用AI时遇到错误，请稍后再试。"}), 500

if __name__ == '__main__':
    if not os.path.exists(HK_DATA_FILE_PATH):
        update_hk_data_from_source()
    app.run(debug=True, port=5000)