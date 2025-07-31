// 彩球颜色映射
const RED_BALLS = [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46];
const BLUE_BALLS = [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48];
const GREEN_BALLS = [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49];

// 获取号码颜色
function getBallColorClass(number) {
    const num = parseInt(number);
    if (RED_BALLS.includes(num)) return 'red';
    if (BLUE_BALLS.includes(num)) return 'blue';
    if (GREEN_BALLS.includes(num)) return 'green';
    return '';
}

// 创建彩球元素
function createLotteryBallElement(num, isSpecial = false, zodiac = '') {
    const wrapper = document.createElement('div');
    wrapper.className = 'ball-wrapper';
    wrapper.style.display = 'flex';
    wrapper.style.flexDirection = 'column';
    wrapper.style.alignItems = 'center';
    
    const ball = document.createElement('div');
    ball.className = `lottery-ball ${getBallColorClass(num)}`;
    if (isSpecial) ball.classList.add('special');
    ball.textContent = num;
    
    wrapper.appendChild(ball);
    
    // 显示生肖
    if (zodiac) {
        const zodiacLabel = document.createElement('span');
        zodiacLabel.className = 'zodiac-label';
        zodiacLabel.textContent = zodiac;
        zodiacLabel.style.marginTop = '5px';
        wrapper.appendChild(zodiacLabel);
    }
    
    return wrapper;
}

// 显示开奖记录
function displayDraws(draws) {
    const tableBody = document.getElementById('drawsTableBody');
    tableBody.innerHTML = '';
    
    if (!draws || draws.length === 0) {
        const emptyRow = document.createElement('tr');
        const emptyCell = document.createElement('td');
        emptyCell.colSpan = 5; // 修改为5列，与表头一致
        emptyCell.textContent = '暂无数据';
        emptyCell.style.padding = '30px';
        emptyCell.style.textAlign = 'center';
        emptyRow.appendChild(emptyCell);
        tableBody.appendChild(emptyRow);
        return;
    }
    
    draws.forEach(draw => {
        const row = document.createElement('tr');
        
        // 期数
        const periodCell = document.createElement('td');
        periodCell.textContent = draw.id || '';
        row.appendChild(periodCell);
        
        // 开奖日期
        const dateCell = document.createElement('td');
        if (draw.date) {
            const formattedDate = draw.date.split(' ')[0];
            dateCell.textContent = formattedDate;
        }
        row.appendChild(dateCell);
        
        // 正码
        const normalNumbersCell = row.insertCell();
        const normalNumbersContainer = document.createElement('div');
        normalNumbersContainer.style.cssText = 'display:flex; justify-content:center; align-items:center; flex-wrap:wrap; gap: 5px;';
        
        if (Array.isArray(draw.no)) {
            // 获取生肖数据
            const zodiacs = draw.raw_zodiac ? draw.raw_zodiac.split(',') : [];
            
            draw.no.forEach((num, index) => {
                // 使用生肖数据
                const zodiac = zodiacs[index] || '';
                const numElement = createLotteryBallElement(num, false, zodiac);
                normalNumbersContainer.appendChild(numElement);
            });
        }
        normalNumbersCell.appendChild(normalNumbersContainer);
        
        // 特码
        const specialNumberCell = row.insertCell();
        if (draw.sno) {
            const specialElement = createLotteryBallElement(draw.sno, true, draw.sno_zodiac || '');
            specialNumberCell.appendChild(specialElement);
        }
        
        // 添加详情按钮
        const detailsCell = row.insertCell();
        const detailsBtn = document.createElement('button');
        detailsBtn.className = 'btn btn-sm btn-outline-primary';
        detailsBtn.innerHTML = '<i class="fas fa-info-circle"></i> 详情';
        detailsBtn.style.cssText = 'border-radius: 20px; padding: 5px 15px; font-size: 0.85rem; background: rgba(0, 123, 255, 0.1); border: 1px solid rgba(0, 123, 255, 0.3); color: #007bff;';
        detailsBtn.onclick = () => showDrawDetails(draw);
        detailsCell.appendChild(detailsBtn);
        
        tableBody.appendChild(row);
    });
}

// 显示开奖详情
function showDrawDetails(draw) {
    const modal = document.getElementById('drawDetailsModal');
    const modalTitle = document.getElementById('drawDetailsModalTitle');
    const modalBody = document.getElementById('drawDetailsModalBody');
    
    // 设置标题
    modalTitle.textContent = `第 ${draw.id} 期开奖详情`;
    
    // 清空模态框内容
    modalBody.innerHTML = '';
    
    // 计算总和
    let sum = 0;
    if (Array.isArray(draw.no)) {
        draw.no.forEach(num => {
            sum += parseInt(num);
        });
        if (draw.sno) {
            sum += parseInt(draw.sno);
        }
    }
    
    // 判断单双大小
    const isSumOdd = sum % 2 !== 0; // 单数
    const isSumBig = sum > 175; // 大于175为大
    
    // 特码判断
    const specialNum = parseInt(draw.sno);
    const isSpecialOdd = specialNum % 2 !== 0; // 单数
    const isSpecialBig = specialNum > 24; // 大于24为大
    
    // 特码合数判断（十位数+个位数）
    const specialTens = Math.floor(specialNum / 10);
    const specialOnes = specialNum % 10;
    const specialSum = specialTens + specialOnes;
    const isSpecialSumOdd = specialSum % 2 !== 0; // 合单
    const isSpecialSumBig = specialSum > 6; // 合大
    
    // 特码尾数判断（个位数）
    const isSpecialTailBig = specialOnes > 4; // 尾大
    
    // 创建统计表格 - 先显示统计表格
    const statsContainer = document.createElement('div');
    statsContainer.style.cssText = 'background: rgba(255,255,255,0.8); padding: 20px; border-radius: 15px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); margin-bottom: 20px;';
    
    const statsTable = document.createElement('table');
    statsTable.className = 'table';
    statsTable.style.cssText = 'width: 100%; border-collapse: separate; border-spacing: 0; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);';
    
    // 创建表头行
    const headerRow1 = document.createElement('tr');
    headerRow1.style.cssText = 'background: linear-gradient(135deg, #6a11cb 0%, #2575fc 100%);';
    
    const headerCell1 = document.createElement('th');
    headerCell1.textContent = '总和';
    headerCell1.colSpan = 4;
    headerCell1.style.cssText = 'text-align: center; padding: 12px; color: white; font-weight: 600; border-bottom: 2px solid #e9ecef;';
    headerRow1.appendChild(headerCell1);
    
    const headerCell2 = document.createElement('th');
    headerCell2.textContent = '特码';
    headerCell2.colSpan = 5;
    headerCell2.style.cssText = 'text-align: center; padding: 12px; color: white; font-weight: 600; border-bottom: 2px solid #e9ecef;';
    headerRow1.appendChild(headerCell2);
    
    statsTable.appendChild(headerRow1);
    
    // 创建子表头行
    const headerRow2 = document.createElement('tr');
    headerRow2.style.cssText = 'background: #f8f9fa;';
    
    const headers = ['总数', '单双', '大小', '七色波', '单双', '大小', '合单双', '合大小', '尾大小'];
    headers.forEach(header => {
        const th = document.createElement('th');
        th.textContent = header;
        th.style.cssText = 'text-align: center; padding: 10px; color: #495057; font-weight: 600; border-bottom: 1px solid #e9ecef;';
        headerRow2.appendChild(th);
    });
    statsTable.appendChild(headerRow2);
    
    // 创建数据行
    const dataRow = document.createElement('tr');
    dataRow.style.cssText = 'background: white;';
    
    // 总数
    const sumCell = document.createElement('td');
    sumCell.textContent = sum;
    sumCell.style.cssText = 'text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 600;';
    dataRow.appendChild(sumCell);
    
    // 单双
    const oddEvenCell = document.createElement('td');
    oddEvenCell.textContent = isSumOdd ? '单' : '双';
    oddEvenCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${isSumOdd ? 'red' : 'blue'};`;
    dataRow.appendChild(oddEvenCell);
    
    // 大小
    const bigSmallCell = document.createElement('td');
    bigSmallCell.textContent = isSumBig ? '大' : '小';
    bigSmallCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${isSumBig ? 'red' : 'blue'};`;
    dataRow.appendChild(bigSmallCell);
    
    // 七色波
    const colorCell = document.createElement('td');
    const ballColor = getBallColorClass(draw.sno);
    colorCell.textContent = ballColor === 'red' ? '红' : (ballColor === 'blue' ? '蓝' : '绿');
    colorCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${ballColor === 'red' ? 'red' : (ballColor === 'blue' ? 'blue' : 'green')};`;
    dataRow.appendChild(colorCell);
    
    // 特码单双
    const specialOddEvenCell = document.createElement('td');
    specialOddEvenCell.textContent = isSpecialOdd ? '单' : '双';
    specialOddEvenCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${isSpecialOdd ? 'red' : 'blue'};`;
    dataRow.appendChild(specialOddEvenCell);
    
    // 特码大小
    const specialBigSmallCell = document.createElement('td');
    specialBigSmallCell.textContent = isSpecialBig ? '大' : '小';
    specialBigSmallCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${isSpecialBig ? 'red' : 'blue'};`;
    dataRow.appendChild(specialBigSmallCell);
    
    // 合单双
    const specialSumOddEvenCell = document.createElement('td');
    specialSumOddEvenCell.textContent = isSpecialSumOdd ? '合单' : '合双';
    specialSumOddEvenCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${isSpecialSumOdd ? 'red' : 'blue'};`;
    dataRow.appendChild(specialSumOddEvenCell);
    
    // 合大小
    const specialSumBigSmallCell = document.createElement('td');
    specialSumBigSmallCell.textContent = isSpecialSumBig ? '合大' : '合小';
    specialSumBigSmallCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${isSpecialSumBig ? 'red' : 'blue'};`;
    dataRow.appendChild(specialSumBigSmallCell);
    
    // 尾大小
    const specialTailBigSmallCell = document.createElement('td');
    specialTailBigSmallCell.textContent = isSpecialTailBig ? '尾大' : '尾小';
    specialTailBigSmallCell.style.cssText = `text-align: center; padding: 12px; font-size: 1.1rem; font-weight: 700; color: ${isSpecialTailBig ? 'red' : 'blue'};`;
    dataRow.appendChild(specialTailBigSmallCell);
    
    statsTable.appendChild(dataRow);
    statsContainer.appendChild(statsTable);
    modalBody.appendChild(statsContainer);
    
    // 创建号码容器 - 一排显示所有号码
    const numbersContainer = document.createElement('div');
    numbersContainer.id = 'numbersContainer';
    numbersContainer.style.cssText = 'display: flex; justify-content: center; align-items: center; gap: 10px; background: rgba(255,255,255,0.8); padding: 15px; border-radius: 15px; box-shadow: 0 5px 15px rgba(0,0,0,0.05);';
    
    // 添加平码和特码在同一行
    if (Array.isArray(draw.no)) {
        // 获取生肖数据
        const zodiacs = draw.raw_zodiac ? draw.raw_zodiac.split(',') : [];
        
        // 创建号码容器
        const regularContainer = document.createElement('div');
        regularContainer.style.cssText = 'display: flex; align-items: center; gap: 8px;';
        
        draw.no.forEach((num, index) => {
            // 使用生肖数据
            const zodiac = zodiacs[index] || '';
            
            // 创建号码球和生肖的组合
            const ballWrapper = document.createElement('div');
            ballWrapper.style.cssText = 'display: flex; flex-direction: column; align-items: center; gap: 4px;';
            
            const ballElement = document.createElement('div');
            const ballColor = getBallColorClass(num);
            ballElement.className = `ball ${ballColor}`;
            ballElement.textContent = num;
            ballElement.style.cssText = 'width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-weight: 700; font-size: 1.1rem; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);';
            
            if (ballColor === 'red') {
                ballElement.style.background = 'linear-gradient(135deg, #ff4b4b 0%, #dc3545 100%)';
            } else if (ballColor === 'blue') {
                ballElement.style.background = 'linear-gradient(135deg, #4b83ff 0%, #007bff 100%)';
            } else {
                ballElement.style.background = 'linear-gradient(135deg, #4bff91 0%, #28a745 100%)';
            }
            
            // 添加生肖标签
            const zodiacLabel = document.createElement('div');
            zodiacLabel.textContent = zodiac;
            zodiacLabel.style.cssText = 'font-size: 0.9rem; color: #495057; font-weight: 600;';
            
            ballWrapper.appendChild(ballElement);
            ballWrapper.appendChild(zodiacLabel);
            regularContainer.appendChild(ballWrapper);
        });
        
        numbersContainer.appendChild(regularContainer);
        
        // 添加分隔符
        const separator = document.createElement('div');
        separator.textContent = '+';
        separator.style.cssText = 'font-size: 1.2rem; font-weight: bold; margin: 0 10px; color: #495057;';
        numbersContainer.appendChild(separator);
        
        // 添加特码
        if (draw.sno) {
            const specialWrapper = document.createElement('div');
            specialWrapper.style.cssText = 'position: relative; display: flex; flex-direction: column; align-items: center; gap: 4px;';
            
            // 添加特效光环
            const glowEffect = document.createElement('div');
            glowEffect.style.cssText = 'position: absolute; width: 60px; height: 60px; border-radius: 50%; background: radial-gradient(circle, rgba(255,215,0,0.3) 0%, rgba(255,215,0,0) 70%); z-index: 0; top: 20px; left: 50%; transform: translate(-50%, -50%);';
            specialWrapper.appendChild(glowEffect);
            
            // 创建特码球
            const specialBall = document.createElement('div');
            const specialColor = getBallColorClass(draw.sno);
            specialBall.className = `ball ${specialColor} special`;
            specialBall.textContent = draw.sno;
            specialBall.style.cssText = 'width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-weight: 700; font-size: 1.2rem; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2); border: 2px solid #ffd700; position: relative; z-index: 1;';
            
            if (specialColor === 'red') {
                specialBall.style.background = 'linear-gradient(135deg, #ff4b4b 0%, #dc3545 100%)';
            } else if (specialColor === 'blue') {
                specialBall.style.background = 'linear-gradient(135deg, #4b83ff 0%, #007bff 100%)';
            } else {
                specialBall.style.background = 'linear-gradient(135deg, #4bff91 0%, #28a745 100%)';
            }
            
            // 添加生肖标签
            const specialZodiacLabel = document.createElement('div');
            specialZodiacLabel.textContent = draw.sno_zodiac || '';
            specialZodiacLabel.style.cssText = 'font-size: 0.9rem; color: #495057; font-weight: 600;';
            
            specialWrapper.appendChild(specialBall);
            specialWrapper.appendChild(specialZodiacLabel);
            
            numbersContainer.appendChild(specialWrapper);
        }
    }
    
    modalBody.appendChild(numbersContainer);
    
    // 显示模态框
    modal.style.display = 'flex';
}

// 关闭模态框
function closeModal() {
    const modals = document.querySelectorAll('.modal');
    modals.forEach(modal => {
        modal.style.display = 'none';
    });
}

// 获取开奖记录
function fetchDraws() {
    const loadingIndicator = document.getElementById('loadingIndicator');
    if (loadingIndicator) {
        loadingIndicator.style.display = 'block';
        
        // 3秒后自动隐藏加载指示器（如果API请求时间过长）
        setTimeout(() => {
            if (loadingIndicator.style.display === 'block') {
                loadingIndicator.style.display = 'none';
            }
        }, 3000);
    }
    
    // 获取当前选择的地区和年份
    const region = document.querySelector('.region-btn.active')?.dataset.region || 'macau';
    const year = document.getElementById('yearSelect')?.value || 'all';
    
    console.log(`正在获取开奖记录: 地区=${region}, 年份=${year}`);
    
    // 添加当前时间戳，避免缓存
    const timestamp = new Date().getTime();
    const url = `/api/draws?region=${region}&year=${year}&_=${timestamp}`;
    console.log(`API请求URL: ${url}`);
    
    fetch(url)
        .then(response => {
            console.log(`API响应状态: ${response.status}`);
            if (!response.ok) {
                throw new Error(`HTTP错误! 状态: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            console.log(`获取到${data ? data.length : 0}条开奖记录`);
            console.log('数据示例:', data && data.length > 0 ? data[0] : '无数据');
            displayDraws(data);
            if (loadingIndicator) {
                loadingIndicator.style.display = 'none';
            }
        })
        .catch(error => {
            console.error('获取开奖记录失败:', error);
            if (loadingIndicator) {
                loadingIndicator.style.display = 'none';
            }
            const tableBody = document.getElementById('drawsTableBody');
            if (tableBody) {
                tableBody.innerHTML = '<tr><td colspan="10" style="padding: 30px; text-align: center;">获取数据失败，请稍后再试</td></tr>';
            }
        });
}

// 搜索开奖记录
function searchDraws() {
    const searchTerm = document.getElementById('unifiedSearch')?.value.trim();
    const region = document.querySelector('.region-btn.active').dataset.region;
    const year = document.getElementById('yearSelect').value;
    
    if (!searchTerm) {
        fetchDraws();
        return;
    }
    
    const loadingIndicator = document.getElementById('loadingIndicator');
    if (loadingIndicator) {
        loadingIndicator.style.display = 'block';
        
        // 3秒后自动隐藏加载指示器（如果API请求时间过长）
        setTimeout(() => {
            if (loadingIndicator.style.display === 'block') {
                loadingIndicator.style.display = 'none';
            }
        }, 3000);
    }
    
    // 获取所有开奖记录，然后在前端进行过滤
    fetch(`/api/draws?region=${region}&year=${year}`)
        .then(response => response.json())
        .then(data => {
            // 在前端进行过滤
            const filteredData = filterDraws(data, searchTerm);
            displayDraws(filteredData);
            
            if (loadingIndicator) {
                loadingIndicator.style.display = 'none';
            }
        })
        .catch(error => {
            console.error('搜索开奖记录失败:', error);
            if (loadingIndicator) {
                loadingIndicator.style.display = 'none';
            }
            const tableBody = document.getElementById('drawsTableBody');
            if (tableBody) {
                tableBody.innerHTML = '<tr><td colspan="5" style="padding: 30px; text-align: center;">搜索失败，请稍后再试</td></tr>';
            }
        });
}

// 在前端过滤开奖记录
function filterDraws(draws, searchTerm) {
    if (!searchTerm || !draws || !Array.isArray(draws)) {
        return draws;
    }
    
    // 转换为小写以进行不区分大小写的搜索
    const term = searchTerm.toLowerCase().trim();
    
    // 检查是否是纯数字
    const isNumber = /^\d+$/.test(term);
    const number = isNumber ? parseInt(term) : null;
    
    // 中文生肖列表
    const zodiacs = ['鼠', '牛', '虎', '兔', '龙', '蛇', '马', '羊', '猴', '鸡', '狗', '猪'];
    
    // 检查是否是生肖
    const isZodiac = zodiacs.some(zodiac => term.includes(zodiac));
    
    return draws.filter(draw => {
        // 期数搜索 - 精确匹配期数
        if (draw.id && draw.id.toString() === term) {
            return true;
        }
        
        // 特码号码搜索 - 严格只匹配特码，不匹配平码
        if (isNumber && draw.sno && parseInt(draw.sno) === number) {
            // 确保不会匹配到平码
            return true;
        }
        
        // 特码生肖搜索 - 只匹配特码生肖
        if (isZodiac && draw.sno_zodiac) {
            for (const zodiac of zodiacs) {
                if (term.includes(zodiac) && draw.sno_zodiac === zodiac) {
                    return true;
                }
            }
        }
        
        return false;
    });
}

// 重置搜索
function resetSearch() {
    // 清空搜索框
    if (document.getElementById('unifiedSearch')) {
        document.getElementById('unifiedSearch').value = '';
    }
    
    // 重新获取数据
    fetchDraws();
}

// 初始化页面
document.addEventListener('DOMContentLoaded', function() {
    console.log('页面加载完成，开始初始化...');
    
    // 初始化地区选择
    const regionButtons = document.querySelectorAll('.region-btn');
    console.log(`找到${regionButtons.length}个地区按钮`);
    
    regionButtons.forEach(btn => {
        btn.addEventListener('click', function() {
            console.log(`切换地区: ${this.dataset.region}`);
            regionButtons.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            fetchDraws();
        });
    });
    
    // 初始化年份选择
    const yearSelect = document.getElementById('yearSelect');
    if (yearSelect) {
        console.log('找到年份选择器');
        yearSelect.addEventListener('change', function() {
            console.log(`切换年份: ${this.value}`);
            fetchDraws();
        });
    } else {
        console.warn('未找到年份选择器');
    }
    
    // 初始化搜索
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('keyup', function(event) {
            if (event.key === 'Enter') {
                searchDraws();
            }
        });
    }
    
    const searchButton = document.getElementById('searchButton');
    if (searchButton) {
        searchButton.addEventListener('click', searchDraws);
    }
    
    // 初始化模态框关闭按钮
    const closeButtons = document.querySelectorAll('.close-modal');
    closeButtons.forEach(btn => {
        btn.addEventListener('click', closeModal);
    });
    
    // 点击模态框背景关闭模态框
    const modals = document.querySelectorAll('.modal');
    modals.forEach(modal => {
        modal.addEventListener('click', function(event) {
            if (event.target === this) {
                closeModal();
            }
        });
    });
    
    // 初始化动态背景
    initParticles();
    
    // 加载初始数据
    console.log('开始加载初始数据...');
    fetchDraws();
});

// 初始化动态背景粒子
function initParticles() {
    const particlesContainer = document.getElementById('particles');
    if (!particlesContainer) {
        console.warn('找不到粒子容器元素');
        return;
    }
    
    const particleCount = 20;
    console.log(`正在初始化${particleCount}个背景粒子`);
    
    // 清空现有粒子
    particlesContainer.innerHTML = '';
    
    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        particle.className = 'particle';
        
        // 随机大小
        const size = Math.random() * 20 + 10;
        particle.style.width = `${size}px`;
        particle.style.height = `${size}px`;
        
        // 随机位置
        particle.style.left = `${Math.random() * 100}%`;
        particle.style.top = `${Math.random() * 100}%`;
        
        // 随机动画延迟
        particle.style.animationDelay = `${Math.random() * 5}s`;
        
        // 随机透明度
        particle.style.opacity = Math.random() * 0.3 + 0.1;
        
        particlesContainer.appendChild(particle);
    }
}
