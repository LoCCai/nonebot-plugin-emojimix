from typing import Optional

from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    http_proxy: Optional[str] = None
    emojimix_permission_mode: str = "blacklist"
    emojimix_whitelist_users: list[str] = Field(default_factory=list)
    emojimix_whitelist_groups: list[str] = Field(default_factory=list)
    emojimix_blacklist_users: list[str] = Field(default_factory=list)
    emojimix_blacklist_groups: list[str] = Field(default_factory=list)


emoji_config = get_plugin_config(Config)
