"""Guarded fetch for client-supplied media URLs.

The proxy accepts video as base64 because that is all llama.cpp's `input_video`
takes, but clients reasonably send URLs — images already work that way, since
their URL is handed to llama.cpp which fetches it. So a video URL is fetched
here and inlined.

Fetching a URL chosen by whoever is talking to the proxy is an SSRF primitive:
the proxy sits on the user's machine and can reach localhost, the LAN, and any
cloud metadata endpoint the client cannot. Nothing else in this codebase guards
that today — `proxy/api_jobs.py` will fetch any URL it is handed with no checks
at all, and `web_search.py` is only safe by accident because its URLs come from
search results rather than from the caller. So the checks live here:

  * scheme must be http/https — no file://, no data: (handled before this), no
    gopher/ftp redirect tricks
  * the resolved address must be public — loopback, private, link-local (incl.
    169.254.169.254, the cloud metadata address), unique-local and reserved
    ranges are refused, and every DNS answer is checked, not just the first
  * redirects are followed manually so each hop is re-validated; a public URL
    that redirects to 127.0.0.1 is the classic bypass
  * a byte cap, enforced while streaming rather than after, so a hostile server
    cannot exhaust memory by ignoring Content-Length
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("telecode.proxy.media_fetch")

MAX_BYTES = 128 * 1024 * 1024      # 128 MiB — a few minutes of phone video
TIMEOUT_SEC = 60.0
MAX_REDIRECTS = 3


class MediaFetchError(ValueError):
    """The URL was refused, or could not be fetched."""


def _addresses_for(host: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise MediaFetchError(f"cannot resolve host: {host}") from exc
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not out:
        raise MediaFetchError(f"cannot resolve host: {host}")
    return out


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise MediaFetchError(f"unsupported URL scheme: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise MediaFetchError("URL has no host")

    # EVERY resolved address must be public. Checking only the first lets a
    # multi-A-record host slip a private address through.
    for addr in _addresses_for(parsed.hostname):
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            raise MediaFetchError(
                f"refusing to fetch a non-public address ({addr}) — the proxy can "
                f"reach hosts the caller cannot"
            )


async def fetch_media_b64(url: str, *, max_bytes: int = MAX_BYTES) -> str:
    """Fetch `url` and return its bytes base64-encoded. Raises MediaFetchError."""
    seen = 0
    current = url
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SEC)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            _check_url(current)          # re-validated on every hop
            try:
                async with session.get(current, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        seen += 1
                        if not loc or seen > MAX_REDIRECTS:
                            raise MediaFetchError("too many redirects")
                        current = str(resp.url.join(aiohttp.client.URL(loc)))
                        continue
                    if resp.status != 200:
                        raise MediaFetchError(f"fetch failed: HTTP {resp.status}")

                    # Cap while streaming — Content-Length is a hint, not a promise.
                    chunks, total = [], 0
                    async for chunk in resp.content.iter_chunked(1 << 16):
                        total += len(chunk)
                        if total > max_bytes:
                            raise MediaFetchError(
                                f"media exceeds {max_bytes // (1024*1024)} MiB cap")
                        chunks.append(chunk)
                    body = b"".join(chunks)
            except aiohttp.ClientError as exc:
                raise MediaFetchError(f"fetch failed: {exc}") from exc
            except asyncio.TimeoutError as exc:
                raise MediaFetchError(f"fetch timed out after {TIMEOUT_SEC:.0f}s") from exc

            log.info("media_fetch: %d bytes from %s", len(body), current)
            return base64.b64encode(body).decode("ascii")
