"""Real DKIM cryptography with an ephemeral synthetic key and isolated DNS lookup."""
import base64
from datetime import datetime,timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
import subprocess
import unittest
from rtm_core.case_mail_authentication import authenticated_sender


class DKIMTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        exe=shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
        if not Path(exe).is_file():raise RuntimeError('OpenSSL test dependency unavailable; not an omitted verification')
        cls.key=subprocess.run([exe,'genrsa','-traditional','2048'],capture_output=True,check=True).stdout
        public=subprocess.run([exe,'rsa','-pubout','-outform','DER'],input=cls.key,capture_output=True,check=True).stdout
        cls.dns=b'v=DKIM1; k=rsa; p='+base64.b64encode(public)

    def raw(self):
        m=EmailMessage(policy=policy.SMTP);m['From']='client@example.com';m['To']='expedientes@operativo.example.com'
        m['Subject']='Respuesta sintética';m['Date']=format_datetime(datetime.now(timezone.utc));m['Message-ID']='<reply@example.com>'
        m['In-Reply-To']='<quote@operativo.example.com>';m['References']='<quote@operativo.example.com>'
        m.set_content('Respuesta inocua del ensayo.');m.add_attachment(b'ADJUNTO SINTETICO',maintype='text',subtype='plain',filename='nota.txt')
        return m.as_bytes()

    def signed(self,raw,headers=None,**kw):
        import dkim
        headers=headers or [b'from',b'to',b'subject',b'date',b'message-id',b'in-reply-to',b'references']
        return dkim.sign(raw,b'fixture',b'example.com',self.key,include_headers=headers,**kw)+raw

    def verify(self,raw):
        return authenticated_sender(raw,dnsfunc=lambda name,**kw:self.dns if name.rstrip(b'.')==b'fixture._domainkey.example.com' else None)

    def test_verified_thread_and_full_attachment_body(self):self.assertTrue(self.verify(self.signed(self.raw())))
    def test_tampered_body_or_sender_rejected(self):
        signed=self.signed(self.raw())
        original=base64.b64encode(b'ADJUNTO SINTETICO')
        self.assertIn(original,signed)
        self.assertFalse(self.verify(signed.replace(original,base64.b64encode(b'ALTERADO SINTETICO'))))
        self.assertFalse(self.verify(signed.replace(b'client@example.com',b'other@example.com')))
    def test_unsigned_thread_and_spoofed_authentication_results_rejected(self):
        raw=self.raw()
        self.assertFalse(self.verify(self.signed(raw,headers=[b'from',b'to',b'subject',b'date',b'message-id'])))
        self.assertFalse(self.verify(b'Authentication-Results: trusted.example; dkim=pass\r\n'+raw))
    def test_signature_body_length_and_duplicate_thread_rejected(self):
        self.assertFalse(self.verify(self.signed(self.raw(),length=True)))
        self.assertFalse(self.verify(b'References: <other@operativo.example.com>\r\n'+self.signed(self.raw())))


if __name__=='__main__':unittest.main()
