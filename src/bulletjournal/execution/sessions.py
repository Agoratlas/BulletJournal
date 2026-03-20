from __future__ import annotations

import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from subprocess import Popen

from bulletjournal.execution.marimo_adapter import launch_editor


@dataclass(slots=True)
class MarimoSession:
    session_id: str
    node_id: str
    run_id: str
    notebook_path: str
    host: str
    port: int
    base_url: str
    public_url: str
    process: Popen[str]

    @property
    def url(self) -> str:
        return self.public_url


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, MarimoSession] = {}

    def create(
        self,
        node_id: str,
        notebook_path: Path,
        *,
        run_id: str,
        public_base_url: str,
        runtime_env: dict[str, str] | None = None,
    ) -> MarimoSession:
        existing = self.get_by_node(node_id)
        if existing is not None and existing.process.poll() is None:
            return existing
        session_id = secrets.token_hex(8)
        port = _free_port()
        base_url = f'/api/v1/edit/sessions/{session_id}'
        public_url = f'{public_base_url}{base_url}'
        process = launch_editor(
            notebook_path,
            host='127.0.0.1',
            port=port,
            base_url=base_url,
            environment=runtime_env,
        )
        session = MarimoSession(
            session_id=session_id,
            node_id=node_id,
            run_id=run_id,
            notebook_path=str(notebook_path),
            host='127.0.0.1',
            port=port,
            base_url=base_url,
            public_url=public_url,
            process=process,
        )
        self._sessions[session_id] = session
        return session

    def list(self) -> list[MarimoSession]:
        self._cleanup()
        return sorted(self._sessions.values(), key=lambda item: item.session_id)

    def get(self, session_id: str) -> MarimoSession | None:
        self._cleanup()
        return self._sessions.get(session_id)

    def get_by_node(self, node_id: str) -> MarimoSession | None:
        self._cleanup()
        for session in self._sessions.values():
            if session.node_id == node_id:
                return session
        return None

    def stop(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        if session.process.poll() is None:
            session.process.terminate()

    def stop_all(self) -> None:
        for session_id in list(self._sessions):
            self.stop(session_id)

    def is_ready(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        if session.process.poll() is not None:
            return False
        with socket.socket() as sock:
            sock.settimeout(0.2)
            return sock.connect_ex((session.host, session.port)) == 0

    def _cleanup(self) -> None:
        dead = [session_id for session_id, session in self._sessions.items() if session.process.poll() is not None]
        for session_id in dead:
            self._sessions.pop(session_id, None)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])
