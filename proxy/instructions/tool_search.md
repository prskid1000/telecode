# Unloaded tools + ToolSearch

If you see a `<system-reminder>` block titled:

`Unloaded tools (call ToolSearch to load schema before use):`

then **every tool listed there is NOT callable yet**.

## Rules (do not break)

1. **Never call an unloaded tool directly.** A name in the unloaded list is an identifier, not a loaded tool.
2. **Always load schema first:** `ToolSearch(query="<keyword>", max_results=5)` → read schema → call tool.
3. **On "no such tool" errors:** do **not** retry with a guessed name. Use `ToolSearch` with a keyword.
4. **Prefer dedicated tools over `web_search`.** If the unloaded list contains a domain tool (e.g. MySQL), load and use it instead of web searching.

## How to search

- Use the **short action** segment after the last `__`.
  - Example: `mcp__mcp_server_mysql__mysql_query` → query `"mysql"` or `"query"`, not the full name.
- Use exact selection when you know the name:
  - `ToolSearch(query="select:mcp__mcp_server_mysql__mysql_query", max_results=5)`

After the schema appears in a `<functions>` block, call the tool using the parameter names from that schema.

