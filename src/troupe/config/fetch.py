"""Fetching remote sources: the only network code in troupe (config-remote-schema).

:func:`fetch_remote` sends one plain GET with urllib's default headers, follows redirects and
returns the body bytes. Every failure is a :class:`FetchError` with a short reason. It sends no
credentials and writes nothing to disk (Requirement 10.4). Decoding the bytes and turning a failure
into a load error are the loader's job.
"""

import socket
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from troupe.config.constants import REMOTE_FETCH_TIMEOUT_SECONDS


class FetchError(Exception):
    """A remote source could not be fetched; ``reason`` says why in a few words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fetch_remote(url: str, *, timeout: float = REMOTE_FETCH_TIMEOUT_SECONDS) -> bytes:
    """GET ``url`` once, following redirects, and return the final 2xx answer's body bytes.

    Raises :class:`FetchError` when the server cannot be reached, does not answer within
    ``timeout`` seconds, or answers with a final status outside 200-299.
    """
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as response:
            status = int(response.status)
            if not 200 <= status <= 299:
                raise FetchError(f"HTTP {status} {response.reason}")
            body: bytes = response.read()
            return body
    except HTTPError as error:
        raise FetchError(f"HTTP {error.code} {error.reason}") from error
    except URLError as error:
        raise FetchError(_os_reason(error.reason)) from error
    except (TimeoutError, OSError) as error:
        raise FetchError(_os_reason(error)) from error
    except HTTPException as error:
        raise FetchError(f"bad HTTP answer ({type(error).__name__})") from error
    except ValueError as error:
        raise FetchError(f"invalid URL ({error})") from error


def _os_reason(reason: object) -> str:
    """A short reason for a low-level failure (``URLError.reason`` or an ``OSError``)."""
    if isinstance(reason, TimeoutError):
        return "timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, socket.gaierror):
        return "unknown host"
    if isinstance(reason, OSError) and reason.strerror:
        return reason.strerror.lower()
    return str(reason)
