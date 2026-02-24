import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("OLLAMA_HOST", "127.0.0.1").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", f"http://{HOST}:11434").strip()
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:latest").strip()

MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
MCP_SERVER_CMD = os.getenv("MCP_SERVER_CMD", sys.executable).strip()
_DEFAULT_SUITES_SERVER = "/home/dang/flexric/build/examples/xApp/python3/mcp_flexric_suites.py"
MCP_SERVER_ARGS = os.getenv("MCP_SERVER_ARGS", _DEFAULT_SUITES_SERVER).strip()
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "flexric-suites").strip() or "flexric-suites"
MCP_HTTP_URL = os.getenv("MCP_HTTP_URL", os.getenv("MCP_PROXY_URL", "http://127.0.0.1:8000/mcp")).strip()
MCP_HTTP_AUTH_TOKEN = os.getenv("MCP_HTTP_AUTH_TOKEN", os.getenv("MCP_PROXY_AUTH_TOKEN", "")).strip()
MCP_CALL_TIMEOUT_S = int(os.getenv("MCP_CALL_TIMEOUT_S", "120").strip() or "120")

_BACKEND_DIR = Path(__file__).resolve().parents[2]
AUDIT_LOG_PATH = str(_BACKEND_DIR / "agentRIC_audit.log")
