from . import models, controllers


def uninstall_hook(env):
    from odoo.addons.payment import reset_payment_provider
    reset_payment_provider(env, 'xpay_lab')
