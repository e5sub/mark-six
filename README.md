# 彩研所 - AI 数据分析预测系统

一个基于 Flask 的香港、澳门六合彩数据分析与智能预测平台，包含用户系统、激活码机制、管理后台、历史开奖数据、策略预测、自动学习、通知推送和移动端 Flutter 应用。

## 许可协议

本项目采用 `PolyForm Noncommercial License 1.0.0`。

- 允许查看、复制、分发和修改代码
- 仅限个人学习、研究、测试和其他非商业用途
- 禁止将本项目或其修改版本用于商业用途

正式协议见 [LICENSE](./LICENSE)，中文说明见 [LICENSE.zh-CN.md](./LICENSE.zh-CN.md)。

## 主要功能

### 用户与权限

- 用户注册、登录、修改密码
- 激活码开通与有效期控制
- 激活码申请与管理员发放
- 可选 GitHub 登录、Turnstile 人机验证
- 管理员后台用户管理

### 数据分析

- 支持香港、澳门开奖记录
- 历史开奖数据查询与展示
- 号码、生肖、波色、单双等多维统计
- 用户维度预测记录和准确率统计
- 回测快照与策略表现排行

### 智能预测

- 多策略预测：热门、冷门、走势、综合、均衡、马尔科夫、机器学习、AI
- 预测记录自动保存
- 特码命中、平码/生肖命中统计
- 支持同一期多策略预测汇总
- 可选按用户差异化预测，关闭后同一期同策略使用统一号码

### 自动学习与优化

- 马尔科夫策略会学习转移概率、二阶转移、阶段转移、属性转移、失败反馈等信号
- 机器学习策略会参考其他本地策略近期表现，动态调整特征画像、运行画像、融合比例和集成偏好
- 新增自适应学习强度：
  - `responsive`：近期表现弱或样本不足时，提高近期权重并降低学习门槛
  - `balanced`：默认均衡模式
  - `conservative`：近期表现稳定时收紧学习强度，降低短期波动影响
- 管理后台支持策略自动优化，可按历史模拟回测择优调整参数
- 自动优化支持轻度、均衡、积极三档，并可配置最小提升阈值

### 通知与推送

- 站内通知中心
- 邮件通知
- Webhook、Telegram Bot、PushPlus、Bark 推送
- 预测汇总和命中通知支持球形号码展示
- 球形号码包含数字、生肖、红/绿/蓝波属性，站内通知和邮件样式同步

### 管理后台

- 激活码管理
- 激活码申请处理
- 系统配置管理
- 策略学习面板
- 预测记录和数据统计
- 自动优化开关与参数配置

### 移动端支持

- 提供移动端 API
- Flutter 移动端位于 `mobile/mark_six`
- 支持移动端登录、注册、激活、预测、预测记录、统计和通知相关数据

## 技术栈

### 后端

- Flask
- SQLAlchemy
- SQLite / MySQL / MariaDB
- APScheduler
- Requests

### 前端

- HTML5 / CSS3
- JavaScript
- Chart.js
- Font Awesome

### 移动端

- Flutter
- Dio
- Shared Preferences

## 安装与运行

### 环境要求

- Python 3.7+
- pip
- 可选：MySQL 或 MariaDB

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动项目

```bash
python run.py
```

启动后访问：

```text
http://localhost:5000
```

## 数据库

默认使用 SQLite：

```text
data/lottery_system.db
```

也可以通过环境变量使用 MySQL/MariaDB：

```bash
set DB_TYPE=mysql
set DB_HOST=localhost
set DB_PORT=3306
set DB_NAME=mark_six
set DB_USER=root
set DB_PASSWORD=your_password
```

或直接配置：

```bash
set DATABASE_URL=mysql+pymysql://user:password@host:3306/mark_six?charset=utf8mb4
```

更新已有数据库结构：

```bash
python auto_update_db.py
```

## 默认账号

首次部署时，第一个注册用户会自动成为管理员。

首次登录后请立即修改密码，并在后台配置必要的 AI、SMTP 和系统信息。

## 系统配置

### AI 配置

- `ai_api_key`
- `ai_api_url`
- `ai_model`

### 邮件配置

- `smtp_server`
- `smtp_port`
- `smtp_username`
- `smtp_password`
- `notify_email_enabled`

### 激活申请通知

- `activation_request_notify_emails`

说明：

- 可填写一个或多个邮箱，多个邮箱使用英文逗号分隔
- 用户提交激活码申请后，系统会发送通知邮件
- 留空时默认通知所有管理员账号邮箱

### 系统展示配置

- `system_name`
- `system_description`
- `site_name`
- `site_description`
- `seo_title`
- `seo_description`

### 业务开关

- `allow_registration`
- `require_email_verification`
- `enable_turnstile`
- `enable_github_login`
- `enable_personalized_predictions`
- `auto_optimize_enabled`
- `auto_optimize_level`
- `auto_optimize_min_gain`

## 使用流程

### 普通用户

1. 注册账号
2. 登录系统
3. 输入激活码，或提交激活码申请
4. 查看历史开奖数据
5. 使用号码预测功能
6. 在个人中心查看预测记录、准确率和通知

### 管理员

1. 登录后台
2. 配置 AI、SMTP 和系统展示信息
3. 处理激活码申请
4. 生成或发放激活码
5. 查看用户、数据、预测记录和策略统计
6. 按需要开启策略自动优化

## 定时任务

系统默认启用 APScheduler：

- 每天 21:40 自动更新开奖记录
- 每天 22:05 执行策略自动优化任务

可通过环境变量关闭：

```bash
set ENABLE_SCHEDULER=0
```

## 目录结构

```text
├── app.py
├── models.py
├── auth.py
├── admin.py
├── user.py
├── api_mobile.py
├── notification_service.py
├── activation_code_routes.py
├── invite_routes.py
├── auto_update_db.py
├── create_db.py
├── run.py
├── requirements.txt
├── templates/
├── static/
├── mobile/
└── data/
```

## 注意事项

1. 首次运行会自动创建数据库；第一个注册用户会自动成为管理员。
2. 使用 AI 相关功能前，需要先配置有效的 AI 接口信息。
3. 使用邮件通知前，需要先配置可用的 SMTP 服务。
4. 生产环境请使用安全的 `SECRET_KEY`。
5. 新版预测汇总通知的球形号码样式只影响新生成的通知，旧通知不会自动转换。
6. 如果调整数据库结构后部署到已有环境，请执行 `python auto_update_db.py`。
7. 如需通过 Python 重置管理员密码，可使用：

```bash
python reset_admin.py --username 你的管理员用户名 --password 新密码
```

## 免责声明

本系统仅供学习、研究和数据分析参考使用。任何预测结果都不构成投资、投注或其他决策建议。请理性使用。
