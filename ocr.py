"""MinerU 在线 API：PDF/文档/图片 → 结构化 Markdown
流程：申请上传链接 → 上传文件 → 轮询解析结果 → 下载 zip 取 .md
"""
import asyncio
import base64
import io
import os
import tempfile
import zipfile

import httpx

import config

BASE = "https://mineru.net/api/v4"


async def ocr_file(path: str, is_ocr: bool = True) -> str:
    if not config.MINERU_API_KEY:
        raise RuntimeError("未配置 MINERU_API_KEY（在 mineru.net 申请，每日有免费额度）")
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    name = os.path.basename(path)
    headers = {"Authorization": f"Bearer {config.MINERU_API_KEY}"}

    async with httpx.AsyncClient(timeout=180) as c:
        # 1. 申请上传链接
        r = await c.post(
            f"{BASE}/file-urls/batch",
            headers=headers,
            json={
                "enable_formula": True,
                "enable_table": True,
                "is_ocr": is_ocr,
                "files": [{"name": name}],
            },
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"MinerU 申请上传失败: {data}")
        batch_id = data["data"]["batch_id"]
        upload_url = data["data"]["file_urls"][0]

        # 2. 上传文件
        with open(path, "rb") as f:
            put = await c.put(upload_url, content=f.read())
            put.raise_for_status()

        # 3. 轮询解析结果（前 30 秒每 5 秒，之后每 15 秒，退避减少空转）
        for attempt in range(config.MINERU_POLL_MAX):
            interval = (
                config.MINERU_POLL_EARLY_SEC if attempt < 6 else config.MINERU_POLL_LATE_SEC
            )
            await asyncio.sleep(interval)
            try:
                res = await c.get(
                    f"{BASE}/extract-results/batch/{batch_id}", headers=headers
                )
                res.raise_for_status()
                payload = res.json()
                item = payload["data"]["extract_result"][0]
            except (KeyError, IndexError, ValueError) as e:
                raise RuntimeError(f"MinerU 查询结果异常: {e!r}，原始响应: {res.text[:300]}")
            state = item.get("state")
            if state == "done":
                z = await c.get(item["full_zip_url"])
                z.raise_for_status()
                zf = zipfile.ZipFile(io.BytesIO(z.content))
                for n in zf.namelist():
                    if n.endswith(".md"):
                        return zf.read(n).decode("utf-8", "ignore")
                raise RuntimeError("MinerU 结果包里没有 markdown 文件")
            if state == "failed":
                raise RuntimeError(f"MinerU 解析失败: {item.get('err_msg')}")

    raise TimeoutError(f"MinerU 解析超时（约 {config.MINERU_POLL_MAX} 次轮询）")


async def materialize_image(image_url: str) -> str | None:
    """把聊天消息里的图片（data URL 或 http 链接）落盘为临时文件，返回路径。
    MinerU 只接受文件上传，内嵌图片需要先落地。失败返回 None。
    """
    try:
        if image_url.startswith("data:"):
            header, b64 = image_url.split(",", 1)
            mime = header.split(";")[0][5:]
            ext = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/webp": ".webp",
            }.get(mime, ".png")
            data = base64.b64decode(b64)
        else:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                r = await c.get(image_url)
                r.raise_for_status()
            data = r.content
            ext = os.path.splitext(image_url.split("?")[0])[1].lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"

        fd, path = tempfile.mkstemp(suffix=ext, prefix="eyes_")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path
    except Exception:  # noqa: BLE001
        return None
