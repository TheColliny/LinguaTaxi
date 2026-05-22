"""Shared fixtures for LinguaTaxi integration tests."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = REPO_ROOT / "server.py"

DISPLAY_PORT = 3000
OPERATOR_PORT = 3001
EXTENDED_PORT = 3002
DICTATION_PORT = 3005

ALL_PORTS = [DISPLAY_PORT, OPERATOR_PORT, EXTENDED_PORT, DICTATION_PORT]


def _port_open(port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_ports(ports: list[int], timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(_port_open(p) for p in ports):
            return True
        time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def server_process() -> Generator[subprocess.Popen, None, None]:
    """Start the LinguaTaxi server for integration testing.

    Starts as a subprocess with vosk backend (CPU-only, no GPU required).
    Yields the Popen object and tears down on session end.
    """
    for p in ALL_PORTS:
        if _port_open(p, timeout=0.3):
            pytest.skip(f"Port {p} already in use — is a server already running?")

    python = sys.executable
    cmd = [python, str(SERVER_PY), "--backend", "vosk"]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        text=True,
    )

    if not _wait_for_ports(ALL_PORTS, timeout=30):
        out = proc.stdout.read() if proc.stdout else ""
        proc.kill()
        pytest.fail(f"Server did not start within 30s.\nOutput:\n{out}")

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
