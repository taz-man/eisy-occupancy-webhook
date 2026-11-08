import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import occ_device as od  # noqa: E402
from occ_device import OccupancyDevice  # noqa: E402
from occ_location import OccupancyLocation  # noqa: E402
from urllib.parse import urlsplit, parse_qs  # noqa: E402

TOKEN = 'sekret-token'


class FakeNotices(dict):
    pass


class FakePoly:
    uuid = '00:11:22:33:44:55'
    profileNum = '7'

    def __init__(self, db_nodes=()):
        self.nodes = {}
        self.db_nodes = list(db_nodes)
        self.sent = []
        self.deleted = []
        self.responses = []
        self.Notices = FakeNotices()

    # Node.__init__ / reporting
    def db_getNodeDrivers(self, address):
        return []

    def send(self, message, kind):
        self.sent.append((kind, message))

    # Interface API used by the plugin
    def getValidName(self, name):
        return name

    def addNode(self, node, conn_status=None, rename=False):
        self.nodes[node.address] = node
        return node

    def delNode(self, address):
        self.deleted.append(address)
        self.nodes.pop(address, None)

    def getNodes(self):
        return self.nodes

    def getNode(self, address):
        return self.nodes.get(address)

    def getNodesFromDb(self, addr=None):
        return [{'address': a} for a in self.db_nodes]

    def webhookResponse(self, body='Success', status=200):
        self.responses.append((body, status))


class FakeCtrl:
    address = 'controller'

    def __init__(self, tmp_path, poly=None):
        self.poly = poly or FakePoly()
        self.persist_dir = str(tmp_path / 'persist')


@pytest.fixture
def ctrl(tmp_path):
    return FakeCtrl(tmp_path)


DEVICES = [
    {'name': 'Tom iPhone', 'location': 'Home'},
    {'name': 'Ann iPhone', 'location': 'home'},
    {'name': 'Cabin', 'location': ''},
]


def cmds(poly, node, name):
    return [m['command'][0]['cmd'] for k, m in poly.sent
            if k == 'command' and 'command' in m
            and m['command'][0]['address'] == node.address
            and m['command'][0]['cmd'] == name]


# ----------------------------------------------------------- planning ----
def test_plan_addresses_valid_unique_and_short():
    plan = od.plan_nodes([{'name': 'A very long device name indeed', 'location': 'Some long location name'},
                          {'name': 'A very long device name indeed too'},
                          {'name': 'dup'}, {'name': 'DUP'}, {'name': '  '}, 'junk'])
    addrs = [a for a, _n, _l in plan['devices']] + [a for a, _n in plan['locations']]
    assert len(addrs) == len(set(addrs)) == 5
    for a in addrs:
        assert len(a) <= 14
        assert a == a.lower()
        assert all(c.isalnum() or c == '_' for c in a)


def test_plan_locations_deduped_case_insensitively():
    plan = od.plan_nodes(DEVICES)
    assert len(plan['locations']) == 1
    assert plan['locations'][0][1] == 'Home'
    locs = {a: l for a, _n, l in plan['devices']}
    assert locs['d_tom_iphone'] == locs['d_ann_iphone'] == plan['locations'][0][0]
    assert locs['d_cabin'] is None


# ------------------------------------------------------------ parsing ----
def q(**kw):
    base = {'occ': 'd_tom_iphone', 'tok': TOKEN, 'st': 'in'}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


def test_parse_ok_in_and_out():
    assert od.parse_webhook({'query': q()}, TOKEN) == (200, 'ok', {'address': 'd_tom_iphone', 'occupied': True})
    assert od.parse_webhook({'query': q(st='OUT')}, TOKEN)[2]['occupied'] is False


def test_parse_querystring_alias_and_list_values():
    data = {'querystring': {k: [v] for k, v in q().items()}}
    assert od.parse_webhook(data, TOKEN)[0] == 200


def test_parse_rejects_bad_or_missing_token():
    assert od.parse_webhook({'query': q(tok='nope')}, TOKEN)[0] == 403
    assert od.parse_webhook({'query': q(tok=None)}, TOKEN)[0] == 403
    assert od.parse_webhook({'query': q()}, '')[0] == 403
    assert od.parse_webhook({'query': q(tok='')}, '')[0] == 403


def test_parse_token_in_header_or_bearer():
    assert od.parse_webhook({'query': q(tok=None), 'headers': {'X-Occupancy-Token': TOKEN}}, TOKEN)[0] == 200
    assert od.parse_webhook({'query': q(tok=None), 'headers': {'Authorization': 'Bearer ' + TOKEN}}, TOKEN)[0] == 200


def test_parse_bad_requests():
    assert od.parse_webhook({'query': q(occ=None)}, TOKEN)[0] == 400
    assert od.parse_webhook({'query': q(st='maybe')}, TOKEN)[0] == 400
    assert od.parse_webhook({'query': q(st=None)}, TOKEN)[0] == 400
    assert od.parse_webhook(None, TOKEN)[0] == 400
    assert od.parse_webhook('x', TOKEN)[0] == 400


def test_parse_locative_trigger_fallback_and_ignored_extras():
    extra = q(st=None, trigger='exit', device='ABC-UUID', latitude='1.0', id='Home')
    assert od.parse_webhook({'query': extra}, TOKEN)[2]['occupied'] is False
    extra['trigger'] = 'enter'
    assert od.parse_webhook({'query': extra}, TOKEN)[2]['occupied'] is True


def test_parse_body_json_string_and_urlencoded_and_dict():
    assert od.parse_webhook({'body': '{"occ": "x", "tok": "%s", "st": "in"}' % TOKEN}, TOKEN)[0] == 200
    assert od.parse_webhook({'body': 'occ=x&tok=%s&trigger=enter' % TOKEN}, TOKEN)[0] == 200
    assert od.parse_webhook({'body': q()}, TOKEN)[0] == 200
    assert od.parse_webhook({'body': b'occ=x&tok=%s&st=out' % TOKEN.encode()}, TOKEN)[2]['occupied'] is False


def test_built_urls_round_trip():
    base = od.webhook_base('00:11', '7')
    assert base == 'https://my.isy.io/api/eisy/pg3/webhook/noresponse/00:11/7'
    assert od.webhook_base('u', '7', True).startswith('https://my.isy.io/api/eisy/pg3/webhook/response/')
    url_in, url_out = od.build_urls(base, 'd_tom_iphone', 'a+b/c=d&e')
    for url, want in ((url_in, True), (url_out, False)):
        query = parse_qs(urlsplit(url).query)
        status, _m, event = od.parse_webhook({'query': query}, 'a+b/c=d&e')
        assert status == 200 and event == {'address': 'd_tom_iphone', 'occupied': want}


# ------------------------------------------------------------- secret ----
def test_token_persisted_and_rotatable(tmp_path):
    d = str(tmp_path / 'p')
    t1 = od.load_token(d)
    assert t1 and od.load_token(d) == t1
    t2 = od.load_token(d, new=True)
    assert t2 != t1 and od.load_token(d) == t2
    assert oct(os.stat(os.path.join(d, od.SECRET_FILE)).st_mode & 0o777) == '0o600'


# --------------------------------------------------------------- sync ----
def start_with(ctrl, devices, respond=False):
    od.on_typed_data(ctrl, {'devices': devices, 'send_response': respond})
    od.on_start(ctrl)


def test_start_creates_nodes_and_notices(ctrl):
    start_with(ctrl, DEVICES)
    nodes = ctrl.poly.nodes
    assert set(nodes) == {'d_tom_iphone', 'd_ann_iphone', 'd_cabin', 'l_home'}
    assert isinstance(nodes['d_tom_iphone'], OccupancyDevice)
    assert isinstance(nodes['l_home'], OccupancyLocation)
    assert nodes['d_tom_iphone'].id == 'occdev' and nodes['l_home'].id == 'occloc'
    assert set(ctrl.poly.Notices) == {'url_d_tom_iphone', 'url_d_ann_iphone', 'url_d_cabin'}
    token = od.token_for(ctrl)
    assert token in ctrl.poly.Notices['url_d_cabin']
    assert '/noresponse/00:11:22:33:44:55/7?' in ctrl.poly.Notices['url_d_cabin']


def test_typed_data_before_start_does_not_touch_nodes(ctrl):
    od.on_typed_data(ctrl, {'devices': DEVICES})
    assert ctrl.poly.nodes == {}
    od.on_start(ctrl)
    assert len(ctrl.poly.nodes) == 4


def test_not_configured_never_deletes(tmp_path):
    poly = FakePoly(db_nodes=['d_old'])
    ctrl = FakeCtrl(tmp_path, poly)
    od.on_typed_data(ctrl, {})            # no 'devices' key
    od.on_start(ctrl)
    assert poly.deleted == []
    assert 'config' in poly.Notices


def test_stale_nodes_removed_including_db_only_ones(tmp_path):
    poly = FakePoly(db_nodes=['d_old', 'controller'])
    ctrl = FakeCtrl(tmp_path, poly)
    start_with(ctrl, DEVICES)
    assert poly.deleted == ['d_old']
    od.on_typed_data(ctrl, {'devices': [{'name': 'Cabin'}]})
    assert set(poly.nodes) == {'d_cabin'}
    assert 'controller' not in poly.deleted


def test_empty_list_removes_everything_and_shows_config_notice(ctrl):
    start_with(ctrl, DEVICES)
    od.on_typed_data(ctrl, {'devices': []})
    assert ctrl.poly.nodes == {}
    assert 'config' in ctrl.poly.Notices


def test_new_token_changes_urls(ctrl):
    start_with(ctrl, DEVICES)
    old = od.token_for(ctrl)
    od.new_token(ctrl)
    new = od.token_for(ctrl)
    assert new != old
    assert new in ctrl.poly.Notices['url_d_cabin'] and old not in ctrl.poly.Notices['url_d_cabin']


# ------------------------------------------------- dismissed notices ----
def test_restart_does_not_resurrect_dismissed_notices(tmp_path):
    first = FakeCtrl(tmp_path)
    start_with(first, DEVICES)
    assert len(first.poly.Notices) == 3
    # Plugin restarts: new process/poly, same persist dir; the installer had
    # closed the notices so PG3 has none stored.
    second = FakeCtrl(tmp_path)
    start_with(second, DEVICES)
    assert dict(second.poly.Notices) == {}
    assert len(second.poly.nodes) == 4        # nodes are still rebuilt


def test_restart_leaves_undismissed_notices_alone(tmp_path):
    first = FakeCtrl(tmp_path)
    start_with(first, DEVICES)
    keep = dict(first.poly.Notices)
    first.poly.Notices.clear = lambda: pytest.fail('must not clear unchanged notices')
    od.on_start(first)                        # same process, nothing changed
    assert dict(first.poly.Notices) == keep


def test_rebuild_nodes_forces_notices_back(tmp_path):
    first = FakeCtrl(tmp_path)
    start_with(first, DEVICES)
    second = FakeCtrl(tmp_path)
    start_with(second, DEVICES)
    assert dict(second.poly.Notices) == {}
    od.do_sync(second, force=True)            # DISCOVER / Rebuild Nodes
    assert set(second.poly.Notices) == {'url_d_tom_iphone', 'url_d_ann_iphone', 'url_d_cabin'}


def test_changed_devices_or_secret_or_mode_are_published_on_restart(tmp_path):
    first = FakeCtrl(tmp_path)
    start_with(first, DEVICES)
    # a device was added while the plugin was down / config changed
    second = FakeCtrl(tmp_path)
    start_with(second, DEVICES + [{'name': 'Garage'}])
    assert 'url_d_garage' in second.poly.Notices
    # response mode toggled changes every URL
    third = FakeCtrl(tmp_path)
    start_with(third, DEVICES + [{'name': 'Garage'}], respond=True)
    assert '/response/' in third.poly.Notices['url_d_garage']
    # New Secret always publishes, even after the notices were dismissed
    third.poly.Notices.clear()
    od.new_token(third)
    assert 'url_d_garage' in third.poly.Notices


def test_no_devices_notice_shown_on_every_start(tmp_path):
    for _ in range(2):
        c = FakeCtrl(tmp_path)
        od.on_typed_data(c, {})
        od.on_start(c)
        assert 'config' in c.poly.Notices


# ------------------------------------------------------------ webhooks ----
def call(ctrl, **kw):
    return od.handle(ctrl, {'query': q(**kw)})


def test_in_out_updates_device_and_location(ctrl):
    start_with(ctrl, DEVICES)
    od.on_typed_data(ctrl, {'devices': DEVICES})
    token = od.token_for(ctrl)
    nodes = ctrl.poly.nodes

    def hit(addr, st):
        return od.handle(ctrl, {'query': {'occ': addr, 'tok': token, 'st': st}})

    assert hit('d_tom_iphone', 'in') == 200
    assert nodes['d_tom_iphone'].getDriver('ST') == 1
    assert nodes['l_home'].getDriver('ST') == 1
    assert hit('d_ann_iphone', 'in') == 200
    assert hit('d_tom_iphone', 'out') == 200
    assert nodes['l_home'].getDriver('ST') == 1      # Ann still in
    assert hit('d_ann_iphone', 'out') == 200
    assert nodes['l_home'].getDriver('ST') == 0
    assert nodes['d_cabin'].getDriver('ST') == 0


def test_only_real_changes_send_commands(ctrl):
    start_with(ctrl, DEVICES)
    token = od.token_for(ctrl)
    tom = ctrl.poly.nodes['d_tom_iphone']
    for _ in range(3):
        od.handle(ctrl, {'query': {'occ': 'd_tom_iphone', 'tok': token, 'st': 'in'}})
    assert len(cmds(ctrl.poly, tom, 'DON')) == 1
    od.handle(ctrl, {'query': {'occ': 'd_tom_iphone', 'tok': token, 'st': 'out'}})
    assert len(cmds(ctrl.poly, tom, 'DOF')) == 1


def test_unknown_device_and_location_cannot_be_set(ctrl):
    start_with(ctrl, DEVICES)
    token = od.token_for(ctrl)
    assert od.handle(ctrl, {'query': {'occ': 'nobody', 'tok': token, 'st': 'in'}}) == 404
    assert od.handle(ctrl, {'query': {'occ': 'l_home', 'tok': token, 'st': 'in'}}) == 404
    assert ctrl.poly.nodes['l_home'].getDriver('ST') == 0


def test_bad_token_changes_nothing(ctrl):
    start_with(ctrl, DEVICES)
    assert od.handle(ctrl, {'query': {'occ': 'd_tom_iphone', 'tok': 'wrong', 'st': 'in'}}) == 403
    assert ctrl.poly.nodes['d_tom_iphone'].getDriver('ST') == 0


def test_response_only_when_enabled(ctrl):
    start_with(ctrl, DEVICES, respond=False)
    token = od.token_for(ctrl)
    od.handle(ctrl, {'query': {'occ': 'd_cabin', 'tok': token, 'st': 'in'}})
    assert ctrl.poly.responses == []
    od.on_typed_data(ctrl, {'devices': DEVICES, 'send_response': True})
    od.handle(ctrl, {'query': {'occ': 'd_cabin', 'tok': 'bad', 'st': 'in'}})
    od.handle(ctrl, {'query': {'occ': 'd_cabin', 'tok': token, 'st': 'out'}})
    assert ctrl.poly.responses == [('forbidden', 403), ('ok', 200)]
    assert '/response/' in ctrl.poly.Notices['url_d_cabin']


def test_program_commands_set_state(ctrl):
    start_with(ctrl, DEVICES)
    tom = ctrl.poly.nodes['d_tom_iphone']
    tom.runCmd({'cmd': 'DON'})
    assert tom.getDriver('ST') == 1 and ctrl.poly.nodes['l_home'].getDriver('ST') == 1
    tom.runCmd({'cmd': 'DOF'})
    assert tom.getDriver('ST') == 0 and ctrl.poly.nodes['l_home'].getDriver('ST') == 0


def test_state_restored_as_string_is_not_a_change():
    class P(FakePoly):
        def db_getNodeDrivers(self, address):
            return [{'driver': 'ST', 'value': '1', 'uom': 2}]
    poly = P()
    node = OccupancyDevice(poly, 'controller', 'd_x', 'X')
    assert od.apply_state(node, True) is False
    assert od.apply_state(node, False) is True
