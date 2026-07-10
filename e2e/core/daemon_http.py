"""HTTP client helpers for the daemon's documented host surface."""

import json
import time
import urllib.error
import urllib.request

from .cli import manager


def daemon_http_endpoint(sandbox_id):
    """Resolve the published daemon HTTP endpoint through manager inspection."""
    inspected = manager("inspect_sandbox", "--sandbox-id", sandbox_id)
    endpoint = inspected.get("daemon_http") if isinstance(inspected, dict) else None
    assert endpoint, f"inspect_sandbox is missing daemon_http endpoint: {inspected}"
    return endpoint["host"], int(endpoint["port"])


def http_get(url, attempts=20):
    return http_request(url, attempts=attempts)


def http_post(url, document, attempts=20):
    return http_request(
        url,
        method="POST",
        body=json.dumps(document).encode("utf-8"),
        attempts=attempts,
    )


def http_request(url, method="GET", body=None, attempts=20):
    last_error = None
    for _ in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"} if body is not None else {},
                method=method,
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                return (
                    response.status,
                    response.read().decode("utf-8", "replace"),
                    response.headers.get_content_type(),
                )
        except urllib.error.HTTPError as error:
            content_type = error.headers.get_content_type() if error.headers else ""
            return error.code, error.read().decode("utf-8", "replace"), content_type
        except urllib.error.URLError as error:
            last_error = error
            time.sleep(0.25)
    raise AssertionError(f"{method} {url} never connected: {last_error}")
