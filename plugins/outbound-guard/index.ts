// outbound-guard: before web_search, web_fetch or a command that names a web address runs, ask services/outbound_guard.py
// what it carries. Something personal pauses the run and shows you the exact text; anything but your yes stops it.
// If the guard service doesn't answer, the call is blocked: a privacy check that can fail open is no check.
const GUARD = `http://127.0.0.1:${process.env.OUTBOUND_GUARD_PORT || "18892"}/check`;
const ASK_MS = 120_000;

function outgoing(tool: string, params: Record<string, unknown>): { text: string; url: boolean } | null {
  if (tool === "web_fetch") return { text: String(params.url ?? ""), url: true };
  if (tool === "web_search") return { text: String(params.query ?? JSON.stringify(params)), url: false };
  const command = String(params.command ?? "");
  return /https?:\/\/|\b(curl|wget|nc|ncat|scp|ftp)\b/.test(command) ? { text: command, url: false } : null;
}

export default {
  id: "outbound-guard",
  name: "Outbound guard",
  description: "Checks what the agent is about to send to the internet.",
  register(api: any) {
    api.on(
      "before_tool_call",
      async (event: any) => {
        const out = outgoing(event.toolName, event.params ?? {});
        if (!out) return;
        let verdict: { action?: string; found?: { label: string }[] };
        try {
          const r = await fetch(GUARD, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tool: event.toolName, ...out }),
            signal: AbortSignal.timeout(3000),
          });
          verdict = await r.json();
        } catch {
          return { block: true, blockReason: "The privacy check isn't answering, so nothing was sent. Tell the user: bin/services-up.sh starts it." };
        }
        if (verdict.action === "allow") return;
        if (verdict.action !== "ask") return { block: true, blockReason: "The privacy check gave no clear answer, so nothing was sent." };
        const found = (verdict.found ?? []).map((f) => f.label).join(", ");
        return {
          requireApproval: {
            title: "Send this to the internet?",
            description: `${found ? `Looks like it contains: ${found}.` : "You asked to approve everything that goes online."}\n\n${out.text.slice(0, 600)}`,
            severity: found ? "warning" : "info",
            timeoutMs: ASK_MS,
            allowedDecisions: ["allow-once", "deny"],
          },
        };
      },
      { matcher: ["web_search", "web_fetch", "exec"], priority: 100 },
    );
  },
};
