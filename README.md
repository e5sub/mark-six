# AI数据分析预测系统

一个基于 Flask 的数据分析与预测平台，集成用户系统、激活码机制、管理员后台、历史数据分析、AI 预测和移动端接口。

## 许可协议

本项目采用 `PolyForm Noncommercial License 1.0.0`。

- 允许查看源代码
- 允许复制、分发和修改代码
- 仅限个人学习、研究、测试和其他非商业用途
- 禁止将本项目或其修改版本用于商业用途

正式协议文本见 [LICENSE](./LICENSE)，中文说明见 [LICENSE.zh-CN.md](./LICENSE.zh-CN.md)。

## 主要功能

### 用户与权限

- 用户注册、登录、找回密码
- 激活码开通与有效期控制
- 可选邮箱验证
- 管理员后台用户管理

### 数据分析

- 支持香港与澳门两类地区数据
- 历史开奖记录查询与展示
- 号码、生肖、波色等多维统计
- 用户维度的数据分析视图

### 智能预测

- 多种预测策略
- AI 智能分析与推荐
- 预测记录自动保存
- 命中结果与准确率统计

### 管理后台

- 激活码管理
- 激活码申请处理
- 系统配置管理
- 数据与预测统计

### 移动端支持

- 提供移动端 API
- 支持移动端登录、注册、激活和申请激活码

## 技术栈

### 后端

- Flask
- SQLAlchemy
- SQLite
- APScheduler

### 前端

- HTML5 / CSS3
- JavaScript
- Chart.js
- Font Awesome

## 安装与运行

### 环境要求

- Python 3.7+
- pip

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

## 默认账号

- 用户名：`admin`
- 密码：`admin123`

首次登录后请立即修改密码。

## 系统配置

管理员后台可配置以下内容：

### AI 配置

- `ai_api_key`
- `ai_api_url`
- `ai_model`

### 邮件配置

- `smtp_server`
- `smtp_port`
- `smtp_username`
- `smtp_password`

### 激活申请通知

- `activation_request_notify_emails`

说明：

- 可填写一个或多个邮箱，多个邮箱用英文逗号分隔
- 用户提交激活码申请后，系统会发送通知邮件
- 若该项留空，则默认通知所有管理员账号邮箱

### 系统展示配置

- `system_name`
- `system_description`
- `site_name`
- `site_description`

说明：

- 页面标题默认以后台设置为准
- 后台页面优先使用 `system_name`
- 前台页面优先使用 `site_name`

### 业务开关

- `allow_registration`
- `require_email_verification`
- `enable_personalized_predictions`

## 使用流程

### 普通用户

1. 注册账号
2. 登录系统
3. 输入激活码，或提交激活码申请
4. 查看历史数据并使用预测功能
5. 在个人中心查看预测记录与统计

### 管理员

1. 登录后台
2. 配置 AI、SMTP 和系统展示信息
3. 处理激活码申请
4. 生成或发放激活码
5. 查看用户、数据和预测统计

## 数据说明

- 项目会优先从数据库读取开奖记录
- 当本地无数据时，会按既有逻辑从远程数据源同步
- 同步后的记录会写入数据库表 `lottery_draws`
- 系统包含定时更新机制

## 目录结构

```text
├── app.py
├── models.py
├── auth.py
├── admin.py
├── user.py
├── api_mobile.py
├── activation_code_routes.py
├── run.py
├── requirements.txt
├── templates/
├── static/
├── mobile/
└── data/
```

## 注意事项

1. 首次运行会自动创建数据库和默认管理员账号
2. 使用 AI 相关功能前，需要先配置有效的 AI 接口信息
3. 使用邮件功能前，需要先配置可用的 SMTP 服务
4. 生产环境请使用安全的 `SECRET_KEY`
5. 当前仓库中仍保留 `mark-six` 作为项目标识与源码链接

## 免责声明

本系统仅供学习和研究使用，分析与预测结果仅供参考，不构成任何投资建议。请理性使用。
