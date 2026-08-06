# nonebot-plugin-emojimix

适用于 [Nonebot2](https://github.com/nonebot/nonebot2) 的 emoji 合成器

😎+😁=？


### 安装

- 使用 nb-cli

```
nb plugin install nonebot_plugin_emojimix
```

- 使用 pip

```
pip install nonebot_plugin_emojimix
```

若经常下载出错，可以在 `.env.xxx` 文件中设置代理：

```
http_proxy=http://ip:port
```

### 权限模式

插件支持用户和群两个层级的持久化黑白名单。运行时修改会保存到
`nonebot-plugin-localstore` 分配的插件数据目录中，重启后仍然生效。

- `黑名单`：默认模式。除黑名单用户和黑名单群外均可使用。
- `白名单`：只有白名单用户，或白名单群内的用户可以使用。
- `开放`：所有会话都可以使用。
- `关闭`：普通用户全部停用。
- 超级用户在所有模式下都可以使用，并且只有超级用户能执行管理指令。

可以在 NoneBot 配置中设置初始值：

```dotenv
EMOJIMIX_PERMISSION_MODE=blacklist
EMOJIMIX_WHITELIST_USERS=["123456"]
EMOJIMIX_WHITELIST_GROUPS=["987654"]
EMOJIMIX_BLACKLIST_USERS=[]
EMOJIMIX_BLACKLIST_GROUPS=[]
```

超级用户指令：

```text
emoji权限
emoji模式 开放
emoji模式 黑名单
emoji模式 白名单
emoji模式 关闭

emoji加白 用户 123456
emoji加白 群 987654
emoji加白 @某人
emoji加白 本群

emoji移白 用户 123456
emoji加黑 群 987654
emoji移黑 本群
```

将对象加入白名单时，会自动把同一对象从黑名单移除；反向操作同理。


### 示例

<div align="left">
  <img src="https://s2.loli.net/2022/01/23/EyoA1BHe9YpJZUD.png" width="400" />
</div>
