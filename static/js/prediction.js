// 获取预测结果
function getPrediction(strategy) {
    // 显示预测加载指示器
    document.getElementById('predictionIndicator').style.display = 'block';

    // 获取当前选择的地区
    const region = document.querySelector('.region-btn.active').dataset.region;

    // 由于年份选择器已被删除，默认使用'all'
    const year = 'all';

    console.log(`正在获取${strategy}预测结果: 地区=${region}, 年份=${year}`);

    // 发送请求获取预测结果
    fetch(`/api/predict?region=${region}&strategy=${strategy}&year=${year}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP错误! 状态: ${response.status}`);
            }

            // 检查是否是流式响应
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('text/event-stream')) {
                // 处理流式响应（AI预测）
                return handleStreamingResponse(response, strategy);
            } else {
                // 处理普通JSON响应（其他预测）
                return response.json().then(data => {
                    // 隐藏预测加载指示器
                    document.getElementById('predictionIndicator').style.display = 'none';

                    // 调试信息
                    console.log('预测结果数据:', data);

                    // 检查是否有错误信息
                    if (data.error) {
                        throw new Error(data.error);
                    }

                    // 获取生肖数据
                    if (data.normal && data.normal.length > 0) {
                        // 调用API获取生肖数据，确保与开奖记录使用相同的生肖计算逻辑
                        const numbers = [...data.normal];
                        if (data.special && data.special.number) {
                            numbers.push(data.special.number);
                        }

                        // 确保使用与开奖记录相同的生肖计算逻辑
                        // 获取当前选择的地区和年份
                        const selectedRegion = document.querySelector('.region-btn.active').dataset.region;
                        const selectedYear = new Date().getFullYear(); // 使用当前年份

                        return fetch(`/api/get_zodiacs?numbers=${numbers.join(',')}&region=${selectedRegion}&year=${selectedYear}`)
                            .then(response => {
                                if (!response.ok) {
                                    throw new Error(`获取生肖数据失败: ${response.status}`);
                                }
                                return response.json();
                            })
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
                });
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
                    <p>获取预测失败: ${error.message}</p>
                    <button class="modern-btn btn-danger" onclick="location.reload()" style="margin-top: 10px;">
                        <i class="fas fa-sync"></i> 刷新页面
                    </button>
                </div>
            `;
        });
}

// 处理流式响应
function handleStreamingResponse(response, strategy) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';
    let finalResult = null;

    // 显示预测结果容器，准备接收流式内容
    const predictionResult = document.getElementById('predictionResult');
    predictionResult.style.display = 'flex';

    const predictionContent = document.getElementById('predictionContent');

    // 创建流式显示的HTML
    predictionContent.innerHTML = `
        <div style="text-align: center; margin-bottom: 20px;">
            <span style="background: rgba(0, 123, 255, 0.1); color: #007bff; padding: 5px 15px; border-radius: 20px; font-weight: 600;">
                <i class="fas fa-robot"></i> AI智能预测
            </span>
        </div>
        <div id="streamingContent" style="background: rgba(248, 249, 250, 0.7); padding: 15px; border-radius: 10px; border: 1px solid rgba(0, 0, 0, 0.1); min-height: 100px;">
            <p id="streamingText" style="line-height: 1.6; white-space: pre-wrap;"></p>
        </div>
    `;

    const streamingText = document.getElementById('streamingText');

    function read() {
        return reader.read().then(({ done, value }) => {
            if (done) {
                // 流式传输完成，隐藏加载指示器
                document.getElementById('predictionIndicator').style.display = 'none';

                // 如果有最终结果，显示号码
                if (finalResult) {
                    displayFinalResult(finalResult, strategy);
                }
                return;
            }

            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n\n');

            for (const line of lines) {
                if (!line.trim()) continue;

                try {
                    const data = JSON.parse(line);

                    if (data.type === 'content') {
                        // 更新流式文本
                        fullText = data.full_text;
                        streamingText.textContent = fullText;
                        // 自动滚动到底部
                        streamingText.scrollTop = streamingText.scrollHeight;
                    } else if (data.type === 'done') {
                        // 保存最终结果
                        finalResult = data;

                        // 获取生肖数据
                        if (data.normal && data.normal.length > 0) {
                            const numbers = [...data.normal];
                            if (data.special && data.special.number) {
                                numbers.push(data.special.number);
                            }

                            const selectedRegion = document.querySelector('.region-btn.active').dataset.region;
                            const selectedYear = new Date().getFullYear();

                            return fetch(`/api/get_zodiacs?numbers=${numbers.join(',')}&region=${selectedRegion}&year=${selectedYear}`)
                                .then(response => {
                                    if (!response.ok) {
                                        throw new Error(`获取生肖数据失败: ${response.status}`);
                                    }
                                    return response.json();
                                })
                                .then(zodiacData => {
                                    finalResult.normal_zodiacs = zodiacData.normal_zodiacs;
                                    if (finalResult.special) {
                                        finalResult.special.sno_zodiac = zodiacData.special_zodiac;
                                    }
                                })
                                .catch(error => {
                                    console.error('获取生肖数据失败:', error);
                                })
                                .then(() => {
                                    savePredictionRecord(finalResult);
                                    return read();
                                });
                        } else {
                            savePredictionRecord(finalResult);
                            return read();
                        }
                    } else if (data.type === 'error') {
                        // 错误处理
                        document.getElementById('predictionIndicator').style.display = 'none';
                        streamingText.innerHTML = `
                            <div style="background: rgba(220, 53, 69, 0.1); padding: 15px; border-radius: 10px; text-align: center; color: #dc3545;">
                                <i class="fas fa-exclamation-circle" style="font-size: 2rem; margin-bottom: 10px;"></i>
                                <p>${data.error}</p>
                            </div>
                        `;
                        return;
                    }
                } catch (e) {
                    console.error('解析JSON失败:', e, 'Line:', line);
                }
            }

            return read();
        });
    }

    return read();
}

function savePredictionRecord(data) {
    if (!data || !data.period || !data.normal || !data.special || !data.special.number) {
        return;
    }

    const payload = {
        region: data.region || document.querySelector('.region-btn.active')?.dataset.region,
        period: data.period,
        strategy: data.strategy || 'ai',
        normal_numbers: data.normal,
        special_number: data.special.number,
        special_zodiac: data.special.sno_zodiac || '',
        prediction_text: data.recommendation_text || ''
    };

    if (!payload.region) {
        return;
    }

    fetch('/user/save-prediction', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(payload)
    }).catch(() => {});
}

// 显示最终结果（包括号码）
function displayFinalResult(data, strategy) {
    const predictionContent = document.getElementById('predictionContent');

    // 创建预测结果HTML
    let html = `
        <div style="text-align: center; margin-bottom: 20px;">
            <span style="background: rgba(0, 123, 255, 0.1); color: #007bff; padding: 5px 15px; border-radius: 20px; font-weight: 600;">
                <i class="fas fa-robot"></i> AI智能预测
            </span>
        </div>
    `;

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
            <div style="display: flex; align-items: center; justify-content: center; margin-top: 20px; position: relative;">
                <div style="font-size: 1.2rem; font-weight: 700; color: #333; margin-right: 15px;">特码:</div>
                <div style="position: relative; display: flex; flex-direction: column; align-items: center;">
                    <div style="position: absolute; width: 70px; height: 70px; border-radius: 50%; background: radial-gradient(circle, rgba(255,215,0,0.4) 0%, rgba(255,215,0,0) 70%); z-index: 0; top: 20px; left: 50%; transform: translate(-50%, -50%);"></div>
                    <div class="lottery-ball ${colorClass} special" style="width: 50px; height: 50px; font-size: 1.4rem; border: 3px solid #ffd700; margin-bottom: 5px; position: relative; z-index: 1; box-shadow: 0 2px 15px rgba(0, 0, 0, 0.3);">${specialNum}</div>
                    <div style="font-size: 1rem; font-weight: 600; color: #333;">${zodiac}</div>
                </div>
            </div>
        `;
    }

    // 显示AI分析文本
    if (data.recommendation_text) {
        // 添加marked.js库（如果页面中还没有）
        if (!window.marked) {
            const script = document.createElement('script');
            script.src = 'https://fastly.jsdelivr.net/npm/marked/marked.min.js';
            document.head.appendChild(script);

            // 等待脚本加载完成
            script.onload = function() {
                renderMarkdown();
            };
        } else {
            renderMarkdown();
        }

        function renderMarkdown() {
            // 使用marked解析Markdown文本
            const parsedContent = window.marked ? window.marked.parse(data.recommendation_text) : data.recommendation_text;

            html += `
                <div style="margin-top: 20px; text-align: left; background: rgba(248, 249, 250, 0.7); padding: 15px; border-radius: 10px; border: 1px solid rgba(0, 0, 0, 0.1);">
                    <h4 style="margin-bottom: 10px; color: #495057;">AI分析:</h4>
                    <div style="line-height: 1.6;" class="markdown-content">${parsedContent}</div>
                </div>
            `;

            predictionContent.innerHTML = html;

            // 添加Markdown样式
            const style = document.createElement('style');
            style.textContent = `
                .markdown-content h1, .markdown-content h2, .markdown-content h3,
                .markdown-content h4, .markdown-content h5, .markdown-content h6 {
                    margin-top: 1em;
                    margin-bottom: 0.5em;
                    font-weight: 600;
                    color: #333;
                }
                .markdown-content h1 { font-size: 1.8em; }
                .markdown-content h2 { font-size: 1.6em; }
                .markdown-content h3 { font-size: 1.4em; }
                .markdown-content h4 { font-size: 1.2em; }
                .markdown-content h5 { font-size: 1.1em; }
                .markdown-content h6 { font-size: 1em; }
                .markdown-content p { margin-bottom: 1em; }
                .markdown-content strong { font-weight: 700; }
                .markdown-content em { font-style: italic; }
                .markdown-content ul, .markdown-content ol {
                    margin-left: 2em;
                    margin-bottom: 1em;
                }
                .markdown-content li { margin-bottom: 0.5em; }
                .markdown-content code {
                    background-color: rgba(0,0,0,0.05);
                    padding: 0.2em 0.4em;
                    border-radius: 3px;
                    font-family: monospace;
                }
                .markdown-content pre {
                    background-color: rgba(0,0,0,0.05);
                    padding: 1em;
                    border-radius: 5px;
                    overflow-x: auto;
                    margin-bottom: 1em;
                }
                .markdown-content pre code {
                    background-color: transparent;
                    padding: 0;
                }
                .markdown-content blockquote {
                    border-left: 4px solid #ddd;
                    padding-left: 1em;
                    margin-left: 0;
                    color: #666;
                }
                .markdown-content table {
                    border-collapse: collapse;
                    width: 100%;
                    margin-bottom: 1em;
                }
                .markdown-content table th, .markdown-content table td {
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                }
                .markdown-content table th {
                    background-color: rgba(0,0,0,0.05);
                }
            `;
            document.head.appendChild(style);
        }

        return; // 提前返回，因为renderMarkdown会设置innerHTML
    }

    predictionContent.innerHTML = html;
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
        'hot': '热门预测',
        'cold': '冷门预测',
        'trend': '走势预测',
        'hybrid': '综合预测',
        'balanced': '均衡预测',
        'random': '随机预测',
        'ai': 'AI智能预测'
    };
    
    const strategyIcons = {
        'hot': 'fire',
        'cold': 'snowflake',
        'trend': 'chart-line',
        'hybrid': 'sliders-h',
        'balanced': 'balance-scale',
        'random': 'dice',
        'ai': 'robot'
    };
    
    const strategyTitle = strategyTitles[strategy] || '预测';
    const strategyIcon = strategyIcons[strategy] || 'dice';
    
    html += `<div style="text-align: center; margin-bottom: 20px;">
        <span style="background: rgba(0, 123, 255, 0.1); color: #007bff; padding: 5px 15px; border-radius: 20px; font-weight: 600;">
            <i class="fas fa-${strategyIcon}"></i> ${strategyTitle}
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
            <div style="display: flex; align-items: center; justify-content: center; margin-top: 20px; position: relative;">
                <div style="font-size: 1.2rem; font-weight: 700; color: #333; margin-right: 15px;">特码:</div>
                <div style="position: relative; display: flex; flex-direction: column; align-items: center;">
                    <div style="position: absolute; width: 70px; height: 70px; border-radius: 50%; background: radial-gradient(circle, rgba(255,215,0,0.4) 0%, rgba(255,215,0,0) 70%); z-index: 0; top: 20px; left: 50%; transform: translate(-50%, -50%);"></div>
                    <div class="lottery-ball ${colorClass} special" style="width: 50px; height: 50px; font-size: 1.4rem; border: 3px solid #ffd700; margin-bottom: 5px; position: relative; z-index: 1; box-shadow: 0 2px 15px rgba(0, 0, 0, 0.3);">${specialNum}</div>
                    <div style="font-size: 1rem; font-weight: 600; color: #333;">${zodiac}</div>
                </div>
            </div>
        `;
    }
    
    // 显示AI分析文本
    if (data.recommendation_text) {
        // 添加marked.js库（如果页面中还没有）
        if (!window.marked) {
            const script = document.createElement('script');
            script.src = 'https://fastly.jsdelivr.net/npm/marked/marked.min.js';
            document.head.appendChild(script);
            
            // 等待脚本加载完成
            script.onload = function() {
                renderMarkdown();
            };
        } else {
            renderMarkdown();
        }
        
        function renderMarkdown() {
            // 使用marked解析Markdown文本
            const parsedContent = window.marked ? window.marked.parse(data.recommendation_text) : data.recommendation_text;
            
            html += `
                <div style="margin-top: 20px; text-align: left; background: rgba(248, 249, 250, 0.7); padding: 15px; border-radius: 10px; border: 1px solid rgba(0, 0, 0, 0.1);">
                    <h4 style="margin-bottom: 10px; color: #495057;">AI分析:</h4>
                    <div style="line-height: 1.6;" class="markdown-content">${parsedContent}</div>
                </div>
            `;
            
            predictionContent.innerHTML = html;
            
            // 添加Markdown样式
            const style = document.createElement('style');
            style.textContent = `
                .markdown-content h1, .markdown-content h2, .markdown-content h3, 
                .markdown-content h4, .markdown-content h5, .markdown-content h6 {
                    margin-top: 1em;
                    margin-bottom: 0.5em;
                    font-weight: 600;
                    color: #333;
                }
                .markdown-content h1 { font-size: 1.8em; }
                .markdown-content h2 { font-size: 1.6em; }
                .markdown-content h3 { font-size: 1.4em; }
                .markdown-content h4 { font-size: 1.2em; }
                .markdown-content h5 { font-size: 1.1em; }
                .markdown-content h6 { font-size: 1em; }
                .markdown-content p { margin-bottom: 1em; }
                .markdown-content strong { font-weight: 700; }
                .markdown-content em { font-style: italic; }
                .markdown-content ul, .markdown-content ol { 
                    margin-left: 2em; 
                    margin-bottom: 1em;
                }
                .markdown-content li { margin-bottom: 0.5em; }
                .markdown-content code {
                    background-color: rgba(0,0,0,0.05);
                    padding: 0.2em 0.4em;
                    border-radius: 3px;
                    font-family: monospace;
                }
                .markdown-content pre {
                    background-color: rgba(0,0,0,0.05);
                    padding: 1em;
                    border-radius: 5px;
                    overflow-x: auto;
                    margin-bottom: 1em;
                }
                .markdown-content pre code {
                    background-color: transparent;
                    padding: 0;
                }
                .markdown-content blockquote {
                    border-left: 4px solid #ddd;
                    padding-left: 1em;
                    margin-left: 0;
                    color: #666;
                }
                .markdown-content table {
                    border-collapse: collapse;
                    width: 100%;
                    margin-bottom: 1em;
                }
                .markdown-content table th, .markdown-content table td {
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                }
                .markdown-content table th {
                    background-color: rgba(0,0,0,0.05);
                }
            `;
            document.head.appendChild(style);
        }
        
        return; // 提前返回，因为renderMarkdown会设置innerHTML
    }
    
    predictionContent.innerHTML = html;
}

// 清除预测结果
function clearPredictionResult() {
    const predictionResult = document.getElementById('predictionResult');
    if (predictionResult) {
        predictionResult.style.display = 'none';
    }
    
    const predictionContent = document.getElementById('predictionContent');
    if (predictionContent) {
        predictionContent.innerHTML = '';
    }
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

// 在页面加载完成后，为地区按钮添加切换事件监听器
document.addEventListener('DOMContentLoaded', function() {
    // 查找所有地区按钮
    const regionButtons = document.querySelectorAll('.region-btn');
    
    // 为每个地区按钮添加点击事件监听器
    regionButtons.forEach(button => {
        button.addEventListener('click', function() {
            // 清除预测结果
            clearPredictionResult();
        });
    });
});

