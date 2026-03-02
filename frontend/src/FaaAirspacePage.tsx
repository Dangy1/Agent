import React, { useEffect, useMemo, useState } from "react";
import { MissionSyncMap, type MissionPoint, type MissionPolygonOverlay } from "./MissionSyncMap";
import { getSharedPageState, patchSharedPageState, subscribeSharedPageState } from "./pageSync";

type WaypointRow = { lon: string; lat: string; altM: string };
type BboxMode = "none" | "route";

type LonLatPoint = { lon: number; lat: number };
type GeoExtent = { minLon: number; maxLon: number; minLat: number; maxLat: number };

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null;
}

function asRecord(x: unknown): Record<string, unknown> | null {
  return isObject(x) ? x : null;
}

function asArrayRecords(x: unknown): Record<string, unknown>[] {
  return Array.isArray(x) ? x.filter(isObject).map((row) => ({ ...row })) : [];
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
  if (!status || status === "success" || status === "warning" || status === "ok") return;
  const nested = asRecord(root.result);
  const detail = root.detail ?? root.error ?? nested?.detail ?? nested?.error ?? root;
  throw new Error(formatApiErrorDetail(detail));
}

function parseWaypointRows(rows: WaypointRow[]): { waypoints: Array<Record<string, number>>; errors: string[] } {
  const errors: string[] = [];
  const waypoints: Array<Record<string, number>> = [];
  rows.forEach((row, index) => {
    const lon = Number(row.lon);
    const lat = Number(row.lat);
    const altM = Number(row.altM);
    if (!Number.isFinite(lon)) errors.push(`Row ${index + 1}: lon is invalid`);
    if (!Number.isFinite(lat)) errors.push(`Row ${index + 1}: lat is invalid`);
    if (!Number.isFinite(altM)) errors.push(`Row ${index + 1}: altitude is invalid`);
    if (Number.isFinite(lon) && (lon < -180 || lon > 180)) errors.push(`Row ${index + 1}: lon must be [-180, 180]`);
    if (Number.isFinite(lat) && (lat < -90 || lat > 90)) errors.push(`Row ${index + 1}: lat must be [-90, 90]`);
    if (Number.isFinite(altM) && altM < 0) errors.push(`Row ${index + 1}: altitude must be >= 0`);
    if (Number.isFinite(lon) && Number.isFinite(lat) && Number.isFinite(altM)) {
      waypoints.push({ lon, lat, altM, x: lon, y: lat, z: altM });
    }
  });
  if (waypoints.length < 2) errors.push("At least 2 valid waypoints are required.");
  return { waypoints, errors };
}

function yesNoBadge(value: unknown): React.ReactNode {
  const pass = value === true;
  const fail = value === false;
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
      {pass ? "PASS" : fail ? "FAIL" : String(value ?? "-")}
    </span>
  );
}

function parseGeoPolygons(geometryInput: unknown): LonLatPoint[][][] {
  const geometry = asRecord(geometryInput);
  if (!geometry) return [];
  const geomType = String(geometry.type ?? "");
  if (geomType === "Polygon") {
    const rings = Array.isArray(geometry.coordinates) ? geometry.coordinates : [];
    const parsedRings: LonLatPoint[][] = [];
    rings.forEach((ring) => {
      if (!Array.isArray(ring)) return;
      const points: LonLatPoint[] = [];
      ring.forEach((coord) => {
        if (!Array.isArray(coord) || coord.length < 2) return;
        const lon = Number(coord[0]);
        const lat = Number(coord[1]);
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
        points.push({ lon, lat });
      });
      if (points.length >= 3) parsedRings.push(points);
    });
    return parsedRings.length ? [parsedRings] : [];
  }
  if (geomType === "MultiPolygon") {
    const polys = Array.isArray(geometry.coordinates) ? geometry.coordinates : [];
    const parsedPolys: LonLatPoint[][][] = [];
    polys.forEach((poly) => {
      if (!Array.isArray(poly)) return;
      const parsedRings: LonLatPoint[][] = [];
      poly.forEach((ring) => {
        if (!Array.isArray(ring)) return;
        const points: LonLatPoint[] = [];
        ring.forEach((coord) => {
          if (!Array.isArray(coord) || coord.length < 2) return;
          const lon = Number(coord[0]);
          const lat = Number(coord[1]);
          if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
          points.push({ lon, lat });
        });
        if (points.length >= 3) parsedRings.push(points);
      });
      if (parsedRings.length) parsedPolys.push(parsedRings);
    });
    return parsedPolys;
  }
  return [];
}

function computeGeoExtent(route: LonLatPoint[], features: Record<string, unknown>[]): GeoExtent | null {
  const allPts: LonLatPoint[] = [];
  route.forEach((pt) => allPts.push(pt));
  features.forEach((feature) => {
    parseGeoPolygons(feature.geometry).forEach((poly) => {
      poly.forEach((ring) => {
        ring.forEach((pt) => allPts.push(pt));
      });
    });
  });
  if (!allPts.length) return null;
  let minLon = allPts[0]!.lon;
  let maxLon = allPts[0]!.lon;
  let minLat = allPts[0]!.lat;
  let maxLat = allPts[0]!.lat;
  allPts.forEach((pt) => {
    minLon = Math.min(minLon, pt.lon);
    maxLon = Math.max(maxLon, pt.lon);
    minLat = Math.min(minLat, pt.lat);
    maxLat = Math.max(maxLat, pt.lat);
  });
  const lonPad = Math.max(0.001, (maxLon - minLon) * 0.08);
  const latPad = Math.max(0.001, (maxLat - minLat) * 0.08);
  const out: GeoExtent = {
    minLon: minLon - lonPad,
    maxLon: maxLon + lonPad,
    minLat: minLat - latPad,
    maxLat: maxLat + latPad,
  };
  if (out.maxLon <= out.minLon) {
    out.maxLon = out.minLon + 0.01;
  }
  if (out.maxLat <= out.minLat) {
    out.maxLat = out.minLat + 0.01;
  }
  return out;
}

function buildRouteBboxCsv(waypoints: Array<Record<string, number>>): string | null {
  if (!waypoints.length) return null;
  const lons = waypoints.map((w) => Number(w.x)).filter(Number.isFinite);
  const lats = waypoints.map((w) => Number(w.y)).filter(Number.isFinite);
  if (!lons.length || !lats.length) return null;
  let lonMin = Math.min(...lons);
  let lonMax = Math.max(...lons);
  let latMin = Math.min(...lats);
  let latMax = Math.max(...lats);
  const lonPad = Math.max(0.005, (lonMax - lonMin) * 0.2);
  const latPad = Math.max(0.005, (latMax - latMin) * 0.2);
  lonMin = Math.max(-180, lonMin - lonPad);
  lonMax = Math.min(180, lonMax + lonPad);
  latMin = Math.max(-90, latMin - latPad);
  latMax = Math.min(90, latMax + latPad);
  return `${lonMin.toFixed(6)},${latMin.toFixed(6)},${lonMax.toFixed(6)},${latMax.toFixed(6)}`;
}

function matchKeyFromRecord(rec: Record<string, unknown> | null): string {
  if (!rec) return "";
  const publishedId = String(rec.published_id ?? "").trim();
  const airspaceType = String(rec.airspace_type ?? "").trim();
  const ordinalRaw = rec.volume_ordinal;
  const volumeOrdinal = Number.isFinite(Number(ordinalRaw)) ? String(Number(ordinalRaw)) : String(ordinalRaw ?? "").trim();
  if (!publishedId || !airspaceType || !volumeOrdinal) return "";
  return `${publishedId}:${airspaceType}:${volumeOrdinal}`;
}

function featureKeyFromFeatureRow(feature: Record<string, unknown>): string {
  const properties = asRecord(feature.properties);
  return matchKeyFromRecord(properties);
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

const monoPanelStyle: React.CSSProperties = {
  border: "1px solid #eaecf0",
  borderRadius: 8,
  background: "#f9fafb",
  padding: 10,
  fontSize: 11,
  overflowX: "auto",
  maxHeight: 260,
};

export function FaaAirspacePage() {
  const sharedInit = getSharedPageState();
  const [utmApiBase, setUtmApiBase] = useState(sharedInit.utmApiBase || "http://127.0.0.1:8021");
  const [utmAuthToken, setUtmAuthToken] = useState(sharedInit.utmAuthToken || "local-dev-token");
  const [airspaceSegment, setAirspaceSegment] = useState(sharedInit.airspace || "faa:*");
  const [uavId, setUavId] = useState(sharedInit.uavId || "uav-1");
  const [routeId, setRouteId] = useState("faa-route-1");
  const [operatorLicenseId, setOperatorLicenseId] = useState("op-001");
  const [requiredLicenseClass, setRequiredLicenseClass] = useState("VLOS");
  const [requestedSpeedMps, setRequestedSpeedMps] = useState("12");
  const [maxFeatures, setMaxFeatures] = useState("300");
  const [bboxMode, setBboxMode] = useState<BboxMode>("route");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [includeSchedules, setIncludeSchedules] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const [utmState, setUtmState] = useState<Record<string, unknown> | null>(null);
  const [utmSourceInfo, setUtmSourceInfo] = useState<Record<string, unknown> | null>(null);
  const [routeResult, setRouteResult] = useState<Record<string, unknown> | null>(null);
  const [verifyResult, setVerifyResult] = useState<Record<string, unknown> | null>(null);
  const [faaResult, setFaaResult] = useState<Record<string, unknown> | null>(null);
  const [faaFeatures, setFaaFeatures] = useState<Record<string, unknown>[]>([]);

  const [featureSearch, setFeatureSearch] = useState("");
  const [airspaceTypeFilter, setAirspaceTypeFilter] = useState("all");
  const [classCodeFilter, setClassCodeFilter] = useState("all");
  const [showMatchedOnly, setShowMatchedOnly] = useState(false);

  const [waypointRows, setWaypointRows] = useState<WaypointRow[]>([
    { lon: "24.8164", lat: "60.1808", altM: "60" },
    { lon: "24.8268", lat: "60.1860", altM: "70" },
    { lon: "24.8385", lat: "60.1915", altM: "65" },
  ]);

  const waypointParse = useMemo(() => parseWaypointRows(waypointRows), [waypointRows]);
  const parsedSpeed = Number(requestedSpeedMps);
  const routeLonLat = useMemo<LonLatPoint[]>(
    () =>
      waypointParse.waypoints
        .map((wp) => ({ lon: Number(wp.x), lat: Number(wp.y) }))
        .filter((pt) => Number.isFinite(pt.lon) && Number.isFinite(pt.lat)),
    [waypointParse.waypoints]
  );

  useEffect(() => {
    patchSharedPageState({
      utmApiBase,
      utmAuthToken,
      airspace: airspaceSegment,
      uavId,
    });
  }, [utmApiBase, utmAuthToken, airspaceSegment, uavId]);

  useEffect(() => {
    const unsubscribe = subscribeSharedPageState((next) => {
      if (next.utmApiBase && next.utmApiBase !== utmApiBase) setUtmApiBase(next.utmApiBase);
      if (typeof next.utmAuthToken === "string" && next.utmAuthToken !== utmAuthToken) setUtmAuthToken(next.utmAuthToken);
      if (next.airspace && next.airspace !== airspaceSegment) setAirspaceSegment(next.airspace);
      if (next.uavId && next.uavId !== uavId) setUavId(next.uavId);
    });
    return unsubscribe;
  }, [utmApiBase, utmAuthToken, airspaceSegment, uavId]);

  async function requestUtm(path: string, method: "GET" | "POST", body?: Record<string, unknown>) {
    const resp = await fetch(`${normalizeBaseUrl(utmApiBase)}${path}`, {
      method,
      headers: utmAuthHeaders(utmAuthToken, Boolean(body)),
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(formatApiErrorDetail(data));
    assertApiPayloadOk(data);
    return data as Record<string, unknown>;
  }

  const refreshState = async () => {
    setBusy(true);
    setMsg("Refreshing FAA/UTM state...");
    try {
      const query = new URLSearchParams({
        airspace_segment: airspaceSegment.trim() || "faa:*",
      });
      if (operatorLicenseId.trim()) query.set("operator_license_id", operatorLicenseId.trim());
      const [stateData, sourceData] = await Promise.all([
        requestUtm(`/api/utm/state?${query.toString()}`, "GET"),
        requestUtm("/api/utm/live/source", "GET"),
      ]);
      setUtmState(asRecord(stateData.result));
      setUtmSourceInfo(asRecord(sourceData.result));
      setMsg("FAA/UTM state refreshed.");
    } catch (error) {
      setMsg(`Refresh failed: ${String((error as Error).message || error)}`);
    } finally {
      setBusy(false);
    }
  };

  const loadFaaAirspace = async () => {
    const maxN = Number(maxFeatures);
    if (!Number.isFinite(maxN) || maxN <= 0) {
      setMsg("Load FAA airspace failed: max features must be a positive number.");
      return;
    }
    setBusy(true);
    setMsg("Loading FAA airspace GeoJSON from PostGIS...");
    try {
      const query = new URLSearchParams({
        airspace_segment: airspaceSegment.trim() || "faa:*",
        max_features: String(Math.round(maxN)),
      });
      if (includeInactive) query.set("include_inactive", "true");
      if (includeSchedules) query.set("include_schedules", "true");
      if (bboxMode === "route") {
        const bboxCsv = buildRouteBboxCsv(waypointParse.waypoints);
        if (bboxCsv) query.set("bbox", bboxCsv);
      }
      const data = await requestUtm(`/api/utm/faa/airspace?${query.toString()}`, "GET");
      const result = asRecord(data.result);
      const collection = asRecord(result?.collection);
      const features = asArrayRecords(collection?.features);
      setFaaResult(result);
      setFaaFeatures(features);
      setMsg(`FAA airspace loaded: ${features.length} GeoJSON features.`);
    } catch (error) {
      setMsg(`Load FAA airspace failed: ${String((error as Error).message || error)}`);
    } finally {
      setBusy(false);
    }
  };

  const runRouteCheck = async () => {
    if (waypointParse.errors.length > 0) {
      setMsg(`Route check failed: ${waypointParse.errors[0]}`);
      return;
    }
    if (!Number.isFinite(parsedSpeed) || parsedSpeed <= 0) {
      setMsg("Requested speed must be a positive number.");
      return;
    }
    setBusy(true);
    setMsg("Running FAA route geofence check...");
    try {
      const payload: Record<string, unknown> = {
        uav_id: uavId.trim() || "uav-1",
        route_id: routeId.trim() || "faa-route-1",
        airspace_segment: airspaceSegment.trim() || "faa:*",
        requested_speed_mps: parsedSpeed,
        waypoints: waypointParse.waypoints,
      };
      if (operatorLicenseId.trim()) payload.operator_license_id = operatorLicenseId.trim();
      const data = await requestUtm("/api/utm/checks/route", "POST", payload);
      setRouteResult(asRecord(data.result));
      setMsg("FAA route check completed.");
    } catch (error) {
      setMsg(`Route check failed: ${String((error as Error).message || error)}`);
    } finally {
      setBusy(false);
    }
  };

  const runVerify = async () => {
    if (waypointParse.errors.length > 0) {
      setMsg(`UTM verify failed: ${waypointParse.errors[0]}`);
      return;
    }
    if (!operatorLicenseId.trim()) {
      setMsg("Operator license ID is required for verify-from-uav.");
      return;
    }
    if (!Number.isFinite(parsedSpeed) || parsedSpeed <= 0) {
      setMsg("Requested speed must be a positive number.");
      return;
    }
    setBusy(true);
    setMsg("Running UTM verify (FAA geofence included)...");
    try {
      const payload = {
        uav_id: uavId.trim() || "uav-1",
        route_id: routeId.trim() || "faa-route-1",
        airspace_segment: airspaceSegment.trim() || "faa:*",
        operator_license_id: operatorLicenseId.trim(),
        required_license_class: requiredLicenseClass.trim() || "VLOS",
        requested_speed_mps: parsedSpeed,
        waypoints: waypointParse.waypoints,
      };
      const data = await requestUtm("/api/utm/verify-from-uav", "POST", payload);
      setVerifyResult(asRecord(data.result));
      setMsg("UTM verify completed.");
    } catch (error) {
      setMsg(`UTM verify failed: ${String((error as Error).message || error)}`);
    } finally {
      setBusy(false);
    }
  };

  const routeGeofence = asRecord(routeResult?.geofence);
  const routeSource = asRecord(routeGeofence?.source);
  const routeBounds = asRecord(routeGeofence?.bounds);
  const routeMatched = asArrayRecords(routeGeofence?.matched_airspace);
  const routeOutOfBounds = asArrayRecords(routeGeofence?.out_of_bounds);

  const verifyDecision = asRecord(verifyResult?.decision);
  const verifyChecks = asRecord(verifyResult?.checks);
  const verifyRouteBounds = asRecord(verifyChecks?.route_bounds);
  const verifyRouteSource = asRecord(verifyRouteBounds?.source);
  const verifyMatched = asArrayRecords(verifyRouteBounds?.matched_airspace);
  const verifyOutOfBounds = asArrayRecords(verifyRouteBounds?.out_of_bounds);

  const matchedFeatureKeys = useMemo(() => {
    const keys = new Set<string>();
    routeMatched.forEach((rec) => {
      const key = matchKeyFromRecord(rec);
      if (key) keys.add(key);
    });
    verifyMatched.forEach((rec) => {
      const key = matchKeyFromRecord(rec);
      if (key) keys.add(key);
    });
    return keys;
  }, [routeMatched, verifyMatched]);

  const airspaceTypes = useMemo(() => {
    const vals = new Set<string>();
    faaFeatures.forEach((feature) => {
      const props = asRecord(feature.properties);
      const v = String(props?.airspace_type ?? "").trim();
      if (v) vals.add(v);
    });
    return Array.from(vals).sort((a, b) => a.localeCompare(b));
  }, [faaFeatures]);

  const classCodes = useMemo(() => {
    const vals = new Set<string>();
    faaFeatures.forEach((feature) => {
      const props = asRecord(feature.properties);
      const v = String(props?.class_code ?? "").trim();
      if (v) vals.add(v);
    });
    return Array.from(vals).sort((a, b) => a.localeCompare(b));
  }, [faaFeatures]);

  const filteredFaaFeatures = useMemo(() => {
    const q = featureSearch.trim().toLowerCase();
    return faaFeatures.filter((feature) => {
      const props = asRecord(feature.properties);
      const textParts = [
        String(props?.published_id ?? ""),
        String(props?.feature_name ?? ""),
        String(props?.airspace_type ?? ""),
        String(props?.class_code ?? ""),
        String(props?.designator ?? ""),
      ];
      if (q) {
        const found = textParts.some((part) => part.toLowerCase().includes(q));
        if (!found) return false;
      }
      if (airspaceTypeFilter !== "all" && String(props?.airspace_type ?? "") !== airspaceTypeFilter) return false;
      if (classCodeFilter !== "all" && String(props?.class_code ?? "") !== classCodeFilter) return false;
      if (showMatchedOnly) {
        const key = featureKeyFromFeatureRow(feature);
        if (!key || !matchedFeatureKeys.has(key)) return false;
      }
      return true;
    });
  }, [airspaceTypeFilter, classCodeFilter, faaFeatures, featureSearch, matchedFeatureKeys, showMatchedOnly]);

  const mapExtent = useMemo(() => computeGeoExtent(routeLonLat, filteredFaaFeatures), [routeLonLat, filteredFaaFeatures]);

  const mapRoute = useMemo<MissionPoint[]>(() => {
    return waypointParse.waypoints.map((wp) => ({ x: Number(wp.x), y: Number(wp.y), z: Number(wp.z) }));
  }, [waypointParse.waypoints]);

  const mapPolygons = useMemo<MissionPolygonOverlay[]>(() => {
    const out: MissionPolygonOverlay[] = [];
    filteredFaaFeatures.forEach((feature, index) => {
      const props = asRecord(feature.properties);
      const baseKey = featureKeyFromFeatureRow(feature) || `feature-${index}`;
      const matched = matchedFeatureKeys.has(baseKey);
      const label = `${String(props?.published_id ?? "FAA")} ${String(props?.volume_ordinal ?? "").trim() ? `#${String(props?.volume_ordinal ?? "").trim()}` : ""}`.trim();
      const upperLimitM = Number(props?.upper_limit_m);
      const z = Number.isFinite(upperLimitM) ? Math.max(0, upperLimitM) : 0;
      const polys = parseGeoPolygons(feature.geometry);
      polys.forEach((polyRings, polyIdx) => {
        const rings = polyRings.map((ring) => ring.map((pt) => ({ x: pt.lon, y: pt.lat, z })));
        if (!rings.length) return;
        out.push({
          id: `${baseKey}-${polyIdx}`,
          rings,
          color: matched ? "#12b76a" : "#155eef",
          fill: matched ? "rgba(18,183,106,0.26)" : "rgba(21,94,239,0.14)",
          label: polyIdx === 0 ? label : undefined,
        });
      });
    });
    return out;
  }, [filteredFaaFeatures, matchedFeatureKeys]);

  return (
    <main style={{ maxWidth: 1240, margin: "0 auto", padding: "16px", display: "grid", gap: 12 }}>
      <section style={cardStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#101828" }}>FAA Airspace Page</div>
            <div style={{ fontSize: 12, color: "#667085", marginTop: 2 }}>
              FAA/PostGIS GeoJSON query + route verification + map overlay. Route input uses lon/lat (`x=lon`, `y=lat`, `z=meters`).
            </div>
          </div>
          <div style={{ fontSize: 12, color: "#475467" }}>
            UTM data source: <b>{String(utmSourceInfo?.active ?? "-")}</b> ({String(utmSourceInfo?.mode ?? "-")})
          </div>
        </div>
      </section>

      <section style={{ ...cardStyle, display: "grid", gap: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#344054" }}>API + Query Controls</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8 }}>
          <label style={{ fontSize: 12 }}>UTM API Base<input style={inputStyle} value={utmApiBase} onChange={(e) => setUtmApiBase(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>UTM Bearer Token<input style={inputStyle} value={utmAuthToken} onChange={(e) => setUtmAuthToken(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>Airspace Segment<input style={inputStyle} value={airspaceSegment} onChange={(e) => setAirspaceSegment(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>UAV ID<input style={inputStyle} value={uavId} onChange={(e) => setUavId(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>Route ID<input style={inputStyle} value={routeId} onChange={(e) => setRouteId(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>Operator License<input style={inputStyle} value={operatorLicenseId} onChange={(e) => setOperatorLicenseId(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>Required Class<input style={inputStyle} value={requiredLicenseClass} onChange={(e) => setRequiredLicenseClass(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>Requested Speed (m/s)<input style={inputStyle} value={requestedSpeedMps} onChange={(e) => setRequestedSpeedMps(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>Max FAA Features<input style={inputStyle} value={maxFeatures} onChange={(e) => setMaxFeatures(e.target.value)} /></label>
          <label style={{ fontSize: 12 }}>
            BBox Mode
            <select style={inputStyle} value={bboxMode} onChange={(e) => setBboxMode((e.target.value === "none" ? "none" : "route"))}>
              <option value="route">Route bbox</option>
              <option value="none">No bbox</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingTop: 24 }}>
            <input type="checkbox" checked={includeInactive} onChange={(e) => setIncludeInactive(e.target.checked)} />
            Include inactive
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingTop: 24 }}>
            <input type="checkbox" checked={includeSchedules} onChange={(e) => setIncludeSchedules(e.target.checked)} />
            Include schedules
          </label>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" disabled={busy} onClick={() => void refreshState()} style={{ borderRadius: 8, border: "1px solid #d0d5dd", padding: "8px 12px", cursor: "pointer", background: "#fff" }}>Refresh State</button>
          <button type="button" disabled={busy} onClick={() => void loadFaaAirspace()} style={{ borderRadius: 8, border: "1px solid #175cd3", padding: "8px 12px", cursor: "pointer", background: "#eff8ff", color: "#175cd3", fontWeight: 700 }}>Load FAA Airspace</button>
          <button type="button" disabled={busy} onClick={() => void runRouteCheck()} style={{ borderRadius: 8, border: "1px solid #155eef", padding: "8px 12px", cursor: "pointer", background: "#eef4ff", color: "#155eef", fontWeight: 700 }}>FAA Route Check</button>
          <button type="button" disabled={busy} onClick={() => void runVerify()} style={{ borderRadius: 8, border: "1px solid #0f766e", padding: "8px 12px", cursor: "pointer", background: "#e6fffb", color: "#0f766e", fontWeight: 700 }}>UTM Verify</button>
        </div>
        <div style={{ fontSize: 12, color: msg.toLowerCase().includes("failed") ? "#b42318" : "#475467" }}>{msg || "Ready."}</div>
        <div style={{ fontSize: 11, color: "#667085" }}>
          Tip: `airspace_segment=faa:*` loads all current FAA airspace. You can also use `faa:&lt;published_id/designator/name&gt;`.
        </div>
      </section>

      <section style={{ ...cardStyle, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#344054" }}>FAA Waypoints (lon/lat/alt)</div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => setWaypointRows((rows) => rows.concat([{ lon: "", lat: "", altM: "60" }]))}
              disabled={busy}
              style={{ borderRadius: 8, border: "1px solid #d0d5dd", padding: "6px 10px", cursor: "pointer", background: "#fff" }}
            >
              Add Waypoint
            </button>
            <button
              type="button"
              onClick={() =>
                setWaypointRows([
                  { lon: "24.8164", lat: "60.1808", altM: "60" },
                  { lon: "24.8268", lat: "60.1860", altM: "70" },
                  { lon: "24.8385", lat: "60.1915", altM: "65" },
                ])
              }
              disabled={busy}
              style={{ borderRadius: 8, border: "1px solid #d0d5dd", padding: "6px 10px", cursor: "pointer", background: "#fff" }}
            >
              Reset Sample
            </button>
          </div>
        </div>
        <div style={{ border: "1px solid #eaecf0", borderRadius: 8, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 520 }}>
            <thead>
              <tr style={{ background: "#f8fafc", textAlign: "left" }}>
                <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>#</th>
                <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Longitude (x)</th>
                <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Latitude (y)</th>
                <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Altitude m (z)</th>
                <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {waypointRows.map((row, index) => (
                <tr key={`faa-wp-${index}`}>
                  <td style={{ padding: "8px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{index}</td>
                  <td style={{ padding: "8px 10px", borderBottom: "1px solid #f2f4f7" }}>
                    <input
                      style={inputStyle}
                      value={row.lon}
                      onChange={(e) => setWaypointRows((rows) => rows.map((curr, i) => (i === index ? { ...curr, lon: e.target.value } : curr)))}
                    />
                  </td>
                  <td style={{ padding: "8px 10px", borderBottom: "1px solid #f2f4f7" }}>
                    <input
                      style={inputStyle}
                      value={row.lat}
                      onChange={(e) => setWaypointRows((rows) => rows.map((curr, i) => (i === index ? { ...curr, lat: e.target.value } : curr)))}
                    />
                  </td>
                  <td style={{ padding: "8px 10px", borderBottom: "1px solid #f2f4f7" }}>
                    <input
                      style={inputStyle}
                      value={row.altM}
                      onChange={(e) => setWaypointRows((rows) => rows.map((curr, i) => (i === index ? { ...curr, altM: e.target.value } : curr)))}
                    />
                  </td>
                  <td style={{ padding: "8px 10px", borderBottom: "1px solid #f2f4f7" }}>
                    <button
                      type="button"
                      disabled={busy || waypointRows.length <= 2}
                      onClick={() => setWaypointRows((rows) => (rows.length <= 2 ? rows : rows.filter((_, i) => i !== index)))}
                      style={{ borderRadius: 8, border: "1px solid #d0d5dd", padding: "6px 10px", cursor: "pointer", background: "#fff" }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 11, color: waypointParse.errors.length ? "#b42318" : "#027a48" }}>
          {waypointParse.errors.length ? waypointParse.errors[0] : `Waypoints valid (${waypointParse.waypoints.length}).`}
        </div>
      </section>

      <section style={{ ...cardStyle, display: "grid", gap: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#344054" }}>FAA Feature Filters + Map</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8 }}>
          <label style={{ fontSize: 12 }}>Search FAA feature<input style={inputStyle} value={featureSearch} onChange={(e) => setFeatureSearch(e.target.value)} placeholder="published_id / name / designator" /></label>
          <label style={{ fontSize: 12 }}>
            Airspace Type
            <select style={inputStyle} value={airspaceTypeFilter} onChange={(e) => setAirspaceTypeFilter(e.target.value)}>
              <option value="all">All</option>
              {airspaceTypes.map((v) => (
                <option key={`airspace-type-${v}`} value={v}>{v}</option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12 }}>
            Class Code
            <select style={inputStyle} value={classCodeFilter} onChange={(e) => setClassCodeFilter(e.target.value)}>
              <option value="all">All</option>
              {classCodes.map((v) => (
                <option key={`class-code-${v}`} value={v}>{v}</option>
              ))}
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingTop: 24 }}>
            <input type="checkbox" checked={showMatchedOnly} onChange={(e) => setShowMatchedOnly(e.target.checked)} />
            Show matched only
          </label>
        </div>

        <div style={{ fontSize: 12, color: "#475467", display: "flex", gap: 16, flexWrap: "wrap" }}>
          <span>Loaded FAA features: <b>{faaFeatures.length}</b></span>
          <span>Filtered features: <b>{filteredFaaFeatures.length}</b></span>
          <span>Matched feature keys: <b>{matchedFeatureKeys.size}</b></span>
          {mapExtent ? (
            <span>
              Extent: <b>{mapExtent.minLon.toFixed(4)}</b>,<b>{mapExtent.minLat.toFixed(4)}</b> to <b>{mapExtent.maxLon.toFixed(4)}</b>,<b>{mapExtent.maxLat.toFixed(4)}</b>
            </span>
          ) : null}
        </div>

        {mapExtent ? (
          <MissionSyncMap
            title="FAA Airspace Overlay Map"
            route={mapRoute}
            polygonOverlays={mapPolygons}
            routeOverlays={[]}
            trackedPositions={[]}
            mapServiceBase={getSharedPageState().networkApiBase || "http://127.0.0.1:8022"}
            noFlyZones={[]}
            baseStations={[]}
            coverage={[]}
            showCoverage={false}
            showInterferenceHints={false}
            coordinateMode="geo"
          />
        ) : (
          <div style={{ fontSize: 12, color: "#667085" }}>
            Load FAA airspace and provide valid route waypoints to render the map overlay.
          </div>
        )}
      </section>

      <section style={cardStyle}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#344054", marginBottom: 10 }}>FAA Route Check Result</div>
        {routeResult ? (
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 12 }}>Geofence: {yesNoBadge(routeGeofence?.ok ?? routeGeofence?.geofence_ok)}</span>
              <span style={{ fontSize: 12 }}>Engine: <b>{String(routeSource?.engine ?? routeBounds?.engine ?? "-")}</b></span>
              <span style={{ fontSize: 12 }}>Selector: <b>{String(routeSource?.selector ?? routeBounds?.selector ?? "-")}</b></span>
              <span style={{ fontSize: 12 }}>Candidate features: <b>{String(routeBounds?.candidate_feature_count ?? "-")}</b></span>
              <span style={{ fontSize: 12 }}>Out-of-bounds points: <b>{routeOutOfBounds.length}</b></span>
              <span style={{ fontSize: 12 }}>Matched volumes: <b>{routeMatched.length}</b></span>
            </div>
            <pre style={monoPanelStyle}>{JSON.stringify(routeResult, null, 2)}</pre>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "#667085" }}>No route check result yet.</div>
        )}
      </section>

      <section style={cardStyle}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#344054", marginBottom: 10 }}>UTM Verify Result (FAA Geofence Included)</div>
        {verifyResult ? (
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 12 }}>Approved: {yesNoBadge(verifyResult.approved)}</span>
              <span style={{ fontSize: 12 }}>Route bounds: {yesNoBadge(verifyRouteBounds?.ok ?? verifyRouteBounds?.geofence_ok)}</span>
              <span style={{ fontSize: 12 }}>FAA engine: <b>{String(verifyRouteSource?.engine ?? "-")}</b></span>
              <span style={{ fontSize: 12 }}>Matched FAA volumes: <b>{verifyMatched.length}</b></span>
              <span style={{ fontSize: 12 }}>Out-of-bounds points: <b>{verifyOutOfBounds.length}</b></span>
              <span style={{ fontSize: 12 }}>Decision: <b>{String(verifyDecision?.status ?? "-")}</b></span>
            </div>
            <pre style={monoPanelStyle}>{JSON.stringify(verifyResult, null, 2)}</pre>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "#667085" }}>No verify result yet.</div>
        )}
      </section>

      <section style={cardStyle}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#344054", marginBottom: 8 }}>Filtered FAA Feature Rows</div>
        {filteredFaaFeatures.length ? (
          <div style={{ border: "1px solid #eaecf0", borderRadius: 8, overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
              <thead>
                <tr style={{ background: "#f8fafc", textAlign: "left" }}>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Published ID</th>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Name</th>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Type</th>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Class</th>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Designator</th>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Volume</th>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Upper m</th>
                  <th style={{ padding: "8px 10px", borderBottom: "1px solid #eaecf0", fontSize: 12 }}>Matched</th>
                </tr>
              </thead>
              <tbody>
                {filteredFaaFeatures.slice(0, 200).map((feature, index) => {
                  const props = asRecord(feature.properties);
                  const key = featureKeyFromFeatureRow(feature);
                  return (
                    <tr key={`faa-row-${key || index}`}>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{String(props?.published_id ?? "-")}</td>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{String(props?.feature_name ?? "-")}</td>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{String(props?.airspace_type ?? "-")}</td>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{String(props?.class_code ?? "-")}</td>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{String(props?.designator ?? "-")}</td>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{String(props?.volume_ordinal ?? "-")}</td>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{String(props?.upper_limit_m ?? "-")}</td>
                      <td style={{ padding: "7px 10px", borderBottom: "1px solid #f2f4f7", fontSize: 12 }}>{yesNoBadge(key ? matchedFeatureKeys.has(key) : null)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {filteredFaaFeatures.length > 200 ? (
              <div style={{ padding: "8px 10px", fontSize: 11, color: "#667085", borderTop: "1px solid #eaecf0" }}>
                Showing first 200 rows out of {filteredFaaFeatures.length}.
              </div>
            ) : null}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "#667085" }}>No FAA features loaded or all filtered out.</div>
        )}
      </section>

      <section style={cardStyle}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#344054", marginBottom: 8 }}>FAA Query Raw Result</div>
        {faaResult ? <pre style={monoPanelStyle}>{JSON.stringify(faaResult, null, 2)}</pre> : <div style={{ fontSize: 12, color: "#667085" }}>No FAA query result yet.</div>}
      </section>

      <section style={cardStyle}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#344054", marginBottom: 8 }}>Current UTM State Snapshot</div>
        {utmState ? <pre style={monoPanelStyle}>{JSON.stringify(utmState, null, 2)}</pre> : <div style={{ fontSize: 12, color: "#667085" }}>State not loaded yet.</div>}
      </section>
    </main>
  );
}
