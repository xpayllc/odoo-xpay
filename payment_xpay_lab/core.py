"""Pure validation for the XPay Base USDC payment flow."""
import re
from decimal import Decimal, InvalidOperation

NETWORK = 'eip155:8453'
ASSET = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
AUTHORIZATION_USED = '0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5'
FACILITATOR = 'https://facilitator-xpay.llc'
RPC = 'https://mainnet.base.org'


def address(value):
    return isinstance(value, str) and bool(re.fullmatch(r'0x[0-9a-fA-F]{40}', value)) and int(value, 16) != 0


def atomic_amount(value):
    try:
        amount = Decimal(str(value))
        units = amount * 1000000
        if not amount.is_finite() or amount <= 0 or amount > Decimal('1000000000') or units != units.to_integral_value():
            raise ValueError('The amount must be positive with no more than six decimals.')
        return str(int(units))
    except (InvalidOperation, TypeError, OverflowError) as exc:
        raise ValueError('Invalid USDC amount.') from exc


def validate_payload(payload, quote, now):
    if not isinstance(payload, dict) or payload.get('x402Version') != 2:
        raise ValueError('An x402 v2 payment authorization is required.')
    if payload.get('accepted') != quote['terms'] or payload.get('resource') != quote['resource']:
        raise ValueError('The authorization must match this transaction and its payment terms.')
    inner = payload.get('payload')
    if not isinstance(inner, dict) or not isinstance(inner.get('authorization'), dict):
        raise ValueError('Invalid authorization.')
    auth = inner['authorization']
    if not isinstance(inner.get('signature'), str) or not re.fullmatch(r'0x[0-9a-fA-F]{130}', inner['signature']):
        raise ValueError('A 65-byte wallet signature is required.')
    if not address(auth.get('from')) or not address(auth.get('to')):
        raise ValueError('Invalid wallet address.')
    if auth['to'].lower() != quote['terms']['payTo'].lower() or str(auth.get('value')) != quote['terms']['amount'] or auth.get('nonce') != quote['nonce']:
        raise ValueError('Recipient, amount and nonce must match the transaction quote.')
    if any(not isinstance(auth.get(k), (str, int)) or isinstance(auth.get(k), bool) or not re.fullmatch(r'[0-9]{1,12}', str(auth[k])) for k in ('validAfter', 'validBefore')):
        raise ValueError('Invalid authorization validity period.')
    if int(auth['validAfter']) != 0 or not now < int(auth['validBefore']) <= quote['expires'] or now >= quote['expires']:
        raise ValueError('The payment authorization has expired or is not yet valid.')
    return auth


def validate_receipt(receipt, tx_hash, quote, auth):
    if not isinstance(receipt, dict) or receipt.get('status') != '0x1' or receipt.get('transactionHash', '').lower() != tx_hash.lower() or not receipt.get('blockHash'):
        raise ValueError('A successful included Base receipt is required.')
    transfer_found = nonce_found = False
    for log in receipt.get('logs', []):
        if not isinstance(log, dict) or log.get('removed') or str(log.get('address', '')).lower() != ASSET.lower():
            continue
        topics = log.get('topics', [])
        if not isinstance(topics, list) or len(topics) != 3 or any(not isinstance(t, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', t) for t in topics):
            continue
        payer = '0x' + topics[1][-40:]
        if payer.lower() != auth['from'].lower():
            continue
        if topics[0].lower() == AUTHORIZATION_USED and topics[2].lower() == quote['nonce'].lower():
            nonce_found = True
        if topics[0].lower() == TRANSFER and ('0x' + topics[2][-40:]).lower() == quote['terms']['payTo'].lower():
            data = log.get('data', '')
            if isinstance(data, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', data) and int(data, 16) == int(quote['terms']['amount']):
                transfer_found = True
    if not transfer_found or not nonce_found:
        raise ValueError('The receipt must contain the expected USDC transfer and order-bound authorization nonce.')
