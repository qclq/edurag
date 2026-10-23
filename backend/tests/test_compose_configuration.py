import re
from pathlib import Path


COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _service_block(compose: str, service_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service_name)}:\s*$\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\s*$|\Z)",
        compose,
    )
    assert match is not None, f"Compose service {service_name!r} is missing"
    return match.group("body")


def test_python_services_receive_cookie_secure_setting():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")

    for service_name in ("backend", "worker", "scheduler"):
        service = _service_block(compose, service_name)
        assert re.search(r"(?m)^      COOKIE_SECURE:\s*\$\{COOKIE_SECURE:-false\}\s*$", service), (
            f"{service_name} must receive COOKIE_SECURE because it creates the shared production Settings"
        )
