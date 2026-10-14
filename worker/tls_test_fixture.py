"""Generate temporary loopback-only TLS material; never store keys in Git."""
import atexit
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess
import tempfile


@lru_cache(maxsize=1)
def tls_fixture_directory():
    openssl = shutil.which("openssl")
    if not openssl:
        git = shutil.which("git")
        if git:
            for parent in Path(git).resolve().parents:
                candidate = parent / "usr/bin/openssl.exe"
                if candidate.is_file():
                    openssl = str(candidate)
                    break
    if not openssl:
        raise RuntimeError("TLS regression tests require OpenSSL (included in Git for Windows)")
    temporary = tempfile.TemporaryDirectory(prefix="cloak-loopback-tls-")
    atexit.register(temporary.cleanup)
    directory = Path(temporary.name)
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout", str(directory / "LOCAL_TEST_ONLY_key.pem"),
                    "-out", str(directory / "LOCAL_TEST_ONLY_cert.pem")],
                   check=True, capture_output=True, timeout=30)
    return directory
