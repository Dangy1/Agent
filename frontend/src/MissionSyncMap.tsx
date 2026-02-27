import React, { useEffect, useMemo, useRef, useState } from "react";

export type MissionPoint = { x: number; y: number; z?: number };
export type MissionNfz = { zone_id?: string; cx: number; cy: number; radius_m: number; z_min?: number; z_max?: number; reason?: string };
export type MissionBs = { id: string; x: number; y: number; status?: string };
export type MissionCoverage = { bsId: string; radiusM: number };
export type MissionTrack = {
  id: string;
  x: number;
  y: number;
  z?: number;
  attachedBsId?: string;
  interferenceRisk?: "low" | "medium" | "high";
};
export type MissionRouteOverlay = { id: string; route: MissionPoint[]; color?: string };

type Props = {
  title?: string;
  route: MissionPoint[];
  plannedPosition?: MissionPoint | null;
  trackedPositions?: MissionTrack[];
  routeOverlays?: MissionRouteOverlay[];
  focusSelectedTrack?: boolean;
  selectedUavId?: string;
  noFlyZones?: MissionNfz[];
  baseStations?: MissionBs[];
  coverage?: MissionCoverage[];
  showCoverage?: boolean;
  showInterferenceHints?: boolean;
  clickable?: boolean;
  onAddWaypoint?: (point: MissionPoint) => void;
  onAddNoFlyZoneCenter?: (point: MissionPoint) => void;
  externalResetSeq?: number;
};

type ViewMode = "2d" | "3d";
type MapChoice = "2d" | "3d" | "cesium";
type ScreenPoint = { x: number; y: number; depth: number };
type CesiumLoadState = "idle" | "loading" | "ready" | "error";

declare global {
  interface Window {
    Cesium?: any;
  }
}

const CESIUM_JS_URL = "https://unpkg.com/cesium@1.126.0/Build/Cesium/Cesium.js";
const CESIUM_CSS_URL = "https://unpkg.com/cesium@1.126.0/Build/Cesium/Widgets/widgets.css";
const CESIUM_ACCESS_TOKEN = ""; // Optional: set a token if you want Cesium Ion assets/terrain.

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

function coverageColor(status?: string): { fill: string; stroke: string } {
  if (status === "degraded") return { fill: "rgba(247,144,9,0.06)", stroke: "rgba(247,144,9,0.24)" };
  if (status === "maintenance") return { fill: "rgba(152,162,179,0.05)", stroke: "rgba(152,162,179,0.20)" };
  return { fill: "rgba(21,94,239,0.06)", stroke: "rgba(21,94,239,0.18)" };
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
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

function xyToLonLat(x: number, y: number): { lon: number; lat: number } {
  // Local demo grid (meters-like) mapped onto a small geographic patch for Cesium rendering.
  const origin = { lon: -122.4194, lat: 37.7749 };
  const metersPerDegLat = 111_320;
  const metersPerDegLon = metersPerDegLat * Math.cos((origin.lat * Math.PI) / 180);
  return {
    lon: origin.lon + x / metersPerDegLon,
    lat: origin.lat + y / metersPerDegLat,
  };
}

function lonLatToXy(lon: number, lat: number): { x: number; y: number } {
  const origin = { lon: -122.4194, lat: 37.7749 };
  const metersPerDegLat = 111_320;
  const metersPerDegLon = metersPerDegLat * Math.cos((origin.lat * Math.PI) / 180);
  return {
    x: (lon - origin.lon) * metersPerDegLon,
    y: (lat - origin.lat) * metersPerDegLat,
  };
}

function buildCirclePolygonLonLat(cx: number, cy: number, r: number, steps = 36): number[] {
  const out: number[] = [];
  for (let i = 0; i < steps; i += 1) {
    const a = (i / steps) * Math.PI * 2;
    const p = xyToLonLat(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
    out.push(p.lon, p.lat);
  }
  return out;
}

function CesiumMissionMap({
  viewMode,
  route,
  plannedPosition,
  trackedPositions = [],
  routeOverlays = [],
  focusSelectedTrack = false,
  selectedUavId,
  noFlyZones = [],
  baseStations = [],
  coverage = [],
  showCoverage,
  clickable,
  onAddWaypoint,
  onAddNoFlyZoneCenter,
  zoomCommandSeq = 0,
  zoomCommandDir = 0,
  resetViewSeq = 0,
}: Props & { viewMode: ViewMode; zoomCommandSeq?: number; zoomCommandDir?: -1 | 0 | 1; resetViewSeq?: number }): React.ReactNode {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<any | null>(null);
  const handlerRef = useRef<any | null>(null);

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
    viewer.scene.skyBox.show = false;
    viewer.scene.skyAtmosphere.show = false;
    viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#f9fbff");
    viewerRef.current = viewer;

    if (clickable && (onAddWaypoint || onAddNoFlyZoneCenter)) {
      const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
      handler.setInputAction((movement: any) => {
        const cartesian = viewer.camera.pickEllipsoid(movement.position, viewer.scene.globe.ellipsoid);
        if (!cartesian) return;
        const carto = Cesium.Cartographic.fromCartesian(cartesian);
        const lon = Cesium.Math.toDegrees(carto.longitude);
        const lat = Cesium.Math.toDegrees(carto.latitude);
        const xy = lonLatToXy(lon, lat);
        const point = { x: Number(clamp(xy.x, 0, 400).toFixed(1)), y: Number(clamp(xy.y, 0, 300).toFixed(1)), z: 40 };
        const shift = Boolean(movement?.shiftKey);
        if (shift && onAddNoFlyZoneCenter) onAddNoFlyZoneCenter(point);
        else if (onAddWaypoint) onAddWaypoint(point);
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
      handlerRef.current = handler;
    }

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
  }, [clickable, onAddNoFlyZoneCenter, onAddWaypoint]);

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
    if (!viewer || !zoomCommandDir || zoomCommandSeq <= 0) return;
    try {
      const amount = viewer.scene.mode === (window.Cesium?.SceneMode?.SCENE2D ?? -1) ? 400 : 220;
      if (zoomCommandDir > 0) viewer.camera.zoomIn(amount);
      else viewer.camera.zoomOut(amount);
    } catch {
      // ignore zoom failures (viewer may still be initializing)
    }
  }, [zoomCommandDir, zoomCommandSeq]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium || resetViewSeq <= 0) return;
    try {
      if (route.length > 0 || noFlyZones.length > 0 || trackedPositions.length > 0 || baseStations.length > 0) {
        viewer.flyTo(viewer.entities, { duration: 0.6, offset: new Cesium.HeadingPitchRange(0, -0.7, 1800) });
      } else {
        const ll = xyToLonLat(200, 150);
        viewer.camera.flyTo({
          destination: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, 1800),
          duration: 0.6,
        });
      }
    } catch {
      // ignore reset failures while viewer initializes
    }
  }, [resetViewSeq, route, noFlyZones, trackedPositions, baseStations]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;

    viewer.entities.removeAll();

    routeOverlays.forEach((ov) => {
      if (!Array.isArray(ov.route) || ov.route.length < 2) return;
      const pos = ov.route.flatMap((p) => {
        const ll = xyToLonLat(p.x, p.y);
        return [ll.lon, ll.lat, Number(p.z ?? 0)];
      });
      viewer.entities.add({
        name: `Route ${ov.id}`,
        polyline: {
          positions: Cesium.Cartesian3.fromDegreesArrayHeights(pos),
          width: 2,
          material: Cesium.Color.fromCssColorString(ov.color || "#94a3b8").withAlpha(0.7),
          clampToGround: false,
        },
      });
    });

    if (route.length >= 2) {
      const pos = route.flatMap((p) => {
        const ll = xyToLonLat(p.x, p.y);
        return [ll.lon, ll.lat, Number(p.z ?? 0)];
      });
      viewer.entities.add({
        name: "Planned Route",
        polyline: {
          positions: Cesium.Cartesian3.fromDegreesArrayHeights(pos),
          width: 4,
          material: Cesium.Color.fromCssColorString("#2563eb"),
          clampToGround: false,
        },
      });
    }

    noFlyZones.forEach((z, i) => {
      const ring = buildCirclePolygonLonLat(z.cx, z.cy, z.radius_m);
      viewer.entities.add({
        name: z.zone_id ?? `NFZ-${i + 1}`,
        polygon: {
          hierarchy: Cesium.Cartesian3.fromDegreesArray(ring),
          height: Number(z.z_min ?? 0),
          extrudedHeight: Number(z.z_max ?? 120),
          material: Cesium.Color.fromCssColorString("#f04438").withAlpha(0.18),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString("#f04438"),
          perPositionHeight: false,
        },
        label: {
          text: `${z.zone_id ?? "NFZ"} (${Math.round(Number(z.z_min ?? 0))}-${Math.round(Number(z.z_max ?? 120))}m)`,
          font: "12px sans-serif",
          fillColor: Cesium.Color.fromCssColorString("#b42318"),
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -12),
          heightReference: Cesium.HeightReference.NONE,
        },
        position: Cesium.Cartesian3.fromDegrees(xyToLonLat(z.cx, z.cy).lon, xyToLonLat(z.cx, z.cy).lat, Number(z.z_max ?? 120)),
      });
    });

    baseStations.forEach((bs) => {
      const ll = xyToLonLat(bs.x, bs.y);
      viewer.entities.add({
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
          const ring = buildCirclePolygonLonLat(bs.x, bs.y, cov.radiusM, 48);
          viewer.entities.add({
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

    trackedPositions.forEach((t) => {
      const ll = xyToLonLat(t.x, t.y);
      const isSelected = t.id === selectedUavId;
      const muted = focusSelectedTrack && !isSelected;
      viewer.entities.add({
        name: t.id,
        position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, Number(t.z ?? 0)),
        point: {
          pixelSize: isSelected ? 12 : 9,
          color: Cesium.Color.fromCssColorString(isSelected ? "#f97316" : muted ? "#98a2b3" : "#22c55e"),
          outlineColor: Cesium.Color.fromCssColorString(muted ? "#667085" : "#1f2937"),
          outlineWidth: 1,
        },
        label: {
          text: `${t.id} • ${Math.round(Number(t.z ?? 0))}m`,
          font: "12px sans-serif",
          fillColor: Cesium.Color.fromCssColorString(muted ? "#667085" : "#101828"),
          pixelOffset: new Cesium.Cartesian2(10, 12),
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
        },
      });
    });

    if (plannedPosition) {
      const ll = xyToLonLat(plannedPosition.x, plannedPosition.y);
      viewer.entities.add({
        name: "planned",
        position: Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, Number(plannedPosition.z ?? 0)),
        point: {
          pixelSize: 10,
          color: Cesium.Color.fromCssColorString("#f59e0b"),
          outlineColor: Cesium.Color.fromCssColorString("#92400e"),
          outlineWidth: 1.5,
        },
        label: {
          text: `planned • ${Math.round(Number(plannedPosition.z ?? 0))}m`,
          font: "12px sans-serif",
          pixelOffset: new Cesium.Cartesian2(10, 12),
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineWidth: 2,
        },
      });
    }

    if (route.length > 0) {
      const allPts = route.map((p) => {
        const ll = xyToLonLat(p.x, p.y);
        return Cesium.Cartesian3.fromDegrees(ll.lon, ll.lat, Number(p.z ?? 0));
      });
      try {
        viewer.flyTo(viewer.entities, { duration: 0.7, offset: new Cesium.HeadingPitchRange(0, -0.7, 1800) });
      } catch {
        if (allPts.length) viewer.camera.lookAt(allPts[0], new Cesium.HeadingPitchRange(0, -0.7, 1800));
      }
    }
  }, [baseStations, coverage, focusSelectedTrack, noFlyZones, plannedPosition, route, routeOverlays, selectedUavId, showCoverage, trackedPositions]);

  return <div ref={hostRef} style={{ width: "100%", height: 360, borderRadius: 12, overflow: "hidden", border: "1px solid #e4e7ec", background: "#f9fbff" }} />;
}

function SvgMissionMap({
  title,
  route,
  plannedPosition,
  trackedPositions = [],
  routeOverlays = [],
  selectedUavId,
  focusSelectedTrack = false,
  noFlyZones = [],
  baseStations = [],
  coverage = [],
  showCoverage = true,
  showInterferenceHints = true,
  clickable = false,
  onAddWaypoint,
  onAddNoFlyZoneCenter,
  initialViewMode = "3d",
  resetKey = 0,
  zoomCommandSeq = 0,
  zoomCommandDir = 0,
  zoomResetKey = 0,
}: Props & {
  title: string;
  initialViewMode?: ViewMode;
  resetKey?: number;
  zoomCommandSeq?: number;
  zoomCommandDir?: -1 | 0 | 1;
  zoomResetKey?: number;
}): React.ReactNode {
  const [viewMode, setViewMode] = useState<ViewMode>(initialViewMode);
  const [yawDeg, setYawDeg] = useState(-28);
  const [pitchDeg, setPitchDeg] = useState(24);
  const [zoomScale, setZoomScale] = useState(1);
  const dragRef = useRef<{ active: boolean; moved: boolean; x: number; y: number }>({ active: false, moved: false, x: 0, y: 0 });

  useEffect(() => {
    setViewMode(initialViewMode);
  }, [initialViewMode]);

  useEffect(() => {
    if (initialViewMode === "3d") {
      setYawDeg(-28);
      setPitchDeg(24);
    }
  }, [initialViewMode, resetKey]);

  useEffect(() => {
    if (!zoomCommandDir || zoomCommandSeq <= 0) return;
    setZoomScale((v) => clamp(Number((v * (zoomCommandDir > 0 ? 1.15 : 1 / 1.15)).toFixed(3)), 0.6, 3));
  }, [zoomCommandDir, zoomCommandSeq]);

  useEffect(() => {
    setZoomScale(1);
  }, [zoomResetKey]);

  const width = 760;
  const height = 360;
  const pad = 20;
  const world = { minX: 0, maxX: 400, minY: 0, maxY: 300 };
  const maxAltitudeM = 120;
  const worldCenter = { x: (world.minX + world.maxX) / 2, y: (world.minY + world.maxY) / 2, z: maxAltitudeM / 2 };
  const sx = (x: number) => pad + ((x - world.minX) / (world.maxX - world.minX)) * (width - pad * 2);
  const sy = (y: number) => height - pad - ((y - world.minY) / (world.maxY - world.minY)) * (height - pad * 2);
  const wx = (px: number) => world.minX + ((px - pad) / (width - pad * 2)) * (world.maxX - world.minX);
  const wy = (py: number) => world.minY + ((height - pad - py) / (height - pad * 2)) * (world.maxY - world.minY);
  const zOf = (z?: number) => clamp(Number(z ?? 0), 0, maxAltitudeM);
  const coverageMap = new Map(coverage.map((c) => [c.bsId, c.radiusM]));
  const selectedTrack = trackedPositions.find((t) => t.id === selectedUavId) ?? trackedPositions[0];

  const projection = useMemo(() => {
    const yaw = (yawDeg * Math.PI) / 180;
    const pitch = (pitchDeg * Math.PI) / 180;
    const cosY = Math.cos(yaw);
    const sinY = Math.sin(yaw);
    const cosP = Math.cos(pitch);
    const sinP = Math.sin(pitch);
    const scaleX = (width - pad * 2) / (world.maxX - world.minX) * 0.72 * zoomScale;
    const scaleY = (height - pad * 2) / (world.maxY - world.minY) * 0.72 * zoomScale;
    const scaleZ = scaleY * 1.25;
    const cx = width * 0.5;
    const cy = height * 0.58;
    const cameraDepth = 520;
    const perspective = 0.0017;
    const project3 = (p: MissionPoint): ScreenPoint => {
      const x0 = (p.x - worldCenter.x) * scaleX;
      const y0 = -(p.y - worldCenter.y) * scaleY;
      const z0 = (zOf(p.z) - worldCenter.z) * scaleZ;
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
  }, [height, pad, pitchDeg, width, world.maxX, world.maxY, world.minX, world.minY, worldCenter.x, worldCenter.y, worldCenter.z, zoomScale]);

  const project2 = (p: MissionPoint): ScreenPoint => {
    const x = sx(p.x);
    const y = sy(p.y);
    const cx = width * 0.5;
    const cy = height * 0.5;
    return { x: cx + (x - cx) * zoomScale, y: cy + (y - cy) * zoomScale, depth: 0 };
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

  const pointerCursor = viewMode === "3d" ? (dragRef.current.active ? "grabbing" : "grab") : clickable ? "crosshair" : "default";

  const handleMapClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (viewMode === "3d") return;
    if (!clickable) return;
    if (dragRef.current.moved) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const py = ((e.clientY - rect.top) / rect.height) * height;
    const cx = width * 0.5;
    const cy = height * 0.5;
    const unzoomPx = cx + (px - cx) / zoomScale;
    const unzoomPy = cy + (py - cy) / zoomScale;
    const x = Math.max(0, Math.min(400, wx(unzoomPx)));
    const y = Math.max(0, Math.min(300, wy(unzoomPy)));
    const point = { x: Number(x.toFixed(1)), y: Number(y.toFixed(1)), z: 40 };
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
            {viewMode === "3d" ? "3D map with mouse orbit (X/Y/Z axes shown). Drag to rotate view." : "2D top-down map for precise waypoint/NFZ editing (X/Y plane)."}{" "}
            {` Zoom ${Math.round(zoomScale * 100)}%.`}{" "}
            {clickable && viewMode === "2d" ? "Click map to add waypoint." : ""}
            {clickable && viewMode === "2d" && onAddNoFlyZoneCenter ? " Shift+click to add a no-fly-zone center." : ""}
            {clickable && viewMode === "3d" ? " Switch to 2D to add waypoint/NFZ center." : ""}
          </div>
        </div>
      </div>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", borderRadius: 12, border: "1px solid #e4e7ec", background: "#f9fbff", cursor: pointerCursor }}
        onMouseDown={(e) => { if (viewMode !== "3d") return; dragRef.current = { active: true, moved: false, x: e.clientX, y: e.clientY }; }}
        onMouseMove={(e) => {
          if (viewMode !== "3d" || !dragRef.current.active) return;
          const dx = e.clientX - dragRef.current.x;
          const dy = e.clientY - dragRef.current.y;
          if (Math.abs(dx) + Math.abs(dy) > 2) dragRef.current.moved = true;
          dragRef.current.x = e.clientX;
          dragRef.current.y = e.clientY;
          setYawDeg((v) => v + dx * 0.35);
          setPitchDeg((v) => clamp(v - dy * 0.25, -10, 80));
        }}
        onMouseUp={() => { dragRef.current.active = false; }}
        onMouseLeave={() => { dragRef.current.active = false; }}
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
            <text x={width - 86} y={height - 26} fontSize="10" fill="#667085">Plane: X/Y</text>
          </g>
        ) : axis ? (
          <g opacity={0.9}>
            <line x1={axis.origin.x} y1={axis.origin.y} x2={axis.xEnd.x} y2={axis.xEnd.y} stroke="#ef4444" strokeWidth="2" />
            <line x1={axis.origin.x} y1={axis.origin.y} x2={axis.yEnd.x} y2={axis.yEnd.y} stroke="#2563eb" strokeWidth="2" />
            <line x1={axis.origin.x} y1={axis.origin.y} x2={axis.zEnd.x} y2={axis.zEnd.y} stroke="#16a34a" strokeWidth="2" />
            <circle cx={axis.origin.x} cy={axis.origin.y} r={2.8} fill="#111827" />
            <text x={axis.xEnd.x + 4} y={axis.xEnd.y + 2} fontSize="10" fill="#b42318">X</text>
            <text x={axis.yEnd.x + 4} y={axis.yEnd.y + 2} fontSize="10" fill="#1d4ed8">Y</text>
            <text x={axis.zEnd.x + 4} y={axis.zEnd.y + 2} fontSize="10" fill="#15803d">Z</text>
            <text x={12} y={height - 18} fontSize="10" fill="#667085">Z starts at 0 • drag mouse to rotate</text>
          </g>
        ) : null}

        {showCoverage ? baseStations.map((bs) => {
          const rWorld = coverageMap.get(bs.id);
          if (!rWorld) return null;
          const c = coverageColor(bs.status);
          if (viewMode === "2d") {
            const rPx = (rWorld / (world.maxX - world.minX)) * (width - pad * 2) * zoomScale;
            const c2 = project2({ x: bs.x, y: bs.y, z: 0 });
            return <circle key={`${bs.id}-cov`} cx={c2.x} cy={c2.y} r={rPx} fill={c.fill} stroke={c.stroke} />;
          }
          const ringPts: ScreenPoint[] = [];
          for (let i = 0; i <= 40; i += 1) {
            const a = (i / 40) * Math.PI * 2;
            ringPts.push(project({ x: bs.x + Math.cos(a) * rWorld, y: bs.y + Math.sin(a) * rWorld, z: 0 }));
          }
          return <polyline key={`${bs.id}-cov`} points={projectRingPath(ringPts)} fill="none" stroke={c.stroke} strokeWidth="1.5" />;
        }) : null}

        {noFlyZones.map((z, i) => {
          const zMin = zOf(z.z_min);
          const zMax = zOf(z.z_max);
          if (viewMode === "2d") {
            const rPx = (z.radius_m / (world.maxX - world.minX)) * (width - pad * 2) * zoomScale;
            const c2 = project2({ x: z.cx, y: z.cy, z: 0 });
            return (
              <g key={`${z.zone_id ?? "nfz"}-${i}`}>
                <circle cx={c2.x} cy={c2.y} r={Math.max(6, rPx)} fill="rgba(240,68,56,0.08)" stroke="#f04438" strokeDasharray="4 3" />
                <text x={c2.x + 8} y={c2.y - 8} fontSize="10" fill="#b42318">{z.zone_id ?? "NFZ"}</text>
              </g>
            );
          }
          const bottomRing: ScreenPoint[] = [];
          const topRing: ScreenPoint[] = [];
          for (let k = 0; k <= 32; k += 1) {
            const a = (k / 32) * Math.PI * 2;
            bottomRing.push(project({ x: z.cx + Math.cos(a) * z.radius_m, y: z.cy + Math.sin(a) * z.radius_m, z: zMin }));
            topRing.push(project({ x: z.cx + Math.cos(a) * z.radius_m, y: z.cy + Math.sin(a) * z.radius_m, z: zMax }));
          }
          return (
            <g key={`${z.zone_id ?? "nfz"}-${i}`}>
              {[0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2].map((a, si) => {
                const b = project({ x: z.cx + Math.cos(a) * z.radius_m, y: z.cy + Math.sin(a) * z.radius_m, z: zMin });
                const t = project({ x: z.cx + Math.cos(a) * z.radius_m, y: z.cy + Math.sin(a) * z.radius_m, z: zMax });
                return <line key={`side-${si}`} x1={b.x} y1={b.y} x2={t.x} y2={t.y} stroke="rgba(240,68,56,0.65)" strokeWidth="1" />;
              })}
              <polyline points={projectRingPath(bottomRing)} fill="none" stroke="rgba(249,112,102,0.7)" strokeDasharray="4 3" />
              <polyline points={projectRingPath(topRing)} fill="rgba(240,68,56,0.08)" stroke="#f04438" strokeDasharray="4 3" />
              {(() => { const labelPt = project({ x: z.cx + z.radius_m, y: z.cy, z: zMax }); return <text x={labelPt.x + 8} y={labelPt.y - 6} fontSize="10" fill="#b42318">{`${z.zone_id ?? "NFZ"} (${Math.round(zMin)}-${Math.round(zMax)}m)`}</text>; })()}
            </g>
          );
        })}

        {route.length > 0 && viewMode === "2d" ? <polyline points={routePath2d} fill="none" stroke="#2563eb" strokeWidth="2.4" /> : null}
        {route.length > 0 && viewMode === "3d" ? <><polyline points={route.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")} fill="none" stroke="rgba(37,99,235,0.15)" strokeWidth="1.4" strokeDasharray="4 4" /><polyline points={routePathProjected} fill="none" stroke="#2563eb" strokeWidth="2.4" /></> : null}
        {routeOverlays.map((ov, idx) => {
          if (!Array.isArray(ov.route) || ov.route.length < 2) return null;
          const overlayPts2 = ov.route.map((p) => project2(p));
          const overlayPts3 = ov.route.map((p) => project(p));
          const pts2 = overlayPts2.map((q) => `${q.x},${q.y}`).join(" ");
          const pts3 = overlayPts3.map((q) => `${q.x},${q.y}`).join(" ");
          const color = ov.color || "#94a3b8";
          const label2 = overlayPts2[overlayPts2.length - 1];
          const label3 = overlayPts3[overlayPts3.length - 1];
          return (
            <g key={`ov-${ov.id}-${idx}`}>
              {viewMode === "2d" ? <polyline points={pts2} fill="none" stroke={color} strokeWidth="1.6" strokeDasharray="4 4" opacity={0.85} /> : null}
              {viewMode === "3d" ? <polyline points={pts3} fill="none" stroke={color} strokeWidth="1.5" strokeDasharray="4 4" opacity={0.8} /> : null}
              {(viewMode === "2d" ? overlayPts2 : overlayPts3).map((q, pi) => (
                <g key={`ov-pt-${ov.id}-${idx}-${pi}`}>
                  <circle cx={q.x} cy={q.y} r={2.2} fill="#fff" stroke={color} strokeWidth="1.1" opacity={0.95} />
                </g>
              ))}
              {viewMode === "2d" && label2 ? (
                <text x={label2.x + 6} y={label2.y + 12} fontSize="10" fill={color}>{`WP${Math.max(0, ov.route.length - 1)}`}</text>
              ) : null}
              {viewMode === "3d" && label3 ? (
                <text x={label3.x + 6} y={label3.y + 12} fontSize="10" fill={color}>{`WP${Math.max(0, ov.route.length - 1)}`}</text>
              ) : null}
            </g>
          );
        })}

        {route.map((p, i) => {
          const q = project(p);
          const g = project2({ x: p.x, y: p.y, z: 0 });
          return (
            <g key={`wp-${i}`}>
              {viewMode === "3d" ? <line x1={g.x} y1={g.y} x2={q.x} y2={q.y} stroke="#cbd5e1" strokeDasharray="3 3" /> : null}
              {viewMode === "3d" ? <circle cx={g.x} cy={g.y} r={2.5} fill="#dbeafe" stroke="#93c5fd" /> : null}
              <circle cx={q.x} cy={q.y} r={3.8} fill={i === 0 ? "#16a34a" : "#1d4ed8"} />
              <text x={q.x + 5} y={q.y - 5} fontSize="10" fill="#1f2937">
                {viewMode === "3d"
                  ? `${i === 0 ? "HM" : `WP${i}`} • ${Math.round(zOf(p.z))}m`
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
              <text x={q.x + 8} y={q.y + 14} fontSize="10" fill="#92400e">{viewMode === "3d" ? `planned • ${Math.round(zOf(plannedPosition.z))}m` : "planned"}</text>
            </g>
          );
        })() : null}

        {trackedPositions.map((t) => {
          const q = projectTrack(t);
          const g = project2({ x: t.x, y: t.y, z: 0 });
          const isSelected = t.id === selectedUavId;
          const muted = focusSelectedTrack && !isSelected;
          return (
            <g key={`trk-${t.id}`}>
              {viewMode === "3d" ? <line x1={g.x} y1={g.y} x2={q.x} y2={q.y} stroke={muted ? "#d0d5dd" : "#d0d5dd"} strokeDasharray="3 3" /> : null}
              {viewMode === "3d" ? <circle cx={g.x} cy={g.y} r={2.4} fill={muted ? "#f2f4f7" : "#ecfdf3"} stroke={muted ? "#d0d5dd" : "#86efac"} /> : null}
              <circle cx={q.x} cy={q.y} r={isSelected ? 6 : 4.8} fill={isSelected ? "#f97316" : muted ? "#98a2b3" : "#22c55e"} stroke={muted ? "#667085" : "#1f2937"} strokeWidth="1" />
              <text x={q.x + 7} y={q.y + 12} fontSize="10" fill={muted ? "#667085" : "#111827"}>{viewMode === "3d" ? `${t.id} • ${Math.round(zOf(t.z))}m` : t.id}</text>
              {isSelected ? <circle cx={q.x} cy={q.y} r={16} fill="none" stroke={riskColor(t.interferenceRisk)} strokeDasharray="4 4" /> : null}
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
    </div>
  );
}

export function MissionSyncMap(props: Props): React.ReactNode {
  const { state, error } = useCesiumLoader();
  const [mapChoice, setMapChoice] = useState<MapChoice>("2d");
  const [cesiumViewMode, setCesiumViewMode] = useState<ViewMode>("3d");
  const [svgResetKey, setSvgResetKey] = useState(0);
  const [svgZoomResetKey, setSvgZoomResetKey] = useState(0);
  const [zoomCommandSeq, setZoomCommandSeq] = useState(0);
  const [zoomCommandDir, setZoomCommandDir] = useState<-1 | 0 | 1>(0);
  const [cesiumResetViewSeq, setCesiumResetViewSeq] = useState(0);
  const canUseCesium = state === "ready";
  const wantCesium = mapChoice === "cesium";
  const forceSvg = !wantCesium || !canUseCesium;

  const issueZoom = (dir: -1 | 1) => {
    setZoomCommandDir(dir);
    setZoomCommandSeq((v) => v + 1);
  };
  const resetAllViews = () => {
    setSvgResetKey((v) => v + 1);
    setSvgZoomResetKey((v) => v + 1);
    setCesiumResetViewSeq((v) => v + 1);
  };

  useEffect(() => {
    if (!props.externalResetSeq || props.externalResetSeq <= 0) return;
    resetAllViews();
  }, [props.externalResetSeq]);

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div style={{ fontSize: 11, color: "#667085" }}>
          Map:{" "}
          <strong style={{ color: !forceSvg ? "#027a48" : mapChoice === "cesium" && !canUseCesium ? "#b42318" : "#475467" }}>
            {!forceSvg ? "Cesium" : mapChoice === "2d" ? "SVG 2D" : mapChoice === "3d" ? "SVG 3D" : "Cesium unavailable (SVG fallback)"}
          </strong>
          {mapChoice === "cesium" && !canUseCesium ? ` (${state === "loading" ? "loading Cesium CDN..." : error ?? "Cesium unavailable"})` : ""}
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: "#667085", marginLeft: 4 }}>View</span>
          <button type="button" onClick={() => setMapChoice("2d")} style={{ borderRadius: 999, border: "1px solid #d0d5dd", background: mapChoice === "2d" ? "#eef4ff" : "#fff", color: mapChoice === "2d" ? "#155eef" : "#344054", padding: "4px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>2D</button>
          <button type="button" onClick={() => setMapChoice("3d")} style={{ borderRadius: 999, border: "1px solid #d0d5dd", background: mapChoice === "3d" ? "#eef4ff" : "#fff", color: mapChoice === "3d" ? "#155eef" : "#344054", padding: "4px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>3D</button>
          <button type="button" onClick={() => { setMapChoice("cesium"); setCesiumViewMode("3d"); }} style={{ borderRadius: 999, border: "1px solid #d0d5dd", background: mapChoice === "cesium" ? "#eef4ff" : "#fff", color: mapChoice === "cesium" ? "#155eef" : "#344054", padding: "4px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>Cesium</button>
          <button type="button" onClick={() => issueZoom(1)} style={{ borderRadius: 999, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", padding: "4px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>Zoom In</button>
          <button type="button" onClick={() => issueZoom(-1)} style={{ borderRadius: 999, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", padding: "4px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>Zoom Out</button>
          <button
            type="button"
            onClick={resetAllViews}
            style={{ borderRadius: 999, border: "1px solid #d0d5dd", background: "#fff", color: "#344054", padding: "4px 10px", fontSize: 12, fontWeight: 600, cursor: "pointer" }}
          >
            Reset
          </button>
        </div>
      </div>

      {!forceSvg ? (
        <CesiumMissionMap {...props} viewMode={cesiumViewMode} zoomCommandSeq={zoomCommandSeq} zoomCommandDir={zoomCommandDir} resetViewSeq={cesiumResetViewSeq} />
      ) : (
        <SvgMissionMap
          {...props}
          title={props.title ?? "Synchronized Mission Map"}
          initialViewMode={mapChoice === "2d" ? "2d" : "3d"}
          resetKey={svgResetKey}
          zoomCommandSeq={zoomCommandSeq}
          zoomCommandDir={zoomCommandDir}
          zoomResetKey={svgZoomResetKey}
        />
      )}
    </div>
  );
}
