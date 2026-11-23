"""Test the real Claude SDK/MCP bridge using mocked HTTP responses, without API calls."""

import asyncio
import json
from contextlib import asynccontextmanager

import anthropic
import httpx2

from client import ChatClient, ChatError, connect_mcp, describe_error, discover_tools


def message(content: list, reason: str = "end_turn") -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


def tool_call(call_id: str, order_id: str, name: str = "lookup_order") -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": {"order_id": order_id}}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


@asynccontextmanager
async def connected_chat(handler, max_tool_rounds=5, api_max_retries=0):
    async with connect_mcp() as session:
        tools = await discover_tools(session)
        transport = httpx2.MockTransport(handler)
        async with anthropic.AsyncAnthropic(
            api_key="test-key-never-sent-over-network",
            base_url="https://api.anthropic.com",
            http_client=httpx2.AsyncClient(transport=transport),
            max_retries=api_max_retries,
        ) as api:
            yield ChatClient(session, api, tools, max_tool_rounds=max_tool_rounds)


async def check_round_trip_and_history():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        tools = {tool["name"]: tool for tool in body["tools"]}
        assert set(tools) == {"lookup_order", "search_orders"}
        assert tools["lookup_order"]["input_schema"]["required"] == ["order_id"]
        if len(requests) == 1:
            return message([tool_call("toolu_success", "ORD-12345678")], "tool_use")
        if len(requests) == 2:
            assert body["messages"][-2]["content"][0]["id"] == "toolu_success"
            result = body["messages"][-1]["content"][0]
            assert result["tool_use_id"] == "toolu_success"
            assert result["is_error"] is False
            assert json.loads(result["content"])["order"]["status"] == "delivered"
            return message([text("The wireless mouse was delivered three days ago.")])
        assert len(body["messages"]) == 5, body["messages"]
        assert body["messages"][-1]["content"] == "What item was that?"
        return message([text("It was a wireless mouse.")])

    async with connected_chat(handler) as chat:
        assert "three days" in await chat.ask("Check ORD-12345678")
        assert "wireless mouse" in await chat.ask("What item was that?")
        assert len(chat.history) == 6
    print("PASS: real SDK request -> real MCP tool -> tool_result -> answer, with follow-up history")


async def check_errors_and_multiple_calls():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return message([
                tool_call("toolu_invalid", "bad-id"),
                tool_call("toolu_absent", "ORD-00000000"),
            ], "tool_use")
        results = body["messages"][-1]["content"]
        assert [item["tool_use_id"] for item in results] == ["toolu_invalid", "toolu_absent"]
        assert results[0]["is_error"] is True
        assert "eight digits" in results[0]["content"]
        assert results[1]["is_error"] is False
        assert json.loads(results[1]["content"])["found"] is False
        return message([text("The first ID is invalid; the second order was not found.")])

    async with connected_chat(handler) as chat:
        assert "not found" in await chat.ask("Check these IDs")
    print("PASS: multiple calls each receive correctly matched success/error results")


async def check_unknown_tool():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return message([tool_call("toolu_unknown", "ORD-12345678", name="delete_order")], "tool_use")
        result = body["messages"][-1]["content"][0]
        assert result["is_error"] is True
        assert "Unknown tool" in result["content"]
        return message([text("I can look up orders using the available tool.")])

    async with connected_chat(handler) as chat:
        await chat.ask("Check the order")
    print("PASS: an unavailable tool is rejected and reported to the model")


async def check_failure_keeps_history():
    def handler(request):
        return httpx2.Response(401, json={
            "type": "error",
            "error": {"type": "authentication_error", "message": "Test authentication failure"},
        })

    async with connected_chat(handler) as chat:
        chat.history = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": [text("Hello!")]}]
        original = list(chat.history)
        try:
            await chat.ask("Check ORD-12345678")
        except anthropic.AuthenticationError:
            pass
        else:
            raise AssertionError("Authentication error was not surfaced")
        assert chat.history == original
    print("PASS: API errors preserve the last complete conversation history")


async def check_tool_loop_limit():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return message([tool_call(f"toolu_loop_{count}", "ORD-12345678")], "tool_use")

    async with connected_chat(handler, max_tool_rounds=2) as chat:
        try:
            await chat.ask("Check ORD-12345678")
        except ChatError as exc:
            assert "Tool-call limit" in str(exc)
        else:
            raise AssertionError("Tool loop was not bounded")
        assert count == 3
        assert chat.history == []
    print("PASS: repeated model tool requests stop at the configured round limit")


async def check_bad_request_details():
    examples = [
        {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}},
        {"type": "invalid_request_error", "message": "tools.0.input_schema: Invalid schema."},
    ]
    for body in examples:
        count = 0

        def handler(request):
            nonlocal count
            count += 1
            return httpx2.Response(400, headers={"request-id": "req_test_bad_request"}, json=body)

        async with connected_chat(handler, api_max_retries=2) as chat:
            try:
                await chat.ask("Check ORD-12345678")
            except anthropic.BadRequestError as exc:
                description = describe_error(exc)
                expected = body.get("error", body)
                assert expected["message"] in description, description
                assert "invalid_request_error" in description, description
                assert "req_test_bad_request" in description, description
                assert "HTTP 400" in description, description
            else:
                raise AssertionError("Bad request was not surfaced")
            assert count == 1, "An unchanged HTTP 400 request should not be retried"
            assert chat.history == []
    print("PASS: HTTP 400 retains the API reason/type/request ID and is not retried")


async def check_csv_search_prompts():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        stage = len(requests)
        if stage in {1, 3, 5}:
            filters = {"status": "delivered", "city": "Mumbai"}
            if stage >= 3:
                filters["customer_name"] = "Aditi"
            if stage == 5:
                filters["limit"] = 1
            return message([{"type": "tool_use", "id": f"toolu_search_{stage}", "name": "search_orders", "input": filters}], "tool_use")
        result = body["messages"][-1]["content"][0]
        assert result["is_error"] is False, result
        payload = json.loads(result["content"])
        assert payload["source"] == "orders.csv"
        if stage == 2:
            assert payload["total_matches"] == 6
            assert all(order["status"] == "delivered" and order["city"] == "Mumbai" for order in payload["orders"])
            return message([text("There are six delivered orders in Mumbai.")])
        assert payload["total_matches"] == 3
        assert all(order["customer_name"] == "Aditi Shah" for order in payload["orders"])
        if stage == 4:
            return message([text("Aditi has three delivered orders in Mumbai.")])
        assert payload["returned_count"] == 1
        assert payload["totals_by_currency"]["INR"] == "3994.00"
        return message([text("Their total order value is INR 3,994.00.")])

    async with connected_chat(handler) as chat:
        assert "six" in await chat.ask("Show delivered orders in Mumbai")
        assert "three" in await chat.ask("Only Aditi's orders")
        assert "3,994.00" in await chat.ask("What is their total order value?")
    print("PASS: user prompts and follow-up filters retrieve CSV rows and full-match totals through MCP")


async def main():
    async with asyncio.timeout(60):
        await check_round_trip_and_history()
        await check_errors_and_multiple_calls()
        await check_unknown_tool()
        await check_failure_keeps_history()
        await check_tool_loop_limit()
        await check_bad_request_details()
        await check_csv_search_prompts()
    print("All 7 client integration checks passed (mocked Claude HTTP; real MCP).")


if __name__ == "__main__":
    asyncio.run(main())
