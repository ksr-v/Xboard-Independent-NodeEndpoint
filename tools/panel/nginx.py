"""Conservative Nginx inspection and generation. Never edit original sites."""
from __future__ import annotations
from dataclasses import dataclass
import fnmatch
import hashlib
from pathlib import Path, PurePosixPath
import re
import shlex
import posixpath
from urllib.parse import urlsplit
from pki import SafetyError, validate_host

# Audited cedar2025/Xboard V1/V2 ServerRoute.php; route:list must also confirm.
API_METHODS = {
    **{'/api/v1/server/UniProxy/' + x: ['GET'] for x in ('config', 'user', 'alivelist')},
    **{'/api/v1/server/UniProxy/' + x: ['POST'] for x in ('push', 'alive', 'status')},
    '/api/v2/server/handshake': ['GET', 'POST'],
    **{'/api/v2/server/' + x: ['GET'] for x in ('config', 'user', 'alivelist')},
    **{'/api/v2/server/' + x: ['POST'] for x in ('report', 'push', 'alive', 'status')},
    '/api/v2/server/machine/nodes': ['POST'],
    '/api/v2/server/machine/status': ['POST'],
}

# Exact source snapshot audited in .audit/Xboard. Unknown middleware is never
# patched or implicitly trusted; add new profiles only after a source audit.
AUDITED_PROXY_TRUST_SHA256 = {
    'f259274043cc68d609bb826d5efac4c2f547c734585dbd43462635304a18f5f3',
}


def verify_proxy_trust(application):
    path = Path(application) / 'app/Http/Middleware/TrustProxies.php'
    if not path.is_file():
        raise SafetyError('Cannot identify application TrustProxies middleware')
    digest = sha256(path.read_bytes())
    if digest not in AUDITED_PROXY_TRUST_SHA256:
        raise SafetyError('Unknown TrustProxies SHA-256 profile; HTTP proxy installation refused. Audit this build before adding a profile; original middleware is never modified.')
    return digest


@dataclass
class Directive:
    words: list
    children: list | None = None


def parse(text):
    """Handle quoted strings/comments; refuse unsupported escapes or malformed AST."""
    pattern = r'''\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{};]|[^\s{};"'\#]+'''
    tokens = []
    position = 0
    for match in re.finditer(pattern, text):
        if match.start() != position:
            raise SafetyError('Unsupported Nginx syntax')
        position = match.end()
        token = match.group()
        if token.isspace() or token.startswith('#'):
            continue
        literal = token.startswith(('"', "'"))
        if literal:
            token = shlex.split(token)[0]
        # Quoted JSON log fragments such as '{', '}' and ';' are arguments,
        # even when their decoded value equals a structural punctuation token.
        tokens.append((token, literal))
    if position != len(text):
        raise SafetyError('Unsupported Nginx syntax')
    cursor = 0

    def block(nested=False):
        nonlocal cursor
        result, words = [], []
        while cursor < len(tokens):
            token, literal = tokens[cursor]
            cursor += 1
            if token == '}' and not literal:
                if words or not nested:
                    raise SafetyError('Malformed Nginx block')
                return result
            if token == ';' and not literal:
                if not words:
                    raise SafetyError('Empty Nginx directive')
                result.append(Directive(words))
                words = []
            elif token == '{' and not literal:
                if not words:
                    raise SafetyError('Unnamed Nginx block')
                result.append(Directive(words, block(True)))
                words = []
            else:
                words.append(token)
        if words or nested:
            raise SafetyError('Unterminated Nginx directive')
        return result
    return block()


def dump_files(text):
    matches = list(re.finditer(r'^# configuration file (.+):\s*$', text, re.M))
    if not matches:
        raise SafetyError('nginx -T did not report loaded configuration files')
    return {m.group(1): text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            for i, m in enumerate(matches)}


def resolve_include(pattern, prefix):
    if '$' in pattern or '\n' in pattern:
        raise SafetyError('Variable Nginx include cannot be resolved safely')
    return pattern if posixpath.isabs(pattern) else posixpath.normpath(posixpath.join(str(prefix), pattern))


def expanded(nodes, files, prefix, stack=()):
    result = []
    for node in nodes:
        if node.words[0] == 'include':
            if len(node.words) != 2:
                raise SafetyError('Malformed Nginx include')
            pattern = resolve_include(node.words[1], prefix)
            for filename, text in files.items():
                if fnmatch.fnmatchcase(filename, pattern):
                    if filename in stack:
                        raise SafetyError('Recursive Nginx include')
                    result += expanded(parse(text), files, prefix, (*stack, filename))
        else:
            result.append(Directive(node.words, expanded(node.children, files, prefix, stack)
                                    if node.children is not None else None))
    return result


def http_include_patterns(nodes, prefix, files, context=(), seen=()):
    result = []
    for node in nodes:
        if node.words[0] == 'include':
            pattern = resolve_include(node.words[1], prefix)
            if context == ('http',):
                result.append(pattern)
            for name, text in files.items():
                if fnmatch.fnmatchcase(name, pattern) and name not in seen:
                    result += http_include_patterns(parse(text), prefix, files, context, (*seen, name))
        elif node.children is not None:
            result += http_include_patterns(node.children, prefix, files, (*context, node.words[0]), seen)
    return result


def walk(nodes):
    for node in nodes:
        yield node
        if node.children is not None:
            yield from walk(node.children)


def values(nodes, name):
    return [x.words[1:] for x in nodes if x.words[0] == name]


def port_from_listener(value):
    # nginx listen supports addresses, sockets, IPv6, or bare ports.
    if value.startswith('unix:'):
        return None
    match = re.search(r'(?:^|:)([0-9]+)$', value)
    return int(match.group(1)) if match else 80


def check_port(port, loaded_nodes, ss_output, own_config=None):
    if not 1 <= int(port) <= 65535:
        raise SafetyError('Port must be between 1 and 65535')
    for node in walk(loaded_nodes):
        if node.words[0] == 'listen' and port_from_listener(node.words[1]) == int(port):
            raise SafetyError('Requested port already appears in another loaded Nginx listener')
    for line in ss_output.splitlines():
        fields = line.split()
        # ss -H -ltnp => state recv-q send-q local peer process
        if len(fields) >= 5 and port_from_listener(fields[3]) == int(port):
            if own_config and 'nginx' in line:
                continue
            raise SafetyError('Requested TCP port already in use')


def verify_routes(routes):
    observed = {}
    for item in routes:
        path = '/' + item.get('uri', '').lstrip('/')
        if path in API_METHODS:
            observed.setdefault(path, []).append(set(item.get('method', '').split('|')) - {'HEAD'})
    if any(path not in observed or len(observed[path]) != 1 or
           not set(methods).issubset(observed[path][0]) for path, methods in API_METHODS.items()):
        raise SafetyError('Actual Laravel route table differs from audited Node API profile')


def literal_loopback_backend(value, upstreams=None):
    if upstreams and value in upstreams:
        targets = [x.words[1] for x in upstreams[value] if x.words[0] == 'server']
        if len(targets) != 1:
            raise SafetyError('Load-balanced upstream is outside V1 scope')
        value = 'http://' + targets[0]
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
            parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise SafetyError('Backend must be observed literal http://127.0.0.1:port with no URI rewrite; IPv6 proxy trust is not audited')
    try:
        port = parsed.port
    except ValueError as exc:
        raise SafetyError('Invalid backend port') from exc
    if not port:
        raise SafetyError('Backend must explicitly include observed port')
    return value.rstrip('/'), port


def quote_path(path):
    text = str(path)
    if any(x in text for x in ('"', '$', '\\', '\r', '\n', ';', '{', '}')):
        raise SafetyError('Unsafe Nginx path')
    return '"' + text + '"'


def render(plan, cert=None, key=None):
    host, _ = validate_host(plan['host'])
    port = int(plan['port'])
    if port in (80, 443):
        raise SafetyError('V1 independent entry refuses original website ports 80 and 443')
    ssl_flag = ' ssl' if plan['https'] else ''
    lines = [f'# NodeEndpoint {plan["version"]} managed; ownership {plan["ownership"]}', 'server {',
             f'    listen 0.0.0.0:{port}{ssl_flag} default_server;']
    if plan.get('ipv6'):
        lines += [f'    listen [::]:{port}{ssl_flag} default_server;']
    lines += [f'    server_name {host};', '    access_log off;',
              '    error_log /dev/null crit;', '    client_max_body_size 32m;']
    if plan['https']:
        if not cert or not key:
            raise SafetyError('HTTPS requires verified private PKI')
        lines += [f'    ssl_certificate {quote_path(cert)};', f'    ssl_certificate_key {quote_path(key)};',
                  '    ssl_protocols TLSv1.2 TLSv1.3;', '    ssl_session_tickets off;']
    for path, methods in API_METHODS.items():
        lines += [f'    location = {path} {{',
                  f'        if ($request_method !~ ^({"|".join(methods)})$) {{ return 405; }}']
        if plan['deployment'] == 'fpm':
            app = PurePosixPath(plan['app'])
            lines += [f'        fastcgi_pass {plan["backend"]};',
                      f'        fastcgi_param SCRIPT_FILENAME {quote_path(app / "public/index.php")};',
                      f'        fastcgi_param DOCUMENT_ROOT {quote_path(app / "public")};',
                      '        fastcgi_param SCRIPT_NAME /index.php;', '        fastcgi_param QUERY_STRING $query_string;',
                      '        fastcgi_param REQUEST_METHOD $request_method;', '        fastcgi_param CONTENT_TYPE $content_type;',
                      '        fastcgi_param CONTENT_LENGTH $content_length;', '        fastcgi_param REQUEST_URI $request_uri;',
                      '        fastcgi_param SERVER_PROTOCOL $server_protocol;', '        fastcgi_param REQUEST_SCHEME $scheme;',
                      '        fastcgi_param HTTPS $https if_not_empty;', '        fastcgi_param GATEWAY_INTERFACE CGI/1.1;',
                      '        fastcgi_param SERVER_SOFTWARE nginx;', '        fastcgi_param REMOTE_ADDR $remote_addr;',
                      '        fastcgi_param REMOTE_PORT $remote_port;', '        fastcgi_param SERVER_ADDR $server_addr;',
                      '        fastcgi_param SERVER_PORT $server_port;', '        fastcgi_param SERVER_NAME $host;',
                      '        fastcgi_param REDIRECT_STATUS 200;', '        fastcgi_param HTTP_HOST $http_host;']
            lines += ['        fastcgi_param HTTP_X_FORWARDED_HOST $http_host;',
                      '        fastcgi_param HTTP_X_FORWARDED_PROTO $scheme;',
                      '        fastcgi_param HTTP_X_FORWARDED_PORT $server_port;',
                      '        fastcgi_param HTTP_X_FORWARDED_FOR $remote_addr;',
                      '        fastcgi_param HTTP_FORWARDED "";', '        fastcgi_param HTTP_X_FORWARDED_PREFIX "";',
                      '        fastcgi_param HTTP_X_NODE_ENDPOINT "";', '        fastcgi_param HTTP_X_NODEENDPOINT "";']
        else:
            lines += [f'        proxy_pass {plan["backend"]};', '        proxy_http_version 1.1;',
                      '        proxy_set_header Host $http_host;', '        proxy_set_header X-Forwarded-Proto $scheme;',
                      '        proxy_set_header X-Forwarded-Port $server_port;', '        proxy_set_header X-Real-IP $remote_addr;',
                      '        proxy_set_header X-Forwarded-For $remote_addr;',
                      '        proxy_set_header X-Forwarded-Host $http_host;', '        proxy_set_header Forwarded "";',
                      '        proxy_set_header X-Forwarded-Prefix "";', '        proxy_set_header X-Node-Endpoint "";',
                      '        proxy_set_header X-NodeEndpoint "";', '        proxy_redirect off;']
        lines += ['    }']
    if plan.get('ws_backend'):
        lines += ['    location = /ws {', '        if ($request_method != GET) { return 405; }',
                  f'        proxy_pass {plan["ws_backend"]};', '        proxy_http_version 1.1;',
                  '        proxy_set_header Host $http_host;', '        proxy_set_header Upgrade $http_upgrade;',
                  '        proxy_set_header Connection "upgrade";', '        proxy_set_header X-Forwarded-Proto $scheme;',
                  '        proxy_set_header X-Forwarded-Port $server_port;',
                  '        proxy_set_header X-Forwarded-For $remote_addr;',
                  '        proxy_set_header X-Forwarded-Host $http_host;', '        proxy_set_header Forwarded "";',
                  '        proxy_set_header X-Forwarded-Prefix "";', '        proxy_set_header X-Node-Endpoint "";',
                  '        proxy_set_header X-NodeEndpoint "";', '        proxy_redirect off;',
                  '        proxy_read_timeout 90s;', '    }']
    lines += ['    location / { return 404; }', '}']
    return '\n'.join(lines) + '\n'


def sha256(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
