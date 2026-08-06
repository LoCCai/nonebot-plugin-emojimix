from typing import Optional

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from .access import normalize_identifier
from .runtime import access_policy


MODE_ALIASES = {
    "开放": "open",
    "全开": "open",
    "open": "open",
    "黑名单": "blacklist",
    "blacklist": "blacklist",
    "白名单": "whitelist",
    "whitelist": "whitelist",
    "关闭": "disabled",
    "停用": "disabled",
    "disabled": "disabled",
}
MODE_LABELS = {
    "open": "开放模式",
    "blacklist": "黑名单模式",
    "whitelist": "白名单模式",
    "disabled": "关闭模式",
}
TARGET_ALIASES = {
    "用户": "users",
    "成员": "users",
    "user": "users",
    "users": "users",
    "群": "groups",
    "群组": "groups",
    "group": "groups",
    "groups": "groups",
}


def _group_id(event: Event) -> Optional[str]:
    value = getattr(event, "group_id", None)
    return str(value) if value is not None else None


def _mentioned_users(event: Event) -> list[str]:
    result: list[str] = []
    try:
        message = event.get_message()
    except (AttributeError, ValueError):
        return result
    for segment in message:
        if getattr(segment, "type", "") != "at":
            continue
        data = getattr(segment, "data", {})
        for key in ("qq", "user_id", "target"):
            value = str(data.get(key) or "").strip()
            if value and value != "all":
                result.append(value)
                break
    return result


def _target(event: Event, argument: Message) -> tuple[str, str]:
    text = argument.extract_plain_text().strip()
    parts = text.split()
    mentions = _mentioned_users(event)
    if parts and parts[0] in {"本群", "当前群"}:
        group = _group_id(event)
        if not group:
            raise ValueError("当前不是群聊，请明确提供群号")
        return "groups", normalize_identifier(group)
    if parts and parts[0] in {"自己", "本人"}:
        return "users", normalize_identifier(event.get_user_id())
    if parts and parts[0].lower() in TARGET_ALIASES:
        target_type = TARGET_ALIASES[parts.pop(0).lower()]
        if parts:
            return target_type, normalize_identifier(parts[0])
        if target_type == "users" and mentions:
            return target_type, normalize_identifier(mentions[0])
        raise ValueError("请在用户或群后提供对应编号")
    if mentions:
        return "users", normalize_identifier(mentions[0])
    if parts:
        return "users", normalize_identifier(parts[0])
    raise ValueError("请提供用户、群、本群或要操作的 @用户")


def _list_preview(values: list[str]) -> str:
    if not values:
        return "无"
    preview = "、".join(values[:30])
    return preview if len(values) <= 30 else f"{preview}……另有 {len(values) - 30} 项"


def _status_text() -> str:
    state = access_policy.snapshot()
    whitelist = state["whitelist"]
    blacklist = state["blacklist"]
    lines = [
        "EmojiMix 权限状态",
        f"模式：{MODE_LABELS[state['mode']]}",
        f"白名单用户（{len(whitelist['users'])}）：{_list_preview(whitelist['users'])}",
        f"白名单群（{len(whitelist['groups'])}）：{_list_preview(whitelist['groups'])}",
        f"黑名单用户（{len(blacklist['users'])}）：{_list_preview(blacklist['users'])}",
        f"黑名单群（{len(blacklist['groups'])}）：{_list_preview(blacklist['groups'])}",
        "",
        "超级用户指令：",
        "emoji模式 开放/黑名单/白名单/关闭",
        "emoji加白 用户 账号 ｜ emoji加白 群 群号 ｜ emoji加白 @用户 ｜ emoji加白 本群",
        "emoji移白、emoji加黑、emoji移黑的参数格式相同",
    ]
    if access_policy.last_error:
        lines.append(f"注意：{access_policy.last_error}")
    return "\n".join(lines)


status_command = on_command(
    "emoji权限",
    aliases={"表情混合权限", "EmojiMix权限"},
    permission=SUPERUSER,
    priority=2,
    block=True,
)
mode_command = on_command(
    "emoji模式",
    aliases={"表情混合模式", "EmojiMix模式"},
    permission=SUPERUSER,
    priority=2,
    block=True,
)
add_white_command = on_command(
    "emoji加白", aliases={"表情加白"}, permission=SUPERUSER, priority=2, block=True
)
remove_white_command = on_command(
    "emoji移白", aliases={"表情移白"}, permission=SUPERUSER, priority=2, block=True
)
add_black_command = on_command(
    "emoji加黑", aliases={"表情加黑"}, permission=SUPERUSER, priority=2, block=True
)
remove_black_command = on_command(
    "emoji移黑", aliases={"表情移黑"}, permission=SUPERUSER, priority=2, block=True
)


@status_command.handle()
async def _status() -> None:
    await status_command.finish(_status_text())


@mode_command.handle()
async def _mode(argument: Message = CommandArg()) -> None:
    value = argument.extract_plain_text().strip().lower()
    mode = MODE_ALIASES.get(value)
    if not mode:
        await mode_command.finish("模式只能是：开放、黑名单、白名单、关闭")
    await access_policy.set_mode(mode)
    await mode_command.finish(f"已切换为{MODE_LABELS[mode]}")


async def _change(
    matcher: Matcher,
    event: Event,
    argument: Message,
    list_name: str,
    add: bool,
) -> None:
    try:
        target_type, identifier = _target(event, argument)
        changed = await access_policy.change_member(
            list_name, target_type, identifier, add=add
        )
    except (OSError, ValueError) as exc:
        await matcher.finish(f"操作失败：{exc}")
        return
    list_label = "白名单" if list_name == "whitelist" else "黑名单"
    target_label = "用户" if target_type == "users" else "群"
    action_label = "加入" if add else "移出"
    result = "已完成" if changed else "名单没有变化"
    await matcher.finish(
        f"{result}：{target_label} {identifier} 已{action_label}{list_label}"
    )


@add_white_command.handle()
async def _add_white(event: Event, argument: Message = CommandArg()) -> None:
    await _change(add_white_command, event, argument, "whitelist", True)


@remove_white_command.handle()
async def _remove_white(event: Event, argument: Message = CommandArg()) -> None:
    await _change(remove_white_command, event, argument, "whitelist", False)


@add_black_command.handle()
async def _add_black(event: Event, argument: Message = CommandArg()) -> None:
    await _change(add_black_command, event, argument, "blacklist", True)


@remove_black_command.handle()
async def _remove_black(event: Event, argument: Message = CommandArg()) -> None:
    await _change(remove_black_command, event, argument, "blacklist", False)
