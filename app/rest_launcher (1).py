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

"""Connect and session management for the Fluent REST transport.

This module provides a **standalone, low-level** REST transport layer.  It
does **not** build a settings tree (no ``session.settings``), expose
convenience helpers like ``read_case()``, or depend on ``flobject``.  All
interaction goes through the client returned by :attr:`RestSolverSession.client`
using explicit path-based calls (``get_var``, ``set_var``, ``execute_cmd``, ...).

Transport security
~~~~~~~~~~~~~~~~~~~
TLS (HTTPS) is supported by passing ``scheme="https"`` to
:func:`connect_to_webserver`.  For self-signed development certificates, pass
an unverified :class:`ssl.SSLContext` via ``ssl_context``.

Public API
----------
* :class:`RestSolverSession` — session wrapper for the low-level REST client.
* :func:`connect_to_webserver` — connect to an already-running Fluent REST
  server, returning a :class:`RestSolverSession`.

Examples
--------
Connect to an already-running Fluent web server and interact via the client::

    from ansys.fluent.core.rest import connect_to_webserver

    with connect_to_webserver(
        ip="127.0.0.1",
        port=5000,
        auth_token="my-secret-token",
    ) as session:
        enabled = session.client.get_var("setup/models/energy/enabled")
"""

from __future__ import annotations

import logging
import ssl

# from ansys.fluent.core.launcher.process_launch_string import get_fluent_exe_path  # (phase 2: launch_webserver)
from ansys.fluent.core.rest.client import FluentRestClient

__all__ = ["connect_to_webserver", "RestSolverSession"]

logger = logging.getLogger(__name__)

_VALID_SCHEMES = ("http", "https")


# ---------------------------------------------------------------------------
# Public API — session
# ---------------------------------------------------------------------------


class RestSolverSession:
    """Session wrapper for a running Fluent REST (SimBA) server.

    Holds the connection metadata and owns a :class:`FluentRestClient`, which
    is exposed through :attr:`client` for path-based operations.  The session
    is a context manager: leaving the ``with`` block calls :meth:`exit`.

    Parameters
    ----------
    base_url : str
        Root URL of the Fluent REST server, e.g. ``"http://127.0.0.1:5000"``.
    auth_token : str, optional
        Raw bearer token (the password set when Fluent was started).
    component : str, optional
        DataModel component name.  Defaults to ``"fluent_1"`` (solver).
    timeout : float, optional
        HTTP socket timeout in seconds.  Defaults to ``30.0``.
    max_retries : int, optional
        Maximum automatic retries on transient HTTP errors.  Defaults to ``0``.
    retry_delay : float, optional
        Base delay in seconds between retries (exponential back-off).
        Defaults to ``1.0``.
    ssl_context : ssl.SSLContext, optional
        Custom SSL context for HTTPS connections.  Defaults to ``None``.

    Attributes
    ----------
    ip : str or None
        IP address of the connected server (set by :func:`connect_to_webserver`).
    port : int or None
        TCP port of the connected server.
    auth_token : str or None
        Bearer token used for authentication.

    Examples
    --------
    >>> from ansys.fluent.core.rest import connect_to_webserver
    >>> session = connect_to_webserver(
    ...     ip="127.0.0.1",
    ...     port=5000,
    ...     auth_token="my-secret-token",
    ... )
    >>> session.client.get_var("setup/models/energy/enabled")
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
        self._client = FluentRestClient(
            base_url,
            auth_token=auth_token,
            component=component,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            ssl_context=ssl_context,
        )
        # Connection metadata, populated by connect_to_webserver().
        self.ip: str | None = None
        self.port: int | None = None
        self.auth_token: str | None = auth_token

    @property
    def client(self) -> FluentRestClient:
        """The low-level REST client for path-based operations."""
        return self._client

    def exit(self) -> None:
        """Shut down the Fluent server and close the underlying client."""
        self._client.exit()

    def __enter__(self) -> "RestSolverSession":
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager — calls :meth:`exit`."""
        self.exit()


# ---------------------------------------------------------------------------
# Public API — connector
# ---------------------------------------------------------------------------


def connect_to_webserver(
    ip: str,
    port: int,
    auth_token: str,
    *,
    scheme: str = "http",
    component: str = "fluent_1",
    version: str = "",
    timeout: float = 30.0,
    max_retries: int = 0,
    retry_delay: float = 1.0,
    ssl_context: ssl.SSLContext | None = None,
) -> RestSolverSession:
    """Connect to an already-running Fluent REST (SimBA) server.

    Use this when the server is already running and you know its ``ip``,
    ``port``, and ``auth_token``.  The connection is validated with a
    readiness probe before the session is returned.

    Parameters
    ----------
    ip : str
        IP address or hostname of the server, e.g. ``"127.0.0.1"``.
    port : int
        TCP port the server is listening on.
    auth_token : str
        Bearer token (password) for authentication.
    scheme : str, optional
        URL scheme — ``"http"`` or ``"https"``.  Defaults to ``"http"``.
    component : str, optional
        DataModel component name.  Defaults to ``"fluent_1"`` (solver).
    version : str, optional
        Fluent version string (e.g. ``"261"``), used only for logging.
        Defaults to ``""``.
    timeout : float, optional
        HTTP socket timeout in seconds.  Defaults to ``30.0``.
    max_retries : int, optional
        Maximum automatic retries on transient HTTP errors.  Defaults to ``0``.
    retry_delay : float, optional
        Base delay in seconds between retries (exponential back-off).
        Defaults to ``1.0``.
    ssl_context : ssl.SSLContext, optional
        Custom SSL context for HTTPS connections.  Pass an unverified context
        to accept self-signed development certificates.  Defaults to ``None``.

    Returns
    -------
    RestSolverSession
        A session with ``ip``, ``port``, and ``auth_token`` populated.

    Raises
    ------
    ValueError
        If *scheme* is not ``"http"`` or ``"https"``.
    ConnectionError
        If the server does not respond to the readiness probe.

    Examples
    --------
    >>> from ansys.fluent.core.rest import connect_to_webserver
    >>> session = connect_to_webserver(
    ...     ip="127.0.0.1",
    ...     port=5000,
    ...     auth_token="my-secret-token",
    ... )
    >>> session.client.get_var("setup/models/energy/enabled")
    """
    if scheme not in _VALID_SCHEMES:
        raise ValueError(f"scheme must be 'http' or 'https', got {scheme!r}")

    base_url = f"{scheme}://{ip}:{port}"
    session = RestSolverSession(
        base_url,
        auth_token=auth_token,
        component=component,
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
        ssl_context=ssl_context,
    )

    if not session.client.is_server_reachable():
        raise ConnectionError(
            f"Fluent REST server at {base_url} did not respond to the readiness "
            "probe (GET /api/connection/run_mode). Verify that the server is "
            "running on the given ip and port, and that the auth_token is correct."
        )

    session.ip = ip
    session.port = port
    session.auth_token = auth_token
    if version:
        logger.info(
            "Connected to Fluent REST server: version=%s, ip=%s, port=%d, component=%s",
            version,
            ip,
            port,
            component,
        )
    return session
