#!/usr/bin/env python3
"""While the agent works with the working output hidden, say what it is doing instead of showing bare dots.

Why: with patch 12, a run that uses tools shows only the dashboard's "reading" dots until the answer starts. A turn that
spent a minute writing a 10 KB file (2026-10-26) looked like a frozen chat, and the person asked whether it had locked up.
Now the same spot shows one line, e.g. "Working... last step: Writing projects/opportunity-log.md". The last finished
tool is named because the step in progress (the model composing the next tool call) isn't visible to the dashboard yet.

Needs patch 12 (it edits the line patch 12 adds). Edits dist/control-ui/assets/index-*.js. Signature-based and idempotent
(marker LOCAL_AGENT_CONTROLUI_WORKING_STATUS_V1).
"""
import glob, os, sys
DIST = os.environ.get("OPENCLAW_DIST", "/usr/lib/node_modules/openclaw/dist")  # set by apply.sh
MARKER = "LOCAL_AGENT_CONTROLUI_WORKING_STATUS_V1"
LABEL = ('((m)=>{try{const c=Array.isArray(m&&m.content)?m.content:[];const b=c.find(x=>x&&(x.name||x.toolName))||m||{};'
         'const n=String(b.name||b.toolName||m.toolName||"");const a=b.arguments||b.args||b.input||m.args||{};'
         'const p=String(a.path||a.file_path||a.query||a.url||a.command||"").slice(0,80);'
         'const w={web_search:"Searching the web",web_fetch:"Reading a web page",write:"Writing",edit:"Editing",read:"Reading",'
         'exec:"Running a command",memory_search:"Searching memory",memory_get:"Reading memory"}[n]||(n?"Using "+n:"");'
         'return w?"Working... last step: "+w+(p?" "+p:""):"Working..."}catch(e){return "Working..."}})')
EDITS = [
    ('r===a-1&&e.stream===null&&t.push({kind:"reading-indicator",key:"la-tools:"+e.sessionKey})',
     'r===a-1&&e.stream===null&&t.push({kind:"stream",key:"la-tools:"+e.sessionKey,text:' + LABEL + '(s[r]),'
     'startedAt:e.streamStartedAt??Date.now()})'),
]

files = [f for f in glob.glob(DIST + "/control-ui/assets/index-*.js")]
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
        print("  WARN: signature not found exactly once in " + fp + " (is patch 12 applied?): " + bad[0][:60], file=sys.stderr)
        continue
    open(fp + ".pre-working-status", "w", encoding="utf-8").write(src)
    for old, new in EDITS:
        src = src.replace(old, new, 1)
    open(fp, "w", encoding="utf-8").write(src + "\n/* " + MARKER + " */\n")
    print("  patched: " + fp); patched += 1
sys.exit(0 if patched else 1)
