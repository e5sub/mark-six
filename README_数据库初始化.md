# 数据库初始化说明

本文档提供了如何初始化六合彩数据分析系统数据库的说明。

## 方法一：使用预初始化脚本（推荐）

这种方法会直接创建一个完整的数据库文件，包含所有必要的表和初始数据。

### Windows 环境

1. 双击运行 `create_db.bat` 文件
2. 等待脚本执行完成，会显示成功信息

### Linux/Mac 环境

1. 打开终端，进入项目目录
2. 执行以下命令：
   ```bash
   chmod +x create_db.sh
   ./create_db.sh
   ```

## 方法二：使用初始化脚本

这种方法会通过 Flask 应用程序的上下文来初始化数据库。

### Windows 环境

1. 双击运行 `init_db.bat` 文件
2. 等待脚本执行完成，会显示成功信息

### Linux/Mac 环境

1. 打开终端，进入项目目录
2. 执行以下命令：
   ```bash
   chmod +x init_db.sh
   ./init_db.sh
   ```

## 方法三：使用 Docker 部署

如果你使用 Docker 部署，修改后的 `entrypoint.sh` 会自动检查并初始化数据库。

1. 确保 `data` 目录存在并有正确的权限
2. 执行以下命令构建并启动容器：
   ```bash
   docker-compose build
   docker-compose up -d
   ```

## 默认管理员账号

初始化后，系统会创建一个默认的管理员账号：

- 用户名：admin
- 密码：admin123

**重要提示：** 请在首次登录后立即修改管理员密码！

## 故障排除

如果遇到 "Internal Server Error" 错误，可能是数据库初始化不完整或者权限问题。请尝试以下步骤：

1. 确保 `data` 目录存在并有写入权限
2. 删除现有的 `data/lottery_system.db` 文件（如果存在）
3. 重新运行初始化脚本
4. 检查应用程序日志以获取更多信息