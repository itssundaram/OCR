"""Nothing leaves private networks in offline mode; models are never looked up on the hub."""
import os
import socket

import pytest

from app.core import offline_guard as og
from app.core.config import settings


@pytest.fixture(autouse=True)
def _guard_on():
    if not settings.OFFLINE_MODE:
        pytest.skip("OFFLINE_MODE is off in this environment")
    og.install(settings)


def test_public_addresses_and_names_are_refused():
    assert not og.host_allowed("8.8.8.8")
    assert not og.host_allowed("huggingface.co")
    assert not og.host_allowed("fonts.googleapis.com")
    assert not og.host_allowed("2606:4700:4700::1111")


def test_private_and_local_destinations_are_allowed():
    for h in ("127.0.0.1", "localhost", "10.0.0.5", "192.168.1.20", "172.16.4.4", "::1", "oracle-db", "git.lan", "box.local"):
        assert og.host_allowed(h), h


def test_hosts_from_the_settings_urls_are_allowed():
    assert og.host_allowed(settings.ORACLE_HOST)
    assert og.host_allowed("localhost")


def test_a_connection_to_a_public_address_is_refused_before_it_leaves():
    s = socket.socket()
    s.settimeout(2)
    with pytest.raises(OSError) as e:
        s.connect(("93.184.216.34", 80))
    assert "offline mode" in str(e.value)
    s.close()
    assert s.connect_ex is not None


def test_a_name_lookup_for_a_public_host_is_refused():
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("huggingface.co", 443)
    with pytest.raises(socket.gaierror):
        socket.gethostbyname("pypi.org")


def test_a_local_connection_still_works():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.create_connection(srv.getsockname(), timeout=2)
    c.close()
    srv.close()


def test_the_hub_switches_are_forced_on():
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        assert os.environ.get(k) == "1"


def test_the_table_model_is_loaded_without_fetching_a_backbone():
    import inspect

    from app.ocr import tatr_engine
    assert "use_pretrained_backbone=False" in inspect.getsource(tatr_engine)
