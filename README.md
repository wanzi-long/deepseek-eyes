# DeepSeek Eyes

**给 DeepSeek 装上免费的眼睛** —— 一个轻量本地代理，让任何 OpenAI 兼容的 Agent（pi / Harness / reasonix / Codex / 其他）都能把图片和文档"发"给 DeepSeek。

> Give DeepSeek free eyes: a drop-in OpenAI-compatible proxy that routes images to free GLM-4V-Flash vision and documents to MinerU OCR, while every final answer still comes from DeepSeek.

## 特性

- 🔌 **即插即用**：Agent 只需填 `base_url` + 一个自设密钥，其余零配置
- 👁 **免费视觉**：GLM-4V-Flash 免费识别截图/照片/代码/UI
- 📄 **文档增强**：表格/扫描件/PDF 自动叠加 MinerU OCR，输出结构化 Markdown
- 🧠 **思考模式**：透传 DeepSeek 官方 `thinking` 参数，客户端开关可控
- 🚀 **缓存友好**：图片识别结果哈希缓存（同图同文），全力保住 DeepSeek 前缀缓存
- 📊 **内置控制台**：浏览器打开 8000 端口，即可看计费/Token/日志/密钥/启停/接入指南
- 🔑 **首次向导**：第一次启动自动引导填密钥，无需手动改 .env
- 🛡 **安全增强**：密钥脱敏显示、常量时间比较认证、限流、熔断提醒
- 🖥 **多机共享**：Docker 一键部署到服务器，所有设备共用

## 架构

```
Agent ──聊天请求──▶ 本代理 ──┬─ 内嵌图片 ──▶ GLM-4V-Flash（免费）
                            ├─ 文档类图片 ──▶ + MinerU OCR 增援
                            ├─ 文本中的本地文件路径 ──▶ 自动读文件识别
                            │
                            └─ 处理后的纯文字 ──▶ DeepSeek ──▶ 最终回答
```

**最终回答 100% 由 DeepSeek 生成**，GLM/MinerU 只做"图转文字"的搬运工。

## 快速开始

### 1. 装 Python（一次性）

需要 **Python 3.10 或更高版本**。安装时勾选 **"Add Python to PATH"**。

> 本项目是纯 Python 脚本，不打包成 exe，所以需要自己装 Python。

### 2. 拿代码

```bash
git clone https://github.com/wanzi-long/deepseek-eyes.git && cd deepseek-eyes
```

### 3. 启动（首次运行会自动引导）

Windows 用户：**直接双击 `start-eyes.bat`**。它会自动完成以下事情：

1. 检测 Python → 没装则提示你去官网装
2. 检测依赖 → 没装则自动 `pip install`
3. **首次运行向导** → 引导你填密钥（DeepSeek / 智谱 / MinerU，代理密钥可自动生成）
4. 启动服务 + 自动打开浏览器到监控面板

Linux/macOS 用户：

```bash
pip install -r requirements.txt
python run.py   # 首次运行同样会引导填密钥
```

### 4. 密钥说明

| Key | 申请地址 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | platform.deepseek.com | 必填，需充值 |
| `ZHIPU_API_KEY` | open.bigmodel.cn | 必填，实名后 GLM-4V-Flash **免费** |
| `MINERU_API_KEY` | mineru.net | 可选，文档 OCR，每日免费额度 |
| `PROXY_API_KEY` | 自动生成或自己定 | Agent 连代理用的密钥，建议 16 位以上 |

密钥保存在项目目录的 `.env` 文件（已被 `.gitignore` 忽略，不会上传）。也可以在监控面板的「密钥管理」页随时修改，保存后热生效。

## Agent 接入

任何支持自定义 OpenAI 端点的 Agent，填：

```
Base URL : http://127.0.0.1:8000/v1
API Key  : <你的 PROXY_API_KEY>
Model    : deepseek-v4-flash  （或 deepseek-v4-pro）
```

### ⚠️ 重要：必须开启模型的"图片输入"能力

**代理本身能看图**（图片→GLM 识别→转文字→DeepSeek），但**各 Agent 默认认为模型不支持图片**，需要你在 Agent 的模型配置里**声明图片输入能力**，否则 Agent 不会把图片发出来、上传按钮也不会显示。

各 Agent 的配置方式不同，下面给几个常见示例。

#### DeepSeek Harness（dsh）

编辑 `~/.dsh/settings.yaml`，在 provider 的 models 里加 `input: [text, image]`（或 provider 级 `defaultInput: [text, image]`）：

```yaml
llm-pi-ai:
  providers:
    deepseekeyes:
      apiKeyEnv: DEEPSEEKEYES_API_KEY
      api: openai-completions
      baseURL: http://127.0.0.1:8000/v1
      defaultInput: [text, image]   # ← 声明图片输入，GUI 才会开放上传入口
      models:
        - id: deepseek-v4-flash
          contextWindow: 1000000
          maxTokens: 384000
          input: [text, image]      # ← 模型级也要声明
        - id: deepseek-v4-pro
          contextWindow: 1000000
          maxTokens: 384000
          input: [text, image]
```

> 不改这个配置，Harness 会报 `model "..." does not support image input`，且界面上不显示图片上传按钮。

#### pi（~/.pi/agent/models.json）

```json
{
  "baseUrl": "http://127.0.0.1:8000/v1",
  "apiKey": "<PROXY_API_KEY>",
  "api": "openai-completions",
  "models": [{
    "id": "deepseek-v4-flash",
    "name": "DeepSeek + 免费视觉",
    "input": ["text", "image"],
    "contextWindow": 1000000,
    "maxTokens": 384000
  }]
}
```

#### 其他 Agent

找 Agent 设置里的"模型能力 / Image input / 图像输入"选项，开启它。找不到就用 `POST /v1/describe` 兜底通道（见下文）。

## 监控面板

启动后浏览器访问 **http://127.0.0.1:8000**（自动打开），内置 5 个页面：

| 页面 | 功能 |
|---|---|
| 📊 计费与用量 | 费用/Token/缓存命中率/图片次数 + 图表 + CSV 导出 |
| 📜 日志 | 请求/错误/系统日志实时滚动 + 历史回看 |
| ⚡ 服务控制 | 一键启停代理 + 熔断状态 |
| 🔑 密钥管理 | 脱敏显示 + 修改热生效 |
| 🔌 接入指南 | 动态 base_url + 各 agent 配置示例 |

## 三种图片进入方式（代理全部自动处理）

| 方式 | 适用 Agent | 代理行为 |
|---|---|---|
| 消息内嵌图片（image_url） | pi、Harness 等 | 拦截 → GLM 识别 → 换文字 → DeepSeek |
| 文本里的本地文件路径 | reasonix 等只发路径的 | 自动探测路径 → 读文件 → 识别注入 |
| Agent 主动调 `POST /v1/describe` | 装了 skill 的 | 返回识别文字，Agent 自行使用 |

PDF 在任何方式下都会自动走 MinerU。

## 智能路由：GLM 还是 MinerU？

```
图片 → GLM-4V-Flash 识别（免费、秒回）
        │
        ├─ GLM 判定 [TYPE:general] → 直接用 GLM 结果
        ├─ GLM 判定 [TYPE:document] → MinerU 自动增援，双份结果合并
        └─ 用户消息含"提取文字/OCR/转录/识别表格" → 强制双路
```

## API 端点

| 端点 | 说明 |
|---|---|
| `POST /v1/chat/completions` | 主入口（OpenAI 兼容，流式/非流式） |
| `GET /v1/models` | 模型列表 |
| `POST /v1/describe` | 本地图片/文档识别 `{"file_path": "..."}` |
| `POST /v1/ocr` | MinerU 文档解析，返回 Markdown |
| `GET /v1/stats` | 用量统计（JSON，需鉴权） |
| `GET /api/service` | 服务状态 + 熔断信息 |
| `POST /api/service` | 启停代理 `{"action": "start"/"stop"/"toggle"}` |
| `GET /api/logs` | 实时日志 |
| `GET /api/config/masked` | 密钥脱敏查询 |

## 缓存与成本

DeepSeek 按 64-token 块做前缀缓存，命中价是未命中的 **1/50**。本代理为此做了：

- 图片识别结果按哈希缓存——同一张图永远返回一字不差的文本，前缀不断裂
- 只改含图片的消息，system prompt 与历史原样透传
- 监控面板三口径（今天/本月/累计）展示命中率与省钱金额

## 部署到服务器（多设备共享）

```bash
docker compose up -d --build
```

所有设备填 `http://<服务器IP>:8000/v1` + 同一个 `PROXY_API_KEY`。

**上线前必读**：
1. `PROXY_API_KEY` 必须足够复杂——这是三道 key 的唯一闸门
2. 建议加 HTTPS（如 Caddy 反代：`eyes.example.com { reverse_proxy 127.0.0.1:8000 }`）
3. 防火墙不要直接暴露 8000 端口

## FAQ

**Q: 模型 ID 用哪个？**
A: 推荐 `deepseek-v4-flash` / `deepseek-v4-pro`。旧的 `deepseek-chat` / `deepseek-reasoner` 别名仍兼容，代理会自动归一。

**Q: 用的是 Responses API 吗？**
A: 不是，是兼容性最广的 Chat Completions 格式。

**Q: 支持思考模式吗？**
A: 支持，原样透传官方 `thinking` 参数。

**Q: 代理能读我消息里的本地路径，安全吗？**
A: 路径识别只在文件真实存在时生效（即代理与 Agent 同机）。部署到服务器后该功能自动失效。

**Q: 为什么 Agent 收不到图片？**
A: 九成是没在 Agent 的模型配置里声明 `input: [text, image]`。见上文「必须开启模型的图片输入能力」一节。

## License

MIT
