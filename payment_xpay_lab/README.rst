X PAY LAB USDC Payments
=======================

An independent Odoo 18 payment provider from X PAY LAB LIMITED, British Virgin
Islands company 2221444. Accept Base mainnet USDC through an order-bound x402 v2
authorization. This integration is unrelated to Nexi XPay and xpayeg.

Features
--------

* Injected browser-wallet checkout, with no private keys stored or requested.
* Payment requests for existing Odoo transactions; no autonomous order creation.
* Exact amount, recipient, asset, network and nonce binding.
* Independent Base receipt checks, including the USDC AuthorizationUsed event.
* Durable payment intent, idempotent retries and a unique receipt ledger.
* Automatic pending-payment rechecks every two minutes while Odoo cron is running.

Requirements
------------

Odoo 18 on Odoo.sh or self-hosted, PostgreSQL and HTTPS. Odoo Online does not
support installation of this Python add-on. An active XPay merchant credential
with settlement permission and a Base receiving wallet are required. USD and USDC
totals only; USD is quoted 1:1 in USDC, with no exchange-rate conversion.

The extension is free to download. XPay's stated merchant processing fee is 1%,
subject to the applicable merchant agreement. Obtain that agreement and confirm
settlement permissions with support@x-pay.llc before enabling live payments.

Limitations
-----------

Base mainnet USDC only. No BNB, Solana, KRW, cards, automatic refunds, recurring
charges, WalletConnect, currency conversion or custody. Test mode never submits
real payments. The browser checkout uses an installed Ethereum-compatible wallet.
Generic x402 agents need support for the documented order-bound nonce extension.
An included successful Base receipt is required; finalized-chain guarantees are
not claimed. Validate a real-wallet payment on staging before live merchant use.

External services and privacy
-----------------------------

No external service is contacted on installation. After merchant configuration
and buyer authorization, the Odoo server sends public wallet addresses, USDC
amount, chain/token, nonce, validity period, signature and transaction resource
metadata to https://facilitator-xpay.llc/verify and /settle. The API credential is
sent only in an Authorization header to that facilitator. The public Base RPC at
https://mainnet.base.org receives chain and receipt requests, never the credential
or payment signature. Service operators also receive the merchant server's IP.
No customer names, email or billing/shipping addresses are sent by this module.

Payment receipts are retained for accounting. A signed authorization is retained
until confirmation, including after timeouts, to reconcile the same payment.
Access tokens and authorizations are restricted server-side fields. Protect the
database and backups, and never publish checkout tokens. No remote executable
code or advertising/usage analytics is loaded by the module.

XPay privacy and terms: https://x-pay.llc/legal/
Base terms: https://docs.base.org/terms-of-service
Base privacy: https://docs.base.org/privacy-policy

Support
-------

support@x-pay.llc | https://x-pay.llc/

Independent integration; no endorsement by Odoo, Circle, Coinbase or Base is
implied. See doc/index.rst for configuration, agent integration and reconciliation.
