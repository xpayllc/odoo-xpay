import os

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from ..core import address


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(selection_add=[('xpay_lab', 'XPay USDC')], ondelete={'xpay_lab': 'set default'})
    xpay_wallet = fields.Char('Base receiving wallet', groups='base.group_system', copy=False)
    xpay_api_key = fields.Char('XPay merchant API credential', groups='base.group_system', copy=False)

    def _xpay_key(self):
        self.ensure_one()
        return self.sudo().xpay_api_key or os.environ.get('XPAY_FACILITATOR_KEY', '')

    @api.constrains('code', 'state', 'xpay_wallet', 'xpay_api_key')
    def _xpay_check_configuration(self):
        for provider in self.filtered(lambda p: p.code == 'xpay_lab' and p.state == 'enabled'):
            if not address(provider.xpay_wallet) or not provider._xpay_key():
                raise ValidationError(_('XPay requires a valid Base receiving wallet and a merchant API credential.'))

    def _get_default_payment_method_codes(self):
        if self.code == 'xpay_lab':
            return {'xpay_lab_usdc'}
        return super()._get_default_payment_method_codes()

    def _get_supported_currencies(self):
        currencies = super()._get_supported_currencies()
        return currencies.filtered(lambda c: c.name in ('USD', 'USDC')) if self.code == 'xpay_lab' else currencies
