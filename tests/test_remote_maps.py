import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import harvest,test_bootstrap

class RemoteMapTests(unittest.TestCase):
 def fixture(self,root):
  rows=test_bootstrap.FontPolicyTests().fixture(root);files=harvest.inventory([root/'stock']);browser=harvest.latest_browser(files)
  folder=browser.parent/'irkeymap';folder.mkdir();(folder/'primary.xml').write_text('<map/>');(folder/'nested').mkdir();(folder/'nested/extra.dat').write_bytes(b'additional data')
  rows.append(('0'*64,'runtime/opt/chromium/bin/irkeymap/retired.xml'))
  return rows,folder
 def test_whole_directory_is_copied_without_historical_name_checks(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows,folder=self.fixture(root)
   with patch.object(harvest,'requirements',return_value=rows):selected,found=harvest.check(root,[root/'stock'])
   harvest.copy(selected,found,root/'out');target=root/'out/runtime/opt/chromium/bin/irkeymap'
   self.assertEqual((target/'primary.xml').read_bytes(),(folder/'primary.xml').read_bytes());self.assertEqual((target/'nested/extra.dat').read_bytes(),b'additional data');self.assertFalse((target/'retired.xml').exists())
 def test_actual_map_copy_integrity_is_still_required(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows,folder=self.fixture(root)
   with patch.object(harvest,'requirements',return_value=rows):selected,found=harvest.check(root,[root/'stock'])
   (folder/'primary.xml').write_text('changed')
   with self.assertRaisesRegex(ValueError,'copy verification'):harvest.copy(selected,found,root/'out')
