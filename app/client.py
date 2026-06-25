# Copyright (C) 2021 - 2026 ANSYS, Inc. and/or its affiliates.
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""REST client for Fluent DataModel settings endpoints.

This client talks to ``/api/{component}/...`` and sends
``Authorization: Bearer <sha256(auth_token)>`` when a token is configured.
Transport-level failures are raised as :class:`FluentRestError`; a command
that requires user confirmation is raised as :class:`ConfirmationRequired`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import ssl
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# HTTP methods that are safe to replay after a transient failure.
_RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Server-side statuses that indicate a transient, retryable condition.
_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})

# Statuses that mean "shutdown was explicitly refused" and must not be
# swallowed by :meth:`FluentRestClient.exit`.
_SHUTDOWN_BLOCKED_STATUS_CODES = frozenset({403, 409})


# ---------------------------------------------------------------------------
# Errors — the single place that interprets transport-level failures.
# ---------------------------------------------------------------------------


class FluentRestError(RuntimeError):
    """HTTP error raised when a Fluent REST request fails.

    This class is the **single place** that understands how to interpret
    transport-level failures.  It knows which HTTP status codes come from
    the server vs. which originate from a broken connection, and it knows
    which failures are transient enough to be worth retrying.

    Attributes
    ----------
    status : int
        HTTP status code.  ``0`` means the request never reached the
        server (connection refused, reset, DNS failure, etc.).
    retryable : bool
        ``True`` when the failure is transient — a 502/503/504 gateway
        error or a connection-level ``OSError`` — and re-issuing the
        same request has a reasonable chance of succeeding.
    """

    def __init__(self, status: int, message: str, *, retryable: bool = False) -> None:
        self.status = status
        self.retryable = retryable
        super().__init__(f"HTTP {status}: {message}")

    @classmethod
    def from_transport(cls, exc: OSError) -> "FluentRestError":
        """Construct from a stdlib transport exception.

        ``urllib`` raises ``HTTPError`` (a subclass of ``OSError``) when the
        server replies with an error status, and plain ``OSError`` when the
        connection itself fails.  A ``409`` is promoted to the dedicated
        :class:`ConfirmationRequired` so callers can react to confirmation
        prompts without string-matching.
        """
        if isinstance(exc, urllib.error.HTTPError):
            body = exc.read().decode("utf-8", errors="replace").strip()
            if exc.code == 409:
                return ConfirmationRequired(cls._prompt_from_body(body))
            message = body or exc.reason
            return cls(exc.code, message, retryable=exc.code in _RETRYABLE_STATUS_CODES)
        return cls(0, str(getattr(exc, "reason", exc)), retryable=True)

    @staticmethod
    def _prompt_from_body(body: str) -> str:
        """Extract the ``show-prompt`` text from a 409 response body."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return body
        if isinstance(data, dict):
            return data.get("show-prompt", body)
        return body


class ConfirmationRequired(FluentRestError):
    """Raised when a command needs explicit confirmation (HTTP 409).

    The server declined to run the command because it has a confirmation
    prompt.  Inspect :attr:`prompt`, and to proceed re-issue the same call
    with ``force=True``.

    Attributes
    ----------
    prompt : str
        Human-readable confirmation message returned by the server.
    """

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        super().__init__(409, prompt or "Confirmation required", retryable=False)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class FluentRestClient:
    """HTTP client for the Fluent DataModel REST API.

    Parameters
    ----------
    base_url : str
        Root URL of the Fluent REST server, e.g. ``"http://127.0.0.1:<port>"``.
        A trailing slash is stripped automatically.
    auth_token : str, optional
        Raw bearer token (the password set when Fluent was started).  Before
        each request the token is SHA-256 hashed and sent as
        ``Authorization: Bearer <sha256(auth_token)>``.

        .. note::
           Hashing is the behaviour of the SimBA server this client targets.
           If your server expects the raw token instead, change the single
           line in :meth:`_make_auth_headers`.
    component : str, optional
        DataModel component name.  Defaults to ``"fluent_1"`` (solver).
        Use ``"fluent_meshing_1"`` for a meshing session.
    timeout : float, optional
        Socket timeout in seconds for every request.  Defaults to ``30.0``.
    max_retries : int, optional
        Maximum number of automatic retries on transient connection errors or
        HTTP 502/503/504 responses, applied to idempotent methods only
        (GET/HEAD/OPTIONS).  Defaults to ``0`` (fail immediately).
    retry_delay : float, optional
        Base delay in seconds between retries.  Uses exponential back-off:
        ``retry_delay * 2 ** attempt``.  Defaults to ``1.0``.
    ssl_context : ssl.SSLContext, optional
        Custom SSL context for HTTPS connections.  Pass an unverified context
        to accept self-signed development certificates.  Defaults to ``None``.
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str | None = None,
        component: str = "fluent_1",
        timeout: float = 30.0,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._component = component
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._ssl_context = ssl_context
        self._api_base = f"api/{component}"
        self._is_closed = False
        self._headers = self._make_auth_headers(auth_token)

    @staticmethod
    def _make_auth_headers(auth_token: str | None) -> dict[str, str]:
        """Return auth headers for *auth_token*, or an empty dict if none.

        This is the **only** place the wire format of the token is decided.
        Swap the hashing here if the server expects the raw token.
        """
        if not auth_token:
            return {}
        token_hash = hashlib.sha256(auth_token.encode()).hexdigest()
        return {"Authorization": f"Bearer {token_hash}"}

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_name(name: str) -> str:
        """Percent-encode a single URL segment (object name, command name)."""
        return urllib.parse.quote(name, safe="")

    @classmethod
    def _encode_path(cls, path: str) -> str:
        """Percent-encode each segment of a slash-delimited path."""
        return "/".join(cls._encode_name(seg) for seg in path.split("/"))

    def _settings_endpoint(self, path: str) -> str:
        """Return the API endpoint for a settings *path* under this component."""
        return f"{self._api_base}/{self._encode_path(path)}"

    # ------------------------------------------------------------------
    # HTTP transport internals
    # ------------------------------------------------------------------

    def _build_request(
        self,
        method: str,
        endpoint: str,
        body: Any = None,
    ) -> urllib.request.Request:
        """Assemble a :class:`urllib.request.Request` with auth + JSON body."""
        url = f"{self._base_url}/{endpoint}"
        headers = dict(self._headers)
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(
            url, data=data, headers=headers, method=method.upper()
        )

    def _send_once(self, req: urllib.request.Request) -> Any:
        """Execute one HTTP request and decode the JSON response.

        Returns ``None`` for an empty body and ``{}`` for a non-empty body
        that is not valid JSON.
        """
        with urllib.request.urlopen(  # nosec B310
            req, timeout=self._timeout, context=self._ssl_context
        ) as resp:
            raw = resp.read()
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send(self, req: urllib.request.Request) -> Any:
        """Send one request, translating transport errors to domain errors."""
        try:
            return self._send_once(req)
        except OSError as exc:
            raise FluentRestError.from_transport(exc) from exc

    def _back_off(self, attempt: int) -> None:
        """Sleep for an exponentially increasing amount of time."""
        time.sleep(self._retry_delay * (2**attempt))

    def _send_with_retry(self, req: urllib.request.Request, retries: int) -> Any:
        """Send *req*, retrying only on retryable errors up to *retries* times."""
        attempt = 0
        while True:
            try:
                return self._send(req)
            except FluentRestError as exc:
                if not exc.retryable or attempt >= retries:
                    raise
                self._back_off(attempt)
                attempt += 1

    def _request(self, method: str, endpoint: str, *, body: Any = None) -> Any:
        """Send an HTTP request, retrying idempotent methods on transient failure."""
        if self._is_closed:
            raise FluentRestError(0, "Session is closed")
        req = self._build_request(method, endpoint, body)
        retries = self._max_retries if method.upper() in _RETRYABLE_METHODS else 0
        return self._send_with_retry(req, retries)

    # ------------------------------------------------------------------
    # Connection health
    # ------------------------------------------------------------------

    def is_server_reachable(self) -> bool:
        """Return ``True`` if the server answers the readiness probe.

        Issues ``GET /api/connection/run_mode``.  A ``2xx`` response, or a
        ``401`` (server is up but rejected our credentials), both count as
        reachable; anything else — including a refused or reset connection —
        counts as unreachable.
        """
        try:
            self._request("GET", "api/connection/run_mode")
            return True
        except FluentRestError as exc:
            return exc.status == 401

    # ------------------------------------------------------------------
    # Settings API — read
    # ------------------------------------------------------------------

    def get_static_info(self, full: bool = False) -> dict[str, Any]:
        """Return the full settings schema (GET ``static-info``).

        Parameters
        ----------
        full : bool, optional
            When ``True``, bypass the server cache and fetch fresh schema.
            Defaults to ``False`` (cached).
        """
        endpoint = f"{self._api_base}/static-info"
        if full:
            endpoint += "?full=true"
        return self._request("GET", endpoint)

    def get_var(self, path: str) -> Any:
        """Return the value at *path* (POST ``get_var``)."""
        return self._request(
            "POST", f"{self._api_base}/get_var", body={"path": path.lstrip("/")}
        )

    def get_attrs(self, path: str, attrs: list[str], recursive: bool = False) -> Any:
        """Return selected attributes for *path* (GET with ``attrs=...``)."""
        params = {"attrs": ",".join(attrs)}
        if recursive:
            params["recursive"] = "true"
        query = urllib.parse.urlencode(params)
        return self._request("GET", f"{self._settings_endpoint(path)}?{query}")

    def get_object_names(self, path: str) -> list[str]:
        """Return child object names at *path*; return ``[]`` if *path* is 404.

        Raises
        ------
        FluentRestError
            If the request fails with a non-404 HTTP error.
        """
        result = self._get_or_none(path)
        return self._names_from(result)

    def get_list_size(self, path: str) -> int:
        """Return the element count at *path*; return ``0`` if *path* is 404.

        Raises
        ------
        FluentRestError
            If the request fails with a non-404 HTTP error.
        """
        result = self._get_or_none(path)
        return self._size_from(result)

    def _get_or_none(self, path: str) -> Any:
        """GET a settings *path*, returning ``None`` instead of raising on 404."""
        try:
            return self._request("GET", self._settings_endpoint(path))
        except FluentRestError as exc:
            if exc.status == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Settings API — write
    # ------------------------------------------------------------------

    def set_var(self, path: str, value: Any) -> Any:
        """Write *value* at *path* (PUT ``{path}``); return the updated state."""
        return self._request("PUT", self._settings_endpoint(path), body=value)

    def resize_list_object(self, path: str, size: int) -> None:
        """Resize the list-object at *path* to *size* elements (POST ``{path}``)."""
        self._request("POST", self._settings_endpoint(path), body={"new-size": size})

    # ------------------------------------------------------------------
    # Settings API — named objects CRUD
    # ------------------------------------------------------------------

    def create(self, path: str, name: str = "", properties: dict | None = None) -> Any:
        """Create a child object at *path* (POST ``{path}``).

        Raises
        ------
        FluentRestError
            If the request fails.
        """
        body = dict(properties) if properties else {}
        if name:
            body["name"] = name
        return self._request("POST", self._settings_endpoint(path), body=body)

    def delete(self, path: str, name: str, *, ignore_not_found: bool = False) -> None:
        """Delete named object *name* at *path* (DELETE ``{path}/{name}``).

        Raises
        ------
        FluentRestError
            If deletion fails, except when ``ignore_not_found=True`` and the
            server returns HTTP 404.
        """
        endpoint = f"{self._settings_endpoint(path)}/{self._encode_name(name)}"
        try:
            self._request("DELETE", endpoint)
        except FluentRestError as exc:
            if ignore_not_found and exc.status == 404:
                return
            raise

    def rename(self, path: str, new: str, old: str) -> None:
        """Rename *old* to *new* at *path* (PUT ``{path}/{old}``)."""
        endpoint = f"{self._settings_endpoint(path)}/{self._encode_name(old)}"
        self._request("PUT", endpoint, body={"name": new})

    def delete_child_objects(
        self, path: str, obj_type: str, child_names: list[str]
    ) -> None:
        """Delete specific named children of *obj_type* under *path*."""
        for name in child_names:
            self.delete(f"{path}/{obj_type}", name)

    def delete_all_child_objects(self, path: str, obj_type: str) -> None:
        """Delete all named children of *obj_type* under *path*."""
        names = self.get_object_names(f"{path}/{obj_type}")
        self.delete_child_objects(path, obj_type, names)

    # ------------------------------------------------------------------
    # Settings API — commands & queries
    # ------------------------------------------------------------------

    def execute_cmd(self, path: str, command: str, force: bool = False, **kwds) -> Any:
        """Execute *command* at *path*.

        With ``force=False`` (the default) the server may decline a command
        that has a confirmation prompt, raising :class:`ConfirmationRequired`.
        Re-issue with ``force=True`` to skip the prompt and proceed.
        """
        return self._post_action(path, command, args=kwds, force=force)

    def execute_query(self, path: str, query: str, **kwds) -> Any:
        """Execute *query* at *path* (POST ``{path}/{query}``)."""
        return self._post_action(path, query, args=kwds)

    def _post_action(
        self, path: str, name: str, *, args: dict, force: bool = False
    ) -> Any:
        """POST to a command/query endpoint and return the response payload."""
        endpoint = f"{self._settings_endpoint(path)}/{self._encode_name(name)}"
        if force:
            endpoint += "?force=true"
        return self._request("POST", endpoint, body=args)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def exit(self) -> None:
        """Request shutdown via ``POST /api/app/exit`` and mark the session closed.

        A 403/409 means the server refused to shut down and is raised to the
        caller.  Every other failure is assumed to be the connection dropping
        as the server tears down, and is suppressed.

        Raises
        ------
        FluentRestError
            If shutdown is blocked by the server (HTTP 403 or 409).
        """
        if self._is_closed:
            return
        try:
            self._request("POST", "api/app/exit")
        except FluentRestError as exc:
            if exc.status in _SHUTDOWN_BLOCKED_STATUS_CODES:
                raise
            logger.debug("Suppressed transport error during shutdown: %s", exc)
        self._is_closed = True
        logger.info("Fluent server terminated.")

    def __enter__(self) -> "FluentRestClient":
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager — calls :meth:`exit`."""
        self.exit()

    # ------------------------------------------------------------------
    # Response normalisers
    # ------------------------------------------------------------------

    @staticmethod
    def _names_from(result: Any) -> list[str]:
        """Normalise a child-listing response to a plain list of names.

        The server returns either a JSON array ``["a", "b"]`` or a dict keyed
        by object name ``{"a": {...}, "b": {...}}``.
        """
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return list(result.keys())
        return []

    @staticmethod
    def _size_from(result: Any) -> int:
        """Extract an element count from a list-object response.

        A list-object reports its length directly; a named-object container
        may include an explicit ``size`` field or just its key count.
        """
        if isinstance(result, list):
            return len(result)
        if isinstance(result, dict):
            return result.get("size", len(result))
        return 0
