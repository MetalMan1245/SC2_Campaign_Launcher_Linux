import hashlib
import http.client
import json
import urllib.request
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from sc2_campaign_launcher_linux.catalog import Catalog, raw_campaign
from sc2_campaign_launcher_linux.files import Cancelled
from sc2_campaign_launcher_linux.network import HttpClient
from conftest import campaign, write
from test_library import Response


@pytest.mark.parametrize('header', [None, '100', '2', '0'])
def test_download_limits_keep_previous_file_and_clean_partial(tmp_path, header):
    from unittest.mock import Mock
    response = Response(b'x' * 20)
    response.headers = {} if header is None else {'Content-Length': header}
    client = HttpClient(download_limit=10, free_reserve=0)
    client.open = Mock(return_value=response)
    target = write(tmp_path / 'map.SC2Map', b'keep old map')
    with pytest.raises((ValueError, OSError), match='exceeds'):
        client.download('https://github.com/test/map', target, hashlib.sha256(b'x' * 20).hexdigest(),
                        Event(), lambda *_: None)
    assert target.read_bytes() == b'keep old map'
    assert not list(tmp_path.glob('.sc2cl-*'))
    if header == '100':
        assert response.read_sizes == []


def test_missing_length_at_limit_and_larger_configured_limit_succeed(tmp_path, monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setenv('SC2CL_MAX_DOWNLOAD_BYTES', '20')
    for size in (10, 20):
        data = b'x' * size
        response = Response(data)
        response.headers = {}
        client = HttpClient(free_reserve=0)
        client.open = Mock(return_value=response)
        target = tmp_path / str(size)
        client.download('https://github.com/test/map', target, hashlib.sha256(data).hexdigest(), Event(), lambda *_: None)
        assert target.read_bytes() == data


@pytest.mark.parametrize('header', ['', '-1', '+2', '1.5', 'nan'])
def test_invalid_content_length_preserves_destination(tmp_path, header):
    from unittest.mock import Mock
    response = Response(b'x')
    response.headers = {'Content-Length': header}
    client = HttpClient(free_reserve=0)
    client.open = Mock(return_value=response)
    target = write(tmp_path / 'map.SC2Map', b'old')
    with pytest.raises(ValueError, match='Content-Length'):
        client.download('https://github.com/test/map', target, '0' * 64, Event(), lambda *_: None)
    assert target.read_bytes() == b'old'
    assert not list(tmp_path.glob('.sc2cl-*'))


@pytest.mark.parametrize('variable,value', [('SC2CL_MAX_DOWNLOAD_BYTES','0'), ('SC2CL_MAX_DOWNLOAD_BYTES','bad'),
                                            ('SC2CL_MIN_FREE_BYTES','-1')])
def test_invalid_resource_configuration_does_not_create_files(tmp_path, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=variable):
        HttpClient().download('https://github.com/test/map', tmp_path / 'new/map', '0' * 64,
                              Event(), lambda *_: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('header', [None, '4'])
def test_disk_space_is_rechecked_during_streaming(tmp_path, monkeypatch, header):
    from unittest.mock import Mock
    response = Response(b'data')
    response.headers = {} if header is None else {'Content-Length': header}
    client = HttpClient(free_reserve=10)
    client.open = Mock(return_value=response)
    usage = namedtuple('Usage', 'total used free')
    free = iter([usage(100, 0, 100), usage(100, 87, 13)])
    monkeypatch.setattr('sc2_campaign_launcher_linux.network.shutil.disk_usage', lambda _: next(free))
    target = write(tmp_path / 'map.SC2Map', b'old')
    with pytest.raises(OSError, match='reserve'):
        client.download('https://github.com/test/map', target, hashlib.sha256(b'data').hexdigest(),
                        Event(), lambda *_: None)
    assert target.read_bytes() == b'old'
    assert not list(tmp_path.glob('.sc2cl-*'))


def test_cancelled_unknown_length_download_cleans_partial(tmp_path):
    from unittest.mock import Mock
    response = Response(b'x' * (512 * 1024))
    response.headers = {}
    client = HttpClient(free_reserve=0)
    client.open = Mock(return_value=response)
    token = Event()
    target = write(tmp_path / 'map.SC2Map', b'old')

    def progress(received, _):
        if received:
            token.set()

    with pytest.raises(Cancelled):
        client.download('https://github.com/test/map', target, '0' * 64, token, progress)
    assert target.read_bytes() == b'old'
    assert not list(tmp_path.glob('.sc2cl-*'))


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        mode = 'complete'
        data = b'map data'
        calls = 0

        def do_GET(self):
            type(self).calls += 1
            if self.mode == 'busy':
                self.send_error(503)
                return
            self.send_response(200)
            if self.mode == 'broken_chunk':
                self.send_header('Transfer-Encoding', 'chunked')
            elif self.mode != 'no_length':
                self.send_header('Content-Length', str(len(self.data)))
            self.end_headers()
            if self.mode == 'broken_chunk':
                self.wfile.write(b'100\r\npartial')
            else:
                self.wfile.write(self.data)
            self.close_connection = True

        def log_message(self, *args):
            pass

    service = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=service.serve_forever, daemon=True)
    thread.start()

    class LocalTransport(urllib.request.HTTPSHandler):
        def https_open(self, request):
            def connect(host, **kwargs):
                return http.client.HTTPConnection('127.0.0.1', service.server_port, **kwargs)
            return self.do_open(connect, request)

    client = HttpClient(urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalTransport()))
    try:
        yield Handler, client
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


def test_streamed_download_over_http(server, tmp_path):
    handler, client = server
    handler.data = b'a' * (2 * 1024 * 1024 + 17)
    target = tmp_path / 'map.SC2Map'
    progress = []
    client.download('https://github.com/test/map', target, hashlib.sha256(handler.data).hexdigest(),
                    Event(), lambda received, total: progress.append((received, total)))
    assert target.read_bytes() == handler.data
    assert progress[-1] == (len(handler.data), len(handler.data))
    assert len(progress) > 5


def test_headerless_http_download_stops_at_stream_limit(server, tmp_path):
    handler, client = server
    handler.mode = 'no_length'
    handler.data = b'x' * (128 * 1024)
    client.download_limit = 1024
    target = write(tmp_path / 'map.SC2Map', b'old map')
    with pytest.raises(ValueError, match='SC2CL_MAX_DOWNLOAD_BYTES'):
        client.download('https://github.com/test/map', target, hashlib.sha256(handler.data).hexdigest(),
                        Event(), lambda *_: None)
    assert target.read_bytes() == b'old map'
    assert not list(tmp_path.glob('.sc2cl-*'))


def test_broken_chunked_response_uses_offline_catalog(server, tmp_path):
    handler, client = server
    handler.data = json.dumps([raw_campaign(campaign())]).encode()
    catalog = Catalog(tmp_path, client)
    assert not catalog.load(Event()).offline
    handler.mode = 'broken_chunk'
    result = catalog.load(Event())
    assert result.offline
    assert result.campaigns[0]['name'] == 'Test'


def test_broken_chunked_download_keeps_existing_file(server, tmp_path):
    handler, client = server
    handler.mode = 'broken_chunk'
    target = write(tmp_path / 'map.SC2Map', b'previous map')
    with pytest.raises(OSError, match='interrupted'):
        client.download('https://github.com/test/map', target, '0' * 64, Event(), lambda *_: None)
    assert target.read_bytes() == b'previous map'
    assert list(tmp_path.glob('.sc2cl-*')) == []


def test_retry_delay_can_be_cancelled(server):
    handler, client = server
    handler.mode = 'busy'

    class CancelOnWait(Event):
        def wait(self, timeout=None):
            self.set()
            return True

    with pytest.raises(Cancelled):
        client.get('https://github.com/test/map', CancelOnWait())
    assert handler.calls == 1
