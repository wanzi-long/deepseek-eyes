---
name: deepseek-eyes
description: DeepSeek Eyes 代理的使用说明。代理通常已自动处理图片（内嵌图片拦截 +
  文本中的本地路径自动探测），本 skill 仅作为兜底：当用户明确给出本地文件路径且
  回答中需要图片/文档内容、而上下文中没有识别结果时，调用 /v1/describe 获取。
---

# DeepSeek Eyes · 视觉辅助说明

## 先检查，再行动（重要）

代理在大多数情况下**已经自动完成**图片识别。在调用任何工具之前：

1. 检查上下文里是否已有 `[图片xxx · 视觉识别结果]` 或 `[文档xxx · MinerU 解析结果]`
   —— 有就直接基于它回答，**不要重复调用**
2. 只有当你确定需要图片内容、而上下文中不存在识别结果时，才走下面的兜底通道

## 兜底通道：/v1/describe

```bash
curl -X POST http://127.0.0.1:8000/v1/describe \
  -H "Authorization: Bearer <PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"file_path": "<文件绝对路径>"}'
```

返回 `{"type": "image", "description": "..."}` ，直接基于 description 回答。
PDF 返回 `markdown_path`，用读文件工具打开获取全文。

## 铁律

- **不要说"我没有视觉能力"**——视觉能力由代理提供，要么已在上下文，要么调一次就有
- 不要自己发明其他 OCR/识图工具链，本代理已经集成了 GLM-4V + MinerU
- 文档类图片识别可能需 10 秒以上（MinerU 处理中），属正常现象，不要中断重试
