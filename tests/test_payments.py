import unittest, tempfile, json, os
from unittest.mock import patch, Mock
from app import create_app, connect, digest, signature, settle
from manage import deliver_once

class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'DB':self.temp.name+'/test.db','MODE':'mock','DATABASE_URL':'','PROVIDER_SECRET':'provider-secret','ACCOUNT':'HPTEST'})
        self.c=self.app.test_client(); self.auth={'Authorization':'Bearer test-key'}
        with connect(self.app) as db:
            db.execute('INSERT INTO merchants VALUES(?,?,?,?,?,?,1)',('m1','Main Store',digest('test-key'),'merchant-secret','http://localhost:4000/webhook','http://localhost:4000'))
    def tearDown(self): self.temp.cleanup()
    def initialize(self,order='ORDER-1',amount=1500):
        return self.c.post('/api/v1/checkout/initialize',json={'order_id':order,'amount':amount},headers=self.auth)
    def start(self):
        r=self.initialize().json
        token=r['checkout_url'].split('/')[-1]
        self.assertEqual(self.c.post('/checkout/'+token+'/pay',json={'phone':'0712345678'}).status_code,200)
        return r,token
    def callback(self,p,**changes):
        b={'event':'payment.success','ResponseCode':0,'TransactionReference':p['id'],'AccountID':'HPTEST','TransactionAmount':1500,'CheckoutRequestID':'mock_'+p['id'],'TransactionReceipt':'RCPT01','Msisdn':254712345678}
        b.update(changes); raw=json.dumps(b).encode(); self.app.config['MODE']='live'
        with patch('manage.deliver_once',return_value=0):
            return self.c.post('/webhooks/hashpay',data=raw,headers={'Content-Type':'application/json','X-Hashpay-Signature':signature('provider-secret',raw)})
    def test_auth_validation(self):
        self.assertEqual(self.c.post('/api/v1/checkout/initialize',json={'order_id':'x','amount':10}).status_code,401)
        for x in (0,-1,1.2,'NaN','Infinity',150001): self.assertEqual(self.initialize(amount=x).status_code,400)
        self.assertEqual(self.c.get('/api/v1/payments/unknown',headers=self.auth).status_code,404)
    def test_idempotency_and_amount_conflict(self):
        a=self.initialize().json; b=self.initialize().json
        self.assertEqual(a['id'],b['id']); self.assertEqual(self.initialize(amount=2).status_code,409)
    def test_checkout_does_not_accept_browser_amount(self):
        p,t=self.start()
        self.assertEqual(self.c.post('/checkout/'+t+'/pay',json={'phone':'0712345678','amount':1}).status_code,409)
        self.assertEqual(self.c.get('/checkout/'+t+'/status').json['amount'],1500)
        page=self.c.get('/checkout/'+t)
        self.assertEqual(page.status_code,200); self.assertIn(b'TEST MODE',page.data)
    def test_signed_success_duplicate_and_outbox(self):
        p,t=self.start(); self.assertEqual(self.callback(p).status_code,200)
        self.assertEqual(self.callback(p).status_code,200)
        self.assertEqual(self.c.get('/checkout/'+t+'/status').json['status'],'SUCCESSFUL')
        with connect(self.app) as db: self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
    def test_signature_and_mismatches_rejected(self):
        p,t=self.start(); self.app.config['MODE']='live'
        self.assertEqual(self.c.post('/webhooks/hashpay',json={}).status_code,401)
        for changes in ({'TransactionAmount':1},{'AccountID':'OTHER'},{'CheckoutRequestID':'wrong'},{'Msisdn':254711111111}):
            self.assertEqual(self.callback(p,**changes).status_code,409)
        self.assertEqual(self.c.get('/checkout/'+t+'/status').json['status'],'PROCESSING')
    def test_receipt_cannot_pay_two_orders(self):
        p,_=self.start(); self.assertEqual(self.callback(p).status_code,200)
        self.app.config['MODE']='mock'; q=self.initialize('ORDER-2').json; t=q['checkout_url'].split('/')[-1]
        self.c.post('/checkout/'+t+'/pay',json={'phone':'0712345678'})
        self.assertEqual(self.callback(q).status_code,409)
        self.assertEqual(self.c.get('/checkout/'+t+'/status').json['status'],'PROCESSING')
    def test_tenant_isolation(self):
        p=self.initialize().json
        with connect(self.app) as db: db.execute('INSERT INTO merchants VALUES(?,?,?,?,?,?,1)',('m2','Other',digest('other-key'),'s','http://localhost','http://localhost'))
        self.assertEqual(self.c.get('/api/v1/payments/'+p['id'],headers={'Authorization':'Bearer other-key'}).status_code,404)
    def test_expiry_and_disabled_merchant(self):
        p=self.initialize().json; t=p['checkout_url'].split('/')[-1]
        with connect(self.app) as db: db.execute('UPDATE payments SET expires=0')
        self.assertEqual(self.c.post('/checkout/'+t+'/pay',json={'phone':'0712345678'}).status_code,410)
        with connect(self.app) as db: db.execute('UPDATE merchants SET active=0')
        self.assertEqual(self.c.get('/checkout/'+t).status_code,403)
    def test_delivery_retries_and_signature(self):
        p,_=self.start(); self.callback(p)
        with patch('manage.requests.post',return_value=Mock(status_code=500)) as post:
            deliver_once(self.app)
            kwargs=post.call_args.kwargs; stamp=kwargs['headers']['X-RuthPay-Timestamp']
            expected=signature('merchant-secret',stamp.encode()+b'.'+kwargs['data'])
            self.assertEqual(kwargs['headers']['X-RuthPay-Signature'],expected)
        with connect(self.app) as db:
            e=db.execute('SELECT * FROM events').fetchone(); self.assertEqual(e['delivered'],0); self.assertEqual(e['attempts'],1)
            db.execute('UPDATE events SET next_at=0')
        with patch('manage.requests.post',return_value=Mock(status_code=204)): deliver_once(self.app)
        with connect(self.app) as db: self.assertEqual(db.execute('SELECT delivered FROM events').fetchone()[0],1)
    def test_unknown_event_never_means_failure(self):
        p,t=self.start(); self.callback(p,event='unknown')
        self.assertEqual(self.c.get('/checkout/'+t+'/status').json['status'],'PROCESSING')
    def test_live_initiation_unknown_does_not_retry(self):
        p=self.initialize().json; t=p['checkout_url'].split('/')[-1]; self.app.config['MODE']='live'
        with patch('app.requests.post',side_effect=__import__('requests').Timeout) as post:
            self.assertEqual(self.c.post('/checkout/'+t+'/pay',json={'phone':'0712345678'}).status_code,202)
            self.assertEqual(self.c.post('/checkout/'+t+'/pay',json={'phone':'0712345678'}).status_code,409)
            self.assertEqual(post.call_count,1)
    def test_live_guard(self):
        with self.assertRaises(RuntimeError): create_app({'DB':self.temp.name+'/guard.db','MODE':'live','PROVIDER_KEY':''})
if __name__=='__main__': unittest.main()
