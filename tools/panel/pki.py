"""Private PKI: encrypted offline-capable CA, explicit dates, strict verification.
Only Python standard library and OpenSSL >= 1.1.1. No revocation enforcement.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import tempfile

UTC = dt.timezone.utc
VERSION = '1.0.0'


class SafetyError(RuntimeError):
    pass


def atomic_write(path: Path, data: str | bytes, mode=0o600):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise SafetyError('Refusing symlink destination')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.node-endpoint-', dir=path.parent)
    try:
        os.chmod(name, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data.encode('utf-8') if isinstance(data, str) else data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_json(path: Path, value, mode=0o600):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n', mode)


def validate_host(host: str) -> tuple[str, str]:
    host = host.strip()
    if not host or len(host) > 253 or any(c in host for c in '/\\?#@[]%\n\r\t '):
        raise SafetyError('Address must be a bare IPv4, IPv6, or DNS hostname')
    try:
        return str(ipaddress.ip_address(host)), 'IP'
    except ValueError:
        pass
    if re.fullmatch(r'[0-9.]+', host):
        raise SafetyError('Invalid IPv4 address')
    if not host.isascii():
        raise SafetyError('Use an ASCII/punycode DNS hostname')
    name = host.lower()
    name = name[:-1] if name.endswith('.') else name
    labels = name.split('.')
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in labels):
        raise SafetyError('DNS hostname requires valid labels')
    return name, 'DNS'


def ten_years(value: dt.datetime) -> dt.datetime:
    try:
        return value.replace(year=value.year + 10)
    except ValueError:
        return value.replace(year=value.year + 10, day=28)


def asn_time(value):
    return value.astimezone(UTC).strftime('%Y%m%d%H%M%SZ')


def cert_date(value):
    return dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(value), UTC)


def normalize_fingerprint(value):
    result = value.replace(':', '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', result):
        raise SafetyError('Expected full SHA-256 certificate fingerprint')
    return result


class OpenSSL:
    def __init__(self, binary='openssl'):
        self.binary = binary

    def run(self, args, password=None, input_bytes=None):
        if password is not None:
            if '\n' in password or '\r' in password or not password:
                raise SafetyError('Passphrase must be nonempty with no newline')
            input_bytes = (password + '\n').encode('utf-8')
        try:
            result = subprocess.run([self.binary, *map(str, args)], input=input_bytes,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SafetyError('OpenSSL execution failed') from exc
        if result.returncode:
            raise SafetyError('OpenSSL operation failed: ' + result.stderr.decode(errors='replace')[-1200:])
        return result.stdout

    def info(self, path, now=None):
        raw = self.run(['x509', '-in', path, '-noout', '-dates', '-subject', '-issuer',
                        '-serial', '-fingerprint', '-sha256']).decode()
        fields = dict(x.split('=', 1) for x in raw.splitlines() if '=' in x)
        before, after = cert_date(fields['notBefore']), cert_date(fields['notAfter'])
        now = now or dt.datetime.now(UTC)
        days = (after - now).days
        san = self.run(['x509', '-in', path, '-noout', '-ext', 'subjectAltName']).decode().strip()
        return {'sha256': normalize_fingerprint(next(v for k, v in fields.items() if 'Fingerprint' in k)),
                'subject': fields['subject'].strip(), 'issuer': fields['issuer'].strip(),
                'serial': fields['serial'], 'not_before': before.isoformat(), 'not_after': after.isoformat(),
                'remaining_days': days, 'san': san.splitlines()[-1].strip() if 'Subject Alternative Name' in san else '',
                'status': 'not_yet_valid' if now < before else 'expired' if now >= after else 'valid',
                'reminder_days': next((x for x in (30, 90, 180) if days <= x), None)}

    def verify(self, cert, ca, host, attime=None):
        host, kind = validate_host(host)
        args = ['verify', '-CAfile', ca, '-purpose', 'sslserver', '-x509_strict',
                '-verify_ip' if kind == 'IP' else '-verify_hostname', host]
        if attime is not None:
            args += ['-attime', str(int(attime))]
        self.run([*args, cert])
        leaf = self.info(cert)
        # A dual-trust bundle can start with an older CA. Compare the actual
        # signing trust anchor, rather than the first certificate in the bundle.
        roots = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
                           Path(ca).read_text(), re.S)
        with tempfile.TemporaryDirectory(prefix='node-endpoint-verify-') as folder:
            candidate = Path(folder) / 'ca.pem'
            for root in roots:
                atomic_write(candidate, root + '\n')
                info = self.info(candidate)
                if info['subject'] != leaf['issuer']:
                    continue
                try:
                    self.run([x if x != ca else candidate for x in [*args, cert]])
                except SafetyError:
                    continue
                if leaf['not_after'] > info['not_after']:
                    raise SafetyError('Server certificate exceeds signing CA expiry')
                return
        raise SafetyError('No verified signing CA found in trust bundle')

    def key_matches(self, key, cert, password):
        key_public = self.run(['pkey', '-in', key, '-passin', 'stdin', '-pubout', '-outform', 'DER'], password)
        cert_public = self.run(['x509', '-in', cert, '-pubkey', '-noout'])
        cert_der = self.run(['pkey', '-pubin', '-outform', 'DER'], input_bytes=cert_public)
        if key_public != cert_der:
            raise SafetyError('Private key does not match CA certificate')


class PKI:
    def __init__(self, state, openssl='openssl'):
        self.state = Path(state)
        self.root = self.state / 'pki'
        self.meta_path = self.root / 'state.json'
        self.openssl = OpenSSL(openssl)

    @property
    def meta(self):
        return read_json(self.meta_path, {})

    def ca_dir(self, identity=None):
        identity = identity or self.meta.get('active_ca')
        if not identity or not re.fullmatch(r'[a-f0-9]{16}', identity):
            raise SafetyError('No valid active CA')
        return self.root / 'ca' / identity

    def leaf_dir(self, identity=None):
        identity = identity or self.meta.get('active_leaf')
        if not identity or not re.fullmatch(r'[a-f0-9]{16}', identity):
            raise SafetyError('No valid active server certificate')
        return self.root / 'server' / identity

    def _config(self, folder, key, cert, ca=False, host=None):
        for path in (folder, key, cert):
            if any(c in str(path) for c in ('"', '\n', '\r', '$')):
                raise SafetyError('Unsafe OpenSSL path')
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
        atomic_write(folder / 'index.txt', '')
        atomic_write(folder / 'serial', secrets.token_hex(16).upper() + '\n')
        (folder / 'newcerts').mkdir(mode=0o700, exist_ok=True)
        ext = ('basicConstraints = critical,CA:TRUE,pathlen:0\nkeyUsage = critical,keyCertSign,cRLSign\n'
               'subjectKeyIdentifier = hash\nauthorityKeyIdentifier = keyid:always\n') if ca else (
               'basicConstraints = critical,CA:FALSE\nkeyUsage = critical,digitalSignature\n'
               'extendedKeyUsage = serverAuth\nsubjectKeyIdentifier = hash\nauthorityKeyIdentifier = keyid:always\n'
               'subjectAltName = ' + validate_host(host)[1] + ':' + validate_host(host)[0] + '\n')
        text = (f'[ca]\ndefault_ca = local_ca\n[local_ca]\n'
                f'database = "{(folder / "index.txt").as_posix()}"\n'
                f'serial = "{(folder / "serial").as_posix()}"\n'
                f'new_certs_dir = "{(folder / "newcerts").as_posix()}"\n'
                f'private_key = "{key.as_posix()}"\ncertificate = "{cert.as_posix()}"\n'
                'default_md = sha256\ndefault_days = 3652\npolicy = policy\nunique_subject = no\n'
                'copy_extensions = none\nx509_extensions = extensions\n[policy]\ncommonName = supplied\n[extensions]\n' + ext)
        atomic_write(folder / 'openssl.cnf', text)
        return folder / 'openssl.cnf'

    def _new_ca(self, password, now=None):
        if len(password) < 16:
            raise SafetyError('CA passphrase requires at least 16 characters')
        now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
        identity = secrets.token_hex(8)
        folder = self.root / 'ca' / identity
        folder.mkdir(parents=True, mode=0o700)
        key, cert, request = folder / 'ca.key.pem', folder / 'ca.pem', folder / 'ca.csr'
        self.openssl.run(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256',
                          '-aes-256-cbc', '-pass', 'stdin', '-out', key], password)
        os.chmod(key, 0o600)
        atomic_write(folder / 'request.cnf', '[req]\ndistinguished_name=dn\n[dn]\n')
        self.openssl.run(['req', '-new', '-config', folder / 'request.cnf', '-key', key,
                          '-passin', 'stdin', '-subj', '/CN=NodeEndpoint Root ' + identity, '-out', request], password)
        cfg = self._config(folder / 'issuer', key, cert, ca=True)
        self.openssl.run(['ca', '-batch', '-selfsign', '-config', cfg, '-keyfile', key, '-passin', 'stdin',
                          '-in', request, '-out', cert, '-notext',
                          '-startdate', asn_time(now - dt.timedelta(minutes=5)), '-enddate', asn_time(ten_years(now))], password)
        os.chmod(cert, 0o644)
        return identity

    def _new_leaf(self, ca_id, host, password, ca_key=None, now=None):
        host, _ = validate_host(host)
        now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
        ca_folder = self.ca_dir(ca_id)
        key_ca = Path(ca_key) if ca_key else ca_folder / 'ca.key.pem'
        if not key_ca.is_file():
            raise SafetyError('CA key is offline; provide --ca-key pointing to encrypted offline export')
        self.openssl.key_matches(key_ca, ca_folder / 'ca.pem', password)
        ca_info = self.openssl.info(ca_folder / 'ca.pem', now)
        if ca_info['status'] != 'valid':
            raise SafetyError('Signing CA is not currently valid')
        end = min(ten_years(now), dt.datetime.fromisoformat(ca_info['not_after']))
        identity = secrets.token_hex(8)
        folder = self.root / 'server' / identity
        folder.mkdir(parents=True, mode=0o700)
        key, cert, request = folder / 'server.key.pem', folder / 'server.pem', folder / 'server.csr'
        self.openssl.run(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', key])
        os.chmod(key, 0o600)
        atomic_write(folder / 'request.cnf', '[req]\ndistinguished_name=dn\n[dn]\n')
        self.openssl.run(['req', '-new', '-config', folder / 'request.cnf', '-key', key,
                          '-subj', '/CN=' + host[:64], '-out', request])
        cfg = self._config(folder / 'issuer', key_ca, ca_folder / 'ca.pem', host=host)
        start = max(now - dt.timedelta(minutes=5), dt.datetime.fromisoformat(ca_info['not_before']))
        self.openssl.run(['ca', '-batch', '-config', cfg, '-passin', 'stdin', '-in', request, '-out', cert,
                          '-notext', '-startdate', asn_time(start), '-enddate', asn_time(end)], password)
        os.chmod(cert, 0o644)
        self.openssl.verify(cert, ca_folder / 'ca.pem', host)
        return identity

    def create(self, host, password):
        if self.meta:
            raise SafetyError('PKI already initialized; use renew or rotation')
        now = dt.datetime.now(UTC).replace(microsecond=0)
        ca = self._new_ca(password, now)
        leaf = self._new_leaf(ca, host, password, now=now)
        write_json(self.meta_path, {'version': VERSION, 'active_ca': ca, 'active_leaf': leaf,
                                   'host': validate_host(host)[0], 'trusted_ca': [ca], 'rotation': None})
        self.bundle()
        return self.status()

    def renew(self, host, password, ca_key=None):
        meta = self.meta
        if meta.get('rotation'):
            raise SafetyError('Finish or roll back pending CA rotation before renewing')
        leaf = self._new_leaf(meta['active_ca'], host, password, ca_key)
        meta['previous_leaf'] = meta['active_leaf']
        meta['active_leaf'], meta['host'] = leaf, validate_host(host)[0]
        write_json(self.meta_path, meta)
        return self.status()

    def bundle(self):
        atomic_write(self.root / 'trust-bundle.pem', ''.join((self.ca_dir(x) / 'ca.pem').read_text()
                     for x in self.meta['trusted_ca']), 0o644)

    def prepare_rotation(self, password):
        meta = self.meta
        if meta.get('rotation'):
            raise SafetyError('CA rotation already pending')
        now = dt.datetime.now(UTC).replace(microsecond=0)
        ca = self._new_ca(password, now)
        leaf = self._new_leaf(ca, meta['host'], password, now=now)
        meta['trusted_ca'] = [meta['active_ca'], ca]
        meta['rotation'] = {'phase': 'distribute_dual_trust', 'old_ca': meta['active_ca'],
                            'new_ca': ca, 'old_leaf': meta['active_leaf'], 'new_leaf': leaf}
        write_json(self.meta_path, meta)
        self.bundle()
        return self.status()

    def activate_rotation(self, fingerprint):
        meta = self.meta
        rotation = meta.get('rotation') or {}
        if rotation.get('phase') != 'distribute_dual_trust':
            raise SafetyError('No prepared CA rotation')
        actual = self.openssl.info(self.ca_dir(rotation['new_ca']) / 'ca.pem')['sha256']
        if normalize_fingerprint(fingerprint) != actual:
            raise SafetyError('New CA fingerprint confirmation differs')
        meta['active_ca'], meta['active_leaf'] = rotation['new_ca'], rotation['new_leaf']
        rotation['phase'] = 'new_ca_active_dual_trust'
        write_json(self.meta_path, meta)

    def finish_rotation(self, fingerprint):
        meta = self.meta
        if (meta.get('rotation') or {}).get('phase') != 'new_ca_active_dual_trust':
            raise SafetyError('Activate new CA before retiring old trust')
        if normalize_fingerprint(fingerprint) != self.openssl.info(self.ca_dir() / 'ca.pem')['sha256']:
            raise SafetyError('New CA fingerprint confirmation differs')
        meta['trusted_ca'], meta['rotation'] = [meta['active_ca']], None
        write_json(self.meta_path, meta)
        self.bundle()

    def export_ca(self, destination, password, ca_id=None):
        folder = self.ca_dir(ca_id)
        destination = Path(destination).resolve()
        if destination == self.state.resolve() or self.state.resolve() in destination.parents:
            raise SafetyError('Offline export must be outside online state directory')
        if destination.exists():
            raise SafetyError('Refusing existing export destination')
        self.openssl.key_matches(folder / 'ca.key.pem', folder / 'ca.pem', password)
        destination.mkdir(mode=0o700, parents=True)
        os.chmod(destination, 0o700)
        for name in ('ca.pem', 'ca.key.pem'):
            atomic_write(destination / name, (folder / name).read_bytes())
        manifest = {'version': VERSION, 'ca_sha256': self.openssl.info(folder / 'ca.pem')['sha256'],
                    'files': {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                              for name in ('ca.pem', 'ca.key.pem')}}
        write_json(destination / 'manifest.json', manifest)
        self.verify_export(destination, password, manifest['ca_sha256'], folder.name)
        return manifest

    def verify_export(self, destination, password, fingerprint, ca_id=None):
        destination = Path(destination)
        manifest = read_json(destination / 'manifest.json', {})
        expected = normalize_fingerprint(fingerprint)
        actual = self.openssl.info(destination / 'ca.pem')['sha256']
        online = self.openssl.info(self.ca_dir(ca_id) / 'ca.pem')['sha256']
        if actual != expected or online != expected or manifest.get('ca_sha256') != expected:
            raise SafetyError('CA export fingerprint differs from independently confirmed CA')
        for name in ('ca.pem', 'ca.key.pem'):
            if hashlib.sha256((destination / name).read_bytes()).hexdigest() != manifest.get('files', {}).get(name):
                raise SafetyError('Export integrity check failed')
        self.openssl.key_matches(destination / 'ca.key.pem', destination / 'ca.pem', password)

    def remove_online_key(self, destination, password, fingerprint, ca_id=None):
        self.verify_export(destination, password, fingerprint, ca_id)
        key = self.ca_dir(ca_id) / 'ca.key.pem'
        if not key.is_file() or key.is_symlink():
            raise SafetyError('Online CA key missing or symlink')
        key.unlink()  # SSD/COW secure erasure cannot be guaranteed.

    def status(self):
        meta = self.meta
        if not meta:
            return {'initialized': False}
        return {'initialized': True, 'host': meta['host'], 'ca': self.openssl.info(self.ca_dir() / 'ca.pem'),
                'server': self.openssl.info(self.leaf_dir() / 'server.pem'),
                'ca_key_online': (self.ca_dir() / 'ca.key.pem').exists(),
                'trust': [self.openssl.info(self.ca_dir(x) / 'ca.pem') for x in meta['trusted_ca']],
                'rotation': meta.get('rotation'), 'revocation_enforced': False}
