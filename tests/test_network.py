import hashlib
import http.client
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from sc2_campaign_launcher_linux.catalog import Catalog, raw_campaign
from sc2_campaign_launcher_linux.files import Cancelled
from sc2_campaign_launcher_linux.network import HttpClient
from conftest import campaign, write


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
            else:
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
