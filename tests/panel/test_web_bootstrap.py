"""Pinned web-helper bootstrap boundary; no actual privileged effects."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/panel'))
import panel_ca
spec=importlib.util.spec_from_file_location('web_bootstrap_quick',ROOT/'tools/panel/quick_install.py')
quick=importlib.util.module_from_spec(spec);spec.loader.exec_module(quick)

class BootstrapBoundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.source=self.base/'helper.py'
        self.data=b'print("synthetic installer fixture")\n';self.source.write_bytes(self.data)
        self.plan={'deployment':'fpm','https':True,'app':'/fixture/app','app_user':'www','php':'/fixture/php'}
        self.digest=hashlib.sha256(self.data).hexdigest()

    def paths(self,value):
        if str(value)=='/opt/nodeendpoint-web-bootstrap': return self.base/'cache'
        return Path(value)

    def test_tampered_application_owned_asset_never_executes(self):
        with patch.object(panel_ca,'payload_from_state',return_value=(self.plan,{})), \
             patch.object(quick,'WEB_MANAGER_SHA256','f'*64), \
             patch.object(quick.subprocess,'run') as execute:
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                quick.install_web_manager(helper_file=self.source)
            execute.assert_not_called()

    def test_verified_bytes_are_copied_before_root_install_and_confirm_is_explicit(self):
        with patch.object(panel_ca,'payload_from_state',return_value=(self.plan,{})), \
             patch.object(quick,'WEB_MANAGER_SHA256',self.digest), \
             patch.object(quick,'secure_path',side_effect=lambda p:Path(p)), \
             patch.object(quick,'Path',side_effect=self.paths), \
             patch.object(quick.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as execute:
            result=quick.install_web_manager(helper_file=self.source,nginx='/fixture/nginx',confirmed=True)
            argv=execute.call_args.args[0]
            self.assertEqual(Path(argv[2]).read_bytes(),self.data)
            self.assertNotEqual(Path(argv[2]),self.source)
            self.assertEqual(argv[-2:],['--confirm','ENABLE WEB MANAGEMENT'])
            self.assertEqual(result,{'installed':True,'version':'1.0.0'})

    def test_foreign_cached_bytes_are_not_overwritten_or_executed(self):
        target=self.base/'cache'/self.digest/'web_manager.py';target.parent.mkdir(parents=True)
        target.write_bytes(b'foreign fixture bytes')
        with patch.object(panel_ca,'payload_from_state',return_value=(self.plan,{})), \
             patch.object(quick,'WEB_MANAGER_SHA256',self.digest), \
             patch.object(quick,'secure_path',side_effect=lambda p:Path(p)), \
             patch.object(quick,'Path',side_effect=self.paths), \
             patch.object(quick.subprocess,'run') as execute:
            with self.assertRaisesRegex(ValueError,'缓存被修改'):
                quick.install_web_manager(helper_file=self.source)
            execute.assert_not_called();self.assertEqual(target.read_bytes(),b'foreign fixture bytes')

    def test_docker_binding_fails_before_fetch_or_install(self):
        self.plan['deployment']='docker'
        with patch.object(panel_ca,'payload_from_state',return_value=(self.plan,{})), \
             patch.object(quick.urllib.request,'build_opener') as fetch, \
             patch.object(quick.subprocess,'run') as execute:
            with self.assertRaisesRegex(ValueError,'Docker'):
                quick.install_web_manager()
            fetch.assert_not_called();execute.assert_not_called()

if __name__=='__main__':unittest.main()
