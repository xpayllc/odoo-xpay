(function () {
    'use strict';
    function start() {
        const config = document.getElementById('xpay-checkout');
        const pay = document.getElementById('xpay-connect');
        const retry = document.getElementById('xpay-recheck');
        const status = document.getElementById('xpay-status');
        if (!config || !pay || !retry || !status) return;
        let busy = false, submitted = false;
        async function request(method, payload) {
            const response = await fetch(config.dataset.endpoint, {method, cache:'no-store', credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-XPay-Token':config.dataset.token},
                ...(payload ? {body:JSON.stringify({paymentPayload:payload})} : {})});
            const data = await response.json();
            if (![200,202,402].includes(response.status)) throw new Error(data.message || 'Payment is not confirmed. Check this same payment again.');
            return data;
        }
        function completed(data) {
            if (!data.paid) return false;
            status.textContent = 'Payment confirmed. Opening your payment status…';
            window.location.assign('/payment/status');
            return true;
        }
        async function recheck() {
            if (busy) return;
            busy=true; retry.disabled=true;
            try {
                status.textContent='Checking this payment…';
                const data=await request('GET');
                if (completed(data)) return;
                if (data.pending) { submitted=true; pay.disabled=true; }
                status.textContent=data.pending ? 'Settlement is pending. Check again shortly; do not sign another payment.' : 'No authorization has been submitted. Connect your wallet to pay.';
            } catch (error) { status.textContent=error.message; }
            finally { busy=false; retry.disabled=false; }
        }
        retry.addEventListener('click',recheck);
        pay.addEventListener('click',async () => {
            if (busy || submitted) return;
            busy=true; pay.disabled=true;
            try {
                const quote=await request('GET');
                if (completed(quote)) return;
                if (quote.pending) { submitted=true; throw new Error('A payment is already pending. Use Check existing payment.'); }
                if (!window.ethereum) throw new Error('Use an Ethereum-compatible browser wallet with Base USDC.');
                const terms=quote.accepts[0], account=(await window.ethereum.request({method:'eth_requestAccounts'}))[0];
                await window.ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId:'0x2105'}]});
                if ((await window.ethereum.request({method:'eth_chainId'})).toLowerCase() !== '0x2105') throw new Error('Switch your wallet to Base before paying.');
                const auth={from:account,to:terms.payTo,value:terms.amount,validAfter:'0',validBefore:String(quote.extensions['xpay-order'].expires),nonce:quote.extensions['xpay-order'].nonce};
                const typed={types:{
                    EIP712Domain:[{name:'name',type:'string'},{name:'version',type:'string'},{name:'chainId',type:'uint256'},{name:'verifyingContract',type:'address'}],
                    TransferWithAuthorization:[{name:'from',type:'address'},{name:'to',type:'address'},{name:'value',type:'uint256'},{name:'validAfter',type:'uint256'},{name:'validBefore',type:'uint256'},{name:'nonce',type:'bytes32'}]
                },primaryType:'TransferWithAuthorization',domain:{name:'USD Coin',version:'2',chainId:8453,verifyingContract:terms.asset},message:auth};
                status.textContent=`Review ${(Number(terms.amount)/1e6).toFixed(6)} USDC to ${terms.payTo} in your wallet.`;
                const signature=await window.ethereum.request({method:'eth_signTypedData_v4',params:[account,JSON.stringify(typed)]});
                submitted=true;
                status.textContent='Submitting the authorized payment. Keep this page open…';
                const data=await request('POST',{x402Version:2,resource:quote.resource,accepted:terms,payload:{signature,authorization:auth}});
                if (!completed(data)) status.textContent='Settlement is pending. Check this same payment again; do not sign another payment.';
            } catch (error) { status.textContent=error.code===4001 ? 'Wallet request cancelled. No new authorization was submitted.' : error.message; }
            finally { busy=false; pay.disabled=submitted; }
        });
        window.setInterval(() => { if (submitted && !document.hidden) recheck(); },15000);
    }
    if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true}); else start();
}());
