"""HashPay operations whose contracts are documented by the provider."""
import requests

class HashPay:
    BASE='https://api.hashback.co.ke'
    def __init__(self,app): self.config=app.config
    def call(self,path,payload):
        r=requests.post(self.BASE+path,json=payload,timeout=20,allow_redirects=False)
        r.raise_for_status()
        result=r.json()
        if not isinstance(result,dict): raise ValueError('Invalid provider response')
        return result
    def status(self,checkout_id):
        return self.call('/transactionstatus',{'api_key':self.config['PROVIDER_KEY'],'account_id':self.config['ACCOUNT'],'checkoutid':checkout_id})
    def register_webhook(self):
        result=self.call('/registerwebhook',{'API_KEY':self.config['PROVIDER_KEY'],'account_id':self.config['ACCOUNT'],'webhook_url':self.config['BASE']+'/webhooks/hashpay'})
        if str(result.get('ResultCode'))!='0': raise ValueError('Provider rejected webhook registration; check partner access')
        return result
