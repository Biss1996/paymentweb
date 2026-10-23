import argparse, json, secrets, time, os
import requests
from app import create_app, connect, digest, signature, valid_url, settle, now

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    c=sub.add_parser('merchant'); c.add_argument('--name',required=True); c.add_argument('--webhook',required=True); c.add_argument('--return-url',required=True)
    sub.add_parser('worker'); sub.add_parser('status')
    c=sub.add_parser('reconcile'); c.add_argument('payment_id'); c.add_argument('--receipt',required=True)
    c=sub.add_parser('simulate'); c.add_argument('payment_id'); c.add_argument('--result',choices=['success','failed'],default='success')
    c=sub.add_parser('replay'); c.add_argument('event_id')
    c=sub.add_parser('disable'); c.add_argument('merchant_id')
    sub.add_parser('migrate')
    sub.add_parser('doctor')
    c=sub.add_parser('provider-status'); c.add_argument('payment_id')
    c=sub.add_parser('register-webhook'); c.add_argument('--partner-account',action='store_true',required=True)
    args=p.parse_args(); app=create_app()
    if args.cmd=='migrate':
        from database import migrate
        migrate(app); print('Database schema ready')
    elif args.cmd=='doctor':
        with connect(app) as db:
            merchants=db.execute('SELECT COUNT(*) AS n FROM merchants WHERE active=1').fetchone()['n']
            pending=db.execute('SELECT COUNT(*) AS n FROM events WHERE delivered=0').fetchone()['n']
        print(json.dumps({'mode':app.config['MODE'],'database':'postgresql' if app.config['DATABASE_URL'] else 'sqlite','active_merchants':merchants,'pending_events':pending,'public_url':app.config['BASE'],'cron_secret_configured':len(app.config['CRON_SECRET'])>=32,'provider_credentials_configured':bool(app.config['PROVIDER_KEY'] and app.config['ACCOUNT'] and app.config['PROVIDER_SECRET'])},indent=2))
    elif args.cmd=='provider-status':
        if app.config['MODE']!='live': p.error('Provider lookup requires live mode')
        with connect(app) as db:
            tx=db.execute('SELECT checkout_id,status FROM payments WHERE id=?',(args.payment_id,)).fetchone()
        if not tx or not tx['checkout_id']: p.error('No provider checkout ID recorded; use receipt reconciliation')
        from provider import HashPay
        result=HashPay(app).status(tx['checkout_id'])
        print(json.dumps(result,indent=2))
        print('Diagnostic only: status alone never credits an order. Use signed success callback or receipt reconciliation.')
    elif args.cmd=='register-webhook':
        if app.config['MODE']!='live': p.error('Webhook registration requires live mode')
        from provider import HashPay
        HashPay(app).register_webhook()
        print('Callback URL registered. Retrieve its signing secret from HashPay Settings and configure HASHPAY_WEBHOOK_SECRET before accepting payments.')
    elif args.cmd=='merchant':
        key='rk_'+secrets.token_urlsafe(32); secret=secrets.token_urlsafe(32); mid=secrets.token_hex(12)
        with connect(app) as db: db.execute('INSERT INTO merchants VALUES(?,?,?,?,?,?,1)',(mid,args.name,digest(key),secret,valid_url(args.webhook,app.config['MODE']),valid_url(args.return_url,app.config['MODE'])))
        print(json.dumps({'merchant_id':mid,'api_key':key,'webhook_secret':secret},indent=2))
    elif args.cmd=='simulate':
        if app.config['MODE']!='mock': p.error('Simulation is unavailable in live mode')
        with connect(app) as db:
            db.execute('BEGIN IMMEDIATE')
            tx=db.execute('SELECT * FROM payments WHERE id=?',(args.payment_id,)).fetchone()
            if not tx or tx['status']!='PROCESSING': p.error('Start payment on checkout before simulation')
            if args.result=='success': settle(db,tx,'MOCK'+secrets.token_hex(6),'mock_'+tx['id'])
            else: db.execute("UPDATE payments SET status='FAILED' WHERE id=?",(tx['id'],))
        print('Simulation recorded')
    elif args.cmd=='reconcile':
        if app.config['MODE']!='live': p.error('Reconciliation requires live mode')
        with connect(app) as db:
            tx=db.execute('SELECT * FROM payments WHERE id=?',(args.payment_id,)).fetchone()
        if not tx or tx['status']!='PROCESSING': p.error('Only unresolved PROCESSING payments can be reconciled')
        try:
            r=requests.post('https://api.hashback.co.ke/v1/pullapi',json={'api_key':app.config['PROVIDER_KEY'],'account_id':app.config['ACCOUNT'],'transaction_id':args.receipt},timeout=20)
            r.raise_for_status(); result=r.json(); data=result.get('data',{})
            from app import money
            if result.get('success') is not True or data.get('transactionId')!=args.receipt or data.get('billreference')!=tx['id'] or money(data.get('amount'))!=tx['amount']:
                p.error('Provider receipt, reference or amount did not match; no settlement made')
            with connect(app) as db:
                db.execute('BEGIN IMMEDIATE')
                tx=db.execute('SELECT * FROM payments WHERE id=?',(args.payment_id,)).fetchone()
                settle(db,tx,args.receipt,tx['checkout_id'])
            print('Provider-verified receipt settled and notification queued')
        except (requests.RequestException,ValueError): p.error('Provider lookup failed; payment remains unresolved')
    elif args.cmd=='worker':
        print('Delivery worker running. Run exactly one worker per database.',flush=True)
        while True:
            deliver_once(app); time.sleep(2)
    elif args.cmd=='status':
        with connect(app) as db:
            print(json.dumps({'payments':[dict(x) for x in db.execute('SELECT id,order_id,status,created FROM payments ORDER BY created DESC LIMIT 50')],'delivery':[dict(x) for x in db.execute('SELECT id,attempts,delivered,last_error FROM events ORDER BY created DESC,id DESC LIMIT 50')]},indent=2))
    elif args.cmd=='replay':
        with connect(app) as db:
            if not db.execute('UPDATE events SET delivered=0,attempts=0,next_at=0 WHERE id=?',(args.event_id,)).rowcount: p.error('Event not found')
    elif args.cmd=='disable':
        with connect(app) as db: db.execute('UPDATE merchants SET active=0 WHERE id=?',(args.merchant_id,))

def deliver_once(app,limit=4,payment_id=None,timeout=8,budget=35):
    started=time.monotonic(); processed=0
    with connect(app) as db:
        sql='SELECT e.id FROM events e WHERE delivered=0 AND attempts<12 AND next_at<=? AND lease_until<=?'
        params=[now(),now()]
        if payment_id: sql+=' AND payment=?'; params.append(payment_id)
        rows=db.execute(sql+' ORDER BY created,id LIMIT ?',(*params,limit)).fetchall()
    for row in rows:
        if time.monotonic()-started+timeout>budget: break
        lease=secrets.token_hex(16)
        # Atomic conditional update leases each event across overlapping invocations.
        with connect(app) as db:
            claimed=db.execute('UPDATE events SET lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=? AND delivered=0 AND attempts<12 AND next_at<=? AND lease_until<=?',(lease,now()+90,row['id'],now(),now())).rowcount
            if not claimed: continue
            e=db.execute('SELECT e.*,m.webhook,m.secret FROM events e JOIN payments p ON p.id=e.payment JOIN merchants m ON m.id=p.merchant WHERE e.id=?',(row['id'],)).fetchone()
        timestamp=str(now()); raw=e['body'].encode(); signed=timestamp.encode()+b'.'+raw
        error=None
        try:
            r=requests.post(e['webhook'],data=raw,headers={'Content-Type':'application/json','X-RuthPay-Signature':signature(e['secret'],signed),'X-RuthPay-Timestamp':timestamp,'X-RuthPay-Event':e['id']},timeout=timeout,allow_redirects=False)
            if not 200<=r.status_code<300: error='HTTP '+str(r.status_code)
        except requests.RequestException: error='Network error'
        with connect(app) as db:
            db.execute('UPDATE events SET delivered=?,next_at=?,last_error=?,lease_token=NULL,lease_until=0 WHERE id=? AND lease_token=?',(0 if error else 1,now()+min(3600,5*2**(e['attempts']-1)),error,e['id'],lease))
        processed+=1
    return processed

if __name__=='__main__': main()
