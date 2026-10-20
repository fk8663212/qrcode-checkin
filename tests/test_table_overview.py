import os
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import FileSystemLoader

from checkin.db import get_db
from conftest import client_for, perform_checkin, post


class OverviewCards(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.cards = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and 'table-attendance-card' in attrs.get('class', '').split():
            self.current = self.cards.setdefault(attrs['data-table-no'], {'class': attrs['class']})
        elif tag == 'progress':
            self.current.update(checked=int(attrs['value']), total=int(attrs['max']))


def import_roster(app, content):
    import io
    import re

    admin = client_for(app, 1)
    response = post(admin, '/admin/import', data={'file': (io.BytesIO(content.encode()), 'uneven-tables.csv')})
    preview = re.search(r'name="preview_id" value="([^"]+)"', response.text)
    assert preview, response.text
    response = post(admin, '/admin/import/confirm', data={'preview_id': preview[1]})
    assert response.status_code == 302
    return admin


def test_denominators_follow_actual_csv_groups_and_roster_moves(app):
    content = ('attendee_id,name,affiliation,table_no\n'
               'A001,來賓一,,01\nA002,來賓二,,02\n'
               'A003,來賓三,,02\nA004,來賓四,,02\n'
               'A005,來賓五,,主桌\nA006,來賓六,,主桌\n'
               'A007,未分桌來賓,,\n')
    admin = import_roster(app, content)
    assert perform_checkin(client_for(app, 2), pk=1).status_code == 201
    cards = OverviewCards(admin.get('/admin').text).cards
    assert {key: (value['checked'], value['total']) for key, value in cards.items()} == {
        '01': (1, 1), '02': (0, 3), '主桌': (0, 2), '': (0, 1)}
    assert 'is-complete' in cards['01']['class']
    assert list(cards)[-1] == ''

    # Moving an attendee in the roster changes both denominators and carries
    # that attendee's existing attendance to the new group.
    admin = import_roster(app, content.replace('A001,來賓一,,01', 'A001,來賓一,,02'))
    cards = OverviewCards(admin.get('/admin?q=來賓一&status=pending').text).cards
    assert {key: (value['checked'], value['total']) for key, value in cards.items()} == {
        '02': (1, 4), '主桌': (0, 2), '': (0, 1)}
    assert 'is-complete' not in cards['02']['class']
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 7
        assert get_db().execute('SELECT COUNT(*) FROM checkins WHERE undone_at IS NULL').fetchone()[0] == 1


def test_local_server_reloads_updated_table_template(app, tmp_path):
    templates = tmp_path / 'templates'
    templates.mkdir()
    template = templates / 'admin.html'
    template.write_text('{% extends "base.html" %}{% block content %}'
                        '<section><h2>各桌報到人數</h2><div class="table-stats">'
                        '<div class="table-chip"><span>01</span><strong>0 / 2</strong></div>'
                        '</div></section>{% endblock %}', encoding='utf-8')
    os.utime(template, (1, 1))
    app.jinja_loader = FileSystemLoader([str(templates), str(Path(app.root_path) / 'templates')])
    admin = client_for(app, 1)
    assert 'class="table-chip"' in admin.get('/admin').text

    # A running local server has cached the prior HTML. Deploy the new template
    # without recreating the application, exactly as when editing the local app.
    template.write_text((Path(app.root_path) / 'templates/admin.html').read_text(), encoding='utf-8')
    response = admin.get('/admin')
    assert 'id="table-overview-title"' in response.text, 'Old cached HTML is still served with the new CSS'
    assert 'class="table-chip"' not in response.text


def test_stylesheet_url_changes_when_asset_is_updated(app, tmp_path):
    static = tmp_path / 'static'
    static.mkdir()
    stylesheet = static / 'app.css'
    stylesheet.write_text('/* initial stylesheet */')
    os.utime(stylesheet, (1, 1))
    app.static_folder = str(static)
    with app.test_request_context():
        from flask import url_for
        before = url_for('static', filename='app.css')
        stylesheet.write_text('/* updated table layout */')
        after = url_for('static', filename='app.css')
    assert before != after, 'Old and new stylesheets share the same browser cache key'
