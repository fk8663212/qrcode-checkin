import base64
import io
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import zxingcpp
from PIL import Image

from checkin.db import get_db
from conftest import client_for, perform_checkin, post, ticket


def count(app, table, condition='1=1'):
    with app.app_context():
        return get_db().execute(f'SELECT COUNT(*) FROM {table} WHERE {condition}').fetchone()[0]


def test_duplicate_preserves_original_time_and_operator(app):
    first = perform_checkin(client_for(app, 2), token=ticket(app))
    second = perform_checkin(client_for(app, 3), token=ticket(app))
    assert first.status_code == 201 and first.json['created']
    assert second.status_code == 200 and not second.json['created']
    assert second.json['attendee']['operator_name'] == 'staff1'
    assert first.json['attendee']['checked_at'] == second.json['attendee']['checked_at']
    assert count(app, 'checkins') == 1


def test_parallel_checkin_exactly_one_commit(app):
    barrier = Barrier(10)
    token = ticket(app)
    clients = [client_for(app, 2 + index % 2) for index in range(10)]
    def work(client):
        barrier.wait(timeout=15)
        response = perform_checkin(client, token=token)
        return response.status_code, response.json
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(work, clients))
    assert sum(status == 201 for status, _ in results) == 1
    assert all(status in (200, 201) for status, _ in results)
    assert len({value['attendee']['operator_name'] for _, value in results}) == 1
    assert count(app, 'checkins') == 1
    assert count(app, 'audit_log', "action='checkin'") == 1


def test_network_retry_same_request_and_undo_stays_undone(app):
    staff = client_for(app, 2)
    request_id = secrets.token_hex(24)
    first = perform_checkin(staff, pk=1, request_id=request_id)
    retry = perform_checkin(staff, pk=1, request_id=request_id)
    assert first.status_code == 201 and retry.status_code == 200
    admin = client_for(app, 1)
    response = post(admin, '/admin/attendees/1/undo', data={'checkin_id': '1', 'confirm': 'yes', 'reason': '測試誤報到'})
    assert response.status_code == 302
    assert perform_checkin(staff, pk=1, request_id=request_id).status_code == 409
    assert count(app, 'checkins', 'undone_at IS NULL') == 0
    assert perform_checkin(staff, pk=1).status_code == 201
    assert count(app, 'checkins') == 2


@pytest.mark.parametrize('as_participant', [False, True])
def test_unauthenticated_and_ticket_holder_cannot_checkin(app, as_participant):
    client = client_for(app)
    if as_participant:
        assert post(client, '/api/ticket', json={'token': ticket(app)}).status_code == 200
    assert perform_checkin(client, token=ticket(app)).status_code == 401
    assert client.get('/api/staff/checkin').status_code == 405
    assert client.get('/api/staff/search?q=同名').status_code == 401
    assert count(app, 'checkins') == 0


def test_ticket_read_and_link_preview_have_no_side_effect(app):
    client = client_for(app)
    assert client.get('/ticket').status_code == 200
    assert post(client, '/api/ticket', json={'token': ticket(app)}).status_code == 200
    assert post(client_for(app, 2), '/api/staff/lookup', json={'token': ticket(app)}).status_code == 200
    assert count(app, 'checkins') == 0


@pytest.mark.parametrize('bad_token', ['', '123', 'A' * 43, 'x' * 42, 1, None, ['bad']])
def test_invalid_tokens_cannot_read_or_checkin(app, bad_token):
    assert post(client_for(app), '/api/ticket', json={'token': bad_token}).status_code == 404
    staff = client_for(app, 2)
    assert post(staff, '/api/staff/lookup', json={'token': bad_token}).status_code == 404
    if bad_token is not None:
        assert perform_checkin(staff, token=bad_token).status_code == 404
    assert count(app, 'checkins') == 0


def test_reissue_revokes_old_ticket_preserves_history_and_count(app):
    old = ticket(app)
    assert perform_checkin(client_for(app, 2), token=old).status_code == 201
    admin = client_for(app, 1)
    assert post(admin, '/admin/attendees/1/reissue', data={'ticket_id': '1', 'confirm': 'yes', 'reason': '原票券遺失'}).status_code == 302
    new = ticket(app)
    assert new != old
    assert len(secrets.token_urlsafe(32)) == len(new) == 43
    assert post(client_for(app), '/api/ticket', json={'token': old}).status_code == 404
    assert perform_checkin(client_for(app, 3), token=old).status_code == 404
    value = perform_checkin(client_for(app, 3), token=new)
    assert value.status_code == 200 and value.json['attendee']['operator_name'] == 'staff1'
    assert count(app, 'checkins') == 1
    assert count(app, 'tickets') == 3
    assert count(app, 'audit_log', "action='ticket_reissue'") == 1
    assert post(admin, '/admin/attendees/1/reissue', data={'ticket_id': '1', 'confirm': 'yes', 'reason': '再次送出測試'}).status_code == 409
    assert ticket(app) == new


def test_administrative_confirmation_required(app):
    admin = client_for(app, 1)
    assert post(admin, '/admin/attendees/1/reissue', data={'reason': '沒有確認框'}).status_code == 400
    assert perform_checkin(client_for(app, 2), pk=1).status_code == 201
    assert post(admin, '/admin/attendees/1/undo', data={'confirm': 'yes', 'reason': '短', 'checkin_id': '1'}).status_code == 400
    assert post(admin, '/admin/attendees/1/undo', data={'confirm': 'yes', 'reason': '錯誤紀錄編號', 'checkin_id': '99'}).status_code == 409
    assert count(app, 'checkins', 'undone_at IS NULL') == 1


def test_public_ticket_minimal_data_and_no_roster_or_predictable_id(app):
    client = client_for(app)
    value = post(client, '/api/ticket', json={'token': ticket(app), 'attendee_pk': 2}).json
    assert set(value) == {'name', 'affiliation', 'table_no', 'checked_at', 'checked_in', 'event_name', 'qr'}
    assert 'token' not in value and 'attendee_id' not in value and 'operator_name' not in value
    assert post(client, '/api/ticket', json={'attendee_pk': 2}).status_code == 404
    assert client.get('/ticket/2').status_code == 404
    assert client.get('/admin').status_code == 302
    assert client.get('/admin/attendees/2').status_code == 302
    assert post(client, '/admin/export/tickets').status_code == 401
    assert post(client, '/admin/attendees/2/qr').status_code == 401


def test_qr_image_decodes_to_same_fragment_url(app):
    token = ticket(app)
    response = post(client_for(app), '/api/ticket', json={'token': token})
    raw = base64.b64decode(response.json['qr'].split(',', 1)[1])
    decoded = zxingcpp.read_barcode(Image.open(io.BytesIO(raw)))
    assert decoded.text == 'http://localhost/ticket#' + token
    assert count(app, 'checkins') == 0


def test_request_replay_cannot_target_another_attendee(app):
    staff = client_for(app, 2)
    request_id = secrets.token_hex(24)
    assert perform_checkin(staff, pk=1, request_id=request_id).status_code == 201
    assert perform_checkin(staff, pk=2, request_id=request_id).status_code == 409
    assert count(app, 'checkins') == 1
