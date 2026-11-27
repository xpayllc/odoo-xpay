import base64
import hmac
import json

from odoo import api, http, SUPERUSER_ID
from odoo.http import request
from odoo.exceptions import ValidationError
import requests


def process_locked(registry, tx_id, submitted=None, token=None):
    if not isinstance(tx_id, int) or not 0 < tx_id < 2147483648:
        return 403, {'message': 'Invalid payment access.'}, {}
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        cr.execute('SELECT pg_try_advisory_lock(%s, %s)', (20261127, tx_id))
        if not cr.fetchone()[0]:
            return 409, {'message': 'This payment is being checked. Please retry shortly.'}, {}
        try:
            tx = env['payment.transaction'].browse(tx_id).exists()
            if not tx or tx.provider_code != 'xpay_lab':
                return 403, {'message': 'Invalid payment access.'}, {}
            if token is not None and (not token or not tx.xpay_access_token or not hmac.compare_digest(str(token), tx.xpay_access_token)):
                return 403, {'message': 'Invalid payment access.'}, {}
            result = tx._xpay_process(submitted)
            cr.commit()
            return result
        except (ValueError, ValidationError):
            cr.rollback()
            return 422, {'message': 'Payment could not be validated. Reload the quote or ask the merchant to review this transaction. Recheck an already submitted payment before signing again.'}, {}
        except requests.RequestException:
            cr.rollback()
            return 503, {'message': 'Payment service is unavailable. Keep this transaction open and check it again.'}, {}
        finally:
            cr.rollback()
            cr.execute('SELECT pg_advisory_unlock(%s, %s)', (20261127, tx_id))
            cr.commit()


class XPayController(http.Controller):
    @staticmethod
    def _secure_request():
        return request.httprequest.is_secure

    @staticmethod
    def _headers():
        return {'Cache-Control': 'no-store, private', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'}

    @http.route('/payment/xpay_lab/checkout', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def checkout(self, tx_id=None, access_token=None, **kwargs):
        if not self._secure_request():
            return request.make_response('HTTPS is required for XPay checkout.', status=403, headers=self._headers())
        try:
            tx_id = int(tx_id)
        except (ValueError, TypeError):
            return request.not_found()
        tx = request.env['payment.transaction'].sudo().browse(tx_id).exists()
        if not tx or tx.provider_code != 'xpay_lab' or not access_token or not tx.xpay_access_token or not hmac.compare_digest(str(access_token), tx.xpay_access_token):
            return request.not_found()
        return request.render('payment_xpay_lab.checkout', {'tx': tx,
            'endpoint': '/payment/xpay_lab/%s/payment' % tx.id, 'access_token': access_token}, headers=self._headers())

    @http.route('/payment/xpay_lab/<int:tx_id>/payment', type='http', auth='public', methods=['GET', 'POST'], csrf=False, sitemap=False)
    def payment(self, tx_id, **kwargs):
        headers = self._headers()
        if not self._secure_request():
            return request.make_json_response({'message': 'HTTPS is required.'}, status=403, headers=headers)
        token = request.httprequest.headers.get('X-XPay-Token', '')
        if not token or len(token) > 256:
            return request.make_json_response({'message': 'Invalid payment access.'}, status=403, headers=headers)
        submitted = None
        if request.httprequest.method == 'POST':
            raw = request.httprequest.stream.read(32769)
            signed = request.httprequest.headers.get('PAYMENT-SIGNATURE', '')
            if len(raw) > 32768 or len(signed) > 32768:
                return request.make_json_response({'message': 'Payment payload is too large.'}, status=413, headers=headers)
            try:
                submitted = json.loads(base64.b64decode(signed, validate=True)) if signed else json.loads(raw).get('paymentPayload')
                if not isinstance(submitted, dict):
                    raise ValueError('Missing payload.')
            except (ValueError, TypeError, AttributeError):
                return request.make_json_response({'message': 'Invalid payment payload.'}, status=400, headers=headers)
        code, body, extra = process_locked(request.env.registry, tx_id, submitted, token)
        return request.make_json_response(body, status=code, headers={**headers, **extra})
