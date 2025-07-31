// 获取预测结果
function getPrediction(strategy) {
    // 显示预测加载指示器
    document.getElementById('predictionIndicator').style.display = 'block';
    
    // 获取当前选择的地区
    const region = document.querySelector('.region-btn.active').dataset.region;
    
    // 获取当前选择的年份
    const year = document.getElementById('yearSelect').value;
    
    console.log(`正在获取${strategy}预测结果: 地区=${region}, 年份=${year}`);
    
    // 发送请求获取预测结果
    fetch(`/api/predict?region=${region}&strategy=${strategy}&year=${year}`)
        .then(response => response.json())
        .then(data => {
            // 隐藏预测加载指示器
            document.getElementById('predictionIndicator').style.display = 'none';
            
            // 调试信息
            console.log('预测结果数据:', data);
            
            // 获取生肖数据
            if (data.normal && data.normal.length > 0) {
                // 调用API获取生肖数据
                fetch(`/api/get_zodiacs?numbers=${data.normal.join(',')},${data.special ? data.special.number : ''}`)
                    .then(response => response.json())
                    .then(zodiacData => {
                        // 添加生肖数据
                        data.normal_zodiacs = zodiacData.normal_zodiacs;
                        if (data.special) {
                            data.special.sno_zodiac = zodiacData.special_zodiac;
                        }
                        
                        // 显示预测结果
                        displayPrediction(data, strategy);
                    })
                    .catch(error => {
                        console.error('获取生肖数据失败:', error);
                        // 即使没有生肖数据，也显示预测结果
                        displayPrediction(data, strategy);
                    });
            } else {
                // 显示预测结果
                displayPrediction(data, strategy);
            }
        })
        .catch(error => {
            console.error('获取预测失败:', error);
            document.getElementById('predictionIndicator').style.display = 'none';
            
            // 显示错误信息
            const predictionResult = document.getElementById('predictionResult');
            predictionResult.style.display = 'flex';
            
            const predictionContent = document.getElementById('predictionContent');
            predictionContent.innerHTML = `
                <div style="background: rgba(220, 53, 69, 0.1); padding: 15px; border-radius: 10px; text-align: center; color: #dc3545;">
                    <i class="fas fa-exclamation-circle" style="font-size: 2rem; margin-bottom: 10px;"></i>
                    <p>获取预测失败，请稍后再试</p>
                </div>
            `;
        });
}

// 显示预测结果
function displayPrediction(data, strategy) {
    const predictionResult = document.getElementById('predictionResult');
    predictionResult.style.display = 'flex';
    
    const predictionContent = document.getElementById('predictionContent');
    
    // 检查是否有错误
    if (data.error) {
        predictionContent.innerHTML = `
            <div style="background: rgba(220, 53, 69, 0.1); padding: 15px; border-radius: 10px; text-align: center; color: #dc3545;">
                <i class="fas fa-exclamation-circle" style="font-size: 2rem; margin-bottom: 10px;"></i>
                <p>${data.error}</p>
            </div>
        `;
        return;
    }
    
    // 创建预测结果HTML
    let html = '';
    
    // 根据策略显示不同的标题
    const strategyTitles = {
        'random': '随机预测',
        'balanced': '均衡预测',
        'ai': 'AI智能预测'
    };
    
    const strategyIcons = {
        'random': 'dice',
        'balanced': 'balance-scale',
        'ai': 'robot'
    };
    
    html += `<div style="text-align: center; margin-bottom: 20px;">
        <span style="background: rgba(0, 123, 255, 0.1); color: #007bff; padding: 5px 15px; border-radius: 20px; font-weight: 600;">
            <i class="fas fa-${strategyIcons[strategy]}"></i> ${strategyTitles[strategy]}
        </span>
    </div>`;
    
    // 显示平码
    if (data.normal && data.normal.length > 0) {
        html += '<div style="display: flex; justify-content: center; flex-wrap: wrap; gap: 15px; margin-bottom: 20px;">';
        
        // 获取生肖数据
        const zodiacs = data.normal_zodiacs || [];
        
        data.normal.forEach((num, index) => {
            const colorClass = getBallColorClass(num);
            const zodiac = zodiacs[index] || '';
            
            html += `
                <div style="display: flex; flex-direction: column; align-items: center;">
                    <div class="lottery-ball ${colorClass}" style="margin-bottom: 5px;">${num}</div>
                    <div style="font-size: 0.9rem; font-weight: 600; color: #495057;">${zodiac}</div>
                </div>
            `;
        });
        
        html += '</div>';
    }
    
    // 显示特码
    if (data.special && data.special.number) {
        const specialNum = data.special.number;
        const colorClass = getBallColorClass(specialNum);
        const zodiac = data.special.sno_zodiac || '';
        
        html += `
            <div style="display: flex; flex-direction: column; align-items: center; margin-top: 10px; position: relative;">
                <div style="position: absolute; width: 60px; height: 60px; border-radius: 50%; background: radial-gradient(circle, rgba(255,215,0,0.3) 0%, rgba(255,215,0,0) 70%); z-index: 0;"></div>
                <div class="lottery-ball ${colorClass} special" style="margin-bottom: 5px; position: relative; z-index: 1;">${specialNum}</div>
                <div style="font-size: 0.9rem; font-weight: 600; color: #495057;">${zodiac}</div>
            </div>
        `;
    }
    
    // 显示AI分析文本
    if (data.recommendation_text) {
        html += `
            <div style="margin-top: 20px; text-align: left; background: rgba(248, 249, 250, 0.7); padding: 15px; border-radius: 10px; border: 1px solid rgba(0, 0, 0, 0.1);">
                <h4 style="margin-bottom: 10px; color: #495057;">AI分析:</h4>
                <p style="white-space: pre-line; line-height: 1.6;">${data.recommendation_text}</p>
            </div>
        `;
    }
    
    predictionContent.innerHTML = html;
}

// 获取球的颜色类
function getBallColorClass(number) {
    const num = parseInt(number);
    
    // 红波
    const redBalls = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46];
    if (redBalls.includes(num)) return 'red';
    
    // 蓝波
    const blueBalls = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48];
    if (blueBalls.includes(num)) return 'blue';
    
    // 绿波
    const greenBalls = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49];
    if (greenBalls.includes(num)) return 'green';
    
    return '';
}