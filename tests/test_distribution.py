import hashlib,importlib.util,json,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('distribution',ROOT/'tools/package-distribution.py')
distribution=importlib.util.module_from_spec(spec);spec.loader.exec_module(distribution)

class DistributionTests(unittest.TestCase):
 def payload(self,root,data):
  (root/'phoenix').mkdir();path=root/'phoenix/SHA256SUMS';path.write_bytes(data)
  (root/'payload.json').write_text(json.dumps({'Files':[{'Path':'phoenix/SHA256SUMS','Bytes':len(data),'Sha256':hashlib.sha256(data).hexdigest()}]}))
 def test_lf_payload_is_accepted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.payload(root,b'hash  file\n');distribution.validate_payload(root)
 def test_crlf_is_rejected_even_when_manifest_hash_matches(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.payload(root,b'hash  file\r\n')
   with self.assertRaisesRegex(ValueError,'LF endings'):distribution.validate_payload(root)
 def test_normalization_requires_updated_manifest(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.payload(root,b'hash  file\r\n');(root/'phoenix/SHA256SUMS').write_bytes(b'hash  file\n')
   with self.assertRaisesRegex(ValueError,'manifest mismatch'):distribution.validate_payload(root)
