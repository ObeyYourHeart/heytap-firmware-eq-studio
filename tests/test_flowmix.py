import json
import struct
import urllib.request

import pytest

from heytap_eq.apk_config import dex_strings, measurement_authorization
from heytap_eq.flowmix import FlowmixClient, SafeRedirect, endpoint, index_entries


def test_path_encoding_and_host_restriction():
    assert endpoint("https://fr-api.ykload.cn", "sources", "A/B 中文") == "https://fr-api.ykload.cn/api/sources/A%2FB%20%E4%B8%AD%E6%96%87"
    for base in ("http://fr-api.ykload.cn", "https://example.com", "https://fr-api.ykload.cn/other"):
        with pytest.raises(ValueError):
            endpoint(base, "sources")


def test_redirect_never_transfers_auth_to_other_hosts():
    req = urllib.request.Request("https://fr-api.ykload.cn/api/sources", headers={"Authorization": "Bearer synthetic"})
    for url in ("https://fr-api.ykload.com/api/sources", "http://fr-api.ykload.cn/api/sources"):
        with pytest.raises(ValueError):
            SafeRedirect().redirect_request(req, None, 302, "Found", {}, url)


def test_dex_bounds_and_unknown_apk(tmp_path):
    blob = bytearray(120)
    blob[:4] = b"dex\n"
    struct.pack_into("<II", blob, 56, 1, 112)
    struct.pack_into("<I", blob, 112, 116)
    blob[116:120] = b"\x02Hi\0"
    assert list(dex_strings(bytes(blob))) == ["Hi"]
    struct.pack_into("<I", blob, 112, 1000)
    with pytest.raises(ValueError):
        list(dex_strings(bytes(blob)))
    path = tmp_path/"unknown.apk"
    path.write_bytes(b"not the studied apk")
    with pytest.raises(ValueError):
        measurement_authorization(path)


def test_observed_index_shapes_and_file_identifier():
    sources = index_entries("sources", {"success": True, "count": 1,
                              "data": [{"name": "synthetic", "displayName": "Synthetic"}]})
    assert sources == [{"name": "synthetic", "display": "Synthetic"}]
    assert index_entries("brands", {"success": True, "data": ["A/B"]})[0]["name"] == "A/B"
    phones = index_entries("headphones", {"success": True, "data": [{"fileName": "File_ID", "originalName": "Display Name"}]})
    assert phones[0]["name"] == "File_ID"
    targets = index_entries("targets", {"success": True, "data":
                           [{"fileName": "Target_ID", "name": "Target display"}]})
    assert targets == [{"name": "Target_ID", "display": "Target display"}]
    with pytest.raises(ValueError):
        index_entries("sources", {"success": True, "count": 2, "data": []})


def test_network_failure_uses_sanitized_numeric_cache(monkeypatch, tmp_path):
    from heytap_eq import flowmix
    payload = {"success": True, "data": {"sourceName": "synthetic", "lastUpdated": "2026-10-08",
               "frequencyData": {"Original": {"title": "Synthetic", "frequencies": [20, 1000],
               "spl_values": [80, 90], "measurement_id": "fixture-id", "content_version": "fixture-version"}}},
               "untrusted_header_echo": "secret"}
    monkeypatch.setattr(flowmix, "request_response", lambda *a: {"http_status": 200, "body": json.dumps(payload)})
    client = FlowmixClient("Bearer secret", tmp_path)
    first = client.measurements("source", "brand", "File_ID")
    assert first[0].measurement_id == "fixture-id" and not client.from_cache
    assert "secret" not in next(tmp_path.glob('*.json')).read_text()
    def failed(*a):
        raise OSError("synthetic offline")
    monkeypatch.setattr(flowmix, "request_response", failed)
    assert client.measurements("source", "brand", "File_ID") == first and client.from_cache


def test_device_matching_searches_dynamic_sources_without_a_fixed_default(monkeypatch):
    client = FlowmixClient()
    monkeypatch.setattr(client, "brands", lambda source: [{"name": "Brand", "display": "Brand"}])
    monkeypatch.setattr(client, "headphones", lambda source, brand:
                        [{"name": "Brand_Model", "display": "Brand Model"}] if source == "new-source" else [])
    result = client.match_device({"brand": ["Brand"], "model": ["Brand Model"]},
                                 [{"name": "old-source"}, {"name": "new-source"}])
    assert result == {"source": "new-source", "brand": "Brand", "headphone": "Brand_Model"}


@pytest.mark.parametrize("packager", ["pyinstaller", "nuitka"])
def test_independent_api_profile_loads_beside_frozen_executable(monkeypatch, tmp_path, packager):
    from heytap_eq import runtime, service_profile
    monkeypatch.delenv("HEYTAP_FLOWMIX_AUTHORIZATION", raising=False)
    monkeypatch.setattr(service_profile, "__file__", str(tmp_path/"_internal"/"heytap_eq"/"service_profile.py"))
    monkeypatch.setattr(service_profile.sys, "frozen", packager == "pyinstaller", raising=False)
    if packager == "nuitka":
        monkeypatch.setattr(runtime, "__compiled__", object(), raising=False)
    monkeypatch.setattr(service_profile.sys, "executable", str(tmp_path/"Studio.exe"))
    fallback = service_profile.builtin_authorization()
    if fallback is not None:
        service_profile.validate_authorization(fallback)
    service_profile.prepare_profile(tmp_path, "Bearer synthetic")
    assert service_profile.builtin_authorization() == "Bearer synthetic"
    monkeypatch.setenv("HEYTAP_FLOWMIX_AUTHORIZATION", "Bearer synthetic environment")
    assert service_profile.builtin_authorization() == "Bearer synthetic environment"
    monkeypatch.delenv("HEYTAP_FLOWMIX_AUTHORIZATION")
    assert service_profile.builtin_authorization() == "Bearer synthetic"
    (tmp_path/service_profile.PROFILE_NAME).unlink()
    assert service_profile.builtin_authorization() == fallback
    assert not list(tmp_path.glob("*.apk"))
    with pytest.raises(ValueError):
        service_profile.prepare_profile(tmp_path, "Bearer synthetic\ninvalid")
