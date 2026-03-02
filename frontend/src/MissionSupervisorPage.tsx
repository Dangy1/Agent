import React, { useEffect, useMemo, useState } from "react";

type JsonRecord = Record<string, unknown>;

type MissionListItem = {
  mission_id: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  request_text?: string;
};

type MissionEvent = {
  ts?: string;
  type?: string;
  data?: JsonRecord;
};

type SequenceMessage = {
  from: string;
  to: string;
  label: string;
  replayed: boolean;
};

type SequenceModel = {
  participants: string[];
  messages: SequenceMessage[];
};

function isObject(x: unknown): x is JsonRecord {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): JsonRecord | null {
  return isObject(x) ? x : null;
}

function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

function parseHttpErrorText(raw: string, fallback: string): string {
  const text = raw.trim();
  if (!text) return fallback;
  try {
    const parsed = JSON.parse(text) as unknown;
    const rec = asRecord(parsed);
    if (rec && typeof rec.detail === "string" && rec.detail.trim()) return rec.detail.trim();
  } catch {
    // Keep plain text response when not JSON.
  }
  return text;
}

function fmtTs(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const dt = new Date(value);
  return Number.isNaN(dt.getTime()) ? value : dt.toLocaleString();
}

function statusChip(status: string | undefined): React.CSSProperties {
  const s = (status || "unknown").toLowerCase();
  const tone =
    s === "completed"
      ? { bg: "#ecfdf3", fg: "#027a48", bd: "#abefc6" }
      : s === "failed"
        ? { bg: "#fef3f2", fg: "#b42318", bd: "#fecdca" }
        : s === "running"
          ? { bg: "#eef4ff", fg: "#155eef", bd: "#b2ccff" }
          : s === "queued" || s === "stop_requested"
            ? { bg: "#fff7e6", fg: "#b54708", bd: "#fedf89" }
            : { bg: "#f2f4f7", fg: "#475467", bd: "#d0d5dd" };
  return {
    display: "inline-block",
    borderRadius: 999,
    border: `1px solid ${tone.bd}`,
    background: tone.bg,
    color: tone.fg,
    padding: "2px 8px",
    fontSize: 12,
    fontWeight: 700,
    whiteSpace: "nowrap",
  };
}

const cardStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #eaecf0",
  borderRadius: 14,
  padding: 12,
  boxShadow: "0 1px 2px rgba(16,24,40,0.04)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
  padding: "8px 10px",
  fontSize: 13,
};

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function renderList(items: unknown[], emptyLabel: string) {
  if (!items.length) return <div style={{ color: "#667085", fontSize: 13 }}>{emptyLabel}</div>;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {items.map((item, idx) => (
        <pre
          key={idx}
          style={{
            margin: 0,
            whiteSpace: "pre-wrap",
            background: "#f8fafc",
            border: "1px solid #eaecf0",
            borderRadius: 10,
            padding: 10,
            fontSize: 12,
            color: "#101828",
          }}
        >
          {JSON.stringify(item, null, 2)}
        </pre>
      ))}
    </div>
  );
}

function parseMermaidSequence(mermaidText: string): SequenceModel {
  const participants: string[] = [];
  const aliasToName = new Map<string, string>();
  const messages: SequenceMessage[] = [];
  const lines = mermaidText
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean);

  for (const line of lines) {
    if (line.startsWith("participant ")) {
      const body = line.slice("participant ".length).trim();
      const asMatch = body.match(/^([A-Za-z0-9_.-]+)\s+as\s+(.+)$/);
      if (asMatch) {
        const alias = asMatch[1];
        const name = asMatch[2].trim();
        aliasToName.set(alias, name);
        if (!participants.includes(name)) participants.push(name);
        continue;
      }
      const token = body.split(/\s+/g)[0];
      if (token) {
        aliasToName.set(token, token);
        if (!participants.includes(token)) participants.push(token);
      }
      continue;
    }

    const messageMatch = line.match(/^([A-Za-z0-9_.-]+)\s*(-{1,2}>>)\s*([A-Za-z0-9_.-]+)\s*:\s*(.+)$/);
    if (!messageMatch) continue;
    const fromAlias = messageMatch[1];
    const arrow = messageMatch[2];
    const toAlias = messageMatch[3];
    const label = messageMatch[4].trim();
    const from = aliasToName.get(fromAlias) ?? fromAlias;
    const to = aliasToName.get(toAlias) ?? toAlias;
    if (!participants.includes(from)) participants.push(from);
    if (!participants.includes(to)) participants.push(to);
    messages.push({
      from,
      to,
      label,
      replayed: arrow === "-->>" || /\[[^\]]*replayed[^\]]*\]/i.test(label),
    });
  }

  return { participants, messages };
}

function SequenceDiagram({ mermaidText }: { mermaidText: string }) {
  const model = useMemo(() => parseMermaidSequence(mermaidText), [mermaidText]);
  const participants = model.participants;
  const messages = model.messages;

  if (!mermaidText.trim()) {
    return <div style={{ color: "#667085", fontSize: 13 }}>No protocol trace yet</div>;
  }
  if (!participants.length || !messages.length) {
    return <div style={{ color: "#667085", fontSize: 13 }}>Protocol trace has no message flow yet</div>;
  }

  const laneGap = 220;
  const width = participants.length <= 1 ? 760 : Math.max(760, (participants.length - 1) * laneGap + 160);
  const xForIndex = (idx: number) => (participants.length <= 1 ? Math.floor(width / 2) : 80 + idx * laneGap);
  const height = Math.max(180, 120 + messages.length * 56);
  const laneTop = 38;
  const laneBottom = height - 24;

  return (
    <div style={{ overflowX: "auto", border: "1px solid #eaecf0", borderRadius: 10, background: "#fcfcfd" }}>
      <svg width={width} height={height} role="img" aria-label="A2A sequence diagram">
        {participants.map((name, idx) => {
          const x = xForIndex(idx);
          return (
            <g key={name}>
              <line x1={x} y1={laneTop} x2={x} y2={laneBottom} stroke="#d0d5dd" strokeDasharray="4 4" strokeWidth={1} />
              <rect x={x - 72} y={8} width={144} height={24} rx={7} fill="#eef4ff" stroke="#b2ccff" />
              <text x={x} y={24} textAnchor="middle" fontSize={12} fill="#0c2b73" fontWeight={600}>
                {name}
              </text>
            </g>
          );
        })}

        {messages.map((msg, idx) => {
          const fromIdx = participants.indexOf(msg.from);
          const toIdx = participants.indexOf(msg.to);
          const x1 = xForIndex(fromIdx >= 0 ? fromIdx : 0);
          const x2 = xForIndex(toIdx >= 0 ? toIdx : 0);
          const y = 66 + idx * 56;
          const stroke = msg.replayed ? "#98a2b3" : "#155eef";
          const dash = msg.replayed ? "6 4" : undefined;
          const labelColor = msg.replayed ? "#667085" : "#1d2939";

          if (x1 === x2) {
            const loopWidth = 26;
            const loopHeight = 16;
            const path = `M ${x1} ${y} C ${x1 + loopWidth} ${y}, ${x1 + loopWidth} ${y + loopHeight}, ${x1} ${y + loopHeight}`;
            const arrow = `${x1},${y + loopHeight} ${x1 + 8},${y + loopHeight - 4} ${x1 + 8},${y + loopHeight + 4}`;
            return (
              <g key={`${msg.from}-${msg.to}-${idx}`}>
                <path d={path} fill="none" stroke={stroke} strokeWidth={2} strokeDasharray={dash} />
                <polygon points={arrow} fill={stroke} />
                <text x={x1 + loopWidth + 10} y={y + loopHeight / 2 + 4} fontSize={11} fill={labelColor}>
                  {msg.label}
                </text>
              </g>
            );
          }

          const arrowPoints =
            x2 >= x1 ? `${x2},${y} ${x2 - 8},${y - 4} ${x2 - 8},${y + 4}` : `${x2},${y} ${x2 + 8},${y - 4} ${x2 + 8},${y + 4}`;
          return (
            <g key={`${msg.from}-${msg.to}-${idx}`}>
              <line x1={x1} y1={y} x2={x2} y2={y} stroke={stroke} strokeWidth={2} strokeDasharray={dash} />
              <polygon points={arrowPoints} fill={stroke} />
              <text x={(x1 + x2) / 2} y={y - 8} textAnchor="middle" fontSize={11} fill={labelColor}>
                {msg.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function MissionSupervisorPage() {
  const [apiBase, setApiBase] = useState("http://127.0.0.1:8023");
  const [requestText, setRequestText] = useState("Coordinate UAV and UTM preflight approval, then optimize network coverage for route.");
  const [metadataText, setMetadataText] = useState('{"operator":"console","priority":"normal"}');
  const [missions, setMissions] = useState<MissionListItem[]>([]);
  const [selectedMissionId, setSelectedMissionId] = useState<string>("");
  const [stateSnapshot, setStateSnapshot] = useState<JsonRecord | null>(null);
  const [events, setEvents] = useState<MissionEvent[]>([]);
  const [protocolMermaid, setProtocolMermaid] = useState("");
  const [protocolMermaidError, setProtocolMermaidError] = useState("");
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState("Mission supervisor UI ready");
  const [errorMsg, setErrorMsg] = useState("");
  const [stopReason, setStopReason] = useState("operator_request");

  const loadMissionList = async () => {
    try {
      const res = await fetch(`${normalizeBaseUrl(apiBase)}/api/mission`);
      const data = (await res.json()) as unknown;
      if (!res.ok) throw new Error(String(asRecord(data)?.detail ?? "Mission list request failed"));
      const result = asRecord(asRecord(data)?.result);
      const rows = readArray(result?.missions)
        .map((m) => asRecord(m))
        .filter((m): m is JsonRecord => m !== null)
        .map(
          (m): MissionListItem => ({
            mission_id: String(m.mission_id ?? ""),
            status: typeof m.status === "string" ? m.status : undefined,
            created_at: typeof m.created_at === "string" ? m.created_at : undefined,
            updated_at: typeof m.updated_at === "string" ? m.updated_at : undefined,
            request_text: typeof m.request_text === "string" ? m.request_text : undefined,
          }),
        )
        .filter((m) => m.mission_id);
      setMissions(rows);
      if (!selectedMissionId && rows.length > 0) setSelectedMissionId(rows[0].mission_id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const loadSelectedMission = async (missionId: string) => {
    if (!missionId) return;
    try {
      const base = normalizeBaseUrl(apiBase);
      const [stateRes, eventsRes, mermaidRes] = await Promise.all([
        fetch(`${base}/api/mission/${encodeURIComponent(missionId)}/state`),
        fetch(`${base}/api/mission/${encodeURIComponent(missionId)}/events?limit=200`),
        fetch(`${base}/api/mission/${encodeURIComponent(missionId)}/protocol-trace/mermaid?limit=500&include_replayed=true`),
      ]);
      const stateData = (await stateRes.json()) as unknown;
      const eventsData = (await eventsRes.json()) as unknown;
      const mermaidText = await mermaidRes.text();
      if (!stateRes.ok) throw new Error(String(asRecord(stateData)?.detail ?? "Mission state request failed"));
      if (!eventsRes.ok) throw new Error(String(asRecord(eventsData)?.detail ?? "Mission events request failed"));
      if (!mermaidRes.ok) throw new Error(parseHttpErrorText(mermaidText, "Mission protocol trace request failed"));
      setStateSnapshot(asRecord(asRecord(stateData)?.result));
      setProtocolMermaid(mermaidText);
      setProtocolMermaidError("");
      const result = asRecord(asRecord(eventsData)?.result);
      const rows = readArray(result?.events)
        .map((ev) => asRecord(ev))
        .filter((ev): ev is JsonRecord => ev !== null)
        .map(
          (ev): MissionEvent => ({
            ts: typeof ev.ts === "string" ? ev.ts : undefined,
            type: typeof ev.type === "string" ? ev.type : undefined,
            data: asRecord(ev.data) ?? undefined,
          }),
        );
      setEvents(rows);
    } catch (e) {
      setProtocolMermaidError(e instanceof Error ? e.message : String(e));
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void loadMissionList();
    const id = window.setInterval(() => {
      void loadMissionList();
    }, 3000);
    return () => window.clearInterval(id);
  }, [apiBase]);

  useEffect(() => {
    if (!selectedMissionId) return;
    void loadSelectedMission(selectedMissionId);
    const id = window.setInterval(() => {
      void loadSelectedMission(selectedMissionId);
    }, 1500);
    return () => window.clearInterval(id);
  }, [apiBase, selectedMissionId]);

  const startMission = async () => {
    setBusy(true);
    setErrorMsg("");
    setStatusMsg("");
    try {
      let metadata: JsonRecord | undefined;
      if (metadataText.trim()) {
        const parsed = JSON.parse(metadataText) as unknown;
        if (!isObject(parsed)) throw new Error("metadata must be a JSON object");
        metadata = parsed;
      }
      const res = await fetch(`${normalizeBaseUrl(apiBase)}/api/mission/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_text: requestText, metadata }),
      });
      const data = (await res.json()) as unknown;
      if (!res.ok) throw new Error(String(asRecord(data)?.detail ?? "Mission start failed"));
      const result = asRecord(asRecord(data)?.result);
      const missionId = typeof result?.mission_id === "string" ? result.mission_id : "";
      if (missionId) setSelectedMissionId(missionId);
      setStatusMsg(missionId ? `Started mission ${missionId}` : "Mission started");
      await loadMissionList();
      if (missionId) await loadSelectedMission(missionId);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const stopMission = async () => {
    if (!selectedMissionId) return;
    setBusy(true);
    setErrorMsg("");
    try {
      const res = await fetch(`${normalizeBaseUrl(apiBase)}/api/mission/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mission_id: selectedMissionId, reason: stopReason || "operator_request" }),
      });
      const data = (await res.json()) as unknown;
      if (!res.ok) throw new Error(String(asRecord(data)?.detail ?? "Mission stop failed"));
      setStatusMsg(`Stop requested for ${selectedMissionId}`);
      await loadMissionList();
      await loadSelectedMission(selectedMissionId);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const graphState = asRecord(stateSnapshot?.graph_state) ?? stateSnapshot;
  const missionPhase = typeof graphState?.mission_phase === "string" ? graphState.mission_phase : "-";
  const missionStatus = typeof graphState?.mission_status === "string" ? graphState.mission_status : typeof stateSnapshot?.status === "string" ? stateSnapshot.status : "-";
  const currentStep = typeof graphState?.current_step === "number" ? graphState.current_step : graphState?.current_step != null ? String(graphState.current_step) : "-";
  const plan = readArray(graphState?.plan);
  const proposedActions = readArray(graphState?.proposed_actions);
  const appliedActions = readArray(graphState?.applied_actions);
  const decisionLog = readArray(graphState?.decision_log);

  return (
    <div style={{ maxWidth: 1280, margin: "0 auto", padding: 16, display: "grid", gap: 14 }}>
      <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#101828" }}>Mission Supervisor</div>
            <div style={{ fontSize: 12, color: "#667085" }}>Start/stop missions and inspect runtime state/events</div>
          </div>
          <div style={{ minWidth: 300, flex: "1 1 320px" }}>
            <label style={{ fontSize: 12, color: "#344054" }}>Mission API Base</label>
            <input style={inputStyle} value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 10 }}>
          <div>
            <label style={{ fontSize: 12, color: "#344054" }}>Request Text</label>
            <textarea
              style={{ ...inputStyle, minHeight: 72, resize: "vertical" }}
              value={requestText}
              onChange={(e) => setRequestText(e.target.value)}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: "#344054" }}>Metadata (JSON object, optional)</label>
            <textarea
              style={{ ...inputStyle, minHeight: 72, resize: "vertical", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
              value={metadataText}
              onChange={(e) => setMetadataText(e.target.value)}
            />
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => void startMission()}
            disabled={busy || !requestText.trim()}
            style={{ borderRadius: 10, border: "1px solid #1570ef", background: "#1570ef", color: "#fff", padding: "8px 12px", fontWeight: 700, cursor: "pointer" }}
          >
            Start Mission
          </button>
          <input style={{ ...inputStyle, width: 180 }} value={stopReason} onChange={(e) => setStopReason(e.target.value)} />
          <button
            type="button"
            onClick={() => void stopMission()}
            disabled={busy || !selectedMissionId}
            style={{ borderRadius: 10, border: "1px solid #d92d20", background: "#fff", color: "#b42318", padding: "8px 12px", fontWeight: 700, cursor: "pointer" }}
          >
            Stop Selected
          </button>
          <button type="button" onClick={() => void loadMissionList()} style={{ borderRadius: 10, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", padding: "8px 12px", fontWeight: 600, cursor: "pointer" }}>
            Refresh
          </button>
          <div style={{ fontSize: 12, color: errorMsg ? "#b42318" : "#475467" }}>{errorMsg || statusMsg}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "340px minmax(0, 1fr)", gap: 14, alignItems: "start" }}>
        <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>Missions</div>
          <div style={{ display: "grid", gap: 8, maxHeight: 720, overflow: "auto" }}>
            {missions.length === 0 ? <div style={{ color: "#667085", fontSize: 13 }}>No missions yet</div> : null}
            {missions.map((mission) => {
              const active = mission.mission_id === selectedMissionId;
              return (
                <button
                  key={mission.mission_id}
                  type="button"
                  onClick={() => setSelectedMissionId(mission.mission_id)}
                  style={{
                    textAlign: "left",
                    borderRadius: 12,
                    border: active ? "1px solid #84caff" : "1px solid #eaecf0",
                    background: active ? "#eff8ff" : "#fff",
                    padding: 10,
                    cursor: "pointer",
                    display: "grid",
                    gap: 6,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }}>
                    <div style={{ fontWeight: 700, color: "#101828", fontSize: 12 }}>{mission.mission_id}</div>
                    <span style={statusChip(mission.status)}>{mission.status || "unknown"}</span>
                  </div>
                  <div style={{ fontSize: 12, color: "#475467" }}>{mission.request_text || "-"}</div>
                  <div style={{ fontSize: 11, color: "#667085" }}>Updated: {fmtTs(mission.updated_at)}</div>
                </button>
              );
            })}
          </div>
        </div>

        <div style={{ display: "grid", gap: 14 }}>
          <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Mission State Snapshot</div>
              <div style={{ fontSize: 12, color: "#667085" }}>{selectedMissionId ? `Selected: ${selectedMissionId}` : "No mission selected"}</div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }}>
              <div style={cardStyle}>
                <div style={{ fontSize: 11, color: "#667085" }}>Mission Phase</div>
                <div style={{ fontWeight: 700 }}>{missionPhase}</div>
              </div>
              <div style={cardStyle}>
                <div style={{ fontSize: 11, color: "#667085" }}>Status</div>
                <div><span style={statusChip(typeof stateSnapshot?.status === "string" ? stateSnapshot.status : missionStatus)}>{missionStatus}</span></div>
              </div>
              <div style={cardStyle}>
                <div style={{ fontSize: 11, color: "#667085" }}>Current Step</div>
                <div style={{ fontWeight: 700 }}>{String(currentStep)}</div>
              </div>
              <div style={cardStyle}>
                <div style={{ fontSize: 11, color: "#667085" }}>Updated</div>
                <div style={{ fontWeight: 700, fontSize: 12 }}>{fmtTs(stateSnapshot?.updated_at)}</div>
              </div>
            </div>
            <pre
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                background: "#f8fafc",
                border: "1px solid #eaecf0",
                borderRadius: 10,
                padding: 10,
                fontSize: 12,
                maxHeight: 220,
                overflow: "auto",
              }}
            >
              {JSON.stringify(stateSnapshot, null, 2)}
            </pre>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 14 }}>
            <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Plan</div>
              {renderList(plan, "No plan entries")}
            </div>
            <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Proposed Actions</div>
              {renderList(proposedActions, "No proposed actions")}
            </div>
            <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Applied Actions</div>
              {renderList(appliedActions, "No applied actions")}
            </div>
            <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>Policy Decision Log</div>
              {renderList(decisionLog, "No policy decisions logged")}
            </div>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>A2A Sequence Diagram (Live)</div>
              <div style={{ fontSize: 11, color: "#667085" }}>Source: /protocol-trace/mermaid</div>
            </div>
            {protocolMermaidError ? <div style={{ fontSize: 12, color: "#b42318" }}>{protocolMermaidError}</div> : null}
            <SequenceDiagram mermaidText={protocolMermaid} />
            <details>
              <summary style={{ cursor: "pointer", color: "#344054", fontSize: 12 }}>Show Mermaid Source</summary>
              <pre
                style={{
                  margin: "8px 0 0 0",
                  whiteSpace: "pre-wrap",
                  fontSize: 12,
                  color: "#1d2939",
                  background: "#f8fafc",
                  border: "1px solid #eaecf0",
                  borderRadius: 8,
                  padding: 8,
                  maxHeight: 220,
                  overflow: "auto",
                }}
              >
                {protocolMermaid || "sequenceDiagram\nautonumber"}
              </pre>
            </details>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 10 }}>
            <div style={{ fontSize: 14, fontWeight: 700 }}>Event Timeline</div>
            <div style={{ display: "grid", gap: 8, maxHeight: 360, overflow: "auto" }}>
              {events.length === 0 ? <div style={{ color: "#667085", fontSize: 13 }}>No events</div> : null}
              {events
                .slice()
                .reverse()
                .map((event, idx) => (
                  <div key={`${event.ts || "evt"}-${idx}`} style={{ border: "1px solid #eaecf0", borderRadius: 10, padding: 10, background: "#fcfcfd" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                      <div style={{ fontWeight: 700, fontSize: 12 }}>{event.type || "event"}</div>
                      <div style={{ fontSize: 11, color: "#667085" }}>{fmtTs(event.ts)}</div>
                    </div>
                    <pre style={{ margin: "6px 0 0 0", whiteSpace: "pre-wrap", fontSize: 12, color: "#475467" }}>{JSON.stringify(event.data ?? {}, null, 2)}</pre>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
