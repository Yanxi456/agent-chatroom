# 后端与 Agent 接口 v1

由后端和 Agent 共同维护。后端负责识别调用者、检查会话权限、调度任务，并提供绑定权限的工具。Agent 负责模型接入、Function Calling、工具选择和回复生成。Agent 通过 Python 函数与后端集成，所需历史由注入的工具提供。客户端协议见 [TCP 协议](protocol.md)。

## 1. 入口和类型

入口固定为 `agent.runner.run_task`。下列签名是 `agent/runner.py` 和 `server/tools/` 的联调契约。实际项目可以把数据类型集中到 `agent/types.py`，后端和 Agent 使用同一份定义。字段与 TCP 协议同名时含义一致。

```python
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, TypedDict


class User(TypedDict):
    user_id: str
    username: str


class Message(TypedDict):
    message_id: int
    conversation_id: str
    sender: User
    kind: Literal["user", "agent"]
    text: str
    created_at_ms: int
    task_id: str | None


class AgentContext(TypedDict):
    task_id: str
    requester: User
    conversation_id: str
    trigger_message_id: int
    mode: Literal["auto", "summary", "todos"]
    instruction: str


class HistoryPage(TypedDict):
    messages: list[Message]
    next_before_id: int | None


class ReadHistory(Protocol):
    def __call__(
        self, *, before_id: int | None = None, limit: int = 20
    ) -> Awaitable[HistoryPage]: ...


class SearchHistory(Protocol):
    def __call__(
        self, *, keyword: str, before_id: int | None = None, limit: int = 20
    ) -> Awaitable[HistoryPage]: ...


class AgentTools(TypedDict):
    read_history: ReadHistory
    search_history: SearchHistory


class ToolCall(TypedDict):
    call_id: str
    name: Literal["read_history", "search_history"]


class ToolErrorInfo(TypedDict):
    code: Literal["INVALID_ARGUMENT", "FORBIDDEN", "INTERNAL_ERROR"]
    message: str


class ToolStarted(ToolCall):
    state: Literal["started"]


class ToolSucceeded(ToolCall):
    state: Literal["succeeded"]
    result_count: int


class ToolFailed(ToolCall):
    state: Literal["failed"]
    error: ToolErrorInfo


class AgentResult(TypedDict):
    text: str


async def run_task(
    context: AgentContext,
    tools: AgentTools,
    emit: Callable[[ToolStarted | ToolSucceeded | ToolFailed], Awaitable[None]],
) -> AgentResult: ...
```

`context` 由后端构造。`trigger_message_id` 是已经写入数据库的 `@Agent` 消息 ID，`instruction` 是去掉 `@Agent ` 前缀的指令正文。`auto` 按指令执行问答、摘要或待办提取，`summary` 和 `todos` 分别固定为摘要和待办提取。返回 `text` 使用可直接显示的纯文本，可用换行组织要点。后端验证去除首尾空白后长度为 1–2000 个 Unicode 字符。

## 2. 工具注入与权限

| 工具 | 模型可传的参数 | 返回 |
| --- | --- | --- |
| `read_history` | 可选 `before_id`、`limit` | 当前会话的历史消息页 |
| `search_history` | 必填 `keyword`，可选 `before_id`、`limit` | 当前会话内匹配关键词的消息页 |

- 后端为每个任务创建工具函数，将任务发起者、会话和触发消息 ID 绑定到函数中。模型工具参数中只有表格所列字段。后端在每次调用时重新校验访问权限，并限制查询结果的 `message_id < trigger_message_id`。这样，即使任务晚于触发消息开始，也只读取触发前的消息。
- `limit`、`keyword`、`before_id` 和分页顺序与 `history.list` 一致。存在 `before_id` 时同时应用该值和触发消息的上界。工具通过后端的数据访问层查询 SQLite。没有匹配项时返回 `{"messages":[],"next_before_id":null}`。
- 参数非法、无访问权限或查询失败时，工具抛出 `agent.errors.ToolError`。异常的 `.code` 与 `.message` 按 `ToolErrorInfo` 定义，供模型决定修正参数或交由 Agent 返回任务失败。公开错误信息由后端生成，聊天记录和查询参数留在工具与模型的调用链中。
- Agent 按模型返回的工具请求调用注入函数，把真实结果交回模型后继续生成。同一任务内按顺序执行工具调用，最多执行 8 次。摘要、待办和基于聊天内容的问答需要至少一次成功的历史工具调用。若查询成功但没有结果，回复应说明当前范围内没有可用消息。

## 3. 工具事件回调

`emit` 由后端提供。Agent 为每次工具调用分配一个在当前任务内唯一的 `call_id`，先 `await emit(...)` 报告 `started`，再调用工具。调用正常返回时报告 `succeeded`，`result_count` 等于这次结果中 `messages` 的长度。调用因 `ToolError` 失败时报告 `failed` 并附错误。两次事件用相同的 `call_id` 和 `name`。

```python
await emit({"call_id": "tool-1", "name": "read_history", "state": "started"})
page = await tools["read_history"](limit=20)
await emit({
    "call_id": "tool-1",
    "name": "read_history",
    "state": "succeeded",
    "result_count": len(page["messages"]),
})
```

后端将 `task_id` 和 `conversation_id` 加到 `agent.tool` 推送中，也将调用元数据写入服务端日志。前端只向任务发起者显示工具事件，记录中可展示工具名、每次调用状态、返回条数和错误说明。工具参数、历史消息正文、模型密钥和模型内部推理均不进入工具事件。任务超时或取消时，后端发送任务失败事件，前端把仍显示执行中的工具行结束为中断状态。

## 4. 调度、结果与异常

1. 后端处理 `agent.run` 时验证身份、会话权限和参数，再检查任务容量。默认每个用户最多一个未结束的任务，服务端最多同时执行两个任务，另外允许四个任务排队。没有容量时返回 `AGENT_BUSY`。后端在写入触发消息前预留名额，写入失败时归还名额。
2. 后端为通过检查的任务分配独立 `task_id`，把带该 ID 的触发消息持久化，再发送成功响应。任务进入执行时发送 `agent.task` 的 `running`，异步调用 `run_task`。TCP 读取、回复和心跳应在模型或工具执行期间继续工作。模型调用和工具查询使用异步接口，或在有超时限制的工作线程中调用同步接口。
3. 单个任务执行超时为 60 秒，从开始执行时计时。超时时后端取消等待并结束任务，后续到达的回调和结果不再生效。发起者断线时任务继续执行，成功回复写入原会话。后端在任务结束时释放容量。
4. `run_task` 返回有效结果后，后端将 `text` 作为 `kind: "agent"` 的消息持久化，填入原会话和 `task_id`。先推送消息，再向发起者发送 `succeeded` 及回复的 `message_id`。任务只产生一个终态，且成功时只保存一条回复。
5. 失败时后端向发起者发送 `agent.task` 的 `failed`，触发消息留在会话历史。任务失败不会终止 TCP 连接或影响普通聊天。有异常的工具调用或任务在服务端留存日志，前端显示约定的错误码和简短说明。

| 失败情形 | Agent／后端处理 | 发送给前端的代码 |
| --- | --- | --- |
| 模型未配置、连接失败或服务不可用 | Agent 抛出 `agent.errors.AgentUnavailableError` | `AGENT_UNAVAILABLE` |
| 模型响应无法解析、工具失败后无法继续、工具调用次数超限 | Agent 抛出 `agent.errors.AgentExecutionError`，两种 Agent 异常均继承 `Exception` | `AGENT_FAILED` |
| Agent 返回格式错误、正文为空或长度超限 | 后端验证失败 | `AGENT_FAILED` |
| 任务在执行阶段超时 | 后端停止等待，忽略迟到的事件和结果 | `AGENT_TIMEOUT` |
| 回复持久化失败、调度异常或其他未处理异常 | 后端记录错误并结束任务 | `INTERNAL_ERROR` |

模型密钥只由服务端进程的环境变量或本地配置提供。传给模型的输入限于指令和工具按会话权限返回的数据。采用云端模型时，按课程要求选用经授权的模型服务。

## 5. 联调用例

1. 先在群聊发送几条记录，再从客户端发 `@Agent 总结最近的讨论`。检查 `read_history` 调用了服务端历史查询，前端可见同一 `call_id` 的执行状态和条数，回复带同一 `task_id` 且对其他群聊成员可见。
2. 在任务触发后继续发消息。验证任务只读取触发前的记录。在私聊触发同一任务后，验证只读取该私聊，第三人收不到触发消息、工具事件或回复。
3. 模拟工具错误、模型不可用和任务超时。发起者应收到对应的失败事件，任务释放容量，三个客户端继续发送群聊和私聊消息。
