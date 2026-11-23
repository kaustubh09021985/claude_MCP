"""Exercise discovery and calls over real MCP stdio, without an LLM or API key."""

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def read_payload(result) -> dict:
    if result.structuredContent is not None:
        return result.structuredContent
    text = "".join(block.text for block in result.content if block.type == "text")
    return json.loads(text)


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", str(Path(__file__).with_name("server.py").resolve())],
    )

    async with asyncio.timeout(30):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                await session.initialize()

                listing = await session.list_tools()
                tools = {tool.name: tool for tool in listing.tools}
                assert set(tools) == {"lookup_order", "search_orders"}, tools
                tool = tools["lookup_order"]
                assert tool.description and "existing order" in tool.description
                assert "order_id" in tool.inputSchema["required"]
                assert tool.inputSchema["properties"]["order_id"]["type"] == "string"
                assert "ALL matches" in tools["search_orders"].description
                print("PASS: tools/list exposes lookup_order/search_orders and the lookup input schema")

                found = await session.call_tool("lookup_order", {"order_id": "ORD-12345678"})
                assert not found.isError, found
                payload = read_payload(found)
                assert payload["found"] is True, payload
                assert payload["order"]["status"] == "delivered", payload
                print("PASS: existing order returns delivered status")
                print(json.dumps(payload, indent=2))

                absent = await session.call_tool("lookup_order", {"order_id": "ORD-00000000"})
                assert not absent.isError, absent
                assert read_payload(absent)["found"] is False, absent
                print("PASS: missing order returns found=false as a normal result")

                invalid = await session.call_tool("lookup_order", {"order_id": "wrong-format"})
                assert invalid.isError, invalid
                error_text = " ".join(block.text for block in invalid.content if block.type == "text")
                assert "eight digits" in error_text, error_text
                print("PASS: invalid ID returns isError=true with actionable feedback")

                missing_input = await session.call_tool("lookup_order", {})
                assert missing_input.isError, missing_input
                print("PASS: missing required input returns isError=true")

    print("All 5 MCP integration checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
