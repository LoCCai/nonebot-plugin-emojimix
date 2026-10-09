import asyncio
import importlib.util
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import httpx

SOURCE_PATH = Path(__file__).parents[1] / "nonebot_plugin_emojimix/data_source.py"
REAL_CLIENT = httpx.AsyncClient


def png_image() -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack("!I", len(body))
            + kind + body + struct.pack("!I", zlib.crc32(kind + body))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


PNG = png_image()


class DataSourceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "images"
        self.modules = []
        self.source = self.load_source()
        self.options, self.calls = [], []

    def load_source(self):
        name = "isolated_emojimix_source"
        package = ModuleType(name)
        package.__path__ = []
        config = ModuleType(name + ".config")
        config.emoji_config = SimpleNamespace(http_proxy=None)
        data = ModuleType(name + ".emoji_data")
        data.dates = ["20201001"]
        data.emojis = [[0x1F600], [0x1F601], [0x2764, 0xFE0F]]
        modules = {module.__name__: module for module in (package, config, data)}
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                name + ".data_source", SOURCE_PATH
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Cannot load EmojiMix data source")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        module._cache_dir = lambda: self.directory
        self.modules.append(module)
        return module

    async def asyncTearDown(self) -> None:
        tasks = [task for module in self.modules for task in module._inflight.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def client_factory(self, respond):
        def factory(**options):
            self.options.append(options)

            async def handle(request):
                self.calls.append(request)
                response = respond(request, options)
                if asyncio.iscoroutine(response):
                    response = await response
                return response

            return REAL_CLIENT(transport=httpx.MockTransport(handle), trust_env=False)

        return factory

    def successful_client(self):
        return self.client_factory(
            lambda request, options: httpx.Response(
                200, content=PNG, headers={"content-type": "image/png"}
            )
        )

    def saved_files(self):
        return list(self.directory.glob("*.png"))

    async def test_download_persists_and_reversed_pair_works_offline(self):
        with patch.object(self.source.httpx, "AsyncClient", self.successful_client()):
            self.assertEqual(await self.source.mix_emoji("😀", "😁"), PNG)
        files = self.saved_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), PNG)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        with patch.object(self.source.httpx, "AsyncClient", side_effect=AssertionError):
            self.assertEqual(await self.source.mix_emoji("😁", "😀"), PNG)
        self.assertEqual(len(self.calls), 1)

    async def test_cache_survives_module_reload_without_http(self):
        with patch.object(self.source.httpx, "AsyncClient", self.successful_client()):
            await self.source.mix_emoji("😀", "😁")
        restored = self.load_source()
        with patch.object(restored.httpx, "AsyncClient", side_effect=AssertionError):
            self.assertEqual(await restored.mix_emoji("😁", "😀"), PNG)

    async def test_concurrent_reversed_requests_share_one_download(self):
        with patch.object(self.source.httpx, "AsyncClient", self.successful_client()):
            results = await asyncio.gather(
                self.source.mix_emoji("😀", "😁"),
                self.source.mix_emoji("😁", "😀"),
                self.source.mix_emoji("😀", "😁"),
            )
        self.assertEqual(results, [PNG] * 3)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.source._inflight, {})

    async def test_cancelled_caller_does_not_cancel_shared_download(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def respond(request, options):
            started.set()
            await release.wait()
            return httpx.Response(
                200, content=PNG, headers={"content-type": "image/png"}
            )

        with patch.object(
            self.source.httpx, "AsyncClient", self.client_factory(respond)
        ):
            first = asyncio.create_task(self.source.mix_emoji("😀", "😁"))
            await asyncio.wait_for(started.wait(), 1)
            second = asyncio.create_task(self.source.mix_emoji("😁", "😀"))
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
            self.assertEqual(await second, PNG)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.saved_files()), 1)

    async def test_corrupt_or_truncated_cache_is_repaired(self):
        self.directory.mkdir()
        path = self.directory / "u1f600_u1f601.png"
        corrupted = bytearray(PNG)
        corrupted[20] ^= 1
        for damaged in (b"not a png", PNG[:-8], bytes(corrupted)):
            with self.subTest(damaged=damaged[:8]):
                path.write_bytes(damaged)
                with patch.object(
                    self.source.httpx, "AsyncClient", self.successful_client()
                ):
                    self.assertEqual(await self.source.mix_emoji("😀", "😁"), PNG)
                self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(len(self.calls), 3)

    async def test_failed_atomic_save_still_delivers_and_removes_temporary_file(self):
        with (
            patch.object(self.source.httpx, "AsyncClient", self.successful_client()),
            patch.object(self.source.os, "replace", side_effect=OSError("disk full")),
        ):
            self.assertEqual(await self.source.mix_emoji("😀", "😁"), PNG)
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_unreadable_cache_directory_still_delivers_download(self):
        self.directory.write_bytes(b"not a directory")
        with patch.object(self.source.httpx, "AsyncClient", self.successful_client()):
            self.assertEqual(await self.source.mix_emoji("😀", "😁"), PNG)

    async def test_failed_proxy_uses_direct_without_inheriting_environment(self):
        self.source.emoji_config.http_proxy = "http://test-proxy.invalid:8888"

        def respond(request, options):
            if options["proxy"]:
                raise httpx.ConnectError("proxy TLS failure", request=request)
            return httpx.Response(
                200, content=PNG, headers={"content-type": "image/png"}
            )

        with patch.object(
            self.source.httpx, "AsyncClient", self.client_factory(respond)
        ):
            self.assertEqual(await self.source.mix_emoji("😀", "😁"), PNG)
        self.assertEqual([item["proxy"] for item in self.options], [
            "http://test-proxy.invalid:8888", None,
        ])
        self.assertTrue(all(item["trust_env"] is False for item in self.options))

    async def test_404_and_502_remain_retryable_without_negative_cache(self):
        status = 404

        def respond(request, options):
            return httpx.Response(status)

        with patch.object(
            self.source.httpx, "AsyncClient", self.client_factory(respond)
        ):
            self.assertIn("不支持", await self.source.mix_emoji("😀", "😁"))
            status = 502
            self.assertIn("下载出错", await self.source.mix_emoji("😀", "😁"))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.saved_files(), [])

    async def test_html_and_invalid_png_are_never_cached(self):
        invalid_images = (
            ("text/html", b"<html>error</html>"), ("image/png", b"bad")
        )
        for kind, content in invalid_images:
            with self.subTest(kind=kind):
                factory = self.client_factory(
                    lambda request, options: httpx.Response(
                        200, content=content, headers={"content-type": kind}
                    )
                )
                with patch.object(self.source.httpx, "AsyncClient", factory):
                    self.assertIn("下载出错", await self.source.mix_emoji("😀", "😁"))
                self.assertEqual(self.saved_files(), [])
        with patch.object(self.source.httpx, "AsyncClient", self.successful_client()):
            self.assertEqual(await self.source.mix_emoji("😀", "😁"), PNG)

    async def test_oversize_response_is_not_saved(self):
        with (
            patch.object(self.source, "MAX_IMAGE_BYTES", len(PNG) - 1),
            patch.object(self.source.httpx, "AsyncClient", self.successful_client()),
        ):
            self.assertIn("下载出错", await self.source.mix_emoji("😀", "😁"))
        self.assertEqual(self.saved_files(), [])

    async def test_total_timeout_cancels_download_and_clears_inflight(self):
        cancelled = asyncio.Event()

        async def respond(request, options):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with (
            patch.object(self.source, "DOWNLOAD_TIMEOUT", 0.02),
            patch.object(
                self.source.httpx, "AsyncClient", self.client_factory(respond)
            ),
        ):
            self.assertIn("下载出错", await self.source.mix_emoji("😀", "😁"))
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.source._inflight, {})
        self.assertEqual(self.saved_files(), [])

    async def test_canonical_emoji_codes_preserve_variation_selector(self):
        with patch.object(self.source.httpx, "AsyncClient", self.successful_client()):
            self.assertEqual(await self.source.mix_emoji("❤", "😀"), PNG)
        files = self.saved_files()
        self.assertIn("u2764-ufe0f", files[0].name)
        with patch.object(self.source.httpx, "AsyncClient", side_effect=AssertionError):
            self.assertEqual(await self.source.mix_emoji("😀", "❤"), PNG)

    async def test_unsupported_emoji_never_creates_http_client(self):
        with patch.object(self.source.httpx, "AsyncClient", side_effect=AssertionError):
            self.assertIn("不支持的emoji", await self.source.mix_emoji("a", "😁"))
        self.assertEqual(self.saved_files(), [])


if __name__ == "__main__":
    unittest.main()
