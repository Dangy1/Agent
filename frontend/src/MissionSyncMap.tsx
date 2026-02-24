import React from "react";

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

type Props = {
  title?: string;
  route: MissionPoint[];
  plannedPosition?: MissionPoint | null;
  trackedPositions?: MissionTrack[];
  selectedUavId?: string;
  noFlyZones?: MissionNfz[];
  baseStations?: MissionBs[];
  coverage?: MissionCoverage[];
  showCoverage?: boolean;
  showInterferenceHints?: boolean;
  clickable?: boolean;
  onAddWaypoint?: (point: MissionPoint) => void;
  onAddNoFlyZoneCenter?: (point: MissionPoint) => void;
};

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

export function MissionSyncMap({
  title = "Synchronized Mission Map",
  route,
  plannedPosition,
  trackedPositions = [],
  selectedUavId,
  noFlyZones = [],
  baseStations = [],
  coverage = [],
  showCoverage = true,
  showInterferenceHints = true,
  clickable = false,
  onAddWaypoint,
  onAddNoFlyZoneCenter,
}: Props): React.ReactNode {
  const width = 760;
  const height = 360;
  const pad = 20;
  const world = { minX: 0, maxX: 400, minY: 0, maxY: 300 };
  const sx = (x: number) => pad + ((x - world.minX) / (world.maxX - world.minX)) * (width - pad * 2);
  const sy = (y: number) => height - pad - ((y - world.minY) / (world.maxY - world.minY)) * (height - pad * 2);
  const wx = (px: number) => world.minX + ((px - pad) / (width - pad * 2)) * (world.maxX - world.minX);
  const wy = (py: number) => world.minY + ((height - pad - py) / (height - pad * 2)) * (world.maxY - world.minY);
  const routePath = route.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
  const coverageMap = new Map(coverage.map((c) => [c.bsId, c.radiusM]));
  const selectedTrack = trackedPositions.find((t) => t.id === selectedUavId) ?? trackedPositions[0];

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#101828" }}>{title}</div>
          <div style={{ fontSize: 11, color: "#667085" }}>
            Planned route (blue), UTM no-fly zones (red), network BS coverage (blue rings), tracked UAV positions (green/orange). {clickable ? "Click map to add waypoint." : ""}
            {clickable && onAddNoFlyZoneCenter ? " Shift+click to add a no-fly-zone center." : ""}
          </div>
        </div>
      </div>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", borderRadius: 12, border: "1px solid #e4e7ec", background: "#f9fbff", cursor: clickable ? "crosshair" : "default" }}
        onClick={(e) => {
          if (!clickable) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - rect.left) / rect.width) * width;
          const py = ((e.clientY - rect.top) / rect.height) * height;
          const x = Math.max(0, Math.min(400, wx(px)));
          const y = Math.max(0, Math.min(300, wy(py)));
          const point = { x: Number(x.toFixed(1)), y: Number(y.toFixed(1)), z: 40 };
          if (e.shiftKey && onAddNoFlyZoneCenter) {
            onAddNoFlyZoneCenter(point);
            return;
          }
          if (onAddWaypoint) onAddWaypoint(point);
        }}
      >
        <defs>
          <pattern id="sync-map-grid" width="24" height="24" patternUnits="userSpaceOnUse">
            <rect width="24" height="24" fill="#f9fbff" />
            <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#e5edff" strokeWidth="1" />
          </pattern>
        </defs>
        <rect x={0} y={0} width={width} height={height} fill="url(#sync-map-grid)" />

        {showCoverage
          ? baseStations.map((bs) => {
              const rWorld = coverageMap.get(bs.id);
              if (!rWorld) return null;
              const rPx = (rWorld / (world.maxX - world.minX)) * (width - pad * 2);
              const c = coverageColor(bs.status);
              return (
                <g key={`${bs.id}-cov`}>
                  <circle cx={sx(bs.x)} cy={sy(bs.y)} r={rPx} fill={c.fill} stroke={c.stroke} />
                </g>
              );
            })
          : null}

        {noFlyZones.map((z, i) => {
          const rPx = (z.radius_m / (world.maxX - world.minX)) * (width - pad * 2);
          return (
            <g key={`${z.zone_id ?? "nfz"}-${i}`}>
              <circle cx={sx(z.cx)} cy={sy(z.cy)} r={Math.max(6, rPx)} fill="rgba(240,68,56,0.08)" stroke="#f04438" strokeDasharray="4 3" />
              <text x={sx(z.cx) + 8} y={sy(z.cy) - 8} fontSize="10" fill="#b42318">{z.zone_id ?? "NFZ"}</text>
            </g>
          );
        })}

        {route.length > 0 ? <polyline points={routePath} fill="none" stroke="#2563eb" strokeWidth="2.4" /> : null}
        {route.map((p, i) => (
          <g key={`wp-${i}`}>
            <circle cx={sx(p.x)} cy={sy(p.y)} r={3.8} fill={i === 0 ? "#16a34a" : "#1d4ed8"} />
            <text x={sx(p.x) + 5} y={sy(p.y) - 5} fontSize="10" fill="#1f2937">{i + 1}</text>
          </g>
        ))}

        {baseStations.map((bs) => (
          <g key={bs.id}>
            <circle cx={sx(bs.x)} cy={sy(bs.y)} r={7} fill="#fff" stroke={statusColor(bs.status)} strokeWidth={2} />
            <text x={sx(bs.x) + 9} y={sy(bs.y) - 8} fontSize="10" fill="#101828">{bs.id}</text>
          </g>
        ))}

        {plannedPosition ? (
          <g>
            <circle cx={sx(plannedPosition.x)} cy={sy(plannedPosition.y)} r={6} fill="#f59e0b" stroke="#92400e" strokeWidth="1.4" />
            <text x={sx(plannedPosition.x) + 8} y={sy(plannedPosition.y) + 14} fontSize="10" fill="#92400e">planned</text>
          </g>
        ) : null}

        {trackedPositions.map((t) => (
          <g key={`trk-${t.id}`}>
            <circle cx={sx(t.x)} cy={sy(t.y)} r={t.id === selectedUavId ? 6 : 4.8} fill={t.id === selectedUavId ? "#f97316" : "#22c55e"} stroke="#1f2937" strokeWidth="1" />
            <text x={sx(t.x) + 7} y={sy(t.y) + 12} fontSize="10" fill="#111827">{t.id}</text>
            {t.id === selectedUavId ? <circle cx={sx(t.x)} cy={sy(t.y)} r={16} fill="none" stroke={riskColor(t.interferenceRisk)} strokeDasharray="4 4" /> : null}
          </g>
        ))}

        {plannedPosition && selectedTrack ? (
          <g>
            <line x1={sx(plannedPosition.x)} y1={sy(plannedPosition.y)} x2={sx(selectedTrack.x)} y2={sy(selectedTrack.y)} stroke="#7c3aed" strokeWidth={1.5} strokeDasharray="4 4" />
            {showInterferenceHints ? (
              <text x={(sx(plannedPosition.x) + sx(selectedTrack.x)) / 2 + 6} y={(sy(plannedPosition.y) + sy(selectedTrack.y)) / 2 - 4} fontSize="10" fill="#6d28d9">
                tracking error
              </text>
            ) : null}
          </g>
        ) : null}
      </svg>
    </div>
  );
}
