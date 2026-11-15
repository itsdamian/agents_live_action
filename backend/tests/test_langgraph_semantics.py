"""LangGraph semantics, learned on toy graphs (no pipeline code involved).

Each test pins one behaviour of langgraph 1.2.22 that the pipeline engine
relies on. Async code is driven with ``asyncio.run`` like the other tests.

(No ``from __future__ import annotations`` here: the toy ``State`` classes are
defined inside the tests, and LangGraph resolves node/router annotations with
``typing.get_type_hints``, which cannot see function-local names once
annotations are strings.)
"""

import asyncio
import operator
from typing import Annotated, Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel


def _config(thread_id: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread_id}}


# LangGraph 學習: 條件邊 (add_conditional_edges) 是一個讀 state、回傳下一個 node
# 名稱的普通函式；兩個分支依 state 值走不同 node。
def test_conditional_edge_routes_by_state():
    class State(TypedDict, total=False):
        n: int
        path: str

    def route(state: State) -> str:
        return "big" if state["n"] >= 10 else "small"

    graph = StateGraph(State)
    graph.add_node("big", lambda state: {"path": "big"})
    graph.add_node("small", lambda state: {"path": "small"})
    graph.add_conditional_edges(START, route)
    graph.add_edge("big", END)
    graph.add_edge("small", END)
    app = graph.compile()

    assert asyncio.run(app.ainvoke({"n": 50}))["path"] == "big"
    assert asyncio.run(app.ainvoke({"n": 1}))["path"] == "small"


# LangGraph 學習: reducer。state 欄位標成 Annotated[list, operator.add] 後，
# node 回傳的清單會被「加」到既有值，而不是覆蓋。
def test_reducer_appends_lists():
    class State(TypedDict):
        items: Annotated[list[str], operator.add]

    graph = StateGraph(State)
    graph.add_node("a", lambda state: {"items": ["from-a"]})
    graph.add_node("b", lambda state: {"items": ["from-b"]})
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    app = graph.compile()

    result = asyncio.run(app.ainvoke({"items": []}))

    assert result["items"] == ["from-a", "from-b"]


# LangGraph 學習: interrupt() 第一次呼叫會暫停 graph（需要 checkpointer）；
# 用 Command(resume=...) 繼續時，含 interrupt 的 node 會「從第一行重跑」，
# 這次 interrupt() 直接回傳 resume 值。所以 interrupt 之前不能有副作用（規則 I1）。
def test_interrupt_node_reruns_from_top_on_resume():
    class State(TypedDict, total=False):
        answer: str

    side_effects: list[str] = []

    def ask(state: State) -> dict:
        side_effects.append("ran")  # a side effect *before* interrupt()
        answer = interrupt("q")
        return {"answer": answer}

    graph = StateGraph(State)
    graph.add_node("ask", ask)
    graph.add_edge(START, "ask")
    graph.add_edge("ask", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = _config()

    async def run():
        await app.ainvoke({}, config)
        paused = await app.aget_state(config)
        after_first = len(side_effects)
        result = await app.ainvoke(Command(resume="a"), config)
        return paused, after_first, result

    paused, after_first, result = asyncio.run(run())

    assert after_first == 1
    assert paused.next  # stopped, waiting at "ask"
    assert paused.next == ("ask",)
    assert len(paused.interrupts) == 1
    assert paused.interrupts[0].value == "q"
    # Resuming re-executed the node from its first line: the side effect ran twice.
    assert len(side_effects) == 2
    assert result["answer"] == "a"


# LangGraph 學習: 已經完成的 node 不會因為 resume 而重跑，它們的結果從
# checkpoint 讀回；只有被 interrupt 打斷的那個 node 會重跑。
def test_completed_nodes_do_not_rerun_on_resume():
    class State(TypedDict, total=False):
        answer: str

    counter = {"before": 0}

    def before(state: State) -> dict:
        counter["before"] += 1
        return {}

    def ask(state: State) -> dict:
        return {"answer": interrupt("q")}

    graph = StateGraph(State)
    graph.add_node("before", before)
    graph.add_node("ask", ask)
    graph.add_edge(START, "before")
    graph.add_edge("before", "ask")
    graph.add_edge("ask", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = _config()

    async def run():
        await app.ainvoke({}, config)
        await app.ainvoke(Command(resume="a"), config)

    asyncio.run(run())

    assert counter["before"] == 1


# LangGraph 學習: checkpointer 會序列化 state。放進 state 的物件 resume 之後，
# node 拿到的是反序列化的「複本」，不是原物件 (plan D3：所以 live Task 放在 state 外面)。
def test_object_in_state_loses_identity_after_resume():
    class Payload(BaseModel):
        text: str

    class State(TypedDict, total=False):
        obj: Any
        answer: str

    seen: list[Any] = []

    def ask(state: State) -> dict:
        seen.append(state["obj"])
        return {"answer": interrupt("q")}

    graph = StateGraph(State)
    graph.add_node("ask", ask)
    graph.add_edge(START, "ask")
    graph.add_edge("ask", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = _config()
    original = Payload(text="hello")

    async def run():
        await app.ainvoke({"obj": original}, config)
        await app.ainvoke(Command(resume="a"), config)

    asyncio.run(run())

    assert len(seen) == 2
    assert seen[0] is original  # first run: still the very same object
    assert seen[1] is not original  # after resume: a deserialized copy
    # Observed in 1.2.22: with an untyped (``Any``) channel the copy comes back
    # as a plain dict, not even a Payload -- another reason to keep rich objects
    # out of state.
    assert seen[1] == {"text": "hello"}


# LangGraph 學習: node 內的例外會從 astream 的迴圈原樣往外拋（不包裝），
# 所以 orchestrator 的 _worker_loop `except Exception` 能照舊處理。
def test_node_exception_propagates_from_astream():
    class State(TypedDict, total=False):
        x: int

    def boom(state: State) -> dict:
        raise ValueError("boom")

    graph = StateGraph(State)
    graph.add_node("boom", boom)
    graph.add_edge(START, "boom")
    graph.add_edge("boom", END)
    app = graph.compile()

    async def run():
        async for _chunk in app.astream({}, stream_mode="updates"):
            pass

    with pytest.raises(Exception) as excinfo:
        asyncio.run(run())

    # Observed in 1.2.22: raised as-is, same type and message.
    assert type(excinfo.value) is ValueError
    assert str(excinfo.value) == "boom"


# LangGraph 學習: stream_mode="updates" 每個 node 結束送出 {node 名稱: 回傳的更新}；
# 停在 interrupt 時最後一個 chunk 是 {"__interrupt__": (Interrupt, ...)}，串流本身
# 正常結束、不拋例外。
def test_updates_stream_shape_on_interrupt():
    class State(TypedDict, total=False):
        a: int
        answer: str

    def first(state: State) -> dict:
        return {"a": 1}

    def ask(state: State) -> dict:
        return {"answer": interrupt("q")}

    graph = StateGraph(State)
    graph.add_node("first", first)
    graph.add_node("ask", ask)
    graph.add_edge(START, "first")
    graph.add_edge("first", "ask")
    graph.add_edge("ask", END)
    app = graph.compile(checkpointer=InMemorySaver())

    async def run():
        return [
            chunk
            async for chunk in app.astream({}, _config(), stream_mode="updates")
        ]

    chunks = asyncio.run(run())

    assert chunks[0] == {"first": {"a": 1}}
    last = chunks[-1]
    assert list(last) == ["__interrupt__"]
    (interrupt_obj,) = last["__interrupt__"]
    assert interrupt_obj.value == "q"


# LangGraph 學習: 跑到 END 之後 aget_state(config).next 是空 tuple；
# driver 就是靠 .next 是否為空判斷「跑完了」還是「停在 interrupt」。
def test_aget_state_next_empty_at_end():
    class State(TypedDict, total=False):
        x: int

    graph = StateGraph(State)
    graph.add_node("only", lambda state: {"x": 1})
    graph.add_edge(START, "only")
    graph.add_edge("only", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = _config()

    async def run():
        await app.ainvoke({}, config)
        return await app.aget_state(config)

    snapshot = asyncio.run(run())

    assert snapshot.next == ()
