# DeepSeek Eyes 👁

**给 DeepSeek 装上免费的眼睛** —— 一个轻量本地代理，让任何 OpenAI 兼容的 Agent
（pi / Hermes / reasonix / Codex / 其他）都能把图片和文档"发"给 DeepSeek。

> Give DeepSeek free eyes: a drop-in OpenAI-compatible proxy that routes images to
> free GLM-4V-Flash vision and documents to MinerU OCR, while every final answer
> still comes from DeepSeek.

## 特性

- 🔌 **即插即用**：Agent 只需填 `base_url` + 一个自设密钥，其余零配置
- 👁 **免费视觉**：GLM-4V-Flash 免费识别截图/照片/代码/UI
- 📄 **文档增强**：表格/扫描件/PDF 自动叠加 MinerU OCR（每日免费 5000 份），输出结构化 Markdown
- 🧠 **思考模式**：透传 DeepSeek 官方 `thinking` 参数，客户端开关可控
- 🚀 **缓存友好**：图片识别结果哈希缓存（同图同文），全力保住 DeepSeek 前缀缓存
- 📊 **自带面板**：缓存命中率、token 用量、省钱统计（Streamlit）
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

## 快速开始（5 分钟）

### 1. 拿代码装依赖

```bash
git clone <your-repo-url> deepseek-eyes && cd deepseek-eyes
pip install -r requirements.txt
```

### 2. 填 Key

```bash
cp .env.example .env   # 然后编辑 .env
```

| Key | 申请地址 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | platform.deepseek.com | 必填，需充值（缓存命中价仅 ¥0.02/百万 token） |
| `ZHIPU_API_KEY` | open.bigmodel.cn | 必填，实名后 GLM-4V-Flash **免费** |
| `MINERU_API_KEY` | mineru.net | 可选，文档 OCR，每日免费 5000 份 |
| `PROXY_API_KEY` | 自己随便定 | Agent 连代理用的密钥，建议 16 位以上 |
| `WORKSPACE_ROOT` | 可选 | Agent 发相对路径时的解析根目录 |

### 3. 启动

```bash
# Linux/macOS
python -m uvicorn proxy:app --host 127.0.0.1 --port 8000

# Windows 也可双击 start-eyes.bat
```

### 4. Agent 接入（通用）

在任何支持自定义 OpenAI 端点的 Agent 里填：

```
Base URL : http://127.0.0.1:8000/v1
API Key  : <你的 PROXY_API_KEY>
Model    : deepseek-chat   （或 deepseek-reasoner）
```

⚠️ 如果 Agent 有"模型能力"设置，**务必开启 Image input / 图像输入**，否则它不会把图片发出来。

### pi 用户参考配置（~/.pi/agent/models.json）

```json
"deepseek-eyes": {
  "baseUrl": "http://127.0.0.1:8000/v1",
  "apiKey": "<PROXY_API_KEY>",
  "api": "openai-completions",
  "models": [{
    "id": "deepseek-chat",
    "name": "DeepSeek Chat + 免费视觉",
    "reasoning": true,
    "input": ["text", "image"],
    "compat": { "thinkingFormat": "deepseek" },
    "cost": { "input": 0.14, "output": 0.28, "cacheRead": 0.003, "cacheWrite": 0 },
    "contextWindow": 1000000,
    "maxTokens": 32768
  }]
}
```

## 三种图片进入方式（代理全部自动处理）

| 方式 | 适用 Agent | 代理行为 |
|---|---|---|
| 消息内嵌图片（image_url） | pi、多数 Agent | 拦截 → GLM 识别 → 换文字 → DeepSeek |
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
| `GET /v1/stats` | 缓存命中与用量统计（JSON） |

## 缓存与成本

DeepSeek 按 64-token 块做前缀缓存，命中价是未命中的 **1/50**。本代理为此做了：

- 图片识别结果按哈希缓存——同一张图永远返回一字不差的文本，前缀不断裂
- 只改含图片的消息，system prompt 与历史原样透传
- 面板三口径（今天/本月/累计）展示命中率与省钱金额：

```bash
streamlit run dashboard.py   # http://localhost:8501
```

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

**Q: 模型 ID 为什么用 `deepseek-chat` 而不是 `deepseek-v4-flash`？**
A: 官方别名，自动跟随模型升级；思考模式用参数可控（真名默认强制思考）。代理也会把不认识的模型名自动纠正为 `deepseek-chat`。

**Q: 用的是 Responses API 吗？**
A: 不是，是兼容性最广的 Chat Completions 格式。

**Q: 支持思考模式吗？**
A: 支持，原样透传官方 `thinking` 参数，客户端可控开关。

**Q: 代理能读我消息里的本地路径，安全吗？**
A: 路径识别只在文件真实存在时生效（即代理与 Agent 同机）。部署到服务器后该功能自动失效，不影响其他能力。

## License

MIT
