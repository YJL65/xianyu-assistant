# 闲鱼服务咨询助手

这是一个本地运行的闲鱼咨询自动接待助手。它会接收买家消息，自动做首轮回复，继续把需求问清楚；当信息已经足够时，会把卖家摘要发到飞书群。项目支持 `mock` 本地调试，也支持接入真实闲鱼消息连接器。

## 现在能做什么

- 接入真实闲鱼消息，监听买家咨询。
- 运行本地 `mock` 对话，方便调试回复逻辑。
- 使用 OpenAI 兼容接口生成自然回复。
- 需求收集只重点确认两件事：
  - 文件或任务类型是什么。
  - 有没有资料、原文、参考文件或提示词。
- 不会继续追问预算、时间、交付方式、页数等非必要细节，除非买家主动提起。
- 当模型判断需求已经基本问清时，会把总结通过飞书通知卖家。
- 如果没有配置模型，或者模型调用失败，会回退到固定兜底问句。
- 支持 SQLite 会话存储、重复消息过滤和历史上下文恢复。
- 支持定期清理过期消息和调试文件。

## 人工接管

有，且现在仍然保留。

- 卖家在闲鱼里手动回复后，系统会把该会话标记为人工接管。
- 一旦进入人工接管，后续买家消息不会继续自动回复。
- 如果需要恢复自动回复，可以执行 `cleanup --clear-states`，或者清空对应会话状态。

## 工作方式

1. 监听买家消息。
2. 根据最近对话和商品信息生成回复。
3. 只围绕“文件类型”和“资料/提示词”做最小必要追问。
4. 当信息足够时，生成给卖家的摘要并发飞书。
5. 记录消息、状态和去重信息，方便后续继续接话。

## 项目结构

```text
app/
  cli.py                 命令行入口
  config.py              环境变量与运行配置
  runner.py              消息主循环、AI 回复、飞书通知、人工接管
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

真实闲鱼连接器依赖本地 `vendor/XianYuApis` 目录。该目录默认不提交到 GitHub，请按团队或个人方式放置依赖代码，并保持 `.env.local` 中的 `XIANYU_VENDOR_PATH` 指向该目录。`mock` 模式不需要该目录。

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
- `FEISHU_WEBHOOK_SECRET` 可以为空；飞书机器人未开启签名校验时不需要填写。
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
- 自动回复只适合前置咨询和信息收集；涉及价格、交付承诺、争议、违规或敏感内容时，建议人工确认。
- 卖家手动回复会触发人工接管，后续买家消息不会继续自动回复。
