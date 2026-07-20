"""Count hound-mcp tool-definition tokens: exactly what an MCP client pays.

Two surfaces:
  1. instructions  - sent once on `initialize` (the MCP `instructions` field).
  2. tools/list    - the 6 tool schemas (name + description + inputSchema + annotations),
                     sent on `tools/list` and injected into the model context.

We replicate the EXACT wire JSON the client receives (mcp.types.Tool serialized
via model_dump(exclude_none=True)), then tokenize with tiktoken cl100k_base
(±5% vs newer Claude/GPT-5 tokenizers). Per-tool breakdown + grand total.
"""
import sys, json
sys.path.insert(0, "src")

from mcp.types import Tool
import tiktoken

enc = tiktoken.get_encoding("cl100k_base")
def tok(s: str) -> int:
    return len(enc.encode(s))

from master_fetch.server import MasterFetchServer, HOUND_INSTRUCTIONS

defs = MasterFetchServer._TOOL_DEFS

print(f"{'tool':<22} {'desc_tok':>9} {'schema_tok':>11} {'total_tok':>10}")
print("-" * 56)
grand = 0
rows = []
for td in defs:
    # Exact wire object the client receives for this tool.
    tool = Tool(**td)
    wire = tool.model_dump(exclude_none=True)
    wire_json = json.dumps(wire, ensure_ascii=False)
    full = tok(wire_json)
    desc = tok(wire.get("description", ""))
    schema = full - desc
    rows.append((wire["name"], desc, schema, full))
    grand += full
for name, desc, schema, full in rows:
    print(f"{name:<22} {desc:>9} {schema:>11} {full:>10}")
print("-" * 56)
print(f"{'TOOLS/LIST TOTAL':<22} {'':>9} {'':>11} {grand:>10}")
print()

# The tools/list result envelope: {"tools": [ ... ]} - adds the wrapping braces + key.
envelope = json.dumps({"tools": [Tool(**td).model_dump(exclude_none=True) for td in defs]}, ensure_ascii=False)
env_tok = tok(envelope)
print(f"tools/list envelope ({{'tools':[...]}}) raw tokens : {env_tok}")
print(f"  (sum of per-tool == {grand}; envelope overhead == {env_tok - grand})")
print()

inst_tok = tok(HOUND_INSTRUCTIONS)
print(f"instructions (initialize, connect-time) : {inst_tok} tokens")
print()
print(f"=== CONNECT-TIME TOTAL (instructions + tools/list) : {inst_tok + env_tok} tokens ===")
print()

# Byte sizes for context
print("byte sizes (wire JSON):")
for name, _, _, _ in rows:
    pass
for td in defs:
    w = json.dumps(Tool(**td).model_dump(exclude_none=True), ensure_ascii=False)
    print(f"  {Tool(**td).name:<22} {len(w):>7} bytes")
print(f"  {'instructions':<22} {len(HOUND_INSTRUCTIONS):>7} bytes")
