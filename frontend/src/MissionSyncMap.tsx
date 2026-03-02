import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  basemapLabel,
  createCesiumImageryProvider,
  fetchMapSyncState,
  fetchMapServiceConfig,
  normalizeBaseUrl,
  plotMapPoint,
  prefetchMapServiceCache,
  readViteEnv,
  toggleMapView,
  type CesiumBasemapChoice,
  type MapSyncState,
  type MapViewEngine,
  type MapServiceConfig,
} from "./map/services";

export type MissionPoint = { x: number; y: number; z?: number };
export type MissionNfz = { zone_id?: string; cx: number; cy: number; radius_m: number; z_min?: number; z_max?: number; reason?: string; shape?: "circle" | "box" };
export type MissionBs = { id: string; x: number; y: number; status?: string };
export type MissionCoverage = { bsId: string; radiusM: number };
export type MissionTrack = {
  id: string;
  x: number;
  y: number;
  z?: number;
  headingDeg?: number;
  speedMps?: number;
  attachedBsId?: string;
  interferenceRisk?: "low" | "medium" | "high";
};
export type MissionRouteOverlay = { id: string; route: MissionPoint[]; color?: string };
export type MissionPolygonOverlay = { id: string; rings: MissionPoint[][]; color?: string; fill?: string; label?: string };

type Props = {
  title?: string;
  route: MissionPoint[];
  plannedPosition?: MissionPoint | null;
  trackedPositions?: MissionTrack[];
  routeOverlays?: MissionRouteOverlay[];
  polygonOverlays?: MissionPolygonOverlay[];
  focusSelectedTrack?: boolean;
  selectedUavId?: string;
  noFlyZones?: MissionNfz[];
  baseStations?: MissionBs[];
  coverage?: MissionCoverage[];
  showCoverage?: boolean;
  showInterferenceHints?: boolean;
  trackMarkerStyle?: "dot" | "uav";
  clickable?: boolean;
  onAddWaypoint?: (point: MissionPoint) => void;
  onAddNoFlyZoneCenter?: (point: MissionPoint) => void;
  externalResetSeq?: number;
  coordinateMode?: "auto" | "geo" | "local";
  enableLiveGps?: boolean;
  mapServiceBase?: string;
  syncEnabled?: boolean;
  syncScope?: string;
  syncIncludeShared?: boolean;
};

type ViewMode = "2d" | "3d";
type MapRenderMode = "svg2d" | "svg3d" | "cesium3d";
type ScreenPoint = { x: number; y: number; depth: number };
type CesiumLoadState = "idle" | "loading" | "ready" | "error";
type CoordinateMode = "geo" | "local";
type WorldBounds = { minX: number; maxX: number; minY: number; maxY: number };
type LiveGpsPoint = { lon: number; lat: number; altM: number; accuracyM: number; tsMs: number };
type CoordinateContext = {
  mode: CoordinateMode;
  world: WorldBounds;
  maxAltitudeM: number;
  originLon: number;
  originLat: number;
};
type GeodeticOrigin = { lon: number; lat: number };

declare global {
  interface Window {
    Cesium?: any;
  }
}

const CESIUM_JS_URL = "https://unpkg.com/cesium@1.126.0/Build/Cesium/Cesium.js";
const CESIUM_CSS_URL = "https://unpkg.com/cesium@1.126.0/Build/Cesium/Widgets/widgets.css";
const CESIUM_ACCESS_TOKEN = ""; // Optional: set a token if you want Cesium Ion assets/terrain.
const DEFAULT_LOCAL_WORLD: WorldBounds = { minX: 0, maxX: 400, minY: 0, maxY: 300 };
const AALTO_CENTER = { lon: 24.8286, lat: 60.1866 };
const DEFAULT_GEO_WORLD: WorldBounds = {
  minX: AALTO_CENTER.lon - 0.03,
  maxX: AALTO_CENTER.lon + 0.03,
  minY: AALTO_CENTER.lat - 0.03,
  maxY: AALTO_CENTER.lat + 0.03,
};
const DEFAULT_LOCAL_ORIGIN = { lon: AALTO_CENTER.lon, lat: AALTO_CENTER.lat };

function statusColor(status?: string): string {
  if (status === "degraded") return "#f79009";
  if (status === "maintenance") return "#98a2b3";
  return "#12b76a";
}

function riskColor(risk?: string): string {
  if (risk === "high") return "#f04438";
  if (risk === "medium") return "#f79009";
  return "#12b76a";
}

type OverlayRouteStyle = {
  color: string;
  svgDash?: string;
  width2d: number;
  width3d: number;
  pointRadius: number;
  opacity: number;
  cesiumDashed: boolean;
  cesiumDashPattern?: number;
};

const OVERLAY_ROUTE_COLORS = ["#22c55e", "#f59e0b", "#a855f7", "#06b6d4", "#ef4444", "#14b8a6", "#eab308", "#3b82f6"];
const OVERLAY_ROUTE_DASH_SVG: Array<string | undefined> = [undefined, "7 4", "3 3", "10 3 2 3"];
const OVERLAY_ROUTE_DASH_CESIUM: number[] = [0xffff, 0xff00, 0xf0f0, 0xcccc];

function hashPathStyleSeed(input: string): number {
  let h = 0;
  for (let i = 0; i < input.length; i += 1) {
    h = (h * 31 + input.charCodeAt(i)) >>> 0;
  }
  return h;
}

function overlayRouteStyle(idLike: string, explicitColor: string | undefined, idx: number): OverlayRouteStyle {
  const seed = hashPathStyleSeed(`${idLike}:${idx}`);
  const variant = seed % OVERLAY_ROUTE_DASH_SVG.length;
  const explicitNorm = String(explicitColor || "").trim().toLowerCase();
  const usePaletteColor = !explicitNorm || explicitNorm === "#98a2b3" || explicitNorm === "rgb(152,162,179)";
  const color = usePaletteColor ? OVERLAY_ROUTE_COLORS[seed % OVERLAY_ROUTE_COLORS.length]! : String(explicitColor);
  return {
    color,
    svgDash: OVERLAY_ROUTE_DASH_SVG[variant],
    width2d: 1.7 + (variant === 0 ? 0.5 : variant === 3 ? 0.35 : 0.1),
    width3d: 1.6 + (variant === 0 ? 0.45 : variant === 3 ? 0.3 : 0.1),
    pointRadius: 2.1 + (variant === 0 ? 0.4 : 0),
    opacity: 0.86,
    cesiumDashed: variant !== 0,
    cesiumDashPattern: OVERLAY_ROUTE_DASH_CESIUM[variant],
  };
}

function compactOverlayLabel(idLike: string): string {
  const label = String(idLike || "").trim();
  if (!label) return "route";
  if (label.length <= 18) return label;
  return `${label.slice(0, 15)}...`;
}

function trackMarkerFill(isSelected: boolean, muted: boolean): string {
  if (isSelected) return "#f97316";
  return muted ? "#98a2b3" : "#22c55e";
}

function normalizeHeadingDeg(headingDeg?: number): number {
  if (typeof headingDeg !== "number" || Number.isNaN(headingDeg)) return 0;
  const normalized = headingDeg % 360;
  return normalized < 0 ? normalized + 360 : normalized;
}

function coverageColor(status?: string): { fill: string; stroke: string } {
  if (status === "degraded") return { fill: "rgba(247,144,9,0.06)", stroke: "rgba(247,144,9,0.24)" };
  if (status === "maintenance") return { fill: "rgba(152,162,179,0.05)", stroke: "rgba(152,162,179,0.20)" };
  return { fill: "rgba(21,94,239,0.06)", stroke: "rgba(21,94,239,0.18)" };
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function geoDistanceMeters(lonA: number, latA: number, lonB: number, latB: number): number {
  const dLat = degToRad(latB - latA);
  const dLon = degToRad(lonB - lonA);
  const aLat = degToRad(latA);
  const bLat = degToRad(latB);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(aLat) * Math.cos(bLat) * Math.sin(dLon / 2) ** 2;
  return 2 * WGS84_A * Math.asin(Math.min(1, Math.sqrt(Math.max(0, h))));
}

function roundMeters(value: number | undefined): number {
  const rounded = Math.round(Number(value ?? 0));
  return Object.is(rounded, -0) ? 0 : rounded;
}

const WGS84_A = 6378137.0;
const WGS84_F = 1 / 298.257223563;
const WGS84_E2 = WGS84_F * (2 - WGS84_F);
const WGS84_B = WGS84_A * (1 - WGS84_F);
const WGS84_EP2 = (WGS84_A * WGS84_A - WGS84_B * WGS84_B) / (WGS84_B * WGS84_B);

function degToRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

function radToDeg(rad: number): number {
  return (rad * 180) / Math.PI;
}

function geodeticToEcef(lonDeg: number, latDeg: number, altM = 0): { x: number; y: number; z: number } {
  const lon = degToRad(lonDeg);
  const lat = degToRad(latDeg);
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const sinLon = Math.sin(lon);
  const cosLon = Math.cos(lon);
  const n = WGS84_A / Math.sqrt(1 - WGS84_E2 * sinLat * sinLat);
  return {
    x: (n + altM) * cosLat * cosLon,
    y: (n + altM) * cosLat * sinLon,
    z: (n * (1 - WGS84_E2) + altM) * sinLat,
  };
}

function ecefToGeodetic(x: number, y: number, z: number): { lon: number; lat: number; altM: number } {
  const p = Math.hypot(x, y);
  if (p < 1e-9) {
    const lat = z >= 0 ? 90 : -90;
    const altM = Math.abs(z) - WGS84_B;
    return { lon: 0, lat, altM };
  }
  const lon = Math.atan2(y, x);
  const theta = Math.atan2(z * WGS84_A, p * WGS84_B);
  const sinTheta = Math.sin(theta);
  const cosTheta = Math.cos(theta);
  const lat = Math.atan2(z + WGS84_EP2 * WGS84_B * sinTheta ** 3, p - WGS84_E2 * WGS84_A * cosTheta ** 3);
  const sinLat = Math.sin(lat);
  const n = WGS84_A / Math.sqrt(1 - WGS84_E2 * sinLat * sinLat);
  const altM = p / Math.max(1e-12, Math.cos(lat)) - n;
  return { lon: radToDeg(lon), lat: radToDeg(lat), altM };
}

function ecefToEnu(
  x: number,
  y: number,
  z: number,
  refLonDeg: number,
  refLatDeg: number,
  refAltM = 0
): { east: number; north: number; up: number } {
  const ref = geodeticToEcef(refLonDeg, refLatDeg, refAltM);
  const dx = x - ref.x;
  const dy = y - ref.y;
  const dz = z - ref.z;
  const lon0 = degToRad(refLonDeg);
  const lat0 = degToRad(refLatDeg);
  const sinLon = Math.sin(lon0);
  const cosLon = Math.cos(lon0);
  const sinLat = Math.sin(lat0);
  const cosLat = Math.cos(lat0);
  return {
    east: -sinLon * dx + cosLon * dy,
    north: -sinLat * cosLon * dx - sinLat * sinLon * dy + cosLat * dz,
    up: cosLat * cosLon * dx + cosLat * sinLon * dy + sinLat * dz,
  };
}

function enuToEcef(
  east: number,
  north: number,
  up: number,
  refLonDeg: number,
  refLatDeg: number,
  refAltM = 0
): { x: number; y: number; z: number } {
  const ref = geodeticToEcef(refLonDeg, refLatDeg, refAltM);
  const lon0 = degToRad(refLonDeg);
  const lat0 = degToRad(refLatDeg);
  const sinLon = Math.sin(lon0);
  const cosLon = Math.cos(lon0);
  const sinLat = Math.sin(lat0);
  const cosLat = Math.cos(lat0);
  const dx = -sinLon * east - sinLat * cosLon * north + cosLat * cosLon * up;
  const dy = cosLon * east - sinLat * sinLon * north + cosLat * sinLon * up;
  const dz = cosLat * north + sinLat * up;
  return { x: ref.x + dx, y: ref.y + dy, z: ref.z + dz };
}

function lonLatOffsetMeters(lonDeg: number, latDeg: number, eastM: number, northM: number): { x: number; y: number } {
  const ecef = enuToEcef(eastM, northM, 0, lonDeg, latDeg, 0);
  const geo = ecefToGeodetic(ecef.x, ecef.y, ecef.z);
  return { x: geo.lon, y: geo.lat };
}

function geoOffsetFromMeters(cx: number, cy: number, radiusM: number, angleRad: number): { x: number; y: number } {
  return lonLatOffsetMeters(cx, cy, Math.cos(angleRad) * radiusM, Math.sin(angleRad) * radiusM);
}

function nfzShape(zone: MissionNfz): "circle" | "box" {
  return String(zone.shape ?? "circle").trim().toLowerCase() === "box" ? "box" : "circle";
}

function worldOffsetMeters(cx: number, cy: number, dxMeters: number, dyMeters: number, mode: CoordinateMode): { x: number; y: number } {
  if (mode === "geo") {
    return lonLatOffsetMeters(cx, cy, dxMeters, dyMeters);
  }
  return { x: cx + dxMeters, y: cy + dyMeters };
}

function squareWorldCorners(cx: number, cy: number, halfSizeM: number, mode: CoordinateMode): MissionPoint[] {
  const s = Math.max(1, Number(halfSizeM || 0));
  return [
    worldOffsetMeters(cx, cy, -s, -s, mode),
    worldOffsetMeters(cx, cy, s, -s, mode),
    worldOffsetMeters(cx, cy, s, s, mode),
    worldOffsetMeters(cx, cy, -s, s, mode),
  ];
}

function circleWorldPoints(cx: number, cy: number, radiusM: number, mode: CoordinateMode, steps = 36): MissionPoint[] {
  const out: MissionPoint[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const a = (i / steps) * Math.PI * 2;
    if (mode === "geo") out.push(geoOffsetFromMeters(cx, cy, radiusM, a));
    else out.push({ x: cx + Math.cos(a) * radiusM, y: cy + Math.sin(a) * radiusM });
  }
  return out;
}

function isGeoLikeXY(x: number, y: number): boolean {
  return Number.isFinite(x) && Number.isFinite(y) && x >= -180 && x <= 180 && y >= -90 && y <= 90;
}

function collectMissionPoints(props: Props): MissionPoint[] {
  const pts: MissionPoint[] = [];
  pts.push(...props.route);
  if (props.plannedPosition) pts.push(props.plannedPosition);
  (props.trackedPositions ?? []).forEach((p) => pts.push({ x: p.x, y: p.y, z: p.z }));
  (props.baseStations ?? []).forEach((p) => pts.push({ x: p.x, y: p.y }));
  (props.routeOverlays ?? []).forEach((ov) => pts.push(...(ov.route ?? [])));
  (props.polygonOverlays ?? []).forEach((poly) => (poly.rings ?? []).forEach((ring) => pts.push(...ring)));
  (props.noFlyZones ?? []).forEach((z) => {
    pts.push({ x: z.cx, y: z.cy, z: z.z_min });
    pts.push({ x: z.cx, y: z.cy, z: z.z_max });
  });
  return pts.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
}

function decideCoordinateMode(props: Props): CoordinateMode {
  if (props.coordinateMode === "geo") return "geo";
  if (props.coordinateMode === "local") return "local";
  const pts = collectMissionPoints(props);
  if (!pts.length) return "local";
  const geoPts = pts.filter((p) => isGeoLikeXY(p.x, p.y));
  const maxAbsY = Math.max(...pts.map((p) => Math.abs(p.y)));
  const maxAbsX = Math.max(...pts.map((p) => Math.abs(p.x)));
  const spanX = Math.max(...pts.map((p) => p.x)) - Math.min(...pts.map((p) => p.x));
  const spanY = Math.max(...pts.map((p) => p.y)) - Math.min(...pts.map((p) => p.y));
  if (maxAbsY > 90 || maxAbsX > 180) return "local";
  if (geoPts.length >= Math.max(2, Math.ceil(pts.length * 0.75)) && spanX <= 5 && spanY <= 5) return "geo";
  return "local";
}

function worldFromPoints(points: MissionPoint[], fallback: WorldBounds, mode: CoordinateMode): WorldBounds {
  if (!points.length) return fallback;
  let minX = points[0]!.x;
  let maxX = points[0]!.x;
  let minY = points[0]!.y;
  let maxY = points[0]!.y;
  points.forEach((p) => {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  });
  const dx = Math.max(1e-6, maxX - minX);
  const dy = Math.max(1e-6, maxY - minY);
  const minPad = mode === "geo" ? 0.001 : 5;
  const padX = Math.max(dx * 0.08, minPad);
  const padY = Math.max(dy * 0.08, minPad);
  return {
    minX: minX - padX,
    maxX: maxX + padX,
    minY: minY - padY,
    maxY: maxY + padY,
  };
}

function buildCoordinateContext(props: Props, localOrigin?: GeodeticOrigin | null): CoordinateContext {
  const mode = decideCoordinateMode(props);
  const points = collectMissionPoints(props);
  const extentPoints: MissionPoint[] = [...points];
  (props.noFlyZones ?? []).forEach((z) => {
    const halfSizeM = Math.max(1, Number(z.radius_m ?? 0));
    if (nfzShape(z) === "box") squareWorldCorners(z.cx, z.cy, halfSizeM, mode).forEach((p) => extentPoints.push(p));
    else circleWorldPoints(z.cx, z.cy, halfSizeM, mode, 24).forEach((p) => extentPoints.push(p));
  });
  const coverageMap = new Map((props.coverage ?? []).map((c) => [c.bsId, Number(c.radiusM ?? 0)]));
  (props.baseStations ?? []).forEach((bs) => {
    const rMeters = coverageMap.get(bs.id);
    if (rMeters && Number.isFinite(rMeters) && rMeters > 0) {
      circleWorldPoints(bs.x, bs.y, rMeters, mode, 24).forEach((p) => extentPoints.push(p));
    }
  });
  const world = worldFromPoints(extentPoints, mode === "geo" ? DEFAULT_GEO_WORLD : DEFAULT_LOCAL_WORLD, mode);
  const altSamples = points
    .map((p) => Number(p.z ?? 0))
    .filter((v) => Number.isFinite(v));
  const maxAltitudeM = Math.max(120, altSamples.length ? Math.max(...altSamples) + 10 : 120);
  const hasLocalOrigin =
    Boolean(localOrigin)
    && Number.isFinite(Number(localOrigin?.lon))
    && Number.isFinite(Number(localOrigin?.lat))
    && Number(localOrigin?.lon) >= -180
    && Number(localOrigin?.lon) <= 180
    && Number(localOrigin?.lat) >= -90
    && Number(localOrigin?.lat) <= 90;
  const originLon = mode === "geo" ? (world.minX + world.maxX) / 2 : hasLocalOrigin ? Number(localOrigin!.lon) : DEFAULT_LOCAL_ORIGIN.lon;
  const originLat = mode === "geo" ? (world.minY + world.maxY) / 2 : hasLocalOrigin ? Number(localOrigin!.lat) : DEFAULT_LOCAL_ORIGIN.lat;
  return { mode, world, maxAltitudeM, originLon, originLat };
}

function mergeById<T extends { id: string }>(sharedRows: T[], ownRows: T[]): T[] {
  const merged = new Map<string, T>();
  sharedRows.forEach((row) => {
    if (row.id) merged.set(String(row.id), row);
  });
  ownRows.forEach((row) => {
    if (row.id) merged.set(String(row.id), row);
  });
  return Array.from(merged.values());
}

function mergeCoverageRows(sharedRows: MissionCoverage[], ownRows: MissionCoverage[]): MissionCoverage[] {
  const merged = new Map<string, MissionCoverage>();
  sharedRows.forEach((row) => {
    if (row.bsId) merged.set(String(row.bsId), row);
  });
  ownRows.forEach((row) => {
    if (row.bsId) merged.set(String(row.bsId), row);
  });
  return Array.from(merged.values());
}

function mergeNoFlyZones(sharedRows: MissionNfz[], ownRows: MissionNfz[]): MissionNfz[] {
  const merged = new Map<string, MissionNfz>();
  const keyOf = (row: MissionNfz, idx: number) => {
    if (row.zone_id && String(row.zone_id).trim()) return `z:${String(row.zone_id).trim()}`;
    return `xy:${Number(row.cx).toFixed(6)}:${Number(row.cy).toFixed(6)}:${Number(row.radius_m).toFixed(2)}:${idx}`;
  };
  sharedRows.forEach((row, idx) => merged.set(keyOf(row, idx), row));
  ownRows.forEach((row, idx) => merged.set(keyOf(row, idx + 10_000), row));
  return Array.from(merged.values());
}

function worldToLonLat(x: number, y: number, coord: CoordinateContext): { lon: number; lat: number } {
  if (coord.mode === "geo") return { lon: x, lat: y };
  const ecef = enuToEcef(x, y, 0, coord.originLon, coord.originLat, 0);
  const geo = ecefToGeodetic(ecef.x, ecef.y, ecef.z);
  return { lon: geo.lon, lat: geo.lat };
}

function lonLatToWorld(lon: number, lat: number, coord: CoordinateContext): { x: number; y: number } {
  if (coord.mode === "geo") return { x: lon, y: lat };
  const ecef = geodeticToEcef(lon, lat, 0);
  const enu = ecefToEnu(ecef.x, ecef.y, ecef.z, coord.originLon, coord.originLat, 0);
  return { x: enu.east, y: enu.north };
}

function projectRingPath(points: ScreenPoint[]): string {
  return points.map((p) => `${p.x},${p.y}`).join(" ");
}

function useCesiumLoader(): { state: CesiumLoadState; error: string | null } {
  const [state, setState] = useState<CesiumLoadState>(() => (typeof window !== "undefined" && window.Cesium ? "ready" : "idle"));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.Cesium) {
      setState("ready");
      return;
    }
    setState("loading");

    const cssId = "cesium-widgets-css";
    if (!document.getElementById(cssId)) {
      const link = document.createElement("link");
      link.id = cssId;
      link.rel = "stylesheet";
      link.href = CESIUM_CSS_URL;
      document.head.appendChild(link);
    }

    const scriptId = "cesium-js-script";
    let script = document.getElementById(scriptId) as HTMLScriptElement | null;
    if (!script) {
      script = document.createElement("script");
      script.id = scriptId;
      script.src = CESIUM_JS_URL;
      script.async = true;
      document.body.appendChild(script);
    }

    const onLoad = () => {
      if (window.Cesium) {
        setState("ready");
        setError(null);
      } else {
        setState("error");
        setError("Cesium loaded script but window.Cesium is unavailable");
      }
    };
    const onError = () => {
      setState("error");
      setError("Failed to load Cesium CDN assets (network blocked/unavailable)");
    };

    script.addEventListener("load", onLoad);
    script.addEventListener("error", onError);
    if ((script as any).dataset.loaded === "1") onLoad();
    else script.addEventListener("load", () => {
      if (script) script.dataset.loaded = "1";
    });

    return () => {
      script?.removeEventListener("load", onLoad);
      script?.removeEventListener("error", onError);
    };
  }, []);

  return { state, error };
}

function buildCirclePolygonLonLat(cx: number, cy: number, r: number, coord: CoordinateContext, steps = 36): number[] {
  const out: number[] = [];
  const points = circleWorldPoints(cx, cy, r, coord.mode, steps);
  points.forEach((pt) => {
    const p = worldToLonLat(pt.x, pt.y, coord);
    out.push(p.lon, p.lat);
  });
  return out;
}

function buildSquarePolygonLonLat(cx: number, cy: number, halfSizeM: number, coord: CoordinateContext): number[] {
  const out: number[] = [];
  squareWorldCorners(cx, cy, halfSizeM, coord.mode).forEach((pt) => {
    const p = worldToLonLat(pt.x, pt.y, coord);
    out.push(p.lon, p.lat);
  });
  return out;
}

function CesiumMissionMap({
  viewMode,
  basemap,
  nlsApiKey,
  mapServiceBase,
  mapServiceConfig,
  route,
  plannedPosition,
  trackedPositions = [],
  routeOverlays = [],
  polygonOverlays = [],
  focusSelectedTrack = false,
  selectedUavId,
  noFlyZones = [],
  baseStations = [],
  coverage = [],
  showCoverage,
  trackMarkerStyle = "dot",
  clickable = false,
  onAddWaypoint,
  onAddNoFlyZoneCenter,
  onRecenterCurrent,
  canRecenterCurrent = false,
  resetViewSeq = 0,
  focusPoint,
  focusSeq = 0,
  coordinateContext,
  highContrastRoute = true,
}: Props & {
  viewMode: ViewMode;
  basemap: CesiumBasemapChoice;
  nlsApiKey: string;
  mapServiceBase: string;
  mapServiceConfig: MapServiceConfig | null;
  onRecenterCurrent?: () => void;
  canRecenterCurrent?: boolean;
  resetViewSeq?: number;
  focusPoint?: MissionPoint | null;
  focusSeq?: number;
  coordinateContext: CoordinateContext;
  highContrastRoute?: boolean;
}): React.ReactNode {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<any | null>(null);
  const handlerRef = useRef<any | null>(null);
  const didInitialFitRef = useRef(false);
  const consumedFocusSeqRef = useRef(0);
  const clickConfigRef = useRef<{
    clickable: boolean;
    onAddWaypoint?: (point: MissionPoint) => void;
    onAddNoFlyZoneCenter?: (point: MissionPoint) => void;
  }>({ clickable, onAddWaypoint, onAddNoFlyZoneCenter });
  const coordinateContextRef = useRef<CoordinateContext>(coordinateContext);

  useEffect(() => {
    clickConfigRef.current = { clickable, onAddWaypoint, onAddNoFlyZoneCenter };
  }, [clickable, onAddNoFlyZoneCenter, onAddWaypoint]);

  useEffect(() => {
    coordinateContextRef.current = coordinateContext;
  }, [coordinateContext]);

  useEffect(() => {
    if (!hostRef.current || !window.Cesium) return;
    const Cesium = window.Cesium;
    if (CESIUM_ACCESS_TOKEN) {
      Cesium.Ion.defaultAccessToken = CESIUM_ACCESS_TOKEN;
    }
    const viewer = new Cesium.Viewer(hostRef.current, {
      animation: false,
      timeline: false,
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
      shouldAnimate: true,
      terrainProvider: new Cesium.EllipsoidTerrainProvider(),
    });
    viewer.scene.globe.depthTestAgainstTerrain = false;
    viewer.scene.globe.enableLighting = false;
    viewer.scene.skyBox.show = true;
    viewer.scene.skyAtmosphere.show = true;
    viewer.scene.fog.enabled = false;
    viewer.scene.highDynamicRange = false;
    viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#f3f4f6");
    viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#f3f4f6");
    viewerRef.current = viewer;

    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction((movement: any) => {
      const cfg = clickConfigRef.current;
      if (!cfg.clickable) return;
      if (!cfg.onAddWaypoint && !cfg.onAddNoFlyZoneCenter) return;
      const cartesian = viewer.camera.pickEllipsoid(movement.position, viewer.scene.globe.ellipsoid);
      if (!cartesian) return;
      const carto = Cesium.Cartographic.fromCartesian(cartesian);
      const lon = Cesium.Math.toDegrees(carto.longitude);
      const lat = Cesium.Math.toDegrees(carto.latitude);
      const ctx = coordinateContextRef.current;
      const xy = lonLatToWorld(lon, lat, ctx);
      const point = {
        x: Number(clamp(xy.x, ctx.world.minX, ctx.world.maxX).toFixed(6)),
        y: Number(clamp(xy.y, ctx.world.minY, ctx.world.maxY).toFixed(6)),
        z: 40,
      };
      const shift = Boolean(movement?.shiftKey);
      if (shift && cfg.onAddNoFlyZoneCenter) cfg.onAddNoFlyZoneCenter(point);
      else if (cfg.onAddWaypoint) cfg.onAddWaypoint(point);
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    handlerRef.current = handler;

    return () => {
      try {
        handlerRef.current?.destroy?.();
      } catch {
        // ignore cleanup failure
      }
      handlerRef.current = null;
      try {
        viewer.destroy();
      } catch {
        // ignore cleanup failure
      }
      viewerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;
    try {
      viewer.imageryLayers.removeAll();
      const provider = createCesiumImageryProvider(Cesium, basemap, {
        nlsApiKey,
        mapServiceBase,
        mapServiceConfig,
      });
      viewer.imageryLayers.addImageryProvider(provider);
    } catch {
      // ignore imagery swap errors while Cesium initializes
    }
  }, [basemap, mapServiceBase, mapServiceConfig, nlsApiKey]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;
    const sceneMode = viewMode === "2d" ? Cesium.SceneMode.SCENE2D : Cesium.SceneMode.SCENE3D;
    if (viewer.scene.mode !== sceneMode) {
      if (viewMode === "2d") viewer.scene.morphTo2D(0.4);
      else viewer.scene.morphTo3D(0.4);
    }
  }, [viewMode]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium || resetViewSeq <= 0) return;
    try {
      if (route.length > 0 || noFlyZones.length > 0 || trackedPositions.length > 0 || baseStations.length > 0) {
        viewer.flyTo(viewer.entities, { duration: 0.6, offset: new Cesium.HeadingPitchRange(0, -0.7, 1800) });
      } else {
        const ll = worldToLonLat((coordinateContext.world.minX + coordinateContext.world.maxX) / 2, (coordinateContext.world.minY + coordinateContext.world.maxY) / 2, coordinateContext);
        viewer.camera.flyTo({
          destination: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, 1800),
          duration: 0.6,
        });
      }
    } catch {
      // ignore reset failures while viewer initializes
    }
  }, [resetViewSeq]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium || focusSeq <= 0 || !focusPoint) return;
    if (consumedFocusSeqRef.current === focusSeq) return;
    consumedFocusSeqRef.current = focusSeq;
    try {
      const ll = worldToLonLat(focusPoint.x, focusPoint.y, coordinateContext);
      const camHeight = clamp(Number(focusPoint.z ?? 0) + 700, 650, 2400);
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, camHeight),
        duration: 0.6,
      });
    } catch {
      // ignore focus failures while viewer initializes
    }
  }, [coordinateContext, focusPoint, focusSeq]);

  const removeEntitiesByPrefix = (prefix: string) => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    const matches = viewer.entities.values.filter((entity: any) => String(entity?.id ?? "").startsWith(prefix));
    matches.forEach((entity: any) => {
      try {
        viewer.entities.remove(entity);
      } catch {
        // ignore remove failures while entities update
      }
    });
  };

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;
    removeEntitiesByPrefix("static:");

    routeOverlays.forEach((ov, ovIdx) => {
      if (!Array.isArray(ov.route) || ov.route.length < 2) return;
      const overlayId = String(ov.id || `route-${ovIdx + 1}`);
      const style = overlayRouteStyle(overlayId, ov.color, ovIdx);
      const pos = ov.route.flatMap((p) => {
        const ll = worldToLonLat(p.x, p.y, coordinateContext);
        return [ll.lon, ll.lat, Number(p.z ?? 0)];
      });
      viewer.entities.add({
        id: `static:route-overlay:${ovIdx}:${overlayId}`,
        name: `Route ${overlayId}`,
        polyline: {
          positions: Cesium.Cartesian3.fromDegreesArrayHeights(pos),
          width: style.width3d,
          material: style.cesiumDashed
            ? new Cesium.PolylineDashMaterialProperty({
                color: Cesium.Color.fromCssColorString(style.color).withAlpha(style.opacity),
                dashLength: 18,
                dashPattern: style.cesiumDashPattern ?? 0xff00,
              })
            : Cesium.Color.fromCssColorString(style.color).withAlpha(style.opacity),
          clampToGround: false,
        },
      });
      const tail = ov.route[ov.route.length - 1];
      if (tail) {
        const ll = worldToLonLat(tail.x, tail.y, coordinateContext);
        viewer.entities.add({
          id: `static:route-overlay-tail:${ovIdx}:${overlayId}`,
          position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, Number(tail.z ?? 0)),
          point: {
            pixelSize: 7.4,
            color: Cesium.Color.fromCssColorString("#ffffff"),
            outlineColor: Cesium.Color.fromCssColorString(style.color),
            outlineWidth: 2,
          },
          label: {
            text: compactOverlayLabel(overlayId),
            font: "11px sans-serif",
            fillColor: Cesium.Color.fromCssColorString(style.color),
            showBackground: true,
            backgroundColor: Cesium.Color.fromCssColorString("#ffffff").withAlpha(0.72),
            pixelOffset: new Cesium.Cartesian2(8, 10),
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            outlineWidth: 2,
          },
        });
      }
    });

    polygonOverlays.forEach((poly, i) => {
      const rings = Array.isArray(poly.rings) ? poly.rings.filter((ring) => Array.isArray(ring) && ring.length >= 3) : [];
      if (!rings.length) return;
      const polyId = String(poly.id || `poly-${i + 1}`);
      const outer = rings[0].flatMap((p) => {
        const ll = worldToLonLat(Number(p.x ?? 0), Number(p.y ?? 0), coordinateContext);
        return [ll.lon, ll.lat];
      });
      if (outer.length < 6) return;
      const holes = rings
        .slice(1)
        .map((ring) =>
          ring.flatMap((p) => {
            const ll = worldToLonLat(Number(p.x ?? 0), Number(p.y ?? 0), coordinateContext);
            return [ll.lon, ll.lat];
          })
        )
        .filter((coords) => coords.length >= 6)
        .map((coords) => new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(coords)));
      const zValues = rings.flatMap((ring) => ring.map((p) => Number(p.z ?? 0))).filter((v) => Number.isFinite(v));
      const maxZ = zValues.length ? Math.max(0, ...zValues) : 0;
      const labelPoint = rings[0][0] ?? null;
      const labelLonLat = labelPoint ? worldToLonLat(Number(labelPoint.x ?? 0), Number(labelPoint.y ?? 0), coordinateContext) : null;
      viewer.entities.add({
        id: `static:polygon:${i}:${polyId}`,
        name: poly.id || `Polygon-${i + 1}`,
        polygon: {
          hierarchy: new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(outer), holes),
          height: 0,
          extrudedHeight: maxZ > 0 ? maxZ : undefined,
          material: Cesium.Color.fromCssColorString(poly.fill || "rgba(21,94,239,0.16)"),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString(poly.color || "#155eef"),
        },
      });
      if (poly.label && labelLonLat) {
        viewer.entities.add({
          id: `static:polygon-label:${i}:${polyId}`,
          position: Cesium.Cartesian3.fromDegrees(labelLonLat.lon, labelLonLat.lat, maxZ > 0 ? maxZ : 0),
          label: {
            text: String(poly.label),
            font: "11px sans-serif",
            fillColor: Cesium.Color.fromCssColorString(poly.color || "#155eef"),
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(8, -6),
          },
        });
      }
    });

    if (route.length >= 2) {
      const pos = route.flatMap((p) => {
        const ll = worldToLonLat(p.x, p.y, coordinateContext);
        return [ll.lon, ll.lat, Number(p.z ?? 0)];
      });
      if (highContrastRoute) {
        viewer.entities.add({
          id: "static:planned-route-casing",
          name: "Planned Route",
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArrayHeights(pos),
            width: 10,
            material: Cesium.Color.fromCssColorString("#101828").withAlpha(0.9),
            clampToGround: false,
          },
        });
        viewer.entities.add({
          id: "static:planned-route",
          name: "Planned Route",
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArrayHeights(pos),
            width: 5.4,
            material: new Cesium.PolylineGlowMaterialProperty({
              glowPower: 0.3,
              color: Cesium.Color.fromCssColorString("#fde047"),
            }),
            clampToGround: false,
          },
        });
      } else {
        viewer.entities.add({
          id: "static:planned-route",
          name: "Planned Route",
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArrayHeights(pos),
            width: 4.2,
            material: Cesium.Color.fromCssColorString("#155eef").withAlpha(0.95),
            clampToGround: false,
          },
        });
      }
      route.forEach((p, i) => {
        const ll = worldToLonLat(p.x, p.y, coordinateContext);
        const z = Number(p.z ?? 0);
        const waypointRole = i === 0 ? "HM" : i === route.length - 1 ? "END" : `WP${i}`;
        viewer.entities.add({
          id: `static:planned-route-wp:${i}`,
          position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, z),
          point: {
            pixelSize: i === 0 || i === route.length - 1 ? 11 : 9,
            color: Cesium.Color.fromCssColorString(highContrastRoute ? "#fde047" : "#ffffff"),
            outlineColor: Cesium.Color.fromCssColorString(highContrastRoute ? "#111827" : "#155eef"),
            outlineWidth: 2,
          },
          label: {
            text: waypointRole,
            font: "11px sans-serif",
            fillColor: Cesium.Color.fromCssColorString(highContrastRoute ? "#111827" : "#155eef"),
            showBackground: true,
            backgroundColor: Cesium.Color.fromCssColorString("#ffffff").withAlpha(0.78),
            pixelOffset: new Cesium.Cartesian2(8, -8),
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            outlineWidth: 2,
          },
        });
      });
    }

    noFlyZones.forEach((z, i) => {
      const shape = nfzShape(z);
      const halfSizeM = Math.max(1, Number(z.radius_m ?? 0));
      const zMinM = Number(z.z_min ?? 0);
      const zMaxM = Number(z.z_max ?? 120);
      const ring = shape === "box" ? buildSquarePolygonLonLat(z.cx, z.cy, halfSizeM, coordinateContext) : buildCirclePolygonLonLat(z.cx, z.cy, halfSizeM, coordinateContext, 48);
      const zoneLL = worldToLonLat(z.cx, z.cy, coordinateContext);
      const zoneId = String(z.zone_id ?? `NFZ-${i + 1}`);
      viewer.entities.add({
        id: `static:nfz:${i}:${zoneId}`,
        name: zoneId,
        polygon: {
          hierarchy: Cesium.Cartesian3.fromDegreesArray(ring),
          height: zMinM,
          extrudedHeight: zMaxM,
          material: Cesium.Color.fromCssColorString("#f04438").withAlpha(0.26),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString("#f04438"),
          perPositionHeight: false,
        },
        label: {
          text: `${zoneId} ${shape === "box" ? "box " : ""}R${Math.round(halfSizeM)}m (${Math.round(zMinM)}-${Math.round(zMaxM)}m)`,
          font: "12px sans-serif",
          fillColor: Cesium.Color.fromCssColorString("#b42318"),
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString("#ffffff").withAlpha(0.72),
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -12),
          heightReference: Cesium.HeightReference.NONE,
        },
        position: Cesium.Cartesian3.fromDegrees(zoneLL.lon, zoneLL.lat, zMaxM),
      });
      const topRingHeights: number[] = [];
      for (let ri = 0; ri + 1 < ring.length; ri += 2) {
        topRingHeights.push(ring[ri]!, ring[ri + 1]!, zMaxM);
      }
      if (ring.length >= 6 && topRingHeights.length >= 9) {
        topRingHeights.push(ring[0]!, ring[1]!, zMaxM);
        viewer.entities.add({
          id: `static:nfz-top:${i}:${zoneId}`,
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArrayHeights(topRingHeights),
            width: 2.6,
            material: Cesium.Color.fromCssColorString("#b42318").withAlpha(0.95),
            clampToGround: false,
          },
        });
      }
    });

    baseStations.forEach((bs) => {
      const ll = worldToLonLat(bs.x, bs.y, coordinateContext);
      viewer.entities.add({
        id: `static:bs:${String(bs.id)}`,
        name: bs.id,
        position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, 0),
        point: {
          pixelSize: 10,
          color: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.fromCssColorString(statusColor(bs.status)),
          outlineWidth: 2,
        },
        label: {
          text: bs.id,
          font: "12px sans-serif",
          fillColor: Cesium.Color.fromCssColorString("#101828"),
          pixelOffset: new Cesium.Cartesian2(10, -10),
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
        },
      });

      if (showCoverage) {
        const cov = coverage.find((c) => c.bsId === bs.id);
        if (cov) {
          const ring = buildCirclePolygonLonLat(bs.x, bs.y, cov.radiusM, coordinateContext, 48);
          viewer.entities.add({
            id: `static:bs-coverage:${String(bs.id)}`,
            polygon: {
              hierarchy: Cesium.Cartesian3.fromDegreesArray(ring),
              height: 0,
              material: Cesium.Color.fromCssColorString("#155eef").withAlpha(0.05),
              outline: true,
              outlineColor: Cesium.Color.fromCssColorString("#155eef").withAlpha(0.32),
            },
          });
        }
      }
    });

    if (plannedPosition) {
      const ll = worldToLonLat(plannedPosition.x, plannedPosition.y, coordinateContext);
      viewer.entities.add({
        id: "static:planned-position",
        name: "planned",
        position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, Number(plannedPosition.z ?? 0)),
        point: {
          pixelSize: 10,
          color: Cesium.Color.fromCssColorString("#f59e0b"),
          outlineColor: Cesium.Color.fromCssColorString("#92400e"),
          outlineWidth: 1.5,
        },
        label: {
          text: `planned • ${roundMeters(Number(plannedPosition.z ?? 0))}m`,
          font: "12px sans-serif",
          pixelOffset: new Cesium.Cartesian2(10, 12),
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
        },
      });
    }
  }, [baseStations, coordinateContext, coverage, highContrastRoute, noFlyZones, plannedPosition, polygonOverlays, route, routeOverlays, showCoverage]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;
    removeEntitiesByPrefix("uav:");

    trackedPositions.forEach((t) => {
      const ll = worldToLonLat(t.x, t.y, coordinateContext);
      const z = Number(t.z ?? 0);
      const isSelected = t.id === selectedUavId;
      const muted = focusSelectedTrack && !isSelected;
      const fill = trackMarkerFill(isSelected, muted);
      const headingRad = (normalizeHeadingDeg(t.headingDeg) * Math.PI) / 180;
      const trackId = String(t.id || "uav");
      const nose = worldOffsetMeters(
        t.x,
        t.y,
        Math.cos(headingRad) * 14,
        Math.sin(headingRad) * 14,
        coordinateContext.mode,
      );
      const noseLl = worldToLonLat(nose.x, nose.y, coordinateContext);
      const speedText = typeof t.speedMps === "number" ? ` • ${Math.round(t.speedMps)}m/s` : "";

      if (trackMarkerStyle === "uav") {
        viewer.entities.add({
          id: `uav:ellipse:${trackId}`,
          position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, z),
          ellipse: {
            semiMinorAxis: isSelected ? 11 : 8,
            semiMajorAxis: isSelected ? 11 : 8,
            material: Cesium.Color.fromCssColorString(riskColor(t.interferenceRisk)).withAlpha(isSelected ? 0.18 : 0.1),
            outline: true,
            outlineColor: Cesium.Color.fromCssColorString(riskColor(t.interferenceRisk)).withAlpha(0.7),
            outlineWidth: 1,
            height: z,
          },
        });
        viewer.entities.add({
          id: `uav:nose:${trackId}`,
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArrayHeights([ll.lon, ll.lat, z, noseLl.lon, noseLl.lat, z]),
            width: isSelected ? 2.6 : 2,
            material: Cesium.Color.fromCssColorString(muted ? "#94a3b8" : "#0f172a"),
            clampToGround: false,
          },
        });
      }

      viewer.entities.add({
        id: `uav:marker:${trackId}`,
        name: t.id,
        position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, z),
        point: {
          pixelSize: trackMarkerStyle === "uav" ? (isSelected ? 14 : 11) : isSelected ? 12 : 9,
          color: Cesium.Color.fromCssColorString(fill),
          outlineColor: Cesium.Color.fromCssColorString(muted ? "#667085" : "#1f2937"),
          outlineWidth: 1,
        },
        label: {
          text: trackMarkerStyle === "uav" ? `${t.id} • ${roundMeters(z)}m${speedText}` : `${t.id} • ${roundMeters(z)}m`,
          font: "12px sans-serif",
          fillColor: Cesium.Color.fromCssColorString(muted ? "#667085" : "#101828"),
          pixelOffset: new Cesium.Cartesian2(10, 12),
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
        },
      });
    });
  }, [coordinateContext, focusSelectedTrack, selectedUavId, trackedPositions, trackMarkerStyle]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium || didInitialFitRef.current) return;
    const hasRenderableData = route.length > 0 || noFlyZones.length > 0 || trackedPositions.length > 0 || baseStations.length > 0 || routeOverlays.length > 0 || polygonOverlays.length > 0 || Boolean(plannedPosition);
    if (!didInitialFitRef.current) {
      didInitialFitRef.current = true;
      try {
        if (hasRenderableData && viewer.entities.values.length > 0) {
          viewer.flyTo(viewer.entities, { duration: 0.6, offset: new Cesium.HeadingPitchRange(0, -0.7, 1800) });
        } else {
          const ll = worldToLonLat((coordinateContext.world.minX + coordinateContext.world.maxX) / 2, (coordinateContext.world.minY + coordinateContext.world.maxY) / 2, coordinateContext);
          viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, 1800),
            duration: 0.6,
          });
        }
      } catch {
        // ignore initial fit failures while viewer initializes
      }
    }
  }, [baseStations, coordinateContext, noFlyZones, plannedPosition, polygonOverlays, route, routeOverlays, trackedPositions]);

  const handleCesiumZoom = (dir: -1 | 1) => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    try {
      const amount = viewer.scene.mode === (window.Cesium?.SceneMode?.SCENE2D ?? -1) ? 400 : 220;
      if (dir > 0) viewer.camera.zoomIn(amount);
      else viewer.camera.zoomOut(amount);
    } catch {
      // ignore zoom failures while viewer initializes
    }
  };

  return (
    <div style={{ position: "relative", width: "100%", height: 360 }}>
      <div ref={hostRef} style={{ width: "100%", height: 360, borderRadius: 12, overflow: "hidden", border: "1px solid #e4e7ec", background: "#f9fbff" }} />
      <div style={{ position: "absolute", top: 10, right: 10, display: "grid", gap: 6, zIndex: 4 }}>
        <button
          type="button"
          aria-label="Recenter to current UAV position"
          onClick={() => onRecenterCurrent?.()}
          disabled={!canRecenterCurrent}
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            border: "1px solid #d0d5dd",
            background: canRecenterCurrent ? "#fff" : "#f2f4f7",
            color: canRecenterCurrent ? "#344054" : "#98a2b3",
            fontSize: 9,
            lineHeight: "10px",
            fontWeight: 700,
            letterSpacing: 0.2,
            cursor: canRecenterCurrent ? "pointer" : "not-allowed",
            boxShadow: "0 1px 2px rgba(16,24,40,0.12)",
          }}
        >
          LOC
        </button>
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => handleCesiumZoom(1)}
          style={{ width: 32, height: 32, borderRadius: 8, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", fontSize: 18, lineHeight: "18px", fontWeight: 700, cursor: "pointer", boxShadow: "0 1px 2px rgba(16,24,40,0.12)" }}
        >
          +
        </button>
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => handleCesiumZoom(-1)}
          style={{ width: 32, height: 32, borderRadius: 8, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", fontSize: 18, lineHeight: "18px", fontWeight: 700, cursor: "pointer", boxShadow: "0 1px 2px rgba(16,24,40,0.12)" }}
        >
          -
        </button>
      </div>
    </div>
  );
}

function SvgMissionMap({
  title,
  route,
  plannedPosition,
  trackedPositions = [],
  routeOverlays = [],
  polygonOverlays = [],
  selectedUavId,
  focusSelectedTrack = false,
  noFlyZones = [],
  baseStations = [],
  coverage = [],
  showCoverage = true,
  showInterferenceHints = true,
  trackMarkerStyle = "dot",
  clickable = false,
  onAddWaypoint,
  onAddNoFlyZoneCenter,
  onRecenterCurrent,
  canRecenterCurrent = false,
  initialViewMode = "3d",
  resetKey = 0,
  zoomResetKey = 0,
  focusPoint,
  focusSeq = 0,
  coordinateContext,
}: Props & {
  title: string;
  onRecenterCurrent?: () => void;
  canRecenterCurrent?: boolean;
  initialViewMode?: ViewMode;
  resetKey?: number;
  zoomResetKey?: number;
  focusPoint?: MissionPoint | null;
  focusSeq?: number;
  coordinateContext: CoordinateContext;
}): React.ReactNode {
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode);
  const [yawDeg, setYawDeg] = useState(-28);
  const [pitchDeg, setPitchDeg] = useState(24);
  const [zoomScale, setZoomScale] = useState(1);
  const [panOffset, setPanOffset] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const consumedFocusSeqRef = useRef(0);
  const dragRef = useRef<{ active: boolean; moved: boolean; x: number; y: number; mode: "pan" | "orbit" | null }>({
    active: false,
    moved: false,
    x: 0,
    y: 0,
    mode: null,
  });

  useEffect(() => {
    setViewMode(initialViewMode);
  }, [initialViewMode]);

  useEffect(() => {
    if (initialViewMode === "3d") {
      setYawDeg(-28);
      setPitchDeg(24);
    }
    setPanOffset({ x: 0, y: 0 });
  }, [initialViewMode, resetKey]);

  useEffect(() => {
    setZoomScale(1);
    setPanOffset({ x: 0, y: 0 });
  }, [zoomResetKey]);

  const width = 760;
  const height = 360;
  const pad = 20;
  const world = coordinateContext.world;
  const maxAltitudeM = coordinateContext.maxAltitudeM;
  const mapX = (x: number) => x;
  const mapY = (y: number) => y;
  const unmapX = (x: number) => x;
  const unmapY = (y: number) => y;
  const worldMap = { minX: mapX(world.minX), maxX: mapX(world.maxX), minY: mapY(world.minY), maxY: mapY(world.maxY) };
  const mapSpanX = Math.max(1e-6, worldMap.maxX - worldMap.minX);
  const mapSpanY = Math.max(1e-6, worldMap.maxY - worldMap.minY);
  const zMetersToWorld = coordinateContext.mode === "geo" ? 1 / 111_320 : 1;
  const worldCenter = { x: (world.minX + world.maxX) / 2, y: (world.minY + world.maxY) / 2, z: maxAltitudeM / 2 };
  const worldCenterMap = { x: mapX(worldCenter.x), y: mapY(worldCenter.y) };
  const sx = (x: number) => pad + ((mapX(x) - worldMap.minX) / mapSpanX) * (width - pad * 2);
  const sy = (y: number) => height - pad - ((mapY(y) - worldMap.minY) / mapSpanY) * (height - pad * 2);
  const wx = (px: number) => unmapX(worldMap.minX + ((px - pad) / (width - pad * 2)) * mapSpanX);
  const wy = (py: number) => unmapY(worldMap.minY + ((height - pad - py) / (height - pad * 2)) * mapSpanY);
  const zOf = (z?: number) => clamp(Number(z ?? 0), 0, maxAltitudeM);
  const coverageMap = new Map(coverage.map((c) => [c.bsId, c.radiusM]));
  const selectedTrack = trackedPositions.find((t) => t.id === selectedUavId) ?? trackedPositions[0];

  useEffect(() => {
    if (focusSeq <= 0 || !focusPoint) return;
    if (consumedFocusSeqRef.current === focusSeq) return;
    consumedFocusSeqRef.current = focusSeq;
    const cx = width * 0.5;
    const cy = height * 0.5;
    const rawX = sx(focusPoint.x);
    const rawY = sy(focusPoint.y);
    setPanOffset({
      x: clamp(Number((-(rawX - cx) * zoomScale).toFixed(3)), -width * 1.4, width * 1.4),
      y: clamp(Number((-(rawY - cy) * zoomScale).toFixed(3)), -height * 1.4, height * 1.4),
    });
  }, [focusPoint, focusSeq, height, mapSpanX, mapSpanY, pad, worldMap.maxX, worldMap.maxY, worldMap.minX, worldMap.minY, width, zoomScale]);

  const projection = useMemo(() => {
    const yaw = (yawDeg * Math.PI) / 180;
    const pitch = (pitchDeg * Math.PI) / 180;
    const cosY = Math.cos(yaw);
    const sinY = Math.sin(yaw);
    const cosP = Math.cos(pitch);
    const sinP = Math.sin(pitch);
    const scaleX = (width - pad * 2) / mapSpanX * 0.72 * zoomScale;
    const scaleY = (height - pad * 2) / mapSpanY * 0.72 * zoomScale;
    const scaleZ = ((scaleX + scaleY) * 0.5) * 1.25;
    const cx = width * 0.5;
    const cy = height * 0.58;
    const cameraDepth = 520;
    const perspective = 0.0017;
    const project3 = (p: MissionPoint): ScreenPoint => {
      const x0 = (mapX(p.x) - worldCenterMap.x) * scaleX;
      const y0 = -(mapY(p.y) - worldCenterMap.y) * scaleY;
      const z0 = (zOf(p.z) - worldCenter.z) * zMetersToWorld * scaleZ;
      const x1 = x0 * cosY - y0 * sinY;
      const y1 = x0 * sinY + y0 * cosY;
      const z1 = z0;
      const x2 = x1;
      const y2 = y1 * cosP - z1 * sinP;
      const z2 = y1 * sinP + z1 * cosP;
      const k = 1 + (z2 + cameraDepth) * perspective;
      return { x: cx + x2 * k, y: cy - y2 * k, depth: z2 };
    };
    return { project3 };
  }, [height, mapSpanX, mapSpanY, pad, pitchDeg, worldCenter.z, worldCenterMap.x, worldCenterMap.y, width, yawDeg, zMetersToWorld, zoomScale]);

  const project2 = (p: MissionPoint): ScreenPoint => {
    const x = sx(p.x);
    const y = sy(p.y);
    const cx = width * 0.5;
    const cy = height * 0.5;
    return { x: cx + (x - cx) * zoomScale + panOffset.x, y: cy + (y - cy) * zoomScale + panOffset.y, depth: 0 };
  };
  const radiusPxForMeters = (cx: number, cy: number, rMeters: number): number => {
    const center = project2({ x: cx, y: cy, z: 0 });
    const east = coordinateContext.mode === "geo" ? geoOffsetFromMeters(cx, cy, rMeters, 0) : { x: cx + rMeters, y: cy };
    const north = coordinateContext.mode === "geo" ? geoOffsetFromMeters(cx, cy, rMeters, Math.PI / 2) : { x: cx, y: cy + rMeters };
    const eastPx = project2({ x: east.x, y: east.y, z: 0 });
    const northPx = project2({ x: north.x, y: north.y, z: 0 });
    const rEast = Math.hypot(eastPx.x - center.x, eastPx.y - center.y);
    const rNorth = Math.hypot(northPx.x - center.x, northPx.y - center.y);
    return Math.max(2, (rEast + rNorth) / 2);
  };
  const project = (p: MissionPoint): ScreenPoint => (viewMode === "2d" ? project2(p) : projection.project3(p));
  const projectTrack = (p: MissionTrack): ScreenPoint => project({ x: p.x, y: p.y, z: p.z });

  const routePath2d = route.map((p) => {
    const q = project2(p);
    return `${q.x},${q.y}`;
  }).join(" ");
  const routePathProjected = route.map((p) => {
    const q = project(p);
    return `${q.x},${q.y}`;
  }).join(" ");

  const axis = useMemo(() => {
    if (viewMode !== "3d") return null;
    const yaw = (yawDeg * Math.PI) / 180;
    const pitch = (pitchDeg * Math.PI) / 180;
    const cosY = Math.cos(yaw);
    const sinY = Math.sin(yaw);
    const cosP = Math.cos(pitch);
    const sinP = Math.sin(pitch);
    const origin = { x: 56, y: height - 42 };
    const axisLen = 34;
    const projectAxisVec = (vx: number, vy: number, vz: number) => {
      const x1 = vx * cosY - vy * sinY;
      const y1 = vx * sinY + vy * cosY;
      const z1 = vz;
      const x2 = x1;
      const y2 = y1 * cosP - z1 * sinP;
      const z2 = y1 * sinP + z1 * cosP;
      const mag = Math.hypot(x2, y2, z2) || 1;
      return { x: origin.x + (x2 / mag) * axisLen, y: origin.y - (y2 / mag) * axisLen, depth: z2 };
    };
    const xEnd = projectAxisVec(1, 0, 0);
    const yEnd = projectAxisVec(0, 1, 0);
    const zEnd = projectAxisVec(0, 0, 1);
    return { origin, xEnd, yEnd, zEnd };
  }, [height, pitchDeg, viewMode, yawDeg]);

  const pointerCursor = dragRef.current.active ? "grabbing" : "grab";

  const stepZoom = (dir: -1 | 1) => {
    setZoomScale((v) => clamp(Number((v * (dir > 0 ? 1.15 : 1 / 1.15)).toFixed(3)), 0.45, 4));
  };

  const handleWheelZoom = (e: React.WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const py = ((e.clientY - rect.top) / rect.height) * height;
    const cx = width * 0.5;
    const cy = height * 0.5;
    const nextZoom = clamp(Number((zoomScale * (e.deltaY < 0 ? 1.12 : 1 / 1.12)).toFixed(3)), 0.45, 4);
    if (nextZoom === zoomScale) return;
    const ratio = nextZoom / zoomScale;
    setZoomScale(nextZoom);
    setPanOffset((prev) => ({
      x: Number((px - cx - (px - cx - prev.x) * ratio).toFixed(3)),
      y: Number((py - cy - (py - cy - prev.y) * ratio).toFixed(3)),
    }));
  };

  const handleMapClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (viewMode === "3d") return;
    if (!clickable) return;
    if (dragRef.current.moved) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const py = ((e.clientY - rect.top) / rect.height) * height;
    const cx = width * 0.5;
    const cy = height * 0.5;
    const unzoomPx = cx + (px - cx - panOffset.x) / zoomScale;
    const unzoomPy = cy + (py - cy - panOffset.y) / zoomScale;
    const x = Math.max(world.minX, Math.min(world.maxX, wx(unzoomPx)));
    const y = Math.max(world.minY, Math.min(world.maxY, wy(unzoomPy)));
    const point = { x: Number(x.toFixed(6)), y: Number(y.toFixed(6)), z: 40 };
    if (e.shiftKey && onAddNoFlyZoneCenter) {
      onAddNoFlyZoneCenter(point);
      return;
    }
    if (onAddWaypoint) onAddWaypoint(point);
  };

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#101828" }}>{title}</div>
          <div style={{ fontSize: 11, color: "#667085" }}>
            {viewMode === "3d" ? "3D map with mouse orbit (X/Y/Z axes shown). Drag to rotate view." : `2D top-down map for precise waypoint/NFZ editing (${coordinateContext.mode === "geo" ? "Lon/Lat" : "X/Y"} plane). Drag to pan and scroll to zoom.`}{" "}
            {` Zoom ${Math.round(zoomScale * 100)}%.`}{" "}
            {clickable && viewMode === "2d" ? "Click map to add waypoint." : ""}
            {clickable && viewMode === "2d" && onAddNoFlyZoneCenter ? " Shift+click to add a no-fly-zone center." : ""}
            {clickable && viewMode === "3d" ? " Switch to 2D to add waypoint/NFZ center." : ""}
          </div>
        </div>
      </div>
      <div style={{ position: "relative", width: "100%" }}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", borderRadius: 12, border: "1px solid #e4e7ec", background: "#f9fbff", cursor: pointerCursor }}
        onMouseDown={(e) => {
          dragRef.current = {
            active: true,
            moved: false,
            x: e.clientX,
            y: e.clientY,
            mode: viewMode === "3d" ? "orbit" : "pan",
          };
        }}
        onMouseMove={(e) => {
          if (!dragRef.current.active) return;
          const dx = e.clientX - dragRef.current.x;
          const dy = e.clientY - dragRef.current.y;
          if (Math.abs(dx) + Math.abs(dy) > 2) dragRef.current.moved = true;
          dragRef.current.x = e.clientX;
          dragRef.current.y = e.clientY;
          if (dragRef.current.mode === "orbit" && viewMode === "3d") {
            setYawDeg((v) => v + dx * 0.35);
            setPitchDeg((v) => clamp(v - dy * 0.25, -10, 80));
            return;
          }
          if (dragRef.current.mode === "pan" && viewMode === "2d") {
            setPanOffset((prev) => ({
              x: clamp(prev.x + dx, -width * 1.4, width * 1.4),
              y: clamp(prev.y + dy, -height * 1.4, height * 1.4),
            }));
          }
        }}
        onMouseUp={() => { dragRef.current.active = false; dragRef.current.mode = null; }}
        onMouseLeave={() => { dragRef.current.active = false; dragRef.current.mode = null; }}
        onWheel={handleWheelZoom}
        onClick={handleMapClick}
      >
        <defs>
          <pattern id="sync-map-grid" width="24" height="24" patternUnits="userSpaceOnUse">
            <rect width="24" height="24" fill="#f9fbff" />
            <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#e5edff" strokeWidth="1" />
          </pattern>
        </defs>
        <rect x={0} y={0} width={width} height={height} fill="url(#sync-map-grid)" />

        {viewMode === "2d" ? (
          <g opacity={0.5}>
            <text x={width - 86} y={height - 40} fontSize="10" fill="#475467">2D top view</text>
            <text x={width - 106} y={height - 26} fontSize="10" fill="#667085">{coordinateContext.mode === "geo" ? "Plane: Lon/Lat" : "Plane: X/Y"}</text>
          </g>
        ) : axis ? (
          <g opacity={0.9}>
            <line x1={axis.origin.x} y1={axis.origin.y} x2={axis.xEnd.x} y2={axis.xEnd.y} stroke="#ef4444" strokeWidth="2" />
            <line x1={axis.origin.x} y1={axis.origin.y} x2={axis.yEnd.x} y2={axis.yEnd.y} stroke="#2563eb" strokeWidth="2" />
            <line x1={axis.origin.x} y1={axis.origin.y} x2={axis.zEnd.x} y2={axis.zEnd.y} stroke="#16a34a" strokeWidth="2" />
            <circle cx={axis.origin.x} cy={axis.origin.y} r={2.8} fill="#111827" />
            <text x={axis.xEnd.x + 4} y={axis.xEnd.y + 2} fontSize="10" fill="#b42318">{coordinateContext.mode === "geo" ? "Lon" : "X"}</text>
            <text x={axis.yEnd.x + 4} y={axis.yEnd.y + 2} fontSize="10" fill="#1d4ed8">{coordinateContext.mode === "geo" ? "Lat" : "Y"}</text>
            <text x={axis.zEnd.x + 4} y={axis.zEnd.y + 2} fontSize="10" fill="#15803d">Z</text>
            <text x={12} y={height - 18} fontSize="10" fill="#667085">Z starts at 0 • drag mouse to rotate</text>
          </g>
        ) : null}

        {showCoverage ? baseStations.map((bs) => {
          const rWorld = coverageMap.get(bs.id);
          if (!rWorld) return null;
          const c = coverageColor(bs.status);
          if (viewMode === "2d") {
            const rPx = radiusPxForMeters(bs.x, bs.y, rWorld);
            const c2 = project2({ x: bs.x, y: bs.y, z: 0 });
            return <circle key={`${bs.id}-cov`} cx={c2.x} cy={c2.y} r={rPx} fill={c.fill} stroke={c.stroke} />;
          }
          const ringPts: ScreenPoint[] = circleWorldPoints(bs.x, bs.y, rWorld, coordinateContext.mode, 40).map((pt) => project({ x: pt.x, y: pt.y, z: 0 }));
          return <polyline key={`${bs.id}-cov`} points={projectRingPath(ringPts)} fill="none" stroke={c.stroke} strokeWidth="1.5" />;
        }) : null}

        {polygonOverlays.map((poly, idx) => {
          const rings = Array.isArray(poly.rings) ? poly.rings.filter((ring) => Array.isArray(ring) && ring.length >= 3) : [];
          if (!rings.length) return null;
          const outline = poly.color || "#155eef";
          const fill = poly.fill || "rgba(21,94,239,0.14)";
          const labelPoint = rings[0]?.[0] ? project(rings[0][0]!) : null;
          return (
            <g key={`poly-${poly.id}-${idx}`}>
              {rings.map((ring, ridx) => {
                const points = ring.map((p) => {
                  const q = project({ x: p.x, y: p.y, z: p.z ?? 0 });
                  return `${q.x},${q.y}`;
                }).join(" ");
                return (
                  <polygon
                    key={`poly-${poly.id}-${idx}-${ridx}`}
                    points={points}
                    fill={fill}
                    stroke={outline}
                    strokeWidth={ridx === 0 ? 1.7 : 1.2}
                    strokeDasharray={ridx === 0 ? undefined : "4 3"}
                    fillOpacity={ridx === 0 ? 1 : 0.4}
                  />
                );
              })}
              {poly.label && labelPoint ? <text x={labelPoint.x + 6} y={labelPoint.y - 6} fontSize="10" fill={outline}>{poly.label}</text> : null}
            </g>
          );
        })}

        {noFlyZones.map((z, i) => {
          const zMinM = Number(z.z_min ?? 0);
          const zMaxM = Number(z.z_max ?? 120);
          const zMin = zOf(zMinM);
          const zMax = zOf(zMaxM);
          const halfSizeM = Math.max(1, Number(z.radius_m ?? 0));
          const shape = nfzShape(z);
          const footprint = shape === "box" ? squareWorldCorners(z.cx, z.cy, halfSizeM, coordinateContext.mode) : [];
          const circleRingWorld = shape === "circle" ? circleWorldPoints(z.cx, z.cy, halfSizeM, coordinateContext.mode, 36) : [];
          if (viewMode === "2d") {
            if (shape === "circle") {
              const rPx = radiusPxForMeters(z.cx, z.cy, halfSizeM);
              const c2 = project2({ x: z.cx, y: z.cy, z: 0 });
              return (
                <g key={`${z.zone_id ?? "nfz"}-${i}`}>
                  <circle cx={c2.x} cy={c2.y} r={Math.max(6, rPx)} fill="rgba(240,68,56,0.08)" stroke="#f04438" strokeDasharray="4 3" />
                  <text x={c2.x + 8} y={c2.y - 8} fontSize="10" fill="#b42318">{`${z.zone_id ?? "NFZ"} R${Math.round(halfSizeM)}m (${Math.round(zMinM)}-${Math.round(zMaxM)}m)`}</text>
                </g>
              );
            }
            const footprint2d = footprint.map((pt) => project2({ x: pt.x, y: pt.y, z: 0 }));
            const labelPt = footprint2d[1] ?? project2({ x: z.cx, y: z.cy, z: 0 });
            return (
              <g key={`${z.zone_id ?? "nfz"}-${i}`}>
                <polygon points={footprint2d.map((p) => `${p.x},${p.y}`).join(" ")} fill="rgba(240,68,56,0.08)" stroke="#f04438" strokeDasharray="4 3" />
                <text x={labelPt.x + 8} y={labelPt.y - 8} fontSize="10" fill="#b42318">{`${z.zone_id ?? "NFZ"} box R${Math.round(halfSizeM)}m (${Math.round(zMinM)}-${Math.round(zMaxM)}m)`}</text>
              </g>
            );
          }
          if (shape === "circle") {
            const bottomRing: ScreenPoint[] = circleRingWorld.map((pt) => project({ x: pt.x, y: pt.y, z: zMin }));
            const topRing: ScreenPoint[] = circleRingWorld.map((pt) => project({ x: pt.x, y: pt.y, z: zMax }));
            const sideAngles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
            return (
              <g key={`${z.zone_id ?? "nfz"}-${i}`}>
                {sideAngles.map((a, si) => {
                  const pt = worldOffsetMeters(z.cx, z.cy, Math.cos(a) * halfSizeM, Math.sin(a) * halfSizeM, coordinateContext.mode);
                  const b = project({ x: pt.x, y: pt.y, z: zMin });
                  const t = project({ x: pt.x, y: pt.y, z: zMax });
                  return <line key={`side-${si}`} x1={b.x} y1={b.y} x2={t.x} y2={t.y} stroke="rgba(240,68,56,0.65)" strokeWidth="1" />;
                })}
                <polyline points={projectRingPath(bottomRing)} fill="none" stroke="rgba(249,112,102,0.7)" strokeDasharray="4 3" />
                <polyline points={projectRingPath(topRing)} fill="rgba(240,68,56,0.08)" stroke="#f04438" strokeDasharray="4 3" />
                {(() => {
                  const edgePt = worldOffsetMeters(z.cx, z.cy, halfSizeM, 0, coordinateContext.mode);
                  const labelPt = project({ x: edgePt.x, y: edgePt.y, z: zMax });
                  return <text x={labelPt.x + 8} y={labelPt.y - 6} fontSize="10" fill="#b42318">{`${z.zone_id ?? "NFZ"} R${Math.round(halfSizeM)}m (${Math.round(zMinM)}-${Math.round(zMaxM)}m)`}</text>;
                })()}
              </g>
            );
          }
          const bottomCorners: ScreenPoint[] = footprint.map((pt) => project({ x: pt.x, y: pt.y, z: zMin }));
          const topCorners: ScreenPoint[] = footprint.map((pt) => project({ x: pt.x, y: pt.y, z: zMax }));
          const bottomClosed: ScreenPoint[] = [...bottomCorners, bottomCorners[0]!];
          const topClosed: ScreenPoint[] = [...topCorners, topCorners[0]!];
          const sideFaces = footprint
            .map((_, idx) => {
              const next = (idx + 1) % footprint.length;
              const b0 = bottomCorners[idx]!;
              const b1 = bottomCorners[next]!;
              const t1 = topCorners[next]!;
              const t0 = topCorners[idx]!;
              const avgDepth = (b0.depth + b1.depth + t1.depth + t0.depth) / 4;
              return { idx, points: [b0, b1, t1, t0], avgDepth };
            })
            .sort((a, b) => a.avgDepth - b.avgDepth);
          return (
            <g key={`${z.zone_id ?? "nfz"}-${i}`}>
              {sideFaces.map((face) => (
                <polygon
                  key={`nfz-face-${face.idx}`}
                  points={face.points.map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="rgba(240,68,56,0.12)"
                  stroke="rgba(240,68,56,0.45)"
                  strokeWidth="1"
                />
              ))}
              <polyline points={projectRingPath(bottomClosed)} fill="none" stroke="rgba(249,112,102,0.7)" strokeDasharray="4 3" />
              <polygon points={topCorners.map((p) => `${p.x},${p.y}`).join(" ")} fill="rgba(240,68,56,0.10)" stroke="#f04438" />
              <polyline points={projectRingPath(topClosed)} fill="none" stroke="#f04438" />
              {(() => {
                const labelCorner = footprint[1] ?? { x: z.cx, y: z.cy };
                const labelPt = project({ x: labelCorner.x, y: labelCorner.y, z: zMax });
                return <text x={labelPt.x + 8} y={labelPt.y - 6} fontSize="10" fill="#b42318">{`${z.zone_id ?? "NFZ"} box R${Math.round(halfSizeM)}m (${Math.round(zMinM)}-${Math.round(zMaxM)}m)`}</text>;
              })()}
            </g>
          );
        })}

        {route.length > 0 && viewMode === "2d" ? <polyline points={routePath2d} fill="none" stroke="#2563eb" strokeWidth="2.4" /> : null}
        {route.length > 0 && viewMode === "3d" ? <><polyline points={route.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")} fill="none" stroke="rgba(37,99,235,0.15)" strokeWidth="1.4" strokeDasharray="4 4" /><polyline points={routePathProjected} fill="none" stroke="#2563eb" strokeWidth="2.4" /></> : null}
        {routeOverlays.map((ov, idx) => {
          if (!Array.isArray(ov.route) || ov.route.length < 2) return null;
          const overlayId = String(ov.id || `route-${idx + 1}`);
          const style = overlayRouteStyle(overlayId, ov.color, idx);
          const overlayPts2 = ov.route.map((p) => project2(p));
          const overlayPts3 = ov.route.map((p) => project(p));
          const pts2 = overlayPts2.map((q) => `${q.x},${q.y}`).join(" ");
          const pts3 = overlayPts3.map((q) => `${q.x},${q.y}`).join(" ");
          const color = style.color;
          const label2 = overlayPts2[overlayPts2.length - 1];
          const label3 = overlayPts3[overlayPts3.length - 1];
          return (
            <g key={`ov-${overlayId}-${idx}`}>
              {viewMode === "2d" ? <polyline points={pts2} fill="none" stroke={color} strokeWidth={style.width2d} strokeDasharray={style.svgDash} opacity={style.opacity} /> : null}
              {viewMode === "3d" ? <polyline points={pts3} fill="none" stroke={color} strokeWidth={style.width3d} strokeDasharray={style.svgDash} opacity={style.opacity} /> : null}
              {(viewMode === "2d" ? overlayPts2 : overlayPts3).map((q, pi) => (
                <g key={`ov-pt-${overlayId}-${idx}-${pi}`}>
                  <circle cx={q.x} cy={q.y} r={style.pointRadius} fill="#fff" stroke={color} strokeWidth="1.1" opacity={0.95} />
                </g>
              ))}
              {viewMode === "2d" && label2 ? (
                <text x={label2.x + 6} y={label2.y + 12} fontSize="10" fill={color}>{`${compactOverlayLabel(overlayId)} • WP${Math.max(0, ov.route.length - 1)}`}</text>
              ) : null}
              {viewMode === "3d" && label3 ? (
                <text x={label3.x + 6} y={label3.y + 12} fontSize="10" fill={color}>{`${compactOverlayLabel(overlayId)} • WP${Math.max(0, ov.route.length - 1)}`}</text>
              ) : null}
            </g>
          );
        })}

        {route.map((p, i) => {
          const q = project(p);
          const g = project2({ x: p.x, y: p.y, z: 0 });
          return (
            <g key={`wp-${i}`}>
              {viewMode === "3d" && i > 0 ? <line x1={g.x} y1={g.y} x2={q.x} y2={q.y} stroke="#cbd5e1" strokeDasharray="3 3" /> : null}
              {viewMode === "3d" && i > 0 ? <circle cx={g.x} cy={g.y} r={2.5} fill="#dbeafe" stroke="#93c5fd" /> : null}
              <circle cx={q.x} cy={q.y} r={3.8} fill={i === 0 ? "#16a34a" : "#1d4ed8"} />
              <text x={q.x + 5} y={q.y - 5} fontSize="10" fill="#1f2937">
                {viewMode === "3d"
                  ? `${i === 0 ? "HM" : `WP${i}`} • ${roundMeters(zOf(p.z))}m`
                  : (i === 0 ? "HM" : `WP${i}`)}
              </text>
            </g>
          );
        })}

        {baseStations.map((bs) => { const q = project({ x: bs.x, y: bs.y, z: 0 }); return <g key={bs.id}><circle cx={q.x} cy={q.y} r={7} fill="#fff" stroke={statusColor(bs.status)} strokeWidth={2} /><text x={q.x + 9} y={q.y - 8} fontSize="10" fill="#101828">{bs.id}</text></g>; })}

        {plannedPosition ? (() => {
          const q = project(plannedPosition);
          const g = project2({ x: plannedPosition.x, y: plannedPosition.y, z: 0 });
          return (
            <g>
              {viewMode === "3d" ? <line x1={g.x} y1={g.y} x2={q.x} y2={q.y} stroke="#fcd34d" strokeDasharray="3 3" /> : null}
              <circle cx={q.x} cy={q.y} r={6} fill="#f59e0b" stroke="#92400e" strokeWidth="1.4" />
              <text x={q.x + 8} y={q.y + 14} fontSize="10" fill="#92400e">{viewMode === "3d" ? `planned • ${roundMeters(zOf(plannedPosition.z))}m` : "planned"}</text>
            </g>
          );
        })() : null}

        {trackedPositions.map((t) => {
          const q = projectTrack(t);
          const isSelected = t.id === selectedUavId;
          const muted = focusSelectedTrack && !isSelected;
          const fill = trackMarkerFill(isSelected, muted);
          const stroke = muted ? "#667085" : "#1f2937";
          const headingDeg = normalizeHeadingDeg(t.headingDeg);
          const headingRad = (headingDeg * Math.PI) / 180;
          const noseX = q.x + Math.cos(headingRad) * (isSelected ? 18 : 14);
          const noseY = q.y + Math.sin(headingRad) * (isSelected ? 18 : 14);
          const speedText = typeof t.speedMps === "number" ? ` • ${Math.round(t.speedMps)}m/s` : "";
          const label = viewMode === "3d" ? `${t.id} • ${roundMeters(zOf(t.z))}m${speedText}` : `${t.id}${speedText}`;
          return (
            <g key={`trk-${t.id}`}>
              {trackMarkerStyle === "uav" ? (
                <>
                  <line x1={q.x} y1={q.y} x2={noseX} y2={noseY} stroke={stroke} strokeWidth={isSelected ? 2 : 1.5} />
                  <g transform={`translate(${q.x} ${q.y}) rotate(${headingDeg})`}>
                    <line x1={-7} y1={0} x2={7} y2={0} stroke={stroke} strokeWidth={1.1} />
                    <line x1={0} y1={-7} x2={0} y2={7} stroke={stroke} strokeWidth={1.1} />
                    <circle cx={-7} cy={0} r={1.6} fill="#fff" stroke={stroke} strokeWidth={0.9} />
                    <circle cx={7} cy={0} r={1.6} fill="#fff" stroke={stroke} strokeWidth={0.9} />
                    <circle cx={0} cy={-7} r={1.6} fill="#fff" stroke={stroke} strokeWidth={0.9} />
                    <circle cx={0} cy={7} r={1.6} fill="#fff" stroke={stroke} strokeWidth={0.9} />
                    <polygon
                      points={isSelected ? "8,0 -2.4,-4.1 -1,0 -2.4,4.1" : "6.6,0 -2,-3.4 -0.8,0 -2,3.4"}
                      fill={fill}
                      stroke={stroke}
                      strokeWidth={1}
                    />
                    <circle cx={0} cy={0} r={2.1} fill="#fff" stroke={stroke} strokeWidth={0.9} />
                  </g>
                  <circle cx={q.x} cy={q.y} r={isSelected ? 16 : 12} fill="none" stroke={riskColor(t.interferenceRisk)} strokeOpacity={0.72} strokeDasharray={isSelected ? "5 4" : "4 4"} />
                  {isSelected ? <circle cx={q.x} cy={q.y} r={22} fill="none" stroke={riskColor(t.interferenceRisk)} strokeOpacity={0.45} strokeDasharray="3 6" /> : null}
                </>
              ) : (
                <>
                  <circle cx={q.x} cy={q.y} r={isSelected ? 6 : 4.8} fill={fill} stroke={stroke} strokeWidth="1" />
                  {isSelected ? <circle cx={q.x} cy={q.y} r={16} fill="none" stroke={riskColor(t.interferenceRisk)} strokeDasharray="4 4" /> : null}
                </>
              )}
              <text x={q.x + 8} y={q.y + 13} fontSize="10" fill={muted ? "#667085" : "#111827"}>{label}</text>
            </g>
          );
        })}

        {plannedPosition && selectedTrack ? (() => {
          const p = project(plannedPosition);
          const t = projectTrack(selectedTrack);
          const dx = t.x - p.x;
          const dy = t.y - p.y;
          const len = Math.hypot(dx, dy) || 1;
          const nx = -dy / len;
          const ny = dx / len;
          const bend = Math.min(18, Math.max(8, len * 0.12));
          const cx = (p.x + t.x) / 2 + nx * bend;
          const cy = (p.y + t.y) / 2 + ny * bend;
          const curvedPath = `M ${p.x} ${p.y} Q ${cx} ${cy} ${t.x} ${t.y}`;
          const midLabelX = (p.x + 2 * cx + t.x) / 4;
          const midLabelY = (p.y + 2 * cy + t.y) / 4;
          return (
            <g>
              <path d={curvedPath} fill="none" stroke="#be185d" strokeWidth={1.9} strokeDasharray="3 5" opacity={0.92} />
              {showInterferenceHints ? <text x={midLabelX + 6} y={midLabelY - 4} fontSize="10" fill="#9d174d">HM link</text> : null}
            </g>
          );
        })() : null}
      </svg>
      <div style={{ position: "absolute", top: 10, right: 10, display: "grid", gap: 6, zIndex: 3 }}>
        <button
          type="button"
          aria-label="Recenter to current UAV position"
          onClick={() => onRecenterCurrent?.()}
          disabled={!canRecenterCurrent}
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            border: "1px solid #d0d5dd",
            background: canRecenterCurrent ? "#fff" : "#f2f4f7",
            color: canRecenterCurrent ? "#344054" : "#98a2b3",
            fontSize: 9,
            lineHeight: "10px",
            fontWeight: 700,
            letterSpacing: 0.2,
            cursor: canRecenterCurrent ? "pointer" : "not-allowed",
            boxShadow: "0 1px 2px rgba(16,24,40,0.12)",
          }}
        >
          LOC
        </button>
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => stepZoom(1)}
          style={{ width: 32, height: 32, borderRadius: 8, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", fontSize: 18, lineHeight: "18px", fontWeight: 700, cursor: "pointer", boxShadow: "0 1px 2px rgba(16,24,40,0.12)" }}
        >
          +
        </button>
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => stepZoom(-1)}
          style={{ width: 32, height: 32, borderRadius: 8, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", fontSize: 18, lineHeight: "18px", fontWeight: 700, cursor: "pointer", boxShadow: "0 1px 2px rgba(16,24,40,0.12)" }}
        >
          -
        </button>
      </div>
    </div>
    </div>
  );
}

export function MissionSyncMap(props: Props): React.ReactNode {
  const { state, error } = useCesiumLoader();
  const [svgResetKey, setSvgResetKey] = useState(0);
  const [svgZoomResetKey, setSvgZoomResetKey] = useState(0);
  const [cesiumResetViewSeq, setCesiumResetViewSeq] = useState(0);
  const [centerGpsSeq, setCenterGpsSeq] = useState(0);
  const [liveGpsEnabled, setLiveGpsEnabled] = useState<boolean>(props.enableLiveGps !== false);
  const [liveGpsPoint, setLiveGpsPoint] = useState<LiveGpsPoint | null>(null);
  const [liveGpsError, setLiveGpsError] = useState<string>("");
  const [mapServiceConfig, setMapServiceConfig] = useState<MapServiceConfig | null>(null);
  const [mapServiceStatus, setMapServiceStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [mapPrefetchStatus, setMapPrefetchStatus] = useState<"idle" | "running" | "ready" | "error">("idle");
  const [mapSyncState, setMapSyncState] = useState<MapSyncState | null>(null);
  const [mapSyncStatus, setMapSyncStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const mapSyncRevisionRef = useRef<number | null>(null);
  const mapSyncDigestRef = useRef<string>("");
  const mapServiceBase = useMemo(
    () => normalizeBaseUrl(props.mapServiceBase || readViteEnv("VITE_MAP_SERVICE_BASE")),
    [props.mapServiceBase]
  );
  const mapSyncScope = useMemo(() => String(props.syncScope || "shared").trim() || "shared", [props.syncScope]);
  const mapSyncIncludeShared = props.syncIncludeShared !== false;
  const mapSyncEnabled = props.syncEnabled !== false && Boolean(mapServiceBase);
  const [mapRenderMode, setMapRenderMode] = useState<MapRenderMode>("svg2d");
  const [highContrastRoute, setHighContrastRoute] = useState(true);
  const basemapChoice: CesiumBasemapChoice = "osm";
  const nlsApiKey = useMemo(() => readViteEnv("VITE_NLS_API_KEY"), []);
  const canUseCesium = state === "ready";
  const effectiveRenderMode: MapRenderMode = mapRenderMode === "cesium3d" && !canUseCesium ? "svg3d" : mapRenderMode;
  const usingCesium = effectiveRenderMode === "cesium3d" && canUseCesium;
  const svgViewMode: ViewMode = effectiveRenderMode === "svg3d" ? "3d" : "2d";
  const cesiumViewMode: ViewMode = "3d";
  const syncTracks = useMemo<MissionTrack[]>(
    () =>
      (mapSyncState?.layers.uavs ?? []).map((row) => ({
        id: String(row.id),
        x: Number(row.x ?? 0),
        y: Number(row.y ?? 0),
        z: Number(row.z ?? 0),
        headingDeg: Number(row.headingDeg ?? 0),
        speedMps: Number(row.speedMps ?? 0),
        attachedBsId: String(row.attachedBsId ?? ""),
        interferenceRisk: String(row.interferenceRisk ?? "low") as "low" | "medium" | "high",
      })),
    [mapSyncState],
  );
  const syncNoFlyZones = useMemo<MissionNfz[]>(
    () =>
      (mapSyncState?.layers.noFlyZones ?? []).map((row) => ({
        zone_id: String(row.zone_id ?? ""),
        cx: Number(row.cx ?? 0),
        cy: Number(row.cy ?? 0),
        radius_m: Number(row.radius_m ?? 0),
        z_min: Number(row.z_min ?? 0),
        z_max: Number(row.z_max ?? 120),
        reason: String(row.reason ?? ""),
        shape: String(row.shape ?? "circle") === "box" ? "box" : "circle",
      })),
    [mapSyncState],
  );
  const syncBaseStations = useMemo<MissionBs[]>(
    () =>
      (mapSyncState?.layers.baseStations ?? []).map((row) => ({
        id: String(row.id),
        x: Number(row.x ?? 0),
        y: Number(row.y ?? 0),
        status: String(row.status ?? "online"),
      })),
    [mapSyncState],
  );
  const syncCoverage = useMemo<MissionCoverage[]>(
    () =>
      (mapSyncState?.layers.coverage ?? []).map((row) => ({
        bsId: String(row.bsId ?? ""),
        radiusM: Number(row.radiusM ?? 0),
      })),
    [mapSyncState],
  );
  const syncRouteOverlays = useMemo<MissionRouteOverlay[]>(
    () =>
      (mapSyncState?.layers.paths ?? []).map((row) => ({
        id: String(row.id),
        route: Array.isArray(row.route)
          ? row.route.map((p) => ({
              x: Number(p.x ?? 0),
              y: Number(p.y ?? 0),
              z: Number(p.z ?? 0),
            }))
          : [],
        color: row.color ? String(row.color) : "#94a3b8",
      })),
    [mapSyncState],
  );
  const mergedTrackedBase = useMemo(
    () => mergeById(syncTracks, Array.isArray(props.trackedPositions) ? props.trackedPositions : []),
    [props.trackedPositions, syncTracks],
  );
  const mergedNoFlyZones = useMemo(
    () => mergeNoFlyZones(syncNoFlyZones, Array.isArray(props.noFlyZones) ? props.noFlyZones : []),
    [props.noFlyZones, syncNoFlyZones],
  );
  const mergedBaseStations = useMemo(
    () => mergeById(syncBaseStations, Array.isArray(props.baseStations) ? props.baseStations : []),
    [props.baseStations, syncBaseStations],
  );
  const mergedCoverage = useMemo(
    () => mergeCoverageRows(syncCoverage, Array.isArray(props.coverage) ? props.coverage : []),
    [props.coverage, syncCoverage],
  );
  const mergedRouteOverlays = useMemo(
    () => mergeById(syncRouteOverlays, Array.isArray(props.routeOverlays) ? props.routeOverlays : []),
    [props.routeOverlays, syncRouteOverlays],
  );
  const mapModeHint = useMemo<CoordinateMode>(
    () =>
      decideCoordinateMode({
        ...props,
        trackedPositions: mergedTrackedBase,
        noFlyZones: mergedNoFlyZones,
        baseStations: mergedBaseStations,
        coverage: mergedCoverage,
        routeOverlays: mergedRouteOverlays,
      }),
    [
      props.coordinateMode,
      props.plannedPosition,
      props.polygonOverlays,
      props.route,
      mergedBaseStations,
      mergedCoverage,
      mergedNoFlyZones,
      mergedRouteOverlays,
      mergedTrackedBase,
    ],
  );
  const liveGpsWorldPoint = useMemo<MissionPoint | null>(() => {
    if (!liveGpsEnabled || !liveGpsPoint) return null;
    if (mapModeHint === "geo") {
      return {
        x: Number(liveGpsPoint.lon),
        y: Number(liveGpsPoint.lat),
        z: Number(liveGpsPoint.altM),
      };
    }
    return { x: 0, y: 0, z: Number(liveGpsPoint.altM) };
  }, [liveGpsEnabled, liveGpsPoint, mapModeHint]);
  const mapRoute = useMemo<MissionPoint[]>(
    () => {
      const rows = Array.isArray(props.route) ? props.route.slice() : [];
      if (!liveGpsWorldPoint) return rows;
      if (!rows.length) {
        return [{
          x: Number(liveGpsWorldPoint.x),
          y: Number(liveGpsWorldPoint.y),
          z: Number(liveGpsWorldPoint.z ?? 40),
        }];
      }
      const hm = rows[0]!;
      rows[0] = {
        ...hm,
        x: Number(liveGpsWorldPoint.x),
        y: Number(liveGpsWorldPoint.y),
        z: Number(hm.z ?? liveGpsWorldPoint.z ?? 0),
      };
      return rows;
    },
    [liveGpsWorldPoint, props.route],
  );
  const mapPlannedPosition = useMemo<MissionPoint | null | undefined>(() => {
    if (!liveGpsWorldPoint) return props.plannedPosition;
    if (!props.plannedPosition) {
      if (mapModeHint === "geo") {
        const near = lonLatOffsetMeters(
          Number(liveGpsWorldPoint.x),
          Number(liveGpsWorldPoint.y),
          24,
          16,
        );
        return {
          x: Number(near.x),
          y: Number(near.y),
          z: Number(liveGpsWorldPoint.z ?? 40),
        };
      }
      return {
        x: Number(liveGpsWorldPoint.x) + 24,
        y: Number(liveGpsWorldPoint.y) + 16,
        z: Number(liveGpsWorldPoint.z ?? 40),
      };
    }
    const hmOriginal = Array.isArray(props.route) && props.route.length > 0 ? props.route[0] : null;
    const hmCurrent = mapRoute.length > 0 ? mapRoute[0] : null;
    const tiedToHmOriginal = Boolean(hmOriginal)
      && Math.abs(Number(props.plannedPosition.x) - Number(hmOriginal!.x)) < 1e-9
      && Math.abs(Number(props.plannedPosition.y) - Number(hmOriginal!.y)) < 1e-9;
    const tiedToHmCurrent = Boolean(hmCurrent)
      && Math.abs(Number(props.plannedPosition.x) - Number(hmCurrent!.x)) < 1e-9
      && Math.abs(Number(props.plannedPosition.y) - Number(hmCurrent!.y)) < 1e-9;
    if (!tiedToHmOriginal && !tiedToHmCurrent) return props.plannedPosition;
    return {
      ...props.plannedPosition,
      x: Number(liveGpsWorldPoint.x),
      y: Number(liveGpsWorldPoint.y),
      z: Number(props.plannedPosition.z ?? liveGpsWorldPoint.z ?? 0),
    };
  }, [liveGpsWorldPoint, mapModeHint, mapRoute, props.plannedPosition, props.route]);
  const mapTrackedPositions = useMemo<MissionTrack[]>(() => {
    const rows = mergedTrackedBase.filter((r) => String(r.id || "").trim() !== "gps-live");
    if (!liveGpsWorldPoint) return rows;
    const selectedIdx = props.selectedUavId ? rows.findIndex((r) => r.id === props.selectedUavId) : -1;
    const hmIdx = rows.findIndex((r) => String(r.id || "").trim().toLowerCase() === "hm");
    const targetIdx = selectedIdx >= 0 ? selectedIdx : hmIdx >= 0 ? hmIdx : rows.length > 0 ? 0 : -1;
    if (targetIdx >= 0) {
      const current = rows[targetIdx]!;
      rows[targetIdx] = {
        ...current,
        x: Number(liveGpsWorldPoint.x),
        y: Number(liveGpsWorldPoint.y),
        z: Number(liveGpsWorldPoint.z ?? current.z ?? 0),
      };
      return rows;
    }
    rows.push({
      id: props.selectedUavId || "HM",
      x: Number(liveGpsWorldPoint.x),
      y: Number(liveGpsWorldPoint.y),
      z: Number(liveGpsWorldPoint.z ?? 0),
      attachedBsId: "GNSS",
      interferenceRisk: "low",
    });
    return rows;
  }, [liveGpsWorldPoint, mergedTrackedBase, props.selectedUavId]);
  const liveGpsTrack = useMemo<MissionTrack | null>(() => {
    if (!liveGpsWorldPoint) return null;
    return {
      id: props.selectedUavId || "HM",
      x: Number(liveGpsWorldPoint.x),
      y: Number(liveGpsWorldPoint.y),
      z: Number(liveGpsWorldPoint.z ?? 0),
      attachedBsId: "GNSS",
      interferenceRisk: "low",
    };
  }, [liveGpsWorldPoint, props.selectedUavId]);
  const liveLocalOrigin = useMemo<GeodeticOrigin | null>(
    () => (liveGpsEnabled && liveGpsPoint
      ? { lon: Number(liveGpsPoint.lon), lat: Number(liveGpsPoint.lat) }
      : null),
    [liveGpsEnabled, liveGpsPoint],
  );
  const coordinateContext = useMemo(
    () =>
      buildCoordinateContext({
        ...props,
        route: mapRoute,
        plannedPosition: mapPlannedPosition ?? undefined,
        trackedPositions: mapTrackedPositions,
        noFlyZones: mergedNoFlyZones,
        baseStations: mergedBaseStations,
        coverage: mergedCoverage,
        routeOverlays: mergedRouteOverlays,
      }, liveLocalOrigin),
    [
      props.coordinateMode,
      props.polygonOverlays,
      liveLocalOrigin,
      mapPlannedPosition,
      mapRoute,
      mergedBaseStations,
      mergedCoverage,
      mergedNoFlyZones,
      mergedRouteOverlays,
      mapTrackedPositions,
    ]
  );
  const mapViewEngine: MapViewEngine = effectiveRenderMode === "cesium3d" ? "3D_Cesium" : "2D_Leaflet";

  const publishMapPoint = (point: MissionPoint, source: "waypoint" | "nfz_center") => {
    if (!mapServiceBase) return;
    if (coordinateContext.mode !== "geo") return;
    const id = `${source}-${Date.now()}-${Math.round(Math.random() * 10000)}`;
    void plotMapPoint(mapServiceBase, {
      id,
      lat: Number(point.y),
      lon: Number(point.x),
      alt: Number(point.z ?? 0),
      scope: mapSyncScope,
      metadata: {
        source: `ui-${source}`,
      },
    });
  };

  const onAddWaypoint: ((point: MissionPoint) => void) | undefined = props.onAddWaypoint
    ? (point) => {
      props.onAddWaypoint?.(point);
      publishMapPoint(point, "waypoint");
    }
    : undefined;

  const onAddNoFlyZoneCenter: ((point: MissionPoint) => void) | undefined = props.onAddNoFlyZoneCenter
    ? (point) => {
      props.onAddNoFlyZoneCenter?.(point);
      publishMapPoint(point, "nfz_center");
    }
    : undefined;

  const resetAllViews = () => {
    setSvgResetKey((v) => v + 1);
    setSvgZoomResetKey((v) => v + 1);
    setCesiumResetViewSeq((v) => v + 1);
  };

  const centerOnLiveGps = () => {
    if (!recenterTarget) return;
    setCenterGpsSeq((v) => v + 1);
  };

  useEffect(() => {
    if (!props.externalResetSeq || props.externalResetSeq <= 0) return;
    resetAllViews();
  }, [props.externalResetSeq]);

  useEffect(() => {
    setLiveGpsEnabled(props.enableLiveGps !== false);
  }, [props.enableLiveGps]);

  useEffect(() => {
    let active = true;
    if (!mapServiceBase) {
      setMapServiceStatus("idle");
      setMapServiceConfig(null);
      return () => {
        active = false;
      };
    }
    setMapServiceStatus("loading");
    void (async () => {
      const cfg = await fetchMapServiceConfig(mapServiceBase);
      if (!active) return;
      if (cfg) {
        setMapServiceConfig(cfg);
        setMapServiceStatus("ready");
      } else {
        setMapServiceConfig(null);
        setMapServiceStatus("error");
      }
    })();
    return () => {
      active = false;
    };
  }, [mapServiceBase]);

  useEffect(() => {
    if (!mapSyncEnabled) {
      setMapSyncStatus("idle");
      setMapSyncState(null);
      mapSyncRevisionRef.current = null;
      mapSyncDigestRef.current = "";
      return;
    }
    let active = true;
    let timer = 0;
    const load = async (initial = false) => {
      if (!active) return;
      if (initial) setMapSyncStatus("loading");
      const state = await fetchMapSyncState(mapServiceBase, {
        scope: mapSyncScope,
        includeShared: mapSyncIncludeShared,
      });
      if (!active) return;
      if (state) {
        const revision = typeof state.sync?.revision === "number" ? Number(state.sync.revision) : null;
        let changed = true;
        if (revision !== null) {
          changed = mapSyncRevisionRef.current !== revision;
          mapSyncRevisionRef.current = revision;
        } else {
          const digest = JSON.stringify({
            scope: state.scope,
            includeShared: state.includeShared,
            sharedScope: state.sharedScope,
            layers: state.layers,
          });
          changed = mapSyncDigestRef.current !== digest;
          mapSyncDigestRef.current = digest;
        }
        if (changed) setMapSyncState(state);
        setMapSyncStatus("ready");
      } else {
        setMapSyncStatus("error");
      }
    };
    void load(true);
    timer = window.setInterval(() => {
      void load(false);
    }, 1200);
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [mapServiceBase, mapSyncEnabled, mapSyncIncludeShared, mapSyncScope]);

  useEffect(() => {
    if (!mapServiceBase) {
      setMapPrefetchStatus("idle");
      return;
    }
    if (typeof window === "undefined") return;
    const marker = `network-map-prefetch:${mapServiceBase}`;
    if (window.localStorage.getItem(marker) === "ready") {
      setMapPrefetchStatus("ready");
      return;
    }
    let active = true;
    const zoomMin = mapServiceConfig?.defaults.zoomMin ?? 11;
    const zoomMax = mapServiceConfig?.defaults.zoomMax ?? 15;
    const centerLon = mapServiceConfig?.center.lon ?? 24.8286;
    const centerLat = mapServiceConfig?.center.lat ?? 60.1866;
    const radiusKm = mapServiceConfig?.radiusKm ?? 10;
    setMapPrefetchStatus("running");
    void prefetchMapServiceCache(mapServiceBase, {
      provider: "all",
      centerLon,
      centerLat,
      radiusKm,
      zoomMin,
      zoomMax,
    }).then((ok) => {
      if (!active) return;
      if (ok) {
        setMapPrefetchStatus("ready");
        window.localStorage.setItem(marker, "ready");
      } else {
        setMapPrefetchStatus("error");
      }
    });
    return () => {
      active = false;
    };
  }, [mapServiceBase, mapServiceConfig]);

  useEffect(() => {
    if (!mapServiceBase) return;
    void toggleMapView(mapServiceBase, mapViewEngine);
  }, [mapServiceBase, mapViewEngine]);

  useEffect(() => {
    if (!liveGpsEnabled) return;
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setLiveGpsError("Browser geolocation is unavailable.");
      return;
    }
    let active = true;
    const watchId = navigator.geolocation.watchPosition(
      (position) => {
        if (!active) return;
        const coords = position.coords;
        const nextPoint: LiveGpsPoint = {
          lon: Number(coords.longitude),
          lat: Number(coords.latitude),
          altM: Number(coords.altitude ?? 0),
          accuracyM: Number(coords.accuracy ?? 0),
          tsMs: Number(position.timestamp || Date.now()),
        };
        setLiveGpsPoint((prev) => {
          if (!prev) return nextPoint;
          const movedM = geoDistanceMeters(prev.lon, prev.lat, nextPoint.lon, nextPoint.lat);
          const altDeltaM = Math.abs(Number(prev.altM ?? 0) - Number(nextPoint.altM ?? 0));
          const ageMs = Math.abs(Number(nextPoint.tsMs ?? 0) - Number(prev.tsMs ?? 0));
          const jitterFloorM = Math.max(
            1.2,
            Math.min(8, ((Number(prev.accuracyM ?? 0) + Number(nextPoint.accuracyM ?? 0)) * 0.12)),
          );
          if (movedM < jitterFloorM && altDeltaM < 1.0 && ageMs < 2500) return prev;
          return nextPoint;
        });
        setLiveGpsError("");
      },
      (err) => {
        if (!active) return;
        const msg = err?.message ? String(err.message) : `Geolocation error (${String(err?.code ?? "unknown")})`;
        setLiveGpsError(msg);
      },
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 15000 },
    );
    return () => {
      active = false;
      try {
        navigator.geolocation.clearWatch(watchId);
      } catch {
        // ignore geolocation cleanup failures
      }
    };
  }, [liveGpsEnabled]);

  const recenterTarget = useMemo<MissionPoint | null>(() => {
    if (liveGpsTrack) return { x: liveGpsTrack.x, y: liveGpsTrack.y, z: liveGpsTrack.z };
    const selected = props.selectedUavId
      ? mapTrackedPositions.find((t) => t.id === props.selectedUavId)
      : null;
    if (selected) return { x: selected.x, y: selected.y, z: selected.z };
    const hmTrack = mapTrackedPositions.find((t) => String(t.id || "").trim().toLowerCase() === "hm");
    if (hmTrack) return { x: hmTrack.x, y: hmTrack.y, z: hmTrack.z };
    const firstTrack = mapTrackedPositions[0];
    if (firstTrack) return { x: firstTrack.x, y: firstTrack.y, z: firstTrack.z };
    const hmRoute = mapRoute[0];
    if (hmRoute) return { x: hmRoute.x, y: hmRoute.y, z: hmRoute.z };
    return null;
  }, [liveGpsTrack, mapRoute, mapTrackedPositions, props.selectedUavId]);
  const mapModeLabel =
    mapRenderMode === "svg2d"
      ? "2D SVG"
      : mapRenderMode === "svg3d"
        ? "3D SVG"
        : canUseCesium
          ? "3D Cesium"
          : "3D Cesium (fallback 3D SVG)";

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div style={{ fontSize: 11, color: "#667085" }}>
          Map:{" "}
          <strong style={{ color: mapRenderMode === "cesium3d" && !canUseCesium ? "#b42318" : "#027a48" }}>
            {mapModeLabel}
          </strong>
          <span style={{ marginLeft: 6 }}>• {coordinateContext.mode === "geo" ? "GPS lon/lat" : "local XY"}</span>
          <span style={{ marginLeft: 6 }}>• Basemap {basemapLabel(basemapChoice)}</span>
          {mapServiceBase ? (
            <span style={{ marginLeft: 6 }}>
              • MapSvc {mapServiceStatus === "ready" ? "online" : mapServiceStatus}
              {mapServiceConfig?.cacheStatus ? ` (${Math.round(mapServiceConfig.cacheStatus.tileCount)} tiles)` : ""}
            </span>
          ) : null}
          {mapSyncEnabled ? (
            <span style={{ marginLeft: 6 }}>
              • Sync {mapSyncStatus === "ready" ? "online" : mapSyncStatus} ({mapSyncScope}{mapSyncIncludeShared ? " +shared" : ""})
            </span>
          ) : null}
          {liveGpsPoint ? (
            <span style={{ marginLeft: 6 }}>
              • GPS {liveGpsPoint.lon.toFixed(6)}, {liveGpsPoint.lat.toFixed(6)}, {roundMeters(liveGpsPoint.altM)}m
            </span>
          ) : (
            <span style={{ marginLeft: 6 }}>• GPS {liveGpsEnabled ? "acquiring..." : "off"}</span>
          )}
          {mapRenderMode === "cesium3d" && !canUseCesium ? ` (${state === "loading" ? "loading Cesium CDN..." : error ?? "Cesium unavailable"})` : ""}
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "inline-flex", border: "1px solid #d0d5dd", borderRadius: 999, overflow: "hidden", background: "#fff" }}>
            <button
              type="button"
              onClick={() => setMapRenderMode("svg2d")}
              style={{
                border: "none",
                borderRight: "1px solid #d0d5dd",
                background: mapRenderMode === "svg2d" ? "#eef4ff" : "#fff",
                color: mapRenderMode === "svg2d" ? "#155eef" : "#344054",
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
              }}
              title="2D SVG map for fast path and network coverage checks"
            >
              2D SVG
            </button>
            <button
              type="button"
              onClick={() => setMapRenderMode("svg3d")}
              style={{
                border: "none",
                borderRight: "1px solid #d0d5dd",
                background: mapRenderMode === "svg3d" ? "#eef4ff" : "#fff",
                color: mapRenderMode === "svg3d" ? "#155eef" : "#344054",
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
              }}
              title="3D SVG mission perspective"
            >
              3D SVG
            </button>
            <button
              type="button"
              onClick={() => setMapRenderMode("cesium3d")}
              style={{
                border: "none",
                background: mapRenderMode === "cesium3d" ? "#eef4ff" : "#fff",
                color: mapRenderMode === "cesium3d" ? "#155eef" : "#344054",
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
              }}
              title="3D Cesium globe with extruded route and no-fly zones"
            >
              3D Cesium
            </button>
          </div>
          <button
            type="button"
            onClick={() => setHighContrastRoute((v) => !v)}
            style={{
              borderRadius: 999,
              border: "1px solid #d0d5dd",
              background: highContrastRoute ? "#fffaeb" : "#fff",
              color: highContrastRoute ? "#b54708" : "#344054",
              padding: "4px 10px",
              fontSize: 11,
              fontWeight: 700,
              cursor: "pointer",
            }}
            title="Toggle Cesium route visibility style"
          >
            Route Contrast: {highContrastRoute ? "High" : "Normal"}
          </button>
          <label style={{ fontSize: 11, color: "#475467", display: "inline-flex", alignItems: "center", gap: 6, marginLeft: 2 }}>
            <input type="checkbox" checked={liveGpsEnabled} onChange={(e) => setLiveGpsEnabled(e.target.checked)} />
            Live GPS
          </label>
          <button
            type="button"
            onClick={centerOnLiveGps}
            disabled={!recenterTarget}
            style={{
              borderRadius: 999,
              border: "1px solid #d0d5dd",
              background: recenterTarget ? "#fff" : "#f2f4f7",
              color: recenterTarget ? "#344054" : "#98a2b3",
              padding: "4px 10px",
              fontSize: 12,
              fontWeight: 600,
              cursor: recenterTarget ? "pointer" : "not-allowed",
            }}
          >
            Center GPS
          </button>
          <button
            type="button"
            onClick={resetAllViews}
            style={{ borderRadius: 999, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", padding: "4px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}
          >
            Reset
          </button>
        </div>
      </div>
      {liveGpsError ? <div style={{ fontSize: 11, color: "#b42318" }}>Live GPS: {liveGpsError}</div> : null}
      {mapServiceBase ? (
        <div style={{ fontSize: 11, color: mapServiceStatus === "error" ? "#b42318" : "#667085" }}>
          Map service: {mapServiceBase} • config {mapServiceStatus}
          {mapPrefetchStatus !== "idle" ? ` • cache prefetch ${mapPrefetchStatus}` : ""}
          {mapSyncEnabled ? ` • map sync ${mapSyncStatus}` : ""}
        </div>
      ) : null}

      {usingCesium ? (
        <CesiumMissionMap
          {...props}
          onAddWaypoint={onAddWaypoint}
          onAddNoFlyZoneCenter={onAddNoFlyZoneCenter}
          noFlyZones={mergedNoFlyZones}
          baseStations={mergedBaseStations}
          coverage={mergedCoverage}
          route={mapRoute}
          plannedPosition={mapPlannedPosition ?? undefined}
          routeOverlays={mergedRouteOverlays}
          trackedPositions={mapTrackedPositions}
          viewMode={cesiumViewMode}
          basemap={basemapChoice}
          nlsApiKey={nlsApiKey}
          mapServiceBase={mapServiceBase}
          mapServiceConfig={mapServiceConfig}
          onRecenterCurrent={centerOnLiveGps}
          canRecenterCurrent={Boolean(recenterTarget)}
          resetViewSeq={cesiumResetViewSeq}
          focusPoint={recenterTarget}
          focusSeq={centerGpsSeq}
          coordinateContext={coordinateContext}
          highContrastRoute={highContrastRoute}
        />
      ) : (
        <SvgMissionMap
          {...props}
          onAddWaypoint={onAddWaypoint}
          onAddNoFlyZoneCenter={onAddNoFlyZoneCenter}
          noFlyZones={mergedNoFlyZones}
          baseStations={mergedBaseStations}
          coverage={mergedCoverage}
          route={mapRoute}
          plannedPosition={mapPlannedPosition ?? undefined}
          routeOverlays={mergedRouteOverlays}
          trackedPositions={mapTrackedPositions}
          title={props.title ?? "Synchronized Mission Map"}
          onRecenterCurrent={centerOnLiveGps}
          canRecenterCurrent={Boolean(recenterTarget)}
          initialViewMode={svgViewMode}
          resetKey={svgResetKey}
          zoomResetKey={svgZoomResetKey}
          focusPoint={recenterTarget}
          focusSeq={centerGpsSeq}
          coordinateContext={coordinateContext}
        />
      )}
    </div>
  );
}
