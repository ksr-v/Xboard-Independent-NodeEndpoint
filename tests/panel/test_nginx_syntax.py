"""Nginx token compatibility and private, source-aware error diagnostics."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
from nginx import check_port, dump_files, expanded, http_include_patterns, parse, walk
from pki import SafetyError


class SyntaxTests(unittest.TestCase):
    def test_braced_variables_are_arguments_not_blocks(self):
        nodes = parse('server { listen 8443; set $path ${document_root}/index.php; '
                      'proxy_set_header Host ${host}; }')
        self.assertEqual(nodes[0].children[1].words, ['set', '$path', '${document_root}/index.php'])
        self.assertEqual(nodes[0].children[2].words, ['proxy_set_header', 'Host', '${host}'])
        with self.assertRaises(SafetyError):
            check_port(8443, nodes, '')

    def test_nginx_escapes_preserve_regex_and_unknown_escapes(self):
        nodes = parse(r'''location ~ "^/file/\d{1,3}\.txt$" { return 200 "a\tb\nc\rd\\e\"f\'g"; }''')
        self.assertEqual(nodes[0].words, ['location', '~', r'^/file/\d{1,3}\.txt$'])
        self.assertEqual(nodes[0].children[0].words, ['return', '200', 'a\tb\nc\rd\\e"f\'g'])

    def test_escaped_unquoted_delimiters_do_not_change_structure(self):
        nodes = parse(r'server { set $v one\ two\;three\{four\}five; listen 80; }')
        self.assertEqual(nodes[0].children[0].words, ['set', '$v', r'one\ two\;three\{four\}five'])
        self.assertEqual(nodes[0].children[1].words, ['listen', '80'])

    def test_hash_and_quotes_inside_unquoted_word_are_literal(self):
        nodes = parse('set $value abc#def"ghi; # real comment with }\nset $other "";')
        self.assertEqual(nodes[0].words, ['set', '$value', 'abc#def"ghi'])
        self.assertEqual(nodes[1].words, ['set', '$other', ''])

    def test_quoted_if_argument_can_touch_closing_parenthesis(self):
        nodes = parse('if ($arg_x = "}") { return 403; }')
        self.assertEqual(nodes[0].words, ['if', '($arg_x', '=', '}', ')'])
        self.assertEqual(nodes[0].children[0].words, ['return', '403'])

    def test_lua_strings_comments_and_tables_are_opaque(self):
        body = '''http {
            init_by_lua_block {
                -- } fake end
                local t = { nested = { value = "}" }, v = 'it\\\'s {' }
                local s = [==[ } include /fake/*.conf; server { listen 8443; } ]==]
                --[=[ } { multiline
                comment ]=]
                local x = [=[ braces } { ]=]
            }
            include /real/*.conf;
            server { listen 443; content_by_lua_block { ngx.say("{ ; }") } }
        }'''
        files = {'/real/site.conf': 'server { listen 80; }'}
        nodes = parse(body)
        self.assertEqual(nodes[0].children[0].children, [])
        self.assertEqual(http_include_patterns(nodes, '/etc/nginx', files), ['/real/*.conf'])
        complete = list(walk(expanded(nodes, files, '/etc/nginx')))
        self.assertEqual([n.words for n in complete if n.words[0] == 'listen'], [['listen', '80'], ['listen', '443']])
        check_port(8443, complete, '')
        with self.assertRaises(SafetyError):
            check_port(443, complete, '')

    def test_unterminated_lua_is_not_silently_ignored(self):
        for text in ('init_by_lua_block { local t = {1}',
                     'content_by_lua_block { local s = [=[never closes }',
                     'access_by_lua_block { local s = "never closes }'):
            with self.subTest(text=text), self.assertRaises(SafetyError):
                parse(text, '/etc/nginx/waf.conf')

    def test_diagnostics_show_file_and_line_without_config_values(self):
        with self.assertRaises(SafetyError) as caught:
            parse('server {\nset $secret PRIVATE_VALUE\n}\n', '/etc/nginx/site.conf')
        self.assertIn('/etc/nginx/site.conf:3:1', str(caught.exception))
        self.assertNotIn('PRIVATE_VALUE', str(caught.exception))

    def test_include_failure_reports_included_filename(self):
        nodes = parse('http { include /etc/nginx/site.conf; }')
        with self.assertRaises(SafetyError) as caught:
            expanded(nodes, {'/etc/nginx/site.conf': 'server {\n bad\n}\n'}, '/etc/nginx')
        self.assertIn('/etc/nginx/site.conf:3:1', str(caught.exception))

    def test_dump_preserves_leading_blank_lines_for_diagnostics(self):
        raw = '# configuration file /etc/nginx/nginx.conf:\n\nserver {\n bad\n}\n'
        files = dump_files(raw)
        self.assertTrue(files['/etc/nginx/nginx.conf'].startswith('\nserver'))
        with self.assertRaises(SafetyError) as caught:
            parse(files['/etc/nginx/nginx.conf'], '/etc/nginx/nginx.conf')
        self.assertIn('/etc/nginx/nginx.conf:4:1', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
