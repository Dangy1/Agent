import React, { useEffect, useMemo, useState } from "react";
import { MissionSyncMap, type MissionBs, type MissionCoverage, type MissionNfz, type MissionTrack } from "./MissionSyncMap";
import { bumpSharedRevision, getSharedPageState, patchSharedPageState, subscribeSharedPageState } from "./pageSync";

type UavSimState = {
  uav?: Record<string, unknown>;
  utm?: {
    weather?: Record<string, unknown>;
    no_fly_zones?: Array<Record<string, unknown>>;
    regulations?: Record<string, unknown>;
    licenses?: Record<string, unknown>;
  };
};
type UtmCheckResults = {
  route?: Record<string, unknown>;
  timeWindow?: Record<string, unknown>;
  license?: Record<string, unknown>;
  verify?: Record<string, unknown>;
  corridor?: Record<string, unknown>;
};
type UtmSourceInfo = { mode?: string; active?: string; meta?: Record<string, unknown> | null };
type AgentActionLogItem = {
  id: number;
  action: string;
  entity_id?: unknown;
  payload?: unknown;
  result?: unknown;
  created_at: string;
  agent: "uav" | "utm";
};

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): Record<string, unknown> | null {
  return isObject(x) ? x : null;
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

function readSyncRevision(data: unknown): number | null {
  const root = asRecord(data);
  const result = asRecord(root?.result);
  const sync = asRecord(result?.sync ?? root?.sync);
  return typeof sync?.revision === "number" ? sync.revision : null;
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

function renderUtmDecisionReadable(decisionInput: unknown): React.ReactNode {
  const decision = asRecord(decisionInput);
  if (!decision) return null;
  const reasons = Array.isArray(decision.reasons) ? (decision.reasons as unknown[]).map(String) : [];
  const messages = Array.isArray(decision.messages) ? (decision.messages as unknown[]).map(String) : [];
  const suggestions = Array.isArray(decision.suggestions) ? (decision.suggestions as unknown[]).map(String) : [];
  const nfzSummary = asRecord(decision.nfz_conflict_summary);
  const wpIds = Array.isArray(nfzSummary?.waypoints) ? (nfzSummary!.waypoints as unknown[]).map(String) : [];
  const segIds = Array.isArray(nfzSummary?.segments) ? (nfzSummary!.segments as unknown[]).map(String) : [];
  const status = String(decision.status ?? "-");
  const approved = status === "approved";
  const conciseMsg = messages.find((m) => m.trim()) ?? "";
  const conciseSuggestion = suggestions.find((s) => s.trim()) ?? "";
  return (
    <div style={{ border: `1px solid ${approved ? "#abefc6" : "#fecdca"}`, borderRadius: 8, background: approved ? "#ecfdf3" : "#fef3f2", padding: 8, display: "grid", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{
          display: "inline-block",
          borderRadius: 999,
          padding: "2px 8px",
          fontSize: 11,
          fontWeight: 700,
          background: approved ? "#d1fadf" : "#fee4e2",
          color: approved ? "#027a48" : "#b42318",
          border: `1px solid ${approved ? "#abefc6" : "#fecdca"}`,
        }}>
          {status.toUpperCase()}
        </span>
        {reasons.slice(0, 3).map((r, i) => (
          <span key={`utm-decision-reason-${i}`} style={{ fontSize: 11, color: "#667085", border: "1px solid #d0d5dd", borderRadius: 999, padding: "2px 8px", background: "#fff" }}>
            {r}
          </span>
        ))}
        {(wpIds.length || segIds.length) ? (
          <span style={{ fontSize: 11, color: "#b42318", border: "1px solid #fecdca", borderRadius: 999, padding: "2px 8px", background: "#fff" }}>
            {wpIds.length ? `WP ${wpIds.join(",")}` : ""}{wpIds.length && segIds.length ? " • " : ""}{segIds.length ? `SEG ${segIds.join(",")}` : ""}
          </span>
        ) : null}
      </div>
      {conciseMsg ? <div style={{ fontSize: 12, color: "#344054" }}>{conciseMsg}</div> : null}
      {!approved && conciseSuggestion ? <div style={{ fontSize: 11, color: "#b54708" }}>{conciseSuggestion}</div> : null}
    </div>
  );
}

function summarizeInteraction(item: AgentActionLogItem): string {
  const result = asRecord(item.result);
  const payload = asRecord(item.payload);
  if (item.action.includes("verify")) {
    const approved = result?.approved;
    const decision = asRecord(result?.decision);
    const reasons = Array.isArray(decision?.reasons) ? (decision!.reasons as unknown[]).map(String).join(", ") : "";
    const routeSource = typeof result?.route_source === "string" ? String(result.route_source) : "";
    return `verify ${approved === true ? "approved" : approved === false ? "rejected" : "done"}${routeSource ? ` • route=${routeSource}` : ""}${reasons ? ` • ${reasons}` : ""}`;
  }
  if (item.action.includes("route_check") || item.action === "route_checks") {
    const geofence = asRecord(result?.geofence);
    const geofenceOk = geofence?.geofence_ok;
    const nfz = asRecord(result?.no_fly_zone);
    return `route check • route_bounds=${String(geofenceOk)} • nfz=${String(nfz?.ok)}`;
  }
  if (item.action.includes("approval")) {
    const approved = result?.approved ?? asRecord(result?.result)?.approved;
    return `approval ${approved === true ? "approved" : approved === false ? "rejected" : "updated"}`;
  }
  if (item.action.includes("nfz")) {
    const zoneId = String(asRecord(result?.result)?.zone_id ?? result?.zone_id ?? payload?.zone_id ?? payload?.reason ?? "");
    return `no-fly-zone update${zoneId ? ` • ${zoneId}` : ""}`;
  }
  if (item.action.includes("weather")) {
    return `weather update/check`;
  }
  if (item.action.includes("license")) {
    const lic = String(payload?.operator_license_id ?? asRecord(result?.result)?.operator_license_id ?? "");
    return `license ${item.action.includes("check") ? "check" : "update"}${lic ? ` • ${lic}` : ""}`;
  }
  if (item.action.includes("corridor")) {
    return "corridor reservation";
  }
  if (item.action === "uav_live_ingest" || item.action === "utm_live_ingest") {
    return "live data ingested";
  }
  return item.action.replaceAll("_", " ");
}

export function UtmPage() {
  const sharedInit = getSharedPageState();
  const [apiBase, setApiBase] = useState(sharedInit.utmApiBase || "http://127.0.0.1:8021");
  const [uavApiBase, setUavApiBase] = useState(sharedInit.uavApiBase || "http://127.0.0.1:8020");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [simUavId, setSimUavId] = useState(sharedInit.uavId || "uav-1");
  const [airspace, setAirspace] = useState(sharedInit.airspace || "sector-A3");
  const [wind, setWind] = useState(8);
  const [visibility, setVisibility] = useState(10);
  const [precip, setPrecip] = useState(0);
  const [storm, setStorm] = useState(false);
  const [weatherCheck, setWeatherCheck] = useState<Record<string, unknown> | null>(null);
  const [state, setState] = useState<UavSimState | null>(null);

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
  const [liveRefresh, setLiveRefresh] = useState(false);
  const [liveRefreshSec, setLiveRefreshSec] = useState("3");
  const [networkMap, setNetworkMap] = useState<{ bs: MissionBs[]; coverage: MissionCoverage[]; tracks: MissionTrack[] }>({ bs: [], coverage: [], tracks: [] });
  const [nfzDraftX, setNfzDraftX] = useState("150");
  const [nfzDraftY, setNfzDraftY] = useState("110");
  const [nfzDraftRadiusM, setNfzDraftRadiusM] = useState("30");
  const [nfzDraftReason, setNfzDraftReason] = useState("operator_defined");
  const [nfzDraftZMin, setNfzDraftZMin] = useState("0");
  const [nfzDraftZMax, setNfzDraftZMax] = useState("120");
  const [backendRevisions, setBackendRevisions] = useState<{ uav: number; utm: number; network: number }>({ uav: -1, utm: -1, network: -1 });
  const [utmSourceInfo, setUtmSourceInfo] = useState<UtmSourceInfo | null>(null);
  const [interactionLog, setInteractionLog] = useState<AgentActionLogItem[]>([]);
  const [interactionLogClearedAt, setInteractionLogClearedAt] = useState<string | null>(null);
  const [utmLiveJson, setUtmLiveJson] = useState(
    JSON.stringify(
      {
        source: "ops_utm_feed",
        source_ref: "utm-prod-bridge",
        observed_at: "2026-02-24T20:00:00Z",
        airspace_segment: "sector-A3",
        weather: { wind_mps: 7, visibility_km: 10, precip_mmph: 0, storm_alert: false },
        no_fly_zones: [{ zone_id: "nfz-top-live", cx: 150, cy: 110, radius_m: 35, z_min: 0, z_max: 120, reason: "hospital_helipad" }],
      },
      null,
      2,
    ),
  );

  const loadAll = async () => {
    setBusy(true);
    setMsg("");
    try {
      const simStateQs = new URLSearchParams({ uav_id: simUavId });
      if (licenseId.trim()) simStateQs.set("operator_license_id", licenseId.trim());
      const [simRes, wRes, srcRes, uavSyncRes, utmSyncRes] = await Promise.all([
        fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/sim/state?${simStateQs.toString()}`),
        fetch(`${normalizeBaseUrl(apiBase)}/api/utm/weather?airspace_segment=${encodeURIComponent(airspace)}`),
        fetch(`${normalizeBaseUrl(apiBase)}/api/utm/live/source`),
        fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/sync?limit_actions=12`),
        fetch(`${normalizeBaseUrl(apiBase)}/api/utm/sync?limit_actions=18`),
      ]);
      const simData = await simRes.json();
      const wData = await wRes.json();
      const srcData = await srcRes.json();
      const uavSyncData = await uavSyncRes.json();
      const utmSyncData = await utmSyncRes.json();
      if (!simRes.ok || !isObject(simData)) throw new Error(String(asRecord(simData)?.detail ?? "Simulator state request failed"));
      if (!wRes.ok || !isObject(wData)) throw new Error(String(asRecord(wData)?.detail ?? "Weather request failed"));
      setState(simData as UavSimState);
      setUtmSourceInfo(asRecord((srcData as Record<string, unknown>)?.result) as UtmSourceInfo | null);
      const uavRecent = Array.isArray(asRecord(asRecord(uavSyncData)?.result)?.recentActions)
        ? (asRecord(asRecord(uavSyncData)?.result)?.recentActions as unknown[])
            .filter(isObject)
            .map((r) => ({
              id: Number((r as Record<string, unknown>).id ?? 0),
              action: String((r as Record<string, unknown>).action ?? ""),
              entity_id: (r as Record<string, unknown>).entity_id,
              payload: (r as Record<string, unknown>).payload,
              result: (r as Record<string, unknown>).result,
              created_at: String((r as Record<string, unknown>).created_at ?? ""),
              agent: "uav" as const,
            }))
        : [];
      const utmRecent = Array.isArray(asRecord(asRecord(utmSyncData)?.result)?.recentActions)
        ? (asRecord(asRecord(utmSyncData)?.result)?.recentActions as unknown[])
            .filter(isObject)
            .map((r) => ({
              id: Number((r as Record<string, unknown>).id ?? 0),
              action: String((r as Record<string, unknown>).action ?? ""),
              entity_id: (r as Record<string, unknown>).entity_id,
              payload: (r as Record<string, unknown>).payload,
              result: (r as Record<string, unknown>).result,
              created_at: String((r as Record<string, unknown>).created_at ?? ""),
              agent: "utm" as const,
            }))
        : [];
      const merged = [...utmRecent, ...uavRecent]
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      const filtered = interactionLogClearedAt
        ? merged.filter((r) => String(r.created_at) >= interactionLogClearedAt)
        : merged;
      setInteractionLog(filtered.slice(0, 24));
      const result = asRecord((wData as Record<string, unknown>).result);
      setWeatherCheck(result);
      const currentWeather = asRecord(result?.weather);
      if (currentWeather) {
        if (typeof currentWeather.wind_mps === "number") setWind(currentWeather.wind_mps);
        if (typeof currentWeather.visibility_km === "number") setVisibility(currentWeather.visibility_km);
        if (typeof currentWeather.precip_mmph === "number") setPrecip(currentWeather.precip_mmph);
        if (typeof currentWeather.storm_alert === "boolean") setStorm(currentWeather.storm_alert);
      }
      const licenses = asRecord((simData as Record<string, unknown>).utm && asRecord((simData as Record<string, unknown>).utm)?.licenses);
      const lic = licenses ? asRecord(licenses[licenseId]) : null;
      if (lic) {
        if (typeof lic.license_class === "string") setLicenseClass(lic.license_class);
        if (typeof lic.uav_size_class === "string") setLicenseUavSizeClass(lic.uav_size_class);
        if (typeof lic.active === "boolean") setLicenseActive(lic.active);
        if (typeof lic.expires_at === "string") {
          const local = isoUtcToLocalInput(lic.expires_at);
          if (local) setLicenseExpiry(local);
        }
      }
      setMsg("Loaded UTM state");
      try {
        const shared = getSharedPageState();
        const netRes = await fetch(`${shared.networkApiBase.replace(/\/+$/, "")}/api/network/mission/state?airspace_segment=${encodeURIComponent(airspace)}&selected_uav_id=${encodeURIComponent(simUavId)}`);
        const netData = await netRes.json();
        const resultNet = asRecord(asRecord(netData)?.result);
        const bs = Array.isArray(resultNet?.baseStations)
          ? (resultNet!.baseStations as unknown[]).filter(isObject).map((b) => ({
              id: String((b as Record<string, unknown>).id ?? "BS"),
              x: Number((b as Record<string, unknown>).x ?? 0),
              y: Number((b as Record<string, unknown>).y ?? 0),
              status: String((b as Record<string, unknown>).status ?? "online"),
            }))
          : [];
        const coverage = Array.isArray(resultNet?.coverage)
          ? (resultNet!.coverage as unknown[]).filter(isObject).map((c) => ({
              bsId: String((c as Record<string, unknown>).bsId ?? ""),
              radiusM: Number((c as Record<string, unknown>).radiusM ?? 0),
            }))
          : [];
        const tracks = Array.isArray(resultNet?.trackingSnapshots)
          ? (resultNet!.trackingSnapshots as unknown[]).filter(isObject).map((t) => ({
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
        // optional network overlay
      }
    } catch (e) {
      setMsg(`Load failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    patchSharedPageState({ utmApiBase: apiBase, uavApiBase, uavId: simUavId, airspace });
  }, [apiBase, uavApiBase, simUavId, airspace]);

  useEffect(() => {
    let lastRevision = getSharedPageState().revision;
    return subscribeSharedPageState((next) => {
      if (next.utmApiBase && next.utmApiBase !== apiBase) setApiBase(next.utmApiBase);
      if (next.uavApiBase && next.uavApiBase !== uavApiBase) setUavApiBase(next.uavApiBase);
      if (next.uavId && next.uavId !== simUavId) setSimUavId(next.uavId);
      if (next.airspace && next.airspace !== airspace) setAirspace(next.airspace);
      if (next.revision !== lastRevision) {
        lastRevision = next.revision;
        void loadAll();
      }
    });
  }, [apiBase, uavApiBase, simUavId, airspace]);

  useEffect(() => {
    if (!liveRefresh) return;
    const seconds = Math.max(1, Number.parseInt(liveRefreshSec || "3", 10) || 3);
    const id = window.setInterval(() => {
      if (!busy) void loadAll();
    }, seconds * 1000);
    return () => window.clearInterval(id);
  }, [liveRefresh, liveRefreshSec, busy]);

  useEffect(() => {
    const id = window.setInterval(() => {
      void (async () => {
        if (busy) return;
        try {
          const shared = getSharedPageState();
          const [uavRes, utmRes, netRes] = await Promise.all([
            fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/sync`),
            fetch(`${normalizeBaseUrl(apiBase)}/api/utm/sync`),
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
            if (backendRevisions.uav >= 0 || backendRevisions.utm >= 0 || backendRevisions.network >= 0) void loadAll();
          }
        } catch {
          // optional auto-refresh path
        }
      })();
    }, 1500);
    return () => window.clearInterval(id);
  }, [busy, apiBase, uavApiBase, backendRevisions, simUavId, airspace, interactionLogClearedAt]);

  const postApi = async (path: string, body: unknown, successMsg: string) => {
    setBusy(true);
    setMsg("");
    try {
      const res = await fetch(`${normalizeBaseUrl(apiBase)}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(String(asRecord(data)?.detail ?? "Request failed"));
      setMsg(successMsg);
      await loadAll();
      bumpSharedRevision();
    } catch (e) {
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(false);
    }
  };

  const postApiResult = async (path: string, body: unknown): Promise<Record<string, unknown> | null> => {
    setBusy(true);
    setMsg("");
    try {
      const res = await fetch(`${normalizeBaseUrl(apiBase)}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Request failed"));
      return data as Record<string, unknown>;
    } catch (e) {
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const postJsonToBase = async (baseUrl: string, path: string, body: unknown) => {
    const res = await fetch(`${normalizeBaseUrl(baseUrl)}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !isObject(data)) throw new Error(String(asRecord(data)?.detail ?? "Request failed"));
    return data as Record<string, unknown>;
  };

  const saveWeather = async () => {
    await postApi(
      "/api/utm/weather",
      { airspace_segment: airspace, wind_mps: wind, visibility_km: visibility, precip_mmph: precip, storm_alert: storm },
      "Weather updated",
    );
  };

  const registerLicense = async () => {
    const expiresIso = localInputToIsoUtc(licenseExpiry);
    if (licenseExpiry && !expiresIso) {
      setMsg("Action failed: invalid license expiry");
      return;
    }
    await postApi(
      "/api/utm/license",
      {
        operator_license_id: licenseId,
        license_class: licenseClass,
        uav_size_class: licenseUavSizeClass,
        expires_at: expiresIso ?? "2099-01-01T00:00:00Z",
        active: licenseActive,
      },
      "License registered",
    );
  };

  const runRouteChecks = async () => {
    const speed = Number.parseFloat(requestedSpeedMps);
    const uavRec = asRecord(state?.uav);
    const routeWaypoints = Array.isArray(uavRec?.waypoints)
      ? (uavRec!.waypoints as unknown[]).filter(isObject).map((w) => ({ x: Number((w as Record<string, unknown>).x ?? 0), y: Number((w as Record<string, unknown>).y ?? 0), z: Number((w as Record<string, unknown>).z ?? 0) }))
      : [];
    const data = await postApiResult("/api/utm/checks/route", {
      uav_id: simUavId,
      airspace_segment: airspace,
      requested_speed_mps: Number.isFinite(speed) ? speed : 12,
      operator_license_id: licenseId,
      route_id: typeof uavRec?.route_id === "string" ? uavRec.route_id : undefined,
      waypoints: routeWaypoints.length ? routeWaypoints : undefined,
    });
    if (!data) return;
    setUtmChecks((prev) => ({ ...prev, route: asRecord(data.result) ?? {} }));
    setMsg("Route bounds/NFZ/regulation checks completed");
    await loadAll();
  };

  const runTimeWindowCheck = async () => {
    const data = await postApiResult("/api/utm/checks/time-window", {
      planned_start_at: localInputToIsoUtc(plannedStartAt),
      planned_end_at: localInputToIsoUtc(plannedEndAt),
      operator_license_id: licenseId,
    });
    if (!data) return;
    setUtmChecks((prev) => ({ ...prev, timeWindow: asRecord(data.result) ?? {} }));
    setMsg("Time-window check completed");
  };

  const runLicenseCheck = async () => {
    const data = await postApiResult("/api/utm/checks/license", {
      operator_license_id: licenseId,
      required_license_class: requiredLicenseClass,
    });
    if (!data) return;
    setUtmChecks((prev) => ({ ...prev, license: asRecord(data.result) ?? {} }));
    setMsg("License check completed");
  };

  const runVerifyFromUav = async () => {
    const speed = Number.parseFloat(requestedSpeedMps);
    const uavRec = asRecord(state?.uav);
    const routeWaypoints = Array.isArray(uavRec?.waypoints)
      ? (uavRec!.waypoints as unknown[]).filter(isObject).map((w) => ({ x: Number((w as Record<string, unknown>).x ?? 0), y: Number((w as Record<string, unknown>).y ?? 0), z: Number((w as Record<string, unknown>).z ?? 0) }))
      : [];
    const data = await postApiResult("/api/utm/verify-from-uav", {
      uav_id: simUavId,
      airspace_segment: airspace,
      operator_license_id: licenseId,
      required_license_class: requiredLicenseClass,
      requested_speed_mps: Number.isFinite(speed) ? speed : 12,
      planned_start_at: localInputToIsoUtc(plannedStartAt),
      planned_end_at: localInputToIsoUtc(plannedEndAt),
      route_id: typeof uavRec?.route_id === "string" ? uavRec.route_id : undefined,
      waypoints: routeWaypoints.length ? routeWaypoints : undefined,
    });
    if (!data) return;
    setUtmChecks((prev) => ({ ...prev, verify: asRecord(data.result) ?? {} }));
    setMsg("UTM flight-plan verification completed");
    await loadAll();
  };

  const reserveCorridor = async () => {
    const data = await postApiResult("/api/utm/corridor/reserve", { uav_id: simUavId, airspace_segment: airspace });
    if (!data) return;
    setUtmChecks((prev) => ({ ...prev, corridor: asRecord(data.result) ?? {} }));
    setMsg("Corridor reservation simulated");
  };

  const ingestUtmLive = async () => {
    try {
      const parsed = JSON.parse(utmLiveJson);
      if (!isObject(parsed)) throw new Error("JSON payload must be an object");
      const payload = { ...(parsed as Record<string, unknown>) };
      if (typeof payload.airspace_segment !== "string" || !String(payload.airspace_segment).trim()) payload.airspace_segment = airspace;
      await postApi("/api/utm/live/ingest", payload, "UTM live data ingested");
    } catch (e) {
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const addNoFlyZoneAt = async (point: { x: number; y: number; z?: number }) => {
    const radius_m = Number.parseFloat(nfzDraftRadiusM);
    const z_min = Number.parseFloat(nfzDraftZMin);
    const z_max = Number.parseFloat(nfzDraftZMax);
    if (!Number.isFinite(radius_m) || radius_m <= 0) {
      setMsg("Action failed: NFZ radius must be positive");
      return;
    }
    if (!Number.isFinite(z_min) || !Number.isFinite(z_max) || z_max < z_min) {
      setMsg("Action failed: NFZ altitude range invalid");
      return;
    }
    setBusy(true);
    setMsg("");
    try {
      const payload = {
        cx: point.x,
        cy: point.y,
        radius_m,
        z_min,
        z_max,
        reason: nfzDraftReason.trim() || "operator_defined",
      };
      await postJsonToBase(apiBase, "/api/utm/nfz", payload);
      try {
        await postJsonToBase(uavApiBase, "/api/utm/nfz", payload);
      } catch {
        // keep UTM source of truth even if UAV-side mirror is down
      }
      setMsg(`NFZ added at (x ${point.x.toFixed(1)}, y ${point.y.toFixed(1)})`);
      setNfzDraftX(String(Number(point.x.toFixed(1))));
      setNfzDraftY(String(Number(point.y.toFixed(1))));
      bumpSharedRevision();
      await loadAll();
    } catch (e) {
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(false);
    }
  };

  const addNoFlyZoneByMap = async (point: { x: number; y: number; z?: number }) => addNoFlyZoneAt(point);

  const addNoFlyZoneByForm = async () => {
    const x = Number.parseFloat(nfzDraftX);
    const y = Number.parseFloat(nfzDraftY);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      setMsg("Action failed: NFZ X/Y center must be valid numbers");
      return;
    }
    await addNoFlyZoneAt({ x, y, z: Number.parseFloat(nfzDraftZMin) });
  };

  const utm = asRecord(state?.utm);
  const nfz = Array.isArray(utm?.no_fly_zones) ? (utm?.no_fly_zones as unknown[]).filter(isObject) : [];
  const regulationProfiles = asRecord(utm?.regulation_profiles ?? utm?.regulationProfiles);
  const effectiveRegs = asRecord(utm?.effective_regulations ?? utm?.effectiveRegulations);
  const licenses = asRecord(utm?.licenses) ?? {};
  const licenseRows = useMemo(() => Object.entries(licenses), [licenses]);
  const uav = asRecord(state?.uav);
  const approval = asRecord(uav?.utm_approval);
  const weatherResultChecks = asRecord(weatherCheck?.checks);
  const routeCheck = asRecord(utmChecks.route);
  const routeGeofence = asRecord(routeCheck?.geofence);
  const routeNfz = asRecord(routeCheck?.no_fly_zone);
  const routeRegs = asRecord(routeCheck?.regulations);
  const timeWindowCheck = asRecord(utmChecks.timeWindow);
  const licenseCheck = asRecord(utmChecks.license);
  const verifyCheck = asRecord(utmChecks.verify);
  const corridorCheck = asRecord(utmChecks.corridor);
  const syncedApproval = verifyCheck ?? approval;
  const syncedApprovalChecks = asRecord(syncedApproval?.checks);
  const routePointsForMap = Array.isArray(uav?.waypoints)
    ? (uav!.waypoints as unknown[]).filter(isObject).map((w) => ({ x: Number((w as Record<string, unknown>).x ?? 0), y: Number((w as Record<string, unknown>).y ?? 0), z: Number((w as Record<string, unknown>).z ?? 0) }))
    : [];
  const plannedPosForMap = routePointsForMap.length > 0 ? routePointsForMap[0] : null;
  const nfzZonesForMap: MissionNfz[] = nfz.map((z) => ({
    zone_id: String(z.zone_id ?? "nfz"),
    cx: Number(z.cx ?? 0),
    cy: Number(z.cy ?? 0),
    radius_m: Number(z.radius_m ?? 0),
    z_min: Number(z.z_min ?? 0),
    z_max: Number(z.z_max ?? 120),
    reason: String(z.reason ?? ""),
  }));
  const interactionLogPanel = (
    <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8, alignContent: "start" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontWeight: 700, color: "#101828" }}>UAV ↔ UTM Interaction Log</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div style={{ fontSize: 11, color: "#667085" }}>Merged recent actions from `uav` + `utm` backends</div>
          <button
            type="button"
            style={chipStyle(false)}
            onClick={() => {
              setInteractionLog([]);
              setInteractionLogClearedAt(new Date().toISOString());
              void loadAll();
            }}
            disabled={busy}
          >
            Clear + Refresh
          </button>
        </div>
      </div>
      <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", maxHeight: 360, overflow: "auto", display: "grid", gap: 6, padding: 8 }}>
        {interactionLog.length === 0 ? (
          <div style={{ fontSize: 12, color: "#667085" }}>No interactions recorded yet. Run `Route`, `Verify`, `Approval`, or use UAV copilot actions.</div>
        ) : (
          interactionLog.map((item) => {
            const resultRec = asRecord(item.result);
            const decision = asRecord(resultRec?.decision);
            return (
              <div key={`${item.agent}-${item.id}-${item.created_at}`} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fcfcfd", padding: 8, display: "grid", gap: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                  <div style={{ fontSize: 11, color: item.agent === "utm" ? "#b54708" : "#155eef", fontWeight: 700 }}>
                    {item.agent.toUpperCase()} • <code>{item.action}</code>
                  </div>
                  <div style={{ fontSize: 10, color: "#667085" }}>{new Date(item.created_at).toLocaleString()}</div>
                </div>
                <div style={{ fontSize: 12, color: "#344054" }}>{summarizeInteraction(item)}</div>
                {item.entity_id != null ? <div style={{ fontSize: 11, color: "#667085" }}>entity: <code>{String(item.entity_id)}</code></div> : null}
                {decision ? (
                  <div style={{ fontSize: 11, color: decision.status === "approved" ? "#027a48" : "#b42318" }}>
                    decision: <b>{String(decision.status ?? "-")}</b>
                    {Array.isArray(decision.reasons) && (decision.reasons as unknown[]).length > 0 ? ` (${(decision.reasons as unknown[]).map(String).join(", ")})` : ""}
                  </div>
                ) : null}
                <details>
                  <summary style={{ cursor: "pointer", fontSize: 11, color: "#667085" }}>Payload / Result</summary>
                  <pre style={{ margin: "6px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>
{JSON.stringify({ payload: item.payload, result: item.result }, null, 2)}
                  </pre>
                </details>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
  const checksActionsPanel = (
    <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
      <div style={{ fontWeight: 700, color: "#101828" }}>UTM Checks & Actions</div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button type="button" style={chipStyle(false)} onClick={() => void runRouteChecks()} disabled={busy}>Route</button>
        <button type="button" style={chipStyle(false)} onClick={() => void runTimeWindowCheck()} disabled={busy}>Time</button>
        <button type="button" style={chipStyle(false)} onClick={() => void runLicenseCheck()} disabled={busy}>License</button>
        <button type="button" style={chipStyle(false)} onClick={() => void runVerifyFromUav()} disabled={busy}>Verify</button>
        <button type="button" style={chipStyle(false)} onClick={() => void reserveCorridor()} disabled={busy}>Corridor</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8, fontSize: 12 }}>
        {[
          ["Route bounds", yesNoBadge(routeGeofence?.geofence_ok ?? routeGeofence?.bounds_ok ?? routeGeofence?.ok)],
          ["Route NFZ", yesNoBadge(routeNfz?.ok)],
          ["Route regulations", yesNoBadge(routeRegs?.ok)],
          ["Time window", yesNoBadge(timeWindowCheck?.ok)],
          ["License", yesNoBadge(licenseCheck?.ok)],
          ["Verify flight plan", yesNoBadge(verifyCheck?.approved)],
          ["Corridor reserved", yesNoBadge(corridorCheck?.reserved)],
        ].map(([label, value]) => (
          <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
            <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
            <div style={{ minHeight: 18 }}>{value as React.ReactNode}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: "#667085" }}>
        Results open in the fixed bottom log panel. Use the selector there to switch between Route / Time / License / Verify / Corridor.
      </div>
    </div>
  );
  const weatherControlsPanel = (
    <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
      <div style={{ fontWeight: 700, color: "#101828" }}>Weather Controls ({airspace})</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <label style={{ fontSize: 12 }}>Wind: {wind.toFixed(1)} m/s<input type="range" min={0} max={25} step={0.5} value={wind} onChange={(e) => setWind(Number(e.target.value))} style={{ width: "100%" }} /></label>
        <label style={{ fontSize: 12 }}>Visibility: {visibility.toFixed(1)} km<input type="range" min={0.5} max={20} step={0.5} value={visibility} onChange={(e) => setVisibility(Number(e.target.value))} style={{ width: "100%" }} /></label>
        <label style={{ fontSize: 12 }}>Precip: {precip.toFixed(1)} mm/h<input type="range" min={0} max={10} step={0.5} value={precip} onChange={(e) => setPrecip(Number(e.target.value))} style={{ width: "100%" }} /></label>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingTop: 18 }}>
          <input type="checkbox" checked={storm} onChange={(e) => setStorm(e.target.checked)} /> Storm alert
        </label>
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "space-between", alignItems: "center", flexWrap: "wrap" }}>
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
  );
  const latestApprovalChecksPanel = (
    <div style={{ ...cardStyle, padding: 10 }}>
      <div style={{ fontWeight: 700, color: "#101828", marginBottom: 8 }}>Latest Approval Checks (Synced)</div>
      {renderUtmDecisionReadable(syncedApproval?.decision)}
      <div style={{ height: 8 }} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8, fontSize: 12 }}>
        {[
          ["Approval", yesNoBadge(syncedApproval?.approved)],
          ["Route bounds", yesNoBadge(asRecord(syncedApprovalChecks?.route_bounds)?.ok ?? asRecord(syncedApprovalChecks?.route_bounds)?.geofence_ok)],
          ["Weather", yesNoBadge(asRecord(syncedApprovalChecks?.weather)?.ok)],
          ["No-fly zone", yesNoBadge(asRecord(syncedApprovalChecks?.no_fly_zone)?.ok)],
          ["Regulation", yesNoBadge(asRecord(syncedApprovalChecks?.regulations)?.ok)],
          ["Time window", yesNoBadge(asRecord(syncedApprovalChecks?.time_window)?.ok)],
          ["Operator license", yesNoBadge(asRecord(syncedApprovalChecks?.operator_license)?.ok)],
        ].map(([label, value]) => (
          <div key={String(label)} style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4 }}>
            <div style={{ color: "#667085", fontSize: 11 }}>{label}</div>
            <div style={{ minHeight: 18 }}>{value as React.ReactNode}</div>
          </div>
        ))}
        <div style={{ border: "1px solid #eaecf0", borderRadius: 8, background: "#fff", padding: "6px 8px", display: "grid", gap: 4, gridColumn: "span 2" }}>
          <div style={{ color: "#667085", fontSize: 11 }}>Reason</div>
          <div style={{ color: "#101828", fontSize: 12, minHeight: 18, overflowWrap: "anywhere" }}>
            <code>{String(syncedApproval?.reason ?? "-")}</code>
          </div>
        </div>
      </div>
    </div>
  );
  const regulationsAddNfzPanel = (
    <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontWeight: 700, color: "#101828" }}>Regulations + Add No-Fly Zone</div>
        <div style={{ fontSize: 11, color: "#667085" }}>Create NFZ by form or use map in the next card</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(150px, 1.1fr) repeat(5, minmax(68px, 92px)) auto", gap: 8, alignItems: "end" }}>
        <label style={{ fontSize: 12 }}>NFZ Reason<input style={{ ...inputStyle, maxWidth: 180 }} value={nfzDraftReason} onChange={(e) => setNfzDraftReason(e.target.value)} /></label>
        <label style={{ fontSize: 12 }}>Center X<input style={{ ...inputStyle, maxWidth: 82 }} value={nfzDraftX} onChange={(e) => setNfzDraftX(e.target.value)} /></label>
        <label style={{ fontSize: 12 }}>Center Y<input style={{ ...inputStyle, maxWidth: 82 }} value={nfzDraftY} onChange={(e) => setNfzDraftY(e.target.value)} /></label>
        <label style={{ fontSize: 12 }}>Radius<input style={{ ...inputStyle, maxWidth: 78 }} value={nfzDraftRadiusM} onChange={(e) => setNfzDraftRadiusM(e.target.value)} /></label>
        <label style={{ fontSize: 12 }}>Z Min<input style={{ ...inputStyle, maxWidth: 74 }} value={nfzDraftZMin} onChange={(e) => setNfzDraftZMin(e.target.value)} /></label>
        <label style={{ fontSize: 12 }}>Z Max<input style={{ ...inputStyle, maxWidth: 74 }} value={nfzDraftZMax} onChange={(e) => setNfzDraftZMax(e.target.value)} /></label>
        <div style={{ display: "flex", alignItems: "end" }}>
          <button type="button" style={chipStyle(false)} onClick={() => void addNoFlyZoneByForm()} disabled={busy}>Add NFZ</button>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 4, fontSize: 12 }}>
        <div style={{ color: "#667085" }}>Base regulation defaults</div><div>Used as fallback; UAV-specific limits are derived from the selected license size class.</div>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Parameter</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Small UAV</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Middle UAV</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Large UAV</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Effective ({licenseId})</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["max_altitude_m", "Max altitude", "m"],
              ["max_route_span_m", "Max route span", "m"],
              ["max_wind_mps", "Max wind", "m/s"],
              ["min_visibility_km", "Min visibility", "km"],
              ["allow_precip_mmph_max", "Max precip", "mm/h"],
              ["max_mission_duration_min", "Max mission duration", "min"],
              ["max_speed_mps", "Max speed", "m/s"],
            ].map(([key, label, unit]) => {
              const small = asRecord(regulationProfiles?.small);
              const middle = asRecord(regulationProfiles?.middle);
              const large = asRecord(regulationProfiles?.large);
              const fmt = (r: Record<string, unknown> | null) => (r && r[key] != null ? `${String(r[key])} ${unit}` : "-");
              return (
                <tr key={key}>
                  <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px", color: "#667085" }}>{label}</td>
                  <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{fmt(small)}</td>
                  <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{fmt(middle)}</td>
                  <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{fmt(large)}</td>
                  <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px", fontWeight: 700 }}>{fmt(effectiveRegs)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 4, fontSize: 12 }}>
        <div style={{ color: "#667085" }}>Effective UAV size (license)</div><div>{String(effectiveRegs?.uav_size_class ?? "-")}</div>
        <div style={{ color: "#667085" }}>License-derived reasoning</div><div>UTM applies weather/route/time limits from the selected operator license's UAV size class to reflect aircraft capability.</div>
      </div>
    </div>
  );

  const noFlyZonesMapPanel = (
    <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontWeight: 700, color: "#101828" }}>No-Fly Zones</div>
        <div style={{ fontSize: 11, color: "#667085" }}>Shift+click on map to add NFZ center</div>
      </div>
      <MissionSyncMap
        title="UTM Synchronized Map"
        route={routePointsForMap}
        plannedPosition={plannedPosForMap}
        trackedPositions={networkMap.tracks}
        selectedUavId={simUavId}
        noFlyZones={nfzZonesForMap}
        baseStations={networkMap.bs}
        coverage={networkMap.coverage}
        clickable
        onAddNoFlyZoneCenter={(p) => void addNoFlyZoneByMap(p)}
      />
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Zone</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Center (X, Y)</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Radius</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Altitude</th>
              <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Reason</th>
            </tr>
          </thead>
          <tbody>
            {nfz.map((z, i) => (
              <tr key={`${String(z.zone_id ?? "nfz")}-${i}`}>
                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(z.zone_id ?? "")}</code></td>
                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>x {String(z.cx ?? "")}, y {String(z.cy ?? "")}</td>
                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(z.radius_m ?? "")} m</td>
                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(z.z_min ?? "")} - {String(z.z_max ?? "")} m</td>
                <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(z.reason ?? "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {routeGeofence && Array.isArray(routeGeofence.out_of_bounds) && (routeGeofence.out_of_bounds as unknown[]).length > 0 ? (
        <div style={{ border: "1px solid #fecdca", borderRadius: 8, background: "#fef3f2", padding: 8, fontSize: 12, color: "#7a271a" }}>
          Route bounds out-of-range waypoints detected in latest route check. Replan on the UAV page before approval.
        </div>
      ) : null}
      {routeNfz && routeNfz.ok === true ? (
        <div style={{ border: "1px solid #abefc6", borderRadius: 8, background: "#ecfdf3", padding: 8, fontSize: 12, color: "#027a48" }}>
          Latest route check indicates the route is currently avoiding all no-fly zones.
        </div>
      ) : null}
    </div>
  );

  return (
    <div style={{ display: "grid", gap: 12, padding: 14, maxWidth: 1280, margin: "0 auto" }}>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
        <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
          <div style={{ fontWeight: 700, color: "#101828" }}>UTM Agent Console</div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 8 }}>
            <label style={{ fontSize: 12 }}>UTM API URL<input style={{ ...inputStyle, maxWidth: 260 }} value={apiBase} onChange={(e) => setApiBase(e.target.value)} /></label>
            <label style={{ fontSize: 12 }}>UAV API URL<input style={{ ...inputStyle, maxWidth: 260 }} value={uavApiBase} onChange={(e) => setUavApiBase(e.target.value)} /></label>
            <label style={{ fontSize: 12 }}>Inspect UAV ID<input style={inputStyle} value={simUavId} onChange={(e) => setSimUavId(e.target.value)} /></label>
            <label style={{ fontSize: 12 }}>Airspace<input style={inputStyle} value={airspace} onChange={(e) => setAirspace(e.target.value)} /></label>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "auto auto auto 1fr", gap: 8, alignItems: "center" }}>
            <button type="button" style={chipStyle(false)} onClick={() => void loadAll()} disabled={busy}>Refresh UTM State</button>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, padding: "0 4px" }}>
              <input type="checkbox" checked={liveRefresh} onChange={(e) => setLiveRefresh(e.target.checked)} />
              Live refresh
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              every
              <input style={{ ...inputStyle, width: 60 }} value={liveRefreshSec} onChange={(e) => setLiveRefreshSec(e.target.value)} inputMode="numeric" />
              s
            </label>
            <div style={{ fontSize: 12, textAlign: "right", color: msg.toLowerCase().includes("failed") ? "#b42318" : "#475467" }}>{msg || ""}</div>
          </div>
          <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", padding: 8, display: "grid", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828", fontSize: 12 }}>Live UTM Data Source + Ingest</div>
              <div style={{ fontSize: 11, color: "#667085" }}>
                source:
                <span style={{ marginLeft: 6, fontWeight: 700, color: String(utmSourceInfo?.active ?? "").includes("sim") ? "#667085" : "#027a48" }}>
                  {String(utmSourceInfo?.active ?? "unknown")}
                </span>
                <span style={{ marginLeft: 6 }}>mode={String(utmSourceInfo?.mode ?? "-")}</span>
              </div>
            </div>
            {isObject(utmSourceInfo?.meta) ? (
              <div style={{ fontSize: 11, color: "#667085" }}>
                {String(asRecord(utmSourceInfo?.meta)?.source ?? "-")} • observed {String(asRecord(utmSourceInfo?.meta)?.observed_at ?? "-")}
              </div>
            ) : null}
            <textarea
              style={{ ...inputStyle, minHeight: 96, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 11 }}
              value={utmLiveJson}
              onChange={(e) => setUtmLiveJson(e.target.value)}
              spellCheck={false}
            />
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ fontSize: 11, color: "#667085" }}>Ingested UTM weather/NFZ/regulations are plotted on the UTM map below.</div>
              <button type="button" style={chipStyle(false)} onClick={() => void ingestUtmLive()} disabled={busy}>Ingest Live UTM</button>
            </div>
          </div>
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
        </div>
        {regulationsAddNfzPanel}
        {noFlyZonesMapPanel}
        </div>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          {checksActionsPanel}
          {weatherControlsPanel}
          {latestApprovalChecksPanel}
          {interactionLogPanel}

          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>Operator License Registry</div>
            <div style={{ display: "grid", gridTemplateColumns: "1.15fr 0.9fr 0.9fr 1.1fr auto", gap: 8, alignItems: "end" }}>
              <label style={{ fontSize: 12 }}>License ID<input style={inputStyle} value={licenseId} onChange={(e) => setLicenseId(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>License Class
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
                <input type="checkbox" checked={licenseActive} onChange={(e) => setLicenseActive(e.target.checked)} />
                Active
              </label>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" style={chipStyle(false)} onClick={() => void registerLicense()} disabled={busy}>Register / Update</button>
            </div>
            <div style={{ overflowX: "auto", maxHeight: 180 }}>
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
                  {licenseRows.map(([id, rec]) => {
                    const r = asRecord(rec);
                    return (
                      <tr key={id}>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{id}</code></td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(r?.license_class ?? "")}</td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(r?.uav_size_class ?? "middle")}</td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(r?.expires_at ?? "")}</code></td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{yesNoBadge(r?.active)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
