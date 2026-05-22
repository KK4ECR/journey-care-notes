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
PCO_BASE = 'https://api.planningcenteronline.com'
CARE_CATEGORY_NAME  = os.environ.get('CARE_CATEGORY_NAME',  'Care Team Actions')
PHONE_CATEGORY_NAME = os.environ.get('PHONE_CATEGORY_NAME', 'Care Phone Calls')

_category_cache = {}   # name -> id


def get_pin():
    return os.environ.get('APP_PIN', '1234')


def pco_auth():
    return (PCO_CLIENT_ID, PCO_SECRET)


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated


def get_or_create_category(name):
    """Return the PCO note category ID for `name`, creating it if needed."""
    if name in _category_cache:
        return _category_cache[name]

    resp = requests.get(
        f'{PCO_BASE}/people/v2/note_categories',
        params={'per_page': 100},
        auth=pco_auth(),
        timeout=10
    )
    if resp.ok:
        for cat in resp.json().get('data', []):
            if cat['attributes'].get('name') == name:
                _category_cache[name] = cat['id']
                return cat['id']

    # Not found — create it
    payload = {'data': {'type': 'NoteCategory', 'attributes': {'name': name}}}
    resp = requests.post(
        f'{PCO_BASE}/people/v2/note_categories',
        json=payload,
        auth=pco_auth(),
        timeout=10
    )
    if resp.ok:
        cat_id = resp.json()['data']['id']
        _category_cache[name] = cat_id
        return cat_id

    return None


# ── Routes ────────────────────────────────────────────────────

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


@app.route('/api/categories')
@require_auth
def get_categories():
    care_id  = get_or_create_category(CARE_CATEGORY_NAME)
    phone_id = get_or_create_category(PHONE_CATEGORY_NAME)
    return jsonify({
        'care':  {'id': care_id,  'name': CARE_CATEGORY_NAME},
        'phone': {'id': phone_id, 'name': PHONE_CATEGORY_NAME},
    })


@app.route('/api/search')
@require_auth
def search_people():
    name = request.args.get('q', '').strip()
    if len(name) < 2:
        return jsonify([])

    resp = requests.get(
        f'{PCO_BASE}/people/v2/people',
        params={
            'where[search_name]': name,
            'per_page': 20,
            'order': 'last_name',
            'include': 'emails,phone_numbers',
        },
        auth=pco_auth(),
        timeout=10
    )

    if not resp.ok:
        return jsonify({'error': 'PCO search failed'}), 502

    data = resp.json()

    # Build lookup: (Type, id) -> attributes
    included = {}
    for item in data.get('included', []):
        included[(item['type'], item['id'])] = item.get('attributes', {})

    people = []
    for person in data.get('data', []):
        attrs = person.get('attributes', {})
        rels  = person.get('relationships', {})

        # Primary email (fall back to first)
        email = ''
        for ref in rels.get('emails', {}).get('data', []):
            ea = included.get(('Email', ref['id']), {})
            if ea.get('primary') or not email:
                email = ea.get('address', '')

        # Primary phone (fall back to first)
        phone = ''
        for ref in rels.get('phone_numbers', {}).get('data', []):
            pa = included.get(('PhoneNumber', ref['id']), {})
            if pa.get('primary') or not phone:
                phone = pa.get('number', '')

        people.append({
            'id':         person['id'],
            'name':       attrs.get('name', ''),
            'membership': attrs.get('membership', '') or attrs.get('status', ''),
            'email':      email,
            'phone':      phone,
        })

    return jsonify(people)


@app.route('/api/add_note', methods=['POST'])
@require_auth
def add_note():
    body             = request.get_json() or {}
    person_id        = body.get('person_id')
    note_text        = body.get('note', '').strip()
    note_type        = body.get('note_type', 'care')   # 'care' or 'phone'
    care_member_name = body.get('care_member_name', '').strip()

    if not person_id or not note_text or not care_member_name:
        return jsonify({'error': 'Missing required fields'}), 400

    category_name = PHONE_CATEGORY_NAME if note_type == 'phone' else CARE_CATEGORY_NAME
    category_id   = get_or_create_category(category_name)

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

    return jsonify({'success': True, 'category': category_name})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
