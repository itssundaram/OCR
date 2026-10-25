"""
Offline guarantee for an isolated (air-gapped) deployment.

Two layers, both on by default (OFFLINE_MODE=true):

1. Library switches: Hugging Face / transformers / timm are told never to look for or fetch anything on the hub, and
   telemetry of those libraries is off. They only read MODEL_CACHE_DIR.
2. A socket guard: any attempt by THIS process to reach an address outside private networks is refused immediately
   (and logged once per host as `offline_guard_blocked`), so a library that tries to phone home cannot leave the
   machine and cannot hang waiting for a timeout. Allowed: loopback, private / link-local ranges, unix sockets, local
   host names (no dot, *.local, *.lan, *.internal, *.localdomain), the hosts named in DATABASE_URL / ORACLE_HOST,
   REDIS_URL and OLLAMA_BASE_URL, and anything listed in OFFLINE_ALLOW_HOSTS (comma separated).

OFFLINE_MODE=false switches both layers off (for a machine that is allowed to download).
"""
from __future__ import annotations

import errno
import ipaddress
import os
import socket
from urllib.parse import urlparse

_installed = False
_orig: dict = {}
_allow: set[str] = set()
_logged: set[str] = set()

_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".localdomain", ".home.arpa")

HF_OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "ANONYMIZED_TELEMETRY": "False",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
}


def _is_local_ip(host: str) -> bool | None:
    """True/False for an IP literal, None when `host` is a name."""
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return None
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def host_allowed(host) -> bool:
    if host is None or host == "":
        return True
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("ascii", "ignore")
    host = str(host).strip().lower().strip("[]")
    local = _is_local_ip(host)
    if local is not None:
        return local or host in _allow
    return (host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES) or host in _allow)


def _block(host) -> OSError:
    h = str(host)
    if h not in _logged:
        _logged.add(h)
        try:
            from app.core.logging import get_logger
            get_logger(__name__).warning("offline_guard_blocked", host=h,
                                         note="outbound connection refused: this deployment is offline (OFFLINE_MODE)")
        except Exception:
            pass
    return OSError(errno.ENETUNREACH, f"offline mode: connection to {h} refused")


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return (urlparse(url if "://" in url else "//" + url).hostname or "").lower() or None
    except ValueError:
        return None


def install(settings) -> None:
    """Idempotent. Sets the library switches and installs the socket guard."""
    global _installed
    for k, v in HF_OFFLINE_ENV.items():
        os.environ[k] = v                                   # forced: a stray .env value must not re-enable the hub
    if _installed:
        return
    for h in (settings.ORACLE_HOST, _host_of(settings.DATABASE_URL), _host_of(settings.REDIS_URL), _host_of(settings.OLLAMA_BASE_URL)):
        if h:
            _allow.add(str(h).lower())
    for h in str(getattr(settings, "OFFLINE_ALLOW_HOSTS", "") or "").split(","):
        if h.strip():
            _allow.add(h.strip().lower())

    _orig.update(connect=socket.socket.connect, connect_ex=socket.socket.connect_ex, getaddrinfo=socket.getaddrinfo,
                 gethostbyname=socket.gethostbyname)

    def _dest(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", -1) or not isinstance(address, tuple) or not address:
            return None
        return address[0]

    def connect(self, address):
        h = _dest(self, address)
        if h is not None and not host_allowed(h):
            raise _block(h)
        return _orig["connect"](self, address)

    def connect_ex(self, address):
        h = _dest(self, address)
        if h is not None and not host_allowed(h):
            _block(h)
            return errno.ENETUNREACH
        return _orig["connect_ex"](self, address)

    def getaddrinfo(host, *a, **k):
        if not host_allowed(host):
            err = _block(host)
            raise socket.gaierror(socket.EAI_NONAME, str(err))
        return _orig["getaddrinfo"](host, *a, **k)

    def gethostbyname(host):
        if not host_allowed(host):
            err = _block(host)
            raise socket.gaierror(socket.EAI_NONAME, str(err))
        return _orig["gethostbyname"](host)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo, socket.gethostbyname = getaddrinfo, gethostbyname
    _installed = True


def uninstall() -> None:
    """For tests."""
    global _installed
    if _installed:
        socket.socket.connect, socket.socket.connect_ex = _orig["connect"], _orig["connect_ex"]
        socket.getaddrinfo, socket.gethostbyname = _orig["getaddrinfo"], _orig["gethostbyname"]
        _installed = False
        _allow.clear()
        _logged.clear()
