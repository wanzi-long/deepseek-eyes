"""GLM-4V-Flash 免费视觉识别（智谱开放平台，OpenAI 兼容格式）"""
import httpx

import config

URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 这个 prompt 针对编程助手场景做了优化，保持稳定不要改（不影响 DeepSeek 缓存，
# 但改它会降低识别结果的一致性）
PROMPT = (
    "第一步：判断图片类型。如果图片以密集文字 / 表格 / 公式 / 文档版面为主"
    "（如扫描件、合同、论文、报表、发票），第一行只输出 [TYPE:document]；"
    "其他所有情况（代码截图、报错、UI、照片、架构图等）第一行只输出 [TYPE:general]。\n"
    "第二步：换行后输出识别结果（不得为空，即使图片很简单也要描述，\n"
    "例如纯色图就写「图片为纯 X 色画面，无其他内容」）：\n"
    "- 如果是代码截图：完整转录全部代码，注明语言\n"
    "- 如果是报错/终端：完整转录文字，并单独指出错误信息所在行\n"
    "- 如果是文档/表格：转录为 Markdown（保留表格结构）\n"
    "- 如果是 UI/设计稿/架构图：描述布局、组件层级和关键文字\n"
    "- 其他图片：客观描述内容\n"
    "只输出类型标记和识别结果本身，不要评论、不要寒暄。"
)


async def describe_image(image_url: str) -> str:
    """image_url 支持 http(s) 链接和 data:image/...;base64,... 两种形式"""
    if not config.ZHIPU_API_KEY:
        return "[视觉模块未配置 ZHIPU_API_KEY，无法识别图片，请直接描述图片内容]"

    payload = {
        "model": "glm-4v-flash",
        "max_tokens": 1024,  # GLM-4V-Flash 免费版硬上限就是 1024，超过会报 400（错误码 1210）
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    }

    last_err = None
    for _ in range(2):  # 免费版有 QPS 限制，失败重试一次
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    URL,
                    headers={"Authorization": f"Bearer {config.ZHIPU_API_KEY}"},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last_err = e
    return f"[图片识别失败: {last_err}，请用户改用文字描述或稍后重试]"
