import json
import re
import sqlite3

import pytest

from checkin import create_app
from checkin.db import get_db, now, transaction
from conftest import client_for, perform_checkin, post, ticket


@pytest.mark.parametrize('path,english,chinese', [
    ('/', 'A warm welcome awaits.', '相聚，從這裡開始。'),
    ('/ticket', 'Your personal ticket', '個人電子票券'),
    ('/bingo', 'Get your lucky card', '領取你的幸運卡'),
    ('/bingo/display', 'Tonight’s lucky number', '今晚的幸運號碼'),
    ('/raffle/display', 'Tonight’s prizes', '今晚的獎品'),
])
def test_public_pages_offer_both_languages(app, path, english, chinese):
    client = app.test_client()
    for language, phrase, html_language in [('en', english, 'en'), ('zh-TW', chinese, 'zh-Hant')]:
        response = client.get(path + '?lang=' + language)
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f'<html lang="{html_language}">' in html and phrase in html
        assert 'data-ui-language="en"' in html and 'data-ui-language="zh-TW"' in html
        assert response.headers['Content-Language'] == language
        assert 'no-store' in response.headers['Cache-Control']
        assert 'noindex' in response.headers['X-Robots-Tag']


@pytest.mark.parametrize('browser,expected', [
    ('en-US,en;q=0.9', 'en'), ('en-GB', 'en'), ('en', 'en'),
    ('zh-TW,zh;q=0.9,en;q=0.8', 'zh-TW'), ('zh-Hant', 'zh-TW'),
    ('zh-CN', 'zh-TW'), ('fr-FR', 'zh-TW'), ('', 'zh-TW'),
    ('zh-TW;q=0.5,en-US;q=0.9', 'en'),
])
def test_browser_language_negotiation(app, browser, expected):
    response = app.test_client().get('/ticket', headers={'Accept-Language': browser})
    assert response.headers['Content-Language'] == expected
    assert not any(header.startswith('guest_language=') for header in response.headers.getlist('Set-Cookie'))


def test_manual_choice_persists_across_ticket_bingo_and_projection(app):
    client = app.test_client()
    selected = client.get('/ticket?lang=en', headers={'Accept-Language': 'zh-TW'})
    preference = next(header for header in selected.headers.getlist('Set-Cookie') if header.startswith('guest_language='))
    assert 'HttpOnly' in preference and 'SameSite=Lax' in preference and 'Path=/' in preference
    assert 'Max-Age=31536000' in preference
    for path in ['/ticket', '/bingo', '/bingo/display', '/raffle/display']:
        assert client.get(path, headers={'Accept-Language': 'zh-TW'}).headers['Content-Language'] == 'en'
    client.get('/bingo?lang=zh-TW')
    assert client.get('/ticket', headers={'Accept-Language': 'en-US'}).headers['Content-Language'] == 'zh-TW'


def test_explicit_query_and_api_language_header_precede_cookie(app):
    client = client_for(app)
    client.get('/ticket?lang=zh-TW')
    response = client.post('/api/ticket', json={'token': 'invalid'}, headers={'X-UI-Language': 'en'})
    assert response.status_code == 403 and 'Refresh' in response.json['error']
    response = client.post('/api/ticket?lang=zh-TW', json={}, headers={'X-UI-Language': 'en'})
    assert response.headers['Content-Language'] == 'zh-TW' and '安全驗證' in response.json['error']
    assert not any(header.startswith('guest_language=') for header in response.headers.getlist('Set-Cookie'))


@pytest.mark.parametrize('language', ['de', '<script>alert(1)</script>', '', 'EN', 'zh'])
def test_invalid_choice_is_ignored_and_not_stored(app, language):
    client = app.test_client()
    client.get('/ticket?lang=en')
    response = client.get('/ticket', query_string={'lang': language}, headers={'Accept-Language': 'zh-TW'})
    assert response.headers['Content-Language'] == 'en'
    assert not any(header.startswith('guest_language=') for header in response.headers.getlist('Set-Cookie'))
    assert '<script>alert(1)</script>' not in response.get_data(as_text=True)


def test_preference_cookie_is_secure_in_production(tmp_path):
    app = create_app({'TESTING': True, 'CHECKIN_ENV': 'production', 'DATABASE': str(tmp_path / 'https.sqlite3'),
                      'PUBLIC_BASE_URL': 'https://event.example.test', 'SECRET_KEY': 'test-language-production-secret-0000',
                      'INITIAL_ADMIN_USERNAME': '', 'INITIAL_ADMIN_PASSWORD_HASH': ''})
    response = app.test_client().get('/ticket?lang=en', base_url='https://event.example.test')
    preference = next(header for header in response.headers.getlist('Set-Cookie') if header.startswith('guest_language='))
    assert 'Secure' in preference and 'HttpOnly' in preference and 'SameSite=Lax' in preference


@pytest.mark.parametrize('path', ['/admin', '/admin/import', '/admin/users', '/staff', '/bingo/host', '/raffle/host', '/login'])
def test_operator_pages_stay_chinese_even_with_english_preference(app, path):
    client = client_for(app, 1)
    client.get('/ticket?lang=en')
    response = client.get(path + '?lang=en', headers={'Accept-Language': 'en-US', 'X-UI-Language': 'en'})
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and '<html lang="zh-Hant">' in html
    assert 'public-strings' not in html and 'data-ui-language' not in html
    assert 'Content-Language' not in response.headers


def test_public_projection_hides_operator_navigation_even_when_signed_in(app):
    client = client_for(app, 1)
    html = client.get('/raffle/display?lang=en').get_data(as_text=True)
    assert '管理總覽' not in html and '工作人員' not in html and '登出' not in html
    assert 'Tonight’s prizes' in html
    assert '管理總覽' in client.get('/admin').get_data(as_text=True)


def test_language_switching_does_not_check_in_claim_or_replace_ticket(app):
    client = client_for(app)
    original = ticket(app)
    for path in ['/ticket?lang=en', '/ticket?lang=zh-TW', '/bingo?lang=en', '/raffle/display?lang=en']:
        assert client.get(path).status_code == 200
    for path in ['/api/bingo/claim?lang=en', '/api/staff/checkin?lang=en']:
        assert client.get(path).status_code == 405
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0
        assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 0
        assert get_db().execute('SELECT COUNT(*) FROM tickets').fetchone()[0] == 2
    assert ticket(app) == original


def test_english_ticket_query_preserves_identity_and_privacy(app):
    client = client_for(app)
    token = ticket(app)
    other = ticket(app, 2)
    client.get('/ticket?lang=en')
    response = post(client, '/api/ticket', json={'token': token})
    assert response.status_code == 200 and response.json['name'] == '同名來賓'
    assert response.json['raffle_label'] == 'P001' and not response.json['checked_in']
    assert token not in response.get_data(as_text=True) and other not in response.get_data(as_text=True)
    assert 'attendees' not in response.json and 'operator_name' not in response.json
    assert post(client, '/api/staff/checkin?lang=en', json={'token': token, 'request_id': 'a' * 48}).status_code == 401


@pytest.mark.parametrize('invalid', ['bad', 'A' * 43])
def test_invalid_ticket_and_expired_csrf_errors_are_english(app, invalid):
    client = client_for(app)
    client.get('/ticket?lang=en')
    response = post(client, '/api/ticket', json={'token': invalid})
    assert response.status_code == 404 and all(ord(char) < 128 for char in response.json['error'])
    expired = client.post('/api/ticket', json={'token': ticket(app)})
    assert expired.status_code == 403 and 'Refresh this page' in expired.json['error']


def test_english_eligibility_error_then_same_card_after_language_change(app):
    client = client_for(app)
    client.get('/ticket?lang=en')
    token = ticket(app)
    refused = post(client, '/api/bingo/claim', json={'token': token})
    assert refused.status_code == 403 and 'check you in first' in refused.json['error']
    perform_checkin(client_for(app, 2), token=token)
    closed = post(client, '/api/bingo/claim', json={'token': token})
    assert closed.status_code == 409 and 'registration is closed' in closed.json['error']
    with app.app_context(), transaction() as db:
        db.execute('UPDATE bingo_games SET registration_open=1 WHERE id=1')
    first = post(client, '/api/bingo/claim', json={'token': token})
    assert first.status_code == 200
    client.get('/bingo?lang=zh-TW')
    second = post(client, '/api/bingo/claim', json={'token': token})
    assert second.status_code == 200 and first.json['card']['id'] == second.json['card']['id']
    assert first.json['card']['numbers'] == second.json['card']['numbers']


def test_replaced_ticket_is_rejected_with_english_next_step(app):
    token = ticket(app)
    with app.app_context(), transaction() as db:
        db.execute('UPDATE tickets SET revoked_at=? WHERE token=?', (now(), token))
    client = client_for(app)
    client.get('/ticket?lang=en')
    for path in ['/api/ticket', '/api/bingo/claim']:
        response = post(client, path, json={'token': token})
        assert response.status_code == 404 and 'latest ticket' in response.json['error']


def test_rate_limit_error_includes_english_next_step(app):
    app.config['TICKET_LIMIT'] = 1
    client = client_for(app)
    client.get('/ticket?lang=en')
    assert post(client, '/api/ticket', json={'token': ticket(app)}).status_code == 200
    response = post(client, '/api/ticket', json={'token': ticket(app)})
    assert response.status_code == 429 and 'wait a minute' in response.json['error']
    assert response.headers['Retry-After'] == '60'


def test_database_failure_and_html_errors_are_translated(app, monkeypatch):
    from checkin import views

    def unavailable(_token):
        raise sqlite3.OperationalError('locked')

    client = client_for(app)
    client.get('/ticket?lang=en')
    monkeypatch.setattr(views, 'find_ticket', unavailable)
    response = post(client, '/api/ticket', json={'token': ticket(app)})
    assert response.status_code == 503 and 'safe to retry' in response.json['error']
    html_error = post(client, '/ticket')
    assert html_error.status_code == 405 and 'Unable to complete this request' in html_error.get_data(as_text=True)
    assert 'href="/login"' not in html_error.get_data(as_text=True)


def test_event_text_is_preserved_escaped_and_catalog_is_safe_json(app):
    app.config.update(EVENT_NAME='晚宴 Dinner <img src=x onerror=alert(1)>', EVENT_LOCATION='大廳 / Main Hall')
    response = app.test_client().get('/?lang=en')
    html = response.get_data(as_text=True)
    assert '晚宴 Dinner &lt;img' in html and '<img src=x' not in html
    assert '大廳 / Main Hall' in html and 'Venue' in html
    match = re.search(r'<script type="application/json" id="public-strings">(.*?)</script>', html, re.S)
    catalog = json.loads(match.group(1))
    assert catalog['language'] == 'en'
    assert catalog['strings']['待報到'] == 'Not checked in'
    assert not any(key in catalog for key in ('token', 'attendees', 'password'))
