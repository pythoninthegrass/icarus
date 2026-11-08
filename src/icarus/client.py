import httpx
import json
import sys
import time
from pathlib import Path
from typing import Any


class CreateIdError(RuntimeError):
    """A create call returned no id and re-reading the parent resource could not recover one."""


class DokployClient:
    """Thin httpx wrapper for Dokploy API."""

    def __init__(self, base_url: str, api_key: str, max_retries: int = 3) -> None:
        self.max_retries = max_retries
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key},
            timeout=60.0,
        )

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send an HTTP request with retry on 429/5xx and exponential backoff."""
        url = f"/api/{path}"
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(self.max_retries + 1):
            resp = self.client.request(method, url, **kwargs)
            if resp.status_code < 400:
                return resp
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    message=f"{resp.status_code} {resp.reason_phrase}",
                    request=resp.request,
                    response=resp,
                )
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise last_exc
            # Non-retryable 4xx — fail immediately
            resp.raise_for_status()
        raise last_exc  # type: ignore[misc]

    def get(self, path: str, params: dict | None = None) -> dict | list:
        resp = self._request("GET", path, params=params)
        return resp.json()

    def post(self, path: str, payload: dict | None = None) -> Any:
        """POST and return the decoded body.

        Dokploy replies with an object for most endpoints, but some create
        endpoints answer with a bare boolean (``redirects.create``,
        ``security.create``) or with nothing at all (``backup.create``), so
        callers must not assume a mapping. Use :func:`create_and_get_id` for
        create calls.
        """
        resp = self._request("POST", path, json=payload or {})
        if not resp.content:
            return {}
        return resp.json()


def created_row(
    client: DokployClient,
    endpoint: str,
    resp: Any,
    id_key: str,
    *,
    read_endpoint: str | None = None,
    read_params: dict | None = None,
    read_container: str | None = None,
    match: dict | None = None,
) -> dict:
    """Return the row a create call describes, re-reading the parent when needed.

    Dokploy's create endpoints disagree about what they return: most reply with
    the created row, but ``redirects.create`` and ``security.create`` reply with
    a bare ``true`` and ``backup.create`` replies with nothing at all. When the
    response carries no ``id_key``, re-read ``read_endpoint`` and return the row
    that matches every ``match`` key (``read_container`` drills into a parent
    object, e.g. the ``redirects`` list hanging off ``application.one``).

    Raises:
        CreateIdError: the response had no id and no row could be matched, or no
            lookup was configured. The message names the endpoint that failed.
    """
    if isinstance(resp, dict) and isinstance(resp.get(id_key), str) and resp[id_key]:
        return resp

    if not read_endpoint or not match:
        raise CreateIdError(
            f"{endpoint} returned no {id_key} (got {type(resp).__name__}) and no lookup was configured to recover it"
        )

    rows = client.get(read_endpoint, read_params)
    if read_container and isinstance(rows, dict):
        rows = rows.get(read_container)
    if isinstance(rows, dict):
        rows = [rows]
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if not all(row.get(key) == expected for key, expected in match.items()):
            continue
        if isinstance(row.get(id_key), str) and row[id_key]:
            return row

    keys = ", ".join(f"{key}={expected!r}" for key, expected in match.items())
    raise CreateIdError(
        f"{endpoint} returned no {id_key} (got {type(resp).__name__}) and {read_endpoint} has no row matching {keys}"
    )


def create_and_get_id(
    client: DokployClient,
    endpoint: str,
    payload: dict | None,
    id_key: str,
    *,
    read_endpoint: str | None = None,
    read_params: dict | None = None,
    read_container: str | None = None,
    match: dict | None = None,
) -> str:
    """POST a create call and return the created resource's id.

    Thin wrapper over :func:`created_row` for the common case where only the id
    is needed. Use :func:`created_row` directly when other fields of the created
    row are wanted too (e.g. ``appName``).
    """
    row = created_row(
        client,
        endpoint,
        client.post(endpoint, payload),
        id_key,
        read_endpoint=read_endpoint,
        read_params=read_params,
        read_container=read_container,
        match=match,
    )
    return row[id_key]


def validate_state(client: DokployClient, state: dict) -> bool:
    """Check if the project in state still exists on the server.

    Returns True if valid or if the server can't be reached (assume valid).
    Returns False if the project is confirmed gone.
    """
    try:
        projects = client.get("project.all")
    except httpx.HTTPStatusError:
        return True
    return any(p["projectId"] == state["projectId"] for p in projects)


def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        print(f"ERROR: State file not found: {state_file}")
        print("Run 'setup' first.")
        sys.exit(1)
    return json.loads(state_file.read_text())


def save_state(state: dict, state_file: Path, *, quiet: bool = False) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2) + "\n")
    if not quiet:
        print(f"State saved to {state_file}")
