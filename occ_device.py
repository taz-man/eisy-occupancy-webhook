"""Occupancy device node plus all of the plugin's shared logic.

The controller's override bodies are thin wrappers around the functions in this
module so that everything can be unit tested with a fake Interface.
"""
import hmac
import json
import os
import re
import secrets
from urllib.parse import parse_qs, urlencode

import udi_interface

LOGGER = udi_interface.LOGGER
Node = udi_interface.Node

WEBHOOK_HOST = 'https://my.isy.io/api/eisy/pg3/webhook'
SECRET_FILE = 'occupancy_secret'

IN_WORDS = {'in', 'enter', 'entered', 'arrive', 'arrived', 'arrival', 'home',
            'present', 'true', 'on', '1', 'yes'}
OUT_WORDS = {'out', 'exit', 'exited', 'leave', 'left', 'departure', 'depart',
             'away', 'absent', 'false', 'off', '0', 'no'}


# ---------------------------------------------------------------- node ----
class OccupancyDevice(Node):
    id = 'occdev'
    drivers = [{'driver': 'ST', 'value': 0, 'uom': 2}]

    def __init__(self, polyglot, primary, address, name, loc_addr=None):
        super().__init__(polyglot, primary, address, name)
        self.loc_addr = loc_addr

    def set_occupied(self, occupied):
        return apply_state(self, occupied)

    def don(self, *args, **kwargs):
        self.set_occupied(True)
        refresh_locations(self.poly)

    def dof(self, *args, **kwargs):
        self.set_occupied(False)
        refresh_locations(self.poly)

    commands = {'DON': don, 'DOF': dof}


# ------------------------------------------------------------- helpers ----
def as_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def apply_state(node, occupied):
    """Set ST on a device/location node; send DON/DOF only on a real change."""
    new = 1 if occupied else 0
    changed = as_int(node.getDriver('ST')) != new
    node.setDriver('ST', new)
    if changed:
        node.reportCmd('DON' if occupied else 'DOF')
    return changed


def slugify(text):
    slug = re.sub(r'[^a-z0-9]+', '_', str(text or '').lower()).strip('_')
    return slug or 'x'


def _unique_address(prefix, name, used):
    base = (prefix + slugify(name))[:14]
    addr = base
    n = 2
    while addr in used:
        suffix = '_%d' % n
        addr = base[:14 - len(suffix)] + suffix
        n += 1
    used.add(addr)
    return addr


def plan_nodes(devices):
    """Turn the configured device list into node addresses.

    Returns {'devices': [(address, name, loc_address|None)],
             'locations': [(address, name)]}
    """
    used = set()
    loc_by_key = {}
    locations = []
    devs = []
    for entry in devices or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or '').strip()
        if not name:
            continue
        loc_name = str(entry.get('location') or '').strip()
        loc_addr = None
        if loc_name:
            key = slugify(loc_name)
            if key not in loc_by_key:
                loc_by_key[key] = _unique_address('l_', loc_name, used)
                locations.append((loc_by_key[key], loc_name))
            loc_addr = loc_by_key[key]
        devs.append((_unique_address('d_', name, used), name, loc_addr))
    return {'devices': devs, 'locations': locations}


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode('utf-8')
        except UnicodeDecodeError:
            return {}
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
        if '=' in text:
            return dict(parse_qs(text))
    return {}


def _first(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ''
    if value is None:
        return ''
    return str(value).strip()


def parse_webhook(data, token):
    """Validate one webhook delivery.

    Returns (http_status, message, event) where event is
    {'address': str, 'occupied': bool} only when status is 200.
    """
    if not isinstance(data, dict):
        return 400, 'bad request', None
    headers = {str(k).lower(): _first(v)
               for k, v in _as_dict(data.get('headers')).items()}
    params = {}
    sources = (_as_dict(data.get('body')),
               _as_dict(data.get('query') or data.get('querystring')))
    for src in sources:
        for key, val in src.items():
            params[str(key).lower()] = _first(val)

    supplied = params.get('tok') or headers.get('x-occupancy-token', '')
    auth = headers.get('authorization', '')
    if not supplied and auth.lower().startswith('bearer '):
        supplied = auth[7:].strip()
    if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
        return 403, 'forbidden', None

    address = params.get('occ', '').strip().lower()
    if not address:
        return 400, 'missing occ', None
    raw = (params.get('st') or params.get('state')
           or params.get('trigger') or '').strip().lower()
    if raw in IN_WORDS:
        return 200, 'ok', {'address': address, 'occupied': True}
    if raw in OUT_WORDS:
        return 200, 'ok', {'address': address, 'occupied': False}
    return 400, 'unrecognised state', None


def load_token(persist_dir, new=False):
    """Read (or create) the per-install secret stored under persist/."""
    path = os.path.join(persist_dir, SECRET_FILE)
    if not new:
        try:
            with open(path) as fh:
                token = fh.read().strip()
            if token:
                return token
        except OSError:
            pass
    token = secrets.token_urlsafe(18)
    os.makedirs(persist_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def webhook_base(uuid, slot, respond=False):
    kind = 'response' if respond else 'noresponse'
    return '%s/%s/%s/%s' % (WEBHOOK_HOST, kind, uuid, slot)


def build_urls(base, address, token):
    query = {'occ': address, 'tok': token}
    return (base + '?' + urlencode(dict(query, st='in')),
            base + '?' + urlencode(dict(query, st='out')))


def refresh_locations(poly):
    """A location is In when any device assigned to it is In."""
    nodes = poly.getNodes()
    state = {}
    for node in list(nodes.values()):
        loc = getattr(node, 'loc_addr', None)
        if loc:
            state[loc] = state.get(loc, False) or as_int(node.getDriver('ST')) == 1
    for loc, occupied in state.items():
        loc_node = nodes.get(loc)
        if loc_node is not None and hasattr(loc_node, 'set_occupied'):
            loc_node.set_occupied(occupied)


def sync_nodes(poly, primary, devices):
    """Create/refresh nodes for the configured devices and drop stale ones."""
    from occ_location import OccupancyLocation
    plan = plan_nodes(devices)
    wanted = {primary}
    for addr, name in plan['locations']:
        poly.addNode(OccupancyLocation(poly, primary, addr, poly.getValidName(name)))
        wanted.add(addr)
    for addr, name, loc in plan['devices']:
        poly.addNode(OccupancyDevice(poly, primary, addr, poly.getValidName(name), loc))
        wanted.add(addr)

    known = set(poly.getNodes().keys())
    try:
        known.update(n['address'] for n in poly.getNodesFromDb())
    except Exception as exc:  # DB view is best effort
        LOGGER.warning('Could not list stored nodes: %s', exc)
    for addr in sorted(known - wanted):
        poly.delNode(addr)
    refresh_locations(poly)
    return plan


# ------------------------------------------------- controller plumbing ----
def ctx(ctrl):
    state = getattr(ctrl, '_occ', None)
    if state is None:
        state = {'devices': None, 'respond': False, 'started': False}
        ctrl._occ = state
    return state


def token_for(ctrl, new=False):
    return load_token(ctrl.persist_dir, new=new)


def publish_urls(ctrl, plan):
    poly = ctrl.poly
    state = ctx(ctrl)
    token = token_for(ctrl)
    base = webhook_base(poly.uuid, poly.profileNum, state['respond'])
    poly.Notices.clear()
    if not plan['devices']:
        poly.Notices['config'] = ('No devices configured. Add your devices in the '
                                  'plugin configuration to get their In/Out URLs.')
        return
    for addr, name, _loc in plan['devices']:
        url_in, url_out = build_urls(base, addr, token)
        poly.Notices['url_' + addr] = '%s -- IN: %s  OUT: %s' % (name, url_in, url_out)


def do_sync(ctrl):
    state = ctx(ctrl)
    if state['devices'] is None:
        return None
    plan = sync_nodes(ctrl.poly, ctrl.address, state['devices'])
    publish_urls(ctrl, plan)
    return plan


def on_start(ctrl):
    state = ctx(ctrl)
    state['started'] = True
    token_for(ctrl)  # make sure a secret exists
    if state['devices'] is None:
        ctrl.poly.Notices['config'] = ('No devices configured. Add your devices in the '
                                       'plugin configuration to get their In/Out URLs.')
        return
    do_sync(ctrl)


def on_typed_data(ctrl, params):
    state = ctx(ctrl)
    params = params if isinstance(params, dict) else {}
    # A missing 'devices' key means "never configured" -- never delete nodes for that.
    state['devices'] = list(params['devices'] or []) if 'devices' in params else None
    state['respond'] = bool(params.get('send_response'))
    if state['started']:
        do_sync(ctrl)


def new_token(ctrl):
    token_for(ctrl, new=True)
    LOGGER.info('Occupancy secret regenerated; webhook URLs changed.')
    do_sync(ctrl)


def handle(ctrl, data):
    """Process one webhook delivery. Returns the HTTP-style status used."""
    state = ctx(ctrl)
    status, message, event = parse_webhook(data, token_for(ctrl))
    if event is not None:
        node = ctrl.poly.getNode(event['address'])
        if not isinstance(node, OccupancyDevice):
            status, message = 404, 'unknown device'
        else:
            node.set_occupied(event['occupied'])
            refresh_locations(ctrl.poly)
            LOGGER.info('Occupancy: %s -> %s', node.address,
                        'in' if event['occupied'] else 'out')
    if status != 200:
        LOGGER.warning('Occupancy webhook rejected (%s): %s', status, message)
    if state['respond']:
        ctrl.poly.webhookResponse(message, status)
    return status
