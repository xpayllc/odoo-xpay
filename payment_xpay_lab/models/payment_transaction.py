import base64
import json
import logging
import re
import secrets
import time

import requests
from odoo import _, fields, models
from odoo.exceptions import ValidationError
from .. import core

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    xpay_access_token = fields.Char(copy=False, groups='base.group_system')
    xpay_quote = fields.Json(copy=False, groups='base.group_system')
    xpay_payload = fields.Json(copy=False, groups='base.group_system')
    xpay_pending_hash = fields.Char(copy=False, groups='base.group_system')
    xpay_receipt_hash = fields.Char(copy=False, readonly=True, index=True)
    xpay_reconcile_after = fields.Datetime(copy=False, groups='base.group_system')
    _sql_constraints = [
        ('xpay_unique_receipt', 'unique(xpay_receipt_hash)', 'This Base transaction receipt already belongs to another payment.'),
    ]

    def _get_specific_rendering_values(self, processing_values):
        values = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'xpay_lab':
            return values
        if not self.xpay_access_token:
            self.sudo().xpay_access_token = secrets.token_urlsafe(32)
        # Odoo logs rendering values at INFO: pass the record, never a raw access token.
        return {'api_url': '/payment/xpay_lab/checkout', 'tx_id': self.id, 'xpay_tx': self.sudo()}

    def _xpay_http(self, url, body, headers=None):
        # Fixed HTTPS destinations, bounded responses, no redirects or sensitive logging.
        with requests.post(url, json=body, headers=headers or {}, timeout=(5, 30), allow_redirects=False, stream=True) as response:
            raw = bytearray()
            for chunk in response.iter_content(8192):
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError('Upstream response is too large.')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('Invalid upstream response.')
            return response.status_code, result

    def _xpay_remote(self, path, quote, payload):
        return self._xpay_http(core.FACILITATOR + '/' + path, {
            'x402Version': 2, 'paymentPayload': payload, 'paymentRequirements': quote['terms'],
        }, {'Authorization': 'Bearer ' + self.provider_id._xpay_key(),
            'Idempotency-Key': 'odoo-%s-%s' % (self.id, quote['nonce']), 'Content-Type': 'application/json'})

    def _xpay_receipt(self, tx_hash, quote, auth):
        code, chain = self._xpay_http(core.RPC, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_chainId', 'params': []})
        if code != 200 or chain.get('result') != '0x2105':
            raise ValueError('Base chain confirmation is unavailable.')
        code, result = self._xpay_http(core.RPC, {'jsonrpc': '2.0', 'id': 2, 'method': 'eth_getTransactionReceipt', 'params': [tx_hash]})
        if code != 200 or 'error' in result:
            raise ValueError('Base receipt lookup is unavailable.')
        core.validate_receipt(result.get('result'), tx_hash, quote, auth)

    def _xpay_process(self, submitted=None):
        """Called only with an owned cursor and a held session advisory lock.

        The controller commits verified payment intent before the external settlement call.
        A lost response or worker restart can therefore reconcile the same authorization.
        """
        self.ensure_one()
        if self.provider_code != 'xpay_lab':
            raise ValueError('This transaction does not use XPay.')
        if self.state == 'done':
            return 200, {'paid': True, 'transaction': self.xpay_receipt_hash, 'redirect': '/payment/status'}, {}
        if self.state not in ('draft', 'pending') or self.operation != 'online_redirect':
            raise ValueError('This transaction is not payable with XPay.')
        if self.provider_id.state != 'enabled':
            raise ValueError('XPay is not enabled. Test mode does not send real payments.')
        if self.currency_id.name not in ('USD', 'USDC'):
            raise ValueError('XPay supports USD and USDC totals only, quoted 1:1 in USDC.')
        if not self.provider_id._xpay_key() or not core.address(self.provider_id.xpay_wallet):
            raise ValueError('The merchant must finish configuring XPay.')
        amount = core.atomic_amount(self.amount)
        quote, payload = self.xpay_quote, self.xpay_payload
        now = int(time.time())
        if not quote or (not payload and now >= quote['expires']):
            if submitted:
                raise ValueError('The payment quote expired. Reload checkout before signing.')
            base_url = self.get_base_url().rstrip('/')
            quote = {'nonce': '0x' + secrets.token_hex(32), 'expires': now + 600,
                'resource': {'url': base_url + '/payment/xpay_lab/%s/payment' % self.id,
                    'description': 'Odoo payment ' + self.reference, 'mimeType': 'application/json'},
                'terms': {'scheme': 'exact', 'network': core.NETWORK, 'amount': amount,
                    'asset': core.ASSET, 'payTo': self.provider_id.xpay_wallet, 'maxTimeoutSeconds': 600,
                    'extra': {'name': 'USD Coin', 'version': '2'}}}
            self.xpay_quote = quote
        if amount != quote['terms']['amount']:
            raise ValueError('The transaction total changed. Merchant review is required.')
        if not payload and not submitted:
            body = {'x402Version': 2, 'resource': quote['resource'], 'accepts': [quote['terms']],
                'extensions': {'xpay-order': {'nonce': quote['nonce'], 'expires': quote['expires']}}}
            return 402, body, {'PAYMENT-REQUIRED': base64.b64encode(json.dumps(body).encode()).decode()}
        if not payload:
            core.validate_payload(submitted, quote, now)
            code, result = self._xpay_remote('verify', quote, submitted)
            if code != 200 or result.get('isValid') is not True:
                raise ValueError('XPay could not verify this payment authorization.')
            self.xpay_payload = payload = submitted
            self._set_pending(state_message=_('XPay authorization accepted; settlement confirmation is pending.'))
            # This cursor belongs exclusively to the payment endpoint, never a surrounding checkout.
            # The session-level advisory lock stays held across this intentional durability boundary.
            self.env.cr.commit()
        try:
            tx_hash = self.xpay_pending_hash
            result = {'success': True, 'network': core.NETWORK, 'transaction': tx_hash}
            if not tx_hash:
                code, result = self._xpay_remote('settle', quote, payload)
                tx_hash = str(result.get('transaction', '')).lower()
                if code != 200 or result.get('success') is not True or result.get('network') != core.NETWORK or not re.fullmatch(r'0x[0-9a-f]{64}', tx_hash):
                    raise ValueError('Settlement is pending.')
                self.xpay_pending_hash = tx_hash
                self.env.cr.commit()
            self._xpay_receipt(tx_hash, quote, payload['payload']['authorization'])
            self.write({'xpay_receipt_hash': tx_hash, 'provider_reference': tx_hash})
            self._set_done()
            self.xpay_payload = False
            self.xpay_pending_hash = False
            self.xpay_reconcile_after = False
            return 200, {'paid': True, 'transaction': tx_hash, 'redirect': '/payment/status'}, {
                'PAYMENT-RESPONSE': base64.b64encode(json.dumps(result).encode()).decode()}
        except (requests.RequestException, ValueError, TypeError, KeyError):
            self.xpay_reconcile_after = fields.Datetime.now()
            return 202, {'paid': False, 'pending': True,
                'message': 'Settlement is not yet confirmed. Check this same payment again; do not sign another payment.'}, {}

    def _cron_xpay_reconcile(self):
        # Every item uses its own cursor so payment intent and locks have clear lifetimes.
        pending = self.sudo().search([('provider_code', '=', 'xpay_lab'), ('state', '=', 'pending'),
            ('xpay_payload', '!=', False)], limit=20, order='xpay_reconcile_after asc, id asc')
        from ..controllers.main import process_locked
        for tx in pending:
            try:
                process_locked(self.env.registry, tx.id)
            except Exception:
                _logger.warning('XPay reconciliation requires retry for payment %s', tx.id)
