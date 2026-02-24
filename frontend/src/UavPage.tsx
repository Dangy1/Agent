import React, { useEffect, useMemo, useState } from "react";
import { MissionSyncMap, type MissionBs, type MissionCoverage, type MissionNfz, type MissionTrack } from "./MissionSyncMap";
import { bumpSharedRevision, getSharedPageState, patchSharedPageState, subscribeSharedPageState } from "./pageSync";

type WaypointAction = "transit" | "photo" | "temperature" | "hover" | "inspect";
type UavWaypoint = { x: number; y: number; z: number; action?: WaypointAction };
type EditableWaypointRow = { x: string; y: string; z: string; action: WaypointAction };
type UavSimState = {
  uav?: Record<string, unknown>;
  utm?: {
    weather?: Record<string, unknown>;
    no_fly_zones?: Array<Record<string, unknown>>;
    regulations?: Record<string, unknown>;
  };
};
type UavEventLogItem = {
  ts: string;
  action: string;
  detail?: string;
};
type CopilotMessage =
  | { id: string; role: "user"; text: string; ts: string }
  | { id: string; role: "assistant"; lines: string[]; toolTrace: Array<Record<string, unknown>>; raw: Record<string, unknown> | null; ts: string; pending?: boolean };

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): Record<string, unknown> | null {
  return isObject(x) ? x : null;
}

function badge(ok: unknown): React.ReactNode {
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

const inputStyle: React.CSSProperties = {
  width: "100%",
  minWidth: 0,
  padding: "6px 8px",
  borderRadius: 8,
  border: "1px solid #d0d5dd",
  fontSize: 12,
};

const cardStyle: React.CSSProperties = {
  background: "#fcfcfd",
  border: "1px solid #eaecf0",
  borderRadius: 12,
  padding: 12,
};

const DEFAULT_SIM_ROUTE: UavWaypoint[] = [
  { x: 0, y: 0, z: 0, action: "transit" },
  { x: 100, y: 50, z: 40, action: "photo" },
  { x: 220, y: 120, z: 55, action: "temperature" },
  { x: 280, y: 180, z: 45, action: "inspect" },
];

const WAYPOINT_ACTIONS: Array<{ value: WaypointAction; label: string }> = [
  { value: "transit", label: "Transit" },
  { value: "photo", label: "Take Photo" },
  { value: "temperature", label: "Measure Temp" },
  { value: "hover", label: "Hover" },
  { value: "inspect", label: "Inspect" },
];

function waypointToRow(wp: UavWaypoint): EditableWaypointRow {
  return { x: String(wp.x), y: String(wp.y), z: String(wp.z), action: (wp.action ?? "transit") as WaypointAction };
}

function isoUtcToLocalInput(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}T${p(dt.getHours())}:${p(dt.getMinutes())}`;
}

function localInputToIsoUtc(value: string): string | null {
  if (!value.trim()) return null;
  const dt = new Date(value);
  return Number.isNaN(dt.getTime()) ? null : dt.toISOString();
}

function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

function normalizeRouteIdBase(routeId: string): string {
  let rid = (routeId || "route-1").trim() || "route-1";
  let changed = true;
  while (changed) {
    changed = false;
    for (const s of ["-replan", "-reschedule"]) {
      if (rid.endsWith(s)) {
        rid = rid.slice(0, -s.length) || "route-1";
        changed = true;
      }
    }
  }
  return rid;
}

function parseCopilotPromptDirectives(prompt: string): {
  cleanedPrompt: string;
  profile: "safe" | "balanced" | "aggressive";
  networkMode: "coverage" | "qos" | "power" | null;
  autoVerify: boolean;
  autoNetworkOptimize: boolean;
} {
  let profile: "safe" | "balanced" | "aggressive" = "balanced";
  let networkMode: "coverage" | "qos" | "power" | null = null;
  let autoVerify = false;
  let autoNetworkOptimize = false;
  let cleaned = prompt;
  if (/@safe\b/i.test(cleaned)) profile = "safe";
  if (/@balanced\b/i.test(cleaned)) profile = "balanced";
  if (/@aggressive\b/i.test(cleaned)) profile = "aggressive";
  if (/@qos\b/i.test(cleaned)) networkMode = "qos";
  else if (/@coverage\b/i.test(cleaned)) networkMode = "coverage";
  else if (/@power\b/i.test(cleaned)) networkMode = "power";
  if (/@verify-on\b/i.test(cleaned) || /@verify\b/i.test(cleaned)) autoVerify = true;
  if (/@network-on\b/i.test(cleaned) || /@network\b/i.test(cleaned)) autoNetworkOptimize = true;
  if (/@verify-off\b/i.test(cleaned)) autoVerify = false;
  if (/@network-off\b/i.test(cleaned)) autoNetworkOptimize = false;
  cleaned = cleaned
    .replace(/@(safe|balanced|aggressive)\b/gi, "")
    .replace(/@(qos|coverage|power)\b/gi, "")
    .replace(/@(verify|verify-on|verify-off|network|network-on|network-off)\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  return { cleanedPrompt: cleaned, profile, networkMode, autoVerify, autoNetworkOptimize };
}

function readSyncRevision(data: unknown): number | null {
  const root = asRecord(data);
  const sync = asRecord(root?.result && asRecord(root.result)?.sync ? asRecord(root.result)?.sync : root?.sync);
  const rev = sync?.revision;
  return typeof rev === "number" ? rev : null;
}

function defaultApprovalWindowLocal() {
  const start = new Date(Date.now() + 2 * 60 * 1000);
  const end = new Date(start.getTime() + 20 * 60 * 1000);
  return { start: isoUtcToLocalInput(start.toISOString()), end: isoUtcToLocalInput(end.toISOString()) };
}

export function UavPage() {
  const sharedInit = getSharedPageState();
  const defaults = defaultApprovalWindowLocal();
  const [uavApiBase, setUavApiBase] = useState(sharedInit.uavApiBase || "http://127.0.0.1:8020");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [simUavId, setSimUavId] = useState(sharedInit.uavId || "uav-1");
  const [simRouteId, setSimRouteId] = useState("demo-route");
  const [simAirspace, setSimAirspace] = useState(sharedInit.airspace || "sector-A3");
  const [simTicks, setSimTicks] = useState("1");
  const [simOperatorLicenseId, setSimOperatorLicenseId] = useState("op-001");
  const [simLicenseClass, setSimLicenseClass] = useState("VLOS");
  const [simRequestedSpeedMps, setSimRequestedSpeedMps] = useState("12");
  const [simPlannedStartAt, setSimPlannedStartAt] = useState(defaults.start);
  const [simPlannedEndAt, setSimPlannedEndAt] = useState(defaults.end);
  const [holdReason, setHoldReason] = useState("operator_request");
  const [replanRequest, setReplanRequest] = useState("avoid nfz on north side and slightly higher altitude");
  const [routeRows, setRouteRows] = useState<EditableWaypointRow[]>(DEFAULT_SIM_ROUTE.map(waypointToRow));
  const [state, setState] = useState<UavSimState | null>(null);
  const [eventLog, setEventLog] = useState<UavEventLogItem[]>([]);
  const [networkMap, setNetworkMap] = useState<{ bs: MissionBs[]; coverage: MissionCoverage[]; tracks: MissionTrack[] }>({ bs: [], coverage: [], tracks: [] });
  const [backendRevisions, setBackendRevisions] = useState<{ uav: number; utm: number; network: number }>({ uav: -1, utm: -1, network: -1 });
  const [agentPrompt, setAgentPrompt] = useState("");
  const [agentOptimizationProfile, setAgentOptimizationProfile] = useState<"safe" | "balanced" | "aggressive">("balanced");
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentConversation, setAgentConversation] = useState<CopilotMessage[]>([]);
  const [agentStatusMsg, setAgentStatusMsg] = useState("");
  const [showAllWaypoints, setShowAllWaypoints] = useState(false);

  const loadState = async () => {
    setBusy(true);
    setMsg("");
    try {
      const base = normalizeBaseUrl(uavApiBase);
      const res = await fetch(`${base}/api/uav/sim/state?uav_id=${encodeURIComponent(simUavId)}`);
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Request failed"));
      setState(data as UavSimState);
      const uavObj = asRecord((data as Record<string, unknown>).uav);
      const backendRouteId = typeof uavObj?.route_id === "string" ? uavObj.route_id : null;
      if (backendRouteId) setSimRouteId(backendRouteId);
      const backendPoints = Array.isArray(uavObj?.waypoints)
        ? (uavObj!.waypoints as unknown[])
            .filter(isObject)
            .map((w) => ({
              x: Number((w as Record<string, unknown>).x ?? 0),
              y: Number((w as Record<string, unknown>).y ?? 0),
              z: Number((w as Record<string, unknown>).z ?? 0),
              action: String((w as Record<string, unknown>).action ?? "transit") as WaypointAction,
            }))
            .filter((w) => Number.isFinite(w.x) && Number.isFinite(w.y) && Number.isFinite(w.z))
        : [];
      const rawWpCount = Array.isArray(uavObj?.waypoints) ? (uavObj!.waypoints as unknown[]).length : -1;
      if (backendPoints.length >= 2 || rawWpCount === 0) {
        setRouteRows(backendPoints.map(waypointToRow));
      }
      setMsg("Loaded UAV state");
      try {
        const shared = getSharedPageState();
        const netRes = await fetch(`${shared.networkApiBase.replace(/\/+$/, "")}/api/network/mission/state?airspace_segment=${encodeURIComponent(simAirspace)}&selected_uav_id=${encodeURIComponent(simUavId)}`);
        const netData = await netRes.json();
        const result = asRecord(asRecord(netData)?.result);
        const bs = Array.isArray(result?.baseStations)
          ? (result!.baseStations as unknown[]).filter(isObject).map((b) => ({
              id: String((b as Record<string, unknown>).id ?? "BS"),
              x: Number((b as Record<string, unknown>).x ?? 0),
              y: Number((b as Record<string, unknown>).y ?? 0),
              status: String((b as Record<string, unknown>).status ?? "online"),
            }))
          : [];
        const coverage = Array.isArray(result?.coverage)
          ? (result!.coverage as unknown[]).filter(isObject).map((c) => ({
              bsId: String((c as Record<string, unknown>).bsId ?? ""),
              radiusM: Number((c as Record<string, unknown>).radiusM ?? 0),
            }))
          : [];
        const tracks = Array.isArray(result?.trackingSnapshots)
          ? (result!.trackingSnapshots as unknown[]).filter(isObject).map((t) => ({
              id: String((t as Record<string, unknown>).id ?? "uav"),
              x: Number((t as Record<string, unknown>).x ?? 0),
              y: Number((t as Record<string, unknown>).y ?? 0),
              z: Number((t as Record<string, unknown>).z ?? 0),
              attachedBsId: String((t as Record<string, unknown>).attachedBsId ?? ""),
              interferenceRisk: String((t as Record<string, unknown>).interferenceRisk ?? "low") as MissionTrack["interferenceRisk"],
            }))
          : [];
        setNetworkMap({ bs, coverage, tracks });
      } catch {
        // keep page functional even when network API is unavailable
      }
    } catch (e) {
      const msgText = e instanceof Error ? e.message : String(e);
      const hint = msgText === "Failed to fetch"
        ? " (check uav_agent.api is running on the URL/port, and restart backend after CORS changes)"
        : "";
      setMsg(`Load failed: ${msgText}${hint}`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void loadState();
  }, []);

  useEffect(() => {
    patchSharedPageState({ uavApiBase, uavId: simUavId, airspace: simAirspace });
  }, [uavApiBase, simUavId, simAirspace]);

  useEffect(() => {
    let lastRevision = getSharedPageState().revision;
    return subscribeSharedPageState((next) => {
      if (next.uavApiBase && next.uavApiBase !== uavApiBase) setUavApiBase(next.uavApiBase);
      if (next.uavId && next.uavId !== simUavId) setSimUavId(next.uavId);
      if (next.airspace && next.airspace !== simAirspace) setSimAirspace(next.airspace);
      if (next.revision !== lastRevision) {
        lastRevision = next.revision;
        void loadState();
      }
    });
  }, [uavApiBase, simUavId, simAirspace]);

  useEffect(() => {
    const id = window.setInterval(() => {
      void (async () => {
        if (busy) return;
        try {
          const shared = getSharedPageState();
          const [uavRes, utmRes, netRes] = await Promise.all([
            fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/sync`),
            fetch(`${normalizeBaseUrl(shared.utmApiBase)}/api/utm/sync`),
            fetch(`${normalizeBaseUrl(shared.networkApiBase)}/api/network/sync`),
          ]);
          const [uavData, utmData, netData] = await Promise.all([uavRes.json(), utmRes.json(), netRes.json()]);
          const next = {
            uav: readSyncRevision(uavData) ?? backendRevisions.uav,
            utm: readSyncRevision(utmData) ?? backendRevisions.utm,
            network: readSyncRevision(netData) ?? backendRevisions.network,
          };
          const changed = next.uav !== backendRevisions.uav || next.utm !== backendRevisions.utm || next.network !== backendRevisions.network;
          if (changed) {
            setBackendRevisions(next);
            if (backendRevisions.uav >= 0) void loadState();
          }
        } catch {
          // optional auto-refresh path
        }
      })();
    }, 1500);
    return () => window.clearInterval(id);
  }, [busy, uavApiBase, backendRevisions, simUavId, simAirspace]);

  const postApi = async (path: string, body?: unknown, successMsg?: string) => {
    setBusy(true);
    setMsg("");
    try {
      const base = normalizeBaseUrl(uavApiBase);
      const res = await fetch(`${base}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body == null ? undefined : JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(String(asRecord(data)?.detail ?? "Request failed"));
      setMsg(successMsg ?? "OK");
      await loadState();
      bumpSharedRevision();
      return data;
    } catch (e) {
      const msgText = e instanceof Error ? e.message : String(e);
      const hint = msgText === "Failed to fetch"
        ? " (check uav_agent.api URL/port, CORS, and whether backend was restarted)"
        : "";
      setMsg(`Action failed: ${msgText}${hint}`);
      setBusy(false);
      return null;
    }
  };

  const logEvent = (action: string, detail?: string) => {
    const ts = new Date().toLocaleTimeString();
    setEventLog((prev) => [{ ts, action, detail }, ...prev].slice(0, 25));
  };

  const routeValidation = useMemo(() => {
    const rowErrors: string[][] = routeRows.map(() => []);
    const waypoints: UavWaypoint[] = [];
    const regs = asRecord(state?.utm?.regulations);
    const maxAlt = typeof regs?.max_altitude_m === "number" ? Number(regs.max_altitude_m) : 120;
    routeRows.forEach((row, idx) => {
      const x = Number(row.x);
      const y = Number(row.y);
      const z = Number(row.z);
      if (!Number.isFinite(x)) rowErrors[idx].push("x");
      if (!Number.isFinite(y)) rowErrors[idx].push("y");
      if (!Number.isFinite(z)) rowErrors[idx].push("z");
      if (Number.isFinite(z) && z < 0) rowErrors[idx].push("z<0");
      if (Number.isFinite(z) && z > maxAlt) rowErrors[idx].push(`z>${maxAlt}`);
      if (rowErrors[idx].length === 0) waypoints.push({ x, y, z, action: row.action || "transit" });
    });
    const errors: string[] = [];
    if (routeRows.length < 2) errors.push("Add at least 2 waypoints.");
    rowErrors.forEach((errs, idx) => errs.length && errors.push(`Waypoint ${idx + 1}: ${errs.join(", ")}`));
    return { rowErrors, waypoints, errors, maxAlt };
  }, [routeRows, state]);

  const planRoute = async () => {
    if (routeValidation.errors.length) {
      setMsg(`Route validation failed: ${routeValidation.errors[0]}`);
      return;
    }
    const routeId = normalizeRouteIdBase(simRouteId);
    setSimRouteId(routeId);
    await postApi("/api/uav/sim/plan", { uav_id: simUavId, route_id: routeId, waypoints: routeValidation.waypoints }, "Route planned");
  };

  const clearRouteForReschedule = async () => {
    const nextRouteId = `${normalizeRouteIdBase(simRouteId)}-reschedule`;
    setRouteRows([]);
    setShowAllWaypoints(false);
    const res = await postApi("/api/uav/sim/plan", { uav_id: simUavId, route_id: nextRouteId, waypoints: [] }, "Route cleared for rescheduling");
    if (res) {
      setSimRouteId(nextRouteId);
      logEvent("route_clear", "Cleared all waypoints for rescheduling");
      setMsg("Route cleared. Add new waypoints on the map or with Add WP.");
    }
  };

  const deleteWaypointAndSync = async (idx: number) => {
    if (busy || routeRows.length <= 2) return;
    const nextRows = routeRows.filter((_, i) => i !== idx);
    setRouteRows(nextRows);

    const regs = asRecord(state?.utm?.regulations);
    const maxAlt = typeof regs?.max_altitude_m === "number" ? Number(regs.max_altitude_m) : 120;
    const nextWaypoints = nextRows
      .map((row) => ({ x: Number(row.x), y: Number(row.y), z: Number(row.z), action: row.action || "transit" }))
      .filter((w) => Number.isFinite(w.x) && Number.isFinite(w.y) && Number.isFinite(w.z))
      .filter((w) => w.z >= 0 && w.z <= maxAlt);
    if (nextWaypoints.length < 2 || nextWaypoints.length !== nextRows.length) {
      setMsg("Waypoint deleted locally. Route has validation issues, so backend was not updated yet.");
      return;
    }

    const res = await postApi(
      "/api/uav/sim/plan",
      { uav_id: simUavId, route_id: normalizeRouteIdBase(simRouteId), waypoints: nextWaypoints },
      "Waypoint deleted and route synchronized",
    );
    if (res) logEvent("route_delete", `Deleted waypoint ${idx + 1} and synced`);
  };

  const submitGeofence = async () => {
    await postApi(`/api/uav/sim/geofence-submit?uav_id=${encodeURIComponent(simUavId)}&airspace_segment=${encodeURIComponent(simAirspace)}`, undefined, "Submitted to geofence check");
  };

  const buildVerifyInput = () => {
    const speed = Number.parseFloat(simRequestedSpeedMps);
    const startIso = localInputToIsoUtc(simPlannedStartAt);
    const endIso = localInputToIsoUtc(simPlannedEndAt);
    if (!Number.isFinite(speed) || speed <= 0) {
      setMsg("Action failed: requested speed must be a positive number");
      return null;
    }
    if (simPlannedStartAt && !startIso) {
      setMsg("Action failed: invalid planned start");
      return null;
    }
    if (simPlannedEndAt && !endIso) {
      setMsg("Action failed: invalid planned end");
      return null;
    }
    return { requested_speed_mps: speed, planned_start_at: startIso, planned_end_at: endIso };
  };

  const requestApproval = async () => {
    const verifyInput = buildVerifyInput();
    if (!verifyInput) return;
    await postApi(
      "/api/uav/sim/request-approval",
      {
        uav_id: simUavId,
        airspace_segment: simAirspace,
        operator_license_id: simOperatorLicenseId,
        required_license_class: simLicenseClass,
        requested_speed_mps: verifyInput.requested_speed_mps,
        planned_start_at: verifyInput.planned_start_at,
        planned_end_at: verifyInput.planned_end_at,
      },
      "UTM approval requested",
    );
  };

  const step = async () => {
    const ticks = Math.max(1, Number.parseInt(simTicks || "1", 10) || 1);
    const res = await postApi("/api/uav/sim/step", { uav_id: simUavId, ticks }, `Stepped ${ticks}`);
    if (res) logEvent("step", `${ticks} tick${ticks === 1 ? "" : "s"}`);
  };
  const launchAndLog = async () => {
    const res = await postApi(`/api/uav/sim/launch?uav_id=${encodeURIComponent(simUavId)}`, undefined, "Launch sent");
    if (res) logEvent("launch");
  };
  const hold = async () => {
    const reason = holdReason.trim() || "operator_request";
    const res = await postApi("/api/uav/sim/hold", { uav_id: simUavId, reason }, "Hold command sent");
    if (res) logEvent("hold", reason);
  };
  const resume = async () => {
    const res = await postApi(`/api/uav/sim/resume?uav_id=${encodeURIComponent(simUavId)}`, undefined, "Resume command sent");
    if (res) logEvent("resume");
  };
  const rth = async () => {
    const res = await postApi(`/api/uav/sim/rth?uav_id=${encodeURIComponent(simUavId)}`, undefined, "Return-to-home sent");
    if (res) logEvent("rth");
  };
  const land = async () => {
    const res = await postApi(`/api/uav/sim/land?uav_id=${encodeURIComponent(simUavId)}`, undefined, "Land command sent");
    if (res) logEvent("land");
  };
  const verifyFromUtm = async () => {
    const verifyInput = buildVerifyInput();
    if (!verifyInput) return null;
    const res = await postApi(
      "/api/utm/verify-from-uav",
      {
        uav_id: simUavId,
        airspace_segment: simAirspace,
        operator_license_id: simOperatorLicenseId,
        required_license_class: simLicenseClass,
        requested_speed_mps: verifyInput.requested_speed_mps,
        planned_start_at: verifyInput.planned_start_at,
        planned_end_at: verifyInput.planned_end_at,
      },
      "UTM verify-from-UAV completed",
    );
    if (res) {
      const result = asRecord(asRecord(res)?.result);
      logEvent("verify", `approved=${String(result?.approved ?? false)}`);
    }
    return res;
  };
  const replanViaUtmNfz = async () => {
    if (routeValidation.errors.length) {
      setMsg(`Route validation failed: ${routeValidation.errors[0]}`);
      return null;
    }
    const res = await postApi(
      "/api/uav/sim/replan-via-utm-nfz",
      {
        uav_id: simUavId,
        airspace_segment: simAirspace,
        user_request: replanRequest,
        route_id: normalizeRouteIdBase(simRouteId),
        waypoints: routeValidation.waypoints,
        optimization_profile: agentOptimizationProfile,
      },
      "Route replanned via UTM NFZ",
    );
    if (res) {
      const obj = asRecord(res);
      const result = asRecord(obj?.result);
      const changes = Array.isArray(result?.changes) ? (result?.changes as unknown[]) : [];
      logEvent("replan_nfz", `${changes.length} waypoint change${changes.length === 1 ? "" : "s"}`);
      const replannedUav = asRecord(result?.uav);
      const points = Array.isArray(replannedUav?.waypoints)
        ? (replannedUav!.waypoints as unknown[])
            .filter(isObject)
            .map((w) => ({
              x: Number((w as Record<string, unknown>).x ?? 0),
              y: Number((w as Record<string, unknown>).y ?? 0),
              z: Number((w as Record<string, unknown>).z ?? 0),
              action: String((w as Record<string, unknown>).action ?? "transit") as WaypointAction,
            }))
        : [];
      if (points.length >= 2) {
        setRouteRows(points.map(waypointToRow));
        const nextRouteId = typeof result?.route_id === "string" ? result.route_id : null;
        if (nextRouteId) setSimRouteId(nextRouteId);
      }
    }
    return res;
  };
  const replanGeofenceVerify = async () => {
    const replanRes = await replanViaUtmNfz();
    if (!replanRes) return;
    const geofenceRes = await postApi(
      `/api/uav/sim/geofence-submit?uav_id=${encodeURIComponent(simUavId)}&airspace_segment=${encodeURIComponent(simAirspace)}`,
      undefined,
      "Geofence check completed",
    );
    if (!geofenceRes) return;
    const geofenceResult = asRecord(asRecord(geofenceRes)?.result);
    const geofenceObj = asRecord(geofenceResult?.geofence);
    logEvent("geofence", `ok=${String(geofenceObj?.ok ?? false)}`);
    const verifyRes = await verifyFromUtm();
    if (!verifyRes) return;
    const verifyObj = asRecord(asRecord(verifyRes)?.result);
    logEvent("workflow", `geofence=${String(geofenceObj?.ok ?? "-")} verify=${String(verifyObj?.approved ?? "-")}`);
    setMsg(`Workflow done: geofence=${String(geofenceObj?.ok ?? "-")} verify=${String(verifyObj?.approved ?? "-")}`);
  };

  const runAgentCopilot = async () => {
    const parsed = parseCopilotPromptDirectives(agentPrompt);
    const outgoingPrompt = parsed.cleanedPrompt;
    const effectiveProfile = parsed.profile;
    const effectiveNetworkMode = parsed.networkMode;
    const effectiveAutoVerify = parsed.autoVerify;
    const effectiveAutoNetwork = parsed.autoNetworkOptimize;
    const looksLikeRouteAction = /\b(replan|route|path|waypoint|nfz|no[- ]?fly|detour|optimi[sz]e)\b/i.test(outgoingPrompt);
    const looksLikeNetworkAction = /\b(network|coverage|qos|latency|signal|sinr|power)\b/i.test(outgoingPrompt);
    const hasActionIntent = looksLikeRouteAction || looksLikeNetworkAction || effectiveAutoVerify || effectiveAutoNetwork;
    if (!outgoingPrompt) {
      setAgentStatusMsg("Enter a prompt. Supports @safe/@balanced/@aggressive, @qos/@coverage/@power, @verify/@verify-off, @network/@network-off.");
      return;
    }
    if (routeValidation.errors.length) {
      setAgentStatusMsg(`Route validation failed: ${routeValidation.errors[0]}`);
      return;
    }
    setAgentStatusMsg("");
    setAgentOptimizationProfile(effectiveProfile);
    setAgentBusy(true);
    const ts = new Date().toLocaleTimeString();
    const pendingId = `a-pending-${Date.now()}`;
    const userMsg: CopilotMessage = { id: `u-${Date.now()}`, role: "user", text: outgoingPrompt + (effectiveProfile !== "balanced" ? ` (${effectiveProfile})` : ""), ts };
    const pendingMsg: CopilotMessage = {
      id: pendingId,
      role: "assistant",
      lines: [hasActionIntent ? "Starting copilot workflow..." : "Thinking..."],
      toolTrace: [],
      raw: null,
      ts,
      pending: true,
    };
    setAgentConversation((prev) => [...prev, userMsg, pendingMsg].slice(-24));
    const progressSteps = hasActionIntent
      ? [
          "Analyzing mission context (route, UTM, network, UAV state)...",
          "Planning tool actions...",
          "Executing selected actions and collecting results...",
          "Summarizing outcome...",
        ]
      : [
          "Reading your message...",
          "Checking mission context...",
          "Preparing a response...",
        ];
    progressSteps.forEach((_, i) => {
      window.setTimeout(() => {
        setAgentConversation((prev) =>
          prev.map((m) =>
            m.id === pendingId && m.role === "assistant" && m.pending
              ? { ...m, lines: progressSteps.slice(0, i + 1) }
              : m,
          ),
        );
      }, 180 + i * 240);
    });
    try {
      const base = normalizeBaseUrl(uavApiBase);
      const res = await fetch(`${base}/api/uav/agent/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          uav_id: simUavId,
          airspace_segment: simAirspace,
          prompt: outgoingPrompt,
          route_id: normalizeRouteIdBase(simRouteId),
          waypoints: routeValidation.waypoints,
          optimization_profile: effectiveProfile,
          network_mode: effectiveNetworkMode,
          auto_verify: effectiveAutoVerify,
          auto_network_optimize: effectiveAutoNetwork,
        }),
      });
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Agent request failed"));
      const result = asRecord((data as Record<string, unknown>).result);
      const msgs = Array.isArray(result?.messages) ? (result!.messages as unknown[]).map((m) => String(m)) : [];
      const trace = Array.isArray(result?.toolTrace)
        ? (result!.toolTrace as unknown[]).filter(isObject).map((t) => t as Record<string, unknown>)
        : [];
      const assistantMsg: CopilotMessage = { id: `a-${Date.now()}-1`, role: "assistant", lines: msgs, toolTrace: trace, raw: result, ts };
      setAgentConversation((prev) => [...prev.filter((m) => m.id !== pendingId), assistantMsg].slice(-24));
      setAgentPrompt("");
      const uavObj = asRecord(result?.uav);
      const nextRouteId = typeof uavObj?.route_id === "string" ? uavObj.route_id : null;
      const points = Array.isArray(uavObj?.waypoints)
        ? (uavObj!.waypoints as unknown[])
            .filter(isObject)
            .map((w) => ({
              x: Number((w as Record<string, unknown>).x ?? 0),
              y: Number((w as Record<string, unknown>).y ?? 0),
              z: Number((w as Record<string, unknown>).z ?? 0),
              action: String((w as Record<string, unknown>).action ?? "transit") as WaypointAction,
            }))
        : [];
      if (nextRouteId) setSimRouteId(nextRouteId);
      if (points.length >= 2) setRouteRows(points.map(waypointToRow));
      logEvent("agent_copilot", msgs.join(" | ") || "Agent workflow completed");
      await loadState();
      bumpSharedRevision();
      setAgentStatusMsg("Agent copilot completed.");
    } catch (e) {
      setAgentStatusMsg(`Agent copilot failed: ${e instanceof Error ? e.message : String(e)}`);
      const errMsg: CopilotMessage = {
        id: pendingId,
        role: "assistant",
        lines: [`Error: ${e instanceof Error ? e.message : String(e)}`],
        toolTrace: [],
        raw: null,
        ts: new Date().toLocaleTimeString(),
        pending: false,
      };
      setAgentConversation((prev) => [...prev.filter((m) => m.id !== pendingId), errMsg].slice(-24));
    } finally {
      setAgentBusy(false);
    }
  };

  const uav = asRecord(state?.uav);
  const approval = asRecord(uav?.utm_approval);
  const checks = asRecord(approval?.checks);
  const geofence = asRecord(uav?.utm_geofence_result);
  const utmObj = asRecord(state?.utm);
  const nfzZones: MissionNfz[] = Array.isArray(utmObj?.no_fly_zones)
    ? (utmObj!.no_fly_zones as unknown[])
        .filter(isObject)
        .map((z) => ({
          zone_id: String((z as Record<string, unknown>).zone_id ?? "nfz"),
          cx: Number((z as Record<string, unknown>).cx ?? 0),
          cy: Number((z as Record<string, unknown>).cy ?? 0),
          radius_m: Number((z as Record<string, unknown>).radius_m ?? 0),
          z_min: Number((z as Record<string, unknown>).z_min ?? 0),
          z_max: Number((z as Record<string, unknown>).z_max ?? 120),
          reason: String((z as Record<string, unknown>).reason ?? ""),
        }))
    : [];
  const routePointsForMap = routeValidation.waypoints.map((w) => ({ x: w.x, y: w.y, z: w.z }));
  const visibleRouteRows = showAllWaypoints ? routeRows : routeRows.slice(0, 5);
  const plannedPosForMap = routePointsForMap.length > 0 ? routePointsForMap[0] : null;

  return (
    <div style={{ display: "grid", gap: 12, padding: 14, maxWidth: 1280, margin: "0 auto" }}>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 6, maxHeight: 350, overflowY: "auto" }}>
          <div style={{ fontWeight: 700, color: "#101828" }}>UAV Agent Simulator Console</div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.35fr) minmax(0,0.8fr) minmax(0,0.95fr)", gap: 6 }}>
            <label style={{ fontSize: 12, minWidth: 0 }}>Simulator API URL<input style={{ ...inputStyle }} value={uavApiBase} onChange={(e) => setUavApiBase(e.target.value)} /></label>
            <label style={{ fontSize: 12, minWidth: 0 }}>UAV ID<input style={{ ...inputStyle, maxWidth: 160 }} value={simUavId} onChange={(e) => setSimUavId(e.target.value)} /></label>
            <label style={{ fontSize: 12, minWidth: 0 }}>Route ID
              <input style={inputStyle} value={simRouteId} onChange={(e) => setSimRouteId(e.target.value)} />
              <div style={{ fontSize: 10, color: "#667085", marginTop: 2 }}>Route version/name used by UAV, UTM approval, and replans.</div>
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.15fr) minmax(72px,0.55fr) minmax(0,1fr) minmax(92px,0.75fr)", gap: 6 }}>
            <label style={{ fontSize: 12, minWidth: 0 }}>Airspace<input style={inputStyle} value={simAirspace} onChange={(e) => setSimAirspace(e.target.value)} /></label>
            <label style={{ fontSize: 12, minWidth: 0 }}>Ticks<input style={{ ...inputStyle, maxWidth: 84 }} value={simTicks} onChange={(e) => setSimTicks(e.target.value)} /></label>
            <label style={{ fontSize: 12, minWidth: 0 }}>Operator License<input style={inputStyle} value={simOperatorLicenseId} onChange={(e) => setSimOperatorLicenseId(e.target.value)} /></label>
            <label style={{ fontSize: 12, minWidth: 0 }}>License Class
              <select style={inputStyle} value={simLicenseClass} onChange={(e) => setSimLicenseClass(e.target.value)}>
                <option value="VLOS">VLOS</option>
                <option value="BVLOS">BVLOS</option>
              </select>
            </label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr) minmax(120px,0.7fr)", gap: 6 }}>
            <label style={{ fontSize: 12, minWidth: 0 }}>Planned Start<input type="datetime-local" style={inputStyle} value={simPlannedStartAt} onChange={(e) => setSimPlannedStartAt(e.target.value)} /></label>
            <label style={{ fontSize: 12, minWidth: 0 }}>Planned End<input type="datetime-local" style={inputStyle} value={simPlannedEndAt} onChange={(e) => setSimPlannedEndAt(e.target.value)} /></label>
            <label style={{ fontSize: 12, minWidth: 0 }}>Requested Speed (m/s)<input style={{ ...inputStyle, maxWidth: 140 }} value={simRequestedSpeedMps} onChange={(e) => setSimRequestedSpeedMps(e.target.value)} /></label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6, alignItems: "end" }}>
            <label style={{ fontSize: 12 }}>Hold Reason<input style={inputStyle} value={holdReason} onChange={(e) => setHoldReason(e.target.value)} /></label>
            <div style={{ fontSize: 11, color: "#667085", paddingBottom: 6 }}>Used by `HOLD` command.</div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6, alignItems: "end" }}>
            <label style={{ fontSize: 12 }}>Replan Request (UTM NFZ aware)<input style={inputStyle} value={replanRequest} onChange={(e) => setReplanRequest(e.target.value)} /></label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
              <button type="button" style={chipStyle(false)} onClick={() => void replanViaUtmNfz()} disabled={busy}>Replan NFZ</button>
              <button type="button" style={chipStyle(false)} onClick={() => void replanGeofenceVerify()} disabled={busy}>Replan+Verify</button>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6, alignItems: "center" }}>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <button type="button" style={chipStyle(false)} onClick={() => void loadState()} disabled={busy}>Refresh</button>
            <button type="button" style={chipStyle(false)} onClick={() => void planRoute()} disabled={busy}>Plan</button>
            <button type="button" style={chipStyle(false)} onClick={() => void submitGeofence()} disabled={busy}>Geofence</button>
            <button type="button" style={chipStyle(false)} onClick={() => void verifyFromUtm()} disabled={busy}>Verify</button>
            <button type="button" style={chipStyle(false)} onClick={() => void requestApproval()} disabled={busy}>Approval</button>
            <button type="button" style={chipStyle(false)} onClick={() => void launchAndLog()} disabled={busy}>Launch</button>
            <button type="button" style={chipStyle(false)} onClick={() => void step()} disabled={busy}>Step</button>
            <button type="button" style={chipStyle(false)} onClick={() => void hold()} disabled={busy}>Hold</button>
            <button type="button" style={chipStyle(false)} onClick={() => void resume()} disabled={busy}>Resume</button>
            <button type="button" style={chipStyle(false)} onClick={() => void rth()} disabled={busy}>RTH</button>
            <button type="button" style={chipStyle(false)} onClick={() => void land()} disabled={busy}>Land</button>
            </div>
            <div style={{ fontSize: 12, textAlign: "right", color: msg.toLowerCase().includes("failed") ? "#b42318" : "#475467" }}>{msg || ""}</div>
          </div>
        </div>

        <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8, alignContent: "start", minWidth: 0 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, alignItems: "center" }}>
            <div>
              <div style={{ fontWeight: 700, color: "#101828" }}>Agent Copilot</div>
              <div style={{ fontSize: 11, color: "#667085" }}>Path planning + UTM/NFZ + network coverage optimization (chat mode)</div>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <button type="button" style={chipStyle(false)} onClick={() => setAgentConversation([])} disabled={agentBusy}>Clear</button>
            </div>
          </div>
          <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", minHeight: 320, maxHeight: 390, display: "grid", gridTemplateRows: "1fr auto auto", overflow: "hidden", minWidth: 0 }}>
            <div style={{ overflow: "auto", padding: 8, display: "grid", gap: 8, alignContent: "start" }}>
              {agentConversation.length === 0 ? (
                <div style={{ border: "1px dashed #d0d5dd", borderRadius: 10, background: "#fcfcfd", padding: 10, display: "grid", gap: 6 }}>
                  <div style={{ fontSize: 12, color: "#344054", fontWeight: 700 }}>Start the conversation</div>
                  <div style={{ fontSize: 12, color: "#667085" }}>
                    Ask the UAV flight agent to redesign the route using UTM no-fly zones and network coverage. Replies show tool/action steps used by backend algorithms (not hidden reasoning).
                  </div>
                  <div style={{ fontSize: 11, color: "#667085" }}>
                    `@` directives: `@safe`, `@balanced`, `@aggressive`, `@qos`, `@coverage`, `@power`, `@verify`, `@verify-off`, `@network`, `@network-off`.
                    Example: `@aggressive @qos @verify @network optimize route for video QoS and remove unnecessary transit points`
                  </div>
                </div>
              ) : (
                agentConversation.map((m) =>
                  m.role === "user" ? (
                    <div key={m.id} style={{ justifySelf: "end", maxWidth: "94%", background: "#eef4ff", border: "1px solid #c7d7fe", borderRadius: 10, padding: "8px 10px" }}>
                      <div style={{ fontSize: 10, color: "#155eef", fontWeight: 700, marginBottom: 4 }}>You • {m.ts}</div>
                      <div style={{ fontSize: 12, color: "#1d2939", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{m.text}</div>
                    </div>
                  ) : (
                    <div key={m.id} style={{ justifySelf: "stretch", border: m.pending ? "1px solid #c7d7fe" : "1px solid #e4e7ec", background: m.pending ? "#f5f9ff" : "#f8fafc", borderRadius: 10, padding: "8px 10px" }}>
                      <div style={{ fontSize: 10, color: "#667085", fontWeight: 700, marginBottom: 6 }}>Agent • {m.ts}</div>
                      <div style={{ display: "grid", gap: 4 }}>
                        {m.lines.map((line, i) => <div key={`${m.id}-line-${i}`} style={{ fontSize: 12, color: m.pending ? "#155eef" : "#344054", fontStyle: m.pending ? "italic" : "normal" }}>{line}</div>)}
                      </div>
                      {!m.pending && m.toolTrace.length ? (
                        <details style={{ marginTop: 8 }}>
                          <summary style={{ cursor: "pointer", fontSize: 11, color: "#155eef", fontWeight: 700 }}>Action / Tool Steps</summary>
                          <div style={{ display: "grid", gap: 4, marginTop: 6 }}>
                            {m.toolTrace.map((t, i) => (
                              <div key={`${m.id}-trace-${i}`} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", fontSize: 11, color: "#344054" }}>
                                <code>{String(t.tool ?? "step")}</code>
                                <span style={{ marginLeft: 6, color: String(t.status ?? "") === "success" ? "#027a48" : "#b42318" }}>{String(t.status ?? "")}</span>
                                {"profile" in t ? <span style={{ marginLeft: 6, color: "#667085" }}>profile={String(t.profile)}</span> : null}
                                {"mode" in t ? <span style={{ marginLeft: 6, color: "#667085" }}>mode={String(t.mode)}</span> : null}
                                {"approved" in t ? <span style={{ marginLeft: 6, color: "#667085" }}>approved={String(t.approved)}</span> : null}
                              </div>
                            ))}
                          </div>
                        </details>
                      ) : null}
                      {!m.pending && m.raw ? (
                        <details style={{ marginTop: 8 }}>
                          <summary style={{ cursor: "pointer", fontSize: 11, color: "#155eef", fontWeight: 700 }}>Raw Agent JSON</summary>
                          <pre style={{ margin: "6px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>{JSON.stringify(m.raw, null, 2)}</pre>
                        </details>
                      ) : null}
                    </div>
                  ),
                )
              )}
            </div>
            <div style={{ borderTop: "1px solid #eaecf0", padding: 8, display: "grid", gap: 6 }}>
              <textarea
                value={agentPrompt}
                onChange={(e) => setAgentPrompt(e.target.value)}
                rows={3}
                style={{ ...inputStyle, resize: "vertical", minHeight: 60, maxHeight: 96, fontFamily: "inherit", minWidth: 0 }}
                placeholder="Ask the agent... (use @safe | @balanced | @aggressive | @verify | @network)"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (!agentBusy && !busy) void runAgentCopilot();
                  }
                }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", fontSize: 12 }}>
                <div style={{ color: agentStatusMsg ? (agentStatusMsg.toLowerCase().includes("failed") || agentStatusMsg.toLowerCase().includes("validation") ? "#b42318" : "#155eef") : (agentBusy ? "#155eef" : "#667085") }}>
                  {agentStatusMsg || `${agentBusy ? "Working..." : "Ready"} • Profile: ${agentOptimizationProfile}`}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ color: "#667085" }}>Conversation + tool actions</div>
                  <button
                    type="button"
                    style={{ ...chipStyle(false), borderColor: "#155eef", background: "#eef4ff", color: "#155eef", fontWeight: 700 }}
                    onClick={() => void runAgentCopilot()}
                    disabled={busy || agentBusy || !agentPrompt.trim()}
                  >
                    Send
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)" }}>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>Waypoint Editor + Path Planning</div>
            <MissionSyncMap
              title="Path Planning Map (Click to Add Waypoint)"
              route={routePointsForMap}
              plannedPosition={plannedPosForMap}
              trackedPositions={networkMap.tracks}
              selectedUavId={simUavId}
              noFlyZones={nfzZones}
              baseStations={networkMap.bs}
              coverage={networkMap.coverage}
              clickable
              onAddWaypoint={(p) => {
                const currentPos = asRecord(uav?.position);
                const currentWp = currentPos
                  ? {
                      x: Number(currentPos.x ?? 0),
                      y: Number(currentPos.y ?? 0),
                      z: Number(currentPos.z ?? 0),
                    }
                  : null;
                const z = routeRows.length ? Number(routeRows[routeRows.length - 1]?.z || "40") : Number(currentWp?.z ?? 40);
                setRouteRows((rows) => {
                  const next = rows.slice();
                  if (next.length === 0 && currentWp && Number.isFinite(currentWp.x) && Number.isFinite(currentWp.y) && Number.isFinite(currentWp.z)) {
                    next.push({
                      x: String(currentWp.x),
                      y: String(currentWp.y),
                      z: String(currentWp.z),
                      action: "transit",
                    });
                  }
                  next.push({ x: String(p.x), y: String(p.y), z: String(Number.isFinite(z) ? z : 40), action: "transit" });
                  return next;
                });
              }}
            />
            <div style={{ display: "grid", gap: 5, maxHeight: 250, overflowY: "auto" }}>
              {visibleRouteRows.map((row, idx) => {
                const errs = routeValidation.rowErrors[idx] ?? [];
                return (
                  <div key={`row-${idx}`} style={{ display: "grid", gridTemplateColumns: "24px 1fr 1fr 1fr 130px 42px", gap: 6, alignItems: "center" }}>
                    <div style={{ fontSize: 11, color: "#667085", textAlign: "center" }}>{idx + 1}</div>
                    <input style={{ ...inputStyle, borderColor: errs.some((e) => e.startsWith("x")) ? "#f04438" : "#d0d5dd" }} value={row.x} onChange={(e) => setRouteRows((rows) => rows.map((r, i) => (i === idx ? { ...r, x: e.target.value } : r)))} placeholder="x" />
                    <input style={{ ...inputStyle, borderColor: errs.some((e) => e.startsWith("y")) ? "#f04438" : "#d0d5dd" }} value={row.y} onChange={(e) => setRouteRows((rows) => rows.map((r, i) => (i === idx ? { ...r, y: e.target.value } : r)))} placeholder="y" />
                    <input style={{ ...inputStyle, borderColor: errs.some((e) => e.startsWith("z")) ? "#f04438" : "#d0d5dd" }} value={row.z} onChange={(e) => setRouteRows((rows) => rows.map((r, i) => (i === idx ? { ...r, z: e.target.value } : r)))} placeholder="z" />
                    <select style={{ ...inputStyle, padding: "5px 6px" }} value={row.action} onChange={(e) => setRouteRows((rows) => rows.map((r, i) => (i === idx ? { ...r, action: e.target.value as WaypointAction } : r)))}>
                      {WAYPOINT_ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
                    </select>
                    <button type="button" style={chipStyle(false)} disabled={busy || routeRows.length <= 2} onClick={() => void deleteWaypointAndSync(idx)}>Del</button>
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 11, color: "#667085", alignItems: "center" }}>
              <div>Waypoints: {routeRows.length}. Showing {Math.min(routeRows.length, showAllWaypoints ? routeRows.length : 5)}.</div>
              {routeRows.length > 5 ? (
                <button type="button" style={chipStyle(false)} onClick={() => setShowAllWaypoints((v) => !v)}>
                  {showAllWaypoints ? "Show 5" : `Show All (${routeRows.length})`}
                </button>
              ) : <span />}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button type="button" style={chipStyle(false)} onClick={() => setRouteRows((rows) => rows.concat([{ x: "0", y: "0", z: "20", action: "transit" }]))}>Add WP</button>
              <button type="button" style={chipStyle(false)} onClick={() => void clearRouteForReschedule()} disabled={busy}>Clear for Reschedule</button>
              <button type="button" style={chipStyle(false)} onClick={() => setRouteRows(DEFAULT_SIM_ROUTE.map(waypointToRow))}>Reset Route</button>
              <button
                type="button"
                style={chipStyle(false)}
                onClick={() => {
                  const points = Array.isArray(uav?.waypoints)
                    ? (uav.waypoints as unknown[])
                        .filter(isObject)
                        .map((w) => ({
                          x: Number((w as Record<string, unknown>).x ?? 0),
                          y: Number((w as Record<string, unknown>).y ?? 0),
                          z: Number((w as Record<string, unknown>).z ?? 0),
                          action: String((w as Record<string, unknown>).action ?? "transit") as WaypointAction,
                        }))
                    : [];
                  setRouteRows(points.map(waypointToRow));
                }}
              >
                Load Current
              </button>
              <div style={{ marginLeft: "auto", fontSize: 12, color: routeValidation.errors.length ? "#b42318" : "#475467" }}>
                {routeValidation.errors[0] ?? `Route valid. Max altitude: ${routeValidation.maxAlt}m`}
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, padding: 10 }}>
            <div style={{ fontWeight: 700, color: "#101828", marginBottom: 8 }}>UAV + UTM Status</div>
            <div style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 4, fontSize: 12 }}>
              <div style={{ color: "#667085" }}>Phase</div><div><code>{String(uav?.flight_phase ?? "-")}</code></div>
              <div style={{ color: "#667085" }}>Battery</div><div>{String(uav?.battery_pct ?? "-")}%</div>
              <div style={{ color: "#667085" }}>Waypoint</div><div>{String(uav?.waypoint_index ?? "-")} / {String(uav?.waypoints_total ?? "-")}</div>
              <div style={{ color: "#667085" }}>Geofence</div><div>{badge(geofence?.ok)}</div>
              <div style={{ color: "#667085" }}>UTM Approval</div><div>{badge(approval?.approved)}</div>
              <div style={{ color: "#667085" }}>Weather</div><div>{badge(asRecord(checks?.weather)?.ok)}</div>
              <div style={{ color: "#667085" }}>NFZ</div><div>{badge(asRecord(checks?.no_fly_zone)?.ok)}</div>
              <div style={{ color: "#667085" }}>Regulations</div><div>{badge(asRecord(checks?.regulations)?.ok)}</div>
              <div style={{ color: "#667085" }}>Time Window</div><div>{badge(asRecord(checks?.time_window)?.ok)}</div>
              <div style={{ color: "#667085" }}>Operator License</div><div>{badge(asRecord(checks?.operator_license)?.ok)}</div>
            </div>
          </div>

          <div style={{ ...cardStyle, padding: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, gap: 8 }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>Event Log</div>
              <button type="button" style={chipStyle(false)} onClick={() => setEventLog([])} disabled={busy || eventLog.length === 0}>Clear</button>
            </div>
            {eventLog.length === 0 ? (
              <div style={{ fontSize: 12, color: "#667085" }}>No events yet. Launch/step/hold/resume/rth/land/replan actions will appear here.</div>
            ) : (
              <div style={{ display: "grid", gap: 6, maxHeight: 420, overflowY: "auto" }}>
                {eventLog.map((e, i) => (
                  <div
                    key={`${e.ts}-${e.action}-${i}`}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "78px minmax(82px, auto) minmax(0, 1fr)",
                      gap: 8,
                      alignItems: "start",
                      border: "1px solid #eaecf0",
                      borderRadius: 8,
                      padding: "6px 8px",
                      background: "#fff",
                      fontSize: 12,
                    }}
                  >
                    <code style={{ color: "#475467", whiteSpace: "nowrap" }}>{e.ts}</code>
                    <code style={{ color: "#155eef", whiteSpace: "normal", overflowWrap: "anywhere" }}>{e.action}</code>
                    <div style={{ color: "#344054", whiteSpace: "pre-wrap", wordBreak: "break-word", minWidth: 0 }}>{e.detail || "-"}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
