#!/usr/bin/env python3
"""When the dashboard's working output is switched off, hide the tool cards too, not just the raw results.

Why: patch 11 turns the dashboard's "Toggle assistant thinking/working output" (`chatShowThinking`) off by default, but
stock OpenClaw only uses that switch to drop finished tool RESULTS. Every tool CALL still shows as a card ("Web Search",
with the query as JSON and a View button), and while a turn is running, the live tool messages show their full output.
A person who asked "how did OU do?" saw a pile of JSON before the answer.

With the switch off this now:
  - removes tool-call blocks from assistant messages, and skips a message that held nothing else;
  - hides live tool messages while the agent works, showing the dashboard's own "reading" dots instead.
With the switch on, nothing changes: every call and result shows as before.

Edits dist/control-ui/assets/index-*.js. Signature-based and idempotent (marker LOCAL_AGENT_CONTROLUI_HIDE_TOOLCARDS_V1).
"""
import glob, os, sys
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
MARKER = "LOCAL_AGENT_CONTROLUI_HIDE_TOOLCARDS_V1"
EDITS = [
    # history: drop tool results (stock) AND tool-call blocks (new) when the switch is off
    ('!e.showThinking&&d.role.toLowerCase()==="toolresult"||t.push({kind:"message",key:ul(l,r),message:l})',
     'if(e.showThinking)t.push({kind:"message",key:ul(l,r),message:l});else if(d.role.toLowerCase()!=="toolresult"){'
     'const la=Array.isArray(l.content)?{...l,content:l.content.filter(c=>!["toolcall","tool_call","tooluse","tool_use"]'
     '.includes(String(c&&c.type||"").toLowerCase()))}:l;(!Array.isArray(la.content)||la.content.length>0)&&'
     't.push({kind:"message",key:ul(l,r),message:la})}'),
    # live run: show "reading" dots instead of each tool's raw output
    ('r<s.length&&t.push({kind:"message",key:ul(s[r],r+n.length),message:s[r]})',
     'r<s.length&&(e.showThinking?t.push({kind:"message",key:ul(s[r],r+n.length),message:s[r]}):'
     'r===a-1&&e.stream===null&&t.push({kind:"reading-indicator",key:"la-tools:"+e.sessionKey}))'),
]

files = glob.glob(DIST + "/control-ui/assets/index-*.js")
if not files:
    print("  skip: no dashboard bundle found at " + DIST + "/control-ui/assets", file=sys.stderr)
    sys.exit(0)
patched = 0
for fp in files:
    src = open(fp, encoding="utf-8").read()
    if MARKER in src:
        print("  already patched: " + fp); patched += 1; continue
    bad = [old for old, _ in EDITS if src.count(old) != 1]
    if bad:
        print("  WARN: signature not found exactly once in " + fp + ": " + bad[0][:60], file=sys.stderr)
        continue
    open(fp + ".pre-hide-toolcards", "w", encoding="utf-8").write(src)
    for old, new in EDITS:
        src = src.replace(old, new, 1)
    open(fp, "w", encoding="utf-8").write(src + "\n/* " + MARKER + " */\n")
    print("  patched: " + fp); patched += 1
sys.exit(0 if patched else 1)
