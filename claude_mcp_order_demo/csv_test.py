"""Check CSV searches over real MCP, including file edits and malformed data."""

import asyncio
import csv
import json
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def session_for(csv_path=None, cwd=None):
    params = StdioServerParameters(
        command=sys.executable, args=["-B", str(ROOT / "server.py")],
        env={"ORDERS_CSV": str(csv_path)} if csv_path else {}, cwd=cwd,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
            await session.initialize()
            yield session


async def call(session, tool, arguments):
    result = await session.call_tool(tool, arguments)
    assert not result.isError, result
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads("".join(block.text for block in result.content if block.type == "text"))


def fixture_rows():
    with (ROOT / "orders.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def write_fixture(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


async def main():
    async with asyncio.timeout(60):
        with tempfile.TemporaryDirectory() as directory:
            # Different cwd verifies that the default CSV is resolved beside server.py.
            async with session_for(cwd=directory) as session:
                data = await call(session, "search_orders", {"customer_name": "aDiTi", "item": "MOUSE", "city": "mUMbai"})
                assert data["total_matches"] == 1
                assert data["orders"][0]["order_id"] == "ORD-12345678"
                quoted = await call(session, "lookup_order", {"order_id": "ORD-10000028"})
                assert quoted["order"]["item"] == "Desk organizer, metal"
                print("PASS: combined case-insensitive filters, quoted CSV fields, and independent working directory")

                first = await call(session, "search_orders", {"limit": 3, "sort_by": "order_id", "descending": False})
                second = await call(session, "search_orders", {"limit": 3, "offset": first["next_offset"], "sort_by": "order_id", "descending": False})
                assert first["total_matches"] == second["total_matches"] == 30
                assert first["returned_count"] == second["returned_count"] == 3
                assert set(order["order_id"] for order in first["orders"]).isdisjoint(order["order_id"] for order in second["orders"])
                assert first["totals_by_currency"] == second["totals_by_currency"]
                print("PASS: pagination retains full-match counts and totals without repeating rows")

                amount = await call(session, "search_orders", {"customer_name": "Aditi", "min_total": 700, "max_total": 2000, "sort_by": "total_amount"})
                assert [order["total_amount"] for order in amount["orders"]] == ["1899.00", "1299.00", "796.00"]
                exact = await call(session, "search_orders", {"min_total": 796, "max_total": 796})
                assert [order["order_id"] for order in exact["orders"]] == ["ORD-10000022"]
                print("PASS: amount filters use quantity times unit price, inclusive bounds, and numeric sorting")

                delivered = await call(session, "search_orders", {"status": "delivered", "date_field": "delivery_date", "date_from": "2026-11-19", "date_to": "2026-11-19"})
                assert [order["order_id"] for order in delivered["orders"]] == ["ORD-12345678"]
                ordered = await call(session, "search_orders", {"status": "delivered", "date_field": "order_date", "date_from": "2026-11-19", "date_to": "2026-11-19"})
                assert ordered["total_matches"] == 0
                print("PASS: inclusive date filters distinguish order dates from actual delivery dates")

                summary = await call(session, "search_orders", {"group_by": "status", "limit": 1})
                assert summary["counts_by_status"] == {"cancelled": 2, "delivered": 12, "pending": 4, "processing": 4, "returned": 2, "shipped": 6}
                assert sum(group["order_count"] for group in summary["groups"]) == 30
                columns, rows = fixture_rows()
                expected = sum(Decimal(row["unit_price"]) * int(row["quantity"]) for row in rows)
                assert summary["totals_by_currency"]["INR"] == f"{expected:.2f}"
                assert summary["returned_count"] == 1
                print("PASS: grouped counts and exact decimal totals cover all matches, not just the visible page")

                empty = await call(session, "search_orders", {"customer_name": "No Such Customer"})
                assert empty["found"] is False and empty["total_matches"] == 0
                assert empty["orders"] == [] and empty["totals_by_currency"] == {} and empty["next_offset"] is None
                print("PASS: a no-match search is a normal empty result")

                for arguments in [
                    {"date_from": "not-a-date"}, {"date_from": "2026-11-22", "date_to": "2026-11-01"},
                    {"min_total": 2000, "max_total": 1000}, {"limit": 0}, {"offset": -1}, {"status": "lost"},
                ]:
                    result = await session.call_tool("search_orders", arguments)
                    assert result.isError, arguments
                print("PASS: invalid dates, reversed bounds, pagination values, and statuses produce tool errors")

            fixture = Path(directory) / "editable.csv"
            columns, rows = fixture_rows()
            write_fixture(fixture, columns, rows)
            async with session_for(fixture) as session:
                before = await call(session, "lookup_order", {"order_id": "ORD-12345678"})
                assert before["order"]["item"] == "Wireless mouse"
                rows[0]["item"] = "Updated CSV mouse"
                write_fixture(fixture, columns, rows)
                after = await call(session, "lookup_order", {"order_id": "ORD-12345678"})
                assert after["order"]["item"] == "Updated CSV mouse"
            print("PASS: a CSV edit is visible on the next call in the same MCP session")

            bad = Path(directory) / "bad.csv"
            bad.write_text("order_id\nORD-12345678\n", encoding="utf-8")
            async with session_for(bad) as session:
                missing = await session.call_tool("search_orders", {})
                assert missing.isError
                assert "missing required columns" in " ".join(block.text for block in missing.content if block.type == "text")
                columns, rows = fixture_rows()
                write_fixture(bad, columns, [*rows, rows[0]])
                duplicate = await session.call_tool("search_orders", {})
                assert duplicate.isError
                assert "Duplicate order_id" in " ".join(block.text for block in duplicate.content if block.type == "text")
            print("PASS: missing columns and duplicate IDs are reported as actionable CSV errors")

            multi = Path(directory) / "currencies.csv"
            columns, rows = fixture_rows()
            rows[0]["currency"] = "USD"
            write_fixture(multi, columns, rows)
            async with session_for(multi) as session:
                data = await call(session, "search_orders", {"limit": 1})
                assert data["totals_by_currency"]["USD"] == "1299.00"
                assert Decimal(data["totals_by_currency"]["INR"]) == expected - Decimal("1299.00")
                usd = await call(session, "search_orders", {"currency": "usd"})
                assert usd["total_matches"] == 1
            print("PASS: separate currency totals and currency filters prevent mixed-currency sums")
    print("All 10 CSV integration checks passed (real MCP; no LLM calls).")


if __name__ == "__main__":
    asyncio.run(main())
