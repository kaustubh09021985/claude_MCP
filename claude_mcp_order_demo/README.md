# CSV order agent with Python, Claude and MCP

A small implementation of the example in your screenshot. It includes an
interactive Python chat client, a real MCP server, two read-only order tools,
an editable CSV dataset, input validation, and integration checks.

Run `python client.py` to type your own prompts. The client discovers the MCP
tools, sends their definitions and your prompt to Claude, executes requested
tools through MCP, and passes results back to Claude for its answer. Follow-up
questions retain conversation history for the current session.

The data is fictional and stored in `orders.csv`, beside `server.py`. It contains
30 orders across 10 customers, six statuses, and seven cities. Sample orders are
dated November 2026. The project does not connect to a real customer system.

## CSV data and example prompts

`lookup_order` retrieves one exact order ID. `search_orders` supports filtering,
sorting, pagination, counts, and grouped order-value totals. Claude chooses the
tool and fills its arguments from your prompt.

| Type this in the chat | Tool or query |
| --- | --- |
| What is the status of order ORD-12345678? | `lookup_order(order_id="ORD-12345678")` |
| Show all orders for Aditi Shah. | `search_orders(customer_name="Aditi Shah")` |
| Show delivered orders in Mumbai. | `search_orders(status="delivered", city="Mumbai")` |
| Find wireless mouse orders. | `search_orders(item="wireless mouse")` |
| Show pending orders. | `search_orders(status="pending")` |
| Show orders worth at least INR 2000. | `search_orders(min_total=2000, currency="INR")` |
| Which orders were delivered on 19 November 2026? | `search_orders(status="delivered", date_field="delivery_date", date_from="2026-11-19", date_to="2026-11-19")` |
| Show the five highest-value orders. | `search_orders(sort_by="total_amount", descending=True, limit=5)` |
| How many orders are in each status? | `search_orders(group_by="status", limit=1)` |
| What is the total value of Aditi's delivered orders? | `search_orders(customer_name="Aditi", status="delivered", limit=1)` |
| Group order counts and values by city. | `search_orders(group_by="city", limit=1)` |

Follow-ups such as "Only the shipped ones" or "Now show those in Mumbai" reuse
conversation context. "My orders" requires a customer name or ID because this
demo has no signed-in customer identity.

The original sample data contains six delivered orders in Mumbai. Aditi has four
orders overall and three delivered orders, whose total order value is INR 3,994.00.

CSV columns:

| Column | Example or rule |
| --- | --- |
| `order_id` | Unique ID such as `ORD-12345678` |
| `customer_id` | ID such as `CUST-001` |
| `customer_name` | Fictional customer name |
| `item`, `category` | Product description and category |
| `quantity` | Positive integer |
| `unit_price` | Non-negative amount with up to two decimal places |
| `currency` | Three-letter code; the supplied sample uses `INR` |
| `status` | `pending`, `processing`, `shipped`, `delivered`, `cancelled`, or `returned` |
| `order_date` | Required date in `YYYY-MM-DD` format |
| `expected_delivery_date` | Optional date in `YYYY-MM-DD` format |
| `delivery_date` | Actual date; required for delivered/returned orders and blank otherwise |
| `city` | Delivery city |

The server calculates `total_amount = quantity * unit_price` and
`days_since_delivery` when reading the file. Monetary results are decimal strings,
and totals for different currencies remain separate. Order-value totals include
all matched statuses; they are not a calculation of net revenue.

Edit the CSV in VS Code or a spreadsheet editor and save it as comma-separated
UTF-8 CSV. Keep the header names and date formats. Every tool call reloads the
file, so saved edits appear on the next query. The tools themselves only read.
Restart the client once after installing this updated project so it discovers
both tools; later CSV data edits do not require a restart.

By default, searches return 20 rows, with a maximum of 100 per page. The response
contains `total_matches`, `returned_count`, `has_more`, and `next_offset`. Counts,
totals, and groups are computed over all matches before pagination. Search bounds
are inclusive; all supplied filters are combined with AND. Text searches use
case-insensitive substring matching. IDs and statuses are exact values.

## Prerequisites

- Python 3.11 or later (verified here with Python 3.12.14).
- An Anthropic API key and access to a Claude model for `client.py` chat. API
  requests use your Anthropic API account's billing and usage limits.
- Internet access to install dependencies and call Claude.
- Claude Code is optional; the separate Claude Code instructions below require
  it to be installed and authenticated.

This example pins `mcp==1.29.0` to match the `FastMCP` interface in the screenshot.
SDK 2.x renamed that server class to `MCPServer` and changed other APIs. Use this
project's requirements file to reproduce this particular example. The Python
chat client also uses the official Anthropic SDK, pinned to version 2.12.0.

## 1. Open the project in VS Code and install

Extract the ZIP and open its `claude_mcp_order_demo` folder in VS Code. In its
terminal, run the following commands on macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell equivalent (activation is not needed):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Start the interactive Python client

With the environment active, run:

```bash
python client.py
```

On Windows, use `.\.venv\Scripts\python.exe client.py`.

If `ANTHROPIC_API_KEY` is not set, the client asks for your key at a hidden
prompt. Enter it in your own terminal. It is held in memory and is not saved by
the application. You can create a key at https://platform.claude.com/.

The client launches the server itself with the same Python interpreter, so there
is no separate server terminal to start and no Claude Code registration required
for this route. It also works if your virtual environment is named `.venv-1`.

Illustrative session (answer wording can vary):

```text
Anthropic API key (hidden):
Connected MCP tools: lookup_order, search_orders
Claude model: claude-sonnet-5-5
Type a question. /reset clears the conversation; /exit quits.
You: What is the status of order ORD-12345678?
[MCP] lookup_order({"order_id": "ORD-12345678"})
Claude: Your wireless mouse was delivered three days ago.
You: Show all orders for Aditi Shah.
[MCP] search_orders({"customer_name": "Aditi Shah"})
Claude: Aditi has four orders in the sample data.
You: What is the total value of her delivered orders?
[MCP] search_orders({"customer_name": "Aditi Shah", "status": "delivered", "limit": 1})
Claude: The total order value is INR 3,994.00.
You: /exit
```

Commands:

| Command | Effect |
| --- | --- |
| `/exit`, `exit`, or `quit` | Close the chat and server subprocess. |
| `/reset` | Clear this session's conversation history. |
| `python client.py --list-tools` | Inspect tools without an API key or an LLM call. |
| `python client.py --prompt "Check order ORD-12345678"` | Ask one question and exit. |
| `python client.py --model YOUR_MODEL_ID` | Select another model available to your API account. |
| `python client.py --quiet` | Hide the tool-call trace lines. |

The default model is `claude-sonnet-5-5`; `ANTHROPIC_MODEL` can also set the default.

## 3. Verify the MCP server and client without API calls

On macOS/Linux, with the environment active:

```bash
python smoke_test.py
```

On Windows:

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

The script launches `server.py`, initializes an MCP session, discovers both tools,
and checks success, missing-record, invalid-ID, and missing-input responses. This does
not call an LLM and does not require an API key. It should finish with:

```text
All 5 MCP integration checks passed.
```

Verify the CSV backend, filters, pagination, aggregates, live edits, and validation:

```bash
python csv_test.py
```

This uses temporary fixture copies for edit/error cases and leaves the supplied
CSV unchanged. The expected last line is:

```text
All 10 CSV integration checks passed (real MCP; no LLM calls).
```

Also check the client-to-model/tool bridge:

```bash
python client_test.py
```

This uses the real Anthropic SDK with an HTTP mock and a real MCP subprocess.
It validates the tool-result round trip, follow-up history, multiple tool calls,
error mapping, an unavailable tool, API failure handling, the tool-loop limit,
and CSV searches with follow-up filters and totals. No paid API requests occur
in any of the three test scripts. Its last line should be:

```text
All 7 client integration checks passed (mocked Claude HTTP; real MCP).
```

You can see ordinary server logs on stderr. These are separate from the MCP
messages on stdout.

## 4. Optional: register the server with Claude Code

Run these commands from this project directory. The quoted absolute paths keep
the server tied to the Python environment where its dependencies were installed.

macOS/Linux:

```bash
claude mcp add --transport stdio shopassist -- "$PWD/.venv/bin/python" "$PWD/server.py"
claude mcp get shopassist
claude mcp list
```

Windows PowerShell:

```powershell
claude mcp add --transport stdio shopassist -- "$((Get-Location).Path)\.venv\Scripts\python.exe" "$((Get-Location).Path)\server.py"
claude mcp get shopassist
claude mcp list
```

`shopassist` is the name used by Claude Code for this connection. Its tools will
appear as `mcp__shopassist__lookup_order` and `mcp__shopassist__search_orders`.

The default configuration scope is local to this project. Run Claude Code from
the same project directory. An `Added ...` message confirms configuration was
saved; check the reported connection status separately.

For stdio, Claude Code launches the Python server when it connects. You do not
need to manually run a second server terminal. Running `python server.py` alone
normally waits for MCP messages; it is not an HTTP web page.

## 5. Optional: ask Claude Code to use the tool

Start an interactive session in this project:

```bash
claude
```

Use `/mcp` to inspect the connection and tools, then ask:

```text
Use lookup_order to check order ORD-12345678. Answer in one sentence.
```

Follow any normal tool-permission prompt. Based on the fictional data, an answer
should say the wireless mouse was delivered three days ago. Exact wording varies.

For a one-shot command that grants this particular read-only tool permission:

```bash
claude -p "Use lookup_order to check order ORD-12345678. Answer in one sentence." --allowedTools "mcp__shopassist__lookup_order"
```

`--allowedTools` grants execution permission to matching tools; it does not
restrict all other available tools. `-p` requests a non-interactive response.

## What each part does

| Part | Responsibility |
| --- | --- |
| `@server.tool()` | Registers the function as an MCP tool. |
| Function name | Supplies `lookup_order` as the tool name. |
| Docstring | Tells Claude when the tool is relevant, which ID it needs, and what it returns. |
| `order_id: str` | Supplies a required string parameter in the input schema. |
| `re.fullmatch(...)` | Validates the actual ID format on the server. |
| `csv.DictReader(...)` | Reads the current CSV and validates records on every call. |
| `search_orders(...)` | Applies filters, computes full-match totals, and returns a page of records. |
| Return dictionary | Becomes the tool result through the SDK. |
| `server.run(transport="stdio")` | Runs MCP communication through the subprocess's stdin/stdout. |
| Claude Code | Manages its MCP client, asks the model to select a tool, invokes the tool, and passes results back to the model. |
| `client.py` | Implements that conversation/tool loop in your own Python program, with an interactive prompt. |

## Inside the Python client

1. `connect_mcp()` starts `server.py` over stdio and initializes the MCP session.
2. `discover_tools()` converts each MCP tool's `inputSchema` to Claude's
   `input_schema`, retaining its name and description.
3. `ChatClient.ask()` sends your prompt and completed conversation history to
   Claude's Messages API with those tool definitions.
4. If Claude returns `tool_use`, `execute_tool()` calls `session.call_tool(...)`.
5. The client returns one `tool_result` for each `tool_use`, preserving its ID and
   mapping MCP's `isError` to Claude's `is_error`.
6. Claude receives the results and answers or requests another tool. A completed
   turn is saved in memory for follow-up questions.

Only tool names in the discovered list can be dispatched. Both tools are
read-only. MCP input and CSV validation remain on the server. The client supplies
a session date so relative-date prompts have a reference point.

## Error behavior

- An absent but well-formed ID returns `found: false`, a normal lookup result.
- An incorrectly formatted ID raises `ValueError`; the SDK returns a tool error
  with `isError: true` and a message the model can use to correct the input.
- A missing required parameter is rejected by the SDK before the function runs.
- The Python client returns tool failures to Claude as matched `tool_result`
  blocks, including when a response requests several tools.
- The client allows up to five tool rounds per turn and a 90-second turn deadline.
  MCP calls have a 10-second request timeout. Claude API attempts have a 30-second
  timeout and up to two SDK retries for eligible transient errors. There is no
  extra application retry layer around the SDK.
- Failed/incomplete turns do not corrupt the last completed conversation history.
  An invalid API key ends the client with a clear message; an unavailable model
  can be changed with `--model`.
- HTTP 400 and other generic API status failures display the API's actual error
  message, error type when available, and request ID. An unchanged HTTP 400
  request is not automatically retried by the SDK.
- This CSV lookup has no remote dependency, so it deliberately has no
  artificial network-retry loop. When replacing it with an API, handle temporary
  network/429/503 failures with bounded retries, backoff, jitter, and a deadline.
  Coordinate with any retries already built into the dependency SDK. Respect
  server-provided retry timing and use idempotency protection for writes.

With stdio, do not use ordinary `print()` statements inside the server for logs:
stdout is the protocol channel. This example writes logs to stderr.

## Verification performed here

Python 3.12.14, MCP SDK 1.29.0, and Anthropic SDK 2.12.0. Both test scripts use
actual stdio MCP connections. The client tests use mocked Claude HTTP responses;
live Claude responses require your own API credentials. Claude Code itself was
not executed here because it is not installed in this environment.

All five basic MCP checks, 10 CSV checks, and seven client checks passed (22 in
total). These include real CSV reads and real MCP subprocesses. Claude HTTP
responses are mocked in client tests, so they validate the integration mechanics
rather than live model interpretation. Live prompts require working Anthropic
API access; a previous account/request HTTP 400 must be diagnosed from its detailed
message independently of the CSV backend.

The integration tests target the original 30-row sample CSV. If you replace the
dataset with your own records, update their expected counts and sample IDs.

To use a different CSV location, set `ORDERS_CSV` in the client's environment before
starting it. The client forwards that setting to the MCP subprocess. Otherwise the
CSV is always resolved beside `server.py`, independently of the working directory.

## Troubleshooting HTTP 400

A status code and request ID alone do not establish the cause. The client now
prints the server's detailed reason. Restart `python client.py`, repeat your
question, and use the full error message to choose the fix:

- If the message names a request field, correct that field or tool schema.
- If it names billing, credits, or a spend limit, check the API account's settings.
- If it names an unavailable model, select a model available to your API account
  with `--model`.

Retain the request ID when reporting a persistent API error to Anthropic support.

## References

- MCP SDK v1 package and documentation: https://pypi.org/project/mcp/1.29.0/
- Current MCP Python SDK documentation: https://py.sdk.modelcontextprotocol.io/
- Claude Code MCP setup: https://code.claude.com/docs/en/mcp
- Claude Code CLI flags: https://code.claude.com/docs/en/cli-reference
- Claude tool-result loop: https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
- Anthropic Python SDK: https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python
