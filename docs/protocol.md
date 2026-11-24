# 聊天室 TCP 协议 v1

开发由三人分别负责前端、后端和 Agent。前端负责客户端界面与 Socket 通信，后端负责 TCP 服务、SQLite 与数据访问层、权限检查及 Agent 调度，Agent 开发者负责模型接入和工具调用。

本协议由前端和后端共同维护，覆盖注册登录、在线列表、群聊、私聊、历史检索、延迟监测和 Agent 消息。服务端与 Agent 的 Python 调用契约见 [Agent 接口](agent_interface.md)。

## 1. TCP 分帧

本机联调默认使用 `127.0.0.1:8765`。跨机器演示时，服务端配置可访问的监听地址，客户端填写服务端 IP 和端口。

在仓库根目录用 Python 3.11 或更新版本运行 `python -m server.main`。服务端读取以下环境变量。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SERVER_HOST` | `127.0.0.1` | TCP 监听地址 |
| `SERVER_PORT` | `8765` | TCP 监听端口 |
| `SERVER_DB_PATH` | `chatroom.sqlite3` | SQLite 文件路径。相对路径以进程工作目录为准 |

| 部分 | 格式 |
| --- | --- |
| 长度 | 4 字节无符号整数，网络字节序（大端），表示后续载荷的字节数 |
| 载荷 | UTF-8 编码的单个 JSON 对象，长度为 1–1048576 字节 |

`shared/codec.py` 约定提供 `encode_frame(message) -> bytes` 和 `FrameDecoder.feed(chunk: bytes) -> list[dict]`。前后端每条连接各持有一个解码器。收到数据时循环取出所有完整帧，保留不足一帧的尾部。读到长度头后立即校验长度。零长度、超限、非法 UTF-8、非法 JSON 或载荷根节点类型错误均关闭连接。底层读到 `b""` 也结束连接。发送方对同一连接串行发送完整帧，防止并发写入交错。

`shared/protocol.py` 维护协议版本、消息类型和错误码。下述业务字段在 JSON 中必填，标为可选的字段可省略。整型字段使用 JSON 整数。协议默认在课程演示环境使用普通 TCP，注册密码使用演示专用密码，客户端和服务端均不记录其明文。

计算长度前先把 JSON 编码为 UTF-8。以紧凑编码的 `{"text":"你好"}` 为例，载荷有 17 字节，帧头因此是十六进制的 `00 00 00 11`。TCP 单次读取可能只有部分帧头，也可能包含多个完整帧和下一帧的开头。客户端按长度解析，帧之间无需换行符。

## 2. 消息结构

客户端请求包含 `v`、`type`、`request_id`、`data`。`v` 固定为 `1`，`type` 为请求名，`data` 为对象。每个请求用一个在当前连接内唯一的 `request_id`，取 1–64 个 ASCII 字母、数字、下划线或短横线。

| 字段 | JSON 类型 | 约束 |
| --- | --- | --- |
| `v` | 整数 | 固定为 `1`。`true`、`1.0` 和字符串 `"1"` 均无效 |
| `type` | 字符串 | 第 4 节定义的请求名，区分大小写 |
| `request_id` | 字符串 | 完整匹配 `[A-Za-z0-9_-]{1,64}`，在当前 TCP 连接内不得重复使用 |
| `data` | 对象 | 字段按请求类型确定。无业务参数时传 `{}` |

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

服务端先处理分帧，并检查 `v` 和 `request_id` 是否可用。任一检查失败时直接关闭连接，客户端按断连处理。此后的错误通过 `response` 返回，连接保持可用。

1. 重复使用 `request_id`、缺少顶层 `type` 或 `data`、含额外顶层字段时返回 `INVALID_ARGUMENT`。因此，请求同时包含未知类型和额外顶层字段时也返回 `INVALID_ARGUMENT`。
2. `type` 不是字符串或 `data` 不是对象时返回 `INVALID_ARGUMENT`。请求顶层符合要求且请求名未知时返回 `UNKNOWN_TYPE`。
3. 请求名已知时，检查 `data` 的必填字段、额外字段、类型和取值。已定义的可选字段可以省略。整型参数只接受 JSON 整数。对仅声明为字符串或整数的可选字段，传 `null` 会返回 `INVALID_ARGUMENT`。
4. 结构与参数通过校验后，再检查登录状态、资源存在性、会话权限和任务容量。

同一连接可同时等待多个请求。收到 `response` 时，用 `request_id` 找到对应的等待项。收到事件时，按 `type` 分发给事件处理逻辑。`request_id` 只用于当前连接的请求关联，消息历史无此字段。

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

客户端应原样保存服务端分配的 `user_id`、私聊 `conversation_id` 和 `task_id`。`Conversation.peer` 从当前用户的视角返回对方，私聊两端的 `conversation_id` 相同，`peer` 不同。

| 消息来源 | `kind` | `sender` | `task_id` | `text` |
| --- | --- | --- | --- | --- |
| `message.send` | `user` | 已登录用户 | `null` | 去除首尾空白后的正文 |
| `agent.run` 触发消息 | `user` | 任务发起者 | 任务 ID | `@Agent ` 加去除首尾空白后的指令 |
| Agent 回复 | `agent` | Agent 固定身份 | 与触发消息相同 | 去除首尾空白后的回复正文 |

普通群聊消息示例。

```json
{
  "message_id": 42,
  "conversation_id": "room:general",
  "sender": {"user_id": "usr_1", "username": "alice"},
  "kind": "user",
  "text": "周五前提交演示录屏",
  "created_at_ms": 1735689600000,
  "task_id": null
}
```

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
- `message.send` 和 `agent.run` 均先写入数据库，再返回成功并向其他在场成员推送该消息。写入失败时返回错误，且本次消息不推送。成功响应表示服务端已保存消息。离线接收者下次登录可查历史。如果写入响应在断线时丢失，重连后先对照历史中的发件人、正文和时间再决定是否重发。多条消息内容相同时，历史可能无法确认哪一条对应上次请求。重发会作为新请求处理。

### 4.1 注册、登录与在线状态

1. 新用户先发 `auth.register`，再发 `auth.login`。两条请求的 `data` 都是 `{"username":"Alice","password":"demo-password"}`，使用不同的 `request_id`。注册成功后允许继续使用同一条连接登录。
2. 登录成功响应包含 `{"user":{"user_id":"usr_1","username":"alice"}}`。服务端随后推送 `presence.changed`。客户端可请求 `users.online` 取当前快照。
3. `auth.logout` 的 `data` 为 `{}`。服务端发送成功响应后关闭连接，并向其他在线用户推送新的在线快照。

用户名和密码按原样提交。服务端仅把用户名转为小写存储与查找。保留名 `agent` 的任意大小写形式，注册返回 `INVALID_ARGUMENT`，登录返回 `INVALID_CREDENTIALS`。

### 4.2 会话与发消息

- 客户端登录后通过 `conversation.list` 获取公共群聊与自己的私聊。`conversation.open_dm` 的 `peer_username` 使用与用户名相同的字符和长度限制。对已注册但离线的用户也能创建私聊。重复打开同一对用户的私聊，服务端返回同一会话 ID。
- 给自己开私聊返回 `INVALID_ARGUMENT`，用户不存在时返回 `USER_NOT_FOUND`。对有效格式的会话参数，找不到对应会话返回 `CONVERSATION_NOT_FOUND`。会话存在但当前用户无访问权时，`message.send`、`history.list` 和 `agent.run` 均返回 `FORBIDDEN`。
- 用 `message.send` 发送时，从成功响应的 `result.message` 更新本地聊天列表。其他在线成员通过 `message.new` 收到相同的消息对象。

### 4.3 历史分页示例

假设群聊中匹配关键词 `demo` 的消息 ID 为 `4`、`9`、`13`、`21`，其余记录不匹配。客户端先请求最新一页。

```json
{"v":1,"type":"history.list","request_id":"c1-9","data":{"conversation_id":"room:general","limit":2,"keyword":"demo"}}
```

`result.messages` 按 ID 升序返回 `13`、`21`，`result.next_before_id` 为 `13`。发下一页请求时，保留会话和关键词，并把光标作为 `before_id` 传回。

```json
{"v":1,"type":"history.list","request_id":"c1-10","data":{"conversation_id":"room:general","limit":2,"keyword":"demo","before_id":13}}
```

第二页返回 `4`、`9`，`next_before_id` 为 `null`。若查询没有匹配项，结果为 `{"messages":[],"next_before_id":null}`。关键词 `DEMO` 也会匹配 `demo`。`%` 和 `_` 按普通字符匹配，非 ASCII 字母按原值匹配。更换关键词时，从省略 `before_id` 的第一页重新查询。

### 4.4 触发 Agent

```json
{"v":1,"type":"agent.run","request_id":"c1-11","data":{"conversation_id":"room:general","instruction":"总结最近的讨论","mode":"summary"}}
```

成功响应包含已保存的触发消息和 `task_id`。客户端用返回的 `result.message` 显示触发消息，并用 `result.task_id` 关联后续事件。任务尚在排队时没有 `queued` 事件。任务无法受理时从 `response.data.error` 读取错误。任务已受理后的执行失败则通过 `agent.task` 推送。

## 5. 服务端事件

| `type` | `data` | 接收范围 |
| --- | --- | --- |
| `presence.changed` | `users: User[]`，当前在线快照 | 已登录客户端。登录、登出和异常断线释放会话后推送 |
| `message.new` | `message: Message` | 公共群聊的所有在线用户或对应私聊的在线参与者。用户发送的消息由发送方从成功响应取得，其他人接收推送。Agent 回复推给该会话内所有在线成员 |
| `agent.task` | `task_id: string`，`conversation_id: string`，`state: "running" or "succeeded" or "failed"`。成功附 `message_id: int`，失败附 `error: {code, message}` | 发起者的当前登录连接 |
| `agent.tool` | `task_id: string`，`conversation_id: string`，`call_id: string`，`name: string`，`state: "started" or "succeeded" or "failed"`。成功附 `result_count: int`，失败附 `error: {code, message}` | 发起者的当前登录连接 |

对发起者而言，服务端先将 `agent.run` 的成功响应加入该连接的发送队列，再调度任务。成功时先推送持久化后的回复，再推送 `agent.task` 的 `succeeded`。工具事件只带调用状态、条数或错误信息。状态与工具事件实时发送，重连后的回复靠历史记录恢复。客户端用 `message_id` 合并历史、响应和新消息，用 `task_id` 与 `call_id` 更新对应的任务和工具行。

`presence.changed` 的 `users` 是完整快照。群聊的用户消息推给除发件人以外的所有在线用户，私聊的用户消息只推给在线的另一方。Agent 触发消息沿用该规则。Agent 回复则向会话内所有在线成员推送，包括发起者。

| 事件 | `state` | `data` 中额外的必填字段 |
| --- | --- | --- |
| `agent.task` | `running` | 无 |
| `agent.task` | `succeeded` | `message_id`，已落库回复的消息 ID |
| `agent.task` | `failed` | `error`，含 `code` 和 `message` |
| `agent.tool` | `started` | 无 |
| `agent.tool` | `succeeded` | `result_count`，本次工具返回的消息条数，可为 `0` |
| `agent.tool` | `failed` | `error`，含 `code` 和 `message` |

`agent.task` 各状态都带 `task_id`、`conversation_id` 和 `state`。`agent.tool` 还带任务内唯一的 `call_id`，其 `name` 为 `read_history` 或 `search_history`。各状态只带本表约定的额外字段。

下例按发起者收到消息的顺序展示第 4.4 节请求的成功响应和后续事件。每行分别编码成一个独立帧。其他请求的响应和其他任务的事件仍可能穿插。

```jsonl
{"v":1,"type":"response","request_id":"c1-11","data":{"ok":true,"result":{"task_id":"task_1","message":{"message_id":43,"conversation_id":"room:general","sender":{"user_id":"usr_1","username":"alice"},"kind":"user","text":"@Agent 总结最近的讨论","created_at_ms":1735689601000,"task_id":"task_1"}}}}
{"v":1,"type":"agent.task","data":{"task_id":"task_1","conversation_id":"room:general","state":"running"}}
{"v":1,"type":"agent.tool","data":{"task_id":"task_1","conversation_id":"room:general","call_id":"tool-1","name":"read_history","state":"started"}}
{"v":1,"type":"agent.tool","data":{"task_id":"task_1","conversation_id":"room:general","call_id":"tool-1","name":"read_history","state":"succeeded","result_count":2}}
{"v":1,"type":"message.new","data":{"message":{"message_id":44,"conversation_id":"room:general","sender":{"user_id":"agent","username":"Agent"},"kind":"agent","text":"周五前提交录屏，周四先联调。","created_at_ms":1735689602000,"task_id":"task_1"}}}
{"v":1,"type":"agent.task","data":{"task_id":"task_1","conversation_id":"room:general","state":"succeeded","message_id":44}}
```

服务端以事件发出时发起者的当前登录连接为目标。断线期间错过的任务和工具事件不会补发。重新登录后可查询会话历史，通过相同的 `task_id` 找到已保存的触发消息和回复。如果历史里只有触发消息，任务可能仍在排队或执行，也可能已失败。

## 6. 连接和错误处理

1. 建立 TCP 连接后，允许注册、登录和 `ping`。其余请求须先登录。已登录连接再次注册或登录返回 `INVALID_STATE`。服务端先向新登录的客户端发送登录响应，再推送事件。客户端登录成功后可并行查询在线列表、会话列表和当前聊天历史。
2. 客户端每隔 15 秒发一次 `ping`。用客户端单调时钟记录匹配请求发出至收到响应的时差，显示应用层往返延迟。连续 45 秒没有收到可完整解码为 JSON 对象的帧时，服务端释放连接和在线状态。收到部分帧时计时继续。会返回 `INVALID_ARGUMENT` 或 `UNKNOWN_TYPE` 的可解码请求也会刷新计时。无效版本或请求 ID 仍会直接导致断连。
3. 登出、TCP EOF、连接异常和心跳超时走同一套清理流程。服务端只清理该连接对应的会话。客户端重连后重新登录并刷新列表和历史。

| 错误码 | 出现位置 | 使用场景 |
| --- | --- | --- |
| `UNKNOWN_TYPE` | `response` | 信封有效，请求类型未知 |
| `INVALID_ARGUMENT` | `response`、`agent.tool` 失败事件 | 请求或工具参数缺失、多余或非法，也用于重复请求 ID、注册保留名、给自己开私聊 |
| `AUTH_REQUIRED` | `response` | 当前连接尚未登录 |
| `INVALID_STATE` | `response` | 已登录连接再次登录或注册 |
| `USERNAME_TAKEN` | `response` | 用户名已注册，忽略大小写 |
| `INVALID_CREDENTIALS` | `response` | 凭据格式有效，但用户名不存在或密码不匹配。登录保留名也返回此码 |
| `ALREADY_ONLINE` | `response` | 该用户已在另一条连接登录 |
| `USER_NOT_FOUND` | `response` | 私聊对象不存在 |
| `CONVERSATION_NOT_FOUND` | `response` | 指定会话不存在 |
| `FORBIDDEN` | `response`、`agent.tool` 失败事件 | 当前用户无法访问请求或工具所需的会话 |
| `AGENT_BUSY` | `response` | 后端任务名额已满，或当前用户有未结束的任务 |
| `AGENT_UNAVAILABLE` | `agent.task` 失败事件 | 任务已受理，Runner 或模型不可用 |
| `AGENT_TIMEOUT` | `agent.task` 失败事件 | 任务已受理，执行超时 |
| `AGENT_FAILED` | `agent.task` 失败事件 | 任务已受理，Agent 执行失败或返回无效结果 |
| `INTERNAL_ERROR` | `response`、`agent.task` 或 `agent.tool` 失败事件 | 后端处理、读写、调度或工具查询失败 |

客户端按 `error.code` 分支处理。`error.message` 是给用户阅读的说明，文案可能随实现调整。

## 7. 联调用例

1. 用三个客户端登录。测试多帧一起到达、帧头或正文分次到达，以及强制结束一个客户端后其他客户端收到在线列表变化。
2. 在公共群聊发消息，再发一条私聊。验证第三人只收到群聊，并且使用私聊 ID 查询时返回 `FORBIDDEN`。发件人只显示一条自己的消息。
3. 重新登录后搜索群聊和私聊历史。对相同关键词翻页，检查消息 ID 的顺序且没有重复。确认 `ping` 的响应能单独匹配并更新延迟显示。
4. 在群里发 `@Agent 总结刚才的消息`，查看真实历史工具的 `started` 和 `succeeded` 事件，并确认其他在线成员收到回复。模拟模型不可用，确认失败事件到达且聊天仍能收发。
5. 触发 Agent 后关闭发起者连接。验证在线成员仍收到 Agent 回复。发起者重连后通过 `history.list` 查到触发消息和回复。

在仓库根目录运行以下命令，可使用 SQLite 临时库、真实 TCP 连接和注入的 Agent 测试替身验证上述场景。

```sh
python -B -m unittest discover -s tests -v
```
