const form=document.querySelector('#pay-form'), status=document.querySelector('#status'), button=document.querySelector('#pay-button');
const base=location.pathname;
let stopped=false;
async function refresh(){
 try { const r=await fetch(base+'/status'); if(!r.ok)throw Error(); const p=await r.json();
 const expired=p.expires<Date.now()/1000;
 button.disabled=p.status!=='PENDING'||expired;
 if(p.status==='SUCCESSFUL'){status.textContent='Payment received. Your order is being updated.';form.hidden=true;stopped=true;}
 else if(p.status==='FAILED'){status.textContent='Test payment failed. Contact the merchant for a new order.';stopped=true;}
 else if(p.status==='PROCESSING'){status.textContent='Awaiting payment confirmation. Please do not pay again.';}
 else status.textContent=expired?'Checkout expired. Contact the merchant.':'Ready when you are.';
 } catch {status.textContent='Connection interrupted. Checking again shortly…';}
 if(!stopped)setTimeout(refresh,4000);
}
form.addEventListener('submit',async e=>{e.preventDefault();button.disabled=true;status.textContent='Starting payment…';try{const r=await fetch(base+'/pay',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:document.querySelector('#phone').value})});const b=await r.json();status.textContent=b.message||b.error||b.status;if(!r.ok&&r.status===400)button.disabled=false;}catch{status.textContent='Connection interrupted. Wait for confirmation before trying again.';}});
refresh();
