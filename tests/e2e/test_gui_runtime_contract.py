"""Реальні HTTP-перевірки запущеного `gui` (WP-00 PR3; §7.7, §8, §13, §16.1 п.14).

Статичні тести `tests/unit/test_compose_config.py` читають `nginx.conf` як текст і не можуть
довести, що заголовок справді доїхав до клієнта: у nginx `add_header` у `location` мовчки
скидає весь успадкований набір заголовків `server`, а `return` у rewrite-фазі виконується
ДО `auth_request`. Обидві пастки видно лише на живому контейнері, тому цей файл б'є по
`http://127.0.0.1:${GUI_PORT}` і пропускається, якщо стек не піднято.

Запуск:

    COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait
    uv run pytest tests/e2e/test_gui_runtime_contract.py

Мережа: лише loopback (маркер `e2e` → `allow_hosts` у tests/conftest.py).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import pytest

GUI_PORT = os.environ.get("GUI_PORT", "80")
BASE_URL = f"http://127.0.0.1:{GUI_PORT}"
HEALTH_PATH = "/api/v1/health/components"

# §13: набір, який має віддаватись на КОЖНІЙ відповіді gui (`always` у nginx.conf).
REQUIRED_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
# Директиви CSP, послаблення яких ламає модель §13 (inline-скрипт, CDN, clickjacking).
REQUIRED_CSP: dict[str, str] = {
    "default-src": "'none'",
    "script-src": "'self'",
    "connect-src": "'self'",
    "frame-ancestors": "'none'",
    "base-uri": "'none'",
    "object-src": "'none'",
    "form-action": "'none'",
}


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: str

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Не переходити за Location: саме його значення перевіряє тест на Host injection
    (а перехід на підроблений домен був би ще й спробою реальної мережі)."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(
    path: str,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> Response:
    # HTTP request-line має бути ASCII: кирилицю у шляху (SPA-маршрути) percent-кодуємо,
    # `/`, `?`, `&`, `=` і `.` лишаємо як є, щоб не зламати нормалізацію, яку перевіряємо.
    encoded = urllib.parse.quote(path, safe="/?&=.%")
    request = urllib.request.Request(  # noqa: S310 — loopback
        f"{BASE_URL}{encoded}", method="GET", headers=headers or {}
    )
    opener = urllib.request.urlopen if follow_redirects else _NO_REDIRECT_OPENER.open
    try:
        with opener(request, timeout=timeout) as resp:  # noqa: S310 — loopback
            raw = resp.read()
            return Response(
                status=int(resp.status),
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=raw.decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as exc:  # 4xx/5xx — теж повноцінна відповідь для перевірок
        raw = exc.read()
        return Response(
            status=int(exc.code),
            headers={k.lower(): v for k, v in exc.headers.items()},
            body=raw.decode("utf-8", errors="replace"),
        )


def _gui_is_up() -> bool:
    try:
        return _get("/", timeout=3.0).status == 200
    except OSError:
        return False


# Код-рев'ю PR3 (H-1) + пострев'ю (S-1): там, де стек ГАРАНТОВАНО піднято, пропуск цих
# тестів заборонений — мовчазний skip означав би, що §13-інваріанти nginx не перевіряються
# взагалі. Прапорець власний (`COLLECTOR_E2E_REQUIRED`), а НЕ універсальний `CI`: GitHub
# Actions виставляє `CI=true` в УСІХ job-ах, тож прив'язка до нього ламала job `python`,
# де стека немає й бути не може. Прапорець виставляє лише крок job `docker` після
# `docker compose up -d --wait`.
E2E_REQUIRED = os.environ.get("COLLECTOR_E2E_REQUIRED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def skip_unless_available(available: bool, reason: str) -> pytest.MarkDecorator:
    """Поза середовищем зі стеком — skip; там, де стек обіцяний, — гучне падіння."""
    return pytest.mark.skipif(not available and not E2E_REQUIRED, reason=reason)


pytestmark = [
    pytest.mark.e2e,
    skip_unless_available(
        _gui_is_up(),
        reason=(
            f"gui не відповідає на {BASE_URL} — підніміть "
            "`COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`"
        ),
    ),
]


def _csp_directives(response: Response) -> dict[str, str]:
    csp = response.header("Content-Security-Policy")
    assert csp, "Content-Security-Policy відсутній у відповіді"
    directives: dict[str, str] = {}
    for part in csp.split(";"):
        chunk = part.strip()
        if not chunk:
            continue
        name, _, value = chunk.partition(" ")
        directives[name] = value.strip()
    return directives


def _asset_path() -> str:
    """Перший hashed-asset з index.html — саме той location, де `expires` міг би зрізати CSP."""
    index = _get("/")
    match = re.search(r'src="(/assets/[^"]+\.js)"', index.body)
    assert match, f"у index.html немає hashed-asset: {index.body[:300]}"
    return match.group(1)


# --- security headers на кожному типі location (§13) -----------------------------------------


@pytest.mark.parametrize(
    "path_factory",
    [
        pytest.param(lambda: "/", id="index"),
        pytest.param(_asset_path, id="hashed-asset"),
        pytest.param(lambda: "/index.html", id="index.html"),
        pytest.param(lambda: "/немає-такого-маршруту", id="spa-fallback"),
        pytest.param(lambda: HEALTH_PATH, id="health"),
        pytest.param(lambda: "/internal-api-health", id="internal-denied"),
    ],
)
def test_security_headers_present_on_every_location(path_factory: Any) -> None:
    """Регресія на nginx-пастку: `add_header` у location скидає успадковані заголовки server."""
    response = _get(path_factory())

    directives = _csp_directives(response)
    for name, expected in REQUIRED_CSP.items():
        assert directives.get(name) == expected, f"CSP {name}: {directives.get(name)!r}"
    for header, expected in REQUIRED_HEADERS.items():
        assert response.header(header) == expected, header
    assert response.header("Permissions-Policy"), "Permissions-Policy"
    assert response.header("Strict-Transport-Security"), "Strict-Transport-Security"


def test_csp_has_no_unsafe_sources() -> None:
    """`unsafe-inline`/`unsafe-eval`/зовнішній origin у CSP = XSS-модель §13 зламана."""
    index = _get("/")
    csp = index.header("Content-Security-Policy")
    directives = _csp_directives(index)

    for forbidden in ("'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'", "*", "http:", "data:"):
        offenders = {
            name: value
            for name, value in directives.items()
            # `img-src 'self' data:` — свідомий виняток для inline-іконок, решта директив ні.
            if forbidden in value.split() and name != "img-src"
        }
        assert not offenders, f"{forbidden} у {offenders} (CSP: {csp})"


def test_server_header_hides_version() -> None:
    """§13/`server_tokens off`: версія nginx не підказує CVE-набір."""
    server = _get("/").header("Server")

    assert server == "nginx", server


# --- проксі /api і приховування деталей health (§8, §13, CR-14) -------------------------------


def test_spa_fallback_returns_index_not_404() -> None:
    response = _get("/jobs/невідомий-id")

    assert response.status == 200
    assert '<div id="root">' in response.body
    assert response.header("Content-Type", "").startswith("text/html")


def test_public_health_exposes_only_ready_flag() -> None:
    """CR-14: назовні — лише ready/not_ready; звіт компонентів і git SHA не витікають."""
    response = _get(HEALTH_PATH)

    assert response.status in {200, 503}, response.status
    payload = json.loads(response.body)
    assert set(payload) == {"status"}, payload
    assert payload["status"] in {"ready", "not_ready"}
    for leaked in ("components", "git_sha", "latency_ms", "schema_version", "postgres", "mongo"):
        assert leaked not in response.body, leaked


def test_internal_auth_subrequest_is_not_reachable_from_outside() -> None:
    """`location = /internal-api-health` має `internal` — інакше звіт компонентів публічний."""
    response = _get("/internal-api-health")

    assert response.status == 404, response.status
    assert "components" not in response.body


@pytest.mark.parametrize(
    "path",
    [
        f"{HEALTH_PATH}?x=1",
        f"//{HEALTH_PATH.lstrip('/')}",
        "/api//v1/health/components",
        "/api/v1/health/../health/components",
    ],
)
def test_health_detail_not_reachable_through_path_normalization(path: str) -> None:
    """Обхід exact-location нормалізацією шляху віддав би повний звіт через `location /api/`."""
    response = _get(path)

    assert "components" not in response.body, f"{path}: {response.body[:200]}"
    if response.status in {200, 503}:
        assert set(json.loads(response.body)) == {"status"}, path


def test_api_proxy_reaches_upstream_and_is_same_origin() -> None:
    """`/api/` проксіюється на api:8000 (не 404 від nginx): відповідь приходить від FastAPI."""
    response = _get("/api/v1/does-not-exist")

    # 404 саме від FastAPI (JSON `detail`), а не HTML-сторінка nginx; 502 — api лежить.
    assert response.status != 200
    if response.status == 404:
        assert json.loads(response.body) == {"detail": "Not Found"}, response.body
    else:
        assert response.status in {502, 503, 504}, response.status


# --- security-рев'ю PR3 -----------------------------------------------------------------


def test_forged_host_does_not_reach_api() -> None:
    """SEC M-1: підроблений Host не має з'являтись у відповіді api (open redirect).

    До фіксу `curl -H 'Host: evil.example.com' …/health/components/` повертав
    `location: http://evil.example.com/...` — FastAPI будував absolute-URL з клієнтського
    Host. Тепер api бачить фіксований внутрішній Host, а absolute-Location стає відносним.
    """
    forged = "evil.example.com"
    response = _get(f"{HEALTH_PATH}/", headers={"Host": forged}, follow_redirects=False)

    location = response.header("location")
    assert forged not in location, f"Host просочився у Location: {location!r}"
    assert forged not in response.body, "Host просочився у тіло відповіді"
    if location:
        # Редирект лишається робочим для браузера: або відносний, або на власний origin.
        assert location.startswith("/") or location.startswith(BASE_URL), location


def test_forged_host_does_not_break_static() -> None:
    """Статику з чужим Host віддаємо як є — 444 на невідомий Host прийде з WP-13."""
    response = _get("/", headers={"Host": "attacker.test"}, follow_redirects=False)
    assert response.status == 200


def test_source_maps_are_not_served() -> None:
    """Код-рев'ю M-2 / SEC L-1: `.map` не постачається і не віддається."""
    index = _get("/")
    match = re.search(r'src="(/assets/[^"]+\.js)"', index.body)
    assert match, index.body[:400]
    assert _get(f"{match.group(1)}.map").status == 404


def test_api_without_trailing_slash_is_not_spa_fallback() -> None:
    """Код-рев'ю L-8: `/api` не має віддавати index.html з кодом 200."""
    response = _get("/api")
    assert response.status == 404
    assert '<div id="root">' not in response.body
