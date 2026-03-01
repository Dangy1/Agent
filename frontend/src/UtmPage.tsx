import React, { useEffect, useMemo, useState } from "react";
import { MissionSyncMap, type MissionBs, type MissionCoverage, type MissionNfz, type MissionTrack } from "./MissionSyncMap";
import { bumpSharedRevision, getSharedPageState, patchSharedPageState, subscribeSharedPageState } from "./pageSync";
import { shouldAutoReplanForDssConflict } from "./missionSubmitFlow";

type UavSimState = {
  uav?: Record<string, unknown>;
  fleet?: Record<string, Record<string, unknown>>;
  uav_registry_user?: Record<string, unknown>;
  utm?: {
    weather?: Record<string, unknown>;
    no_fly_zones?: Array<Record<string, unknown>>;
    regulations?: Record<string, unknown>;
    licenses?: Record<string, unknown>;
    effective_regulations?: Record<string, unknown>;
  };
};

type UtmStateResult = {
  weather?: Record<string, unknown>;
  weatherChecks?: Record<string, unknown>;
  noFlyZones?: Array<Record<string, unknown>>;
  regulations?: Record<string, unknown>;
  regulationProfiles?: Record<string, unknown>;
  effectiveRegulations?: Record<string, unknown>;
  licenses?: Record<string, unknown>;
  dss?: Record<string, unknown>;
};

type UtmCheckResults = {
  route?: Record<string, unknown>;
  timeWindow?: Record<string, unknown>;
  license?: Record<string, unknown>;
  verify?: Record<string, unknown>;
  corridor?: Record<string, unknown>;
};

type UtmSourceInfo = { mode?: string; active?: string; meta?: Record<string, unknown> | null };

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): Record<string, unknown> | null {
  return isObject(x) ? x : null;
}

function asArrayRecords(x: unknown): Record<string, unknown>[] {
  return Array.isArray(x) ? x.filter(isObject).map((r) => ({ ...r })) : [];
}

function formatApiErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  const rec = asRecord(detail);
  if (!rec) return String(detail ?? "Request failed");
  const code = typeof rec.error === "string" ? rec.error : null;
  const issues = Array.isArray(rec.issues) ? rec.issues.map(String).filter(Boolean) : [];
  if (issues.length) return `${code ?? "Request failed"}: ${issues.join("; ")}`;
  try {
    return JSON.stringify(rec);
  } catch {
    return code ?? "Request failed";
  }
}

function assertApiPayloadOk(data: unknown): void {
  const root = asRecord(data);
  if (!root) return;
  const status = String(root.status ?? "").trim().toLowerCase();
  if (!status || ["success", "warning", "ok"].includes(status)) return;
  const nested = asRecord(root.result);
  const detail = root.detail ?? root.error ?? nested?.detail ?? nested?.error ?? root;
  throw new Error(formatApiErrorDetail(detail));
}

function normalizeBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

function utmAuthHeaders(token: string, includeJson = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (includeJson) headers["Content-Type"] = "application/json";
  const trimmed = token.trim();
  if (trimmed) headers.Authorization = `Bearer ${trimmed}`;
  return headers;
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

function statusBadge(status: unknown): React.ReactNode {
  const value = String(status ?? "").trim().toLowerCase();
  const color =
    value === "ready"
      ? { bg: "#ecfdf3", fg: "#027a48", bd: "#abefc6" }
      : value === "blocked"
      ? { bg: "#fef3f2", fg: "#b42318", bd: "#fecdca" }
      : value === "attention"
      ? { bg: "#fffaeb", fg: "#b54708", bd: "#fedf89" }
      : { bg: "#f2f4f7", fg: "#475467", bd: "#d0d5dd" };
  return (
    <span
      style={{
        display: "inline-block",
        borderRadius: 999,
        padding: "2px 8px",
        fontSize: 11,
        fontWeight: 700,
        background: color.bg,
        color: color.fg,
        border: `1px solid ${color.bd}`,
      }}
    >
      {value ? value.toUpperCase() : "UNKNOWN"}
    </span>
  );
}

function trimValue(value: unknown, max = 28): string {
  const text = String(value ?? "");
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

function isLocalUssManager(managerUssId: unknown): boolean {
  return String(managerUssId ?? "").trim().toLowerCase().startsWith("uss-local");
}

const inputStyle: React.CSSProperties = {
  width: "100%",
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

function decisionPanel(decisionInput: unknown): React.ReactNode {
  const decision = asRecord(decisionInput);
  if (!decision) return null;
  const status = String(decision.status ?? "-").toLowerCase();
  const approved = status === "approved";
  const reasons = Array.isArray(decision.reasons) ? (decision.reasons as unknown[]).map(String).slice(0, 4) : [];
  const messages = Array.isArray(decision.messages) ? (decision.messages as unknown[]).map(String) : [];
  return (
    <div
      style={{
        border: `1px solid ${approved ? "#abefc6" : "#fecdca"}`,
        borderRadius: 8,
        background: approved ? "#ecfdf3" : "#fef3f2",
        padding: 8,
        display: "grid",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <span
          style={{
            display: "inline-block",
            borderRadius: 999,
            padding: "2px 8px",
            fontSize: 11,
            fontWeight: 700,
            background: approved ? "#d1fadf" : "#fee4e2",
            color: approved ? "#027a48" : "#b42318",
            border: `1px solid ${approved ? "#abefc6" : "#fecdca"}`,
          }}
        >
          {String(decision.status ?? "-").toUpperCase()}
        </span>
        {reasons.map((r, i) => (
          <span key={`${r}-${i}`} style={{ fontSize: 11, color: "#667085", border: "1px solid #d0d5dd", borderRadius: 999, padding: "2px 8px", background: "#fff" }}>
            {r}
          </span>
        ))}
      </div>
      {messages[0] ? <div style={{ fontSize: 12, color: "#344054" }}>{messages[0]}</div> : null}
    </div>
  );
}

export function UtmPage() {
  const sharedInit = getSharedPageState();

  const [apiBase, setApiBase] = useState(sharedInit.utmApiBase || "http://127.0.0.1:8021");
  const [uavApiBase, setUavApiBase] = useState(sharedInit.uavApiBase || "http://127.0.0.1:8020");
  const [utmAuthToken, setUtmAuthToken] = useState(sharedInit.utmAuthToken || "local-dev-token");
  const [simUavId, setSimUavId] = useState(sharedInit.uavId || "uav-1");
  const [airspace, setAirspace] = useState(sharedInit.airspace || "sector-A3");

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [liveRefresh, setLiveRefresh] = useState(false);
  const [liveRefreshSec, setLiveRefreshSec] = useState("3");
  const [missionControlExpanded, setMissionControlExpanded] = useState(false);
  const [selectedMapUserId, setSelectedMapUserId] = useState("all");

  const [simState, setSimState] = useState<UavSimState | null>(null);
  const [utmState, setUtmState] = useState<UtmStateResult | null>(null);
  const [utmSourceInfo, setUtmSourceInfo] = useState<UtmSourceInfo | null>(null);
  const [layeredStatus, setLayeredStatus] = useState<Record<string, unknown> | null>(null);
  const [dispatchStatus, setDispatchStatus] = useState<Record<string, unknown> | null>(null);
  const [dssNotificationRows, setDssNotificationRows] = useState<Record<string, unknown>[]>([]);
  const [dssNotifExpanded, setDssNotifExpanded] = useState(false);
  const [dssNotifStatusFilter, setDssNotifStatusFilter] = useState("pending");
  const [dssNotifTypeFilter, setDssNotifTypeFilter] = useState("all");
  const [networkMap, setNetworkMap] = useState<{ bs: MissionBs[]; coverage: MissionCoverage[]; tracks: MissionTrack[] }>({ bs: [], coverage: [], tracks: [] });

  const [wind, setWind] = useState(8);
  const [visibility, setVisibility] = useState(10);
  const [precip, setPrecip] = useState(0);
  const [storm, setStorm] = useState(false);
  const [weatherCheck, setWeatherCheck] = useState<Record<string, unknown> | null>(null);

  const [licenseId, setLicenseId] = useState("op-001");
  const [licenseClass, setLicenseClass] = useState("VLOS");
  const [licenseUavSizeClass, setLicenseUavSizeClass] = useState("middle");
  const [licenseExpiry, setLicenseExpiry] = useState("2099-01-01T00:00");
  const [licenseActive, setLicenseActive] = useState(true);

  const [requiredLicenseClass, setRequiredLicenseClass] = useState("VLOS");
  const [requestedSpeedMps, setRequestedSpeedMps] = useState("12");
  const [plannedStartAt, setPlannedStartAt] = useState("");
  const [plannedEndAt, setPlannedEndAt] = useState("");
  const [utmChecks, setUtmChecks] = useState<UtmCheckResults>({});
  const [fullSubmitApproved, setFullSubmitApproved] = useState<boolean | null>(null);

  const [nfzDraftX, setNfzDraftX] = useState("150");
  const [nfzDraftY, setNfzDraftY] = useState("110");
  const [nfzDraftRadiusM, setNfzDraftRadiusM] = useState("30");
  const [nfzDraftReason, setNfzDraftReason] = useState("operator_defined");
  const [nfzDraftZMin, setNfzDraftZMin] = useState("0");
  const [nfzDraftZMax, setNfzDraftZMax] = useState("120");

  const postUtm = async (path: string, body: unknown): Promise<Record<string, unknown>> => {
    const res = await fetch(`${normalizeBaseUrl(apiBase)}${path}`, {
      method: "POST",
      headers: utmAuthHeaders(utmAuthToken, true),
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !isObject(data)) throw new Error(formatApiErrorDetail(asRecord(data)?.detail ?? asRecord(data)?.error ?? "Request failed"));
    assertApiPayloadOk(data);
    return data as Record<string, unknown>;
  };

  const deleteUtm = async (path: string): Promise<Record<string, unknown>> => {
    const res = await fetch(`${normalizeBaseUrl(apiBase)}${path}`, {
      method: "DELETE",
      headers: utmAuthHeaders(utmAuthToken, false),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !isObject(data)) throw new Error(formatApiErrorDetail(asRecord(data)?.detail ?? asRecord(data)?.error ?? "Request failed"));
    assertApiPayloadOk(data);
    return data as Record<string, unknown>;
  };

  const postUav = async (path: string, body: unknown): Promise<Record<string, unknown>> => {
    const res = await fetch(`${normalizeBaseUrl(uavApiBase)}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !isObject(data)) throw new Error(formatApiErrorDetail(asRecord(data)?.detail ?? asRecord(data)?.error ?? "Request failed"));
    assertApiPayloadOk(data);
    return data as Record<string, unknown>;
  };

  const loadAll = async () => {
    setBusy(true);
    setMsg("");

    const shared = getSharedPageState();
    const authHeaders = utmAuthHeaders(utmAuthToken, false);
    const simQs = new URLSearchParams({ uav_id: simUavId, operator_license_id: licenseId });
    const utmStateQs = new URLSearchParams({ airspace_segment: airspace, operator_license_id: licenseId });

    const results = await Promise.allSettled([
      fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/live/state?${simQs.toString()}`),
      fetch(`${normalizeBaseUrl(apiBase)}/api/utm/state?${utmStateQs.toString()}`, { headers: authHeaders }),
      fetch(`${normalizeBaseUrl(apiBase)}/api/utm/weather?airspace_segment=${encodeURIComponent(airspace)}`, { headers: authHeaders }),
      fetch(`${normalizeBaseUrl(apiBase)}/api/utm/live/source`, { headers: authHeaders }),
      fetch(`${normalizeBaseUrl(apiBase)}/api/utm/layers/status?airspace_segment=${encodeURIComponent(airspace)}`, { headers: authHeaders }),
      fetch(`${normalizeBaseUrl(apiBase)}/api/utm/dss/notifications/dispatch/status`, { headers: authHeaders }),
      fetch(`${normalizeBaseUrl(apiBase)}/api/utm/dss/notifications?limit=200`, { headers: authHeaders }),
      fetch(
        `${normalizeBaseUrl(shared.networkApiBase)}/api/network/mission/state?airspace_segment=${encodeURIComponent(airspace)}&selected_uav_id=${encodeURIComponent(simUavId)}`,
      ),
    ]);

    const errs: string[] = [];

    if (results[0].status === "fulfilled") {
      const res = results[0].value;
      const data = await res.json().catch(() => ({}));
      if (res.ok && isObject(data)) {
        setSimState(data as UavSimState);
      } else {
        errs.push("UAV state");
      }
    } else {
      errs.push("UAV state");
    }

    if (results[1].status === "fulfilled") {
      const res = results[1].value;
      const data = await res.json().catch(() => ({}));
      const result = asRecord(asRecord(data)?.result);
      if (res.ok && result) {
        setUtmState(result as UtmStateResult);
      } else {
        errs.push("UTM state");
      }
    } else {
      errs.push("UTM state");
    }

    if (results[2].status === "fulfilled") {
      const res = results[2].value;
      const data = await res.json().catch(() => ({}));
      const result = asRecord(asRecord(data)?.result);
      if (res.ok && result) {
        setWeatherCheck(result);
        const weather = asRecord(result.weather);
        if (weather) {
          if (typeof weather.wind_mps === "number") setWind(weather.wind_mps);
          if (typeof weather.visibility_km === "number") setVisibility(weather.visibility_km);
          if (typeof weather.precip_mmph === "number") setPrecip(weather.precip_mmph);
          if (typeof weather.storm_alert === "boolean") setStorm(weather.storm_alert);
        }
      } else {
        errs.push("Weather");
      }
    } else {
      errs.push("Weather");
    }

    if (results[3].status === "fulfilled") {
      const res = results[3].value;
      const data = await res.json().catch(() => ({}));
      const result = asRecord(asRecord(data)?.result);
      if (res.ok) {
        setUtmSourceInfo((result ?? null) as UtmSourceInfo | null);
      }
    }

    if (results[4].status === "fulfilled") {
      const res = results[4].value;
      const data = await res.json().catch(() => ({}));
      const result = asRecord(asRecord(data)?.result);
      if (res.ok && result) {
        setLayeredStatus(result);
      } else {
        errs.push("Layered status");
      }
    } else {
      errs.push("Layered status");
    }

    if (results[5].status === "fulfilled") {
      const res = results[5].value;
      const data = await res.json().catch(() => ({}));
      const result = asRecord(asRecord(data)?.result);
      if (res.ok && result) {
        setDispatchStatus(result);
      } else {
        errs.push("Dispatch status");
      }
    } else {
      errs.push("Dispatch status");
    }

    if (results[6].status === "fulfilled") {
      const res = results[6].value;
      const data = await res.json().catch(() => ({}));
      const result = asRecord(asRecord(data)?.result);
      if (res.ok && result) {
        setDssNotificationRows(asArrayRecords(result.items));
      } else {
        errs.push("DSS notifications");
      }
    } else {
      errs.push("DSS notifications");
    }

    if (results[7].status === "fulfilled") {
      const res = results[7].value;
      const data = await res.json().catch(() => ({}));
      const result = asRecord(asRecord(data)?.result);
      if (res.ok && result) {
        const bs: MissionBs[] = asArrayRecords(result.baseStations).map((b) => ({
          id: String(b.id ?? "BS"),
          x: Number(b.x ?? 0),
          y: Number(b.y ?? 0),
          status: String(b.status ?? "online"),
        }));
        const coverage: MissionCoverage[] = asArrayRecords(result.coverage).map((c) => ({
          bsId: String(c.bsId ?? ""),
          radiusM: Number(c.radiusM ?? 0),
        }));
        const tracks: MissionTrack[] = asArrayRecords(result.trackingSnapshots).map((t) => ({
          id: String(t.id ?? "uav"),
          x: Number(t.x ?? 0),
          y: Number(t.y ?? 0),
          z: Number(t.z ?? 0),
          attachedBsId: String(t.attachedBsId ?? ""),
          interferenceRisk: String(t.interferenceRisk ?? "low") as MissionTrack["interferenceRisk"],
        }));
        setNetworkMap({ bs, coverage, tracks });
      } else {
        errs.push("Network");
      }
    } else {
      errs.push("Network");
    }

    if (errs.length > 0) {
      setMsg(`Partial refresh: ${errs.join(", ")} unavailable`);
    } else {
      setMsg("UTM page refreshed");
    }

    setBusy(false);
  };

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    patchSharedPageState({ utmApiBase: apiBase, uavApiBase, utmAuthToken, uavId: simUavId, airspace });
  }, [apiBase, uavApiBase, utmAuthToken, simUavId, airspace]);

  useEffect(() => {
    let lastRevision = getSharedPageState().revision;
    return subscribeSharedPageState((next) => {
      if (next.utmApiBase && next.utmApiBase !== apiBase) setApiBase(next.utmApiBase);
      if (next.uavApiBase && next.uavApiBase !== uavApiBase) setUavApiBase(next.uavApiBase);
      if (typeof next.utmAuthToken === "string" && next.utmAuthToken !== utmAuthToken) setUtmAuthToken(next.utmAuthToken);
      if (next.uavId && next.uavId !== simUavId) setSimUavId(next.uavId);
      if (next.airspace && next.airspace !== airspace) setAirspace(next.airspace);
      if (next.revision !== lastRevision) {
        lastRevision = next.revision;
        void loadAll();
      }
    });
  }, [apiBase, uavApiBase, utmAuthToken, simUavId, airspace]);

  useEffect(() => {
    if (!liveRefresh) return;
    const seconds = Math.max(1, Number.parseInt(liveRefreshSec || "3", 10) || 3);
    const id = window.setInterval(() => {
      if (!busy) void loadAll();
    }, seconds * 1000);
    return () => window.clearInterval(id);
  }, [liveRefresh, liveRefreshSec, busy]);

  const saveWeather = async () => {
    try {
      setBusy(true);
      await postUtm("/api/utm/weather", {
        airspace_segment: airspace,
        wind_mps: wind,
        visibility_km: visibility,
        precip_mmph: precip,
        storm_alert: storm,
      });
      setMsg("Weather updated");
      bumpSharedRevision();
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const registerLicense = async () => {
    const expiresIso = localInputToIsoUtc(licenseExpiry);
    if (licenseExpiry && !expiresIso) {
      setMsg("Action failed: invalid license expiry");
      return;
    }
    try {
      setBusy(true);
      await postUtm("/api/utm/license", {
        operator_license_id: licenseId,
        license_class: licenseClass,
        uav_size_class: licenseUavSizeClass,
        expires_at: expiresIso ?? "2099-01-01T00:00:00Z",
        active: licenseActive,
      });
      setMsg("License saved");
      bumpSharedRevision();
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const runCheck = async (path: string, body: unknown, key: keyof UtmCheckResults, successMsg: string) => {
    try {
      setBusy(true);
      const data = await postUtm(path, body);
      setUtmChecks((prev) => ({ ...prev, [key]: asRecord(data.result) ?? {} }));
      setMsg(successMsg);
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const runRouteChecks = async () => {
    const speed = Number.parseFloat(requestedSpeedMps);
    const uav = asRecord(simState?.uav);
    const routeWaypoints = asArrayRecords(uav?.waypoints).map((w) => ({
      x: Number(w.x ?? 0),
      y: Number(w.y ?? 0),
      z: Number(w.z ?? 0),
    }));
    await runCheck(
      "/api/utm/checks/route",
      {
        uav_id: simUavId,
        airspace_segment: airspace,
        requested_speed_mps: Number.isFinite(speed) ? speed : 12,
        operator_license_id: licenseId,
        route_id: typeof uav?.route_id === "string" ? uav.route_id : undefined,
        waypoints: routeWaypoints.length ? routeWaypoints : undefined,
      },
      "route",
      "Route checks completed",
    );
  };

  const runTimeWindowCheck = async () => {
    await runCheck(
      "/api/utm/checks/time-window",
      {
        planned_start_at: localInputToIsoUtc(plannedStartAt),
        planned_end_at: localInputToIsoUtc(plannedEndAt),
        operator_license_id: licenseId,
      },
      "timeWindow",
      "Time-window check completed",
    );
  };

  const runLicenseCheck = async () => {
    await runCheck(
      "/api/utm/checks/license",
      {
        operator_license_id: licenseId,
        required_license_class: requiredLicenseClass,
      },
      "license",
      "License check completed",
    );
  };

  const runVerifyFromUav = async () => {
    const speed = Number.parseFloat(requestedSpeedMps);
    const uav = asRecord(simState?.uav);
    const routeWaypoints = asArrayRecords(uav?.waypoints).map((w) => ({
      x: Number(w.x ?? 0),
      y: Number(w.y ?? 0),
      z: Number(w.z ?? 0),
    }));
    await runCheck(
      "/api/utm/verify-from-uav",
      {
        uav_id: simUavId,
        airspace_segment: airspace,
        operator_license_id: licenseId,
        required_license_class: requiredLicenseClass,
        requested_speed_mps: Number.isFinite(speed) ? speed : 12,
        planned_start_at: localInputToIsoUtc(plannedStartAt),
        planned_end_at: localInputToIsoUtc(plannedEndAt),
        route_id: typeof uav?.route_id === "string" ? uav.route_id : undefined,
        waypoints: routeWaypoints.length ? routeWaypoints : undefined,
      },
      "verify",
      "UTM verification completed",
    );
  };

  const runFullSubmitMission = async () => {
    const speed = Number.parseFloat(requestedSpeedMps);
    if (!Number.isFinite(speed) || speed <= 0) {
      setMsg("Action failed: requested speed must be a positive number");
      return;
    }
    const startIso = localInputToIsoUtc(plannedStartAt);
    const endIso = localInputToIsoUtc(plannedEndAt);
    if (plannedStartAt && !startIso) {
      setMsg("Action failed: invalid planned start");
      return;
    }
    if (plannedEndAt && !endIso) {
      setMsg("Action failed: invalid planned end");
      return;
    }
    try {
      setBusy(true);
      const submitPayload = {
        uav_id: simUavId,
        airspace_segment: airspace,
        operator_license_id: licenseId,
        required_license_class: requiredLicenseClass,
        requested_speed_mps: speed,
        planned_start_at: startIso,
        planned_end_at: endIso,
      };
      let data = await postUav("/api/uav/live/utm-submit-mission", submitPayload);
      let aggregate = asRecord(data.result);

      const dssConflictDetected = shouldAutoReplanForDssConflict(aggregate);

      if (dssConflictDetected) {
        const replanData = await postUav("/api/uav/live/replan-via-utm-nfz", {
          uav_id: simUavId,
          airspace_segment: airspace,
          operator_license_id: licenseId,
          optimization_profile: "balanced",
          auto_utm_verify: true,
          user_request: "Auto detour replan to resolve DSS strategic conflict before mission launch",
        });
        if (String(replanData.status ?? "").trim().toLowerCase() === "success") {
          data = await postUav("/api/uav/live/utm-submit-mission", submitPayload);
          aggregate = asRecord(data.result);
          setMsg("DSS conflict detected: auto detour replan applied and mission submit retried.");
        } else {
          setMsg("DSS conflict detected: auto detour replan did not complete.");
        }
      }

      const routeWrap = asRecord(aggregate?.route_checks);
      const verifyWrap = asRecord(aggregate?.verify_from_uav);
      const routeResult = asRecord(routeWrap?.result);
      const verifyResult = asRecord(verifyWrap?.result);
      setUtmChecks((prev) => ({
        ...prev,
        route: routeResult ?? prev.route,
        verify: verifyResult ?? prev.verify,
      }));
      const approved = aggregate?.approved === true;
      setFullSubmitApproved(approved);
      await loadAll();
      if (approved) {
        setMsg("Full mission submit approved (UTM + DSS).");
      } else {
        const submitGate = asRecord(aggregate?.submit_gate);
        if (submitGate?.battery_ok === false) {
          const gateIssues = Array.isArray(submitGate?.issues) ? (submitGate.issues as unknown[]).map(String).filter(Boolean) : [];
          setMsg(`Full mission submit blocked by submit gate${gateIssues.length ? `: ${gateIssues.join("; ")}` : " (battery check failed)."}`);
          return;
        }
        const approvalWrap = asRecord(aggregate?.approval_request);
        const reason = approvalWrap?.reason;
        setMsg(`Full mission submit completed (not approved${reason ? `: ${String(reason)}` : ""}).`);
      }
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const runDispatchCycle = async () => {
    try {
      setBusy(true);
      await postUtm("/api/utm/dss/notifications/dispatch", { run_limit: 1 });
      setMsg("Ran one DSS notification dispatch cycle.");
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const runDssAutoRecover = async () => {
    try {
      setBusy(true);
      const notes: string[] = [];

      if (dispatch?.dispatcher_enabled !== true) {
        await postUtm("/api/utm/dss/notifications/dispatch/enabled", { enabled: true });
        notes.push("dispatcher enabled");
      }

      const badManagers = new Set(ussManagerRows.filter((row) => !row.known || !row.active).map((row) => row.manager));
      const recoverIntentIds = Array.from(
        new Set(
          dssOperationalIntents
            .filter((intent) => {
              const iid = String(intent.intent_id ?? "").trim();
              if (!iid) return false;
              const metadata = asRecord(intent.metadata);
              const intentUav = String(metadata?.uav_id ?? "").trim();
              if (intentUav && intentUav !== simUavId) return false;
              const manager = String(intent.manager_uss_id ?? "").trim();
              const conflictSummary = asRecord(intent.conflict_summary);
              const blocking = Number(conflictSummary?.blocking ?? 0);
              return blocking > 0 || badManagers.has(manager);
            })
            .map((intent) => String(intent.intent_id ?? "").trim())
            .filter(Boolean),
        ),
      );

      let deleted = 0;
      let deleteFailed = 0;
      for (const intentId of recoverIntentIds) {
        try {
          await deleteUtm(`/api/utm/dss/operational-intents/${encodeURIComponent(intentId)}`);
          deleted += 1;
        } catch {
          deleteFailed += 1;
        }
      }
      if (recoverIntentIds.length > 0) {
        notes.push(`intents deleted ${deleted}/${recoverIntentIds.length}`);
      } else {
        notes.push("no problematic intents for selected UAV");
      }
      if (deleteFailed > 0) {
        notes.push(`delete failed ${deleteFailed}`);
      }

      await postUtm("/api/utm/dss/notifications/dispatch", { run_limit: 3 });
      notes.push("dispatch x3");

      await loadAll();
      setMsg(`DSS auto recover done for ${simUavId}: ${notes.join(", ")}`);
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const ackDssNotification = async (notificationId: string) => {
    const nid = String(notificationId || "").trim();
    if (!nid) return;
    try {
      setBusy(true);
      await postUtm(`/api/utm/dss/notifications/${encodeURIComponent(nid)}/ack`, {});
      setMsg(`Acked DSS notification ${nid}.`);
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const toggleDispatcherEnabled = async () => {
    const currentEnabled = dispatch?.dispatcher_enabled === true;
    const nextEnabled = !currentEnabled;
    try {
      setBusy(true);
      await postUtm("/api/utm/dss/notifications/dispatch/enabled", { enabled: nextEnabled });
      setMsg(`DSS dispatcher ${nextEnabled ? "enabled" : "disabled"}.`);
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const deleteDssIntent = async (intentId: string) => {
    const iid = String(intentId || "").trim();
    if (!iid) return;
    if (!window.confirm(`Delete DSS intent ${iid}?`)) return;
    try {
      setBusy(true);
      await deleteUtm(`/api/utm/dss/operational-intents/${encodeURIComponent(iid)}`);
      setMsg(`Deleted DSS intent ${iid}`);
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const deleteUssProblemIntents = async (intentIds: string[]) => {
    const uniqueIds = Array.from(new Set(intentIds.map((v) => String(v || "").trim()).filter(Boolean)));
    if (uniqueIds.length === 0) {
      setMsg("No problematic USS intents to delete.");
      return;
    }
    if (!window.confirm(`Delete ${uniqueIds.length} problematic USS intent(s)?`)) return;
    try {
      setBusy(true);
      let ok = 0;
      let fail = 0;
      for (const iid of uniqueIds) {
        try {
          await deleteUtm(`/api/utm/dss/operational-intents/${encodeURIComponent(iid)}`);
          ok += 1;
        } catch {
          fail += 1;
        }
      }
      setMsg(`USS cleanup finished: deleted ${ok}, failed ${fail}.`);
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const reserveCorridor = async () => {
    await runCheck(
      "/api/utm/corridor/reserve",
      { uav_id: simUavId, airspace_segment: airspace },
      "corridor",
      "Corridor reservation completed",
    );
  };

  const addNoFlyZoneAt = async (point: { x: number; y: number }) => {
    const radiusM = Number.parseFloat(nfzDraftRadiusM);
    const zMin = Number.parseFloat(nfzDraftZMin);
    const zMax = Number.parseFloat(nfzDraftZMax);
    if (!Number.isFinite(radiusM) || radiusM <= 0) {
      setMsg("Action failed: NFZ radius must be positive");
      return;
    }
    if (!Number.isFinite(zMin) || !Number.isFinite(zMax) || zMax < zMin) {
      setMsg("Action failed: NFZ altitude range invalid");
      return;
    }
    try {
      setBusy(true);
      await postUtm("/api/utm/nfz", {
        cx: point.x,
        cy: point.y,
        radius_m: radiusM,
        z_min: zMin,
        z_max: zMax,
        reason: nfzDraftReason.trim() || "operator_defined",
      });
      setNfzDraftX(String(Number(point.x.toFixed(1))));
      setNfzDraftY(String(Number(point.y.toFixed(1))));
      setMsg(`No-fly zone added at (${point.x.toFixed(1)}, ${point.y.toFixed(1)})`);
      bumpSharedRevision();
      await loadAll();
    } catch (e) {
      setBusy(false);
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const addNoFlyZoneByForm = async () => {
    const x = Number.parseFloat(nfzDraftX);
    const y = Number.parseFloat(nfzDraftY);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      setMsg("Action failed: NFZ X/Y must be numbers");
      return;
    }
    await addNoFlyZoneAt({ x, y });
  };

  const simUav = asRecord(simState?.uav);
  const routePointsForMap = asArrayRecords(simUav?.waypoints).map((w) => ({
    x: Number(w.x ?? 0),
    y: Number(w.y ?? 0),
    z: Number(w.z ?? 0),
  }));
  const plannedPosForMap = routePointsForMap.length > 0 ? routePointsForMap[0] : null;

  const nfzRaw = asArrayRecords(utmState?.noFlyZones ?? simState?.utm?.no_fly_zones ?? []);
  const nfzZonesForMap: MissionNfz[] = nfzRaw.map((z) => ({
    zone_id: String(z.zone_id ?? "nfz"),
    cx: Number(z.cx ?? 0),
    cy: Number(z.cy ?? 0),
    radius_m: Number(z.radius_m ?? 0),
    z_min: Number(z.z_min ?? 0),
    z_max: Number(z.z_max ?? 120),
    reason: String(z.reason ?? ""),
  }));

  const approval = asRecord(simUav?.utm_approval);
  const verify = asRecord(utmChecks.verify);
  const activeApproval = verify ?? approval;
  const activeApprovalChecks = asRecord(activeApproval?.checks);

  const route = asRecord(utmChecks.route);
  const routeGeofence = asRecord(route?.geofence);
  const routeNfz = asRecord(route?.no_fly_zone);
  const routeRegs = asRecord(route?.regulations);
  const timeWindow = asRecord(utmChecks.timeWindow);
  const licenseCheck = asRecord(utmChecks.license);
  const corridor = asRecord(utmChecks.corridor);

  const weatherResultChecks = asRecord(weatherCheck?.checks);

  const licenses = useMemo(() => {
    const fromUtm = asRecord(utmState?.licenses);
    if (fromUtm) return fromUtm;
    return asRecord(simState?.utm?.licenses) ?? {};
  }, [utmState, simState]);

  const licenseRows = useMemo(() => Object.entries(licenses), [licenses]);

  const effectiveRegs = asRecord(utmState?.effectiveRegulations ?? simState?.utm?.effective_regulations);

  useEffect(() => {
    const current = asRecord(licenses[licenseId]);
    if (!current) return;
    if (typeof current.license_class === "string") setLicenseClass(current.license_class);
    if (typeof current.uav_size_class === "string") setLicenseUavSizeClass(current.uav_size_class);
    if (typeof current.active === "boolean") setLicenseActive(current.active);
    if (typeof current.expires_at === "string") {
      const local = isoUtcToLocalInput(current.expires_at);
      if (local) setLicenseExpiry(local);
    }
  }, [licenses, licenseId]);

  const dssSummary = asRecord(utmState?.dss);
  const layered = asRecord(layeredStatus);
  const layeredLayers = asRecord(layered?.layers);
  const layeredUtm = asRecord(layeredLayers?.utm);
  const layeredUss = asRecord(layeredLayers?.uss);
  const layeredDss = asRecord(layeredLayers?.dss);
  const layeredSummary = asRecord(layered?.summary);
  const layeredUavCards = asArrayRecords(layered?.uav_status_cards);
  const dispatch = asRecord(dispatchStatus);
  const dispatchConfig = asRecord(dispatch?.config);
  const dispatchLast = asRecord(dispatch?.last_cycle);
  const dispatchCounts = asRecord(dispatch?.counts);
  const dssOperationalIntents = asArrayRecords(dssSummary?.operationalIntents);
  const dssSubscriptions = asArrayRecords(dssSummary?.subscriptions);
  const dssParticipants = asArrayRecords(dssSummary?.participants);
  const dssNotifications = useMemo(() => dssNotificationRows, [dssNotificationRows]);
  const dssNotifTypeOptions = useMemo(() => {
    const set = new Set<string>();
    for (const row of dssNotifications) {
      const eventType = String(row.event_type ?? "").trim().toLowerCase();
      if (eventType) set.add(eventType);
    }
    return Array.from(set).sort();
  }, [dssNotifications]);
  const dssNotifStatusCounts = useMemo(() => {
    const out: Record<string, number> = { pending: 0, delivered: 0, failed: 0, acked: 0 };
    for (const row of dssNotifications) {
      const status = String(row.status ?? "").trim().toLowerCase();
      if (status in out) out[status] += 1;
    }
    return out;
  }, [dssNotifications]);
  const dssNotificationsFiltered = useMemo(
    () =>
      dssNotifications.filter((row) => {
        const status = String(row.status ?? "").trim().toLowerCase();
        const eventType = String(row.event_type ?? "").trim().toLowerCase();
        if (dssNotifStatusFilter !== "all" && status !== dssNotifStatusFilter) return false;
        if (dssNotifTypeFilter !== "all" && eventType !== dssNotifTypeFilter) return false;
        return true;
      }),
    [dssNotifications, dssNotifStatusFilter, dssNotifTypeFilter],
  );
  const participantsById = useMemo(() => {
    const out: Record<string, Record<string, unknown>> = {};
    for (const row of dssParticipants) {
      const pid = String(row.participant_id ?? "").trim();
      if (pid) out[pid] = row;
    }
    return out;
  }, [dssParticipants]);
  const ussManagerIds = useMemo(() => {
    const ids = new Set<string>();
    for (const intent of dssOperationalIntents) {
      const manager = String(intent.manager_uss_id ?? "").trim();
      if (manager) ids.add(manager);
    }
    return Array.from(ids).sort();
  }, [dssOperationalIntents]);
  const layeredUnknownManagerIds = useMemo(
    () => (Array.isArray(layeredUss?.unknown_manager_ids) ? layeredUss.unknown_manager_ids.map((v) => String(v)) : []),
    [layeredUss],
  );
  const ussManagerRows = useMemo(
    () =>
      ussManagerIds.map((manager) => {
        const participant = participantsById[manager];
        const participantStatus = String(participant?.status ?? "").trim().toLowerCase();
        const known = Boolean(participant) || isLocalUssManager(manager);
        const active = Boolean((participant && participantStatus === "active") || isLocalUssManager(manager));
        const intentCount = dssOperationalIntents.filter((intent) => String(intent.manager_uss_id ?? "").trim() === manager).length;
        return {
          manager,
          known,
          active,
          participantStatus: participantStatus || (isLocalUssManager(manager) ? "local" : "unknown"),
          intentCount,
        };
      }),
    [ussManagerIds, participantsById, dssOperationalIntents],
  );
  const ussIntentRows = useMemo(
    () =>
      dssOperationalIntents.map((intent) => {
        const metadata = asRecord(intent.metadata);
        const conflictSummary = asRecord(intent.conflict_summary);
        const volume4d = asRecord(intent.volume4d);
        const manager = String(intent.manager_uss_id ?? "").trim();
        const participant = participantsById[manager];
        const participantStatus = String(participant?.status ?? "").trim().toLowerCase();
        const known = Boolean(participant) || isLocalUssManager(manager);
        const active = Boolean((participant && participantStatus === "active") || isLocalUssManager(manager));
        const timeStart = String(volume4d?.time_start ?? intent.time_start ?? "-");
        const timeEnd = String(volume4d?.time_end ?? intent.time_end ?? "-");
        return {
          intentId: String(intent.intent_id ?? ""),
          uavId: String(metadata?.uav_id ?? "-"),
          managerUssId: manager || "-",
          known,
          active,
          participantStatus: participantStatus || (isLocalUssManager(manager) ? "local" : "unknown"),
          state: String(intent.state ?? "-"),
          priority: String(intent.priority ?? "-"),
          conflictPolicy: String(intent.conflict_policy ?? "-"),
          blocking: Number(conflictSummary?.blocking ?? 0),
          lifecyclePhase: String(metadata?.lifecycle_phase ?? "-"),
          timeStart,
          timeEnd,
          updatedAt: String(intent.updated_at ?? "-"),
        };
      }),
    [dssOperationalIntents, participantsById],
  );
  const ussIntentIdsByManager = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const intent of dssOperationalIntents) {
      const manager = String(intent.manager_uss_id ?? "").trim();
      const intentId = String(intent.intent_id ?? "").trim();
      if (!manager || !intentId) continue;
      if (!out[manager]) out[manager] = [];
      out[manager].push(intentId);
    }
    return out;
  }, [dssOperationalIntents]);
  const ussProblemIntentIds = useMemo(() => {
    const badManagers = new Set(ussManagerRows.filter((row) => !row.known || !row.active).map((row) => row.manager));
    return Array.from(
      new Set(
        dssOperationalIntents
          .filter((intent) => badManagers.has(String(intent.manager_uss_id ?? "").trim()))
          .map((intent) => String(intent.intent_id ?? "").trim())
          .filter(Boolean),
      ),
    );
  }, [ussManagerRows, dssOperationalIntents]);
  const ussLayerReason = useMemo(() => {
    if (layeredUss?.ok === true) return "USS layer healthy: all seen managers are known/allowed.";
    const unknownCount = Number(layeredUss?.unknown_manager_count ?? layeredUnknownManagerIds.length ?? 0);
    if (unknownCount > 0) {
      return `USS layer failed because ${unknownCount} manager USS ID(s) are not registered participants.`;
    }
    return "USS layer failed: manager/participant mapping is not healthy.";
  }, [layeredUss, layeredUnknownManagerIds]);
  const fleetMap = asRecord(simState?.fleet) ?? {};
  const fleetUavIds = Object.keys(fleetMap).sort();
  const registryUserSummary = asRecord(simState?.uav_registry_user);
  const registry = asRecord(registryUserSummary?.registry);
  const registryUsers = asRecord(registry?.users) ?? {};
  const registryUavs = asRecord(registry?.uavs) ?? {};
  const userIds = Object.keys(registryUsers).sort();
  const selectedUserRow = asRecord((registryUsers as Record<string, unknown>)[selectedMapUserId]);
  const selectedUserUavIds = (
    selectedMapUserId === "all"
      ? fleetUavIds
      : (
          Array.isArray(selectedUserRow?.uav_ids)
            ? selectedUserRow.uav_ids.map((id) => String(id)).filter(Boolean)
            : []
        )
  ).filter((id) => fleetUavIds.includes(id));
  const mapSelectableUavIds = selectedUserUavIds.length > 0 ? selectedUserUavIds : fleetUavIds;
  const mapUavOptions = useMemo(() => {
    const base = mapSelectableUavIds.slice();
    if (simUavId && !base.includes(simUavId)) return [simUavId, ...base];
    if (!base.length && simUavId) return [simUavId];
    return base;
  }, [mapSelectableUavIds, simUavId]);
  const mapTrackIdSet = new Set(mapSelectableUavIds);
  const mapTracks =
    selectedMapUserId === "all"
      ? networkMap.tracks
      : networkMap.tracks.filter((track) => mapTrackIdSet.has(track.id));
  const layeredFleetCountRaw = Number(layeredSummary?.fleet_count);
  const totalUavCount =
    Number.isFinite(layeredFleetCountRaw) && layeredFleetCountRaw > 0
      ? Math.trunc(layeredFleetCountRaw)
      : (Object.keys(registryUavs).length || fleetUavIds.length);
  const totalUserCount = userIds.length;
  const layeredUavCardsForScope = useMemo(
    () => layeredUavCards.filter((card) => selectedMapUserId === "all" || mapTrackIdSet.has(String(card.uav_id))),
    [layeredUavCards, selectedMapUserId, mapTrackIdSet],
  );
  const missionScopeUavIds = mapSelectableUavIds;
  const fleetIdsFromLayeredScope = new Set(layeredUavCardsForScope.map((card) => String(card.uav_id ?? "")).filter(Boolean));
  const missionOnlyFleetIds = missionScopeUavIds.filter((id) => !fleetIdsFromLayeredScope.has(id));

  useEffect(() => {
    if (dssNotifTypeFilter !== "all" && !dssNotifTypeOptions.includes(dssNotifTypeFilter)) {
      setDssNotifTypeFilter("all");
    }
  }, [dssNotifTypeFilter, dssNotifTypeOptions]);

  useEffect(() => {
    if (selectedMapUserId !== "all" && !userIds.includes(selectedMapUserId)) {
      setSelectedMapUserId("all");
    }
  }, [selectedMapUserId, userIds]);

  useEffect(() => {
    if (!simUavId && mapSelectableUavIds.length > 0) {
      setSimUavId(mapSelectableUavIds[0]);
    }
  }, [mapSelectableUavIds, simUavId]);

  return (
    <div style={{ display: "grid", gap: 12, padding: 14, maxWidth: 1280, margin: "0 auto" }}>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1.1fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>Layered Runtime (UTM / USS / DSS)</div>
              <div style={{ fontSize: 11, color: "#667085" }}>Updated: <code>{String(layered?.generated_at ?? "-")}</code></div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 8 }}>
              {[
                {
                  label: "UTM Layer",
                  ok: layeredUtm?.ok,
                  detail: `${String(layeredUtm?.approved_uav_count ?? "-")} approved / ${String(layeredUtm?.fleet_count ?? "-")} fleet`,
                },
                {
                  label: "USS Layer",
                  ok: layeredUss?.ok,
                  detail: `${String(layeredUss?.active_participant_count ?? "-")} active / ${String(layeredUss?.participant_count ?? "-")} participants`,
                },
                {
                  label: "DSS Layer",
                  ok: layeredDss?.ok,
                  detail: `${String(layeredDss?.intent_count ?? "-")} intents / ${String(layeredDss?.pending_notification_count ?? "-")} pending`,
                },
              ].map((row) => (
                <div key={row.label} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "8px 10px", display: "grid", gap: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                    <div style={{ fontSize: 11, color: "#667085" }}>{row.label}</div>
                    <div>{yesNoBadge(row.ok)}</div>
                  </div>
                  <div style={{ fontSize: 12, color: "#101828", fontWeight: 700 }}>{row.detail}</div>
                </div>
              ))}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8 }}>
              {[
                ["Ready", layeredSummary?.uav_ready_count],
                ["Attention", layeredSummary?.uav_attention_count],
                ["Blocked", layeredSummary?.uav_blocked_count],
                ["Pending", layeredSummary?.uav_pending_count],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ fontSize: 11, color: "#667085" }}>{String(label)}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#101828" }}>{value == null ? "-" : String(value)}</div>
                </div>
              ))}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8 }}>
              {[
                ["Users", totalUserCount],
                ["Total UAVs", totalUavCount],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ fontSize: 11, color: "#667085" }}>{String(label)}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#101828" }}>{String(value)}</div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              DSS snapshot: intents {String(dssSummary?.operationalIntentCount ?? "-")}, subscriptions {String(dssSummary?.subscriptionCount ?? "-")}, participants {String(dssSummary?.participantCount ?? "-")}, pending {String(dssSummary?.pendingNotificationCount ?? "-")}.
            </div>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>Select User and Target UAV</div>
              <div style={{ fontSize: 11, color: "#667085" }}>Filter: <code>{selectedMapUserId}</code></div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(150px, 1fr) minmax(190px, 1fr) auto", gap: 8, alignItems: "end" }}>
              <label style={{ fontSize: 12 }}>Select User
                <select style={inputStyle} value={selectedMapUserId} onChange={(e) => setSelectedMapUserId(e.target.value)}>
                  <option value="all">All users</option>
                  {userIds.map((uid) => (
                    <option key={uid} value={uid}>{uid}</option>
                  ))}
                </select>
              </label>
              <label style={{ fontSize: 12 }}>Target UAV
                <select style={inputStyle} value={simUavId} onChange={(e) => setSimUavId(e.target.value)}>
                  {(mapUavOptions.length > 0 ? mapUavOptions : [simUavId]).map((uid) => (
                    <option key={uid} value={uid}>{uid}</option>
                  ))}
                </select>
              </label>
              <div style={{ fontSize: 11, color: "#667085", paddingBottom: 8 }}>
                Showing {String(mapTracks.length)} tracks on map
              </div>
            </div>
            <div style={{ borderTop: "1px solid #eaecf0", paddingTop: 8, display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ fontSize: 11, color: "#667085" }}>
                Layered fleet {Number.isFinite(layeredFleetCountRaw) ? Math.trunc(layeredFleetCountRaw) : "N/A"} • Scope mission UAVs {missionScopeUavIds.length} • Scope layered cards {layeredUavCardsForScope.length}
              </div>
            </div>
            {missionOnlyFleetIds.length > 0 ? (
              <div style={{ fontSize: 11, color: "#b54708" }}>
                UAVs present in mission fleet but missing from layered cards: <code>{missionOnlyFleetIds.join(", ")}</code>
              </div>
            ) : null}
            <div style={{ overflowX: "auto", maxHeight: 260 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>UAV</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Overall</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>UTM Approval</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Geofence</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>USS</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>DSS Intent</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>DSS State</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Issues</th>
                  </tr>
                </thead>
                <tbody>
                  {layeredUavCardsForScope.length === 0 ? (
                    <tr><td colSpan={8} style={{ padding: "8px 4px", color: "#667085" }}>No layered UAV cards from backend yet.</td></tr>
                  ) : (
                    layeredUavCardsForScope.map((card, idx) => {
                      const uavId = String(card.uav_id ?? "");
                      const overall = String(card.overall_status ?? "pending");
                      const utmLayer = asRecord(card.utm_layer);
                      const ussLayer = asRecord(card.uss_layer);
                      const dssLayer = asRecord(card.dss_layer);
                      const issues = Array.isArray(card.issues) ? card.issues.map(String).filter(Boolean).slice(0, 4).join(", ") : "";
                      const selected = uavId === simUavId;
                      return (
                        <tr key={`fleet-layer-${uavId || idx}`}>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px", fontWeight: selected ? 700 : 500, color: selected ? "#155eef" : "#101828" }}>
                            <code>{uavId || "N/A"}</code>{selected ? " (selected)" : ""}
                          </td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{statusBadge(overall)}</td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{yesNoBadge(utmLayer?.approval_granted)}</td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{yesNoBadge(utmLayer?.geofence_ok)}</td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(ussLayer?.manager_uss_id ?? "N/A")}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(dssLayer?.intent_id ?? "N/A")}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(dssLayer?.intent_state ?? "N/A")}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px", color: "#667085" }}>{issues || "none"}</td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>UTM Map + No-Fly Zones</div>
              <div style={{ fontSize: 11, color: "#667085" }}>Shift+click map to add NFZ center</div>
            </div>
            <MissionSyncMap
              title="Select User and Target UAV Map"
              route={routePointsForMap}
              plannedPosition={plannedPosForMap}
              trackedPositions={mapTracks}
              selectedUavId={simUavId}
              noFlyZones={nfzZonesForMap}
              baseStations={networkMap.bs}
              coverage={networkMap.coverage}
              clickable
              onAddNoFlyZoneCenter={(p) => void addNoFlyZoneAt({ x: p.x, y: p.y })}
            />
            <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 1.1fr) repeat(5, minmax(64px, 90px)) auto", gap: 8, alignItems: "end" }}>
              <label style={{ fontSize: 12 }}>Reason<input style={inputStyle} value={nfzDraftReason} onChange={(e) => setNfzDraftReason(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>X<input style={inputStyle} value={nfzDraftX} onChange={(e) => setNfzDraftX(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>Y<input style={inputStyle} value={nfzDraftY} onChange={(e) => setNfzDraftY(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>R(m)<input style={inputStyle} value={nfzDraftRadiusM} onChange={(e) => setNfzDraftRadiusM(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>Zmin<input style={inputStyle} value={nfzDraftZMin} onChange={(e) => setNfzDraftZMin(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>Zmax<input style={inputStyle} value={nfzDraftZMax} onChange={(e) => setNfzDraftZMax(e.target.value)} /></label>
              <button type="button" style={chipStyle(false)} onClick={() => void addNoFlyZoneByForm()} disabled={busy}>Add NFZ</button>
            </div>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>Weather Controls</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label style={{ fontSize: 12 }}>Wind: {wind.toFixed(1)} m/s<input type="range" min={0} max={25} step={0.5} value={wind} onChange={(e) => setWind(Number(e.target.value))} style={{ width: "100%" }} /></label>
              <label style={{ fontSize: 12 }}>Visibility: {visibility.toFixed(1)} km<input type="range" min={0.5} max={20} step={0.5} value={visibility} onChange={(e) => setVisibility(Number(e.target.value))} style={{ width: "100%" }} /></label>
              <label style={{ fontSize: 12 }}>Precip: {precip.toFixed(1)} mm/h<input type="range" min={0} max={10} step={0.5} value={precip} onChange={(e) => setPrecip(Number(e.target.value))} style={{ width: "100%" }} /></label>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingTop: 18 }}>
                <input type="checkbox" checked={storm} onChange={(e) => setStorm(e.target.checked)} /> Storm alert
              </label>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <button type="button" style={chipStyle(false)} onClick={() => void saveWeather()} disabled={busy}>Save Weather</button>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 11 }}>
                <span>Weather {yesNoBadge(weatherCheck?.ok)}</span>
                <span>Wind {yesNoBadge(weatherResultChecks?.wind_ok)}</span>
                <span>Vis {yesNoBadge(weatherResultChecks?.visibility_ok)}</span>
                <span>Precip {yesNoBadge(weatherResultChecks?.precip_ok)}</span>
                <span>Storm {yesNoBadge(weatherResultChecks?.storm_ok)}</span>
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>Mission Control (UTM Console + UAV Submit)</div>
              <button type="button" style={chipStyle(false)} onClick={() => setMissionControlExpanded((v) => !v)}>
                {missionControlExpanded ? "Collapse" : "Expand"}
              </button>
            </div>
            <div style={{ fontSize: 11, color: msg.toLowerCase().includes("failed") ? "#b42318" : "#667085" }}>
              {msg || "Collapsed to save page space."}
            </div>
            {missionControlExpanded ? (
              <div style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 8 }}>
                  <label style={{ fontSize: 12 }}>UTM API URL<input style={inputStyle} value={apiBase} onChange={(e) => setApiBase(e.target.value)} /></label>
                  <label style={{ fontSize: 12 }}>UAV API URL<input style={inputStyle} value={uavApiBase} onChange={(e) => setUavApiBase(e.target.value)} /></label>
                  <label style={{ fontSize: 12 }}>UTM Bearer Token<input style={inputStyle} value={utmAuthToken} onChange={(e) => setUtmAuthToken(e.target.value)} /></label>
                  <label style={{ fontSize: 12 }}>UAV ID<input style={inputStyle} value={simUavId} onChange={(e) => setSimUavId(e.target.value)} /></label>
                  <label style={{ fontSize: 12 }}>Airspace<input style={inputStyle} value={airspace} onChange={(e) => setAirspace(e.target.value)} /></label>
                  <div style={{ fontSize: 11, color: "#667085", alignSelf: "end" }}>
                    Source: <strong>{String(utmSourceInfo?.active ?? "-")}</strong> ({String(utmSourceInfo?.mode ?? "-")})
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <button type="button" style={chipStyle(false)} onClick={() => void loadAll()} disabled={busy}>Refresh</button>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                    <input type="checkbox" checked={liveRefresh} onChange={(e) => setLiveRefresh(e.target.checked)} />
                    Live refresh
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                    every
                    <input style={{ ...inputStyle, width: 60 }} value={liveRefreshSec} onChange={(e) => setLiveRefreshSec(e.target.value)} inputMode="numeric" />
                    s
                  </label>
                </div>
                <div style={{ borderTop: "1px solid #eaecf0", paddingTop: 8, display: "grid", gap: 8 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8 }}>
                    <label style={{ fontSize: 12 }}>Required License Class
                      <select style={inputStyle} value={requiredLicenseClass} onChange={(e) => setRequiredLicenseClass(e.target.value)}>
                        <option value="VLOS">VLOS</option>
                        <option value="BVLOS">BVLOS</option>
                      </select>
                    </label>
                    <label style={{ fontSize: 12 }}>Requested Speed (m/s)<input style={inputStyle} value={requestedSpeedMps} onChange={(e) => setRequestedSpeedMps(e.target.value)} /></label>
                    <label style={{ fontSize: 12 }}>Planned Start<input type="datetime-local" style={inputStyle} value={plannedStartAt} onChange={(e) => setPlannedStartAt(e.target.value)} /></label>
                    <label style={{ fontSize: 12 }}>Planned End<input type="datetime-local" style={inputStyle} value={plannedEndAt} onChange={(e) => setPlannedEndAt(e.target.value)} /></label>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <button type="button" style={chipStyle(false)} onClick={() => void runVerifyFromUav()} disabled={busy}>Verify</button>
                    <button type="button" style={chipStyle(true)} onClick={() => void runFullSubmitMission()} disabled={busy}>Full Submit (UTM + DSS)</button>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8, fontSize: 12 }}>
                    {[
                      ["Verify", yesNoBadge(verify?.approved)],
                      ["Full submit", yesNoBadge(fullSubmitApproved)],
                    ].map(([label, value]) => (
                      <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                        <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
                        <div style={{ minHeight: 18 }}>{value as React.ReactNode}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div style={{ borderTop: "1px solid #eaecf0", paddingTop: 8, display: "grid", gap: 8 }}>
                  <div style={{ fontWeight: 700, color: "#101828", fontSize: 13 }}>Operator License Registry</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1.15fr 0.9fr 0.9fr 1.1fr auto", gap: 8, alignItems: "end" }}>
                    <label style={{ fontSize: 12 }}>License ID<input style={inputStyle} value={licenseId} onChange={(e) => setLicenseId(e.target.value)} /></label>
                    <label style={{ fontSize: 12 }}>Class
                      <select style={inputStyle} value={licenseClass} onChange={(e) => setLicenseClass(e.target.value)}>
                        <option value="VLOS">VLOS</option>
                        <option value="BVLOS">BVLOS</option>
                      </select>
                    </label>
                    <label style={{ fontSize: 12 }}>UAV Size
                      <select style={inputStyle} value={licenseUavSizeClass} onChange={(e) => setLicenseUavSizeClass(e.target.value)}>
                        <option value="small">Small</option>
                        <option value="middle">Middle</option>
                        <option value="large">Large</option>
                      </select>
                    </label>
                    <label style={{ fontSize: 12 }}>Expiry<input type="datetime-local" style={inputStyle} value={licenseExpiry} onChange={(e) => setLicenseExpiry(e.target.value)} /></label>
                    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, marginBottom: 8 }}>
                      <input type="checkbox" checked={licenseActive} onChange={(e) => setLicenseActive(e.target.checked)} /> Active
                    </label>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <button type="button" style={chipStyle(false)} onClick={() => void registerLicense()} disabled={busy}>Register / Update</button>
                    <div style={{ fontSize: 11, color: "#667085" }}>
                      Effective size class: <strong>{String(effectiveRegs?.uav_size_class ?? "-")}</strong>
                    </div>
                  </div>
                  <div style={{ overflowX: "auto", maxHeight: 220 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>ID</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Class</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>UAV Size</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Expiry</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Active</th>
                        </tr>
                      </thead>
                      <tbody>
                        {licenseRows.length === 0 ? (
                          <tr><td colSpan={5} style={{ padding: "8px 4px", color: "#667085" }}>No licenses found.</td></tr>
                        ) : (
                          licenseRows.map(([id, rec]) => {
                            const row = asRecord(rec);
                            return (
                              <tr key={id}>
                                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{id}</code></td>
                                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(row?.license_class ?? "")}</td>
                                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(row?.uav_size_class ?? "")}</td>
                                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(row?.expires_at ?? "")}</code></td>
                                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{yesNoBadge(row?.active)}</td>
                              </tr>
                            );
                          })
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            ) : null}
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>UTM Checks (Advanced)</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button type="button" style={chipStyle(false)} onClick={() => void runRouteChecks()} disabled={busy}>Route</button>
              <button type="button" style={chipStyle(false)} onClick={() => void runTimeWindowCheck()} disabled={busy}>Time</button>
              <button type="button" style={chipStyle(false)} onClick={() => void runLicenseCheck()} disabled={busy}>License</button>
              <button type="button" style={chipStyle(false)} onClick={() => void reserveCorridor()} disabled={busy}>Corridor</button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 8, fontSize: 12 }}>
              {[
                ["Route bounds", yesNoBadge(routeGeofence?.geofence_ok ?? routeGeofence?.bounds_ok ?? routeGeofence?.ok)],
                ["Route NFZ", yesNoBadge(routeNfz?.ok)],
                ["Route regulation", yesNoBadge(routeRegs?.ok)],
                ["Time window", yesNoBadge(timeWindow?.ok)],
                ["License", yesNoBadge(licenseCheck?.ok)],
                ["Corridor", yesNoBadge(corridor?.reserved)],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
                  <div style={{ minHeight: 18 }}>{value as React.ReactNode}</div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>Latest Approval</div>
            {decisionPanel(activeApproval?.decision)}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 8, fontSize: 12 }}>
              {[
                ["Approved", yesNoBadge(activeApproval?.approved)],
                ["Route bounds", yesNoBadge(asRecord(activeApprovalChecks?.route_bounds)?.ok ?? asRecord(activeApprovalChecks?.route_bounds)?.geofence_ok)],
                ["Weather", yesNoBadge(asRecord(activeApprovalChecks?.weather)?.ok)],
                ["No-fly zone", yesNoBadge(asRecord(activeApprovalChecks?.no_fly_zone)?.ok)],
                ["Regulation", yesNoBadge(asRecord(activeApprovalChecks?.regulations)?.ok)],
                ["Time", yesNoBadge(asRecord(activeApprovalChecks?.time_window)?.ok)],
                ["Operator", yesNoBadge(asRecord(activeApprovalChecks?.operator_license)?.ok)],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
                  <div style={{ minHeight: 18 }}>{value as React.ReactNode}</div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>USS Layer Details</div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <button type="button" style={chipStyle(false)} onClick={() => void deleteUssProblemIntents(ussProblemIntentIds)} disabled={busy || ussProblemIntentIds.length === 0}>
                  Delete Problem Intents ({ussProblemIntentIds.length})
                </button>
                <div>{yesNoBadge(layeredUss?.ok)}</div>
              </div>
            </div>
            <div style={{ fontSize: 11, color: layeredUss?.ok === true ? "#027a48" : "#b42318" }}>{ussLayerReason}</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8 }}>
              {[
                ["Participants", String(layeredUss?.participant_count ?? dssParticipants.length)],
                ["Active participants", String(layeredUss?.active_participant_count ?? "-")],
                ["Managers seen", String(layeredUss?.manager_uss_seen_count ?? ussManagerIds.length)],
                ["Unknown managers", String(layeredUss?.unknown_manager_count ?? layeredUnknownManagerIds.length)],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
                  <div style={{ minHeight: 18, fontSize: 14, fontWeight: 700, color: "#101828" }}>{String(value)}</div>
                </div>
              ))}
            </div>
            {layeredUnknownManagerIds.length > 0 ? (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {layeredUnknownManagerIds.slice(0, 8).map((manager) => (
                  <span key={manager} style={{ fontSize: 10, color: "#b42318", border: "1px solid #fecdca", borderRadius: 999, padding: "2px 6px", background: "#fef3f2" }}>
                    unknown: {manager}
                  </span>
                ))}
              </div>
            ) : null}
            <div style={{ overflowX: "auto", maxHeight: 190 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Manager USS</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Known</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Active</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Participant status</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Intent count</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {ussManagerRows.length === 0 ? (
                    <tr><td colSpan={6} style={{ padding: "8px 4px", color: "#667085" }}>No manager USS IDs found in intents.</td></tr>
                  ) : (
                    ussManagerRows.slice(0, 16).map((row) => (
                      <tr key={row.manager}>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.manager, 24)}</code></td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{yesNoBadge(row.known)}</td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{yesNoBadge(row.active)}</td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{row.participantStatus}</code></td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(row.intentCount)}</td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>
                          <button
                            type="button"
                            style={chipStyle(false)}
                            onClick={() => void deleteUssProblemIntents(ussIntentIdsByManager[row.manager] ?? [])}
                            disabled={busy || !Array.isArray(ussIntentIdsByManager[row.manager]) || (ussIntentIdsByManager[row.manager] ?? []).length === 0}
                          >
                            Delete ({(ussIntentIdsByManager[row.manager] ?? []).length})
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <div style={{ borderTop: "1px solid #eaecf0", paddingTop: 8, display: "grid", gap: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#101828" }}>USS-Linked Intents (Detailed)</div>
              <div style={{ overflowX: "auto", maxHeight: 220 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Intent</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>UAV</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Manager USS</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Known/Active</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>State/Priority</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Policy</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Blocking</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Lifecycle</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Time Window</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Updated</th>
                      <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ussIntentRows.length === 0 ? (
                      <tr><td colSpan={11} style={{ padding: "8px 4px", color: "#667085" }}>No USS-linked intents.</td></tr>
                    ) : (
                      ussIntentRows.slice(0, 20).map((row, idx) => (
                        <tr key={`${row.intentId}-${idx}`}>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.intentId, 24)}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{trimValue(row.uavId, 18)}</td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.managerUssId, 18)}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>
                            {yesNoBadge(row.known)} {yesNoBadge(row.active)}
                          </td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{row.state}/{row.priority}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{row.conflictPolicy}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(row.blocking)}</td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{trimValue(row.lifecyclePhase, 16)}</td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(`${row.timeStart} -> ${row.timeEnd}`, 26)}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.updatedAt, 24)}</code></td>
                          <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>
                            <button type="button" style={chipStyle(false)} onClick={() => void deleteDssIntent(row.intentId)} disabled={busy || !row.intentId}>Delete</button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>DSS Dispatcher</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button type="button" style={chipStyle(false)} onClick={() => void toggleDispatcherEnabled()} disabled={busy}>
                  {dispatch?.dispatcher_enabled === true ? "Disable Dispatcher" : "Enable Dispatcher"}
                </button>
                <button type="button" style={chipStyle(false)} onClick={() => void runDispatchCycle()} disabled={busy}>Run One Dispatch Cycle</button>
                <button type="button" style={chipStyle(true)} onClick={() => void runDssAutoRecover()} disabled={busy}>DSS Auto Recover</button>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 8, fontSize: 12 }}>
              {[
                ["Enabled", yesNoBadge(dispatch?.dispatcher_enabled)],
                ["Worker alive", yesNoBadge(dispatch?.worker_thread_alive)],
                ["Worker state", String(dispatch?.worker_stop_requested ? "STOP_REQUESTED" : "RUNNING")],
                ["Pending", String(dispatchCounts?.pending ?? "-")],
                ["Delivered", String(dispatchCounts?.delivered ?? "-")],
                ["Failed", String(dispatchCounts?.failed ?? "-")],
                ["Last attempted", String(dispatchLast?.attempted ?? "-")],
                ["Last delivered", String(dispatchLast?.delivered ?? "-")],
                ["Last failed", String(dispatchLast?.failed ?? "-")],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
                  <div style={{ minHeight: 18 }}>{value as React.ReactNode}</div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              Interval {String(dispatchConfig?.interval_s ?? "-")}s, batch {String(dispatchConfig?.batch_size ?? "-")}, timeout {String(dispatchConfig?.timeout_s ?? "-")}s, max attempts {String(dispatchConfig?.max_attempts ?? "-")}. Last update <code>{String(dispatchLast?.updated_at ?? "-")}</code>.
            </div>
          </div>

          <div style={{ ...cardStyle, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>DSS Detail Data</div>
              <div style={{ fontSize: 11, color: "#667085" }}>
                Adapter <code>{String(dssSummary?.intents_adapter_mode ?? "-")}</code> / Subscriptions <code>{String(dssSummary?.subscriptions_adapter_mode ?? "-")}</code>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8 }}>
              {[
                ["Intents", dssOperationalIntents.length],
                ["Subscriptions", dssSubscriptions.length],
                ["Participants", dssParticipants.length],
                ["Recent Notifications", dssNotifications.length],
              ].map(([label, value]) => (
                <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
                  <div style={{ fontSize: 11, color: "#667085" }}>{String(label)}</div>
                  <div style={{ fontSize: 14, fontWeight: 700, color: "#101828" }}>{String(value)}</div>
                </div>
              ))}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8 }}>
              <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: 8, display: "grid", gap: 6 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#101828" }}>Operational Intents</div>
                <div style={{ overflowX: "auto", maxHeight: 180 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                    <thead>
                      <tr>
                        <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Intent</th>
                        <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>UAV</th>
                        <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>USS</th>
                        <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>State</th>
                        <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Blocking</th>
                        <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Updated</th>
                        <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dssOperationalIntents.length === 0 ? (
                        <tr><td colSpan={7} style={{ padding: "8px 4px", color: "#667085" }}>No intents.</td></tr>
                      ) : (
                        dssOperationalIntents.slice(0, 12).map((row) => {
                          const metadata = asRecord(row.metadata);
                          const conflictSummary = asRecord(row.conflict_summary);
                          const intentId = String(row.intent_id ?? "").trim();
                          return (
                            <tr key={intentId || String(Math.random())}>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.intent_id, 24)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{trimValue(metadata?.uav_id ?? "-", 18)}</td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{trimValue(row.manager_uss_id ?? "-", 18)}</td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(row.state ?? "-")}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(conflictSummary?.blocking ?? 0)}</td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.updated_at ?? "-", 24)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>
                                <button type="button" style={chipStyle(false)} onClick={() => void deleteDssIntent(intentId)} disabled={busy || !intentId}>Delete</button>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: 8, display: "grid", gap: 6 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#101828" }}>Subscriptions / Participants</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <div style={{ overflowX: "auto", maxHeight: 160 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Subscription</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>USS</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Expires</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dssSubscriptions.length === 0 ? (
                          <tr><td colSpan={3} style={{ padding: "8px 4px", color: "#667085" }}>No subscriptions.</td></tr>
                        ) : (
                          dssSubscriptions.slice(0, 10).map((row) => (
                            <tr key={String(row.subscription_id ?? Math.random())}>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.subscription_id, 22)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{trimValue(row.manager_uss_id ?? "-", 18)}</td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.expires_at ?? "-", 24)}</code></td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>

                  <div style={{ overflowX: "auto", maxHeight: 160 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Participant</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Status</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Roles</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dssParticipants.length === 0 ? (
                          <tr><td colSpan={3} style={{ padding: "8px 4px", color: "#667085" }}>No participants.</td></tr>
                        ) : (
                          dssParticipants.slice(0, 10).map((row) => (
                            <tr key={String(row.participant_id ?? Math.random())}>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.participant_id, 22)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(row.status ?? "-")}</td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{trimValue(Array.isArray(row.roles) ? row.roles.join(",") : "-", 20)}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: 8, display: "grid", gap: 6 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#101828" }}>DSS Notifications</div>
                  <button type="button" style={chipStyle(false)} onClick={() => setDssNotifExpanded((v) => !v)}>
                    {dssNotifExpanded ? "Collapse" : "Expand"}
                  </button>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8, fontSize: 11 }}>
                  {[
                    ["Pending", dssNotifStatusCounts.pending],
                    ["Delivered", dssNotifStatusCounts.delivered],
                    ["Failed", dssNotifStatusCounts.failed],
                    ["Acked", dssNotifStatusCounts.acked],
                  ].map(([label, value]) => (
                    <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fcfcfd", padding: "6px 8px", display: "grid", gap: 2 }}>
                      <div style={{ color: "#667085" }}>{String(label)}</div>
                      <div style={{ fontWeight: 700, color: "#101828" }}>{String(value)}</div>
                    </div>
                  ))}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "minmax(130px, 1fr) minmax(130px, 1fr) auto", gap: 8, alignItems: "end" }}>
                  <label style={{ fontSize: 12 }}>Status
                    <select style={inputStyle} value={dssNotifStatusFilter} onChange={(e) => setDssNotifStatusFilter(e.target.value)}>
                      {["pending", "delivered", "failed", "acked", "all"].map((status) => (
                        <option key={status} value={status}>{status}</option>
                      ))}
                    </select>
                  </label>
                  <label style={{ fontSize: 12 }}>Type
                    <select style={inputStyle} value={dssNotifTypeFilter} onChange={(e) => setDssNotifTypeFilter(e.target.value)}>
                      <option value="all">all</option>
                      {dssNotifTypeOptions.map((typeId) => (
                        <option key={typeId} value={typeId}>{typeId}</option>
                      ))}
                    </select>
                  </label>
                  <div style={{ fontSize: 11, color: "#667085", paddingBottom: 8 }}>
                    Showing {String(dssNotificationsFiltered.length)} / {String(dssNotifications.length)}
                  </div>
                </div>
                {dssNotifExpanded ? (
                  <div style={{ overflowX: "auto", maxHeight: 220 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Notification</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Status</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Type</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Source Intent</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Position/Callback</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Attempts</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Last Error</th>
                          <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dssNotificationsFiltered.length === 0 ? (
                          <tr><td colSpan={8} style={{ padding: "8px 4px", color: "#667085" }}>No notifications for selected status/type.</td></tr>
                        ) : (
                          dssNotificationsFiltered.slice(0, 120).map((row) => {
                            const notificationId = String(row.notification_id ?? "").trim();
                            const status = String(row.status ?? "").trim().toLowerCase();
                            return (
                            <tr key={notificationId || String(Math.random())}>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.notification_id, 24)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(row.status ?? "-")}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{trimValue(row.event_type ?? "-", 14)}</td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.source_intent_id ?? "-", 24)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.callback_url ?? "-", 28)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(row.dispatch_attempts ?? 0)}</td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{trimValue(row.last_error ?? "-", 22)}</code></td>
                              <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>
                                <button
                                  type="button"
                                  style={chipStyle(false)}
                                  onClick={() => void ackDssNotification(notificationId)}
                                  disabled={busy || !notificationId || status === "acked"}
                                >
                                  {status === "acked" ? "Acked" : "Ack"}
                                </button>
                              </td>
                            </tr>
                          );
                        })
                        )}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div style={{ fontSize: 11, color: "#667085" }}>Expanded view shows pending DSS and other statuses by type and callback position.</div>
                )}
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
