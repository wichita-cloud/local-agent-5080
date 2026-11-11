// restore-copies: before the agent overwrites, edits, patches or deletes a file with write / edit / apply_patch, keep a
// copy of the file as it was in $STATE_DIR/restore-copies (outside the workspace, so the agent can't see or change them).
// The last 20 versions of each file are kept; bin/restore.sh lists and restores them.
// It must never stop the agent: before_tool_call fails closed (an error or a slow handler BLOCKS the tool call), so every
// step is caught, the work is small synchronous copies, and the hook never returns a verdict.
// Commands run with exec (rm, sed -i, > file) are not covered: only the three file tools.
import * as fs from "node:fs";
import * as path from "node:path";

const KEEP = 20;
const MAX_BYTES = 20 * 1024 * 1024;

type Mount = { container: string; host: string; label: string };

function hostPath(p: string, mounts: Mount[], workspace: string, sandboxed: boolean): { file: string; root: Mount } | null {
  if (!p) return null;
  const ws: Mount = { container: "/workspace", host: workspace, label: "workspace" };
  if (!p.startsWith("/")) return inside(path.resolve(workspace, p), ws);
  for (const m of [ws, ...mounts].sort((a, b) => b.container.length - a.container.length)) {
    if (p === m.container || p.startsWith(m.container + "/")) return inside(path.join(m.host, p.slice(m.container.length)), m);
  }
  if (sandboxed) return null;   // anywhere else inside the sandbox is the container's own (read-only) file system
  for (const m of [ws, ...mounts]) {
    if (p === m.host || p.startsWith(m.host + "/")) return { file: path.resolve(p), root: m };
  }
  return { file: path.resolve(p), root: { container: "/", host: "/", label: "other" } };
}

function inside(file: string, root: Mount) {
  return file === root.host || file.startsWith(root.host + "/") ? { file, root } : null;
}

function patchedFiles(input: string): string[] {
  const out: string[] = [];
  for (const line of String(input ?? "").split("\n")) {
    const m = /^\*\*\* (Update|Delete) File: (.+)$/.exec(line.trim());
    if (m) out.push(m[2].trim());
  }
  return out;
}

function keepCopy(file: string, root: Mount, store: string) {
  const st = fs.statSync(file, { throwIfNoEntry: false });
  if (!st || !st.isFile() || st.size > MAX_BYTES) return;
  const rel = path.relative(root.host, file) || path.basename(file);
  const dir = path.join(store, root.label, rel);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, ".origin"), file + "\n");   // where it goes back to (bin/restore.sh)
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  let name = stamp + path.extname(file);
  for (let i = 1; fs.existsSync(path.join(dir, name)); i++) name = `${stamp}-${i}${path.extname(file)}`;   // same millisecond
  fs.copyFileSync(file, path.join(dir, name));
  const versions = fs.readdirSync(dir).filter((n) => n !== ".origin").sort();
  for (const old of versions.slice(0, Math.max(0, versions.length - KEEP))) fs.rmSync(path.join(dir, old), { force: true });
}

export default {
  id: "restore-copies",
  name: "Restore copies",
  description: "Keeps the previous version of files the agent changes, so a bad edit can be undone.",
  register(api: any) {
    const cfg = api.pluginConfig ?? {};
    const store = String(cfg.store ?? "");
    const workspace = String(cfg.workspace ?? "");
    const mounts: Mount[] = Array.isArray(cfg.mounts) ? cfg.mounts : [];
    const sandboxed = Boolean(cfg.sandboxed);
    if (!store || !workspace) {
      api.logger?.warn?.("restore-copies: no store or workspace configured; not keeping copies");
      return;
    }
    api.on(
      "before_tool_call",
      (event: any) => {
        try {
          const params = event.params ?? {};
          const targets = event.toolName === "apply_patch" ? patchedFiles(params.input) : [String(params.path ?? params.file_path ?? "")];
          for (const t of targets) {
            try {
              const hp = hostPath(t, mounts, workspace, sandboxed);
              if (hp) keepCopy(hp.file, hp.root, store);
            } catch (e) {
              api.logger?.warn?.(`restore-copies: could not keep a copy of ${t}: ${e}`);
            }
          }
        } catch {
          // never block the agent's own work
        }
        return undefined;
      },
      { matcher: ["write", "edit", "apply_patch"], priority: 10 },
    );
  },
};
