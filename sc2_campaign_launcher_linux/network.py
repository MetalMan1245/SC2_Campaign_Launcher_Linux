"""Bounded metadata requests and streamed campaign downloads."""

import hashlib
import http.client
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

from . import __version__
from .files import Cancelled, check_cancel

ALLOWED_HOSTS = frozenset({
    'api.github.com',
    'github.com', 'raw.githubusercontent.com', 'objects.githubusercontent.com',
    'release-assets.githubusercontent.com', 'media.githubusercontent.com', 'sc2.sarl',
})
CHUNK_SIZE = 256 * 1024
DEFAULT_DOWNLOAD_LIMIT = 16 * 1024**3
DEFAULT_FREE_RESERVE = 256 * 1024**2


def resource_setting(value, variable, default, minimum):
    value = os.environ.get(variable, str(default)) if value is None else value
    try:
        if type(value) not in (str, int):
            raise ValueError
        result = int(value)
        if result < minimum:
            raise ValueError
        return result
    except ValueError as error:
        raise ValueError(f'{variable} must be an integer number of bytes, at least {minimum}.') from error


def validate_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError('The download URL must be a string.')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.fragment):
        raise ValueError(f'Unsupported download URL: {url}')
    return url


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpClient:
    def __init__(self, opener=None, *, download_limit=None, free_reserve=None):
        self.opener = opener or urllib.request.build_opener(CheckedRedirect())
        self.download_limit = download_limit
        self.free_reserve = free_reserve

    def open(self, url: str, cancel: Event | None = None):
        validate_url(url)
        request = urllib.request.Request(url, headers={
            'User-Agent': f'SC2CampaignLauncher/{__version__}',
            'Accept': '*/*',
        })
        for attempt in range(3):
            check_cancel(cancel)
            try:
                response = self.opener.open(request, timeout=10)
                try:
                    validate_url(response.geturl())
                    if response.status != 200:
                        raise ValueError(f'Unexpected HTTP status: {response.status}')
                except Exception:
                    response.close()
                    raise
                return response
            except (urllib.error.URLError, TimeoutError) as error:
                if isinstance(error, urllib.error.HTTPError):
                    retryable = error.code in (429, 500, 502, 503, 504)
                    error.close()
                    if not retryable:
                        raise
                if attempt == 2:
                    raise
                if cancel is not None:
                    if cancel.wait(attempt + 1):
                        raise Cancelled('Cancelled') from error
                else:
                    Event().wait(attempt + 1)

    def get(self, url: str, cancel: Event | None = None, limit=8 * 1024 * 1024) -> bytes:
        with self.open(url, cancel) as response:
            chunks, total = [], 0
            while chunk := self._read(response, min(CHUNK_SIZE, limit + 1 - total)):
                check_cancel(cancel)
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise ValueError('The response exceeds the allowed size.')
            check_cancel(cancel)
            return b''.join(chunks)

    @staticmethod
    def _read(response, size):
        try:
            return response.read(size)
        except http.client.HTTPException as error:
            raise OSError(f'The download connection was interrupted: {error}') from error

    def json(self, url: str, cancel: Event | None = None):
        return json.loads(self.get(url, cancel))

    def download(self, url: str, destination: Path, expected_sha: str,
                 cancel: Event, progress, check_destination=lambda: None):
        check_cancel(cancel)
        limit = resource_setting(self.download_limit, 'SC2CL_MAX_DOWNLOAD_BYTES', DEFAULT_DOWNLOAD_LIMIT, 1)
        reserve = resource_setting(self.free_reserve, 'SC2CL_MIN_FREE_BYTES', DEFAULT_FREE_RESERVE, 0)

        def check_space(required):
            if shutil.disk_usage(destination.parent).free < required + reserve:
                raise OSError(f'Not enough free space for {destination.name}, including the {reserve}-byte reserve. '
                              'Free space or adjust SC2CL_MIN_FREE_BYTES.')

        check_destination()
        destination.parent.mkdir(parents=True, exist_ok=True)
        check_destination()
        fd, name = tempfile.mkstemp(prefix='.sc2cl-', suffix='.part', dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as output, self.open(url, cancel) as response:
                length_header = response.headers.get('Content-Length')
                length = None
                if length_header is not None:
                    header = length_header.strip()
                    if not header or not header.isascii() or not header.isdecimal():
                        raise ValueError('Invalid Content-Length.')
                    length = int(header)
                    if length > limit:
                        raise ValueError('The download exceeds SC2CL_MAX_DOWNLOAD_BYTES. '
                                         'For a verified larger campaign, raise this byte limit and retry.')
                check_space(length or 0)
                digest, received = hashlib.sha256(), 0
                progress(0, length or 0)
                while chunk := self._read(response, min(CHUNK_SIZE, limit - received + 1)):
                    check_cancel(cancel)
                    if received + len(chunk) > limit:
                        raise ValueError('The download exceeds SC2CL_MAX_DOWNLOAD_BYTES. '
                                         'For a verified larger campaign, raise this byte limit and retry.')
                    if length is not None and received + len(chunk) > length:
                        raise OSError(f'The download exceeds its declared size: {destination.name}')
                    check_space(len(chunk))
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    progress(received, length or 0)
                check_cancel(cancel)
                if length is not None and length != received:
                    raise OSError(f'Incomplete download: {destination.name}')
                if digest.hexdigest() != expected_sha:
                    raise ValueError(f'Checksum mismatch: {destination.name}. Refresh and try again.')
                output.flush()
                os.fsync(output.fileno())
            check_cancel(cancel)
            check_destination()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
