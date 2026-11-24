# 聊天室 TCP 协议 v1

开发由三人分别负责前端、后端和 Agent。前端负责客户端界面与 Socket 通信，后端负责 TCP 服务、SQLite 与数据访问层、权限检查及 Agent 调度，Agent 开发者负责模型接入和工具调用。

本协议由前端和后端共同维护，覆盖注册登录、在线列表、群聊、私聊、历史检索、延迟监测和 Agent 消息。Agent 的服务端调用见 [Agent 接口](agent_interface.md)。

## 1. TCP 分帧

本机联调默认使用 `127.0.0.1:8765`。跨机器演示时，服务端配置可访问的监听地址，客户端填写服务端 IP 和端口。

| 部分 | 格式 |
| --- | --- |
| 长度 | 4 字节无符号整数，网络字节序（大端），表示后续载荷的字节数 |
| 载荷 | UTF-8 编码的单个 JSON 对象，长度为 1–1048576 字节 |

`shared/codec.py` 约定提供 `encode_frame(message) -> bytes` 和 `FrameDecoder.feed(chunk: bytes) -> list[dict]`。前后端每条连接各持有一个解码器。收到数据时循环取出所有完整帧，保留不足一帧的尾部。读到长度头后立即校验长度。零长度、超限、非法 UTF-8、非法 JSON 或载荷根节点类型错误均关闭连接。底层读到 `b""` 也结束连接。发送方对同一连接串行发送完整帧，防止并发写入交错。

`shared/protocol.py` 维护协议版本、消息类型和错误码。下述业务字段在 JSON 中必填，标为可选的字段可省略。整型字段使用 JSON 整数。协议默认在课程演示环境使用普通 TCP，注册密码使用演示专用密码，客户端和服务端均不记录其明文。

## 2. 消息结构

客户端请求包含 `v`、`type`、`request_id`、`data`。`v` 固定为 `1`，`type` 为请求名，`data` 为对象。每个请求用一个在当前连接内唯一的 `request_id`，取 1–64 个 ASCII 字母、数字、下划线或短横线。

```json
{"v":1,"type":"message.send","request_id":"c1-8","data":{"conversation_id":"room:general","text":"周五前提交演示录屏"}}
```

服务端为每个有效请求返回一次 `type: "response"`，原样带回 `request_id`。成功时 `data` 为 `{"ok":true,"result":{...}}`，失败时为 `{"ok":false,"error":{"code":"…","message":"…"}}`。客户端根据 `code` 处理错误。收到响应和收到推送可以交错，客户端据 `request_id` 匹配响应。

```json
{"v":1,"type":"response","request_id":"c1-8","data":{"ok":false,"error":{"code":"AUTH_REQUIRED","message":"请先登录"}}}
```

服务端主动推送使用事件名作为 `type`，并省略 `request_id`。

```json
{"v":1,"type":"presence.changed","data":{"users":[{"user_id":"usr_1","username":"alice"}]}}
```

缺少可用 `request_id` 或收到处理不了的协议版本时关闭连接。版本和 `request_id` 有效时，未知请求类型返回 `UNKNOWN_TYPE`，其余结构和参数错误返回 `INVALID_ARGUMENT`。客户端请求只填写本协议定义的字段，服务端对额外字段也返回 `INVALID_ARGUMENT`。

## 3. 数据对象

| 对象 | 字段 |
| --- | --- |
| `User` | `user_id: string`，`username: string` |
| `Conversation` | `conversation_id: string`，`kind: "group" or "dm"`，`peer: User or null`（私聊的对方，群聊为 `null`） |
| `Message` | `message_id: int`，`conversation_id: string`，`sender: User`，`kind: "user" or "agent"`，`text: string`，`created_at_ms: int`，`task_id: string or null` |

- `message_id` 由服务端按持久化顺序分配，全局严格递增，取值范围为 1 至 2⁵³−1。`created_at_ms` 为服务端生成的 Unix 毫秒时间戳。排序、去重和分页统一使用 `message_id`。
- 公共群聊 ID 固定为 `room:general`，所有已登录用户均可发言和查阅。私聊只对两名参与者开放。同一对用户共用一个稳定的 `conversation_id`，前端直接使用服务端返回的 ID。
- Agent 使用固定身份 `{"user_id":"agent","username":"Agent"}`。普通聊天消息的 `task_id` 为 `null`，Agent 触发消息和回复共用任务 ID。
- 服务端从连接会话确定发件人。客户端在请求里只提交会话、正文等业务参数。

## 4. 客户端请求

| `type` | `data` | 成功时的 `result` |
| --- | --- | --- |
| `auth.register` | `username: string`，`password: string` | `user: User` |
| `auth.login` | `username: string`，`password: string` | `user: User` |
| `auth.logout` | `{}` | `{}`，随后服务端关闭连接 |
| `users.online` | `{}` | `users: User[]`，含当前登录用户 |
| `conversation.list` | `{}` | `conversations: Conversation[]`，包括公共群聊和当前用户已创建或参与的私聊 |
| `conversation.open_dm` | `peer_username: string` | `conversation: Conversation`，已有会话时返回同一对象，允许对方离线 |
| `message.send` | `conversation_id: string`，`text: string` | `message: Message`，即服务端持久化后的消息 |
| `history.list` | `conversation_id: string`，可选 `before_id: int`、`limit: int`、`keyword: string` | `messages: Message[]`，`next_before_id: int or null` |
| `ping` | `{}` | `{}` |
| `agent.run` | `conversation_id: string`，`instruction: string`，可选 `mode: "auto" or "summary" or "todos"` | `task_id: string`，`message: Message`（触发消息） |

- 用户名为 3–32 个 ASCII 字母、数字或下划线，服务端按小写存储和查询。保留用户名 `agent`。密码长度为 8–128 个 Unicode 字符，按原值验证。注册后需登录，登录状态绑定 TCP 连接。同一用户最多有一条登录连接。
- `peer_username` 同样按小写查找。尝试给自己开私聊时返回 `INVALID_ARGUMENT`。
- `text` 和 `instruction` 去除首尾空白后限 1–2000 个 Unicode 字符。`keyword` 去除首尾空白后限 1–100 个 Unicode 字符，按 `text` 的字面子串匹配，ASCII 字母忽略大小写。文本超限统一返回 `INVALID_ARGUMENT`。
- 查询历史默认取最近 20 条，`limit` 取 1–50。`before_id` 与 `message_id` 取值范围相同，查询仅返回 ID 严格小于该值的消息。先按会话和关键词筛选，再从匹配项中取最新一页，页内按 ID 升序返回。还有更早的匹配项时，`next_before_id` 等于本页最小 ID，否则为 `null`。客户端翻页时保留原会话和关键词。
- 用户输入以 `@Agent ` 开头时，客户端去掉此前缀，用其余内容发 `agent.run`。专用摘要或待办入口可以显式传 `mode`。省略 `mode` 时使用 `auto`，由 Agent 按指令完成问答、摘要或待办提取。服务端把触发消息保存为 `@Agent ` 加指令正文。`message.send` 统一按普通消息处理。
- `message.send` 和 `agent.run` 均先写入数据库，再返回成功并向其他在场成员推送该消息。写入失败时返回错误，且本次消息不推送。成功响应表示服务端已保存消息。离线接收者下次登录可查历史。客户端遇到连接中断、未收到响应时，重连后先查历史确认是否已写入，再决定是否重发。

## 5. 服务端事件

| `type` | `data` | 接收范围 |
| --- | --- | --- |
| `presence.changed` | `users: User[]`，当前在线快照 | 已登录客户端。登录、登出和异常断线释放会话后推送 |
| `message.new` | `message: Message` | 公共群聊的所有在线用户或对应私聊的在线参与者。用户发送的消息由发送方从成功响应取得，其他人接收推送。Agent 回复推给该会话内所有在线成员 |
| `agent.task` | `task_id: string`，`conversation_id: string`，`state: "running" or "succeeded" or "failed"`。成功附 `message_id: int`，失败附 `error: {code, message}` | 发起者的当前登录连接 |
| `agent.tool` | `task_id: string`，`conversation_id: string`，`call_id: string`，`name: string`，`state: "started" or "succeeded" or "failed"`。成功附 `result_count: int`，失败附 `error: {code, message}` | 发起者的当前登录连接 |

对发起者而言，服务端先将 `agent.run` 的成功响应加入该连接的发送队列，再调度任务。成功时先推送持久化后的回复，再推送 `agent.task` 的 `succeeded`。工具事件只带调用状态、条数或错误信息。状态与工具事件实时发送，重连后的回复靠历史记录恢复。客户端用 `message_id` 合并历史、响应和新消息，用 `task_id` 与 `call_id` 更新对应的任务和工具行。

## 6. 连接和错误处理

1. 建立 TCP 连接后，允许注册、登录和 `ping`。其余请求须先登录。已登录连接再次注册或登录返回 `INVALID_STATE`。服务端先向新登录的客户端发送登录响应，再推送事件。客户端登录成功后可并行查询在线列表、会话列表和当前聊天历史。
2. 客户端每隔 15 秒发一次 `ping`。用客户端单调时钟记录匹配请求发出至收到响应的时差，显示应用层往返延迟。服务端在连接连续 45 秒未收到可解析的请求时释放连接和在线状态。
3. 登出、TCP EOF、连接异常和心跳超时走同一套清理流程。服务端只清理该连接对应的会话。客户端重连后重新登录并刷新列表和历史。

| 错误码 | 使用场景 |
| --- | --- |
| `UNKNOWN_TYPE`、`INVALID_ARGUMENT` | 请求类型未知或字段缺失、取值错误 |
| `AUTH_REQUIRED`、`INVALID_STATE` | 尚未登录或请求与当前连接状态冲突 |
| `USERNAME_TAKEN`、`INVALID_CREDENTIALS`、`ALREADY_ONLINE` | 用户名已注册、凭据无效或账户已经在线 |
| `USER_NOT_FOUND`、`CONVERSATION_NOT_FOUND`、`FORBIDDEN` | 私聊对象不存在、会话不存在或当前用户无权访问 |
| `AGENT_BUSY` | 后端任务队列已满或当前用户已有进行中的任务，拒绝接收 `agent.run` |
| `AGENT_UNAVAILABLE`、`AGENT_TIMEOUT`、`AGENT_FAILED` | 已受理的 Agent 任务无法访问模型、超时或执行失败 |
| `INTERNAL_ERROR` | 后端读写、调度或其他内部操作失败 |

## 7. 联调用例

1. 用三个客户端登录。测试多帧一起到达、帧头或正文分次到达，以及强制结束一个客户端后其他客户端收到在线列表变化。
2. 在公共群聊发消息，再发一条私聊。验证第三人只收到群聊，并且使用私聊 ID 查询时返回 `FORBIDDEN`。发件人只显示一条自己的消息。
3. 重新登录后搜索群聊和私聊历史。对相同关键词翻页，检查消息 ID 的顺序且没有重复。确认 `ping` 的响应能单独匹配并更新延迟显示。
4. 在群里发 `@Agent 总结刚才的消息`，查看真实历史工具的 `started` 和 `succeeded` 事件，并确认其他在线成员收到回复。模拟模型不可用，确认失败事件到达且聊天仍能收发。
