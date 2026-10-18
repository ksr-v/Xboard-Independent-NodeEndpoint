"""Public-IP suggestions and documentation-address guards; no real network or state writes."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/panel'))
spec = importlib.util.spec_from_file_location('public_ip_quick_fixture', ROOT / 'tools/panel/quick_install.py')
quick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quick)

SOURCE = 'https://api.ipify.org'


class Response:
    def __init__(self, body, url=SOURCE):
        self.body = body
        self.url = url
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        self.read_sizes.append(size)
        return self.body[:size]

    def geturl(self):
        return self.url


class PublicIPDetectionTests(unittest.TestCase):
    def detect(self, body=b'8.8.8.8\n', url=SOURCE):
        response = Response(body, url)
        opener = Mock()
        opener.open.return_value = response
        result = quick.detect_public_ip(opener)
        return result, response, opener

    def test_fixed_https_source_timeout_and_bounded_read(self):
        result, response, opener = self.detect()
        self.assertEqual(result, '8.8.8.8')
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertIsInstance(request, urllib.request.Request)
        self.assertEqual(request.full_url, SOURCE)
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 5})
        self.assertEqual(response.read_sizes, [129])

    def test_nonpublic_nonipv4_and_malformed_responses_are_not_suggestions(self):
        rejected = [
            b'', b'not-an-ip', b'panel.example', b'{"ip":"8.8.8.8"}',
            b'8.8.8.8\n1.1.1.1', b'8.8.8.8:8443', b'8.8.8.8\x00', b'\xff',
            b'0.0.0.0', b'127.0.0.1', b'10.0.0.1', b'172.16.0.1', b'192.168.1.1',
            b'169.254.1.1', b'100.64.0.1', b'192.0.2.10', b'198.51.100.20', b'203.0.113.10',
            b'224.0.0.1', b'255.255.255.255', b'2001:4860:4860::8888', b'2001:db8::1',
        ]
        for body in rejected:
            with self.subTest(body=body):
                result, response, _ = self.detect(body)
                self.assertEqual(result, '')
                self.assertEqual(response.read_sizes, [129])

    def test_oversized_body_is_refused_even_when_prefix_is_a_valid_ip(self):
        result, response, _ = self.detect(b'8.8.8.8' + b' ' * 122)
        self.assertEqual(result, '')
        self.assertEqual(response.read_sizes, [129])

    def test_redirected_response_is_not_trusted_even_over_https(self):
        for target in ['https://other.example/', 'https://api.ipify.org/changed', 'http://api.ipify.org']:
            with self.subTest(target=target):
                result, _, _ = self.detect(url=target)
                self.assertEqual(result, '')

    def test_timeout_and_http_failure_allow_manual_input(self):
        for error in [TimeoutError(), urllib.error.URLError('fixture unavailable'),
                      urllib.error.HTTPError(SOURCE, 503, 'fixture unavailable', {}, None)]:
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                self.assertEqual(quick.detect_public_ip(opener), '')
                opener.open.assert_called_once()

    def test_default_opener_refuses_every_redirect_before_following_it(self):
        response = Response(b'8.8.8.8')
        opener = Mock()
        opener.open.return_value = response
        with patch.object(quick.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(quick.detect_public_ip(), '8.8.8.8')
        handlers = [handler for handler in build.call_args.args
                    if isinstance(handler, urllib.request.HTTPRedirectHandler)]
        self.assertEqual(len(handlers), 1, 'Detection needs its own no-redirect policy.')
        handler = handlers[0]
        for status in [301, 302, 303, 307, 308]:
            for target in ['http://other.example/', 'https://other.example/', SOURCE]:
                with self.subTest(status=status, target=target):
                    try:
                        redirected = handler.redirect_request(urllib.request.Request(SOURCE), None,
                                                              status, 'fixture', {}, target)
                    except (ValueError, urllib.error.URLError):
                        continue
                    self.assertIsNone(redirected, 'Public-IP detection would follow an HTTP redirect.')


class CommunicationHostTests(unittest.TestCase):
    def test_documentation_networks_are_rejected_at_candidate_write_boundary(self):
        addresses = ['192.0.2.0', '192.0.2.10', '192.0.2.255',
                     '198.51.100.0', '198.51.100.20', '198.51.100.255',
                     '203.0.113.0', '203.0.113.10', '203.0.113.255',
                     '2001:db8::', '2001:DB8::1', '2001:db8:ffff:ffff:ffff:ffff:ffff:ffff']
        for value in addresses:
            with self.subTest(value=value), self.assertRaises(ValueError):
                quick.reject_example_host(value)

    def test_valid_bare_addresses_and_dns_are_normalized_without_restricting_lan(self):
        for value, expected in [('8.8.8.8', '8.8.8.8'), ('192.168.128.128', '192.168.128.128'),
                                (' PANEL.Example. ', 'panel.example'), ('2001:4860:4860::8888', '2001:4860:4860::8888'),
                                ('203.0.113.10.example.com', '203.0.113.10.example.com')]:
            with self.subTest(value=value):
                self.assertEqual(quick.reject_example_host(value), expected)

    def test_invalid_bare_host_inputs_are_rejected(self):
        for value in ['', 'https://8.8.8.8', '8.8.8.8:8443', '[2001:4860:4860::8888]',
                      '8.8.8.8/path', '8.8.8.8?host=1.1.1.1', '../host', '999.1.1.1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                quick.reject_example_host(value)

    def test_existing_real_address_is_retained_without_detection_or_prompt(self):
        for current in ['8.8.8.8', '192.168.128.128', 'panel.example']:
            with self.subTest(current=current), patch.object(quick, 'detect_public_ip') as detect, \
                    patch.object(quick, 'ask') as ask:
                self.assertEqual(quick.select_communication_host(current=current), current)
                detect.assert_not_called()
                ask.assert_not_called()

    def test_explicit_new_address_skips_detection_and_prompt(self):
        with patch.object(quick, 'detect_public_ip') as detect, patch.object(quick, 'ask') as ask:
            self.assertEqual(quick.select_communication_host(explicit='1.1.1.1'), '1.1.1.1')
            detect.assert_not_called()
            ask.assert_not_called()

    def test_fresh_install_suggests_detected_ip_and_accepts_entered_choice(self):
        with patch.object(quick, 'detect_public_ip', return_value='8.8.8.8') as detect, \
                patch.object(quick, 'ask', return_value='8.8.8.8') as ask:
            self.assertEqual(quick.select_communication_host(), '8.8.8.8')
            detect.assert_called_once_with()
            ask.assert_called_once()
            self.assertEqual(ask.call_args.args[1] if len(ask.call_args.args) > 1 else ask.call_args.kwargs['default'], '8.8.8.8')

    def test_user_can_override_egress_suggestion_for_nat_or_multihomed_host(self):
        with patch.object(quick, 'detect_public_ip', return_value='8.8.8.8'), \
                patch.object(quick, 'ask', return_value='1.1.1.1'):
            self.assertEqual(quick.select_communication_host(), '1.1.1.1')

    def test_failed_detection_prompts_without_an_invented_default(self):
        with patch.object(quick, 'detect_public_ip', return_value=''), \
                patch.object(quick, 'ask', return_value='panel.example') as ask:
            self.assertEqual(quick.select_communication_host(), 'panel.example')
            self.assertEqual(ask.call_args.args[1] if len(ask.call_args.args) > 1 else ask.call_args.kwargs.get('default', ''), '')

    def test_manual_and_explicit_documentation_addresses_never_become_candidates(self):
        with patch.object(quick, 'detect_public_ip', return_value='8.8.8.8'), \
                patch.object(quick, 'ask', return_value='203.0.113.10'), self.assertRaises(ValueError):
            quick.select_communication_host()
        with patch.object(quick, 'detect_public_ip') as detect, patch.object(quick, 'ask') as ask:
            with self.assertRaises(ValueError):
                quick.select_communication_host(explicit='203.0.113.10')
            detect.assert_not_called()
            ask.assert_not_called()

    def test_legacy_documentation_address_requires_explicit_repair_without_detection(self):
        with patch.object(quick, 'detect_public_ip') as detect, patch.object(quick, 'ask') as ask:
            with self.assertRaises(ValueError):
                quick.select_communication_host(current='203.0.113.10')
            detect.assert_not_called()
            ask.assert_not_called()


if __name__ == '__main__':
    unittest.main()
