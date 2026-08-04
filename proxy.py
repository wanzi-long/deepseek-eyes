"""DeepSeek Eyes 代理主服务
客户端 → 本代理 →（有图先问 GLM-4V-Flash）→ DeepSeek
同时把每次请求的缓存命中数据写进 SQLite 供面板使用。
"""
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import db
from config import (DEEPSEEK_API_KEY, DEEPSEEK_BASE, MINERU_API_KEY,
                    PROXY_API_KEY, WORKSPACE_ROOT)
from ocr import materialize_image, ocr_file
from vision import describe_image

app = FastAPI(title="DeepSeek Eyes")
db.init()


def check_auth(req: Request):
    if PROXY_API_KEY and req.headers.get("authorization") != f"Bearer {PROXY_API_KEY}":
        raise HTTPException(401, "invalid proxy api key")


# 匹配消息文本里的本地文件路径（Windows 绝对路径 / 相对路径 / ~ 路径）
PATH_RE = re.compile(
    r"((?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|~?[\\/])[\w\-./\\\u4e00-\u9fff]+?\.(?:png|jpe?g|webp|gif|bmp|pdf|tiff?))",
    re.IGNORECASE,
)


def resolve_path(raw: str):
    """相对路径依次按代理 cwd 和 WORKSPACE_ROOT 解析"""
    candidates = [Path(raw)]
    if not Path(raw).is_absolute() and WORKSPACE_ROOT:
        candidates.append(Path(WORKSPACE_ROOT) / raw)
    for c in candidates:
        if c.exists():
            return c
    return None


async def process_text_paths(messages: list) -> int:
    """扫描最后一条 user 消息文本中的本地文件路径，自动识别并注入结果。
    解决不支持图片内联的 Agent（只发文件路径）场景，让代理把活全干完。
    仅当代理与 Agent 运行在同一台机器时有效（文件要真实存在）。"""
    count = 0
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            content = [{"type": "text", "text": content or ""}]
            msg["content"] = content
        text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        force_ocr = user_wants_ocr(messages)
        for raw in dict.fromkeys(PATH_RE.findall(text)):  # 去重保序
            p = resolve_path(raw)
            if not p:
                continue
            count += 1
            if p.suffix.lower() == ".pdf":
                try:
                    md = await ocr_file(str(p))
                    content.append({"type": "text",
                                    "text": f"\n[文档 {p.name} · MinerU 解析结果]\n{md}\n"})
                except Exception as e:  # noqa: BLE001
                    content.append({"type": "text",
                                    "text": f"\n[文档 {p.name} 解析失败: {e}]\n"})
            else:
                mime = mimetypes.guess_type(str(p))[0] or "image/png"
                data_url = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
                desc = await handle_one_image(data_url, force_ocr)
                content.append({"type": "text",
                                "text": f"\n[图片 {p.name} · 视觉识别结果]\n{desc}\n"})
        break  # 只处理最后一条 user 消息
    return count


# 用户消息里出现这些词时，跳过 GLM 分类直接调 MinerU（第 2 层路由）
INTENT_KEYWORDS = ("ocr", "提取文字", "识别文字", "转录", "逐字",
                   "提取表格", "识别表格", "这份文档", "文档内容")


def user_wants_ocr(messages: list) -> bool:
    """看最后一条 user 消息的文本部分有没有 OCR 意图关键词"""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text").lower()
        return any(k in text for k in INTENT_KEYWORDS)
    return False


async def handle_one_image(url: str, force_ocr: bool) -> str:
    """路由一张图片：GLM-4V-Flash 识别 + 分类；判定为文档则 MinerU 自动增援。
    同一图片命中本地缓存时直接返回上次的文本——保证 DeepSeek 前缀逐字不变。"""
    img_hash = hashlib.sha256((str(force_ocr) + url).encode()).hexdigest()
    cached = db.get_image_cache(img_hash)
    if cached is not None:
        return cached

    desc = await describe_image(url)

    is_doc = desc.lstrip().startswith("[TYPE:document]")
    # 把分类标记从结果里剥掉，不发给 DeepSeek
    for tag in ("[TYPE:document]", "[TYPE:general]"):
        if desc.lstrip().startswith(tag):
            desc = desc.lstrip()[len(tag):].lstrip()
            break

    need_ocr = is_doc or force_ocr
    if need_ocr and MINERU_API_KEY:
        path = await materialize_image(url)
        if path:
            try:
                md = await ocr_file(path)
                desc += f"\n\n[MinerU OCR 精确文本（表格/公式/版面已结构化）]\n{md}"
            except Exception as e:  # noqa: BLE001
                desc += f"\n\n[MinerU OCR 调用失败: {e}，以上仅为视觉模型识别结果]"
            finally:
                os.unlink(path)
        else:
            desc += "\n\n[MinerU OCR：图片落盘失败，以上仅为视觉模型识别结果]"
    elif need_ocr:
        desc += "\n\n[提示：检测到文档类图片，在代理 .env 配置 MINERU_API_KEY 后可获得逐字精确提取]"

    # 空结果不入缓存，防止模型偶发抽风污染缓存
    if desc.strip():
        db.set_image_cache(img_hash, desc)
    return desc


async def process_images(messages: list) -> int:
    """把消息里的图片原地替换为识别文字，返回图片数量。
    注意：只改含图片的消息（通常是最后一条 user 消息），前面的前缀不动，
    这样 DeepSeek 的 context cache 命中率不受影响。
    """
    force_ocr = user_wants_ocr(messages)
    count = 0
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        img_parts = [p for p in content if p.get("type") in ("image_url", "input_image")]
        if not img_parts:
            continue
        # 同一条消息里的多张图并发识别
        descs = await asyncio.gather(*[
            handle_one_image(
                (lambda iu: iu.get("url", "") if isinstance(iu, dict) else (iu or ""))
                (p.get("image_url")),
                force_ocr,
            )
            for p in img_parts
        ])
        new_parts, di = [], 0
        for part in content:
            if part.get("type") in ("image_url", "input_image"):
                count += 1
                new_parts.append({
                    "type": "text",
                    "text": f"\n[图片{count} · 视觉识别结果]\n{descs[di]}\n",
                })
                di += 1
            else:
                new_parts.append(part)
        msg["content"] = new_parts
    return count


# 客户端可能发来各种模型名（如 deepseek-4v-flash），统一到底成 DeepSeek 真实模型
KNOWN_MODELS = {"deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"}


@app.post("/v1/chat/completions")
@app.post("/chat/completions")  # 兼容 base_url 不带 /v1 的客户端
async def chat(req: Request):
    check_auth(req)
    body = await req.json()
    if not DEEPSEEK_API_KEY:
        raise HTTPException(500, "代理未配置 DEEPSEEK_API_KEY")

    client_model = body.get("model", "")
    if client_model not in KNOWN_MODELS:
        body["model"] = "deepseek-chat"

    # DeepSeek 只认 system/user/assistant/tool，把 developer 角色归一为 system
    for msg in body.get("messages", []):
        if msg.get("role") == "developer":
            msg["role"] = "system"

    n_img = await process_images(body.get("messages", []))
    n_path = await process_text_paths(body.get("messages", []))
    had_visual = n_img + n_path > 0
    stream = bool(body.get("stream"))
    if stream:
        # 让流式响应最后带上 usage，否则统计不到缓存数据
        body.setdefault("stream_options", {})["include_usage"] = True

    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    url = f"{DEEPSEEK_BASE}/chat/completions"
    t0 = time.time()
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30))

    if not stream:
        try:
            r = await client.post(url, headers=headers, json=body)
            result = r.json()
        finally:
            await client.aclose()
        db.log_request(
            client_model, result.get("usage") or {},
            had_visual, False, int((time.time() - t0) * 1000),
        )
        return JSONResponse(result, status_code=r.status_code)

    async def gen():
        usage = {}
        try:
            async with client.stream("POST", url, headers=headers, json=body) as r:
                async for line in r.aiter_lines():
                    if not line:
                        yield "\n"
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data and data != "[DONE]":
                            try:
                                chunk = json.loads(data)
                                if chunk.get("usage"):
                                    usage = chunk["usage"]
                            except json.JSONDecodeError:
                                pass
                    yield line + "\n"
        except Exception as e:  # 流中途异常，打日志并优雅结束
            print(f"[流转发异常] {e!r}", flush=True)
            yield f'data: {{"error": {{"message": "proxy stream error: {e}"}}}}\n\n'
            yield "data: [DONE]\n\n"
        finally:
            db.log_request(
                client_model, usage,
                had_visual, False, int((time.time() - t0) * 1000),
            )
            await client.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/models")
@app.get("/models")
async def models(req: Request):
    check_auth(req)
    return {
        "object": "list",
        "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
        ],
    }


class OcrReq(BaseModel):
    file_path: str


@app.post("/v1/describe")
async def describe(req: Request, body: OcrReq):
    """本地图片识别：给 Agent 用的兜底通道。
    适用于 Agent 不支持图片内联、只能拿到本地文件路径的场景。
    与聊天内嵌图片同一条管线：GLM 识别 → 文档类自动叠加 MinerU → 结果哈希缓存。"""
    check_auth(req)
    import base64
    import mimetypes

    p = Path(body.file_path)
    if not p.exists():
        raise HTTPException(404, f"文件不存在: {body.file_path}")
    if p.suffix.lower() in (".pdf", ".tif", ".tiff"):
        # PDF/扫描件直接走 MinerU 通道
        md = await ocr_file(str(p))
        out = p.with_suffix(".mineru.md")
        out.write_text(md, encoding="utf-8")
        return {"type": "document", "description": md, "markdown_path": str(out)}

    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    data_url = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    desc = await handle_one_image(data_url, force_ocr=False)
    return {"type": "image", "description": desc}


@app.post("/v1/ocr")
async def ocr(req: Request, body: OcrReq):
    """MinerU 文档解析：传本地文件路径，返回解析出的 Markdown 文件路径"""
    check_auth(req)
    t0 = time.time()
    try:
        md = await ocr_file(body.file_path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))
    out = Path(body.file_path).with_suffix(".mineru.md")
    out.write_text(md, encoding="utf-8")
    db.log_request("mineru-ocr", {}, False, True, int((time.time() - t0) * 1000))
    return {"markdown_path": str(out), "chars": len(md)}


@app.get("/v1/stats")
async def stats(req: Request):
    check_auth(req)
    rows = db.query("SELECT * FROM requests WHERE model != 'mineru-ocr'")

    def agg(rs):
        hit = sum(r["cache_hit"] for r in rs)
        miss = sum(r["cache_miss"] for r in rs)
        return {
            "requests": len(rs),
            "cache_hit_tokens": hit,
            "cache_miss_tokens": miss,
            "hit_rate_pct": round(hit / (hit + miss) * 100, 2) if hit + miss else 0,
            "images_processed": sum(r["had_image"] for r in rs),
        }

    now = datetime.now()
    month_start = datetime(now.year, now.month, 1).timestamp()
    today_start = datetime(now.year, now.month, now.day).timestamp()
    return {
        "today": agg([r for r in rows if r["ts"] >= today_start]),
        "month": agg([r for r in rows if r["ts"] >= month_start]),
        "all": agg(rows),
    }
