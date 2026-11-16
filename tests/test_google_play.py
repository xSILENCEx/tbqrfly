import unittest
import json
import zipfile
from unittest.mock import Mock, patch

import test_beta
from tbqrfly.beta_config import validate_beta
from tbqrfly.beta_doctor import collect_beta_checks
from tbqrfly.clients.google_play import upload_google_play
from tbqrfly.config import ConfigError
from tbqrfly.workflow.beta import run_beta_platform, validate_aab


class GooglePlayTests(unittest.TestCase):
    setUp = test_beta.BetaTests.setUp
    save = test_beta.BetaTests.save
    def android_config(self):
        key = self.root / 'service account.json'
        key.write_text(json.dumps(dict(type='service_account', client_email='test@example.com',
                                      private_key='private-secret', token_uri='https://oauth2.googleapis.com/token')))
        self.raw['builds'] = {'android': {
            'build_command': 'flutter build appbundle --release',
            'artifacts': ['build/*.aab'], 'pre_commands': [], 'next_commands': ['post'],
            'publish': dict(provider='google_play', package_name='com.example.demo',
                            service_account_file=key.name, track='internal', release_status='completed')}}
        return self.save()

    def aab(self):
        artifact = self.root / 'build/demo.aab'
        artifact.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(artifact, 'w') as archive:
            archive.writestr('BundleConfig.pb', b'config')
            archive.writestr('base/manifest/AndroidManifest.xml', b'manifest')
        return artifact

    def test_android_validation(self):
        config = self.android_config()
        validate_beta(config, 'prod', ['android'])
        publish = config.builds['prod']['android']['publish']
        for field, value in [('track', 'production'), ('release_status', 'inProgress'),
                             ('package_name', ''), ('service_account_file', 'missing')]:
            with self.subTest(field=field):
                old = publish[field]
                publish[field] = value
                with self.assertRaises(ConfigError):
                    validate_beta(config, 'prod', ['android'])
                publish[field] = old
        (self.root / publish['service_account_file']).write_text('{}')
        with self.assertRaises(ConfigError):
            validate_beta(config, 'prod', ['android'])

    def test_android_upload(self):
        config = self.android_config()
        with patch('tbqrfly.clients.google_play.subprocess.run', return_value=Mock(returncode=0)) as run:
            upload_google_play(config, self.aab(), 'android')
            self.assertEqual(run.call_args.args[0], ['bundle', 'exec', 'fastlane', 'android', 'tbqrfly_upload'])
            self.assertNotIn('private-secret', str(run.call_args))
            self.assertEqual(run.call_args.kwargs['env']['TBQRFLY_PLAY_PACKAGE_NAME'], 'com.example.demo')
        with patch('tbqrfly.clients.google_play.subprocess.run', return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'Google Play 上传失败'):
                upload_google_play(config, self.aab(), 'android')

    def test_android_artifact(self):
        artifact = self.aab()
        validate_aab(artifact)
        artifact.write_bytes(b'invalid')
        with self.assertRaises(RuntimeError):
            validate_aab(artifact)
        with zipfile.ZipFile(artifact, 'w') as archive:
            archive.writestr('wrong', b'x')
        with self.assertRaises(RuntimeError):
            validate_aab(artifact)

    def test_android_pipeline(self):
        config = self.android_config()
        with (patch('tbqrfly.workflow.beta.run_build', side_effect=lambda *a: self.aab()),
              patch('tbqrfly.workflow.beta.upload_google_play') as upload,
              patch('tbqrfly.workflow.beta.upload_testflight') as ios,
              patch('tbqrfly.workflow.beta.run_commands') as commands,
              patch('tbqrfly.workflow.beta.notify_beta') as notify):
            run_beta_platform(config, env='prod', platform='android', info=self.info, summary='')
            upload.assert_called_once()
            ios.assert_not_called()
            self.assertIn('Google Play internal', notify.call_args.kwargs['message'])
            upload.side_effect = RuntimeError('upload failed')
            commands.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'upload failed'):
                run_beta_platform(config, env='prod', platform='android', info=self.info, summary='')
            self.assertEqual(commands.call_count, 1)
            upload.side_effect = None
            commands.side_effect = [None, RuntimeError('post failed')]
            with self.assertRaisesRegex(RuntimeError, 'post failed'):
                run_beta_platform(config, env='prod', platform='android', info=self.info, summary='')
            self.assertIn('已上传 Google Play internal', notify.call_args.kwargs['message'])

    def test_android_doctor_without_xcode(self):
        self.android_config()
        with (patch('tbqrfly.beta_doctor.platform.system', return_value='Linux'),
              patch('tbqrfly.doctor._which', return_value='/fake/tool'),
              patch('tbqrfly.beta_doctor.subprocess.run', side_effect=[
                  Mock(returncode=0, stdout='ruby 3.4.0'), Mock(returncode=0, stdout='Bundler version 2.4.0')])):
            checks = collect_beta_checks(self.root, ['android'])
        self.assertFalse(any(item.level == 'fail' for item in checks))
        names = [item.name for item in checks]
        self.assertIn('java', names)
        self.assertNotIn('xcodebuild', names)

    def test_android_cli_with_fake_toolchain(self):
        import os
        import subprocess
        import sys
        from pathlib import Path

        self.android_config()
        self.raw['builds']['android']['next_commands'] = []
        self.raw['terminal'] = 'inline'
        self.save()
        tools = self.root / 'bin'
        tools.mkdir()
        script = tools / 'fixture'
        script.write_text(f"#!{sys.executable}\n" + r'''
import os
import sys
import zipfile
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path(os.environ['BETA_TEST_ROOT'])
if name == 'ruby':
    print('ruby 3.4.0')
elif name == 'bundle':
    if args == ['--version']:
        print('Bundler version 4.0.20')
    elif args[:1] == ['exec']:
        assert args == ['exec', 'fastlane', 'android', 'tbqrfly_upload']
        assert Path(os.environ['TBQRFLY_BETA_AAB']).is_file()
        assert os.environ['TBQRFLY_PLAY_PACKAGE_NAME'] == 'com.example.demo'
        (root / 'uploaded').touch()
elif name == 'git':
    print('main' if '--abbrev-ref' in args else 'abc' if '--short' in args else 'abcdef')
elif name == 'flutter':
    assert args[:2] == ['build', 'appbundle']
    artifact = root / 'build/demo.aab'
    artifact.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(artifact, 'w') as archive:
        archive.writestr('BundleConfig.pb', b'config')
        archive.writestr('base/manifest/AndroidManifest.xml', b'manifest')
''')
        script.chmod(0o755)
        for name in ('flutter', 'git', 'ruby', 'bundle', 'java'):
            (tools / name).symlink_to(script)
        result = subprocess.run(
            [sys.executable, '-m', 'tbqrfly', 'beta', '--project', str(self.root), '--platform', 'android'],
            cwd=self.root, capture_output=True, text=True,
            env={**os.environ, 'PATH': str(tools) + os.pathsep + os.defpath,
                 'BETA_TEST_ROOT': str(self.root),
                 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / 'uploaded').exists())
        self.assertIn('Google Play internal', result.stdout)
