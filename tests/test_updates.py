import pytest

from sc2_campaign_launcher_linux import updates


def mock_response(monkeypatch, response):
    urls = []

    def get_json(self, url):
        urls.append(url)
        return response

    monkeypatch.setattr(updates.HttpClient, 'json', get_json)
    return urls


@pytest.mark.parametrize('current, expected', [('a' * 40, 'current'), ('b' * 40, 'different')])
def test_linux_update_checks_public_branch_without_login(monkeypatch, current, expected):
    urls = mock_response(monkeypatch, {'sha': 'a' * 40})
    monkeypatch.setattr(updates, 'current_revision', lambda: current)
    monkeypatch.setattr(updates.shutil, 'which', lambda name: None)
    message, url = updates.check_update('linux')
    assert expected in message
    assert urls == ['https://api.github.com/repos/RogerRoger29/SC2_Campaign_Launcher_Linux/commits/feature/ccm-manager']
    assert url == 'https://github.com/RogerRoger29/SC2_Campaign_Launcher_Linux/tree/feature/ccm-manager'


def test_windows_update_ignores_drafts_and_prereleases(monkeypatch):
    mock_response(monkeypatch, [{'draft': True}, {'prerelease': True}])
    message, url = updates.check_update('win32')
    assert 'No Windows release' in message
    assert url.endswith('/SC2_Campaign_Launcher_Linux/releases')


def test_windows_update_recognizes_installed_release(monkeypatch):
    release_url = 'https://github.com/RogerRoger29/SC2_Campaign_Launcher_Linux/releases/tag/v' + updates.__version__
    mock_response(monkeypatch, [{'tag_name': 'v' + updates.__version__, 'html_url': release_url}])
    message, url = updates.check_update('win32')
    assert message.startswith('You have release')
    assert url == release_url


def test_update_network_failure_reaches_error_handler(monkeypatch):
    def unavailable(self, url):
        raise OSError('GitHub is unavailable')

    monkeypatch.setattr(updates.HttpClient, 'json', unavailable)
    with pytest.raises(OSError, match='GitHub is unavailable'):
        updates.check_update('linux')
