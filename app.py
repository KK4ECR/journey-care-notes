from flask import Flask, request, jsonify, render_template, session
import requests
import os
from functools import wraps
from datetime import timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-in-production')
app.permanent_session_lifetime = timedelta(days=7)

PCO_CLIENT_ID = os.environ.get('PCO_CLIENT_ID')
PCO_SECRET = os.environ.get('PCO_SECRET')
def get_pin():
    return os.environ.get('APP_PIN', '1234')
PCO_BASE = 'https://api.planningcenteronline.com'
CARE_CATEGORY_NAME = os.environ.get('CARE_CATEGORY_NAME', 'Care Team Actions')

_category_cache = {}


def pco_auth():
    return (PCO_CLIENT_ID, PCO_SECRET)


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated


@app.route('/')
def index():
    return render_template('index.html', authenticated=session.get('authenticated', False))


@app.route('/api/login', methods=['POST'])
def login():
    pin = (request.get_json() or {}).get('pin', '')
    if pin == get_pin():
        session.permanent = True
        session['authenticated'] = True
        return jsonify({'success': True})
    return jsonify({'error': 'Incorrect PIN'}), 401



@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/search')
@require_auth
def search_people():
    name = request.args.get('q', '').strip()
    if len(name) < 2:
        return jsonify([])

    resp = requests.get(
        f'{PCO_BASE}/people/v2/people',
        params={'where[search_name]': name, 'per_page': 20, 'order': 'last_name'},
        auth=pco_auth(),
        timeout=10
    )

    if not resp.ok:
        return jsonify({'error': 'PCO search failed'}), 502

    people = []
    for person in resp.json().get('data', []):
        attrs = person.get('attributes', {})
        people.append({
            'id': person['id'],
            'name': attrs.get('name', ''),
            'membership': attrs.get('membership', '') or attrs.get('status', ''),
        })

    return jsonify(people)


@app.route('/api/care_category_id')
@require_auth
def get_care_category_id():
    global _category_cache
    if 'id' in _category_cache:
        return jsonify(_category_cache)

    resp = requests.get(
        f'{PCO_BASE}/people/v2/note_categories',
        params={'per_page': 100},
        auth=pco_auth(),
        timeout=10
    )

    if not resp.ok:
        return jsonify({'error': 'Failed to fetch categories'}), 502

    for cat in resp.json().get('data', []):
        if cat['attributes'].get('name') == CARE_CATEGORY_NAME:
            _category_cache = {'id': cat['id'], 'name': cat['attributes']['name']}
            return jsonify(_category_cache)

    _category_cache = {'id': None, 'name': None,
                       'warning': f'Category "{CARE_CATEGORY_NAME}" not found in PCO'}
    return jsonify(_category_cache)


@app.route('/api/add_note', methods=['POST'])
@require_auth
def add_note():
    body = request.get_json() or {}
    person_id = body.get('person_id')
    person_name = body.get('person_name', '')
    note_text = body.get('note', '').strip()
    category_id = body.get('category_id')
    care_member_name = body.get('care_member_name', '').strip()

    if not person_id or not note_text or not care_member_name:
        return jsonify({'error': 'Missing required fields'}), 400

    full_note = f"Care Team Note by {care_member_name}:\n\n{note_text}"

    payload = {
        'data': {
            'type': 'Note',
            'attributes': {'note': full_note}
        }
    }

    if category_id:
        payload['data']['relationships'] = {
            'note_category': {
                'data': {'type': 'NoteCategory', 'id': str(category_id)}
            }
        }

    resp = requests.post(
        f'{PCO_BASE}/people/v2/people/{person_id}/notes',
        json=payload,
        auth=pco_auth(),
        timeout=10
    )

    if not resp.ok:
        return jsonify({'error': f'Failed to save note: {resp.text}'}), 502

    return jsonify({'success': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
