# 后端与 Agent 接口 v1

由后端和 Agent 共同维护。后端负责识别调用者、检查会话权限、调度任务，并提供绑定权限的工具。Agent 负责模型接入、Function Calling、工具选择和回复生成。Agent 通过 Python 函数与后端集成，所需历史由注入的工具提供。客户端协议见 [TCP 协议](protocol.md)。

## 1. 入口和类型

入口固定为 `agent.runner.run_task`。下列签名是 `agent/runner.py` 和 `server/tools/` 的联调契约。后端和 Agent 从 `agent.types` 导入公共类型，从 `agent.errors` 导入异常。测试可向 `ChatServer` 的 `run_task` 参数注入同签名的异步函数，第 3 节提供完整示例。字段与 TCP 协议同名时含义一致。

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

| `AgentContext` 字段 | 来源与用途 |
| --- | --- |
| `task_id` | 后端分配的任务标识，用于关联触发消息、工具事件、回复和任务状态 |
| `requester` | 发起者的 `User` 对象，来自已登录的 TCP 会话 |
| `conversation_id` | 已校验权限的原会话 ID |
| `trigger_message_id` | 本次触发消息的已持久化 ID，也是历史工具的排他上界 |
| `mode` | 客户端显式选择的模式，省略时为 `auto` |
| `instruction` | 客户端提交并经后端去除首尾空白的指令正文 |

`run_task` 接收这三个参数并异步返回只含 `text` 的字典。Agent 将返回正文交给后端，由后端分配回复消息的 ID、时间戳和发送者身份。创建测试替身时可用 `agent.types.RunTask` 标注函数类型。

## 2. 工具注入与权限

| 工具 | 模型可传的参数 | 返回 |
| --- | --- | --- |
| `read_history` | 可选 `before_id`、`limit` | 当前会话的历史消息页 |
| `search_history` | 必填 `keyword`，可选 `before_id`、`limit` | 当前会话内匹配关键词的消息页 |

- 后端为每个任务创建工具函数，将任务发起者、会话和触发消息 ID 绑定到函数中。模型工具参数中只有表格所列字段。后端在每次调用时重新校验访问权限，并限制查询结果的 `message_id < trigger_message_id`。这样，即使任务晚于触发消息开始，也只读取触发前的消息。
- `limit`、`keyword`、`before_id` 和分页顺序与 `history.list` 一致。存在 `before_id` 时同时应用该值和触发消息的上界。工具通过后端的数据访问层查询 SQLite。没有匹配项时返回 `{"messages":[],"next_before_id":null}`。
- 参数非法、无访问权限或查询失败时，工具抛出 `agent.errors.ToolError`。异常的 `.code` 与 `.message` 按 `ToolErrorInfo` 定义，供模型决定修正参数或交由 Agent 返回任务失败。公开错误信息由后端生成，聊天记录和查询参数留在工具与模型的调用链中。
- Agent 按模型返回的工具请求调用注入函数，把真实结果交回模型后继续生成。同一任务内按顺序执行工具调用，最多执行 8 次。摘要、待办和基于聊天内容的问答需要至少一次成功的历史工具调用。若查询成功但没有结果，回复应说明当前范围内没有可用消息。

### 2.1 参数、分页与读取范围

| Python 参数 | 校验与默认值 |
| --- | --- |
| `before_id` | 省略或传 `None` 时只应用触发消息上界。指定时必须是范围 `1` 至 `2**53 - 1` 内的整数 |
| `limit` | 整数 `1` 至 `50`，默认 `20` |
| `keyword` | `search_history` 必填字符串。去除首尾空白后为 1–100 个 Unicode 字符。按字面子串匹配，ASCII 字母忽略大小写 |

调用时使用关键字参数。发起者、会话和触发消息 ID 已由后端绑定，工具可接受的参数见本节表格。`before_id=None` 是 Python 工具接口的可选值。TCP `history.list` 使用省略字段来表达相同含义。

例如，某任务的 `trigger_message_id` 为 `20`，原会话已有消息 `12`、`17`、`19`，触发后又写入 `21`。调用 `read_history(limit=2)` 返回 `17`、`19`，`next_before_id` 为 `17`。再调用 `read_history(before_id=17, limit=2)`，只返回 `12`，且 `next_before_id` 为 `None`。如果传入 `before_id=30`，上界仍为 `20`。触发消息 `20`、之后写入的 `21` 和其他会话的消息均不进入查询结果。

分页时保留工具名称和搜索关键词。`next_before_id` 为 `None` 时已到最后一页。空页在 Python 中为 `{"messages": [], "next_before_id": None}`。

### 2.2 工具调用错误

| `ToolError.code` | 含义 |
| --- | --- |
| `INVALID_ARGUMENT` | 工具参数缺失、额外或非法，例如 `limit=0` 或 `keyword` 去除空白后为空 |
| `FORBIDDEN` | 当前任务已无法访问绑定的会话 |
| `INTERNAL_ERROR` | 历史查询失败 |

Agent 捕获 `ToolError` 后，先用 `emit` 报告本次工具调用失败。Agent 可以在调用预算内修正参数并用新的 `call_id` 重试。无法继续时抛出 `AgentExecutionError`，由后端产生任务失败终态。

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

后端将 `task_id` 和 `conversation_id` 加到 `agent.tool` 推送中，也将调用元数据写入服务端日志。前端只向任务发起者显示工具事件，记录中可展示工具名、每次调用状态、返回条数和错误说明。工具参数、历史消息正文、模型密钥和模型内部推理均不进入工具事件。收到任务失败事件时，前端把该任务仍显示执行中的工具行标记为中断。

上报失败时，`error` 包含后端工具异常的 `code` 和 `message`。查到 0 条消息属于查询成功，仍发送 `succeeded`，并令 `result_count` 为 `0`。工具事件按工具调用实时上报。Agent 继续执行和保存结果无需等待客户端接收事件。

下面的测试替身调用后端注入的真实历史工具，并用查询条数作为联调回复。

```python
from collections.abc import Awaitable, Callable

from agent.errors import AgentExecutionError, ToolError
from agent.types import (
    AgentContext, AgentResult, AgentTools, ToolCall, ToolFailed, ToolStarted, ToolSucceeded,
)


async def run_task(
    context: AgentContext,
    tools: AgentTools,
    emit: Callable[[ToolStarted | ToolSucceeded | ToolFailed], Awaitable[None]],
) -> AgentResult:
    call: ToolCall = {"call_id": "tool-1", "name": "read_history"}
    await emit({**call, "state": "started"})
    try:
        page = await tools["read_history"](limit=20)
    except ToolError as exc:
        await emit({**call, "state": "failed", "error": {"code": exc.code, "message": exc.message}})
        raise AgentExecutionError("History lookup failed") from exc

    count = len(page["messages"])
    await emit({**call, "state": "succeeded", "result_count": count})
    if count == 0:
        return {"text": "当前范围内没有可用消息。"}
    return {"text": f"找到 {count} 条触发前的消息。"}
```

将下列启动代码接在示例函数后面，从仓库根目录运行该脚本。示例监听 `127.0.0.1:8765`，使用单独的 SQLite 文件。

```python
import asyncio

from server.main import ChatServer
from server.storage import Storage


async def main() -> None:
    server = ChatServer(Storage("agent-demo.sqlite3"), run_task=run_task)
    listener = await server.start("127.0.0.1", 8765)
    try:
        await listener.serve_forever()
    finally:
        await server.close()


if __name__ == "__main__":
    asyncio.run(main())
```

## 4. 调度、结果与异常

1. 后端处理 `agent.run` 时验证身份、会话权限和参数，再检查任务容量。默认每个用户最多一个未结束的任务，服务端最多同时执行两个任务，另外允许四个任务排队。没有容量时返回 `AGENT_BUSY`。后端在写入触发消息前预留名额，写入失败时归还名额。
2. 后端为通过检查的任务分配独立 `task_id`，把带该 ID 的触发消息持久化，再发送成功响应。任务进入执行时发送 `agent.task` 的 `running`，异步调用 `run_task`。TCP 读取、回复和心跳应在模型或工具执行期间继续工作。模型调用和工具查询使用异步接口，或在有超时限制的工作线程中调用同步接口。
3. 单个任务执行超时为 60 秒，从开始执行时计时。超时时后端取消等待并结束任务，后续到达的回调和结果不再生效。发起者断线时任务继续执行，成功回复写入原会话。后端在任务结束时释放容量。
4. `run_task` 返回有效结果后，后端将 `text` 作为 `kind: "agent"` 的消息持久化，填入原会话和 `task_id`。先推送消息，再向发起者发送 `succeeded` 及回复的 `message_id`。任务只产生一个终态，且成功时只保存一条回复。
5. 失败时后端向发起者发送 `agent.task` 的 `failed`，触发消息留在会话历史。任务失败不会终止 TCP 连接或影响普通聊天。有异常的工具调用或任务在服务端留存日志，前端显示约定的错误码和简短说明。

### 4.1 受理、排队与终态

- 预留名额和触发消息落库都发生在成功响应之前。`AGENT_BUSY` 经原请求的 `response` 返回，且不创建触发消息。触发消息写入失败则归还名额，通过 `response` 返回 `INTERNAL_ERROR`。
- 通过受理后，后端先把成功响应加入发起者的发送队列，再向其他在线成员推送触发消息，然后调度任务。排队时间不计入 60 秒执行限时，排队阶段没有 `queued` 事件。
- 执行限时到达时，后端取消对 Runner 的等待，释放执行名额，并丢弃迟到的 `emit` 和返回值。Agent 调用外部服务时要让协程可取消，并为该服务调用设置超时。
- 发起者的断线与任务执行相互独立。事件发送时使用发起者的当前登录连接，已错过的事件不会补发。重新登录后，成功写入的回复通过原会话历史恢复。
- 成功时，回复消息和 `agent.task` 的 `succeeded` 通过同一个 `task_id` 和回复 `message_id` 关联。失败时，已保存的触发消息留在历史中，任务只产生一个 `failed` 终态。

### 4.2 任务失败映射

| 失败情形 | Agent／后端处理 | 发送给前端的代码 |
| --- | --- | --- |
| Runner 未安装，或模型未配置、连接失败、服务不可用 | 后端找不到默认 Runner，或 Agent 抛出 `agent.errors.AgentUnavailableError` | `AGENT_UNAVAILABLE` |
| 模型响应无法解析、工具失败后无法继续、工具调用次数超限 | Agent 抛出 `agent.errors.AgentExecutionError`。直接传出 `ToolError` 也映射到任务执行失败 | `AGENT_FAILED` |
| Agent 返回格式错误、正文为空或长度超限 | 后端验证 `AgentResult` | `AGENT_FAILED` |
| 任务在执行阶段超时 | 后端停止等待，忽略迟到的事件和结果 | `AGENT_TIMEOUT` |
| 回复持久化失败、调度异常或其他未处理异常 | 后端记录错误并结束任务 | `INTERNAL_ERROR` |

模型密钥只由服务端进程的环境变量或本地配置提供。传给模型的输入限于指令和工具按会话权限返回的数据。采用云端模型时，按课程要求选用经授权的模型服务。

## 5. 联调用例

1. 先在群聊发送几条记录，再从客户端发 `@Agent 总结最近的讨论`。检查 `read_history` 调用了服务端历史查询，前端可见同一 `call_id` 的执行状态和条数，回复带同一 `task_id` 且对其他群聊成员可见。
2. 在任务触发后继续发消息。验证任务只读取触发前的记录。在私聊触发同一任务后，验证只读取该私聊，第三人收不到触发消息、工具事件或回复。
3. 模拟工具错误、模型不可用和任务超时。发起者应收到对应的失败事件，任务释放容量，三个客户端继续发送群聊和私聊消息。
4. 在任务开始后让发起者断线。其他在线成员应收到原会话的回复。发起者重连后通过 `history.list` 查到同一 `task_id` 的触发消息和回复。

在仓库根目录运行以下命令，可以分别检查 Agent 端到端联调和任务容量、超时测试。测试使用注入的 Runner 和临时 SQLite 数据库。

```sh
python -B -m unittest discover -s tests -p test_integration.py -v
python -B -m unittest discover -s tests -p test_scheduler.py -v
```
