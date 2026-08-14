"""DeepSeek Eyes 代理主服务
客户端 → 本代理 →（有图先问 GLM-4V-Flash）→ DeepSeek
同时把每次请求的缓存命中数据写进 SQLite 供面板使用。

2026-08-14 安全与可观测性升级：
- 认证改用常量时间比较（secrets.compare_digest）
- /config 面板密钥脱敏回显，不再泄露明文
- 简单令牌桶限流，防 key 泄露后余额被打爆
- 结构化日志（logging），每次请求可追溯
- 模型归一改用 config.MODEL_MAP 可配置映射
- 内置可视化计费面板（GET /）
"""
import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import config
import db
import logger as app_logger
import service
from ocr import materialize_image, ocr_file
from vision import describe_image

# ── 结构化日志 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("deepseek-eyes")

app = FastAPI(title="DeepSeek Eyes")

# ── 熔断检测（连续失败达到阈值时提醒）────────────────────────────────
_consecutive_failures = 0
CONSECUTIVE_FAILURE_THRESHOLD = int(os.getenv("EYES_CIRCUIT_THRESHOLD", "5"))


def _record_failure():
    global _consecutive_failures
    _consecutive_failures += 1


def _record_success():
    global _consecutive_failures
    _consecutive_failures = 0


def circuit_tripped() -> bool:
    """连续失败超过阈值，返回 True（UI/托盘据此提醒 key 可能失效）。"""
    return _consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD

# ── 限流（简单固定窗口，按客户端 IP）────────────────────────────────
_rate_windows: dict[str, list[float]] = defaultdict(list)


def _rate_limited(ip: str) -> bool:
    now = time.time()
    window = _rate_windows[ip]
    # 清理窗口外的旧记录
    window[:] = [t for t in window if now - t < config.RATE_LIMIT_WINDOW_SEC]
    if len(window) >= config.RATE_LIMIT_PER_MIN:
        return True
    window.append(now)
    return False


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        log.warning("rate limited ip=%s path=%s", ip, request.url.path)
        return JSONResponse(
            status_code=429,
            content={"error": "too many requests, slow down"},
        )
    return await call_next(request)


def check_auth(req: Request):
    if config.PROXY_API_KEY:
        provided = req.headers.get("authorization", "")
        expected = f"Bearer {config.PROXY_API_KEY}"
        # 常量时间比较，避免时序侧信道
        if not secrets.compare_digest(provided, expected):
            raise HTTPException(401, "invalid proxy api key")


# 匹配消息文本里的本地文件路径（Windows 绝对路径 / 相对路径 / ~ 路径）
PATH_RE = re.compile(
    r"((?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|~?[\\/])[\w\-./\\\u4e00-\u9fff]+?\.(?:png|jpe?g|webp|gif|bmp|pdf|tiff?))",
    re.IGNORECASE,
)


def resolve_path(raw: str):
    """相对路径依次按代理 cwd 和 WORKSPACE_ROOT 解析"""
    candidates = [Path(raw)]
    if not Path(raw).is_absolute() and config.WORKSPACE_ROOT:
        candidates.append(Path(config.WORKSPACE_ROOT) / raw)
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
    cached = await db.get_image_cache(img_hash)
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
    if need_ocr and config.MINERU_API_KEY:
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
        await db.set_image_cache(img_hash, desc)
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


@app.post("/v1/chat/completions")
@app.post("/chat/completions")  # 兼容 base_url 不带 /v1 的客户端
async def chat(req: Request):
    check_auth(req)
    # 软停止：代理开关关闭时拒绝服务，但 UI 路由不受影响
    if not service.service.running:
        raise HTTPException(503, "service stopped - 代理服务已停止，请在面板或托盘重新开启")
    raw = await req.body()
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            body = json.loads(raw.decode("gbk"))  # 兼容 Windows 上 GBK 编码的客户端
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "请求体不是合法 JSON（请使用 UTF-8 编码）")
    if not config.DEEPSEEK_API_KEY:
        raise HTTPException(500, "代理未配置 DEEPSEEK_API_KEY")

    client_model = body.get("model", "")
    body["model"] = config.normalize_model(client_model)

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

    headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}
    url = f"{config.DEEPSEEK_BASE}/chat/completions"
    t0 = time.time()
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30))
    log.info(
        "chat model=%s stream=%s msgs=%d visual=%d",
        body["model"], stream, len(body.get("messages", [])), had_visual,
    )

    if not stream:
        try:
            r = await client.post(url, headers=headers, json=body)
            result = r.json()
        except Exception as e:  # noqa: BLE001
            await client.aclose()
            _record_failure()
            await app_logger.error(f"upstream request failed: {e}", "proxy")
            raise HTTPException(502, f"上游 DeepSeek 请求失败: {e}")
        finally:
            await client.aclose()
        usage = result.get("usage") or {}
        await db.log_request(
            client_model, usage,
            had_visual, False, int((time.time() - t0) * 1000),
        )
        _record_success()
        await app_logger.info(
            f"done model={client_model} latency={int((time.time()-t0)*1000)}ms "
            f"in={usage.get('prompt_tokens',0)} out={usage.get('completion_tokens',0)} "
            f"visual={had_visual}",
            "proxy",
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
                                # 某些上游会发 keep-alive 等非 JSON 行，忽略即可
                                pass
                    yield line + "\n"
        except Exception as e:  # 流中途异常，打日志并优雅结束
            _record_failure()
            await app_logger.error(f"stream error: {e}", "proxy")
            yield f'data: {{"error": {{"message": "proxy stream error: {e}"}}}}\n\n'
            yield "data: [DONE]\n\n"
        else:
            _record_success()
        finally:
            await db.log_request(
                client_model, usage,
                had_visual, False, int((time.time() - t0) * 1000),
            )
            await client.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/models")
@app.get("/models")
async def models(req: Request):
    check_auth(req)
    return {"object": "list", "data": config.EXPOSED_MODELS}


class OcrReq(BaseModel):
    file_path: str


@app.post("/v1/describe")
async def describe(req: Request, body: OcrReq):
    """本地图片识别：给 Agent 用的兜底通道。
    适用于 Agent 不支持图片内联、只能拿到本地文件路径的场景。
    与聊天内嵌图片同一条管线：GLM 识别 → 文档类自动叠加 MinerU → 结果哈希缓存。"""
    check_auth(req)
    if not service.service.running:
        raise HTTPException(503, "service stopped")
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
    if not service.service.running:
        raise HTTPException(503, "service stopped")
    t0 = time.time()
    try:
        md = await ocr_file(body.file_path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))
    out = Path(body.file_path).with_suffix(".mineru.md")
    out.write_text(md, encoding="utf-8")
    await db.log_request("mineru-ocr", {}, False, True, int((time.time() - t0) * 1000))
    return {"markdown_path": str(out), "chars": len(md)}


# ── 计费与用量统计 ──────────────────────────────────────────────────

def _cost(model: str, hit: int, miss: int, completion: int) -> float:
    """按模型单价计算一次请求的人民币成本。"""
    price = config.pricing_for(model)
    cost = (
        hit / 1e6 * price["cache_hit"]
        + miss / 1e6 * price["input"]
        + completion / 1e6 * price["output"]
    )
    return cost


async def _stats_between(start_ts, end_ts=None):
    """聚合 [start_ts, end_ts) 区间（或至今）的用量。"""
    if end_ts is None:
        rows = await db.query(
            "SELECT * FROM requests WHERE model != 'mineru-ocr' AND ts >= ?", (start_ts,)
        )
        ocr_rows = await db.query(
            "SELECT COUNT(*) c FROM requests WHERE model='mineru-ocr' AND ts >= ?", (start_ts,)
        )
    else:
        rows = await db.query(
            "SELECT * FROM requests WHERE model != 'mineru-ocr' AND ts >= ? AND ts < ?",
            (start_ts, end_ts),
        )
        ocr_rows = await db.query(
            "SELECT COUNT(*) c FROM requests WHERE model='mineru-ocr' AND ts >= ? AND ts < ?",
            (start_ts, end_ts),
        )

    hit = sum(r["cache_hit"] for r in rows)
    miss = sum(r["cache_miss"] for r in rows)
    completion = sum(r["completion_tokens"] for r in rows)
    total_cost = sum(_cost(r["model"], r["cache_hit"], r["cache_miss"], r["completion_tokens"]) for r in rows)
    images = sum(r["had_image"] for r in rows)
    return {
        "requests": len(rows),
        "cache_hit_tokens": hit,
        "cache_miss_tokens": miss,
        "completion_tokens": completion,
        "total_tokens": hit + miss + completion,
        "hit_rate_pct": round(hit / (hit + miss) * 100, 2) if hit + miss else 0.0,
        "images_processed": images,
        "ocr_used": ocr_rows[0]["c"] if ocr_rows else 0,
        "cost": round(total_cost, 4),
    }


@app.get("/v1/stats")
async def stats(req: Request):
    check_auth(req)
    return await _stats_payload()


@app.get("/api/stats")
async def stats_ui():
    """UI 面板专用（无鉴权，本机访问）。"""
    return await _stats_payload()


async def _stats_payload():
    now = datetime.now()
    month_start = datetime(now.year, now.month, 1).timestamp()
    today_start = datetime(now.year, now.month, now.day).timestamp()
    return {
        "today": await _stats_between(today_start),
        "month": await _stats_between(month_start),
        "all": await _stats_between(0),
    }


@app.get("/v1/stats/daily")
async def stats_daily(req: Request, days: int = 30):
    """返回最近 N 天的逐日 token/费用序列（供面板画折线图）。"""
    check_auth(req)
    return await _stats_daily_payload(days)


@app.get("/api/stats/daily")
async def stats_daily_ui(days: int = 30):
    return await _stats_daily_payload(days)


async def _stats_daily_payload(days: int):
    days = max(1, min(days, 365))
    now = datetime.now()
    day_start = datetime(now.year, now.month, now.day).timestamp()
    rows = await db.query(
        "SELECT * FROM requests WHERE model != 'mineru-ocr' AND ts >= ?",
        (day_start - days * 86400,),
    )
    buckets = {}
    for r in rows:
        day = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")
        b = buckets.setdefault(day, {"hit": 0, "miss": 0, "completion": 0, "cost": 0.0})
        b["hit"] += r["cache_hit"]
        b["miss"] += r["cache_miss"]
        b["completion"] += r["completion_tokens"]
        b["cost"] += _cost(r["model"], r["cache_hit"], r["cache_miss"], r["completion_tokens"])
    return {
        "daily": [
            {"date": d, **{k: (round(v, 4) if k == "cost" else v) for k, v in b.items()}}
            for d, b in sorted(buckets.items())
        ]
    }


@app.get("/v1/stats/by-model")
async def stats_by_model(req: Request):
    """按模型聚合用量与费用（供面板画饼图）。"""
    check_auth(req)
    return await _stats_by_model_payload()


@app.get("/api/stats/by-model")
async def stats_by_model_ui():
    return await _stats_by_model_payload()


async def _stats_by_model_payload():
    rows = await db.query(
        "SELECT model, SUM(cache_hit) hit, SUM(cache_miss) miss, "
        "SUM(completion_tokens) completion, COUNT(*) cnt "
        "FROM requests WHERE model != 'mineru-ocr' GROUP BY model"
    )
    out = []
    for r in rows:
        out.append({
            "model": r["model"],
            "requests": r["cnt"],
            "hit_tokens": r["hit"],
            "miss_tokens": r["miss"],
            "completion_tokens": r["completion"],
            "cost": round(_cost(r["model"], r["hit"], r["miss"], r["completion"]), 4),
        })
    return {"models": out}


# ── 可视化计费面板 ──────────────────────────────────────────────────

@app.get("/")
async def dashboard(req: Request):
    """内置可视化计费面板（无需 streamlit，直接浏览器打开 8000 端口）。"""
    return FileResponse(str(Path(__file__).parent / "dashboard.html"))


@app.get("/static/echarts.min.js")
async def echarts_js(req: Request):
    return FileResponse(str(Path(__file__).parent / "static" / "echarts.min.js"))


# ── 配置面板（密钥脱敏）─────────────────────────────────────────────

CONFIG_FIELDS = [
    ("DEEPSEEK_API_KEY", "DeepSeek API Key", "platform.deepseek.com"),
    ("ZHIPU_API_KEY", "智谱 API Key（GLM-4V-Flash 免费）", "open.bigmodel.cn"),
    ("MINERU_API_KEY", "MinerU API Key（可选，文档 OCR）", "mineru.net"),
    ("PROXY_API_KEY", "代理访问密钥（客户端填这个）", "自己定，建议 16 位以上"),
    ("WORKSPACE_ROOT", "相对路径解析根目录（可选）", "如 C:/Users/you/Desktop"),
]

CONFIG_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>DeepSeek Eyes 配置</title>
<style>
body{font-family:system-ui;background:#0f172a;color:#e2e8f0;max-width:640px;margin:40px auto;padding:0 16px}
h1{font-size:20px}label{display:block;margin:16px 0 4px;font-size:13px;color:#94a3b8}
input{width:100%;padding:10px;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;box-sizing:border-box}
button{margin-top:24px;padding:12px 24px;border:0;border-radius:8px;background:#38bdf8;color:#0f172a;font-weight:700;cursor:pointer}
#msg{margin-top:12px;font-size:14px}.hint{font-size:12px;color:#64748b}
.mask-note{font-size:12px;color:#fbbf24;margin-top:4px}
</style></head><body>
<h1>🔧 DeepSeek Eyes 配置面板</h1>
<p class="hint">密钥已脱敏显示（sk-****abcd）。留空或保持脱敏值 = 不修改该字段；输入新值才会覆盖。</p>
__FIELDS__
<label>验证密钥（当前 PROXY_API_KEY，未设置过则留空）</label>
<input id="__auth__" type="password" placeholder="">
<button onclick="save()">保存并生效</button>
<div id="msg"></div>
<script>
async function save(){
  const values={};
  document.querySelectorAll('[data-k]').forEach(i=>values[i.dataset.k]=i.value.trim());
  const r = await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({auth:document.getElementById('__auth__').value.trim(),values})});
  const j = await r.json().catch(()=>({detail:'保存失败'}));
  document.getElementById('msg').textContent = r.ok ? '✅ 已保存并热生效，无需重启' : ('❌ '+(j.detail||'保存失败'));
}
</script></body></html>"""


@app.get("/config")
async def config_page():
    from fastapi.responses import HTMLResponse
    fields = []
    for key, label, hint in CONFIG_FIELDS:
        cur = config.mask_secret(getattr(config, key, "") or "")
        fields.append(
            f'<label>{label} <span class="hint">{hint}</span></label>'
            f'<input data-k="{key}" value="{cur}" placeholder="保持原值">'
            f'<div class="mask-note">当前：{cur or "未设置"}</div>'
        )
    return HTMLResponse(CONFIG_HTML.replace("__FIELDS__", "\n".join(fields)))


class ConfigReq(BaseModel):
    auth: str = ""
    values: dict[str, str]


@app.post("/config")
async def config_save(body: ConfigReq):
    # 用 compare_digest 校验面板写入密钥
    if config.PROXY_API_KEY and not secrets.compare_digest(body.auth, config.PROXY_API_KEY):
        raise HTTPException(403, "验证密钥错误（需要当前的 PROXY_API_KEY）")
    allowed = {k for k, _, _ in CONFIG_FIELDS}
    # 过滤出真正要更新的字段：跳过空值（留空=不修改）
    updates = {
        k: v for k, v in body.values.items()
        if k in allowed and v and not config.is_masked(v)
    }
    if not updates:
        return {"ok": True, "updated": []}

    lines = config.ENV_PATH.read_text(encoding="utf-8").splitlines() if config.ENV_PATH.exists() else []
    seen, out = set(), []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    config.ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    config.reload()
    log.info("config updated: %s", sorted(updates.keys()))
    return {"ok": True, "updated": sorted(updates.keys())}


# ── 密钥管理 JSON API（控制台密钥页用）──────────────────────────────

@app.get("/api/config/masked")
async def config_masked():
    """返回所有配置字段的脱敏值。"""
    return {
        k: config.mask_secret(getattr(config, k, "") or "")
        for k, _, _ in CONFIG_FIELDS
    }


@app.get("/api/config/reveal")
async def config_reveal(key: str):
    """临时返回某字段明文（供眼睛按钮查看，5 秒后前端自动隐藏）。"""
    if key not in {k for k, _, _ in CONFIG_FIELDS}:
        raise HTTPException(400, "未知配置字段")
    return {"key": key, "value": getattr(config, key, "") or ""}


class ConfigSaveReq(BaseModel):
    values: dict[str, str]


@app.post("/api/config/save")
async def config_save_json(body: ConfigSaveReq):
    """JSON 版保存：跳过空值和脱敏值（留空=不修改）。"""
    allowed = {k for k, _, _ in CONFIG_FIELDS}
    updates = {
        k: v for k, v in body.values.items()
        if k in allowed and v and not config.is_masked(v)
    }
    if not updates:
        return {"ok": True, "updated": []}
    lines = config.ENV_PATH.read_text(encoding="utf-8").splitlines() if config.ENV_PATH.exists() else []
    seen, out = set(), []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    config.ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    config.reload()
    await app_logger.info(f"config updated: {sorted(updates.keys())}", "config")
    return {"ok": True, "updated": sorted(updates.keys())}


# ── 服务启停控制 ────────────────────────────────────────────────────

@app.get("/api/service")
async def service_status():
    """查询服务状态 + 熔断 + 端口信息。UI 常驻路由，不需鉴权。"""
    return {
        "running": service.service.running,
        "circuit_tripped": circuit_tripped(),
        "consecutive_failures": _consecutive_failures,
        "circuit_threshold": CONSECUTIVE_FAILURE_THRESHOLD,
        "port": _actual_port if "_actual_port" in globals() else 8000,
    }


class ServiceCmd(BaseModel):
    action: str = "toggle"  # start | stop | toggle


@app.post("/api/service")
async def service_control(cmd: ServiceCmd):
    """控制代理服务启停。"""
    action = cmd.action
    if action == "start":
        service.service.start()
    elif action == "stop":
        service.service.stop()
    elif action == "toggle":
        service.service.toggle()
    else:
        raise HTTPException(400, f"未知操作: {action}")
    await app_logger.info(f"service {action} -> running={service.service.running}", "service")
    return {"running": service.service.running}


@app.post("/api/service/reset-circuit")
async def reset_circuit():
    """重置熔断计数器。"""
    _record_success()
    return {"ok": True}


# ── 日志查询 ────────────────────────────────────────────────────────

@app.get("/api/logs")
async def api_logs(after_seq: int = 0, limit: int = 300):
    """实时日志（增量 or 全量）。after_seq=0 返回最近 limit 条。"""
    lines = await app_logger.buffer.snapshot(after_seq, limit)
    return {"lines": lines}


@app.get("/api/logs/dates")
async def api_log_dates():
    """历史日志日期列表。"""
    return {"dates": app_logger.list_log_dates()}


@app.get("/api/logs/history")
async def api_log_history(date: str = "", limit: int = 500):
    """按日期读历史日志文件。"""
    if not date:
        return {"lines": []}
    return {"lines": app_logger.read_history_file(date, limit)}


# ── 接入指南 ────────────────────────────────────────────────────────

@app.get("/api/guide")
async def api_guide():
    """返回接入信息（动态 base_url + key + 各 agent 配置示例）。"""
    port = _actual_port if "_actual_port" in globals() else 8000
    base_url = f"http://127.0.0.1:{port}/v1"
    return {
        "base_url": base_url,
        "api_key": config.PROXY_API_KEY or "(未设置，本机不校验)",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "examples": [
            {
                "name": "通用（任意 OpenAI 兼容 agent）",
                "config": f"Base URL: {base_url}\nAPI Key: <你的 PROXY_API_KEY>\nModel: deepseek-v4-flash",
            },
            {
                "name": "DeepSeek Harness (dsh)",
                "config": (
                    "settings.yaml → llm-pi-ai.providers:\n"
                    f"  baseURL: {base_url}\n"
                    "  apiKeyEnv: DEEPSEEKEYES_API_KEY\n"
                    "  models: [deepseek-v4-flash, deepseek-v4-pro]\n"
                    "  input: [text, image]  # 声明图片输入以开放上传"
                ),
            },
            {
                "name": "pi",
                "config": (
                    f'"baseUrl": "{base_url}",\n'
                    '"apiKey": "<PROXY_API_KEY>",\n'
                    '"api": "openai-completions",\n'
                    '"input": ["text", "image"]'
                ),
            },
        ],
    }


# ── 启动初始化 ──────────────────────────────────────────────────────

_actual_port = 8000


def set_actual_port(port: int):
    """app.py 启动时调用，记录实际监听端口（供接入指南动态显示）。"""
    global _actual_port
    _actual_port = port


@app.on_event("startup")
async def startup():
    await db.init()
    app_logger.writer.start()
    app_logger.init_file_logging()
    await app_logger.info(
        f"DeepSeek Eyes started. rate_limit={config.RATE_LIMIT_PER_MIN}/min "
        f"image_cache_ttl={config.IMAGE_CACHE_TTL_DAYS}d",
        "system",
    )
    log.info(
        "DeepSeek Eyes started. rate_limit=%d/min image_cache_ttl=%dd",
        config.RATE_LIMIT_PER_MIN, config.IMAGE_CACHE_TTL_DAYS,
    )


@app.on_event("shutdown")
async def shutdown():
    await app_logger.writer.stop()
    await app_logger.info("DeepSeek Eyes stopped", "system")
