export type SharedPageState = {
  uavId: string;
  airspace: string;
  uavApiBase: string;
  utmApiBase: string;
  networkApiBase: string;
  revision: number;
  updatedAt: string;
};

const STORAGE_KEY = "multi_agent_console_page_sync_v1";
const EVENT_NAME = "multi-agent-page-sync";

function defaults(): SharedPageState {
  return {
    uavId: "uav-1",
    airspace: "sector-A3",
    uavApiBase: "http://127.0.0.1:8020",
    utmApiBase: "http://127.0.0.1:8021",
    networkApiBase: "http://127.0.0.1:8022",
    revision: 0,
    updatedAt: new Date().toISOString(),
  };
}

export function getSharedPageState(): SharedPageState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaults();
    const parsed = JSON.parse(raw) as Partial<SharedPageState>;
    return {
      ...defaults(),
      ...parsed,
      revision: Number(parsed.revision ?? 0) || 0,
      updatedAt: typeof parsed.updatedAt === "string" ? parsed.updatedAt : new Date().toISOString(),
    };
  } catch {
    return defaults();
  }
}

function writeSharedPageState(next: SharedPageState) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: next }));
}

export function patchSharedPageState(patch: Partial<SharedPageState>) {
  const curr = getSharedPageState();
  const next: SharedPageState = {
    ...curr,
    ...patch,
    updatedAt: new Date().toISOString(),
  };
  writeSharedPageState(next);
  return next;
}

export function bumpSharedRevision() {
  const curr = getSharedPageState();
  const next = {
    ...curr,
    revision: curr.revision + 1,
    updatedAt: new Date().toISOString(),
  };
  writeSharedPageState(next);
  return next;
}

export function subscribeSharedPageState(listener: (state: SharedPageState) => void): () => void {
  const onCustom = (e: Event) => {
    const detail = (e as CustomEvent<SharedPageState>).detail;
    if (detail) listener(detail);
  };
  const onStorage = (e: StorageEvent) => {
    if (e.key !== STORAGE_KEY) return;
    listener(getSharedPageState());
  };
  window.addEventListener(EVENT_NAME, onCustom as EventListener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT_NAME, onCustom as EventListener);
    window.removeEventListener("storage", onStorage);
  };
}

