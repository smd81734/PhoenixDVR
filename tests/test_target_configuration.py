import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import harvest
import test_bootstrap

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('config_prepare', ROOT/'runtime/prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
CONFIG = 'home/hybroad/share/.DTV_frontend_config.cfg'


class ConfigurationPolicyTests(unittest.TestCase):
    def fixture(self, root):
        rows = test_bootstrap.FontPolicyTests().fixture(root)
        config = root/'stock'/CONFIG
        config.parent.mkdir(parents=True)
        config.write_bytes(b'target-specific tuner settings')
        return rows+[('PRESENT', 'runtime/'+CONFIG)], config

    def test_different_configuration_is_accepted_and_copy_verified_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, config = self.fixture(root)
            report = []
            with patch.object(harvest, 'requirements', return_value=rows):
                selected, found = harvest.check(root, [root/'stock'], selection_report=report)
            self.assertIn((harvest.sha(config), 'runtime/'+CONFIG), selected)
            row = next(r for r in report if r['destination'] == 'runtime/'+CONFIG)
            self.assertIsNone(row['expected_sha256'])
            harvest.copy(selected, found, root/'output')
            self.assertEqual((root/'output/runtime'/CONFIG).read_bytes(), config.read_bytes())
            config.write_bytes(b'another supported unit')
            with self.assertRaisesRegex(ValueError, 'copy verification'):
                harvest.copy(selected, found, root/'corrupted')

    def test_missing_configuration_is_not_substituted_by_an_unrelated_same_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, config = self.fixture(root)
            config.unlink()
            (root/'stock'/config.name).write_bytes(b'wrong location')
            with patch.object(harvest, 'requirements', return_value=rows):
                with self.assertRaisesRegex(ValueError, 'configuration is missing'):
                    harvest.check(root, [root/'stock'])

    def test_native_hash_mismatch_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, config = self.fixture(root)
            (root/'stock/lib/libnative.so').write_bytes(b'unsupported native code')
            with patch.object(harvest, 'requirements', return_value=rows):
                with self.assertRaisesRegex(ValueError, 'Firmware does not match'):
                    harvest.check(root, [root/'stock'])

    def test_manifest_supports_presence_marker_and_standalone_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root/CONFIG
            config.parent.mkdir(parents=True)
            config.write_bytes(b'per-device settings')
            manifest = root/'vendor-files.sha256'
            manifest.write_text('PRESENT  '+CONFIG+'  Required target configuration\n')
            self.assertEqual(harvest.manifest(manifest), [('PRESENT', CONFIG)])
            self.assertEqual(prepare.fingerprint_files(manifest, root), [(config, CONFIG)])
            config.unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                prepare.fingerprint_files(manifest, root)
