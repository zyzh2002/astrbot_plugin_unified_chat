"""Full-boot harness: runs a real AstrBot subprocess against a sandbox.

Black-box design: the test process only talks HTTP to the booted dashboard
and reads the sandbox SQLite DB — no astrbot imports here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PKG = "astrbot_plugin_unified_chat"
READY_TIMEOUT_S = 120.0
CHAT_TIMEOUT_S = 90.0


DASHBOARD_USER = "tester"
DASHBOARD_PASSWORD = "Fullboot123"


def _password_hashes(raw: str) -> dict:
    """Precompute dashboard password hashes (MD5 + PBKDF2) for cmd_config."""
    import hashlib
    import secrets

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", raw.encode(), bytes.fromhex(salt), 600_000
    ).hex()
    return {
        "pbkdf2_password": f"pbkdf2_sha256$600000${salt}${digest}",
        "password": hashlib.md5(raw.encode()).hexdigest(),
        "password_storage_upgraded": True,
    }


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AstrBotHarness:
    def __init__(self, sandbox: Path, port: int, proc: subprocess.Popen):
        self.sandbox = sandbox
        self.port = port
        self.proc = proc
        self._jwt: str | None = None
        self._api_key: str | None = None

    # ---- lifecycle -------------------------------------------------

    @classmethod
    def start(cls, plugin_config: dict | None = None) -> AstrBotHarness:
        from .mock_llm import MockLLMServer

        mock = MockLLMServer()
        mock.start()

        root = Path(tempfile.mkdtemp(prefix="uc-fullboot-"))
        (root / ".astrbot").write_text("", encoding="utf-8")
        data_dir = root / "data"
        for sub in ("config", "plugin_data", "temp", "shared"):
            (data_dir / sub).mkdir(parents=True, exist_ok=True)
        plugin_dst = data_dir / "plugins" / PLUGIN_PKG
        plugin_dst.mkdir(parents=True)
        for name in ("main.py", "metadata.yaml", "_conf_schema.json"):
            shutil.copy2(REPO_ROOT / name, plugin_dst / name)
        shutil.copytree(
            REPO_ROOT / "unified_chat",
            plugin_dst / "unified_chat",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

        dash_port = _free_port()
        config = {
            "dashboard": {
                "enable": True,
                "username": DASHBOARD_USER,
                **_password_hashes(DASHBOARD_PASSWORD),
                "jwt_secret": "fullboot-secret-0123456789abcdef0123456789abcdef",
                "host": "127.0.0.1",
                "port": dash_port,
            },
            "provider": [
                {
                    "id": "mock-llm",
                    "provider": "openai",
                    "type": "openai_chat_completion",
                    "provider_type": "chat_completion",
                    "enable": True,
                    "key": ["sk-mock"],
                    "api_base": mock.api_base,
                    "timeout": 60,
                }
            ],
            "provider_settings": {
                "enable": True,
                "default_provider_id": "mock-llm",
                "provider_pool": ["*"],
            },
            "platform": [],
        }
        (data_dir / "cmd_config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if plugin_config:
            cfg_dir = data_dir / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (
                cfg_dir / f"{PLUGIN_PKG}_config.json"
            ).write_text(
                json.dumps(plugin_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        (root / "unified_chat_mock_port.txt").write_text(str(mock.port))

        exe_dir = Path(sys.executable).parent
        exe = exe_dir / ("astrbot.exe" if os.name == "nt" else "astrbot")
        log_fh = open(root / "boot.log", "wb")  # noqa: SIM115
        proc = subprocess.Popen(  # noqa: S603
            [str(exe), "run"],
            cwd=str(root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        harness = cls(root, dash_port, proc)
        harness._mock = mock
        harness._log_fh = log_fh
        try:
            harness._wait_ready()
        except Exception:
            harness.stop()
            raise
        return harness

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        url = f"http://127.0.0.1:{self.port}/api/v1/auth/login"
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"astrbot exited early:\n{self.boot_log()[-2000:]}")
            try:
                request = urllib.request.Request(url, method="POST")
                urllib.request.urlopen(request, timeout=3)  # noqa: S310
                return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return
            except Exception:
                pass
            time.sleep(1.0)
        raise TimeoutError(f"astrbot not ready in {READY_TIMEOUT_S}s\n{self.boot_log()[-2000:]}")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self._log_fh.close()
        self._mock.stop()
        shutil.rmtree(self.sandbox, ignore_errors=True)

    # ---- helpers ---------------------------------------------------

    @property
    def db_path(self) -> Path:
        return (
            self.sandbox
            / "data"
            / "plugin_data"
            / "astrbot_plugin_unified_chat"
            / "unified_chat.db"
        )

    @property
    def mock(self):
        return self._mock

    def boot_log(self) -> str:
        try:
            return (self.sandbox / "boot.log").read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _request(self, path: str, payload: dict, token: str | None = None) -> dict:
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(  # noqa: S310
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=CHAT_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"{path} -> HTTP {exc.code}: {detail}") from exc

    def login(self) -> str:
        if self._jwt is None:
            data = self._request(
                "/api/v1/auth/login",
                {"username": DASHBOARD_USER, "password": DASHBOARD_PASSWORD},
            )
            payload = data.get("data") or {}
            self._jwt = payload.get("token") or payload.get("access_token") or ""
            if not self._jwt:
                raise RuntimeError(f"login failed: {data}")
        return self._jwt

    def api_key(self) -> str:
        if self._api_key is None:
            data = self._request("/api/v1/api-keys", {"name": "fullboot"}, self.login())
            payload = data.get("data") or {}
            self._api_key = payload.get("api_key") if isinstance(payload, dict) else None
            if not self._api_key:
                raise RuntimeError(f"api key creation failed: {data}")
        return self._api_key

    def chat(self, text: str, username: str = "tester") -> str:
        """Send a webchat message; returns concatenated assistant reply."""
        payload = {
            "message": [{"type": "plain", "text": text}],
            "username": username,
            "enable_streaming": False,
        }
        body = json.dumps(payload).encode()
        request = urllib.request.Request(  # noqa: S310
            f"http://127.0.0.1:{self.port}/api/v1/chat",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key()}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=CHAT_TIMEOUT_S) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", "replace")
        if "event-stream" in content_type:
            return _parse_sse(raw)
        try:
            return _collect_reply(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return raw

    def sqlite_query(self, sql: str) -> list[tuple]:
        import sqlite3

        if not self.db_path.exists():
            return []
        con = sqlite3.connect(self.db_path)
        try:
            return list(con.execute(sql))
        finally:
            con.close()


def _parse_sse(raw: str) -> str:
    parts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            parts.append(data)
            continue
        if not isinstance(event, dict):
            continue
        # astrbot webchat envelope: {"type": "plain", "data": "<text>"}
        if "type" in event and "data" in event:
            etype = str(event.get("type"))
            payload = event.get("data")
            if etype in ("plain", "text", "chunk") and isinstance(payload, str):
                parts.append(payload)
            continue
        choices = event.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or choices[0].get("message") or {}
            parts.append(str(delta.get("content") or ""))
    return "".join(parts)


def _collect_reply(response) -> str:
    if isinstance(response, dict):
        payload = response.get("data")
        if isinstance(payload, dict):
            for key in ("reply", "text", "completion", "message"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            chain = payload.get("chain") or payload.get("message_chain")
            if isinstance(chain, list):
                return "".join(
                    str(part.get("text") or "") for part in chain if isinstance(part, dict)
                )
        if isinstance(payload, list):
            parts = []
            for item in payload:
                if isinstance(item, dict):
                    delta = item.get("delta") or item.get("choices")
                    parts.append(json.dumps(delta or item, ensure_ascii=False))
            return "".join(parts)
        return json.dumps(response, ensure_ascii=False)
    return str(response)


def wait_until(predicate, timeout_s: float = 20.0, interval: float = 0.5):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return None
