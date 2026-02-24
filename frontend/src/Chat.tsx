import React, { useEffect, useMemo, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";

type RenderableMsg = {
  id?: string;
  type?: string;
  content?: unknown;
};

type SliceProfile = "monitor" | "static" | "nvs-rate" | "nvs-cap" | "edf" | "all";
type CreateSliceProfile = "STATIC" | "NVS_RATE" | "NVS_CAPACITY" | "EDF";
type CreateSliceRow = {
  id: string;
  label: string;
  ue_sched_algo: string;
  pos_low: string;
  pos_high: string;
  mbps_rsvd: string;
  mbps_ref: string;
  pct_rsvd: string;
  deadline: string;
  guaranteed_prbs: string;
  max_replenish: string;
};
type MCPProfileMap = Record<string, Record<string, unknown>>;
type UavWaypoint = { x: number; y: number; z: number };
type EditableWaypointRow = { x: string; y: string; z: string };
type UavSimState = {
  uav?: Record<string, unknown>;
  utm?: {
    weather?: Record<string, unknown>;
    no_fly_zones?: Array<Record<string, unknown>>;
    regulations?: Record<string, unknown>;
    licenses?: Record<string, unknown>;
  };
};

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): Record<string, unknown> | null {
  return isObject(x) ? x : null;
}

function tryParseJson(text: string): unknown {
  const t = text.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

type ParsedTable = { headers: string[]; rows: string[][] };

function tryParseMarkdownTable(text: string): ParsedTable | null {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|") && line.endsWith("|"));
  if (lines.length < 3) return null;

  const sep = lines[1].replace(/\|/g, "").trim();
  if (!/^:?-{3,}:?$/.test(sep.replace(/\s+/g, "")) && !sep.includes("---")) return null;

  const toCells = (line: string) =>
    line
      .slice(1, -1)
      .split("|")
      .map((cell) => cell.trim());

  const headers = toCells(lines[0]);
  const rows = lines.slice(2).map(toCells).filter((r) => r.length === headers.length);
  if (headers.length === 0 || rows.length === 0) return null;
  return { headers, rows };
}

function extractToolsFromContent(content: unknown): Record<string, unknown>[] | null {
  const obj = asRecord(content);
  if (obj && Array.isArray(obj.tools)) {
    const tools = obj.tools.filter(isObject);
    if (tools.length > 0) return tools as Record<string, unknown>[];
  }
  if (Array.isArray(content)) {
    for (const item of content) {
      const rec = asRecord(item);
      if (rec && typeof rec.text === "string") {
        const parsed = tryParseJson(rec.text);
        const nested = extractToolsFromContent(parsed);
        if (nested) return nested;
      }
      const nested = extractToolsFromContent(item);
      if (nested) return nested;
    }
  }
  return null;
}

const thStyle: React.CSSProperties = {
  textAlign: "left",
  borderBottom: "1px solid #d6dae6",
  padding: "8px 6px",
  fontSize: 13,
  color: "#273043",
};

const tdStyle: React.CSSProperties = {
  borderBottom: "1px solid #ecedf2",
  verticalAlign: "top",
  padding: "8px 6px",
  fontSize: 13,
  color: "#273043",
};

function renderToolsTable(tools: Record<string, unknown>[]): React.ReactNode {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 6 }}>
        <thead>
          <tr>
            <th style={thStyle}>Tool</th>
            <th style={thStyle}>Description</th>
            <th style={thStyle}>Args</th>
          </tr>
        </thead>
        <tbody>
          {tools.map((tool, i) => (
            <tr key={`${String(tool.name ?? "tool")}-${i}`}>
              <td style={tdStyle}><code>{String(tool.name ?? "")}</code></td>
              <td style={tdStyle}>{String(tool.description ?? "")}</td>
              <td style={tdStyle}>
                <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {String(tool.args_schema ?? "")}
                </code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function toRenderableMsg(x: unknown): RenderableMsg {
  if (!isObject(x)) return {};
  return {
    id: typeof x.id === "string" ? x.id : undefined,
    type: typeof x.type === "string" ? x.type : undefined,
    content: "content" in x ? (x as { content?: unknown }).content : undefined,
  };
}

function extractNestedJsonPayload(content: unknown): unknown {
  const obj = asRecord(content);
  if (obj && Array.isArray(obj.result)) {
    for (const item of obj.result) {
      const rec = asRecord(item);
      if (rec && typeof rec.text === "string") {
        const parsed = tryParseJson(rec.text);
        if (parsed) return parsed;
      }
    }
  }
  if (Array.isArray(content)) {
    for (const item of content) {
      const rec = asRecord(item);
      if (rec && typeof rec.text === "string") {
        const parsed = tryParseJson(rec.text);
        if (parsed) return parsed;
      }
    }
  }
  return null;
}

function yesNoBadge(ok: unknown): React.ReactNode {
  const pass = ok === true;
  const fail = ok === false;
  return (
    <span
      style={{
        display: "inline-block",
        borderRadius: 999,
        padding: "2px 8px",
        fontSize: 12,
        fontWeight: 700,
        background: pass ? "#ecfdf3" : fail ? "#fef3f2" : "#f2f4f7",
        color: pass ? "#027a48" : fail ? "#b42318" : "#475467",
        border: `1px solid ${pass ? "#abefc6" : fail ? "#fecdca" : "#d0d5dd"}`,
      }}
    >
      {pass ? "PASS" : fail ? "FAIL" : String(ok)}
    </span>
  );
}

function renderSliceMonitorResult(payload: unknown): React.ReactNode | null {
  const obj = asRecord(payload);
  if (!obj) return null;
  const checks = asRecord(obj.checks);
  const run = asRecord(obj.run);
  if (!checks || !run) return null;
  if (!Array.isArray(checks.tail_lines)) return null;

  const tailLines = (checks.tail_lines as unknown[])
    .filter((x): x is string => typeof x === "string")
    .slice(-8);
  const markers = Array.isArray(checks.profile_marker_hits)
    ? (checks.profile_marker_hits as unknown[]).filter((x): x is string => typeof x === "string")
    : [];
  const errors = Array.isArray(checks.error_hits)
    ? (checks.error_hits as unknown[]).filter((x): x is string => typeof x === "string")
    : [];

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div
        style={{
          border: "1px solid #e7ebf3",
          borderRadius: 10,
          background: "#fcfcfd",
          padding: 10,
        }}
      >
        <div style={{ fontWeight: 700, color: "#0f172a", marginBottom: 8 }}>Slice Monitor Check</div>
        <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 6, fontSize: 13 }}>
          <div style={{ color: "#475467" }}>Overall</div><div>{yesNoBadge(obj.verified ?? checks.ok)}</div>
          <div style={{ color: "#475467" }}>Setup</div><div>{yesNoBadge(checks.setup_ok)}</div>
          <div style={{ color: "#475467" }}>Subscription</div><div>{yesNoBadge(checks.subscription_ok)}</div>
          <div style={{ color: "#475467" }}>Run status</div><div><code>{String(run.status ?? "")}</code></div>
          <div style={{ color: "#475467" }}>Profile</div><div><code>{String(run.profile ?? "")}</code></div>
          <div style={{ color: "#475467" }}>Run ID</div><div><code>{String(run.run_id ?? "")}</code></div>
        </div>
      </div>

      {markers.length > 0 ? (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10 }}>
          <div style={{ fontWeight: 700, marginBottom: 6, color: "#0f172a" }}>Markers Found</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {markers.map((m, i) => (
              <span key={`${m}-${i}`} style={{ fontSize: 12, borderRadius: 999, padding: "2px 8px", background: "#eef4ff", border: "1px solid #c7d7fe" }}>
                {m}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10 }}>
        <div style={{ fontWeight: 700, marginBottom: 6, color: "#0f172a" }}>
          Evidence Log Lines {errors.length > 0 ? "(Errors detected)" : ""}
        </div>
        {errors.length > 0 ? (
          <div style={{ color: "#b42318", fontSize: 12, marginBottom: 6 }}>
            Error markers: {errors.join(", ")}
          </div>
        ) : null}
        <div style={{ display: "grid", gap: 4 }}>
          {tailLines.map((line, i) => (
            <code
              key={`tail-${i}`}
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 12,
                background: "#f8fafc",
                border: "1px solid #eaecf0",
                borderRadius: 6,
                padding: "4px 6px",
              }}
            >
              {line}
            </code>
          ))}
        </div>
      </div>
    </div>
  );
}

function renderToolErrorCard(content: unknown): React.ReactNode | null {
  const obj = asRecord(content);
  if (!obj) return null;
  if (obj.status !== "error" || typeof obj.error !== "string") return null;

  const guidance = asRecord(obj.guidance);
  const tool = typeof obj.tool === "string" ? obj.tool : "tool";
  const isStopMissingTarget =
    (tool === "run_stop" || tool === "tool") &&
    typeof obj.error === "string" &&
    obj.error.toLowerCase().includes("provide run_id or suite");

  return (
    <div style={{ border: "1px solid #fecdca", background: "#fef3f2", borderRadius: 10, padding: 10, display: "grid", gap: 8 }}>
      <div style={{ fontWeight: 700, color: "#b42318" }}>
        {tool} failed
      </div>
      <div style={{ fontSize: 13, color: "#7a271a", whiteSpace: "pre-wrap" }}>{obj.error}</div>
      {isStopMissingTarget ? (
        <div style={{ border: "1px solid #fda29b", background: "#fff", borderRadius: 8, padding: 8, fontSize: 12, color: "#7a271a", lineHeight: 1.35 }}>
          Stop needs a target. Ask for one of these:
          <div><code>Stop the active slice run</code></div>
          <div><code>Run mcp_run_stop with suite=\"slice\"</code></div>
          <div><code>List active runs and stop by run_id</code></div>
        </div>
      ) : null}
      {guidance ? (
        <div style={{ border: "1px solid #fda29b", background: "#fff", borderRadius: 8, padding: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#7a271a", marginBottom: 4 }}>Guidance</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12 }}>
            {JSON.stringify(guidance, null, 2)}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function renderMcpHealthCard(payload: unknown): React.ReactNode | null {
  const obj = asRecord(payload);
  if (!obj) return null;
  if (obj.status !== "success" || typeof obj.server !== "string") return null;
  if (!("active" in obj) || !("known_runs" in obj)) return null;

  const active = asRecord(obj.active) ?? {};
  const activeSuites = Object.keys(active);
  const pythonCmd = Array.isArray(obj.python_cmd) ? (obj.python_cmd as unknown[]).filter((x): x is string => typeof x === "string") : [];

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ border: "1px solid #d1fadf", background: "#f6fef9", borderRadius: 10, padding: 10 }}>
        <div style={{ fontWeight: 700, color: "#027a48", marginBottom: 8 }}>MCP Server Health</div>
        <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: 6, fontSize: 13 }}>
          <div style={{ color: "#475467" }}>Status</div><div>{yesNoBadge(true)}</div>
          <div style={{ color: "#475467" }}>Server</div><div><code>{String(obj.server)}</code></div>
          <div style={{ color: "#475467" }}>Active runs</div><div>{activeSuites.length}</div>
          <div style={{ color: "#475467" }}>Known runs</div><div>{String(obj.known_runs ?? 0)}</div>
          {typeof obj.cwd === "string" ? (
            <>
              <div style={{ color: "#475467" }}>Working dir</div>
              <div><code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{obj.cwd}</code></div>
            </>
          ) : null}
        </div>
      </div>

      {activeSuites.length > 0 ? (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10 }}>
          <div style={{ fontWeight: 700, marginBottom: 6, color: "#0f172a" }}>Active Suites</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {activeSuites.map((suite) => (
              <span key={suite} style={{ fontSize: 12, borderRadius: 999, padding: "2px 8px", background: "#eef4ff", border: "1px solid #c7d7fe" }}>
                {suite}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10, fontSize: 13, color: "#475467" }}>
          No active suite runs. The MCP server is idle and ready.
        </div>
      )}

      {pythonCmd.length > 0 ? (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10 }}>
          <div style={{ fontWeight: 700, marginBottom: 6, color: "#0f172a" }}>Python Command</div>
          <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12 }}>
            {pythonCmd.join(" ")}
          </code>
        </div>
      ) : null}
    </div>
  );
}

function renderRunsListCard(payload: unknown): React.ReactNode | null {
  const obj = asRecord(payload);
  if (!obj) return null;
  if (obj.status !== "success" || !Array.isArray(obj.runs) || typeof obj.count !== "number") return null;

  const runs = (obj.runs as unknown[]).filter(isObject) as Record<string, unknown>[];
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, background: "#fcfcfd", padding: 10 }}>
        <div style={{ fontWeight: 700, color: "#0f172a", marginBottom: 8 }}>Suite Runs</div>
        <div style={{ fontSize: 13, color: "#475467" }}>
          {runs.length === 0 ? "No runs found." : `${runs.length} run${runs.length === 1 ? "" : "s"} found.`}
        </div>
      </div>

      {runs.length === 0 ? (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10, fontSize: 13, color: "#475467", lineHeight: 1.35 }}>
          Nothing is currently active. If you meant to stop a suite, it may have already finished.
          Try starting a new slice run, or ask the agent to list all runs (not only active).
        </div>
      ) : (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={thStyle}>Run ID</th>
                <th style={thStyle}>Suite</th>
                <th style={thStyle}>Profile</th>
                <th style={thStyle}>Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run, i) => (
                <tr key={`${String(run.run_id ?? "run")}-${i}`}>
                  <td style={tdStyle}><code>{String(run.run_id ?? "")}</code></td>
                  <td style={tdStyle}>{String(run.suite ?? "")}</td>
                  <td style={tdStyle}>{String(run.profile ?? "")}</td>
                  <td style={tdStyle}><code>{String(run.status ?? "")}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function renderSliceVerifyResult(payload: unknown): React.ReactNode | null {
  const obj = asRecord(payload);
  if (!obj) return null;
  const run = asRecord(obj.run);
  if (!run) return null;

  const checks = asRecord(obj.checks) ?? asRecord(obj.verify) ?? asRecord(obj.verification);
  const profile = typeof run.profile === "string" ? run.profile : "";
  if (!profile || profile === "monitor") return null;

  const verified = obj.verified ?? obj.ok ?? checks?.ok;
  const tailCandidates = [
    checks?.tail_lines,
    checks?.verify_tail_lines,
    checks?.log_tail,
    obj.tail_lines,
  ];
  let tailLines: string[] = [];
  for (const candidate of tailCandidates) {
    if (Array.isArray(candidate)) {
      tailLines = (candidate as unknown[]).filter((x): x is string => typeof x === "string").slice(-8);
      if (tailLines.length) break;
    }
  }

  const statusPairs: Array<[string, unknown]> = [
    ["Verified", verified],
    ["Run status", run.status],
    ["Profile", run.profile],
    ["Run ID", run.run_id],
    ["Return code", run.returncode],
  ];

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, background: "#fcfcfd", padding: 10 }}>
        <div style={{ fontWeight: 700, color: "#0f172a", marginBottom: 8 }}>Slice Apply + Verify</div>
        <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 6, fontSize: 13 }}>
          {statusPairs.map(([label, value]) => (
            <React.Fragment key={label}>
              <div style={{ color: "#475467" }}>{label}</div>
              <div>{label === "Verified" ? yesNoBadge(value) : <code>{String(value ?? "")}</code>}</div>
            </React.Fragment>
          ))}
        </div>
      </div>

      {checks ? (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10 }}>
          <div style={{ fontWeight: 700, marginBottom: 6, color: "#0f172a" }}>Verification Details</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12 }}>
            {JSON.stringify(checks, null, 2)}
          </pre>
        </div>
      ) : null}

      {tailLines.length > 0 ? (
        <div style={{ border: "1px solid #e7ebf3", borderRadius: 10, padding: 10 }}>
          <div style={{ fontWeight: 700, marginBottom: 6, color: "#0f172a" }}>Evidence Log Lines</div>
          <div style={{ display: "grid", gap: 4 }}>
            {tailLines.map((line, i) => (
              <code
                key={`verify-tail-${i}`}
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontSize: 12,
                  background: "#f8fafc",
                  border: "1px solid #eaecf0",
                  borderRadius: 6,
                  padding: "4px 6px",
                }}
              >
                {line}
              </code>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function renderContent(content: unknown): React.ReactNode {
  if (content == null) return null;
  const tools = extractToolsFromContent(content);
  if (tools) return renderToolsTable(tools);

  const toolErrorCard = renderToolErrorCard(content);
  if (toolErrorCard) return toolErrorCard;

  const healthCard = renderMcpHealthCard(content);
  if (healthCard) return healthCard;

  const runsListCard = renderRunsListCard(content);
  if (runsListCard) return runsListCard;

  const nestedPayload = extractNestedJsonPayload(content);
  if (nestedPayload) {
    const monitorCard = renderSliceMonitorResult(nestedPayload);
    if (monitorCard) return monitorCard;
    const verifyCard = renderSliceVerifyResult(nestedPayload);
    if (verifyCard) return verifyCard;
    const nestedHealthCard = renderMcpHealthCard(nestedPayload);
    if (nestedHealthCard) return nestedHealthCard;
    const nestedRunsListCard = renderRunsListCard(nestedPayload);
    if (nestedRunsListCard) return nestedRunsListCard;
    return renderContent(nestedPayload);
  }

  if (typeof content === "string") {
    const parsed = tryParseJson(content);
    if (parsed) return renderContent(parsed);
    const mdTable = tryParseMarkdownTable(content);
    if (mdTable) {
      return (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {mdTable.headers.map((h, i) => (
                  <th key={`${h}-${i}`} style={thStyle}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {mdTable.rows.map((row, r) => (
                <tr key={`row-${r}`}>
                  {row.map((cell, c) => (
                    <td key={`cell-${r}-${c}`} style={tdStyle}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    return <div style={{ whiteSpace: "pre-wrap" }}>{content}</div>;
  }

  if (typeof content === "number" || typeof content === "boolean") return String(content);
  try {
    return <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{JSON.stringify(content, null, 2)}</pre>;
  } catch {
    return String(content);
  }
}

function chipStyle(active = false): React.CSSProperties {
  return {
    borderRadius: 999,
    border: active ? "1px solid #0f766e" : "1px solid #cfd6e6",
    background: active ? "#e6fffb" : "#fff",
    color: "#1f2937",
    padding: "6px 10px",
    fontSize: 12,
    cursor: "pointer",
  };
}

const DEFAULT_SIM_ROUTE: UavWaypoint[] = [
  { x: 0, y: 0, z: 0 },
  { x: 100, y: 50, z: 40 },
  { x: 220, y: 120, z: 55 },
  { x: 280, y: 180, z: 45 },
];

function waypointToRow(wp: UavWaypoint): EditableWaypointRow {
  return { x: String(wp.x), y: String(wp.y), z: String(wp.z) };
}

function isoUtcToLocalInput(iso: string): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
}

function localInputToIsoUtc(value: string): string | null {
  if (!value.trim()) return null;
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return null;
  return dt.toISOString();
}

function defaultApprovalWindowLocal(): { start: string; end: string } {
  const now = new Date(Date.now() + 2 * 60 * 1000);
  const end = new Date(now.getTime() + 20 * 60 * 1000);
  return {
    start: isoUtcToLocalInput(now.toISOString()),
    end: isoUtcToLocalInput(end.toISOString()),
  };
}

function renderUavOverlay(state: UavSimState | null): React.ReactNode {
  const uav = asRecord(state?.uav);
  const utm = asRecord(state?.utm);
  const route = Array.isArray(uav?.waypoints)
    ? (uav!.waypoints as unknown[]).filter(isObject).map((w) => ({
        x: Number((w as Record<string, unknown>).x ?? 0),
        y: Number((w as Record<string, unknown>).y ?? 0),
        z: Number((w as Record<string, unknown>).z ?? 0),
      }))
    : [];
  const nfz = Array.isArray(utm?.no_fly_zones) ? (utm!.no_fly_zones as unknown[]).filter(isObject) : [];
  const pos = asRecord(uav?.position);

  const width = 260;
  const height = 180;
  const pad = 12;
  const xs = route.map((p) => p.x).concat(nfz.map((z) => Number((z as Record<string, unknown>).cx ?? 0)));
  const ys = route.map((p) => p.y).concat(nfz.map((z) => Number((z as Record<string, unknown>).cy ?? 0)));
  const minX = xs.length ? Math.min(...xs, 0) : 0;
  const maxX = xs.length ? Math.max(...xs, 300) : 300;
  const minY = ys.length ? Math.min(...ys, 0) : 0;
  const maxY = ys.length ? Math.max(...ys, 200) : 200;
  const sx = (x: number) => pad + ((x - minX) / Math.max(1, maxX - minX)) * (width - pad * 2);
  const sy = (y: number) => height - pad - ((y - minY) / Math.max(1, maxY - minY)) * (height - pad * 2);
  const routePath = route.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", border: "1px solid #e5e7eb", borderRadius: 8, background: "#f8fbff" }}>
      <rect x={0} y={0} width={width} height={height} fill="url(#gridBg)" />
      <defs>
        <pattern id="gridBg" width="16" height="16" patternUnits="userSpaceOnUse">
          <rect width="16" height="16" fill="#f8fbff" />
          <path d="M 16 0 L 0 0 0 16" fill="none" stroke="#e8eefc" strokeWidth="1" />
        </pattern>
      </defs>
      {nfz.map((z, i) => {
        const zr = Number((z as Record<string, unknown>).radius_m ?? 0);
        const cx = Number((z as Record<string, unknown>).cx ?? 0);
        const cy = Number((z as Record<string, unknown>).cy ?? 0);
        const px = sx(cx);
        const py = sy(cy);
        const pr = (zr / Math.max(1, maxX - minX)) * (width - pad * 2);
        return (
          <g key={`nfz-${i}`}>
            <circle cx={px} cy={py} r={Math.max(6, pr)} fill="rgba(220,38,38,0.12)" stroke="#dc2626" strokeDasharray="4 3" />
            <text x={px + 4} y={py - 6} fontSize="9" fill="#991b1b">{String((z as Record<string, unknown>).zone_id ?? "nfz")}</text>
          </g>
        );
      })}
      {route.length > 0 ? <polyline points={routePath} fill="none" stroke="#2563eb" strokeWidth="2" /> : null}
      {route.map((p, i) => (
        <circle key={`wp-${i}`} cx={sx(p.x)} cy={sy(p.y)} r={3} fill={i === 0 ? "#16a34a" : "#1d4ed8"} />
      ))}
      {pos ? (
        <circle
          cx={sx(Number(pos.x ?? 0))}
          cy={sy(Number(pos.y ?? 0))}
          r={5}
          fill="#f59e0b"
          stroke="#92400e"
          strokeWidth="1.5"
        />
      ) : null}
    </svg>
  );
}

function fieldErrorStyle(): React.CSSProperties {
  return { color: "#b42318", fontSize: 12, marginTop: 4 };
}

const compactInputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  minWidth: 0,
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
};

const compactSliceConfigFieldStyle: React.CSSProperties = {
  ...compactInputStyle,
  marginTop: 4,
  padding: "4px 8px",
  fontSize: 12,
  height: 28,
};

const sidebarSectionCardStyle: React.CSSProperties = {
  background: "#fcfcfd",
  border: "1px solid #eaecf0",
  borderRadius: 10,
  padding: 10,
};

const tinyLabelStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: "#667085",
  letterSpacing: 0.2,
  textTransform: "uppercase",
};

export function Chat(
  {
    showSimulatorPanel = false,
    compactOranUi = false,
  }: { showSimulatorPanel?: boolean; compactOranUi?: boolean } = {},
) {
  const initialSliceInputMode = (() => {
    try {
      const v = window.localStorage.getItem("oran.sliceInputMode");
      return v === "custom" ? "custom" : "suites";
    } catch {
      return "suites";
    }
  })() as "suites" | "custom";
  const initialCustomPanelOpen = (() => {
    try {
      return window.localStorage.getItem("oran.customPanelOpen") === "1";
    } catch {
      return false;
    }
  })();

  const [input, setInput] = useState("");
  const [sliceProfile, setSliceProfile] = useState<SliceProfile>("monitor");
  const [sliceDuration, setSliceDuration] = useState("30");
  const [sliceVerbose, setSliceVerbose] = useState(true);
  const [sliceAssocDlId, setSliceAssocDlId] = useState("2");
  const [sliceMode, setSliceMode] = useState<"monitor" | "apply">("monitor");
  const [createProfile, setCreateProfile] = useState<CreateSliceProfile>("STATIC");
  const [sliceInputMode, setSliceInputMode] = useState<"suites" | "custom">(initialSliceInputMode);
  const [customPanelOpen, setCustomPanelOpen] = useState(initialCustomPanelOpen);
  const [loadingSeconds, setLoadingSeconds] = useState(0);
  const [mcpApiBase, setMcpApiBase] = useState("http://127.0.0.1:8010");
  const [mcpTransport, setMcpTransport] = useState("stdio");
  const [mcpProfile, setMcpProfile] = useState("suites-stdio");
  const [mcpServerArgs, setMcpServerArgs] = useState("");
  const [mcpHttpUrl, setMcpHttpUrl] = useState("http://127.0.0.1:8000/mcp");
  const [mcpProfiles, setMcpProfiles] = useState<MCPProfileMap>({});
  const [mcpConfigBusy, setMcpConfigBusy] = useState(false);
  const [mcpConfigMsg, setMcpConfigMsg] = useState("");
  const [uavApiBase, setUavApiBase] = useState("http://127.0.0.1:8020");
  const [uavApiBusy, setUavApiBusy] = useState(false);
  const [uavApiMsg, setUavApiMsg] = useState("");
  const [simUavId, setSimUavId] = useState("uav-1");
  const [simRouteId, setSimRouteId] = useState("demo-route");
  const [simAirspace, setSimAirspace] = useState("sector-A3");
  const [simTicks, setSimTicks] = useState("1");
  const [simOperatorLicenseId, setSimOperatorLicenseId] = useState("op-001");
  const [simLicenseClass, setSimLicenseClass] = useState("VLOS");
  const approvalWindowDefaults = defaultApprovalWindowLocal();
  const [simPlannedStartAt, setSimPlannedStartAt] = useState(approvalWindowDefaults.start);
  const [simPlannedEndAt, setSimPlannedEndAt] = useState(approvalWindowDefaults.end);
  const [simRequestedSpeedMps, setSimRequestedSpeedMps] = useState("12");
  const [simRouteRows, setSimRouteRows] = useState<EditableWaypointRow[]>(DEFAULT_SIM_ROUTE.map(waypointToRow));
  const [simRegLicenseId, setSimRegLicenseId] = useState("op-001");
  const [simRegLicenseClass, setSimRegLicenseClass] = useState("VLOS");
  const [simRegLicenseExpiry, setSimRegLicenseExpiry] = useState("2099-01-01T00:00");
  const [simRegLicenseActive, setSimRegLicenseActive] = useState(true);
  const [simWind, setSimWind] = useState(8);
  const [simVisibility, setSimVisibility] = useState(10);
  const [simPrecip, setSimPrecip] = useState(0);
  const [simStorm, setSimStorm] = useState(false);
  const [uavSimState, setUavSimState] = useState<UavSimState | null>(null);
  const [createRows, setCreateRows] = useState<CreateSliceRow[]>([
    {
      id: "0",
      label: "s1",
      ue_sched_algo: "PF",
      pos_low: "0",
      pos_high: "2",
      mbps_rsvd: "60",
      mbps_ref: "120",
      pct_rsvd: "0.5",
      deadline: "10",
      guaranteed_prbs: "20",
      max_replenish: "0",
    },
  ]);

  const stream = useStream({
    assistantId: "agent",
    apiUrl: "http://127.0.0.1:2024",
  });

  useEffect(() => {
    if (!stream.isLoading) {
      setLoadingSeconds(0);
      return;
    }
    setLoadingSeconds(0);
    const timer = window.setInterval(() => {
      setLoadingSeconds((s) => s + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [stream.isLoading]);

  useEffect(() => {
    setCustomPanelOpen(sliceInputMode === "custom");
  }, [sliceInputMode]);

  useEffect(() => {
    if (!compactOranUi) return;
    if (sliceInputMode !== "suites") setSliceInputMode("suites");
    if (customPanelOpen) setCustomPanelOpen(false);
  }, [compactOranUi, sliceInputMode, customPanelOpen]);

  useEffect(() => {
    try {
      window.localStorage.setItem("oran.sliceInputMode", sliceInputMode);
    } catch {
      // ignore storage failures
    }
  }, [sliceInputMode]);

  useEffect(() => {
    try {
      window.localStorage.setItem("oran.customPanelOpen", customPanelOpen ? "1" : "0");
    } catch {
      // ignore storage failures
    }
  }, [customPanelOpen]);

  const loadMcpConfig = async () => {
    setMcpConfigBusy(true);
    setMcpConfigMsg("");
    try {
      const res = await fetch(`${mcpApiBase}/api/mcp/config`);
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String((asRecord(data)?.detail ?? "Request failed")));
      const cfg = asRecord(data.config);
      const profiles = asRecord(data.profiles);
      if (cfg) {
        if (typeof cfg.transport === "string") setMcpTransport(cfg.transport);
        if (typeof cfg.server_args === "string") setMcpServerArgs(cfg.server_args);
        if (typeof cfg.http_url === "string") setMcpHttpUrl(cfg.http_url);
      }
      if (profiles) setMcpProfiles(profiles as MCPProfileMap);
      setMcpConfigMsg("Loaded MCP config");
    } catch (e) {
      setMcpConfigMsg(`Load failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setMcpConfigBusy(false);
    }
  };

  useEffect(() => {
    void loadMcpConfig();
  }, []);

  const loadUavSimState = async () => {
    setUavApiBusy(true);
    setUavApiMsg("");
    try {
      const res = await fetch(`${uavApiBase}/api/uav/sim/state?uav_id=${encodeURIComponent(simUavId)}`);
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String((asRecord(data)?.detail ?? "Request failed")));
      setUavSimState(data as UavSimState);
      const utm = asRecord((data as Record<string, unknown>).utm);
      const weather = asRecord(utm?.weather);
      if (weather) {
        if (typeof weather.wind_mps === "number") setSimWind(weather.wind_mps);
        if (typeof weather.visibility_km === "number") setSimVisibility(weather.visibility_km);
        if (typeof weather.precip_mmph === "number") setSimPrecip(weather.precip_mmph);
        if (typeof weather.storm_alert === "boolean") setSimStorm(weather.storm_alert);
      }
      setUavApiMsg("Loaded UAV/UTM simulator state");
    } catch (e) {
      setUavApiMsg(`Simulator load failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setUavApiBusy(false);
    }
  };

  useEffect(() => {
    void loadUavSimState();
  }, []);

  useEffect(() => {
    const licenses = asRecord(uavSimState?.utm?.licenses);
    if (!licenses) return;
    const existing = asRecord(licenses[simOperatorLicenseId]);
    if (!existing) return;
    if (typeof existing.license_class === "string") setSimRegLicenseClass(existing.license_class);
    if (typeof existing.expires_at === "string") {
      const localValue = isoUtcToLocalInput(existing.expires_at);
      if (localValue) setSimRegLicenseExpiry(localValue);
    }
    if (typeof existing.active === "boolean") setSimRegLicenseActive(existing.active);
    setSimRegLicenseId(simOperatorLicenseId);
  }, [simOperatorLicenseId, uavSimState]);

  const sendText = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || stream.isLoading) return;
    await stream.submit({ messages: [{ type: "human", content: trimmed }] });
  };

  const send = async () => {
    await sendText(input);
    setInput("");
  };

  const applyMcpProfile = async () => {
    setMcpConfigBusy(true);
    setMcpConfigMsg("");
    try {
      const res = await fetch(`${mcpApiBase}/api/mcp/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: mcpProfile }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(String((asRecord(data)?.detail ?? "Request failed")));
      setMcpConfigMsg(`Applied profile: ${mcpProfile}`);
      await loadMcpConfig();
    } catch (e) {
      setMcpConfigMsg(`Apply profile failed: ${e instanceof Error ? e.message : String(e)}`);
      setMcpConfigBusy(false);
    }
  };

  const saveMcpConfig = async () => {
    setMcpConfigBusy(true);
    setMcpConfigMsg("");
    try {
      const payload = {
        transport: mcpTransport,
        server_args: mcpServerArgs,
        http_url: mcpHttpUrl,
      };
      const res = await fetch(`${mcpApiBase}/api/mcp/config`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(String((asRecord(data)?.detail ?? "Request failed")));
      setMcpConfigMsg("Saved MCP runtime config");
      await loadMcpConfig();
    } catch (e) {
      setMcpConfigMsg(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
      setMcpConfigBusy(false);
    }
  };

  const postUavApi = async (path: string, body?: unknown, successMsg?: string) => {
    setUavApiBusy(true);
    setUavApiMsg("");
    try {
      const res = await fetch(`${uavApiBase}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body == null ? undefined : JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(String((asRecord(data)?.detail ?? "Request failed")));
      setUavApiMsg(successMsg ?? "OK");
      await loadUavSimState();
      return data;
    } catch (e) {
      setUavApiMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
      setUavApiBusy(false);
      return null;
    }
  };

  const planSimRoute = async () => {
    if (simRouteValidation.errors.length > 0) {
      setUavApiMsg(`Route validation failed: ${simRouteValidation.errors[0]}`);
      return;
    }
    await postUavApi(
      "/api/uav/sim/plan",
      { uav_id: simUavId, route_id: simRouteId, waypoints: simRouteValidation.waypoints },
      "Route planned",
    );
  };

  const submitGeofence = async () => {
    await postUavApi(`/api/uav/sim/geofence-submit?uav_id=${encodeURIComponent(simUavId)}&airspace_segment=${encodeURIComponent(simAirspace)}`, undefined, "Route submitted to UTM geofence check");
  };

  const requestApproval = async () => {
    const requestedSpeed = Number.parseFloat(simRequestedSpeedMps);
    const plannedStartIso = localInputToIsoUtc(simPlannedStartAt);
    const plannedEndIso = localInputToIsoUtc(simPlannedEndAt);
    if (!Number.isFinite(requestedSpeed) || requestedSpeed <= 0) {
      setUavApiMsg("Action failed: requested speed must be a positive number");
      return;
    }
    if (simPlannedStartAt && !plannedStartIso) {
      setUavApiMsg("Action failed: planned start time is invalid");
      return;
    }
    if (simPlannedEndAt && !plannedEndIso) {
      setUavApiMsg("Action failed: planned end time is invalid");
      return;
    }
    await postUavApi(
      "/api/uav/sim/request-approval",
      {
        uav_id: simUavId,
        airspace_segment: simAirspace,
        operator_license_id: simOperatorLicenseId,
        required_license_class: simLicenseClass,
        requested_speed_mps: requestedSpeed,
        planned_start_at: plannedStartIso,
        planned_end_at: plannedEndIso,
      },
      "UTM approval requested",
    );
  };

  const launchSim = async () => {
    await postUavApi(`/api/uav/sim/launch?uav_id=${encodeURIComponent(simUavId)}`, undefined, "Launch command sent");
  };

  const stepSim = async () => {
    const ticks = Math.max(1, Number.parseInt(simTicks || "1", 10) || 1);
    await postUavApi("/api/uav/sim/step", { uav_id: simUavId, ticks }, `Stepped simulator by ${ticks}`);
  };

  const saveWeather = async () => {
    await postUavApi(
      "/api/utm/weather",
      {
        airspace_segment: simAirspace,
        wind_mps: simWind,
        visibility_km: simVisibility,
        precip_mmph: simPrecip,
        storm_alert: simStorm,
      },
      "UTM weather updated",
    );
  };

  const registerOperatorLicense = async () => {
    if (!simRegLicenseId.trim()) {
      setUavApiMsg("Action failed: operator license ID is required");
      return;
    }
    const expiresIso = localInputToIsoUtc(simRegLicenseExpiry);
    if (simRegLicenseExpiry && !expiresIso) {
      setUavApiMsg("Action failed: license expiry time is invalid");
      return;
    }
    await postUavApi(
      "/api/utm/license",
      {
        operator_license_id: simRegLicenseId.trim(),
        license_class: simRegLicenseClass,
        expires_at: expiresIso ?? "2099-01-01T00:00:00Z",
        active: simRegLicenseActive,
      },
      "Operator license registered",
    );
  };

  const simRouteValidation = useMemo(() => {
    const rowErrors: string[][] = simRouteRows.map(() => []);
    const waypoints: UavWaypoint[] = [];
    const errors: string[] = [];
    const maxAlt =
      Number(asRecord(uavSimState?.utm?.regulations)?.max_altitude_m) > 0
        ? Number(asRecord(uavSimState?.utm?.regulations)?.max_altitude_m)
        : 120;

    simRouteRows.forEach((row, idx) => {
      const x = Number(row.x);
      const y = Number(row.y);
      const z = Number(row.z);
      if (!Number.isFinite(x)) rowErrors[idx].push("x");
      if (!Number.isFinite(y)) rowErrors[idx].push("y");
      if (!Number.isFinite(z)) rowErrors[idx].push("z");
      if (Number.isFinite(z) && z < 0) rowErrors[idx].push("z<0");
      if (Number.isFinite(z) && z > maxAlt) rowErrors[idx].push(`z>${maxAlt}`);
      if (rowErrors[idx].length === 0) waypoints.push({ x, y, z });
    });

    if (simRouteRows.length < 2) errors.push("Add at least 2 waypoints.");
    rowErrors.forEach((rowErr, idx) => {
      if (rowErr.length > 0) errors.push(`Waypoint ${idx + 1}: ${rowErr.join(", ")}`);
    });
    for (let i = 1; i < waypoints.length; i += 1) {
      const a = waypoints[i - 1];
      const b = waypoints[i];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dz = b.z - a.z;
      if (Math.sqrt(dx * dx + dy * dy + dz * dz) > 5000) {
        errors.push(`Waypoint ${i} -> ${i + 1}: segment jump too large (>5000m)`);
        break;
      }
    }

    return { rowErrors, waypoints, errors, maxAlt };
  }, [simRouteRows, uavSimState]);

  const sliceValidation = useMemo(() => {
    const errors: string[] = [];
    const duration = Number(sliceDuration);
    if (!Number.isInteger(duration)) {
      errors.push("Duration must be an integer (seconds).");
    } else {
      const [minD, maxD] = sliceMode === "monitor" ? [5, 600] : [1, 3600];
      if (duration < minD || duration > maxD) {
        errors.push(`Duration must be between ${minD} and ${maxD} seconds.`);
      }
    }

    const assocNeeded = sliceMode === "apply" && sliceProfile === "all";
    if (assocNeeded) {
      const assoc = Number(sliceAssocDlId);
      if (!Number.isInteger(assoc)) {
        errors.push("assoc_dl_id must be an integer.");
      } else if (assoc < 0 || assoc > 255) {
        errors.push("assoc_dl_id must be between 0 and 255.");
      }
    }
    return errors;
  }, [sliceDuration, sliceMode, sliceProfile, sliceAssocDlId]);

  const createSliceValidation = useMemo(() => {
    const errors: string[] = [];
    const warnings: string[] = [];
    const seenIds = new Set<number>();
    const staticRanges: Array<{ lo: number; hi: number; idx: number }> = [];
    let capacityTotal = 0;

    if (createRows.length === 0) errors.push("Add at least one slice row.");

    createRows.forEach((row, idx) => {
      const prefix = `Slice ${idx + 1}`;
      const id = Number(row.id);
      if (!Number.isInteger(id) || id < 0) {
        errors.push(`${prefix}: id must be an integer >= 0.`);
      } else if (seenIds.has(id)) {
        errors.push(`${prefix}: id ${id} is duplicated.`);
      } else {
        seenIds.add(id);
      }

      if (!row.label.trim()) errors.push(`${prefix}: label is required.`);

      if (createProfile === "STATIC") {
        const lo = Number(row.pos_low);
        const hi = Number(row.pos_high);
        if (!Number.isInteger(lo) || lo < 0) errors.push(`${prefix}: pos_low must be an integer >= 0.`);
        if (!Number.isInteger(hi) || hi < 0) errors.push(`${prefix}: pos_high must be an integer >= 0.`);
        if (Number.isInteger(lo) && Number.isInteger(hi)) {
          if (lo > hi) errors.push(`${prefix}: pos_low must be <= pos_high.`);
          staticRanges.push({ lo, hi, idx });
        }
      }

      if (createProfile === "NVS_RATE") {
        const rsvd = Number(row.mbps_rsvd);
        const ref = Number(row.mbps_ref);
        if (!Number.isInteger(rsvd) || rsvd <= 0) errors.push(`${prefix}: mbps_rsvd must be an integer > 0.`);
        if (!Number.isInteger(ref) || ref <= 0) errors.push(`${prefix}: mbps_ref must be an integer > 0.`);
        if (Number.isInteger(rsvd) && Number.isInteger(ref) && rsvd > ref) {
          errors.push(`${prefix}: mbps_rsvd must be <= mbps_ref.`);
        }
      }

      if (createProfile === "NVS_CAPACITY") {
        const pct = Number(row.pct_rsvd);
        if (!Number.isFinite(pct) || pct <= 0 || pct > 1) {
          errors.push(`${prefix}: pct_rsvd must be in (0, 1].`);
        } else {
          capacityTotal += pct;
        }
      }

      if (createProfile === "EDF") {
        const deadline = Number(row.deadline);
        const prbs = Number(row.guaranteed_prbs);
        const maxRepl = Number(row.max_replenish);
        if (!Number.isInteger(deadline) || deadline <= 0) errors.push(`${prefix}: deadline must be an integer > 0.`);
        if (!Number.isInteger(prbs) || prbs < 0) errors.push(`${prefix}: guaranteed_prbs must be an integer >= 0.`);
        if (!Number.isInteger(maxRepl) || maxRepl < 0) errors.push(`${prefix}: max_replenish must be an integer >= 0.`);
      }
    });

    if (createProfile === "STATIC") {
      staticRanges
        .slice()
        .sort((a, b) => a.lo - b.lo)
        .forEach((curr, i, arr) => {
          if (i === 0) return;
          const prev = arr[i - 1];
          if (curr.lo <= prev.hi) {
            warnings.push(
              `Slices ${prev.idx + 1} and ${curr.idx + 1} have overlapping STATIC ranges (${prev.lo}-${prev.hi} and ${curr.lo}-${curr.hi}).`,
            );
          }
        });
    }

    if (createProfile === "NVS_CAPACITY" && capacityTotal > 1) {
      errors.push(`NVS CAPACITY total pct_rsvd must be <= 1.0 (currently ${capacityTotal.toFixed(3)}).`);
    }

    return { errors, warnings };
  }, [createProfile, createRows]);

  const updateCreateRow = (idx: number, key: keyof CreateSliceRow, value: string) => {
    setCreateRows((rows) => rows.map((row, i) => (i === idx ? { ...row, [key]: value } : row)));
  };

  const addCreateRow = () => {
    setCreateRows((rows) => [
      ...rows,
      {
        ...rows[rows.length - 1],
        id: String(rows.length),
        label: `s${rows.length + 1}`,
      },
    ]);
  };

  const removeCreateRow = (idx: number) => {
    setCreateRows((rows) => (rows.length <= 1 ? rows : rows.filter((_, i) => i !== idx)));
  };

  const buildCreateSlicesConfig = () => {
    const slices = createRows.map((row) => {
      const common = {
        id: Number(row.id),
        label: row.label.trim(),
        ue_sched_algo: row.ue_sched_algo || "PF",
      };
      if (createProfile === "STATIC") {
        return {
          ...common,
          slice_algo_params: { pos_low: Number(row.pos_low), pos_high: Number(row.pos_high) },
        };
      }
      if (createProfile === "NVS_RATE") {
        return {
          ...common,
          type: "SLICE_SM_NVS_V0_RATE",
          slice_algo_params: { mbps_rsvd: Number(row.mbps_rsvd), mbps_ref: Number(row.mbps_ref) },
        };
      }
      if (createProfile === "NVS_CAPACITY") {
        return {
          ...common,
          type: "SLICE_SM_NVS_V0_CAPACITY",
          slice_algo_params: { pct_rsvd: Number(row.pct_rsvd) },
        };
      }
      return {
        ...common,
        slice_algo_params: {
          deadline: Number(row.deadline),
          guaranteed_prbs: Number(row.guaranteed_prbs),
          max_replenish: Number(row.max_replenish),
        },
      };
    });

    return {
      slice_sched_algo: createProfile === "STATIC" ? "STATIC" : createProfile.startsWith("NVS") ? "NVS" : "EDF",
      slices,
    };
  };

  const sendCreateSlicesPrompt = async () => {
    if (createSliceValidation.errors.length > 0) return;
    const config = buildCreateSlicesConfig();
    const profileText =
      createProfile === "NVS_RATE" ? "NVS RATE" : createProfile === "NVS_CAPACITY" ? "NVS CAPACITY" : createProfile;
    const prompt = [
      `Please use this custom ${profileText} slice config.`,
      "First run mcp_slice_custom_config_capabilities.",
      "If custom create_slices is supported, call mcp_create_slices(config=...) immediately.",
      "If not supported on the current MCP server, explain that clearly and map the request to the closest suites flow (slice_apply_profile_and_verify or slice_start), keeping my ID/label/parameter intent in the explanation.",
      "Do not just restate the JSON.",
      `config=${JSON.stringify(config)}`,
    ].join("\n");
    await sendText(prompt);
  };

  const buildSlicePrompt = () => {
    const duration = Number(sliceDuration);
    const verboseStr = sliceVerbose ? "true" : "false";
    if (sliceMode === "monitor" || sliceProfile === "monitor") {
      return `Please run mcp_slice_monitor_check with duration_s=${duration}, verbose=${verboseStr}, stop_after_check=true. Then summarize whether setup/subscription passed and show the key evidence log lines.`;
    }
    let extra = "";
    if (sliceProfile === "all") {
      extra = `, assoc_dl_id=${Number(sliceAssocDlId)}`;
    }
    return `Please run mcp_slice_apply_profile_and_verify with profile="${sliceProfile}", duration_s=${duration}, verbose=${verboseStr}${extra}, stop_after_verify=true. If verification fails, explain which range or runtime condition failed and guide me to fix it.`;
  };

  const mcpConnectionPanel = (
    <div style={{ marginBottom: 12, ...sidebarSectionCardStyle, display: "grid", gap: 8 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "#344054" }}>MCP Connection</div>
      <label style={{ fontSize: 12, color: "#344054" }}>
        Config API URL
        <input
          value={mcpApiBase}
          onChange={(e) => setMcpApiBase(e.target.value)}
          style={{ ...compactInputStyle, marginTop: 6 }}
        />
      </label>
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
        <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
          Profile
          <select
            value={mcpProfile}
            onChange={(e) => setMcpProfile(e.target.value)}
            style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}
          >
            {Object.keys(mcpProfiles).length === 0 ? (
              <option value={mcpProfile}>{mcpProfile}</option>
            ) : (
              Object.keys(mcpProfiles).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))
            )}
          </select>
        </label>
        <button type="button" style={{ ...chipStyle(false), alignSelf: "end", whiteSpace: "nowrap" }} onClick={() => void applyMcpProfile()} disabled={mcpConfigBusy}>
          Apply
        </button>
      </div>
      <label style={{ fontSize: 12, color: "#344054" }}>
        Transport
        <select
          value={mcpTransport}
          onChange={(e) => setMcpTransport(e.target.value)}
          style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}
        >
          <option value="stdio">stdio</option>
          <option value="http">http</option>
          <option value="auto">auto</option>
        </select>
      </label>
      <label style={{ fontSize: 12, color: "#344054" }}>
        MCP Server Args (stdio)
        <input
          value={mcpServerArgs}
          onChange={(e) => setMcpServerArgs(e.target.value)}
          style={{ ...compactInputStyle, marginTop: 6 }}
        />
      </label>
      <label style={{ fontSize: 12, color: "#344054" }}>
        MCP HTTP URL
        <input
          value={mcpHttpUrl}
          onChange={(e) => setMcpHttpUrl(e.target.value)}
          style={{ ...compactInputStyle, marginTop: 6 }}
        />
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" style={chipStyle(false)} onClick={() => void loadMcpConfig()} disabled={mcpConfigBusy}>
          Refresh
        </button>
        <button type="button" style={chipStyle(false)} onClick={() => void saveMcpConfig()} disabled={mcpConfigBusy}>
          Save runtime config
        </button>
      </div>
      {mcpConfigMsg ? (
        <div style={{ fontSize: 12, color: mcpConfigMsg.toLowerCase().includes("failed") ? "#b42318" : "#475467" }}>
          {mcpConfigMsg}
        </div>
      ) : null}
    </div>
  );

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "linear-gradient(180deg, #f2f7ff 0%, #f9fbff 40%, #ffffff 100%)",
        padding: "24px 16px",
        color: "#1f2937",
      }}
    >
      <div
        style={{
          maxWidth: compactOranUi ? 1440 : 1100,
          margin: "0 auto",
          display: "grid",
          gap: 16,
          gridTemplateColumns: compactOranUi ? "300px minmax(0, 1fr) 320px" : "300px minmax(0, 1fr)",
          alignItems: "start",
        }}
      >
        <aside
          style={{
            background: "#fff",
            border: "1px solid #e7ebf3",
            borderRadius: 14,
            padding: 16,
            alignSelf: "start",
            boxShadow: "0 10px 30px rgba(15, 23, 42, 0.05)",
          }}
        >
          <h3 style={{ margin: "0 0 8px", color: "#0f172a" }}>Slice Assistant</h3>
          <p style={{ margin: "0 0 12px", fontSize: 13, color: "#475467" }}>
            Guided inputs for slice monitor/apply tools. Values are checked here and by the agent.
          </p>

          {!compactOranUi ? mcpConnectionPanel : null}

          {showSimulatorPanel ? (
          <div style={{ marginBottom: 12, ...sidebarSectionCardStyle, display: "grid", gap: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#344054" }}>UAV / UTM Simulator</div>
            <label style={{ fontSize: 12, color: "#344054" }}>
              Simulator API URL
              <input value={uavApiBase} onChange={(e) => setUavApiBase(e.target.value)} style={{ ...compactInputStyle, marginTop: 6 }} />
            </label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                UAV ID
                <input value={simUavId} onChange={(e) => setSimUavId(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }} />
              </label>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                Route ID
                <input value={simRouteId} onChange={(e) => setSimRouteId(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }} />
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                Airspace
                <input value={simAirspace} onChange={(e) => setSimAirspace(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }} />
              </label>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                Step ticks
                <input value={simTicks} onChange={(e) => setSimTicks(e.target.value)} inputMode="numeric" style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }} />
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                Operator License
                <input value={simOperatorLicenseId} onChange={(e) => setSimOperatorLicenseId(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }} />
              </label>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                License Class
                <select value={simLicenseClass} onChange={(e) => setSimLicenseClass(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}>
                  <option value="VLOS">VLOS</option>
                  <option value="BVLOS">BVLOS</option>
                </select>
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                Planned Start
                <input
                  type="datetime-local"
                  value={simPlannedStartAt}
                  onChange={(e) => setSimPlannedStartAt(e.target.value)}
                  style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}
                />
              </label>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                Planned End
                <input
                  type="datetime-local"
                  value={simPlannedEndAt}
                  onChange={(e) => setSimPlannedEndAt(e.target.value)}
                  style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}
                />
              </label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                Requested Speed (m/s)
                <input
                  value={simRequestedSpeedMps}
                  onChange={(e) => setSimRequestedSpeedMps(e.target.value)}
                  inputMode="decimal"
                  style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}
                />
              </label>
              <div style={{ fontSize: 11, color: "#667085", alignSelf: "end", paddingBottom: 2 }}>
                UTM time-window and speed checks use these values when requesting approval.
              </div>
            </div>

            <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 8, background: "#fbfdff", display: "grid", gap: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#344054" }}>Waypoint Editor</div>
              <div style={{ display: "grid", gap: 6 }}>
                {simRouteRows.map((row, idx) => {
                  const rowErrs = simRouteValidation.rowErrors[idx] ?? [];
                  const badX = rowErrs.some((e) => e.startsWith("x"));
                  const badY = rowErrs.some((e) => e.startsWith("y"));
                  const badZ = rowErrs.some((e) => e.startsWith("z"));
                  const setRowField = (field: keyof EditableWaypointRow, value: string) => {
                    setSimRouteRows((rows) => rows.map((r, i) => (i === idx ? { ...r, [field]: value } : r)));
                  };
                  return (
                    <div
                      key={`sim-wp-edit-${idx}`}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "24px 1fr 1fr 1fr 30px",
                        gap: 6,
                        alignItems: "center",
                        background: rowErrs.length ? "#fef3f2" : "#fff",
                        border: `1px solid ${rowErrs.length ? "#fecdca" : "#eaecf0"}`,
                        borderRadius: 8,
                        padding: 6,
                      }}
                    >
                      <div style={{ fontSize: 11, color: "#667085", textAlign: "center" }}>{idx + 1}</div>
                      <input
                        value={row.x}
                        onChange={(e) => setRowField("x", e.target.value)}
                        inputMode="decimal"
                        placeholder="x"
                        style={{ ...compactSliceConfigFieldStyle, marginTop: 0, height: 28, borderColor: badX ? "#f04438" : "#d0d5dd" }}
                      />
                      <input
                        value={row.y}
                        onChange={(e) => setRowField("y", e.target.value)}
                        inputMode="decimal"
                        placeholder="y"
                        style={{ ...compactSliceConfigFieldStyle, marginTop: 0, height: 28, borderColor: badY ? "#f04438" : "#d0d5dd" }}
                      />
                      <input
                        value={row.z}
                        onChange={(e) => setRowField("z", e.target.value)}
                        inputMode="decimal"
                        placeholder="z"
                        style={{ ...compactSliceConfigFieldStyle, marginTop: 0, height: 28, borderColor: badZ ? "#f04438" : "#d0d5dd" }}
                      />
                      <button
                        type="button"
                        onClick={() => setSimRouteRows((rows) => (rows.length <= 2 ? rows : rows.filter((_, i) => i !== idx)))}
                        style={{ ...chipStyle(false), padding: "4px 0", textAlign: "center" }}
                        disabled={uavApiBusy || simRouteRows.length <= 2}
                        title={simRouteRows.length <= 2 ? "Keep at least 2 waypoints" : "Remove waypoint"}
                      >
                        x
                      </button>
                    </div>
                  );
                })}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button
                  type="button"
                  style={chipStyle(false)}
                  onClick={() => setSimRouteRows((rows) => rows.concat([{ x: "0", y: "0", z: "20" }]))}
                  disabled={uavApiBusy}
                >
                  Add Waypoint
                </button>
                <button
                  type="button"
                  style={chipStyle(false)}
                  onClick={() => setSimRouteRows(DEFAULT_SIM_ROUTE.map(waypointToRow))}
                  disabled={uavApiBusy}
                >
                  Reset Demo Route
                </button>
                <button
                  type="button"
                  style={chipStyle(false)}
                  onClick={() => {
                    const uav = asRecord(uavSimState?.uav);
                    const points = Array.isArray(uav?.waypoints)
                      ? (uav.waypoints as unknown[])
                          .filter(isObject)
                          .map((w) => ({
                            x: Number((w as Record<string, unknown>).x ?? 0),
                            y: Number((w as Record<string, unknown>).y ?? 0),
                            z: Number((w as Record<string, unknown>).z ?? 0),
                          }))
                      : [];
                    if (points.length >= 2) setSimRouteRows(points.map(waypointToRow));
                  }}
                  disabled={uavApiBusy}
                >
                  Load Current Route
                </button>
              </div>
              <div style={{ fontSize: 11, color: simRouteValidation.errors.length ? "#b42318" : "#475467" }}>
                {simRouteValidation.errors.length
                  ? simRouteValidation.errors[0]
                  : `Route looks valid (${simRouteValidation.waypoints.length} waypoint${simRouteValidation.waypoints.length === 1 ? "" : "s"}). Max altitude limit: ${simRouteValidation.maxAlt}m`}
              </div>
            </div>

            <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 8, background: "#fbfdff", display: "grid", gap: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#344054" }}>Operator License Registration</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                  License ID
                  <input value={simRegLicenseId} onChange={(e) => setSimRegLicenseId(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }} />
                </label>
                <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                  License Class
                  <select value={simRegLicenseClass} onChange={(e) => setSimRegLicenseClass(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}>
                    <option value="VLOS">VLOS</option>
                    <option value="BVLOS">BVLOS</option>
                  </select>
                </label>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, alignItems: "end" }}>
                <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                  Expiry
                  <input type="datetime-local" value={simRegLicenseExpiry} onChange={(e) => setSimRegLicenseExpiry(e.target.value)} style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }} />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#344054", marginBottom: 4 }}>
                  <input type="checkbox" checked={simRegLicenseActive} onChange={(e) => setSimRegLicenseActive(e.target.checked)} />
                  Active
                </label>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" style={chipStyle(false)} onClick={() => void registerOperatorLicense()} disabled={uavApiBusy}>Register / Update License</button>
                <button
                  type="button"
                  style={chipStyle(false)}
                  onClick={() => {
                    setSimOperatorLicenseId(simRegLicenseId);
                    setSimLicenseClass(simRegLicenseClass);
                  }}
                  disabled={uavApiBusy}
                >
                  Use For Approval
                </button>
              </div>
            </div>

            <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 8, background: "#fbfdff", display: "grid", gap: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#344054" }}>UTM Weather</div>
              <label style={{ fontSize: 11, color: "#475467" }}>
                Wind: {simWind.toFixed(1)} m/s
                <input type="range" min={0} max={25} step={0.5} value={simWind} onChange={(e) => setSimWind(Number(e.target.value))} style={{ width: "100%" }} />
              </label>
              <label style={{ fontSize: 11, color: "#475467" }}>
                Visibility: {simVisibility.toFixed(1)} km
                <input type="range" min={0.5} max={20} step={0.5} value={simVisibility} onChange={(e) => setSimVisibility(Number(e.target.value))} style={{ width: "100%" }} />
              </label>
              <label style={{ fontSize: 11, color: "#475467" }}>
                Precip: {simPrecip.toFixed(1)} mm/h
                <input type="range" min={0} max={10} step={0.5} value={simPrecip} onChange={(e) => setSimPrecip(Number(e.target.value))} style={{ width: "100%" }} />
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#344054" }}>
                <input type="checkbox" checked={simStorm} onChange={(e) => setSimStorm(e.target.checked)} />
                Storm alert
              </label>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" style={chipStyle(false)} onClick={() => void saveWeather()} disabled={uavApiBusy}>Save weather</button>
                <button type="button" style={chipStyle(false)} onClick={() => void loadUavSimState()} disabled={uavApiBusy}>Refresh sim</button>
              </div>
            </div>

            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button type="button" style={chipStyle(false)} onClick={() => void planSimRoute()} disabled={uavApiBusy}>Plan Route</button>
                <button type="button" style={chipStyle(false)} onClick={() => void submitGeofence()} disabled={uavApiBusy}>Submit Geofence</button>
                <button type="button" style={chipStyle(false)} onClick={() => void requestApproval()} disabled={uavApiBusy}>Request UTM Approval</button>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button type="button" style={chipStyle(false)} onClick={() => void launchSim()} disabled={uavApiBusy}>Launch</button>
                <button type="button" style={chipStyle(false)} onClick={() => void stepSim()} disabled={uavApiBusy}>Step</button>
              </div>
            </div>

            {renderUavOverlay(uavSimState)}

            {(() => {
              const uav = asRecord(uavSimState?.uav);
              const approval = asRecord(uav?.utm_approval);
              const geofence = asRecord(uav?.utm_geofence_result);
              const checks = asRecord(approval?.checks);
              const weatherCheck = asRecord(checks?.weather);
              const nfzCheck = asRecord(checks?.no_fly_zone);
              const regCheck = asRecord(checks?.regulations);
              const timeCheck = asRecord(checks?.time_window);
              const licCheck = asRecord(checks?.operator_license);
              return (
                <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 8, background: "#fff" }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#344054", marginBottom: 6 }}>Sim Status</div>
                  <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 4, fontSize: 12 }}>
                    <div style={{ color: "#667085" }}>Phase</div><div><code>{String(uav?.flight_phase ?? "-")}</code></div>
                    <div style={{ color: "#667085" }}>Battery</div><div>{String(uav?.battery_pct ?? "-")}%</div>
                    <div style={{ color: "#667085" }}>Waypoint</div><div>{String(uav?.waypoint_index ?? "-")} / {String(uav?.waypoints_total ?? "-")}</div>
                    <div style={{ color: "#667085" }}>Geofence</div><div>{yesNoBadge(geofence?.ok)}</div>
                    <div style={{ color: "#667085" }}>UTM Approval</div><div>{yesNoBadge(approval?.approved)}</div>
                    <div style={{ color: "#667085" }}>Weather</div><div>{yesNoBadge(weatherCheck?.ok)}</div>
                    <div style={{ color: "#667085" }}>NFZ</div><div>{yesNoBadge(nfzCheck?.ok)}</div>
                    <div style={{ color: "#667085" }}>Regs</div><div>{yesNoBadge(regCheck?.ok)}</div>
                    <div style={{ color: "#667085" }}>Time Window</div><div>{yesNoBadge(timeCheck?.ok)}</div>
                    <div style={{ color: "#667085" }}>Operator License</div><div>{yesNoBadge(licCheck?.ok)}</div>
                  </div>
                  {typeof approval?.reason === "string" && approval.reason ? (
                    <div style={{ fontSize: 12, color: approval?.approved ? "#027a48" : "#b42318", marginTop: 6 }}>
                      Approval reason: <code>{String(approval.reason)}</code>
                    </div>
                  ) : null}
                </div>
              );
            })()}

            {uavApiMsg ? (
              <div style={{ fontSize: 12, color: uavApiMsg.toLowerCase().includes("failed") ? "#b42318" : "#475467" }}>
                {uavApiMsg}
              </div>
            ) : null}
          </div>
          ) : null}

          <div style={{ marginBottom: 12, ...sidebarSectionCardStyle }}>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: "#344054" }}>Mode</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" style={chipStyle(sliceMode === "monitor")} onClick={() => { setSliceMode("monitor"); setSliceProfile("monitor"); }}>
                Monitor Check
              </button>
              <button type="button" style={chipStyle(sliceMode === "apply")} onClick={() => { setSliceMode("apply"); if (sliceProfile === "monitor") setSliceProfile("static"); }}>
                Apply + Verify
              </button>
            </div>
          </div>

          {!compactOranUi ? (
            <div style={{ marginBottom: 12, ...sidebarSectionCardStyle }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: "#344054" }}>Input Style</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  type="button"
                  style={chipStyle(sliceInputMode === "suites")}
                  onClick={() => setSliceInputMode("suites")}
                >
                  Suites profiles
                </button>
                <button
                  type="button"
                  style={chipStyle(sliceInputMode === "custom")}
                  onClick={() => setSliceInputMode("custom")}
                >
                  Custom config
                </button>
              </div>
            </div>
          ) : null}

          <div style={{ marginBottom: 12, ...sidebarSectionCardStyle }}>
            <label style={{ display: "block", fontSize: 12, fontWeight: 700, marginBottom: 6, color: "#344054" }}>Slice profile</label>
            <select
              value={sliceProfile}
              onChange={(e) => {
                const next = e.target.value as SliceProfile;
                setSliceProfile(next);
                // Let the profile dropdown drive mode so users can choose directly.
                setSliceMode(next === "monitor" ? "monitor" : "apply");
              }}
              style={{ width: "100%", padding: "8px 10px", borderRadius: 8, border: "1px solid #d0d5dd" }}
            >
              {(["monitor", "static", "nvs-rate", "nvs-cap", "edf", "all"] as SliceProfile[]).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
              <div style={{ fontSize: 12, color: "#667085", marginTop: 4, lineHeight: 1.3 }}>
                {sliceMode === "monitor"
                ? "monitor -> Monitor Check"
                : "static/nvs/edf/all -> Apply + Verify"}
              </div>
            </div>

          <div style={{ marginBottom: 12, display: "grid", gap: 8, ...sidebarSectionCardStyle }}>
            <label style={{ display: "block", fontSize: 12, fontWeight: 700, color: "#344054" }}>
              Duration (s)
              <input
                value={sliceDuration}
                onChange={(e) => setSliceDuration(e.target.value)}
                inputMode="numeric"
                style={{ ...compactInputStyle, marginTop: 6, maxWidth: 140 }}
              />
            </label>
            <div style={{ fontSize: 12, color: "#667085", lineHeight: 1.3 }}>
              {sliceMode === "monitor" ? "Range: 5-600" : "Range: 1-3600"}
            </div>
          </div>

          {sliceMode === "apply" && sliceProfile === "all" && (
            <div style={{ marginBottom: 12, ...sidebarSectionCardStyle }}>
              <label style={{ display: "block", fontSize: 12, fontWeight: 700, marginBottom: 6, color: "#344054" }}>
                assoc_dl_id (for profile=all)
              </label>
              <input
                value={sliceAssocDlId}
                onChange={(e) => setSliceAssocDlId(e.target.value)}
                inputMode="numeric"
                style={{ ...compactInputStyle, maxWidth: 140 }}
              />
              <div style={{ fontSize: 12, color: "#667085", marginTop: 4 }}>Range: 0-255</div>
            </div>
          )}

          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#344054", marginBottom: 12, ...sidebarSectionCardStyle }}>
            <input type="checkbox" checked={sliceVerbose} onChange={(e) => setSliceVerbose(e.target.checked)} />
            Verbose logs
          </label>

          {sliceValidation.length > 0 && (
            <div style={{ background: "#fff4ed", border: "1px solid #ffd6ae", borderRadius: 10, padding: 10, marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 12, color: "#9a3412", marginBottom: 4 }}>Please fix input values</div>
              {sliceValidation.map((err, i) => (
                <div key={i} style={fieldErrorStyle()}>{err}</div>
              ))}
            </div>
          )}

          <button
            onClick={() => void sendText(buildSlicePrompt())}
            disabled={stream.isLoading || sliceValidation.length > 0}
            style={{
              width: "100%",
              borderRadius: 10,
              border: "1px solid #0f766e",
              background: sliceValidation.length > 0 ? "#d1d5db" : "#0f766e",
              color: "#fff",
              padding: "10px 12px",
              fontWeight: 700,
              cursor: sliceValidation.length > 0 ? "not-allowed" : "pointer",
            }}
          >
            {sliceMode === "monitor" ? "Run Monitor Check" : "Apply Slice Profile + Verify"}
          </button>

          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#344054", marginBottom: 6 }}>Quick actions</div>
            <div style={{ display: "grid", gap: 8 }}>
              <button style={chipStyle(false)} onClick={() => void sendText("List available MCP tools and summarize the slice-related ones.")}>
                List slice tools
              </button>
              <button style={chipStyle(false)} onClick={() => void sendText("Run mcp_health and tell me if the MCP suites server is healthy.")}>
                Check MCP health
              </button>
              <button style={chipStyle(false)} onClick={() => void sendText("Show current active suite runs and tail the slice run log if it exists.")}>
                Check active runs
              </button>
            </div>
          </div>

          {!compactOranUi ? (
          <div
            style={{
              marginTop: 14,
              paddingTop: 14,
              borderTop: "1px solid #eaecf0",
              display: "grid",
              gap: 10,
              opacity: sliceInputMode === "custom" ? 1 : 0.95,
            }}
          >
            <div style={{ display: "grid", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#344054" }}>Custom Slice Config</div>
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      borderRadius: 999,
                      padding: "2px 8px",
                      border: "1px solid #c7d7fe",
                      background: "#eef4ff",
                      color: "#1d4ed8",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {createProfile} • {createRows.length} row{createRows.length === 1 ? "" : "s"}
                  </span>
                </div>
                <button type="button" style={chipStyle(customPanelOpen)} onClick={() => setCustomPanelOpen((v) => !v)}>
                  {customPanelOpen ? "Hide" : "Show"}
                </button>
              </div>

              <div style={{ fontSize: 12, color: "#667085", lineHeight: 1.35 }}>
                Enter slice ID, label, and parameters. The UI checks ranges first, then the agent runs it (or maps it to suites if needed).
              </div>
            </div>

            {customPanelOpen ? (
            <>
            <div
              style={{
                display: "grid",
                gap: 8,
                ...sidebarSectionCardStyle,
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
              <label style={{ display: "block", fontSize: 12, fontWeight: 700, color: "#344054", flex: "1 1 180px", minWidth: 0 }}>
                Profile
                <select
                  value={createProfile}
                  onChange={(e) => setCreateProfile(e.target.value as CreateSliceProfile)}
                  style={{ ...compactSliceConfigFieldStyle, marginTop: 6, height: 30 }}
                >
                  <option value="STATIC">STATIC</option>
                  <option value="NVS_RATE">NVS RATE</option>
                  <option value="NVS_CAPACITY">NVS CAPACITY</option>
                  <option value="EDF">EDF</option>
                </select>
              </label>
              <button
                type="button"
                style={{ ...chipStyle(false), whiteSpace: "nowrap" }}
                onClick={addCreateRow}
              >
                Add row
              </button>
              </div>
            </div>

            <div style={{ fontSize: 12, color: "#667085", background: "#f8faff", border: "1px solid #e5ecff", borderRadius: 8, padding: 8, lineHeight: 1.35 }}>
              {createProfile === "STATIC" && "STATIC: id >= 0, pos_low >= 0, pos_high >= 0, pos_low <= pos_high."}
              {createProfile === "NVS_RATE" && "NVS RATE: mbps_rsvd > 0, mbps_ref > 0, mbps_rsvd <= mbps_ref."}
              {createProfile === "NVS_CAPACITY" && "NVS CAPACITY: pct_rsvd in (0,1], total <= 1.0."}
              {createProfile === "EDF" && "EDF: deadline > 0, guaranteed_prbs >= 0, max_replenish >= 0."}
            </div>

            <div style={{ display: "grid", gap: 10, maxHeight: 320, overflowY: "auto", overflowX: "hidden", paddingRight: 4 }}>
              {createRows.map((row, idx) => (
                <div key={`create-row-${idx}`} style={{ border: "1px solid #eaecf0", borderRadius: 10, padding: 8, background: "#fcfcfd", display: "grid", gap: 6, overflowX: "hidden" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, gap: 8, flexWrap: "wrap" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: 12, color: "#344054" }}>Slice {idx + 1}</div>
                      <span style={{ fontSize: 11, color: "#667085" }}>#{row.id || "?"}</span>
                    </div>
                    <button
                      type="button"
                      style={{ ...chipStyle(false), whiteSpace: "nowrap" }}
                      onClick={() => removeCreateRow(idx)}
                      disabled={createRows.length <= 1}
                    >
                      Remove
                    </button>
                  </div>

                  <div style={{ ...tinyLabelStyle }}>Basic</div>
                  <div style={{ display: "grid", gridTemplateColumns: "minmax(64px, 80px) minmax(0, 1fr)", gap: 8, alignItems: "end" }}>
                    <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                      ID
                      <input
                        value={row.id}
                        onChange={(e) => updateCreateRow(idx, "id", e.target.value)}
                        inputMode="numeric"
                        style={compactSliceConfigFieldStyle}
                      />
                    </label>
                    <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                      Label
                      <input
                        value={row.label}
                        onChange={(e) => updateCreateRow(idx, "label", e.target.value)}
                        style={compactSliceConfigFieldStyle}
                      />
                    </label>
                    <label style={{ fontSize: 12, color: "#344054", gridColumn: "1 / -1", width: "100%", maxWidth: 140 }}>
                      UE sched
                      <select
                        value={row.ue_sched_algo}
                        onChange={(e) => updateCreateRow(idx, "ue_sched_algo", e.target.value)}
                        style={compactSliceConfigFieldStyle}
                      >
                        <option value="PF">PF</option>
                        <option value="RR">RR</option>
                        <option value="MT">MT</option>
                      </select>
                    </label>
                  </div>

                  <div style={{ ...tinyLabelStyle }}>Parameters</div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
                    {createProfile === "STATIC" && (
                      <>
                        <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                          pos_low
                          <input
                            value={row.pos_low}
                            onChange={(e) => updateCreateRow(idx, "pos_low", e.target.value)}
                            inputMode="numeric"
                            style={compactSliceConfigFieldStyle}
                          />
                        </label>
                        <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                          pos_high
                          <input
                            value={row.pos_high}
                            onChange={(e) => updateCreateRow(idx, "pos_high", e.target.value)}
                            inputMode="numeric"
                            style={compactSliceConfigFieldStyle}
                          />
                        </label>
                      </>
                    )}

                    {createProfile === "NVS_RATE" && (
                      <>
                        <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                          mbps_rsvd
                          <input
                            value={row.mbps_rsvd}
                            onChange={(e) => updateCreateRow(idx, "mbps_rsvd", e.target.value)}
                            inputMode="numeric"
                            style={compactSliceConfigFieldStyle}
                          />
                        </label>
                        <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                          mbps_ref
                          <input
                            value={row.mbps_ref}
                            onChange={(e) => updateCreateRow(idx, "mbps_ref", e.target.value)}
                            inputMode="numeric"
                            style={compactSliceConfigFieldStyle}
                          />
                        </label>
                      </>
                    )}

                    {createProfile === "NVS_CAPACITY" && (
                      <label style={{ fontSize: 12, color: "#344054", gridColumn: "1 / -1", minWidth: 0 }}>
                        pct_rsvd (0..1]
                        <input
                          value={row.pct_rsvd}
                          onChange={(e) => updateCreateRow(idx, "pct_rsvd", e.target.value)}
                          inputMode="decimal"
                          style={compactSliceConfigFieldStyle}
                        />
                      </label>
                    )}

                    {createProfile === "EDF" && (
                      <>
                        <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                          deadline
                          <input
                            value={row.deadline}
                            onChange={(e) => updateCreateRow(idx, "deadline", e.target.value)}
                            inputMode="numeric"
                            style={compactSliceConfigFieldStyle}
                          />
                        </label>
                        <label style={{ fontSize: 12, color: "#344054", minWidth: 0 }}>
                          guaranteed_prbs
                          <input
                            value={row.guaranteed_prbs}
                            onChange={(e) => updateCreateRow(idx, "guaranteed_prbs", e.target.value)}
                            inputMode="numeric"
                            style={compactSliceConfigFieldStyle}
                          />
                        </label>
                        <label style={{ fontSize: 12, color: "#344054", gridColumn: "1 / -1", minWidth: 0 }}>
                          max_replenish
                          <input
                            value={row.max_replenish}
                            onChange={(e) => updateCreateRow(idx, "max_replenish", e.target.value)}
                            inputMode="numeric"
                            style={compactSliceConfigFieldStyle}
                          />
                        </label>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8 }}>
              <button
                type="button"
                style={chipStyle(false)}
                onClick={() =>
                  void sendText(
                    "Help me build a create_slices config step-by-step. First check whether the current MCP server exposes create_slices; if not, tell me and suggest the suites-tool alternative.",
                  )
                }
              >
                Ask agent
              </button>
            </div>

            {createSliceValidation.errors.length > 0 && (
              <div style={{ background: "#fff4ed", border: "1px solid #ffd6ae", borderRadius: 10, padding: 10 }}>
                  <div style={{ fontWeight: 700, fontSize: 12, color: "#9a3412", marginBottom: 4 }}>Fix these custom-slice inputs</div>
                {createSliceValidation.errors.map((err, i) => (
                  <div key={`create-err-${i}`} style={fieldErrorStyle()}>{err}</div>
                ))}
              </div>
            )}

            {createSliceValidation.warnings.length > 0 && (
              <div style={{ background: "#fffaeb", border: "1px solid #fedf89", borderRadius: 10, padding: 10 }}>
                <div style={{ fontWeight: 700, fontSize: 12, color: "#b54708", marginBottom: 4 }}>Warnings</div>
                {createSliceValidation.warnings.map((warn, i) => (
                  <div key={`create-warn-${i}`} style={{ color: "#b54708", fontSize: 12, marginTop: 4 }}>{warn}</div>
                ))}
              </div>
            )}

            <button
              onClick={() => void sendCreateSlicesPrompt()}
              disabled={stream.isLoading || createSliceValidation.errors.length > 0}
              style={{
                width: "100%",
                borderRadius: 10,
                border: "1px solid #155eef",
                background: createSliceValidation.errors.length > 0 ? "#d1d5db" : "#155eef",
                color: "#fff",
                padding: "10px 12px",
                fontWeight: 700,
                cursor: createSliceValidation.errors.length > 0 ? "not-allowed" : "pointer",
              }}
            >
              Apply Custom Slice Request
            </button>
            </>
            ) : (
              <div
                style={{
                  border: "1px dashed #d0d5dd",
                  borderRadius: 10,
                  padding: "10px 12px",
                  background: "#fcfcfd",
                  fontSize: 12,
                  color: "#667085",
                  lineHeight: 1.35,
                }}
              >
                Hidden for a cleaner layout. Switch <b>Input Style</b> to <b>Custom config</b> or press <b>Show</b> to edit ID/label/per-slice parameters.
              </div>
            )}
          </div>
          ) : null}
        </aside>

        <main
          style={{
            background: "#fff",
            border: "1px solid #e7ebf3",
            borderRadius: 14,
            padding: 16,
            boxShadow: "0 10px 30px rgba(15, 23, 42, 0.05)",
            minHeight: 620,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div>
              <h2 style={{ margin: 0, color: "#0f172a" }}>O-RAN Agent Console</h2>
              <div style={{ fontSize: 13, color: "#667085", maxWidth: 520, lineHeight: 1.35 }}>
                Friendly chat with guided slice validation. The agent re-checks inputs before MCP calls.
                {compactOranUi ? " Advanced custom-slice editing is hidden on this page for a simpler workflow." : ""}
              </div>
            </div>
            <div style={{ fontSize: 12, color: stream.isLoading ? "#b54708" : "#027a48" }}>
              {stream.isLoading ? "Agent working..." : "Ready"}
            </div>
          </div>

          <div
            style={{
              height: 470,
              overflow: "auto",
              border: "1px solid #eaecf0",
              borderRadius: 12,
              padding: 12,
              background: "#fcfcfd",
              marginBottom: 12,
            }}
          >
            {(stream.messages ?? []).map((raw, i) => {
              const msg = toRenderableMsg(raw);
              const key = msg.id ?? `${i}`;
              const isHuman = (msg.type ?? "").toLowerCase().includes("human");
              return (
                <div key={key} style={{ display: "flex", justifyContent: isHuman ? "flex-end" : "flex-start", marginBottom: 10 }}>
                  <div
                    style={{
                      maxWidth: "85%",
                      borderRadius: 12,
                      padding: "10px 12px",
                      border: "1px solid " + (isHuman ? "#c7d7fe" : "#e7ebf3"),
                      background: isHuman ? "#eef4ff" : "#fff",
                    }}
                  >
                    <div style={{ fontSize: 11, fontWeight: 700, color: "#667085", marginBottom: 4 }}>
                      {isHuman ? "You" : (msg.type ?? "assistant")}
                    </div>
                    <div style={{ color: "#1f2937", fontSize: 14 }}>{renderContent(msg.content)}</div>
                  </div>
                </div>
              );
            })}
            {stream.isLoading && (
              <div style={{ display: "grid", gap: 6 }}>
                <div style={{ color: "#667085", fontSize: 13 }}>
                  Thinking... {loadingSeconds > 0 ? `(${loadingSeconds}s)` : ""}
                </div>
                {loadingSeconds >= 12 ? (
                  <div
                    style={{
                      fontSize: 12,
                      color: "#b54708",
                      background: "#fffaeb",
                      border: "1px solid #fedf89",
                      borderRadius: 8,
                      padding: "6px 8px",
                      lineHeight: 1.35,
                    }}
                  >
                    This is taking longer than expected. It may be waiting on the MCP server or Ollama.
                    If it keeps spinning, retry and check the last tool/error message.
                  </div>
                ) : null}
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: 10 }}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if ((e.nativeEvent as KeyboardEvent | undefined)?.isComposing) return;
                if (e.key === "Enter") void send();
              }}
              style={{
                flex: 1,
                borderRadius: 10,
                border: "1px solid #d0d5dd",
                padding: "10px 12px",
                fontSize: 14,
              }}
              placeholder="Ask the agent (e.g., 'run slice monitor check and summarize logs')"
              title="Free chat input. For create_slices, the guided builder on the left validates number ranges before sending."
            />
            <button
              onClick={() => void send()}
              disabled={stream.isLoading || !input.trim()}
              style={{
                borderRadius: 10,
                border: "1px solid #175cd3",
                background: "#175cd3",
                color: "#fff",
                padding: "10px 14px",
                fontWeight: 700,
                cursor: stream.isLoading || !input.trim() ? "not-allowed" : "pointer",
                opacity: stream.isLoading || !input.trim() ? 0.6 : 1,
              }}
            >
              Send
            </button>
          </div>

          {stream.error ? (
            <div style={{ color: "#b42318", marginTop: 10, fontSize: 13 }}>
              Error: {stream.error instanceof Error ? stream.error.message : String(stream.error)}
            </div>
          ) : null}
          {!stream.error && stream.isLoading && loadingSeconds >= 25 ? (
            <div style={{ color: "#b42318", marginTop: 10, fontSize: 13 }}>
              No response yet. If this continues, the backend may be hung on an MCP/Ollama call.
            </div>
          ) : null}
        </main>

        {compactOranUi ? (
          <aside
            style={{
              background: "#fff",
              border: "1px solid #e7ebf3",
              borderRadius: 14,
              padding: 16,
              alignSelf: "start",
              boxShadow: "0 10px 30px rgba(15, 23, 42, 0.05)",
              position: "sticky",
              top: 24,
            }}
          >
            <h3 style={{ margin: "0 0 8px", color: "#0f172a" }}>MCP Runtime</h3>
            <p style={{ margin: "0 0 12px", fontSize: 13, color: "#475467", lineHeight: 1.35 }}>
              Connection and runtime profile controls for the O-RAN MCP server.
            </p>
            {mcpConnectionPanel}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
