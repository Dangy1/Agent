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
  const [nfzDraftRadiusM, setNfzDraftRadiusM] = useState("30");
  const [nfzDraftReason, setNfzDraftReason] = useState("operator_defined");
  const [nfzDraftZMin, setNfzDraftZMin] = useState("0");
  const [nfzDraftZMax, setNfzDraftZMax] = useState("120");
  const [logViewMode, setLogViewMode] = useState<"readable" | "raw">("readable");
  const [selectedLogKey, setSelectedLogKey] = useState<keyof UtmCheckResults>("route");
  const [backendRevisions, setBackendRevisions] = useState<{ uav: number; utm: number; network: number }>({ uav: -1, utm: -1, network: -1 });

  const loadAll = async () => {
    setBusy(true);
    setMsg("");
    try {
      const [simRes, wRes] = await Promise.all([
        fetch(`${normalizeBaseUrl(uavApiBase)}/api/uav/sim/state?uav_id=${encodeURIComponent(simUavId)}`),
        fetch(`${normalizeBaseUrl(apiBase)}/api/utm/weather?airspace_segment=${encodeURIComponent(airspace)}`),
      ]);
      const simData = await simRes.json();
      const wData = await wRes.json();
      if (!simRes.ok || !isObject(simData)) throw new Error(String(asRecord(simData)?.detail ?? "Simulator state request failed"));
      if (!wRes.ok || !isObject(wData)) throw new Error(String(asRecord(wData)?.detail ?? "Weather request failed"));
      setState(simData as UavSimState);
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
  }, [busy, apiBase, uavApiBase, backendRevisions, simUavId, airspace]);

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
      { operator_license_id: licenseId, license_class: licenseClass, expires_at: expiresIso ?? "2099-01-01T00:00:00Z", active: licenseActive },
      "License registered",
    );
  };

  const runRouteChecks = async () => {
    const speed = Number.parseFloat(requestedSpeedMps);
    const data = await postApiResult("/api/utm/checks/route", {
      uav_id: simUavId,
      airspace_segment: airspace,
      requested_speed_mps: Number.isFinite(speed) ? speed : 12,
    });
    if (!data) return;
    setUtmChecks((prev) => ({ ...prev, route: asRecord(data.result) ?? {} }));
    setMsg("Route/geofence/NFZ/regulation checks completed");
    await loadAll();
  };

  const runTimeWindowCheck = async () => {
    const data = await postApiResult("/api/utm/checks/time-window", {
      planned_start_at: localInputToIsoUtc(plannedStartAt),
      planned_end_at: localInputToIsoUtc(plannedEndAt),
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
    const data = await postApiResult("/api/utm/verify-from-uav", {
      uav_id: simUavId,
      airspace_segment: airspace,
      operator_license_id: licenseId,
      required_license_class: requiredLicenseClass,
      requested_speed_mps: Number.isFinite(speed) ? speed : 12,
      planned_start_at: localInputToIsoUtc(plannedStartAt),
      planned_end_at: localInputToIsoUtc(plannedEndAt),
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

  const addNoFlyZoneByMap = async (point: { x: number; y: number; z?: number }) => {
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
      setMsg(`NFZ added at (${point.x.toFixed(1)}, ${point.y.toFixed(1)})`);
      bumpSharedRevision();
      await loadAll();
    } catch (e) {
      setMsg(`Action failed: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(false);
    }
  };

  const utm = asRecord(state?.utm);
  const nfz = Array.isArray(utm?.no_fly_zones) ? (utm?.no_fly_zones as unknown[]).filter(isObject) : [];
  const regs = asRecord(utm?.regulations);
  const licenses = asRecord(utm?.licenses) ?? {};
  const licenseRows = useMemo(() => Object.entries(licenses), [licenses]);
  const uav = asRecord(state?.uav);
  const approval = asRecord(uav?.utm_approval);
  const checks = asRecord(approval?.checks);
  const weatherResultChecks = asRecord(weatherCheck?.checks);
  const routeCheck = asRecord(utmChecks.route);
  const routeGeofence = asRecord(routeCheck?.geofence);
  const routeNfz = asRecord(routeCheck?.no_fly_zone);
  const routeRegs = asRecord(routeCheck?.regulations);
  const timeWindowCheck = asRecord(utmChecks.timeWindow);
  const licenseCheck = asRecord(utmChecks.license);
  const verifyCheck = asRecord(utmChecks.verify);
  const corridorCheck = asRecord(utmChecks.corridor);
  const checkLogItems = useMemo(
    () =>
      [
        { key: "route", label: "Route / Geofence / NFZ / Regulations", data: routeCheck, accent: "#155eef" },
        { key: "timeWindow", label: "Time Window Check", data: timeWindowCheck, accent: "#7a5af8" },
        { key: "license", label: "License Check", data: licenseCheck, accent: "#027a48" },
        { key: "verify", label: "Verify From UAV", data: verifyCheck, accent: "#b54708" },
        { key: "corridor", label: "Corridor Reservation", data: corridorCheck, accent: "#344054" },
      ] as Array<{ key: keyof UtmCheckResults; label: string; data: Record<string, unknown> | null; accent: string }>,
    [routeCheck, timeWindowCheck, licenseCheck, verifyCheck, corridorCheck],
  );
  const availableCheckLogItems = checkLogItems.filter((i) => i.data);
  const selectedCheckLog = availableCheckLogItems.find((i) => i.key === selectedLogKey) ?? availableCheckLogItems[0] ?? null;
  const selectedCheckLogData = selectedCheckLog?.data ?? null;
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

  return (
    <div style={{ display: "grid", gap: 12, padding: 14, maxWidth: 1280, margin: "0 auto" }}>
      <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
        <div style={{ fontWeight: 700, color: "#101828" }}>UTM Agent Console</div>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(220px,2fr) minmax(180px,1fr) minmax(150px,1fr) minmax(150px,1fr)", gap: 8 }}>
          <label style={{ fontSize: 12 }}>UTM API URL<input style={inputStyle} value={apiBase} onChange={(e) => setApiBase(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>UAV API URL<input style={inputStyle} value={uavApiBase} onChange={(e) => setUavApiBase(e.target.value)} /></label>
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

      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)" }}>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>UTM Checks & Actions</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button type="button" style={chipStyle(false)} onClick={() => void runRouteChecks()} disabled={busy}>Route</button>
              <button type="button" style={chipStyle(false)} onClick={() => void runTimeWindowCheck()} disabled={busy}>Time</button>
              <button type="button" style={chipStyle(false)} onClick={() => void runLicenseCheck()} disabled={busy}>License</button>
              <button type="button" style={chipStyle(false)} onClick={() => void runVerifyFromUav()} disabled={busy}>Verify</button>
              <button type="button" style={chipStyle(false)} onClick={() => void reserveCorridor()} disabled={busy}>Corridor</button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 4, fontSize: 12 }}>
              <div style={{ color: "#667085" }}>Route geofence</div><div>{yesNoBadge(routeGeofence?.geofence_ok)}</div>
              <div style={{ color: "#667085" }}>Route NFZ</div><div>{yesNoBadge(routeNfz?.ok)}</div>
              <div style={{ color: "#667085" }}>Route regulations</div><div>{yesNoBadge(routeRegs?.ok)}</div>
              <div style={{ color: "#667085" }}>Time window</div><div>{yesNoBadge(timeWindowCheck?.ok)}</div>
              <div style={{ color: "#667085" }}>License</div><div>{yesNoBadge(licenseCheck?.ok)}</div>
              <div style={{ color: "#667085" }}>Verify flight plan</div><div>{yesNoBadge(verifyCheck?.approved)}</div>
              <div style={{ color: "#667085" }}>Corridor reserved</div><div>{yesNoBadge(corridorCheck?.reserved)}</div>
            </div>
            <div style={{ fontSize: 11, color: "#667085" }}>
              Results open in the fixed bottom log panel. Use the selector there to switch between Route / Time / License / Verify / Corridor.
            </div>
          </div>

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

          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 700, color: "#101828" }}>Operator License Registry</div>
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr 1.2fr auto", gap: 8, alignItems: "end" }}>
              <label style={{ fontSize: 12 }}>License ID<input style={inputStyle} value={licenseId} onChange={(e) => setLicenseId(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>License Class
                <select style={inputStyle} value={licenseClass} onChange={(e) => setLicenseClass(e.target.value)}>
                  <option value="VLOS">VLOS</option>
                  <option value="BVLOS">BVLOS</option>
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
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(r?.expires_at ?? "")}</code></td>
                        <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{yesNoBadge(r?.active)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr auto auto", gap: 8, alignItems: "center" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>UTM Checks Log</div>
              <div style={{ display: "flex", gap: 6 }}>
                <button type="button" style={chipStyle(logViewMode === "readable")} onClick={() => setLogViewMode("readable")}>Readable</button>
                <button type="button" style={chipStyle(logViewMode === "raw")} onClick={() => setLogViewMode("raw")}>Raw JSON</button>
              </div>
              <select
                style={{ ...inputStyle, minWidth: 220 }}
                value={selectedCheckLog?.key ?? ""}
                onChange={(e) => setSelectedLogKey(e.target.value as keyof UtmCheckResults)}
              >
                {availableCheckLogItems.length === 0 ? <option value="">No results yet</option> : null}
                {availableCheckLogItems.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ border: "1px solid #eaecf0", borderRadius: 10, background: "#fff", minHeight: 130, maxHeight: 220, overflow: "auto" }}>
              {!selectedCheckLog || !selectedCheckLogData ? (
                <div style={{ padding: 10, fontSize: 12, color: "#667085" }}>
                  Run `Route`, `Time`, `License`, `Verify`, or `Corridor` to populate the log panel.
                </div>
              ) : logViewMode === "raw" ? (
                <pre style={{ margin: 0, padding: 10, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12 }}>
                  {JSON.stringify(selectedCheckLogData, null, 2)}
                </pre>
              ) : (
                <div style={{ display: "grid", gap: 8, padding: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                    <div style={{ fontWeight: 700, color: selectedCheckLog.accent, fontSize: 12 }}>{selectedCheckLog.label}</div>
                    <div style={{ fontSize: 11, color: "#667085" }}>Human-readable summary</div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 4, fontSize: 12 }}>
                    {Object.entries(selectedCheckLogData).slice(0, 18).map(([k, v]) => (
                      <React.Fragment key={k}>
                        <div style={{ color: "#667085" }}>{k}</div>
                        <div style={{ color: "#101828" }}>
                          {typeof v === "object" && v !== null ? (
                            <code style={{ fontSize: 11 }}>{Array.isArray(v) ? `[${v.length} items]` : "{...}"}</code>
                          ) : (
                            <code style={{ fontSize: 11 }}>{String(v)}</code>
                          )}
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div style={{ ...cardStyle, padding: 10, display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ fontWeight: 700, color: "#101828" }}>Regulations + No-Fly Zones</div>
              <div style={{ fontSize: 11, color: "#667085" }}>Shift+click on map to add NFZ center</div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 100px 80px 80px", gap: 8, alignItems: "end" }}>
              <label style={{ fontSize: 12 }}>NFZ Reason<input style={inputStyle} value={nfzDraftReason} onChange={(e) => setNfzDraftReason(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>Radius<input style={inputStyle} value={nfzDraftRadiusM} onChange={(e) => setNfzDraftRadiusM(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>Z Min<input style={inputStyle} value={nfzDraftZMin} onChange={(e) => setNfzDraftZMin(e.target.value)} /></label>
              <label style={{ fontSize: 12 }}>Z Max<input style={inputStyle} value={nfzDraftZMax} onChange={(e) => setNfzDraftZMax(e.target.value)} /></label>
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
            <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 4, fontSize: 12 }}>
              <div style={{ color: "#667085" }}>Max altitude</div><div>{String(regs?.max_altitude_m ?? "-")} m</div>
              <div style={{ color: "#667085" }}>Max route span</div><div>{String(regs?.max_route_span_m ?? "-")} m</div>
              <div style={{ color: "#667085" }}>Max wind</div><div>{String(regs?.max_wind_mps ?? "-")} m/s</div>
              <div style={{ color: "#667085" }}>Min visibility</div><div>{String(regs?.min_visibility_km ?? "-")} km</div>
              <div style={{ color: "#667085" }}>Max precip</div><div>{String(regs?.allow_precip_mmph_max ?? "-")} mm/h</div>
              <div style={{ color: "#667085" }}>Max mission duration</div><div>{String(regs?.max_mission_duration_min ?? "-")} min</div>
            </div>
            <div style={{ overflowX: "auto", maxHeight: 220 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Zone</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Center</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Radius</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Altitude</th>
                    <th style={{ textAlign: "left", borderBottom: "1px solid #eaecf0", padding: "6px 4px" }}>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {nfz.map((z, i) => (
                    <tr key={`${String(z.zone_id ?? "nfz")}-${i}`}>
                      <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}><code>{String(z.zone_id ?? "")}</code></td>
                      <td style={{ borderBottom: "1px solid #f2f4f7", padding: "6px 4px" }}>{String(z.cx ?? "")}, {String(z.cy ?? "")}</td>
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
                Geofence out-of-bounds waypoints detected in latest route check. Replan on the UAV page before approval.
              </div>
            ) : null}
            {routeNfz && routeNfz.ok === true ? (
              <div style={{ border: "1px solid #abefc6", borderRadius: 8, background: "#ecfdf3", padding: 8, fontSize: 12, color: "#027a48" }}>
                Latest route check indicates the route is currently avoiding all no-fly zones.
              </div>
            ) : null}
          </div>

          <div style={{ ...cardStyle, padding: 10 }}>
            <div style={{ fontWeight: 700, color: "#101828", marginBottom: 8 }}>Latest Approval Checks (from UAV state)</div>
            <div style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 4, fontSize: 12 }}>
              <div style={{ color: "#667085" }}>Approval</div><div>{yesNoBadge(approval?.approved)}</div>
              <div style={{ color: "#667085" }}>Weather</div><div>{yesNoBadge(asRecord(checks?.weather)?.ok)}</div>
              <div style={{ color: "#667085" }}>No-fly zone</div><div>{yesNoBadge(asRecord(checks?.no_fly_zone)?.ok)}</div>
              <div style={{ color: "#667085" }}>Regulation</div><div>{yesNoBadge(asRecord(checks?.regulations)?.ok)}</div>
              <div style={{ color: "#667085" }}>Time window</div><div>{yesNoBadge(asRecord(checks?.time_window)?.ok)}</div>
              <div style={{ color: "#667085" }}>Operator license</div><div>{yesNoBadge(asRecord(checks?.operator_license)?.ok)}</div>
              <div style={{ color: "#667085" }}>Reason</div><div><code>{String(approval?.reason ?? "-")}</code></div>
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
