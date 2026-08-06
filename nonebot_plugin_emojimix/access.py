import asyncio
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional


ACCESS_MODES = {"open", "blacklist", "whitelist", "disabled"}
LIST_NAMES = {"whitelist", "blacklist"}
TARGET_TYPES = {"users", "groups"}
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9:_-]{1,100}$")


def normalize_identifier(value: Any) -> str:
    identifier = str(value or "").strip()
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError("用户或群标识只能包含字母、数字、冒号、下划线和短横线")
    return identifier


def normalize_mode(value: Any, default: str = "blacklist") -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in ACCESS_MODES else default


def _identifiers(values: Iterable[Any]) -> set[str]:
    result: set[str] = set()
    for value in values:
        try:
            result.add(normalize_identifier(value))
        except ValueError:
            continue
    return result


class AccessPolicy:
    """Persistent user/group access policy with atomic updates."""

    def __init__(
        self,
        path: Path,
        *,
        default_mode: str = "blacklist",
        whitelist_users: Iterable[Any] = (),
        whitelist_groups: Iterable[Any] = (),
        blacklist_users: Iterable[Any] = (),
        blacklist_groups: Iterable[Any] = (),
    ) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.last_error = ""
        self.mode = normalize_mode(default_mode)
        self.whitelist_users = _identifiers(whitelist_users)
        self.whitelist_groups = _identifiers(whitelist_groups)
        self.blacklist_users = _identifiers(blacklist_users)
        self.blacklist_groups = _identifiers(blacklist_groups)
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("权限文件根节点不是对象")
            whitelist = raw.get("whitelist")
            blacklist = raw.get("blacklist")
            whitelist = whitelist if isinstance(whitelist, dict) else {}
            blacklist = blacklist if isinstance(blacklist, dict) else {}
            self.mode = normalize_mode(raw.get("mode"), self.mode)
            self.whitelist_users = _identifiers(whitelist.get("users", []))
            self.whitelist_groups = _identifiers(whitelist.get("groups", []))
            self.blacklist_users = _identifiers(blacklist.get("users", []))
            self.blacklist_groups = _identifiers(blacklist.get("groups", []))
            self.last_error = ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.last_error = f"权限文件读取失败，正在使用配置默认值：{exc}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "mode": self.mode,
            "whitelist": {
                "users": sorted(self.whitelist_users),
                "groups": sorted(self.whitelist_groups),
            },
            "blacklist": {
                "users": sorted(self.blacklist_users),
                "groups": sorted(self.blacklist_groups),
            },
        }

    def is_allowed(
        self,
        user_id: Any,
        group_id: Optional[Any] = None,
        *,
        is_superuser: bool = False,
    ) -> bool:
        if is_superuser:
            return True
        user = str(user_id or "").strip()
        group = str(group_id or "").strip()
        if self.mode == "open":
            return True
        if self.mode == "disabled":
            return False
        if self.mode == "blacklist":
            return user not in self.blacklist_users and (
                not group or group not in self.blacklist_groups
            )
        return user in self.whitelist_users or bool(
            group and group in self.whitelist_groups
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        descriptor = os.open(
            str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    self.snapshot(),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(self.path))
            os.chmod(self.path, 0o600)
            self.last_error = ""
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    async def set_mode(self, mode: str) -> bool:
        normalized = normalize_mode(mode, "")
        if not normalized:
            raise ValueError("未知权限模式")
        async with self._lock:
            changed = normalized != self.mode
            self.mode = normalized
            self._save()
            return changed

    async def change_member(
        self,
        list_name: str,
        target_type: str,
        identifier: Any,
        *,
        add: bool,
    ) -> bool:
        if list_name not in LIST_NAMES or target_type not in TARGET_TYPES:
            raise ValueError("未知名单类型")
        normalized = normalize_identifier(identifier)
        selected: set[str] = getattr(self, f"{list_name}_{target_type}")
        opposite_name = "blacklist" if list_name == "whitelist" else "whitelist"
        opposite: set[str] = getattr(self, f"{opposite_name}_{target_type}")
        async with self._lock:
            if add:
                changed = normalized not in selected or normalized in opposite
                selected.add(normalized)
                opposite.discard(normalized)
            else:
                changed = normalized in selected
                selected.discard(normalized)
            self._save()
            return changed
