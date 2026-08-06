import re
import base64
from itertools import combinations
from collections import Counter

import emoji
from nonebot import on_message, require, Bot
from nonebot.matcher import Matcher
from nonebot.params import EventPlainText
from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot.typing import T_State
from nonebot.adapters import Event

require("nonebot_plugin_alconna")
require("nonebot_plugin_localstore")

from nonebot_plugin_alconna import UniMessage

from .config import Config
from .data_source import mix_emoji
from .runtime import access_policy
from . import admin as admin

__version__ = "0.5.0"

__plugin_meta__ = PluginMetadata(
    name="emoji合成",
    description="将两个emoji合成为一张图片",
    usage="发送两个及以上emoji，自动去重组合",
    type="application",
    homepage="https://github.com/LoCCai/nonebot-plugin-emojimix",
    config=Config,
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna"),
    extra={
        "example": "😎😁🥺",
    },
)

# 过滤并生成 emoji 正则
emojis = filter(lambda e: len(e) == 1, emoji.EMOJI_DATA.keys())
# 构建匹配 pattern
emoji_pattern_str = "|".join(re.escape(e) for e in emojis)
# 编译正则
emoji_regex = re.compile(f"({emoji_pattern_str})")


async def check_eomjis(
    bot: Bot,
    event: Event,
    state: T_State,
    text: str = EventPlainText(),
) -> bool:
    user_id = event.get_user_id()
    group_id = getattr(event, "group_id", None)
    if not access_policy.is_allowed(
        user_id,
        group_id,
        is_superuser=user_id in bot.config.superusers,
    ):
        return False
    text = text.strip()
    if not text:
        return False

    # 提取所有 emoji
    matched = emoji_regex.findall(text)
    valid_emojis = [e for e in matched if e.strip()]

    # 只要存在 2 个及以上 emoji 就触发处理逻辑
    if len(valid_emojis) >= 2:
        state["emoji_list"] = valid_emojis
        return True
    return False


emojimix = on_message(check_eomjis, block=True, priority=13)


@emojimix.handle()
async def _(bot: Bot, event: Event, state: T_State, matcher: Matcher):
    raw_emoji_list = state["emoji_list"]
    valid_results = []

    # --- 1. 数据预处理：去重与分类 ---
    # 统计词频
    counts = Counter(raw_emoji_list)

    # 找出所有重复出现的表情 (例如：['😎', '😎'] -> duplicate=['😎'])
    duplicate_emojis = [e for e, count in counts.items() if count > 1]

    # 生成去重列表，使用 dict.fromkeys 保留原始输入顺序
    unique_emojis = list(dict.fromkeys(raw_emoji_list))

    # --- 2. 核心逻辑：自我合并测试 ---
    # 如果一个表情出现了多次，说明用户可能想看它的自我融合（如 😳+😳）
    for e in duplicate_emojis:
        res = await mix_emoji(e, e)
        # 只要不是字符串(错误信息)，就加入结果
        if not isinstance(res, str):
            valid_results.append(res)

    # --- 3. 核心逻辑：交叉合并测试 ---
    # 对去重后的列表进行两两组合 (C_n^2)，确保组合唯一性
    # combinations 会生成迭代器：('A', 'B'), ('A', 'C'), ('B', 'C')
    for e1, e2 in combinations(unique_emojis, 2):
        res = await mix_emoji(e1, e2)
        if not isinstance(res, str):
            valid_results.append(res)

    # --- 4. 结果校验 ---
    # 如果没有生成任何一张图片（说明全是无法支持的组合），直接结束，不打扰群聊
    if not valid_results:
        await matcher.finish()

    # --- 5. 消息构建与发送 ---

    # 尝试使用 OneBot V11 的合并转发
    sent_via_forward = False
    try:
        from nonebot.adapters.onebot.v11 import Bot as V11Bot
        from nonebot.adapters.onebot.v11 import MessageSegment as V11Seg
        from nonebot.adapters.onebot.v11 import Message as V11Msg

        if isinstance(bot, V11Bot):
            target_id = getattr(event, "group_id", None)
            # 仅限群聊使用合并转发
            if target_id:
                nodes = []
                for img_bytes in valid_results:
                    b64_img = base64.b64encode(img_bytes).decode()
                    file_uri = f"base64://{b64_img}"

                    nodes.append(
                        V11Seg.node_custom(
                            user_id=int(bot.self_id),
                            nickname="EmojiMix",
                            content=V11Msg(V11Seg.image(file=file_uri)),
                        )
                    )

                await bot.call_api(
                    "send_group_forward_msg", group_id=target_id, messages=nodes
                )
                sent_via_forward = True

    except Exception:
        # 忽略所有 V11 相关错误，准备降级
        pass

    # 如果没有通过合并转发发送（非群聊、非V11、或报错），降级为普通消息发送
    if not sent_via_forward:
        msg = UniMessage()
        for img_bytes in valid_results:
            msg += UniMessage.image(raw=img_bytes)
        # 直接发送
        await msg.send()

    # --- 6. 结束控制 ---
    # 按照要求，最后使用 finish 空消息结束控制
    await matcher.finish()
