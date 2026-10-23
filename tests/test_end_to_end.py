"""Real HTTP test of the provided Node main site and Flask checkout."""
import unittest, tempfile, subprocess, os, socket, threading, time, shutil
from pathlib import Path
import requests
from werkzeug.serving import make_server
from app import create_app, connect, digest, settle
from manage import deliver_once

class EndToEnd(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for main-site integration test')
    def test_main_site_redirect_payment_webhook_and_paid_status(self):
        with socket.socket() as s:
            try: s.bind(('127.0.0.1',4000))
            except OSError: self.skipTest('Demo port 4000 already in use')
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app({'DB':tmp+'/gateway.db','MODE':'mock','DATABASE_URL':'','BASE':'http://127.0.0.1:0'})
            with connect(app) as db:
                db.execute('INSERT INTO merchants VALUES(?,?,?,?,?,?,1)',('demo','Main Store',digest('e2e-key'),'e2e-secret','http://127.0.0.1:4000/webhook','http://127.0.0.1:4000/'))
            server=make_server('127.0.0.1',0,app,threaded=True)
            base='http://127.0.0.1:'+str(server.server_port); app.config['BASE']=base
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            env={**os.environ,'GATEWAY_URL':base,'GATEWAY_API_KEY':'e2e-key','GATEWAY_WEBHOOK_SECRET':'e2e-secret','DEMO_DB':tmp+'/main.db'}
            node=subprocess.Popen(['node',str(Path(__file__).resolve().parents[1]/'examples/main-site.mjs')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for i in range(50):
                    try:
                        if requests.get('http://127.0.0.1:4000/status',timeout=1).ok: break
                    except requests.ConnectionError: time.sleep(.1)
                else: self.fail('Node example did not start')
                r=requests.post('http://127.0.0.1:4000/pay',allow_redirects=False,timeout=5)
                self.assertEqual(r.status_code,303); checkout=r.headers['Location']
                self.assertIn('TEST MODE',requests.get(checkout,timeout=5).text)
                self.assertEqual(requests.post(checkout+'/pay',json={'phone':'0712345678'},timeout=5).status_code,200)
                with connect(app) as db:
                    db.execute('BEGIN IMMEDIATE'); p=db.execute('SELECT * FROM payments').fetchone()
                    settle(db,p,'MOCK-E2E','mock_'+p['id'])
                deliver_once(app)
                status=requests.get('http://127.0.0.1:4000/status',timeout=5).json()
                self.assertEqual(status['paid'],1)
                # Replay real HTTP event; receiver must accept without a second credit.
                with connect(app) as db: db.execute('UPDATE events SET delivered=0,next_at=0')
                deliver_once(app)
                with connect(app) as db:
                    self.assertEqual(db.execute('SELECT delivered FROM events').fetchone()[0],1)
                self.assertEqual(requests.post('http://127.0.0.1:4000/webhook',json={},timeout=5).status_code,401)
            finally:
                node.terminate(); node.wait(timeout=5); server.shutdown(); server.server_close(); thread.join(timeout=5)
