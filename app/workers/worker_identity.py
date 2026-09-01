import os
import socket
from uuid import uuid4

_worker_id: str | None = None


def build_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


def get_worker_id() -> str:
    global _worker_id
    if _worker_id is None:
        _worker_id = build_worker_id()
    return _worker_id
