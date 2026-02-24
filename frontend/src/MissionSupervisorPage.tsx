import React, { useEffect, useState } from "react";

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

function isObject(x: unknown): x is JsonRecord {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): JsonRecord | null {
  return isObject(x) ? x : null;
}

function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
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

export function MissionSupervisorPage() {
  const [apiBase, setApiBase] = useState("http://127.0.0.1:8023");
  const [requestText, setRequestText] = useState("Coordinate UAV and UTM preflight approval, then optimize network coverage for route.");
  const [metadataText, setMetadataText] = useState('{"operator":"console","priority":"normal"}');
  const [missions, setMissions] = useState<MissionListItem[]>([]);
  const [selectedMissionId, setSelectedMissionId] = useState<string>("");
  const [stateSnapshot, setStateSnapshot] = useState<JsonRecord | null>(null);
  const [events, setEvents] = useState<MissionEvent[]>([]);
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
      const [stateRes, eventsRes] = await Promise.all([
        fetch(`${base}/api/mission/${encodeURIComponent(missionId)}/state`),
        fetch(`${base}/api/mission/${encodeURIComponent(missionId)}/events?limit=200`),
      ]);
      const stateData = (await stateRes.json()) as unknown;
      const eventsData = (await eventsRes.json()) as unknown;
      if (!stateRes.ok) throw new Error(String(asRecord(stateData)?.detail ?? "Mission state request failed"));
      if (!eventsRes.ok) throw new Error(String(asRecord(eventsData)?.detail ?? "Mission events request failed"));
      setStateSnapshot(asRecord(asRecord(stateData)?.result));
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
