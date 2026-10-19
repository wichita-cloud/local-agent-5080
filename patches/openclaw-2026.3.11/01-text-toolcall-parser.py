#!/usr/bin/env python3
"""Patch openclaw's Ollama buildAssistantMessage to parse text-shaped tool calls.

Problem: Local LLMs (a local Qwen 27B) often emit tool calls as text
like `read(path="memory/notes.md")` instead of producing structured
tool_calls JSON. Openclaw only checks the structured field, so the user sees
the text and the tool never runs.

Fix: After openclaw extracts the text, scan it for text-shaped tool calls
matching the known tool names. If found AND no structured tool_calls, convert
each match into a synthetic tool_calls entry so openclaw's downstream code
treats it as a real tool invocation.

Idempotent. Applied by apply.sh.

Usage:
  python3 01-text-toolcall-parser.py
"""
import re
import os
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
WS = os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace"))  # set by apply.sh
import sys
import shutil
import time

TARGET = DIST + "/auth-profiles-MKCH-k1W.js"
BACKUP_SUFFIX = ".bak-text-toolcall-pre"

MARKER = "/* __LOCAL_AGENT_TEXT_TOOLCALL_PARSER_PATCHED__ */"

# JavaScript helper to inject. Defined once at the top of the module.
HELPER_JS = r"""
/* __LOCAL_AGENT_TEXT_TOOLCALL_PARSER_PATCHED__ */
const __KNOWN_TOOLS = new Set(["read","edit","write","exec","process","browser",
  "canvas","nodes","cron","message","tts","gateway","agents_list",
  "sessions_list","sessions_history","sessions_send","sessions_spawn",
  "subagents","session_status","web_search","web_fetch","memory_search",
  "memory_get"]);
function __parseTextToolCallsWithSpans(text) {
  if (!text || typeof text !== "string") return { calls: [], strippedText: text };
  const result = __parseTextToolCalls(text, /*returnSpans*/ true);
  if (!result.calls.length) return { calls: [], strippedText: text };
  // Strip matched spans from the visible text, working from end to start
  let stripped = text;
  const spans = result.spans.slice().sort((a, b) => b.start - a.start);
  for (const sp of spans) {
    stripped = stripped.slice(0, sp.start) + stripped.slice(sp.end);
  }
  // Cleanup: collapse multi-newlines, trim
  stripped = stripped.replace(/\n{3,}/g, "\n\n").trim();
  return { calls: result.calls, strippedText: stripped };
}
function __parseTextToolCalls(text, returnSpans) {
  if (!text || typeof text !== "string") return returnSpans ? { calls: [], spans: [] } : [];
  const calls = [];
  const spans = [];
  // PERMISSIVE matcher: <tool_call>...</tool_call> blocks where the function name
  // is any known tool. Catches all separator variants Qwen has invented so far:
  //   <function=NAME>, <function NAME>, <function/NAME>, <function(NAME)>, etc.
  // Args extraction tries JSON, XML <parameter=key>val</parameter>, bare <key>val</key>,
  // and Python-style kwargs in fallback order.
  const blockRe = /<tool_call>([\s\S]*?)<\/tool_call>/gi;
  let bm;
  while ((bm = blockRe.exec(text)) !== null) {
    const block = bm[1];
    // Find tool name: any known tool name appearing in a function-call-shaped context.
    let name = null;
    for (const candidate of __KNOWN_TOOLS) {
      const nameRe = new RegExp("(?:<function[^a-z_]*|function[^a-z_]*|name[^a-z_]+)" + candidate + "\\b", "i");
      if (nameRe.test(block)) { name = candidate; break; }
    }
    if (!name) continue;
    const args = {};
    // Try 1: JSON object embedded anywhere — `{"key": "val"}`
    const jsonMatch = block.match(/\{[\s\S]*?\}/);
    if (jsonMatch) {
      try {
        const parsed = JSON.parse(jsonMatch[0]);
        if (parsed && typeof parsed === "object") Object.assign(args, parsed);
      } catch {
        try {
          const fixed = jsonMatch[0]
            .replace(/([{,]\s*)([a-z_][a-z0-9_]*)\s*:/gi, '$1"$2":')
            .replace(/'/g, '"');
          const parsed = JSON.parse(fixed);
          if (parsed && typeof parsed === "object") Object.assign(args, parsed);
        } catch {}
      }
    }
    // Try 2: <parameter=KEY>VAL</parameter>
    const paramRe = /<parameter=([a-z_][a-z0-9_]*)>([\s\S]*?)<\/parameter>/gi;
    let pm;
    while ((pm = paramRe.exec(block)) !== null) {
      if (!(pm[1] in args)) args[pm[1]] = pm[2].trim();
    }
    // Try 3: bare <KEY>VAL</KEY>
    const bareKeys = ["path","content","command","url","query","oldText","newText","input","key","value","ref","action","text","file","filename","file_path"];
    for (const k of bareKeys) {
      const re = new RegExp("<" + k + ">([\\s\\S]*?)<\\/" + k + ">", "i");
      const m2 = block.match(re);
      if (m2 && !(k in args)) args[k] = m2[1].trim();
    }
    // Try 4: Python-style kwargs `key="value"`
    const kwRe = /\b([a-z_][a-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/gi;
    let kw;
    while ((kw = kwRe.exec(block)) !== null) {
      if (!(kw[1] in args) && !["function","name","tool_call"].includes(kw[1])) {
        args[kw[1]] = kw[2] !== undefined ? kw[2] : kw[3];
      }
    }
    // Per-tool required-arg validation — skip incomplete calls instead of firing
    // them and getting a runtime "Validation failed" error.
    const REQUIRED = {
      "write":         ["path", "content"],
      "edit":          ["path", "oldText", "newText"],
      "read":          ["path"],
      "exec":          ["command"],
      "web_fetch":     ["url"],
      "web_search":    ["query"],
      "memory_search": ["query"],
      "memory_get":    ["path"],
    };
    const req = REQUIRED[name];
    if (req && !req.every(k => k in args)) continue;  // missing required arg → skip
    if (Object.keys(args).length > 0) {
      calls.push({ type: "function", function: { name, arguments: args } });
      spans.push({ start: bm.index, end: bm.index + bm[0].length });
    }
  }
  // Fallback for EOS-truncated XML: match opening <tool_call><function=NAME> + bare params
  // when no complete tool_call was matched above. Only runs if calls is still empty.
  if (calls.length === 0) {
    const openRe = /<tool_call>\s*<function[\s=\/]+([a-z_][a-z0-9_]*)\s*>([\s\S]*)$/i;
    const om = openRe.exec(text);
    if (om) {
      const name = om[1];
      if (__KNOWN_TOOLS.has(name)) {
        const body = om[2];
        const args = {};
        const paramRe = /<parameter=([a-z_][a-z0-9_]*)>([\s\S]*?)(?:<\/parameter>|$)/gi;
        let pm;
        while ((pm = paramRe.exec(body)) !== null) args[pm[1]] = pm[2].trim();
        const bareKeys = ["path","content","command","url","query","oldText","newText"];
        for (const k of bareKeys) {
          const re = new RegExp("<" + k + ">([\\s\\S]*?)(?:<\\/" + k + ">|$)", "i");
          const bm = body.match(re);
          if (bm && !(k in args)) args[k] = bm[1].trim();
        }
        if (Object.keys(args).length > 0) {
          calls.push({ type: "function", function: { name, arguments: args } });
          spans.push({ start: om.index, end: text.length });
        }
      }
    }
  }
  // Pattern 1: tool_name({"key": "value"}) — JSON object literal
  // Pattern 2: tool_name({key: "value"}) — JS object literal
  // Pattern 3: tool_name(key="value", key2="value2") — Python-style kwargs
  // Conservative: tool name must be alphanumeric+underscore, immediately followed by (
  const re1 = /\b([a-z_][a-z0-9_]*)\(\s*(\{[\s\S]*?\})\s*\)/gi;
  const re3 = /\b([a-z_][a-z0-9_]*)\(\s*([a-z_][a-z0-9_]*\s*=\s*(?:"[^"]*"|'[^']*'|[^,)]+)(?:\s*,\s*[a-z_][a-z0-9_]*\s*=\s*(?:"[^"]*"|'[^']*'|[^,)]+))*)\s*\)/gi;
  const seen = new Set();
  let m;
  while ((m = re1.exec(text)) !== null) {
    const name = m[1];
    if (!__KNOWN_TOOLS.has(name)) continue;
    const argsRaw = m[2];
    const key = name + "::" + argsRaw;
    if (seen.has(key)) continue;
    seen.add(key);
    let args;
    try {
      // Try strict JSON first
      args = JSON.parse(argsRaw);
    } catch {
      try {
        // Loose JS object: convert single quotes + unquoted keys to JSON
        const fixed = argsRaw
          .replace(/([{,]\s*)([a-z_][a-z0-9_]*)\s*:/gi, '$1"$2":')
          .replace(/'/g, '"');
        args = JSON.parse(fixed);
      } catch {
        continue; // can't parse → skip
      }
    }
    calls.push({ type: "function", function: { name, arguments: args } });
    spans.push({ start: m.index, end: m.index + m[0].length });
  }
  while ((m = re3.exec(text)) !== null) {
    const name = m[1];
    if (!__KNOWN_TOOLS.has(name)) continue;
    const kwargsRaw = m[2];
    const key = name + "::" + kwargsRaw;
    if (seen.has(key)) continue;
    seen.add(key);
    const args = {};
    // Parse k="v" or k='v' or k=number pairs
    const kwRe = /([a-z_][a-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^,)\s]+))/gi;
    let kw;
    while ((kw = kwRe.exec(kwargsRaw)) !== null) {
      args[kw[1]] = kw[2] ?? kw[3] ?? kw[4];
    }
    if (Object.keys(args).length > 0) {
      calls.push({ type: "function", function: { name, arguments: args } });
      spans.push({ start: m.index, end: m.index + m[0].length });
    }
  }
  return returnSpans ? { calls, spans } : calls;
}
"""

# Modified buildAssistantMessage: inject parsing if no structured tool_calls
ORIGINAL_FN = '''function buildAssistantMessage(response, modelInfo) {
	const content = [];
	const text = response.message.content || response.message.thinking || response.message.reasoning || "";
	if (text) content.push({
		type: "text",
		text
	});
	const toolCalls = response.message.tool_calls;
	if (toolCalls && toolCalls.length > 0) for (const tc of toolCalls) content.push({
		type: "toolCall",
		id: `ollama_call_${randomUUID()}`,
		name: tc.function.name,
		arguments: tc.function.arguments
	});
	return buildAssistantMessage$1({
		model: modelInfo,
		content,
		stopReason: toolCalls && toolCalls.length > 0 ? "toolUse" : "stop",'''

PATCHED_FN = '''function buildAssistantMessage(response, modelInfo) {
	const content = [];
	const rawText = response.message.content || response.message.thinking || response.message.reasoning || "";
	let text = rawText;
	let toolCalls = response.message.tool_calls;
	/* __LOCAL_AGENT_TEXT_TOOLCALL_PARSER_PATCHED__ inline */
	if ((!toolCalls || toolCalls.length === 0) && rawText) {
		const parsed = __parseTextToolCallsWithSpans(rawText);
		if (parsed.calls.length > 0) {
			toolCalls = parsed.calls;
			text = parsed.strippedText;
		}
	}
	if (text && text.trim().length > 0) content.push({
		type: "text",
		text
	});
	if (toolCalls && toolCalls.length > 0) for (const tc of toolCalls) content.push({
		type: "toolCall",
		id: `ollama_call_${randomUUID()}`,
		name: tc.function.name,
		arguments: tc.function.arguments
	});
	return buildAssistantMessage$1({
		model: modelInfo,
		content,
		stopReason: toolCalls && toolCalls.length > 0 ? "toolUse" : "stop",'''


def main():
    if not os.path.exists(TARGET):
        print(f"FATAL: target file not found: {TARGET}", file=sys.stderr)
        sys.exit(2)

    src = open(TARGET).read()

    if MARKER in src:
        print(f"already patched (marker present in {TARGET})")
        return

    if ORIGINAL_FN not in src:
        print("FATAL: original buildAssistantMessage signature not found.")
        print("       The openclaw image may have changed. Refusing to patch.")
        sys.exit(3)

    # Backup
    backup_path = f"{TARGET}{BACKUP_SUFFIX}-{int(time.time())}"
    shutil.copy2(TARGET, backup_path)
    print(f"backup: {backup_path}")

    # Inject helper at the top of the OLLAMA stream section.
    # Find a stable anchor that comes before buildAssistantMessage.
    anchor = "//#region src/agents/ollama-stream.ts"
    if anchor not in src:
        print(f"FATAL: anchor '{anchor}' not found")
        sys.exit(4)

    patched = src.replace(anchor, anchor + "\n" + HELPER_JS.strip() + "\n", 1)
    patched = patched.replace(ORIGINAL_FN, PATCHED_FN, 1)

    if patched == src:
        print("FATAL: no changes made — replace() didn't match")
        sys.exit(5)

    # Patch 2: extractToolCalls must include type:"function" for Ollama upstream
    OLD_EXTRACT = '''function extractToolCalls(content) {
	if (!Array.isArray(content)) return [];
	const parts = content;
	const result = [];
	for (const part of parts) if (part.type === "toolCall") result.push({ function: {
		name: part.name,
		arguments: part.arguments
	} });
	else if (part.type === "tool_use") result.push({ function: {
		name: part.name,
		arguments: part.input
	} });
	return result;
}'''
    NEW_EXTRACT = '''function extractToolCalls(content) {
	if (!Array.isArray(content)) return [];
	const parts = content;
	const result = [];
	for (const part of parts) if (part.type === "toolCall") result.push({ type: "function", function: {
		name: part.name,
		arguments: part.arguments
	} });
	else if (part.type === "tool_use") result.push({ type: "function", function: {
		name: part.name,
		arguments: part.input
	} });
	return result;
}'''
    if OLD_EXTRACT in patched:
        patched = patched.replace(OLD_EXTRACT, NEW_EXTRACT, 1)
        print(f"  modified: extractToolCalls to include type:'function' for Ollama compat")
    else:
        print(f"  WARNING: extractToolCalls signature didn't match — may already be patched")

    # Write
    open(TARGET, "w").write(patched)
    print(f"patched {TARGET}")
    print(f"  added: __parseTextToolCalls helper")
    print(f"  modified: buildAssistantMessage to detect text tool calls")

if __name__ == "__main__":
    main()
