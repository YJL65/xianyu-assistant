# 闲鱼服务咨询助手

这是一个本地运行的闲鱼服务咨询助手，用于接收买家咨询、自动进行初步回复、记录对话上下文，并在需要人工处理时通过飞书群机器人通知卖家。项目支持 mock 模式做本地验证，也支持接入真实闲鱼消息连接器。

## 主要功能

- 闲鱼消息接入：通过 `app/connectors/xianyu.py` 监听真实闲鱼买家消息，并从商品信息中提取咨询上下文。
- 本地模拟调试：通过 `MockConnector` 模拟买家咨询流程，不需要登录闲鱼即可验证回复逻辑。
- AI 客服回复：支持 OpenAI 兼容接口，根据商品标题、描述和最近对话生成自然回复。
- 规则兜底回复：未配置模型时仍可运行，默认使用固定问询话术。
- 飞书通知：首次 AI 对话、客户服务摘要等信息可发送到飞书群机器人。
- 人工接管：检测到卖家手动回复后暂停自动回复，避免机器人和人工同时对买家发消息。
- 对话存储：使用 SQLite 记录消息和会话状态，支持重复消息过滤和历史上下文恢复。
- 数据清理：定期清理过期消息、调试文件和闲鱼抓包快照，避免本地数据持续膨胀。
- 真实/模拟连接器解耦：核心逻辑只依赖统一的 `Connector` 接口，便于后续扩展到其他平台。

## 项目结构

```text
app/
  cli.py                 命令行入口
  config.py              环境变量与运行配置
  runner.py              消息主循环、AI 回复、通知与人工接管
  llm.py                 OpenAI 兼容接口封装
  feishu.py              飞书群机器人通知
  storage.py             SQLite 存储
  cleanup.py             数据与调试文件清理
  connectors/
    base.py              连接器抽象接口
    mock.py              本地模拟连接器
    xianyu.py            真实闲鱼连接器适配层
scripts/                 常用 PowerShell 启动脚本
tests/                   单元测试
requirements.txt         Python 依赖
start_xianyu.bat         Windows 一键启动真实闲鱼监听
```

## 环境准备

建议使用 Python 3.9 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

真实闲鱼连接器依赖本地 `vendor/XianYuApis` 目录。该目录默认不提交到 GitHub，请按团队或个人的方式放置依赖代码，并保持 `.env.local` 中的 `XIANYU_VENDOR_PATH` 指向该目录。mock 模式不需要该目录。

## 配置说明

在项目根目录创建 `.env.local`，按需填写：

```text
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook
FEISHU_WEBHOOK_SECRET=your_feishu_secret

OPENAI_BASE_URL=https://your-provider.example/v1
OPENAI_API_KEY=your_api_key
OPENAI_MODEL=your_model

APP_DB_PATH=data/assistant.sqlite3
APP_LOG_PATH=logs/assistant.log
APP_MODE=mock

XIANYU_COOKIE=your_goofish_cookie
XIANYU_VENDOR_PATH=vendor/XianYuApis
APP_NODE_EXE=
```

说明：

- `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL` 为空时，系统会使用固定兜底回复。
- `FEISHU_WEBHOOK_SECRET` 可为空；飞书机器人没有开启签名校验时不需要填写。
- `.env.local`、`data/`、`logs/`、SQLite 数据库和 vendor 目录都已加入 `.gitignore`，不会默认上传。

## 常用命令

测试飞书通知：

```powershell
.\.venv\Scripts\python -m app test-feishu
```

运行本地 mock 对话：

```powershell
.\.venv\Scripts\python -m app mock
```

登录闲鱼并保存 Cookie：

```powershell
.\.venv\Scripts\python -m app xianyu-login
```

检查闲鱼 Cookie 是否包含必要字段：

```powershell
.\.venv\Scripts\python -m app check-xianyu-cookie
```

启动真实闲鱼监听：

```powershell
.\.venv\Scripts\python -m app run --connector xianyu
```

也可以使用脚本：

```powershell
.\scripts\run_mock.ps1
.\scripts\run_xianyu.ps1
.\start_xianyu.bat
```

## 数据清理

默认策略会清理超过 3 天的消息记录和调试文件。手动清理命令：

```powershell
.\.venv\Scripts\python -m app cleanup
```

仅预览将被清理的内容：

```powershell
.\.venv\Scripts\python -m app cleanup --dry-run
```

清理全部消息并同时清空会话状态：

```powershell
.\.venv\Scripts\python -m app cleanup --all-messages --clear-states
```

PowerShell 脚本形式：

```powershell
.\scripts\cleanup_data.ps1 -DryRun
.\scripts\cleanup_data.ps1 -AllMessages -ClearStates
```

## 测试

```powershell
.\.venv\Scripts\python -m unittest discover
```

测试覆盖飞书签名、需求抽取兜底逻辑、可用性问答、消息主循环、人工接管、重复消息过滤和数据清理。

## 安全与使用注意

- 不要提交 `.env.local`、Cookie、飞书 Webhook、模型 API Key、SQLite 数据库或真实买家聊天记录。
- 真实闲鱼连接器基于非官方接口适配，接口变动、登录失效或平台策略调整都可能导致不可用。
- 自动回复应只做前置咨询和信息收集，涉及价格、交付承诺、争议、违规或敏感内容时建议人工确认。
- 卖家手动回复会触发人工接管，后续买家消息不会继续自动回复。

