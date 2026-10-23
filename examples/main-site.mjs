/** Runnable Node 20+ integration reference. No npm packages required.
 * DEMO ONLY: replace seed order with your existing order DB and session auth.
 * The webhook inbox + paid update below use one SQLite transaction.
 * Node 24+ (node:sqlite) required for this runnable reference.
 */
import http from 'node:http';
import crypto from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
const base=process.env.GATEWAY_URL||'http://localhost:3001';
const key=process.env.GATEWAY_API_KEY, secret=process.env.GATEWAY_WEBHOOK_SECRET;
if(!key||!secret)throw Error('Set GATEWAY_API_KEY and GATEWAY_WEBHOOK_SECRET');
const db=new DatabaseSync(process.env.DEMO_DB||'main-site-demo.db');
db.exec(`CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, amount INTEGER NOT NULL, payment_id TEXT, paid INTEGER DEFAULT 0, receipt TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY);
INSERT OR IGNORE INTO orders(id,amount) VALUES('DEMO-1001',1500);`);
async function body(req){let chunks=[],length=0;for await(const chunk of req){length+=chunk.length;if(length>32768)throw Error('Body too large');chunks.push(chunk);}return Buffer.concat(chunks);}
const send=(res,code,obj)=>{res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(obj));};
http.createServer(async(req,res)=>{
 try{
  if(req.method==='POST'&&req.url==='/webhook'){
   const raw=await body(req), stamp=req.headers['x-ruthpay-timestamp']||'', sig=req.headers['x-ruthpay-signature']||'';
   if(!/^\d+$/.test(stamp)||Math.abs(Date.now()/1000-Number(stamp))>300)return send(res,401,{error:'Expired signature'});
   const expected='sha256='+crypto.createHmac('sha256',secret).update(stamp+'.').update(raw).digest('hex');
   if(Buffer.byteLength(sig)!==Buffer.byteLength(expected)||!crypto.timingSafeEqual(Buffer.from(sig),Buffer.from(expected)))return send(res,401,{error:'Bad signature'});
   const event=JSON.parse(raw);
   if(event.type!=='payment.success'||event.status!=='SUCCESSFUL'||event.currency!=='KES'||typeof event.event_id!=='string')return send(res,400,{error:'Unsupported event'});
   // Confirm authoritative gateway status, including payment/order association.
   const check=await fetch(base+'/api/v1/payments/'+encodeURIComponent(event.payment_id),{headers:{Authorization:'Bearer '+key},signal:AbortSignal.timeout(10000)});
   if(!check.ok)return send(res,503,{error:'Cannot verify payment; retry delivery'});
   const verified=await check.json();
   if(verified.status!=='SUCCESSFUL'||verified.order_id!==event.order_id||verified.amount!==event.amount_paid||verified.receipt!==event.mpesa_receipt)return send(res,409,{error:'Gateway mismatch'});
   db.exec('BEGIN IMMEDIATE');
   try{
    const order=db.prepare('SELECT * FROM orders WHERE id=?').get(event.order_id);
    if(!order||order.amount!==event.amount_paid||order.payment_id!==event.payment_id)throw Error('Order/amount/payment mismatch');
    if(!db.prepare('SELECT 1 FROM inbox WHERE event_id=?').get(event.event_id)){
     if(order.paid&&order.receipt!==event.mpesa_receipt)throw Error('Order already paid by another receipt');
     db.prepare('UPDATE orders SET paid=1,receipt=? WHERE id=?').run(event.mpesa_receipt,event.order_id);
     db.prepare('INSERT INTO inbox VALUES(?)').run(event.event_id);
    }
    db.exec('COMMIT');return send(res,200,{accepted:true});
   }catch(e){db.exec('ROLLBACK');return send(res,409,{error:e.message});}
  }
  if(req.method==='POST'&&req.url==='/pay'){
   // For a real site authenticate customer, check order ownership + CSRF here.
   const order=db.prepare('SELECT * FROM orders WHERE id=?').get('DEMO-1001');
   if(order.paid)return send(res,409,{error:'Already paid'});
   const r=await fetch(base+'/api/v1/checkout/initialize',{method:'POST',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify({order_id:order.id,amount:order.amount,currency:'KES'}),signal:AbortSignal.timeout(10000)});
   const p=await r.json();if(!r.ok)return send(res,r.status,p);
   db.prepare('UPDATE orders SET payment_id=? WHERE id=? AND paid=0').run(p.id,order.id);
   res.writeHead(303,{Location:p.checkout_url});return res.end();
  }
  if(req.method==='GET'&&req.url==='/status')return send(res,200,db.prepare('SELECT id,paid FROM orders WHERE id=?').get('DEMO-1001'));
  if(req.method==='GET'&&req.url==='/'){
   res.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store'});
   return res.end(`<!doctype html><meta name="viewport" content="width=device-width"><title>Main site integration demo</title><style>body{max-width:600px;margin:80px auto;font:18px system-ui;padding:24px}button{background:#168954;color:white;padding:18px;border:0;border-radius:10px}</style><h1>Main Store</h1><p>Demo order DEMO-1001 · KES 1,500</p><form method="post" action="/pay"><button>Pay with M-Pesa</button></form><p id="s">Checking order…</p><script>async function poll(){try{const r=await fetch('/status');const b=await r.json();document.querySelector('#s').textContent=b.paid?'Paid — thank you!':'Awaiting payment';if(b.paid){document.querySelector('form').hidden=true;return;}}catch{}setTimeout(poll,3000);}poll();</script>`);
  }
  send(res,404,{error:'Not found'});
 }catch{send(res,500,{error:'Request failed; retry safely'});}
}).listen(4000,'127.0.0.1',()=>console.log('Demo main site: http://localhost:4000'));
