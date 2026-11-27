"""Run through `odoo shell` against the disposable review database only."""
import copy
import json
import secrets
import time
from unittest.mock import patch

import requests
from odoo import api, SUPERUSER_ID
from odoo.addons.payment_xpay_lab import core
from odoo.addons.payment_xpay_lab.controllers.main import process_locked
from odoo.addons.payment_xpay_lab.models.payment_transaction import PaymentTransaction

passed = []
def check(name, condition):
    assert condition, name
    passed.append(name)
    print('XPAY PASS:', name)

provider = env.ref('payment_xpay_lab.payment_provider_xpay')
check('installed disabled by default', provider.state == 'disabled' and not provider.is_published)
provider.write({'xpay_wallet': '0x' + '11' * 20, 'xpay_api_key': 'review-only-not-a-real-credential', 'state': 'enabled'})
currency = env.ref('base.USD')
currency.active = True
partner = env['res.partner'].create({'name': 'XPay disposable test buyer'})
method = env.ref('payment_xpay_lab.payment_method_xpay_usdc')
method.active = True

def new_tx():
    tx = env['payment.transaction'].create({'provider_id': provider.id,
        'payment_method_id': method.id, 'reference': 'XPAY-REVIEW-' + secrets.token_hex(6),
        'amount': 1.25, 'currency_id': currency.id, 'partner_id': partner.id,
        'operation': 'online_redirect'})
    values = tx._get_specific_rendering_values({})
    check('rendering values do not expose token in core logs', 'access_token' not in values)
    token = tx.xpay_access_token
    env.cr.commit()
    return tx.id, token

def read_tx(tx_id):
    with env.registry.cursor() as cr:
        e = api.Environment(cr, SUPERUSER_ID, {})
        tx = e['payment.transaction'].browse(tx_id)
        return {'state': tx.state, 'quote': tx.xpay_quote, 'payload': tx.xpay_payload,
            'receipt': tx.xpay_receipt_hash, 'pending_hash': tx.xpay_pending_hash}

def payload(quote):
    return {'x402Version': 2, 'resource': quote['resource'], 'accepted': quote['terms'],
        'payload': {'signature': '0x' + '33' * 65, 'authorization': {
            'from': '0x' + '22' * 20, 'to': quote['terms']['payTo'], 'value': quote['terms']['amount'],
            'nonce': quote['nonce'], 'validAfter': '0', 'validBefore': str(quote['expires'])}}}

def receipt(quote, auth, tx_hash):
    wallet_topic = lambda a: '0x' + '0' * 24 + a[2:]
    return {'status': '0x1', 'transactionHash': tx_hash, 'blockHash': '0x' + '44' * 32,
        'logs': [{'address': core.ASSET, 'topics': [core.TRANSFER, wallet_topic(auth['from']), wallet_topic(auth['to'])],
            'data': '0x' + format(int(auth['value']), '064x')},
            {'address': core.ASSET, 'topics': [core.AUTHORIZATION_USED, wallet_topic(auth['from']), quote['nonce']], 'data': '0x'}]}

check('exact atomic amount', core.atomic_amount('1.234567') == '1234567')
for bad in ('0', '-1', 'NaN', 'Infinity', '0.0000001', '1000000001'):
    try: core.atomic_amount(bad)
    except ValueError: pass
    else: raise AssertionError('Accepted invalid amount: ' + bad)
check('invalid amounts rejected', True)

tx_id, token = new_tx()
code, quote_body, _ = process_locked(env.registry, tx_id, token=token)
check('real ORM transaction returns x402 quote', code == 402 and quote_body['accepts'][0]['amount'] == '1250000')
check('bad access token rejected', process_locked(env.registry, tx_id, token='wrong')[0] == 403)
quote = read_tx(tx_id)['quote']
p = payload(quote)
for label, mutate in [
    ('amount', lambda x: x['payload']['authorization'].update(value='1')),
    ('recipient', lambda x: x['payload']['authorization'].update(to='0x' + '55' * 20)),
    ('nonce', lambda x: x['payload']['authorization'].update(nonce='0x' + '55' * 32)),
    ('network', lambda x: x['accepted'].update(network='eip155:1')),
    ('resource', lambda x: x['resource'].update(url='https://wrong.example/')),
    ('expiry', lambda x: x['payload']['authorization'].update(validBefore='1')),
]:
    bad = copy.deepcopy(p); mutate(bad)
    with patch.object(PaymentTransaction, '_xpay_remote', side_effect=AssertionError('Must not call facilitator')):
        check('tampered ' + label + ' rejected', process_locked(env.registry, tx_id, bad, token)[0] == 422)

with env.registry.cursor() as cr:
    cr.execute('SELECT pg_advisory_lock(%s, %s)', (20261127, tx_id))
    check('concurrent payment serialized', process_locked(env.registry, tx_id, token=token)[0] == 409)
    cr.execute('SELECT pg_advisory_unlock(%s, %s)', (20261127, tx_id)); cr.commit()

def uncertain(self, path, q, signed):
    if path == 'verify': return 200, {'isValid': True}
    raise requests.Timeout('simulated lost settlement response')

with patch.object(PaymentTransaction, '_xpay_remote', uncertain):
    check('settlement timeout returns pending', process_locked(env.registry, tx_id, p, token)[0] == 202)
stored = read_tx(tx_id)
check('authorization survives separate cursor and lost response', stored['state'] == 'pending' and stored['payload'] == p)
tx_hash = '0x' + '66' * 32

def settled(self, path, q, signed):
    assert path == 'settle', 'Existing authorization must not be verified/replaced on retry'
    assert signed == p
    return 200, {'success': True, 'network': core.NETWORK, 'transaction': tx_hash}

def rpc(self, url, body, headers=None):
    if body['method'] == 'eth_chainId': return 200, {'result': '0x2105'}
    return 200, {'result': receipt(quote, p['payload']['authorization'], tx_hash)}

with patch.object(PaymentTransaction, '_xpay_remote', settled), patch.object(PaymentTransaction, '_xpay_http', rpc):
    code, body, _ = process_locked(env.registry, tx_id, token=token)
check('successful exact receipt confirms payment', code == 200 and body['paid'])
stored = read_tx(tx_id)
check('confirmed payment retains receipt and clears signature', stored['state'] == 'done' and stored['receipt'] == tx_hash and not stored['payload'])
with patch.object(PaymentTransaction, '_xpay_remote', side_effect=AssertionError('No second charge')):
    check('repeat confirmed request does not settle twice', process_locked(env.registry, tx_id, p, token)[0] == 200)

good_receipt = receipt(quote, p['payload']['authorization'], tx_hash)
for label, mutate in [
    ('failed transaction', lambda r: r.update(status='0x0')),
    ('wrong transaction hash', lambda r: r.update(transactionHash='0x' + '77' * 32)),
    ('wrong transfer amount', lambda r: r['logs'][0].update(data='0x' + format(1, '064x'))),
    ('wrong nonce event', lambda r: r['logs'][1]['topics'].__setitem__(2, '0x' + '77' * 32)),
    ('removed log', lambda r: r['logs'][0].update(removed=True)),
    ('wrong token', lambda r: r['logs'][0].update(address='0x' + '77' * 20)),
]:
    bad = copy.deepcopy(good_receipt); mutate(bad)
    try: core.validate_receipt(bad, tx_hash, quote, p['payload']['authorization'])
    except ValueError: pass
    else: raise AssertionError('Accepted bad receipt: ' + label)
    check(label + ' receipt rejected', True)

second_id, second_token = new_tx()
try:
    with env.registry.cursor() as cr:
        e = api.Environment(cr, SUPERUSER_ID, {})
        e['payment.transaction'].browse(second_id).xpay_receipt_hash = tx_hash
        cr.commit()
except Exception as error:
    check('database rejects receipt reuse across orders', 'xpay_unique_receipt' in str(error))
else: raise AssertionError('Duplicate receipt accepted')

env.invalidate_all()
check('provider credential not in quote response', 'review-only-not-a-real-credential' not in json.dumps(quote_body))

third_id, third_token = new_tx()
process_locked(env.registry, third_id, token=third_token)
third_quote = read_tx(third_id)['quote']
third_payload = payload(third_quote)
third_hash = '0x' + '88' * 32
def third_settle(self, path, q, signed):
    if path == 'verify': return 200, {'isValid': True}
    return 200, {'success': True, 'network': core.NETWORK, 'transaction': third_hash}
with patch.object(PaymentTransaction, '_xpay_remote', third_settle), patch.object(PaymentTransaction, '_xpay_http', side_effect=requests.Timeout('simulated RPC outage')):
    check('RPC outage keeps a settled payment pending', process_locked(env.registry, third_id, third_payload, third_token)[0] == 202)
check('settlement hash survives RPC outage', read_tx(third_id)['pending_hash'] == third_hash)
def third_rpc(self, url, body, headers=None):
    if body['method'] == 'eth_chainId': return 200, {'result': '0x2105'}
    return 200, {'result': receipt(third_quote, third_payload['payload']['authorization'], third_hash)}
with patch.object(PaymentTransaction, '_xpay_remote', side_effect=AssertionError('Must reuse known receipt without resubmitting settlement')), patch.object(PaymentTransaction, '_xpay_http', third_rpc):
    check('RPC recovery confirms without a second settlement call', process_locked(env.registry, third_id, token=third_token)[0] == 200)
print('XPAY_INTEGRATION_RESULT=' + json.dumps({'passed': len(passed), 'checks': passed}))
