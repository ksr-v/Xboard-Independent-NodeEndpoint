"""Conservative Nginx inspection and generation. Never edit original sites."""
from __future__ import annotations
from dataclasses import dataclass
import fnmatch
import hashlib
from pathlib import Path, PurePosixPath
import re
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

# Device admission is absent on older panels. Audit both guarded native routes
# together before exposing them; never infer support from a saved plan or prefix.
DEVICE_ADMISSION_API_METHODS = {
    '/api/v2/server/device/acquire': ['POST'],
    '/api/v2/server/device/release': ['POST'],
}
# The authenticated metadata report is supplied by the optional FlowScope
# plugin. No other FlowScope routes belong on the private entry.
OPTIONAL_API_METHODS = {
    '/api/v2/flowscope/node-address': ['POST'],
    **DEVICE_ADMISSION_API_METHODS,
}
OPTIONAL_API_ACTIONS = {
    '/api/v2/server/device/acquire': 'App\\Http\\Controllers\\V2\\Server\\ServerController@acquireDevice',
    '/api/v2/server/device/release': 'App\\Http\\Controllers\\V2\\Server\\ServerController@releaseDevice',
    '/api/v2/flowscope/node-address': 'Plugin\\Flowscope\\Controllers\\AddressController@store',
}
OPTIONAL_API_MIDDLEWARE = 'Plugin\\Flowscope\\Middleware\\EnsureEnabled'

OPTIONAL_API_GUARDS = {
    '/api/v2/flowscope/node-address': OPTIONAL_API_MIDDLEWARE,
    **{path: 'App\\Http\\Middleware\\ServerV2' for path in DEVICE_ADMISSION_API_METHODS},
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


LUA_BLOCKS = {name + '_by_lua_block' for name in (
    'init', 'init_worker', 'exit_worker', 'set', 'rewrite', 'server_rewrite',
    'access', 'content', 'header_filter', 'body_filter', 'log', 'balancer',
    'ssl_certificate', 'ssl_session_fetch', 'ssl_session_store', 'ssl_client_hello',
)}


def parse(text, source='<nginx>'):
    """Inspect Nginx tokens, not shell tokens. Never evaluate embedded Lua.

    Follow ngx_conf_read_token: #/quotes/} are special at a word boundary,
    ${...} stays within an argument, and unknown backslash escapes survive.
    Only known Lua block directives use Lua string/comment/brace scanning;
    their bodies are opaque and cannot introduce Nginx listeners/includes.
    """
    cursor = 0
    whitespace = ' \t\r\n'

    def error(reason, position):
        line = text.count('\n', 0, position) + 1
        column = position - text.rfind('\n', 0, position)
        # Do not include configuration lines or argument values (may be secrets).
        raise SafetyError(f'{reason} ({source}:{line}:{column})')

    def decode(raw):
        return re.sub(r'\\(["\'\\trn])', lambda m: {'t': '\t', 'r': '\r', 'n': '\n'}.get(m[1], m[1]), raw)

    def long_string(position):
        match = re.match(r'\[(=*)\[', text[position:])
        if not match:
            return None
        closing = ']' + match[1] + ']'
        end = text.find(closing, position + match.end())
        if end < 0:
            error('Unterminated Lua long string/comment', position)
        return end + len(closing)

    def lua_body(opening):
        nonlocal cursor
        depth = 1
        while cursor < len(text):
            position = cursor
            char = text[cursor]
            if text.startswith('--', cursor):
                end = long_string(cursor + 2)
                if end is None:
                    end = text.find('\n', cursor + 2)
                    end = len(text) if end < 0 else end + 1
                cursor = end
            elif char in ('"', "'"):
                cursor += 1
                while cursor < len(text) and text[cursor] != char:
                    cursor += 2 if text[cursor] == '\\' else 1
                if cursor >= len(text):
                    error('Unterminated Lua quoted string', position)
                cursor += 1
            elif char == '[' and (end := long_string(cursor)) is not None:
                cursor = end
            else:
                cursor += 1
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        return
        error('Unterminated Lua block', opening)

    def token():
        nonlocal cursor
        while cursor < len(text):
            if text[cursor] in whitespace:
                cursor += 1
            elif text[cursor] == '#':
                end = text.find('\n', cursor)
                cursor = len(text) if end < 0 else end + 1
            else:
                break
        start = cursor
        if cursor == len(text):
            return None, False, start
        char = text[cursor]
        if char in '{};':
            cursor += 1
            return char, False, start
        if char in ('"', "'"):
            cursor += 1
            begin = cursor
            while cursor < len(text) and text[cursor] != char:
                cursor += 2 if text[cursor] == '\\' else 1
            if cursor >= len(text):
                error('Unterminated Nginx quoted string', start)
            raw = text[begin:cursor]
            cursor += 1
            if cursor < len(text) and text[cursor] not in whitespace + ';{)':
                error('Expected separator after Nginx quoted string', cursor)
            return decode(raw), True, start
        variable = False
        while cursor < len(text):
            char = text[cursor]
            if char == '\\':
                if cursor + 1 >= len(text):
                    error('Unterminated Nginx escape', cursor)
                cursor += 2
                variable = False
                continue
            if char == '{' and variable:
                cursor += 1
                continue
            variable = char == '$'
            if char in whitespace + ';{':
                break
            cursor += 1
        return decode(text[start:cursor]), True, start

    def block(nested=False, depth=0):
        if depth > 128:
            error('Nginx nesting limit exceeded', cursor)
        result, words = [], []
        while True:
            word, literal, position = token()
            if word is None:
                if words or nested:
                    error('Unterminated Nginx directive/block', position)
                return result
            if not literal and word == '}':
                if words or not nested:
                    error('Malformed Nginx block', position)
                return result
            if not literal and word == ';':
                if not words:
                    error('Empty Nginx directive', position)
                result.append(Directive(words))
                words = []
            elif not literal and word == '{':
                if not words:
                    error('Unnamed Nginx block', position)
                if words[0] in LUA_BLOCKS:
                    lua_body(position)
                    children = []
                else:
                    children = block(True, depth + 1)
                result.append(Directive(words, children))
                words = []
            else:
                words.append(word)
    return block()


def dump_files(text):
    matches = list(re.finditer(r'^# configuration file ([^\r\n]+):[ \t]*\r?$', text, re.M))
    if not matches:
        raise SafetyError('nginx -T did not report loaded configuration files')
    return {m.group(1): text[m.end() + (text[m.end():m.end() + 1] == '\n'): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
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
                    result += expanded(parse(text, filename), files, prefix, (*stack, filename))
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
                    result += http_include_patterns(parse(text, name), prefix, files, context, (*seen, name))
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
    optional = {}
    for item in routes:
        path = '/' + item.get('uri', '').lstrip('/')
        if path in API_METHODS:
            observed.setdefault(path, []).append(set(item.get('method', '').split('|')) - {'HEAD'})
        if path in OPTIONAL_API_METHODS:
            optional.setdefault(path, []).append(item)
    if any(path not in observed or len(observed[path]) != 1 or
           not set(methods).issubset(observed[path][0]) for path, methods in API_METHODS.items()):
        raise SafetyError('Actual Laravel route table differs from audited Node API profile')
    verify_device_admission_pair(optional)
    for path, items in optional.items():
        if len(items) != 1:
            raise SafetyError('Optional Node API route must have exactly one audited action')
        item = items[0]
        method = item.get('method')
        if not isinstance(method, str):
            raise SafetyError('Optional Node API route has an invalid method')
        methods = set(method.split('|')) - {'HEAD'}
        middleware = item.get('middleware')
        if (methods != set(OPTIONAL_API_METHODS[path]) or
                item.get('action') != OPTIONAL_API_ACTIONS[path] or
                not isinstance(middleware, list) or OPTIONAL_API_GUARDS[path] not in middleware):
            raise SafetyError('Optional Node API route differs from its audited method, action or state guard')
    return list(optional)


def verify_device_admission_pair(paths):
    selected = set(paths).intersection(DEVICE_ADMISSION_API_METHODS)
    if selected and selected != set(DEVICE_ADMISSION_API_METHODS):
        raise SafetyError('Device admission requires both audited acquire and release routes')


def optional_api_methods(plan):
    """Old saved plans remain closed until an explicit route audit updates them."""
    paths = plan.get('optional_api_paths', [])
    if (not isinstance(paths, list) or any(not isinstance(path, str) or path not in OPTIONAL_API_METHODS for path in paths) or
            len(set(paths)) != len(paths)):
        raise SafetyError('Invalid optional Node API path selection')
    verify_device_admission_pair(paths)
    return {path: OPTIONAL_API_METHODS[path] for path in paths}


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
    api_methods = {**API_METHODS, **optional_api_methods(plan)}
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
    for path, methods in api_methods.items():
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
