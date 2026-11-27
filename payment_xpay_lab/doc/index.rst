XPay payment provider for Odoo 18
================================

Installation
------------

1. Install payment_xpay_lab on an HTTPS staging Odoo 18 instance.
2. Open Website / Configuration / Payment Providers, or Accounting's payment
   providers menu, and choose XPay USDC.
3. As a system administrator, enter the merchant's Base receiving wallet and XPay
   merchant API credential. Alternatively set XPAY_FACILITATOR_KEY on the Odoo
   server. A credential in the provider configuration takes precedence.
4. Confirm the merchant agreement, fee and settlement permissions with XPay.
5. Enable and publish the provider only when ready for a real-wallet staging test.
   The Test state deliberately does not send mainnet payments.
6. Create an ordinary Odoo order or invoice and use Odoo's authorized payment
   flow. Select XPay USDC, connect an injected browser wallet and review/sign the
   displayed Base USDC authorization. The server confirms the exact receipt.

Do not use a production customer's order for acceptance testing. No private key
or seed phrase belongs in the module. Odoo Online is unsupported; use Odoo.sh or
a self-hosted installation that permits custom Python add-ons.

Payment-agent integration
-------------------------

An existing Odoo transaction with operation online_redirect is required. Obtain
its private checkout token through the authenticated Odoo checkout/payment flow.
Use X-XPay-Token as a request header; do not put tokens in URLs or public logs.

GET /payment/xpay_lab/TRANSACTION_ID/payment returns HTTP 402, x402 v2 JSON and
the base64 PAYMENT-REQUIRED header. The xpay-order extension contains the required
32-byte authorization nonce and expiry. Sign EIP-3009 TransferWithAuthorization
using domain USD Coin, version 2, chain 8453 and the official Base USDC contract.
Use the returned nonce, recipient, amount and expiry exactly, and validAfter 0.

POST the same endpoint with the same access header and either JSON
paymentPayload or a base64 PAYMENT-SIGNATURE header. Preserve resource and
accepted exactly. A generic x402 client choosing its own nonce is not compatible
without an adapter. HTTP 200 with paid true is confirmed; HTTP 202 is pending.
Recheck the same transaction after any ambiguous error or interruption.

Settlement and reconciliation
-----------------------------

The module stores a verified authorization durably before calling settlement.
Session advisory locks serialize payment attempts, and the same transaction and
nonce are used as the idempotency key. The nonce cannot be replaced after an
uncertain settlement. Pending payments are reconciled by cron every two minutes
when cron is running, and can also be checked from the checkout page.

If settlement is permanently rejected, an order is cancelled or the amount is
changed after signing, contact the merchant for manual resolution. There is no
unsafe reset button or automatic refund. Do not create a replacement payment
until the original authorization's outcome has been established.

Completion requires a successful included Base receipt, an exact payer/recipient
USDC Transfer and the order's AuthorizationUsed nonce event, with a unique
transaction hash. Odoo's standard payment status and post-processing handle the
related business documents. No finalized-chain guarantee is claimed.

Production acceptance
---------------------

Check a real wallet and merchant credential on staging before live sales. Test
wallet rejection, insufficient USDC, wrong network, mobile wallet behavior,
expired quotes, changed totals, duplicate receipt rejection, settlement timeout,
worker restart, background reconciliation and normal Odoo invoice/order behavior.
Confirm that reverse-proxy HTTPS information is trustworthy and correctly set.
Do not cache payment endpoints or log access tokens/authorizations.
