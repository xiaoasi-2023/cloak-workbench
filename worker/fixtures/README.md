TLS regression certificates and private keys are generated in a temporary
directory by `worker/tls_test_fixture.py`, using OpenSSL (included in Git for
Windows). No certificate or private key is distributed in this repository.
Any older local `.pem` fixture files are ignored and no longer used by tests.

Each temporary certificate is explicitly trusted only in the test's SSLContext.
The production relay always uses the operating system's normal trusted CA store
and validates HTTPS upstream hostnames and certificates.
