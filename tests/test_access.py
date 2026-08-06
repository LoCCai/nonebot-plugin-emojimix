import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ACCESS_PATH = Path(__file__).parents[1] / "nonebot_plugin_emojimix" / "access.py"
SPEC = importlib.util.spec_from_file_location("emojimix_access", ACCESS_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("无法加载 EmojiMix 权限模块")
ACCESS_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS_MODULE)
AccessPolicy = ACCESS_MODULE.AccessPolicy
normalize_identifier = ACCESS_MODULE.normalize_identifier


class AccessPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "permissions.json"
        self.policy = AccessPolicy(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_blacklist_mode_denies_matching_user_or_group(self) -> None:
        asyncio.run(self.policy.change_member("blacklist", "users", "10001", add=True))
        asyncio.run(self.policy.change_member("blacklist", "groups", "20001", add=True))
        self.assertFalse(self.policy.is_allowed("10001", "30001"))
        self.assertFalse(self.policy.is_allowed("30001", "20001"))
        self.assertTrue(self.policy.is_allowed("30001", "30001"))

    def test_whitelist_mode_accepts_user_or_group(self) -> None:
        asyncio.run(self.policy.set_mode("whitelist"))
        asyncio.run(self.policy.change_member("whitelist", "users", "10001", add=True))
        asyncio.run(self.policy.change_member("whitelist", "groups", "20001", add=True))
        self.assertTrue(self.policy.is_allowed("10001", "30001"))
        self.assertTrue(self.policy.is_allowed("30001", "20001"))
        self.assertFalse(self.policy.is_allowed("30001", "30001"))

    def test_superuser_bypasses_disabled_mode(self) -> None:
        asyncio.run(self.policy.set_mode("disabled"))
        self.assertFalse(self.policy.is_allowed("10001", "20001"))
        self.assertTrue(self.policy.is_allowed("10001", "20001", is_superuser=True))

    def test_adding_to_one_list_removes_from_opposite_list(self) -> None:
        asyncio.run(self.policy.change_member("blacklist", "users", "10001", add=True))
        asyncio.run(self.policy.change_member("whitelist", "users", "10001", add=True))
        self.assertNotIn("10001", self.policy.blacklist_users)
        self.assertIn("10001", self.policy.whitelist_users)

    def test_changes_are_atomic_and_persistent(self) -> None:
        asyncio.run(self.policy.set_mode("whitelist"))
        asyncio.run(self.policy.change_member("whitelist", "groups", "20001", add=True))
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document["mode"], "whitelist")
        self.assertEqual(document["whitelist"]["groups"], ["20001"])
        restored = AccessPolicy(self.path)
        self.assertEqual(restored.mode, "whitelist")
        self.assertTrue(restored.is_allowed("30001", "20001"))

    def test_invalid_identifier_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_identifier("bad value")


if __name__ == "__main__":
    unittest.main()
