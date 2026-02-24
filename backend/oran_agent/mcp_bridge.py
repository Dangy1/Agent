import contextlib
import inspect
import os
import queue
import threading
from typing import Any, Dict, List, Optional

import anyio

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from .config.runtime_mcp import build_server_cmd_list, get_mcp_runtime_config
from .config.settings import MCP_CALL_TIMEOUT_S


def _is_sync_invocation_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return "does not support sync invocation" in msg


class MCPBridge:
    """
    Spawns MCP stdio server and loads tools once (cached).
    Uses AnyIO BlockingPortal to call async tool.ainvoke() from sync code.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._portal_cm: Optional[contextlib.AbstractContextManager] = None
        self._portal: Optional[anyio.from_thread.BlockingPortal] = None
        self._client: Optional[MultiServerMCPClient] = None
        self._tools_cache: Optional[list] = None
        self._tool_map_cache: Optional[Dict[str, Any]] = None

    def reset(self) -> None:
        with self._lock:
            self._client = None
            self._tools_cache = None
            self._tool_map_cache = None
            portal = self._portal
            portal_cm = self._portal_cm
            self._portal = None
            self._portal_cm = None
        if portal_cm is not None:
            try:
                portal_cm.__exit__(None, None, None)
            except Exception:
                pass

    def _effective_transport(self) -> str:
        cfg = get_mcp_runtime_config()
        transport = str(cfg.get("transport", "stdio")).lower()
        http_url = str(cfg.get("http_url", "") or "")
        if transport in ("http", "stdio"):
            return transport
        if transport != "auto":
            raise RuntimeError("MCP_TRANSPORT must be one of: auto, http, stdio")
        if http_url and ("MCP_HTTP_URL" in os.environ or "MCP_PROXY_URL" in os.environ):
            return "http"
        return "stdio"

    def _build_client(self) -> MultiServerMCPClient:
        cfg = get_mcp_runtime_config()
        transport = self._effective_transport()
        server_name = str(cfg["server_name"])
        if transport == "http":
            http_url = str(cfg.get("http_url", "") or "")
            if not http_url:
                raise RuntimeError("MCP_HTTP_URL is empty.")
            headers: Dict[str, str] = {}
            auth = str(cfg.get("http_auth_token", "") or "")
            if auth:
                headers["Authorization"] = f"Bearer {auth}"
            server_cfg = {
                server_name: {
                    "transport": "http",
                    "url": http_url,
                    "headers": headers,
                }
            }
            return MultiServerMCPClient(server_cfg)

        cmd = build_server_cmd_list()
        if len(cmd) < 2:
            raise RuntimeError("MCP_SERVER_ARGS is empty. Set it to your MCP server script path.")
        server_cfg = {
            server_name: {
                "transport": "stdio",
                "command": cmd[0],
                "args": cmd[1:],
            }
        }
        return MultiServerMCPClient(server_cfg)

    def _ensure_client(self) -> MultiServerMCPClient:
        with self._lock:
            if self._client is None:
                self._client = self._build_client()
            return self._client

    def _ensure_portal(self) -> None:
        with self._lock:
            if self._portal is not None:
                return
            self._portal_cm = anyio.from_thread.start_blocking_portal()
            self._portal = self._portal_cm.__enter__()

    def _portal_call_safe(self, fn, *args):
        self._ensure_portal()
        assert self._portal is not None
        q: "queue.Queue[tuple[bool, Any]]" = queue.Queue(maxsize=1)

        def _worker():
            try:
                result = self._portal.call(fn, *args)  # type: ignore[union-attr]
                q.put((True, result))
            except Exception as exc:
                q.put((False, exc))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        try:
            ok, payload = q.get(timeout=max(5, MCP_CALL_TIMEOUT_S))
        except queue.Empty as e:
            raise TimeoutError(
                f"MCP call timed out after {MCP_CALL_TIMEOUT_S}s. "
                "The MCP server may be hung or the operation is taking too long."
            ) from e
        t.join(timeout=0.1)
        if ok:
            return payload
        raise payload

    async def _async_load_tools(self) -> list:
        client = self._ensure_client()
        cfg = get_mcp_runtime_config()
        server_name = str(cfg["server_name"])

        if hasattr(client, "get_tools"):
            return await client.get_tools()

        if hasattr(client, "session"):
            async with client.session(server_name) as session:
                return await load_mcp_tools(session)

        raise RuntimeError(
            "MultiServerMCPClient has neither get_tools() nor session(). "
            "Upgrade: pip install -U langchain-mcp-adapters"
        )

    def load_tools(self) -> list:
        self._ensure_portal()
        assert self._portal is not None
        with self._lock:
            if self._tools_cache is not None:
                return self._tools_cache
        tools = self._portal_call_safe(self._async_load_tools)
        with self._lock:
            self._tools_cache = tools
            self._tool_map_cache = None
        return tools

    def list_tools_meta(self) -> List[dict]:
        tools = self.load_tools()
        out: List[dict] = []
        for t in tools:
            out.append(
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "args_schema": str(getattr(t, "args_schema", "")) if getattr(t, "args_schema", None) else None,
                }
            )
        return out

    def _tool_map(self) -> Dict[str, Any]:
        with self._lock:
            if self._tool_map_cache is not None:
                return self._tool_map_cache
        tools = self.load_tools()
        tmap = {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}
        with self._lock:
            self._tool_map_cache = tmap
        return tmap

    async def _async_call(self, tool_name: str, args: Dict[str, Any]) -> Any:
        tmap = self._tool_map()
        if tool_name not in tmap:
            raise ValueError(f"Unknown MCP tool '{tool_name}'. Call mcp_list_tools first.")

        t = tmap[tool_name]
        if hasattr(t, "ainvoke"):
            try:
                return await t.ainvoke(args)
            except Exception as e:
                if not _is_sync_invocation_error(e):
                    raise

        cor = getattr(t, "coroutine", None)
        if callable(cor):
            try:
                sig = inspect.signature(cor)
                if len(sig.parameters) == 1:
                    return await cor(args)
                return await cor(**args)
            except TypeError:
                return await cor(args)

        if hasattr(t, "invoke"):
            try:
                return t.invoke(args)
            except NotImplementedError as e:
                raise RuntimeError(
                    f"Tool '{tool_name}' is async-only and cannot be invoked sync. "
                    "Use adapter path that supports ainvoke()."
                ) from e

        raise RuntimeError(f"Tool object has neither ainvoke() nor invoke(): {type(t)}")

    def call(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        return self._portal_call_safe(self._async_call, tool_name, args or {})
