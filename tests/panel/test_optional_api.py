"""Optional audited APIs never expand the private entry to a route prefix."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/panel'))
from nginx import API_METHODS, parse, render, verify_routes
from pki import SafetyError

ADDRESS_PATH = '/api/v2/flowscope/node-address'
ADDRESS_ROUTE = {
    'uri': ADDRESS_PATH[1:], 'method': 'POST',
    'action': 'Plugin\\Flowscope\\Controllers\\AddressController@store',
    'middleware': ['api', 'Plugin\\Flowscope\\Middleware\\EnsureEnabled'],
}
ADMISSION_PATHS = ['/api/v2/server/device/acquire', '/api/v2/server/device/release']
ADMISSION_ROUTES = [
    {
        'uri': path[1:], 'method': 'POST',
        'action': r'App\Http\Controllers\V2\Server\ServerController@' + action,
        'middleware': ['api', r'App\Http\Middleware\ServerV2'],
    }
    for path, action in zip(ADMISSION_PATHS, ['acquireDevice', 'releaseDevice'])
]


class OptionalApiTests(unittest.TestCase):
    def core_routes(self):
        return [{'uri': path[1:], 'method': '|'.join(methods)} for path, methods in API_METHODS.items()]

    def plan(self, **overrides):
        return {
            'version': '1.0.0', 'ownership': 'a' * 32, 'host': 'node.example',
            'port': 8443, 'https': True, 'ipv6': False, 'deployment': 'octane',
            'backend': 'http://127.0.0.1:8000', 'app': '/srv/xboard', **overrides,
        }

    def test_original_panel_without_plugin_is_accepted(self):
        self.assertEqual(verify_routes(self.core_routes()), [])
        with self.assertRaises(SafetyError):
            verify_routes(self.core_routes()[:-1] + [ADDRESS_ROUTE])

    def test_only_present_audited_guarded_post_is_selected(self):
        self.assertEqual(verify_routes(self.core_routes() + [ADDRESS_ROUTE]), [ADDRESS_PATH])
        # Match the existing native route audit convention: HEAD is stripped.
        self.assertEqual(verify_routes(self.core_routes() + [{**ADDRESS_ROUTE, 'method': 'POST|HEAD'}]), [ADDRESS_PATH])
        for changed in (
            {'method': 'GET'}, {'method': 'POST|GET'}, {'method': 'PUT'},
            {'action': 'Plugin\\Flowscope\\Controllers\\CollectorController@store'},
            {'action': 'Closure'}, {'middleware': ['api']}, {'middleware': None},
        ):
            with self.subTest(changed=changed), self.assertRaises(SafetyError):
                verify_routes(self.core_routes() + [{**ADDRESS_ROUTE, **changed}])
        with self.assertRaises(SafetyError):
            verify_routes(self.core_routes() + [ADDRESS_ROUTE, ADDRESS_ROUTE])

    def test_optional_route_is_exact_post_for_fpm_and_proxy(self):
        for deployment, backend in [('fpm', 'unix:/run/php.sock'), ('octane', 'http://127.0.0.1:8000')]:
            with self.subTest(deployment=deployment):
                plan = self.plan(deployment=deployment, backend=backend, optional_api_paths=[ADDRESS_PATH])
                text = render(plan, '/private/server.pem', '/private/key.pem')
                nodes = parse(text)[0].children
                selected = [node for node in nodes if node.words == ['location', '=', ADDRESS_PATH]]
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].children[0].words, ['if', '($request_method', '!~', '^(POST)$)'])
                self.assertEqual(selected[0].children[0].children[0].words, ['return', '405'])
                self.assertIn('location / { return 404; }', text)
                for forbidden in ('/api/v2/flowscope/collect', '/api/v2/flowscope/status',
                                  '/api/v2/flowscope/assets', '/api/v2/flowscope/machine-addresses'):
                    self.assertNotIn('location = ' + forbidden, text)
                self.assertNotIn('location /api/v2/flowscope', text)
                self.assertEqual(text.count('location = /api/'), len(API_METHODS) + 1)

    def test_device_admission_pair_is_selected_after_auditing_native_actions(self):
        self.assertEqual(verify_routes(self.core_routes() + ADMISSION_ROUTES), ADMISSION_PATHS)
        self.assertEqual(verify_routes(self.core_routes() + [ADDRESS_ROUTE] + ADMISSION_ROUTES),
                         [ADDRESS_PATH] + ADMISSION_PATHS)
        with_head = [{**route, 'method': 'POST|HEAD'} for route in ADMISSION_ROUTES]
        self.assertEqual(verify_routes(self.core_routes() + with_head), ADMISSION_PATHS)

    def test_device_admission_partial_duplicate_and_unguarded_routes_are_rejected(self):
        for index in range(len(ADMISSION_ROUTES)):
            with self.subTest(partial=index), self.assertRaises(SafetyError):
                verify_routes(self.core_routes() + [ADMISSION_ROUTES[index]])
            with self.subTest(duplicate=index), self.assertRaises(SafetyError):
                verify_routes(self.core_routes() + ADMISSION_ROUTES + [ADMISSION_ROUTES[index]])
            for changed in (
                {'method': 'GET'}, {'method': 'POST|GET'}, {'method': 'PUT'},
                {'method': None}, {'method': ['POST']},
                {'action': 'Closure'}, {'action': ADMISSION_ROUTES[1 - index]['action']},
                {'middleware': ['api']}, {'middleware': ['api', 'server.v2']},
                {'middleware': None}, {'middleware': r'App\Http\Middleware\ServerV2'},
            ):
                routes = list(ADMISSION_ROUTES)
                routes[index] = {**routes[index], **changed}
                with self.subTest(index=index, changed=changed), self.assertRaises(SafetyError):
                    verify_routes(self.core_routes() + routes)

    def test_device_admission_routes_are_exact_guarded_posts_for_fpm_and_proxy(self):
        audited = verify_routes(self.core_routes() + [ADDRESS_ROUTE] + ADMISSION_ROUTES)
        for deployment, backend in [('fpm', 'unix:/run/php.sock'), ('octane', 'http://127.0.0.1:8000')]:
            with self.subTest(deployment=deployment):
                plan = self.plan(deployment=deployment, backend=backend, optional_api_paths=audited)
                text = render(plan, '/private/server.pem', '/private/key.pem')
                nodes = parse(text)[0].children
                for path in ADMISSION_PATHS:
                    selected = [node for node in nodes if node.words == ['location', '=', path]]
                    self.assertEqual(len(selected), 1)
                    self.assertEqual(selected[0].children[0].words, ['if', '($request_method', '!~', '^(POST)$)'])
                    self.assertEqual(selected[0].children[0].children[0].words, ['return', '405'])
                self.assertNotIn('location /api/v2/server/device', text)
                self.assertNotIn('location = /api/v2/server/device/status', text)
                self.assertIn('location / { return 404; }', text)
                self.assertEqual(text.count('location = /api/'), len(API_METHODS) + 3)

    def test_saved_plan_cannot_enable_half_of_device_admission(self):
        for path in ADMISSION_PATHS:
            with self.subTest(path=path), self.assertRaises(SafetyError):
                render(self.plan(optional_api_paths=[path]), '/private/server.pem', '/private/key.pem')

    def test_old_plan_keeps_original_closed_render(self):
        old = render(self.plan(), '/private/server.pem', '/private/key.pem')
        self.assertNotIn(ADDRESS_PATH, old)
        for path in ADMISSION_PATHS:
            self.assertNotIn(path, old)
        self.assertEqual(old, render(self.plan(optional_api_paths=[]), '/private/server.pem', '/private/key.pem'))

    def test_plan_selection_rejects_unknown_duplicate_and_invalid_fields(self):
        for paths in (None, ADDRESS_PATH, {}, True, [None], [ADDRESS_PATH, ADDRESS_PATH],
                      ['/api/v2/flowscope/collect'], ['/api/v2/flowscope/'], ['../api/v2/flowscope/node-address']):
            with self.subTest(paths=paths), self.assertRaises(SafetyError):
                render(self.plan(optional_api_paths=paths), '/private/server.pem', '/private/key.pem')


if __name__ == '__main__':
    unittest.main(verbosity=2)
