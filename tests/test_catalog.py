from threading import Event
from unittest.mock import Mock

import pytest

from sc2_campaign_launcher_linux.catalog import Catalog, raw_campaign
from sc2_campaign_launcher_linux.files import Cancelled
from sc2_campaign_launcher_linux.network import CheckedRedirect, HttpClient, validate_url
from conftest import campaign
from test_library import Response


def test_catalog_is_available_offline(tmp_path):
    client = Mock()
    client.json.return_value = [raw_campaign(campaign())]
    catalog = Catalog(tmp_path, client)
    assert not catalog.load(Event()).offline
    client.json.side_effect = OSError('offline')
    cached = catalog.load(Event())
    assert cached.offline
    assert cached.campaigns[0]['name'] == 'Test'


def test_invalid_refresh_does_not_replace_cached_catalog(tmp_path):
    client = Mock()
    catalog = Catalog(tmp_path, client)
    client.json.return_value = [raw_campaign(campaign())]
    catalog.load(Event())
    client.json.return_value = [{'title': 'broken'}]
    assert catalog.load(Event()).campaigns[0]['name'] == 'Test'


def test_cancellation_does_not_return_stale_success(tmp_path):
    client = Mock()
    client.json.side_effect = Cancelled()
    with pytest.raises(Cancelled):
        Catalog(tmp_path, client).load(Event())


@pytest.mark.parametrize('url', ['file:///tmp/secret', 'http://github.com/a',
                               'https://github.com.evil.invalid/a', 'https://user@github.com/a',
                               'https://github.com:444/a', 'https://example.com/a'])
def test_untrusted_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_redirect_is_validated_before_following():
    handler = CheckedRedirect()
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, '', {}, 'http://localhost/secret')


def test_metadata_has_a_size_limit():
    client = HttpClient()
    client.open = Mock(return_value=Response(b'x' * 101))
    with pytest.raises(ValueError, match='allowed size'):
        client.get('https://github.com/test', limit=100)
