"""Hosted checkout. Run management and durable delivery through manage.py."""
import os, sqlite3, time, json, secrets, hashlib, hmac, re, uuid
from pathlib import Path
from database import connect, migrate, INTEGRITY_ERRORS
from urllib.parse import urlsplit
from decimal import Decimal, InvalidOperation
import requests
from flask import Flask, request, jsonify, abort, render_template

os.umask(0o077)
ROOT = Path(__file__).parent

def now(): return int(time.time())
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def signature(secret, raw): return 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
def money(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d != d.to_integral_value() or not 1 <= d <= 150000: raise ValueError()
        return int(d)
    except (InvalidOperation, ValueError): raise ValueError('Amount must be a whole KES value between 1 and 150000')

def create_app(config=None):
    app = Flask(__name__)
    app.config.update(DB=os.getenv('DATABASE_PATH',str(ROOT/'data/payments.db')),MODE=os.getenv('PAYMENT_MODE','mock'),BASE=os.getenv('PUBLIC_BASE_URL','http://localhost:3001').rstrip('/'),PROVIDER_KEY=os.getenv('HASHPAY_API_KEY',''),ACCOUNT=os.getenv('HASHPAY_ACCOUNT_ID',''),PROVIDER_SECRET=os.getenv('HASHPAY_WEBHOOK_SECRET',''),LIVE_ACK=os.getenv('HASHPAY_STK_ENABLED','no'),DATABASE_URL=os.getenv('DATABASE_URL',''),CRON_SECRET=os.getenv('CRON_SECRET',''),MAX_CONTENT_LENGTH=32768)
    if config: app.config.update(config)
    if app.config['MODE'] not in ('mock','live'): raise RuntimeError('PAYMENT_MODE must be mock or live')
    if app.config['MODE']=='live' and (not all(app.config[k] for k in ('PROVIDER_KEY','ACCOUNT','PROVIDER_SECRET')) or app.config['LIVE_ACK']!='yes' or not app.config['BASE'].startswith('https://')):
        raise RuntimeError('Live mode requires HTTPS, HashPay credentials, webhook secret and HASHPAY_STK_ENABLED=yes')
    if os.getenv('VERCEL') and (not app.config['DATABASE_URL'] or len(app.config['CRON_SECRET'])<32):
        raise RuntimeError('Vercel requires durable DATABASE_URL and CRON_SECRET of at least 32 characters')
    if not app.config['DATABASE_URL']: migrate(app)

    @app.errorhandler(ValueError)
    def invalid(e): return jsonify(error=str(e)),400
    @app.after_request
    def security(res):
        res.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})
        return res
    def merchant(db):
        key=request.headers.get('Authorization','').removeprefix('Bearer ')
        m=db.execute('SELECT * FROM merchants WHERE key_hash=? AND active=1',(digest(key),)).fetchone()
        if not m: abort(401)
        return m
    def public_payment(db,token):
        p=db.execute('SELECT * FROM payments WHERE token=?',(token,)).fetchone()
        if not p: abort(404)
        return p
    def view(p): return {k:p[k] for k in ('id','order_id','amount','currency','status','receipt','created','expires')}
    @app.get('/health')
    def health():
        with connect(app) as db: db.execute('SELECT 1')
        return jsonify(status='ok',mode=app.config['MODE'])
    @app.get('/')
    def home():
        return render_template('home.html',mode=app.config['MODE'])
    @app.get('/api/cron/deliver')
    def cron_delivery():
        secret=app.config['CRON_SECRET']
        if not secret or not hmac.compare_digest('Bearer '+secret,request.headers.get('Authorization','')): abort(401)
        from manage import deliver_once
        count=deliver_once(app,limit=4,budget=35)
        return jsonify(processed=count)
    @app.post('/api/v1/checkout/initialize')
    def initialize():
        b=request.get_json()
        if not isinstance(b,dict): abort(400)
        order=b.get('order_id','')
        if not isinstance(order,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',order): raise ValueError('Invalid order_id')
        amount=money(b.get('amount'))
        if b.get('currency','KES')!='KES': raise ValueError('Only KES is supported')
        with connect(app) as db:
            db.execute('BEGIN IMMEDIATE')
            m=merchant(db)
            p=db.execute('SELECT * FROM payments WHERE merchant=? AND order_id=?',(m['id'],order)).fetchone()
            if p and p['amount']!=amount: return jsonify(error='Order already exists with another amount'),409
            if not p:
                pid=uuid.uuid4().hex; token=secrets.token_urlsafe(32)
                db.execute('INSERT INTO payments(id,merchant,order_id,amount,token,created,expires) VALUES(?,?,?,?,?,?,?)',(pid,m['id'],order,amount,token,now(),now()+3600))
                p=db.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
            return jsonify(**view(p),checkout_url=f"{app.config['BASE']}/checkout/{p['token']}")
    @app.get('/api/v1/payments/<pid>')
    def status(pid):
        with connect(app) as db:
            m=merchant(db)
            p=db.execute('SELECT * FROM payments WHERE id=? AND merchant=?',(pid,m['id'])).fetchone()
            if not p: abort(404)
            return jsonify(view(p))
    @app.get('/checkout/<token>')
    def checkout(token):
        with connect(app) as db:
            p=public_payment(db,token)
            m=db.execute('SELECT * FROM merchants WHERE id=?',(p['merchant'],)).fetchone()
            if not m['active']: abort(403)
            return render_template('checkout.html',p=p,m=m,mode=app.config['MODE'])
    @app.get('/checkout/<token>/status')
    def checkout_status(token):
        with connect(app) as db: return jsonify(view(public_payment(db,token)))
    @app.post('/checkout/<token>/pay')
    def pay(token):
        b=request.get_json()
        if not isinstance(b,dict): abort(400)
        phone=re.sub(r'[ +]','',str(b.get('phone','')))
        if phone.startswith('0'): phone='254'+phone[1:]
        if not re.fullmatch(r'254[17][0-9]{8}',phone): raise ValueError('Enter a valid Kenyan mobile number')
        with connect(app) as db:
            db.execute('BEGIN IMMEDIATE')
            p=public_payment(db,token)
            m=db.execute('SELECT active FROM merchants WHERE id=?',(p['merchant'],)).fetchone()
            if not m['active']: abort(403)
            if p['status']!='PENDING': return jsonify(status=p['status']),409
            if now()>p['expires']: return jsonify(error='Checkout expired; contact the merchant'),410
            # Commit before network I/O. A second click cannot send another STK prompt.
            db.execute("UPDATE payments SET status='PROCESSING',phone=? WHERE id=?",(phone,p['id']))
        if app.config['MODE']=='mock':
            with connect(app) as db: db.execute('UPDATE payments SET checkout_id=? WHERE id=?',('mock_'+p['id'],p['id']))
            return jsonify(status='PROCESSING',message='Mock payment started. Use the simulation command to complete it.')
        try:
            r=requests.post('https://api.hashback.co.ke/initiatestk',json={'api_key':app.config['PROVIDER_KEY'],'account_id':app.config['ACCOUNT'],'amount':str(p['amount']),'msisdn':phone,'reference':p['id']},timeout=20)
            r.raise_for_status(); data=r.json()
            checkout_id=data.get('checkout_id') or data.get('CheckoutRequestID')
            if data.get('success') is not True or not checkout_id: raise ValueError('Provider did not acknowledge initiation')
            with connect(app) as db: db.execute('UPDATE payments SET checkout_id=COALESCE(checkout_id,?) WHERE id=?',(checkout_id,p['id']))
            return jsonify(status='PROCESSING',message='Check your phone for the M-Pesa prompt.')
        except (requests.RequestException,ValueError):
            # Unknown outcome: never automatically retry a potentially accepted charge.
            app.logger.warning('Initiation requires reconciliation: %s',p['id'])
            return jsonify(status='PROCESSING',message='Confirmation is pending. Do not pay again; contact support if this persists.'),202
    @app.post('/webhooks/hashpay')
    def provider_callback():
        if app.config['MODE']!='live': abort(404)
        raw=request.get_data()
        if not hmac.compare_digest(signature(app.config['PROVIDER_SECRET'],raw),request.headers.get('X-Hashpay-Signature','')): abort(401)
        b=request.get_json()
        if not isinstance(b,dict): abort(400)
        with connect(app) as db:
            db.execute('BEGIN IMMEDIATE')
            raw_digest=hashlib.sha256(raw).hexdigest()
            if db.execute('SELECT 1 FROM callback_log WHERE digest=?',(raw_digest,)).fetchone(): return jsonify(status='duplicate')
            # Only documented success events settle. Unknown/failure shapes remain pending review.
            if b.get('event')!='payment.success' or str(b.get('ResponseCode'))!='0': return jsonify(status='ignored')
            p=db.execute('SELECT * FROM payments WHERE id=?',(str(b.get('TransactionReference','')),)).fetchone()
            if not p: abort(404)
            if b.get('AccountID')!=app.config['ACCOUNT'] or money(b.get('TransactionAmount'))!=p['amount']: return jsonify(error='Payment mismatch'),409
            cid=b.get('CheckoutRequestID'); receipt=b.get('TransactionReceipt')
            if not isinstance(cid,str) or not cid or not isinstance(receipt,str) or not receipt: abort(400)
            if p['checkout_id'] and p['checkout_id']!=cid: return jsonify(error='Checkout mismatch'),409
            if not p['phone'] or str(b.get('Msisdn'))!=p['phone']: return jsonify(error='Payer mismatch'),409
            if p['status']=='SUCCESSFUL' and p['receipt']!=receipt: return jsonify(error='Receipt mismatch'),409
            try: settle(db,p,receipt,cid)
            except INTEGRITY_ERRORS: return jsonify(error='Receipt already assigned'),409
            db.execute('INSERT INTO callback_log VALUES(?,?,?)',(raw_digest,now(),'accepted'))
        # Best-effort immediate delivery. Outbox/cron guarantees retries if this fails.
        from manage import deliver_once
        try: deliver_once(app,limit=1,payment_id=p['id'],timeout=3,budget=4)
        except Exception: app.logger.exception('Immediate webhook delivery deferred')
        return jsonify(status='accepted')
    return app

def settle(db,p,receipt,checkout_id):
    if p['status']=='SUCCESSFUL': return
    if p['status'] not in ('PROCESSING','PENDING'): raise ValueError('Invalid payment transition')
    db.execute("UPDATE payments SET status='SUCCESSFUL',receipt=?,checkout_id=? WHERE id=?",(receipt,checkout_id,p['id']))
    event_id=uuid.uuid4().hex
    body=json.dumps({'event_id':event_id,'type':'payment.success','payment_id':p['id'],'order_id':p['order_id'],'amount_paid':p['amount'],'currency':'KES','status':'SUCCESSFUL','mpesa_receipt':receipt,'created_at':now()},separators=(',',':'))
    db.execute('INSERT INTO events(id,payment,body,created) VALUES(?,?,?,?)',(event_id,p['id'],body,now()))

def valid_url(url,mode):
    u=urlsplit(url)
    if u.username or u.password or u.fragment or not u.hostname: raise ValueError('Invalid URL')
    if u.scheme!='https' and not (mode=='mock' and u.scheme=='http' and u.hostname in ('localhost','127.0.0.1')): raise ValueError('Use HTTPS (localhost HTTP is allowed in mock mode)')
    # URLs are operator-provisioned only, never supplied by API callers.
    return url
