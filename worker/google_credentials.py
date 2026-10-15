"""Optional plaintext Google credentials and the session backup DPAPI helper."""
import ctypes
from ctypes import wintypes
import json
import os
import threading


def windows_protect(data, decrypt=False):
    if os.name != 'nt':
        raise OSError('Windows credential protection is unavailable')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), 'Cloak Workbench Google login', None, None, None, 1, ctypes.byref(result))
    fn.restype = wintypes.BOOL
    try:
        if not fn(*args):
            raise OSError('Unable to access credentials for this Windows user')
        return ctypes.string_at(result.data, result.size)
    finally:
        if result.data:
            kernel.LocalFree(result.data)
        ctypes.memset(buffer, 0, len(data))


class GoogleCredentialStore:
    def __init__(self, data_dir):
        self.path = data_dir / 'google-credentials.json'
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        records = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(records, dict):
            raise OSError('Invalid local credential store')
        return records

    def _write(self, records):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(records), encoding='utf-8')
        temp.replace(self.path)

    def load(self, profile_id):
        with self.lock:
            record = self._read().get(profile_id)
            if record is None:
                return None
            value = record
            # Bind saved credentials to their environment.
            if not isinstance(value, dict) or value.get('profileId') != profile_id:
                raise OSError('Credential environment does not match')
            if not isinstance(value.get('email'), str) or not isinstance(value.get('password'), str):
                raise OSError('Invalid saved credentials')
            return value

    def save(self, profile_id, email, password):
        with self.lock:
            records = self._read()
            records[profile_id] = {'profileId': profile_id, 'email': email, 'password': password}
            self._write(records)

    def forget(self, profile_id):
        with self.lock:
            records = self._read()
            if profile_id in records:
                del records[profile_id]
                self._write(records)
