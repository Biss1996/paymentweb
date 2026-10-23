import json, threading, tempfile, unittest
from unittest.mock import patch, Mock
from app import create_app, connect, digest, settle, now, signature
from manage import deliver_once
from provider import HashPay

class ServerlessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app({'TESTING':True,'MODE':'mock','DB':self.tmp.name+'/db','CRON_SECRET':'s'*40,'DATABASE_URL':''})
        self.c=self.app.test_client()
        with connect(self.app) as db:
            db.execute('INSERT INTO merchants VALUES(?,?,?,?,?,?,1)',('m','Store',digest('key'),'secret','http://localhost:4999/webhook','http://localhost:4999'))
        p=self.c.post('/api/v1/checkout/initialize',json={'order_id':'A','amount':100},headers={'Authorization':'Bearer key'}).json
        self.pid=p['id']; self.token=p['checkout_url'].split('/')[-1]
        self.c.post('/checkout/'+self.token+'/pay',json={'phone':'0712345678'})
        with connect(self.app) as db:
            p=db.execute('SELECT * FROM payments WHERE id=?',(self.pid,)).fetchone()
            settle(db,p,'RECEIPT','mock_'+self.pid)
    def tearDown(self): self.tmp.cleanup()
    def test_protected_cron_delivers(self):
        self.assertEqual(self.c.get('/api/cron/deliver').status_code,401)
        self.assertEqual(self.c.get('/api/cron/deliver',headers={'Authorization':'Bearer key'}).status_code,401)
        with patch('manage.requests.post',return_value=Mock(status_code=200)):
            r=self.c.get('/api/cron/deliver',headers={'Authorization':'Bearer '+'s'*40})
        self.assertEqual(r.json['processed'],1)
    def test_lease_prevents_overlapping_send(self):
        entered=threading.Event(); release=threading.Event(); results=[]
        def slow(*args,**kwargs): entered.set(); release.wait(3); return Mock(status_code=200)
        with patch('manage.requests.post',side_effect=slow) as sender:
            thread=threading.Thread(target=lambda:results.append(deliver_once(self.app)))
            thread.start(); self.assertTrue(entered.wait(3))
            self.assertEqual(deliver_once(self.app),0)
            release.set(); thread.join(3)
            self.assertEqual(sender.call_count,1)
    def test_abandoned_lease_recovers_and_redirect_is_failure(self):
        with connect(self.app) as db: db.execute('UPDATE events SET lease_until=?,lease_token=?',(now()-1,'dead'))
        with patch('manage.requests.post',return_value=Mock(status_code=302)): self.assertEqual(deliver_once(self.app),1)
        with connect(self.app) as db:
            e=db.execute('SELECT * FROM events').fetchone()
            self.assertEqual(e['delivered'],0); self.assertEqual(e['last_error'],'HTTP 302'); self.assertIsNone(e['lease_token'])
    def test_retry_limit(self):
        with connect(self.app) as db: db.execute('UPDATE events SET attempts=12')
        with patch('manage.requests.post') as send: self.assertEqual(deliver_once(self.app),0); send.assert_not_called()
    def test_live_acknowledgement_is_not_settlement(self):
        # New checkout; isolate initiation from mock settlement above.
        p=self.c.post('/api/v1/checkout/initialize',json={'order_id':'B','amount':100},headers={'Authorization':'Bearer key'}).json
        self.app.config['MODE']='live'
        with patch('app.requests.post',return_value=Mock(json=lambda:{'success':True,'checkout_id':'PROVIDER-1'})):
            r=self.c.post('/checkout/'+p['checkout_url'].split('/')[-1]+'/pay',json={'phone':'0712345678'})
        self.assertEqual(r.status_code,200)
        with connect(self.app) as db:
            tx=db.execute('SELECT * FROM payments WHERE id=?',(p['id'],)).fetchone()
            self.assertEqual(tx['status'],'PROCESSING'); self.assertEqual(tx['checkout_id'],'PROVIDER-1')
    def test_provider_commands_use_documented_fields(self):
        self.app.config.update(PROVIDER_KEY='private',ACCOUNT='HP1',BASE='https://gateway.example')
        with patch('provider.requests.post',return_value=Mock(json=lambda:{'ResultCode':'0'})) as post:
            HashPay(self.app).register_webhook()
            self.assertEqual(post.call_args.kwargs['json'],{'API_KEY':'private','account_id':'HP1','webhook_url':'https://gateway.example/webhooks/hashpay'})
            HashPay(self.app).status('CK1')
            self.assertEqual(post.call_args.kwargs['json']['checkoutid'],'CK1')
    def test_vercel_rejects_ephemeral_database(self):
        with patch.dict('os.environ',{'VERCEL':'1'}):
            with self.assertRaises(RuntimeError): create_app({'MODE':'mock','DATABASE_URL':''})
    def test_vercel_database_does_not_migrate_on_cold_start(self):
        with patch.dict('os.environ',{'VERCEL':'1'}),patch('app.migrate') as migrate:
            app=create_app({'MODE':'mock','DATABASE_URL':'postgresql://invalid','CRON_SECRET':'s'*40})
            migrate.assert_not_called()
            self.assertIsNotNone(app)
