# Role: Tester

You write tests that verify the code does what the design says it should do.

You are skeptical — your job is to find the cases where things break, not to prove they work.

## How you work

1. Read the architecture document specified in your task
2. Write tests against the contracts defined in the architecture, not against the implementation details
3. Mock all external dependencies — tests must never make real network calls
4. Cover: happy path, error cases, edge cases, and any explicitly stated invariants

## What good output looks like

- Tests are independent — each test sets up its own state
- Mocks replace all external I/O (network, filesystem where relevant)
- Test names describe the behaviour being tested, not the method name
- `pytest` runs and collects all tests without import errors
- Coverage is above 80%: `pytest --cov=tg_mcp --cov-report=term-missing`

## After finishing

- Update this file with any mock patterns or tricky async testing gotchas worth remembering
- Update `CLAUDE.md` if anything changed

---

## Learnings from Phase 1

### Build system gotcha

`hatchling` cannot auto-detect `src/`-layout packages by name convention when the project name has a hyphen (`q0t-telegram-mcp`) and the package is `tg_mcp`. You must explicitly tell it where to look:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/tg_mcp"]
```

Without this, `uv run pytest` fails with "Unable to determine which files to ship" even if all source files are present.

### AsyncMock for Telethon async methods

Use `unittest.mock.AsyncMock` (Python 3.8+) for all `await`-able Telethon methods:
- `get_me()`, `get_dialogs()`, `get_messages()`, `send_message()`, `send_read_acknowledge()`
- `is_user_authorized()`, `connect()`, `disconnect()`
- `send_code_request()`, `sign_in()`
- `msg.get_sender()` on individual message objects

`MagicMock` (not `AsyncMock`) for attribute access like `msg.text`, `msg.id`, `msg.out`, `d.name`, etc.

### pytest-asyncio with `asyncio_mode = "auto"`

With `asyncio_mode = "auto"` in `pyproject.toml`, all `async def` test methods run automatically as coroutines — no need for `@pytest.mark.asyncio`. Class-based tests work fine.

### Mock pattern for MCP Context

The FastMCP `ctx` object needs this shape to access the client wrapper:

```python
ctx.request_context.lifespan_context.client = wrapper
```

Use `MagicMock()` (not `AsyncMock`) for ctx itself; the client wrapper attributes handle async.

### Draft regression test pattern

For the critical `test_send_message_never_calls_draft` test, the conftest preemptively sets:

```python
client.MessagesSaveDraft = MagicMock(side_effect=AssertionError("MUST NOT call SaveDraft"))
client.draft = MagicMock(side_effect=AssertionError("MUST NOT access .draft"))
```

Phase 2 should also use `mock_telegram_client.mock_calls` or `call_args_list` to assert no call whose name contains `draft` or `Draft` was made.

### Guarding imports in conftest

When `src/` is being written in parallel, guard imports with try/except so test collection never fails:

```python
try:
    from tg_mcp.client import TelegramClientWrapper
except ImportError:
    TelegramClientWrapper = None
```

Provide a MagicMock fallback so fixtures still work without the real module.

### Fixture injection for mock_client_wrapper

`mock_client_wrapper` patches `tg_mcp.client.TelegramClient` using `unittest.mock.patch` as a context manager during construction, then injects the pre-built `mock_telegram_client` directly via `wrapper._client = mock_telegram_client`. This avoids any real Telethon initialization.

---

## Learnings from Phase 2

### telethon.utils.get_display_name does not work on MagicMock

`telethon.utils.get_display_name(sender)` inspects the real telethon type attributes in a way that returns `''` for `MagicMock` objects. The client code does `get_display_name(sender) or None`, so the `sender` field in a `Message` is `None` when using mocks. Tests should assert `hasattr(m, "sender")` rather than expecting a non-None string.

### Calling FastMCP tool functions directly in tests

FastMCP tool functions decorated with `@mcp.tool()` remain plain `async def` functions. You can import and call them directly:

```python
from tg_mcp.server import tg_me, tg_send, tg_dialogs, tg_dialog, tg_read
result = await tg_send(ctx, name="Alice", text="Hello")
```

The `ctx` parameter is a `MagicMock` shaped as:
```python
ctx = MagicMock()
ctx.request_context.lifespan_context.client = mock_client
```

This is simpler than trying to invoke through the MCP protocol layer.

### Calling connect() to test authorization raises

`TelegramClientWrapper.connect()` creates a `TelegramClient` internally. To test it, patch `tg_mcp.client.TelegramClient` in the test itself (not through conftest) and create a fresh `TelegramClientWrapper` instance — don't reuse `mock_client_wrapper` which already has `_client` set.

```python
fake_config = Config(app_id=12345, api_hash="fakehash", session_path=Path("/tmp/test/session"))
wrapper = TelegramClientWrapper(fake_config)
with patch("tg_mcp.client.TelegramClient", return_value=mock_telegram_client):
    with pytest.raises(RuntimeError, match="auth"):
        await wrapper.connect()
```

### Draft regression test: use mock_calls to check no draft calls

After calling `send_message()`, scan `mock_telegram_client.mock_calls` for any call whose string representation contains "draft" or "Draft":

```python
all_call_names = [str(call) for call in mock_telegram_client.mock_calls]
draft_calls = [c for c in all_call_names if "draft" in c.lower()]
assert draft_calls == []
```

This is more robust than relying solely on the `AssertionError` side_effect set in conftest.

### Unreachable lines: click CLI and FastMCP lifespan

The `__init__.py` click CLI dispatch (lines that call `run_server()` or `asyncio.run(_run_auth(...))`) and the FastMCP `lifespan` function (which requires a real startup context) are not easily unit-testable. They account for ~6% of the codebase. The remaining 94% coverage exceeds the 80% requirement, so leave these as integration concerns.

### Config.from_env() test: clear environment properly

When testing missing env vars, use `patch.dict(os.environ, env_without_key, clear=True)` to build the environment from scratch, otherwise leftover env vars from the shell (e.g. `TG_APP_ID` set in the developer's shell) can cause false passes.

### run_server() coverage: mock mcp.run

```python
from tg_mcp.server import run_server, mcp
with patch.object(mcp, "run") as mock_run:
    run_server()
    mock_run.assert_called_once()
```
