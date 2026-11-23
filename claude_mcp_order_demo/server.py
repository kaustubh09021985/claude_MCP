"""Read-only MCP tools for looking up and filtering the editable orders.csv."""

import csv
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP


logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("shopassist")
server = FastMCP("shopassist")
DATA_FILE = Path(os.environ.get("ORDERS_CSV", str(Path(__file__).with_name("orders.csv")))).resolve()
OrderStatus = Literal["pending", "processing", "shipped", "delivered", "cancelled", "returned"]
DateField = Literal["order_date", "expected_delivery_date", "delivery_date"]
SortField = Literal["order_id", "order_date", "delivery_date", "total_amount"]
GroupField = Literal["status", "customer_name", "city", "category"]
STATUSES = {"pending", "processing", "shipped", "delivered", "cancelled", "returned"}
CSV_COLUMNS = (
    "order_id", "customer_id", "customer_name", "item", "category", "quantity",
    "unit_price", "currency", "status", "order_date", "expected_delivery_date",
    "delivery_date", "city",
)


def parse_date(value: str | None, field: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid date in YYYY-MM-DD format.") from exc


def read_orders() -> list[dict]:
    """Read fresh data on every call; validate the file before returning records."""
    try:
        handle = DATA_FILE.open(encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"Cannot read {DATA_FILE.name}. Put a readable orders.csv beside server.py.") from exc
    orders = []
    seen = set()
    with handle:
        reader = csv.DictReader(handle, strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError("CSV contains duplicate column names.")
        missing = sorted(set(CSV_COLUMNS) - set(headers))
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(missing))
        try:
            for row_number, raw in enumerate(reader, start=2):
                if None in raw or any(raw[column] is None for column in CSV_COLUMNS):
                    raise ValueError(f"CSV row {row_number} has the wrong number of fields; check comma quoting.")
                row = {column: raw[column].strip() for column in CSV_COLUMNS}
                if not any(row.values()):
                    continue
                try:
                    order_id = row["order_id"].upper()
                    customer_id = row["customer_id"].upper()
                    if re.fullmatch(r"ORD-[0-9]{8}", order_id) is None:
                        raise ValueError("order_id must be ORD- followed by eight digits.")
                    if order_id in seen:
                        raise ValueError(f"Duplicate order_id {order_id}.")
                    if re.fullmatch(r"CUST-[0-9]{3}", customer_id) is None:
                        raise ValueError("customer_id must be CUST- followed by three digits.")
                    for column in ("customer_name", "item", "category", "city"):
                        if not row[column]:
                            raise ValueError(f"{column} cannot be blank.")
                    quantity = int(row["quantity"])
                    price = Decimal(row["unit_price"])
                    if quantity < 1 or not price.is_finite() or price < 0:
                        raise ValueError("quantity must be positive and unit_price must be finite and non-negative.")
                    if price != price.quantize(Decimal("0.01")):
                        raise ValueError("unit_price must have at most two decimal places.")
                    currency = row["currency"].upper()
                    if re.fullmatch(r"[A-Z]{3}", currency) is None:
                        raise ValueError("currency must be a three-letter code, e.g. INR.")
                    status = row["status"].lower()
                    if status not in STATUSES:
                        raise ValueError("Unsupported status; use " + ", ".join(sorted(STATUSES)) + ".")
                    order_date = parse_date(row["order_date"], "order_date")
                    expected = parse_date(row["expected_delivery_date"], "expected_delivery_date")
                    delivered = parse_date(row["delivery_date"], "delivery_date")
                    if order_date is None:
                        raise ValueError("order_date is required.")
                    if status in {"delivered", "returned"} and delivered is None:
                        raise ValueError("Delivered/returned orders require delivery_date.")
                    if status not in {"delivered", "returned"} and delivered is not None:
                        raise ValueError("Only delivered/returned orders should have delivery_date.")
                    if (expected and expected < order_date) or (delivered and delivered < order_date):
                        raise ValueError("Delivery dates cannot be earlier than order_date.")
                except (ValueError, InvalidOperation) as exc:
                    raise ValueError(f"CSV row {row_number}: {exc}") from exc
                seen.add(order_id)
                orders.append({
                    **row, "order_id": order_id, "customer_id": customer_id,
                    "quantity": quantity, "unit_price": f"{price:.2f}",
                    "total_amount": f"{price * quantity:.2f}", "currency": currency,
                    "status": status, "order_date": order_date.isoformat(),
                    "expected_delivery_date": expected.isoformat() if expected else None,
                    "delivery_date": delivered.isoformat() if delivered else None,
                    "days_since_delivery": (date.today() - delivered).days if delivered else None,
                })
        except csv.Error as exc:
            raise ValueError(f"Cannot parse {DATA_FILE.name}: {exc}") from exc
    return orders


def totals(orders: list[dict]) -> dict:
    values = defaultdict(Decimal)
    for order in orders:
        values[order["currency"]] += Decimal(order["total_amount"])
    return {
        "order_count": len(orders),
        "total_quantity": sum(order["quantity"] for order in orders),
        "totals_by_currency": {currency: f"{amount:.2f}" for currency, amount in sorted(values.items())},
    }


@server.tool()
def lookup_order(order_id: str) -> dict:
    """Look up an existing order by its exact ID in orders.csv.

    Returns its customer, item, status, quantity, amounts, city, and dates.
    Requires ORD- followed by eight digits, e.g. ORD-12345678. Returns found=false
    for an absent ID. For customer, status, item, date, amount or city searches,
    use search_orders instead. Reads the latest CSV without changing data.
    """
    order_id = order_id.strip().upper()
    if re.fullmatch(r"ORD-[0-9]{8}", order_id) is None:
        raise ValueError("order_id must be ORD- followed by eight digits, e.g. ORD-12345678.")
    logger.info("lookup_order order_id=%s", order_id)
    for order in read_orders():
        if order["order_id"] == order_id:
            return {"found": True, "order_id": order_id, "source": DATA_FILE.name, "current_date": date.today().isoformat(), "order": order}
    return {"found": False, "order_id": order_id, "message": "No order with this ID."}


@server.tool()
def search_orders(
    customer_name: str | None = None,
    customer_id: str | None = None,
    status: OrderStatus | None = None,
    item: str | None = None,
    city: str | None = None,
    category: str | None = None,
    currency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: DateField = "order_date",
    min_total: float | None = None,
    max_total: float | None = None,
    group_by: GroupField | None = None,
    sort_by: SortField = "order_date",
    descending: bool = True,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Search the current orders.csv by filters, or obtain counts and order-value totals.

    Use for prompts such as show shipped orders, find Aditi's mouse orders,
    delivered orders in Mumbai, orders above INR 2000, or totals grouped by city.
    All supplied filters are combined with AND. Customer name, item, city and
    category use case-insensitive substring matching; customer_id is exact.
    Dates are inclusive YYYY-MM-DD bounds on date_field. For orders delivered
    during a period, select date_field=delivery_date and status=delivered.
    min_total/max_total compare quantity times unit_price, inclusively.
    limit must be 1..100 and offset >=0. No filters lists all orders with pagination.
    total_matches, totals_by_currency, total_quantity, counts_by_status and groups
    cover ALL matches, independent of the returned page. Amounts are exact decimal
    strings; currency totals stay separate. Order-value sums include every matched
    status, so specify status if needed. Follow next_offset to retrieve more rows.
    The tool only reads the CSV; it cannot create, cancel or update orders.
    """
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be between 1 and 100, and offset must be non-negative.")
    start = parse_date(date_from, "date_from")
    end = parse_date(date_to, "date_to")
    if start and end and start > end:
        raise ValueError("date_from must be earlier than or equal to date_to.")
    low = Decimal(str(min_total)) if min_total is not None else None
    high = Decimal(str(max_total)) if max_total is not None else None
    if any(value is not None and (not value.is_finite() or value < 0) for value in (low, high)):
        raise ValueError("Amount bounds must be finite and non-negative.")
    if low is not None and high is not None and low > high:
        raise ValueError("min_total must be less than or equal to max_total.")
    text_filters = {"customer_name": customer_name, "item": item, "city": city, "category": category}
    for field, value in text_filters.items():
        if value is not None and not value.strip():
            raise ValueError(f"{field} cannot be blank; omit the filter to search all values.")
    if customer_id is not None:
        customer_id = customer_id.strip().upper()
        if re.fullmatch(r"CUST-[0-9]{3}", customer_id) is None:
            raise ValueError("customer_id must be CUST- followed by three digits.")
    if currency is not None:
        currency = currency.strip().upper()
        if re.fullmatch(r"[A-Z]{3}", currency) is None:
            raise ValueError("currency must be a three-letter code, e.g. INR.")
    matches = []
    for order in read_orders():
        if any(value is not None and value.strip().casefold() not in order[field].casefold() for field, value in text_filters.items()):
            continue
        if customer_id is not None and order["customer_id"] != customer_id:
            continue
        if status is not None and order["status"] != status:
            continue
        if currency is not None and order["currency"] != currency:
            continue
        selected_date = parse_date(order[date_field], date_field)
        if (start or end) and (selected_date is None or (start and selected_date < start) or (end and selected_date > end)):
            continue
        amount = Decimal(order["total_amount"])
        if (low is not None and amount < low) or (high is not None and amount > high):
            continue
        matches.append(order)
    present = [order for order in matches if order[sort_by] is not None]
    missing = [order for order in matches if order[sort_by] is None]
    present.sort(
        key=lambda order: (Decimal(order[sort_by]) if sort_by == "total_amount" else order[sort_by], order["order_id"]),
        reverse=descending,
    )
    matches = present + missing
    page = matches[offset:offset + limit]
    has_more = offset + len(page) < len(matches)
    supplied = {
        **text_filters, "customer_id": customer_id, "status": status, "currency": currency,
        "date_from": date_from, "date_to": date_to, "date_field": date_field,
        "min_total": min_total, "max_total": max_total,
    }
    result = {
        "found": bool(matches), "source": DATA_FILE.name, "current_date": date.today().isoformat(),
        "filters": {key: value for key, value in supplied.items() if value is not None},
        "total_matches": len(matches), "returned_count": len(page), "offset": offset, "limit": limit,
        "has_more": has_more, "next_offset": offset + len(page) if has_more else None,
        **totals(matches), "counts_by_status": dict(sorted(Counter(order["status"] for order in matches).items())),
        "orders": page,
    }
    if group_by:
        grouped = defaultdict(list)
        for order in matches:
            grouped[order[group_by]].append(order)
        result["group_by"] = group_by
        result["groups"] = [{"group": key, **totals(rows)} for key, rows in sorted(grouped.items())]
    logger.info("search_orders matched=%s returned=%s", len(matches), len(page))
    return result


if __name__ == "__main__":
    server.run(transport="stdio")
