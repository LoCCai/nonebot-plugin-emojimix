import nonebot_plugin_localstore as localstore

from .access import AccessPolicy
from .config import emoji_config


access_policy = AccessPolicy(
    localstore.get_plugin_data_file("permissions.json"),
    default_mode=emoji_config.emojimix_permission_mode,
    whitelist_users=emoji_config.emojimix_whitelist_users,
    whitelist_groups=emoji_config.emojimix_whitelist_groups,
    blacklist_users=emoji_config.emojimix_blacklist_users,
    blacklist_groups=emoji_config.emojimix_blacklist_groups,
)
