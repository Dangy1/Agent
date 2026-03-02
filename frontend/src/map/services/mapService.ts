export type CesiumBasemapChoice = "osm" | "nls-topo" | "nls-aerial";
export type MapViewEngine = "2D_Leaflet" | "3D_Cesium" | "2.5D_MapLibre";

export type MapProviderConfig = {
  id: CesiumBasemapChoice;
  label: string;
  supported: boolean;
  maxZoom: number;
  credit: string;
  tileUrlTemplate: string;
};

export type MapServiceConfig = {
  center: { lon: number; lat: number };
  radiusKm: number;
  defaults: { zoomMin: number; zoomMax: number; viewEngine?: MapViewEngine | string };
  providers: Partial<Record<CesiumBasemapChoice, MapProviderConfig>>;
  supportedEngines?: Array<{ id: string; label: string; dimension?: string; capabilities?: string[] }>;
  cacheStatus: { cacheRoot: string; tileCount: number; perProvider: Record<string, number> };
};

export type MapBounds = {
  west: number;
  south: number;
  east: number;
  north: number;
  pointCount?: number;
  source?: string;
  updatedAt?: string;
};

export type MapSyncPoint = {
  x: number;
  y: number;
  z?: number;
};

export type MapSyncTrack = {
  id: string;
  x: number;
  y: number;
  z?: number;
  headingDeg?: number;
  speedMps?: number;
  attachedBsId?: string;
  interferenceRisk?: string;
};

export type MapSyncNoFlyZone = {
  zone_id?: string;
  cx: number;
  cy: number;
  radius_m: number;
  z_min?: number;
  z_max?: number;
  reason?: string;
  shape?: string;
};

export type MapSyncBaseStation = {
  id: string;
  x: number;
  y: number;
  status?: string;
};

export type MapSyncCoverage = {
  bsId: string;
  radiusM: number;
};

export type MapSyncPath = {
  id: string;
  uavId?: string;
  route: MapSyncPoint[];
  color?: string;
  source?: string;
};

export type MapSyncLayers = {
  uavs: MapSyncTrack[];
  paths: MapSyncPath[];
  noFlyZones: MapSyncNoFlyZone[];
  baseStations: MapSyncBaseStation[];
  coverage: MapSyncCoverage[];
  points: Array<MapSyncPoint & { id: string }>;
};

export type MapSyncState = {
  scope: string;
  includeShared: boolean;
  sharedScope: string;
  availableScopes: string[];
  updatedAt: string;
  sync?: { agent?: string; revision?: number; updated_at?: string };
  layers: MapSyncLayers;
};

type ServicePrefetchRequest = {
  provider?: "all" | CesiumBasemapChoice;
  centerLon?: number;
  centerLat?: number;
  radiusKm?: number;
  zoomMin?: number;
  zoomMax?: number;
  forceRefresh?: boolean;
};

type MapPlotPointRequest = {
  id: string;
  lat: number;
  lon: number;
  alt?: number;
  geoidSepM?: number;
  scope?: string;
  metadata?: Record<string, unknown>;
};

const NLS_WMTS_KVP_URL = "https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts";
const NLS_WMTS_WEBMERCATOR_MAX_LEVEL = 16;

const DEFAULT_PROVIDER_META: Record<CesiumBasemapChoice, { label: string; credit: string; maxZoom: number }> = {
  osm: { label: "OpenStreetMap", credit: "OpenStreetMap contributors", maxZoom: 19 },
  "nls-topo": { label: "NLS Topographic", credit: "Maanmittauslaitos (NLS Finland)", maxZoom: 16 },
  "nls-aerial": { label: "NLS Aerial", credit: "Maanmittauslaitos (NLS Finland)", maxZoom: 16 },
};

const MAP_CONFIG_PATHS = ["/api/map/config", "/api/network/map/config"];
const MAP_PREFETCH_PATHS = ["/api/map/cache/prefetch", "/api/network/map/cache/prefetch"];
const MAP_TOGGLE_VIEW_PATHS = ["/api/map/toggle-view", "/api/network/map/toggle-view"];
const MAP_BOUNDS_PATHS = ["/api/map/bounds", "/api/network/map/bounds"];
const MAP_PLOT_POINT_PATHS = ["/api/map/plot-point", "/api/network/map/plot-point"];
const MAP_SYNC_STATE_PATHS = ["/api/map/sync/state", "/api/network/map/sync/state"];

function asRecord(x: unknown): Record<string, unknown> | null {
  return typeof x === "object" && x !== null ? (x as Record<string, unknown>) : null;
}

function asRecordList(x: unknown): Record<string, unknown>[] {
  return Array.isArray(x) ? x.filter((row) => typeof row === "object" && row !== null).map((row) => row as Record<string, unknown>) : [];
}

function toNumber(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

async function fetchWithFallback(base: string, paths: string[], init?: RequestInit): Promise<Response | null> {
  for (const path of paths) {
    try {
      const resp = await fetch(`${base}${path}`, init);
      if (resp.status === 404) continue;
      return resp;
    } catch {
      // try next fallback path
    }
  }
  return null;
}

function providerFromServiceConfig(
  id: CesiumBasemapChoice,
  raw: unknown,
  mapServiceBase: string
): MapProviderConfig {
  const base = normalizeBaseUrl(mapServiceBase);
  const rec = asRecord(raw);
  const defaults = DEFAULT_PROVIDER_META[id];
  const tileUrlTemplate = readString(rec?.tileUrlTemplate, `${base}/api/map/tiles/${id}/{z}/{x}/{y}`);
  return {
    id,
    label: readString(rec?.label, defaults.label),
    supported: Boolean(rec?.supported ?? (id === "osm")),
    maxZoom: toNumber(rec?.maxZoom, defaults.maxZoom),
    credit: readString(rec?.credit, defaults.credit),
    tileUrlTemplate,
  };
}

function supportsFromServiceConfig(
  basemap: CesiumBasemapChoice,
  mapServiceConfig: MapServiceConfig | null
): boolean | null {
  if (!mapServiceConfig) return null;
  const provider = mapServiceConfig.providers[basemap];
  return provider ? Boolean(provider.supported) : null;
}

export function normalizeBaseUrl(url: string): string {
  return String(url || "").trim().replace(/\/+$/, "");
}

export function readViteEnv(key: string): string {
  try {
    const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
    return String(env?.[key] ?? "").trim();
  } catch {
    return "";
  }
}

export function basemapLabel(basemap: CesiumBasemapChoice): string {
  return DEFAULT_PROVIDER_META[basemap].label;
}

export function isBasemapSupported(
  basemap: CesiumBasemapChoice,
  opts: { nlsApiKey: string; mapServiceBase: string; mapServiceConfig: MapServiceConfig | null }
): boolean {
  if (basemap === "osm") return true;
  const supportedByService = supportsFromServiceConfig(basemap, opts.mapServiceConfig);
  if (supportedByService !== null) return supportedByService;
  if (opts.mapServiceBase) return false;
  return Boolean(opts.nlsApiKey);
}

export async function fetchMapServiceConfig(mapServiceBase: string): Promise<MapServiceConfig | null> {
  const base = normalizeBaseUrl(mapServiceBase);
  if (!base) return null;

  const resp = await fetchWithFallback(base, MAP_CONFIG_PATHS);
  if (!resp) return null;
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) return null;

  const root = asRecord(body);
  const result = asRecord(root?.result);
  if (!result) return null;

  const providersRec = asRecord(result.providers) ?? {};
  const providers: Partial<Record<CesiumBasemapChoice, MapProviderConfig>> = {
    osm: providerFromServiceConfig("osm", providersRec.osm, base),
    "nls-topo": providerFromServiceConfig("nls-topo", providersRec["nls-topo"], base),
    "nls-aerial": providerFromServiceConfig("nls-aerial", providersRec["nls-aerial"], base),
  };

  const centerRec = asRecord(result.center);
  const defaultsRec = asRecord(result.defaults);
  const cacheStatusRec = asRecord(result.cacheStatus);
  const perProviderRec = asRecord(cacheStatusRec?.perProvider);
  const perProvider: Record<string, number> = {};
  if (perProviderRec) {
    Object.entries(perProviderRec).forEach(([k, v]) => {
      perProvider[k] = toNumber(v, 0);
    });
  }

  const supportedEngines = Array.isArray(result.supportedEngines)
    ? result.supportedEngines
        .map((row) => asRecord(row))
        .filter((row): row is Record<string, unknown> => Boolean(row))
        .map((row) => ({
          id: readString(row.id, ""),
          label: readString(row.label, ""),
          dimension: readString(row.dimension, ""),
          capabilities: Array.isArray(row.capabilities) ? row.capabilities.map((v) => String(v)) : [],
        }))
    : undefined;

  return {
    center: {
      lon: toNumber(centerRec?.lon, 24.8286),
      lat: toNumber(centerRec?.lat, 60.1866),
    },
    radiusKm: toNumber(result.radiusKm, 10),
    defaults: {
      zoomMin: toNumber(defaultsRec?.zoomMin, 11),
      zoomMax: toNumber(defaultsRec?.zoomMax, 15),
      viewEngine: readString(defaultsRec?.viewEngine, ""),
    },
    providers,
    supportedEngines,
    cacheStatus: {
      cacheRoot: readString(cacheStatusRec?.cacheRoot, ""),
      tileCount: toNumber(cacheStatusRec?.tileCount, 0),
      perProvider,
    },
  };
}

export async function prefetchMapServiceCache(
  mapServiceBase: string,
  req: ServicePrefetchRequest
): Promise<boolean> {
  const base = normalizeBaseUrl(mapServiceBase);
  if (!base) return false;

  const payload: Record<string, unknown> = {
    provider: req.provider ?? "all",
    force_refresh: Boolean(req.forceRefresh),
  };
  if (typeof req.centerLon === "number" && Number.isFinite(req.centerLon)) payload.center_lon = req.centerLon;
  if (typeof req.centerLat === "number" && Number.isFinite(req.centerLat)) payload.center_lat = req.centerLat;
  if (typeof req.radiusKm === "number" && Number.isFinite(req.radiusKm)) payload.radius_km = req.radiusKm;
  if (typeof req.zoomMin === "number" && Number.isFinite(req.zoomMin)) payload.zoom_min = req.zoomMin;
  if (typeof req.zoomMax === "number" && Number.isFinite(req.zoomMax)) payload.zoom_max = req.zoomMax;

  const resp = await fetchWithFallback(base, MAP_PREFETCH_PATHS, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return Boolean(resp?.ok);
}

export async function toggleMapView(mapServiceBase: string, engine: MapViewEngine): Promise<boolean> {
  const base = normalizeBaseUrl(mapServiceBase);
  if (!base) return false;

  const resp = await fetchWithFallback(base, MAP_TOGGLE_VIEW_PATHS, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engine }),
  });
  return Boolean(resp?.ok);
}

export async function fetchMapBounds(mapServiceBase: string): Promise<MapBounds | null> {
  const base = normalizeBaseUrl(mapServiceBase);
  if (!base) return null;

  const resp = await fetchWithFallback(base, MAP_BOUNDS_PATHS);
  if (!resp || !resp.ok) return null;

  const body = await resp.json().catch(() => ({}));
  const root = asRecord(body);
  const result = asRecord(root?.result);
  if (!result) return null;

  return {
    west: toNumber(result.west, 19),
    south: toNumber(result.south, 59),
    east: toNumber(result.east, 32),
    north: toNumber(result.north, 70.5),
    pointCount: toNumber(result.pointCount, 0),
    source: readString(result.source, ""),
    updatedAt: readString(result.updatedAt, ""),
  };
}

export async function plotMapPoint(mapServiceBase: string, req: MapPlotPointRequest): Promise<boolean> {
  const base = normalizeBaseUrl(mapServiceBase);
  if (!base) return false;

  const payload: Record<string, unknown> = {
    id: req.id,
    lat: req.lat,
    lon: req.lon,
    alt: typeof req.alt === "number" ? req.alt : 0,
    metadata: req.metadata ?? {},
  };
  if (typeof req.scope === "string" && req.scope.trim()) {
    payload.scope = req.scope.trim();
  }
  if (typeof req.geoidSepM === "number" && Number.isFinite(req.geoidSepM)) {
    payload.geoid_sep_m = req.geoidSepM;
  }

  const resp = await fetchWithFallback(base, MAP_PLOT_POINT_PATHS, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return Boolean(resp?.ok);
}

export async function fetchMapSyncState(
  mapServiceBase: string,
  opts?: { scope?: string; includeShared?: boolean }
): Promise<MapSyncState | null> {
  const base = normalizeBaseUrl(mapServiceBase);
  if (!base) return null;

  const q = new URLSearchParams();
  if (opts?.scope && String(opts.scope).trim()) q.set("scope", String(opts.scope).trim());
  if (typeof opts?.includeShared === "boolean") q.set("include_shared", String(Boolean(opts.includeShared)));
  const suffix = q.toString() ? `?${q.toString()}` : "";

  const resp = await fetchWithFallback(
    base,
    MAP_SYNC_STATE_PATHS.map((path) => `${path}${suffix}`),
  );
  if (!resp || !resp.ok) return null;

  const body = await resp.json().catch(() => ({}));
  const root = asRecord(body);
  const result = asRecord(root?.result);
  if (!result) return null;
  const layers = asRecord(result.layers) ?? {};

  const uavs = asRecordList(layers.uavs).map((row) => ({
    id: readString(row.id, ""),
    x: toNumber(row.x, toNumber(row.lon, 0)),
    y: toNumber(row.y, toNumber(row.lat, 0)),
    z: toNumber(row.z, toNumber(row.altM, 0)),
    headingDeg: toNumber(row.headingDeg, 0),
    speedMps: toNumber(row.speedMps, 0),
    attachedBsId: readString(row.attachedBsId, ""),
    interferenceRisk: readString(row.interferenceRisk, ""),
  }));

  const paths = asRecordList(layers.paths).map((row) => ({
    id: readString(row.id, ""),
    uavId: readString(row.uavId, ""),
    route: asRecordList(row.route).map((pt) => ({
      x: toNumber(pt.x, toNumber(pt.lon, 0)),
      y: toNumber(pt.y, toNumber(pt.lat, 0)),
      z: toNumber(pt.z, toNumber(pt.altM, 0)),
    })),
    color: readString(row.color, ""),
    source: readString(row.source, ""),
  }));

  const noFlyZones = asRecordList(layers.noFlyZones).map((row) => ({
    zone_id: readString(row.zone_id, readString(row.id, "")),
    cx: toNumber(row.cx, toNumber(row.lon, toNumber(row.x, 0))),
    cy: toNumber(row.cy, toNumber(row.lat, toNumber(row.y, 0))),
    radius_m: toNumber(row.radius_m, toNumber(row.radiusM, 0)),
    z_min: toNumber(row.z_min, 0),
    z_max: toNumber(row.z_max, 120),
    reason: readString(row.reason, ""),
    shape: readString(row.shape, "circle"),
  }));

  const baseStations = asRecordList(layers.baseStations).map((row) => ({
    id: readString(row.id, ""),
    x: toNumber(row.x, toNumber(row.lon, 0)),
    y: toNumber(row.y, toNumber(row.lat, 0)),
    status: readString(row.status, ""),
  }));

  const coverage = asRecordList(layers.coverage).map((row) => ({
    bsId: readString(row.bsId, readString(row.bs_id, "")),
    radiusM: toNumber(row.radiusM, toNumber(row.radius_m, 0)),
  }));

  const points = asRecordList(layers.points).map((row) => ({
    id: readString(row.id, ""),
    x: toNumber(row.x, toNumber(row.lon, 0)),
    y: toNumber(row.y, toNumber(row.lat, 0)),
    z: toNumber(row.z, toNumber(row.altM, 0)),
  }));

  return {
    scope: readString(result.scope, "shared"),
    includeShared: String(result.includeShared ?? "true") !== "false",
    sharedScope: readString(result.sharedScope, "shared"),
    availableScopes: Array.isArray(result.availableScopes) ? result.availableScopes.map((v) => String(v)) : [],
    updatedAt: readString(result.updatedAt, ""),
    sync: asRecord(result.sync) ?? undefined,
    layers: {
      uavs: uavs.filter((row) => row.id),
      paths: paths.filter((row) => row.id),
      noFlyZones,
      baseStations: baseStations.filter((row) => row.id),
      coverage: coverage.filter((row) => row.bsId),
      points: points.filter((row) => row.id),
    },
  };
}

export function createCesiumImageryProvider(
  Cesium: any,
  basemap: CesiumBasemapChoice,
  opts: {
    nlsApiKey: string;
    mapServiceBase: string;
    mapServiceConfig: MapServiceConfig | null;
  }
): any {
  const mapServiceBase = normalizeBaseUrl(opts.mapServiceBase);
  const serviceProvider = mapServiceBase
    ? (opts.mapServiceConfig?.providers[basemap] ?? providerFromServiceConfig(basemap, null, mapServiceBase))
    : null;

  if (mapServiceBase && serviceProvider && serviceProvider.supported) {
    return new Cesium.UrlTemplateImageryProvider({
      url: serviceProvider.tileUrlTemplate,
      credit: serviceProvider.credit,
      maximumLevel: serviceProvider.maxZoom,
    });
  }

  if (basemap === "nls-topo" && opts.nlsApiKey) {
    return new Cesium.WebMapTileServiceImageryProvider({
      url: `${NLS_WMTS_KVP_URL}?api-key=${encodeURIComponent(opts.nlsApiKey)}`,
      layer: "taustakartta",
      style: "default",
      format: "image/png",
      tileMatrixSetID: "WGS84_Pseudo-Mercator",
      tilingScheme: new Cesium.WebMercatorTilingScheme(),
      credit: DEFAULT_PROVIDER_META["nls-topo"].credit,
      maximumLevel: NLS_WMTS_WEBMERCATOR_MAX_LEVEL,
    });
  }
  if (basemap === "nls-aerial" && opts.nlsApiKey) {
    return new Cesium.WebMapTileServiceImageryProvider({
      url: `${NLS_WMTS_KVP_URL}?api-key=${encodeURIComponent(opts.nlsApiKey)}`,
      layer: "ortokuva",
      style: "default",
      format: "image/jpeg",
      tileMatrixSetID: "WGS84_Pseudo-Mercator",
      tilingScheme: new Cesium.WebMercatorTilingScheme(),
      credit: DEFAULT_PROVIDER_META["nls-aerial"].credit,
      maximumLevel: NLS_WMTS_WEBMERCATOR_MAX_LEVEL,
    });
  }

  return new Cesium.OpenStreetMapImageryProvider({
    url: "https://tile.openstreetmap.org/",
    credit: DEFAULT_PROVIDER_META.osm.credit,
  });
}
