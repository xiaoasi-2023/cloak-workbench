"""Offline Google Directory substitute; never submits real credentials."""
import copy
from functools import lru_cache
import json
import threading
import time

from workspace_api import DirectoryError


@lru_cache(maxsize=1)
def fixture_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps({'type': 'service_account', 'client_id': '123456789012345678901',
                      'project_id': 'offline-fixture', 'client_email': 'fixture@offline-fixture.iam.gserviceaccount.com',
                      'private_key': private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                           serialization.NoEncryption()).decode(),
                      'token_uri': 'https://malicious.invalid/token'})


def raw_user(email, customer='customer-alpha', uid=None, **extra):
    return dict(id=uid or email.split('@')[0], primaryEmail=email, customerId=customer,
                name={'givenName': email.split('@')[0], 'familyName': 'Fixture', 'fullName': email.split('@')[0] + ' Fixture'},
                suspended=False, orgUnitPath='/', **extra)


class DirectoryFixture:
    def __init__(self, latency=0):
        self.data = {'customer-alpha': [raw_user('existing@alpha.invalid')],
                     'customer-beta': [raw_user('existing@beta.invalid', 'customer-beta')]}
        self.calls, self.routes = [], []
        self.latency = latency
        self.fail_email = None
        self.uncertain_email = None
        self.customer_override = None
        self.gate = None
        self.entered = threading.Event()

    def connect(self, record, proxy):
        fixture = self
        customer = 'customer-beta' if record['adminEmail'].endswith('@beta.invalid') else 'customer-alpha'
        class Client:
            def __enter__(self):
                fixture.routes.append(copy.deepcopy(proxy))
                fixture.entered.set()
                if fixture.gate:
                    fixture.gate.wait(4)
                if fixture.latency:
                    time.sleep(fixture.latency)
                return self

            def __exit__(self, *args):
                return False

            def identity(self, email):
                fixture.calls.append(('identity', email))
                return raw_user(email, fixture.customer_override or customer)

            def domains(self, cid):
                return [{'domainName': 'alpha.invalid' if cid == 'customer-alpha' else 'beta.invalid', 'verified': True},
                        {'domainName': 'unverified.invalid', 'verified': False}]

            def users(self, cid, cancel):
                fixture.calls.append(('users', cid))
                return copy.deepcopy(fixture.data[cid])

            def create(self, body):
                email = body['primaryEmail']
                fixture.calls.append(('create', email, copy.deepcopy(body)))
                if fixture.latency:
                    time.sleep(fixture.latency)
                if email == fixture.fail_email:
                    raise DirectoryError('Fixture Google refusal')
                if email == fixture.uncertain_email:
                    raise DirectoryError('Fixture unknown write result', True)
                if any(u['primaryEmail'] == email for u in fixture.data[customer]):
                    raise DirectoryError('Fixture duplicate')
                result = raw_user(email, customer)
                result.update({k: copy.deepcopy(v) for k, v in body.items() if k != 'password'})
                fixture.data[customer].append(result)
                return copy.deepcopy(result)

            def update(self, uid, body):
                fixture.calls.append(('update', uid, copy.deepcopy(body)))
                user = next(u for u in fixture.data[customer] if u['id'] == uid)
                user.update({k: copy.deepcopy(v) for k, v in body.items() if k != 'password'})
                return copy.deepcopy(user)

            def user(self, uid):
                fixture.calls.append(('user', uid))
                user = next((u for u in fixture.data[customer] if u['id'] == uid), None)
                if user is None:
                    raise DirectoryError('Fixture user missing')
                return copy.deepcopy(user)

            def delete(self, uid):
                fixture.calls.append(('delete', uid))
                user = next(u for u in fixture.data[customer] if u['id'] == uid)
                if fixture.latency:
                    time.sleep(fixture.latency)
                if user['primaryEmail'] == fixture.fail_email:
                    raise DirectoryError('Fixture deletion refused')
                if user['primaryEmail'] == fixture.uncertain_email:
                    raise DirectoryError('Fixture unknown deletion result', True)
                fixture.data[customer].remove(user)
        return Client()
