import asyncio
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Optional, Union

import httpx
from nonebot.log import logger

from .config import emoji_config
from .emoji_data import dates, emojis

API = "https://www.gstatic.com/android/keyboard/emojikitchen/"
DOWNLOAD_TIMEOUT = 25
MAX_IMAGE_BYTES = 5 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_inflight: dict[str, asyncio.Task[Union[str, bytes]]] = {}


def _emoji_code(emoji: list[int]) -> str:
    return "-".join(f"u{code:x}" for code in emoji)


def create_url(date: str, emoji1: list[int], emoji2: list[int]) -> str:
    u1, u2 = _emoji_code(emoji1), _emoji_code(emoji2)
    return f"{API}{date}/{u1}/{u1}_{u2}.png"


def find_emoji(emoji_code: str) -> Optional[list[int]]:
    emoji_num = ord(emoji_code)
    for emoji in emojis:
        if emoji_num in emoji:
            return emoji
    return None


def _cache_key(emoji1: list[int], emoji2: list[int]) -> str:
    return "_".join(sorted((_emoji_code(emoji1), _emoji_code(emoji2))))


def _cache_dir() -> Path:
    # Data, rather than disposable cache storage: downloaded pairs survive cleanup.
    from nonebot_plugin_localstore import get_data_dir

    return get_data_dir("nonebot_plugin_emojimix") / "images"


def _valid_png(data: bytes) -> bool:
    if not data.startswith(PNG_SIGNATURE) or len(data) > MAX_IMAGE_BYTES:
        return False
    offset, seen_image = len(PNG_SIGNATURE), False
    while offset + 12 <= len(data):
        length = struct.unpack_from("!I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            return False
        kind = data[offset + 4 : offset + 8]
        checksum = struct.unpack_from("!I", data, end - 4)[0]
        if zlib.crc32(data[offset + 4 : end - 4]) != checksum:
            return False
        if offset == 8 and (kind != b"IHDR" or length != 13):
            return False
        if kind == b"IDAT":
            seen_image = True
        if kind == b"IEND":
            return length == 0 and end == len(data) and seen_image
        offset = end
    return False


def _read_cache(key: str) -> Optional[bytes]:
    try:
        with (_cache_dir() / f"{key}.png").open("rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
        if _valid_png(data):
            return data
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Emoji图片缓存读取失败（{}）", type(exc).__name__)
    return None


def _write_cache(key: str, data: bytes) -> None:
    temporary = None
    try:
        directory = _cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=f".{key}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / f"{key}.png")
    except OSError as exc:
        # Deliver the successful download even when persistent storage is unavailable.
        logger.warning("Emoji图片缓存保存失败（{}）", type(exc).__name__)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Emoji缓存临时文件清理失败（{}）", type(exc).__name__)


async def _download_routes(urls: list[str]) -> Union[str, bytes]:
    # Explicit proxy first, then direct. Never inherit process-wide proxy settings.
    routes = [emoji_config.http_proxy, None] if emoji_config.http_proxy else [None]
    failed = False
    for proxy in routes:
        try:
            async with httpx.AsyncClient(
                proxy=proxy, trust_env=False, timeout=httpx.Timeout(10, connect=5)
            ) as client:
                route_failed = False
                for url in urls:
                    resp = await client.get(url)
                    if (
                        resp.status_code == 200
                        and resp.headers.get("content-type", "").startswith("image/")
                        and _valid_png(resp.content)
                    ):
                        return resp.content
                    if resp.status_code != 404:
                        route_failed = failed = True
                        break
                if not route_failed:
                    return "出错了，可能不支持该emoji组合"
        except httpx.HTTPError as exc:
            failed = True
            route = "代理失败，将尝试直连" if proxy else "直连失败"
            logger.warning("Emoji合成图片连接失败（{}），{}", type(exc).__name__, route)
    return "下载出错，请稍后再试" if failed else "出错了，可能不支持该emoji组合"


async def _mix_and_cache(
    key: str, emoji1: list[int], emoji2: list[int]
) -> Union[str, bytes]:
    cached = await asyncio.to_thread(_read_cache, key)
    if cached is not None:
        return cached

    urls = list(
        dict.fromkeys(
            url
            for date in dates
            for url in (
                create_url(date, emoji1, emoji2),
                create_url(date, emoji2, emoji1),
            )
        )
    )
    try:
        result = await asyncio.wait_for(_download_routes(urls), DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Emoji合成图片下载超时")
        return "下载出错，请稍后再试"
    if isinstance(result, bytes):
        await asyncio.to_thread(_write_cache, key, result)
    return result


async def mix_emoji(emoji_code1: str, emoji_code2: str) -> Union[str, bytes]:
    emoji1, emoji2 = find_emoji(emoji_code1), find_emoji(emoji_code2)
    if not emoji1:
        return f"不支持的emoji：{emoji_code1}"
    if not emoji2:
        return f"不支持的emoji：{emoji_code2}"

    key = _cache_key(emoji1, emoji2)
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(_mix_and_cache(key, emoji1, emoji2))
        _inflight[key] = task

        def remove_completed(completed: asyncio.Task[Union[str, bytes]]) -> None:
            if _inflight.get(key) is completed:
                del _inflight[key]
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(remove_completed)
    # One cancelled caller must not interrupt other callers sharing this download.
    return await asyncio.shield(task)
