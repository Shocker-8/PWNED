import tempfile
import unittest
from pathlib import Path
from candidatelab.engine import generate

class ExportTests(unittest.TestCase):
    def run_case(self, data, limit):
        root = Path(self.tmp.name)
        source, dest = root / "in", root / "out"
        source.write_bytes(data)
        count = generate(source, dest, limit)
        return count, dest.read_bytes()
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
    def test_duplicates_spaces_final_record(self):
        self.assertEqual(self.run_case(b"alpha\r\nalpha\n beta ", 2), (2,b"alpha\n beta \n"))
    def test_zero(self):
        self.assertEqual(self.run_case(b"word",0),(0,b""))
    def test_negative_creates_no_file(self):
        with self.assertRaises(ValueError): self.run_case(b"word",-1)
        self.assertFalse((Path(self.tmp.name)/"out").exists())
    def test_invalid_encoding(self):
        with self.assertRaisesRegex(ValueError,"partial"): self.run_case(b"ok\n\xff",5)
    def test_oversized(self):
        with self.assertRaisesRegex(ValueError,"4096"): self.run_case(b"a"*4097,5)
    def test_boundary(self):
        self.assertEqual(self.run_case(b"a"*4096+b"\r\n",1), (0, b""))
    def test_existing_output(self):
        root=Path(self.tmp.name);(root/"in").write_bytes(b"a");(root/"out").write_bytes(b"keep")
        with self.assertRaises(FileExistsError): generate(root/"in",root/"out",1)
        self.assertEqual((root/"out").read_bytes(),b"keep")
    def test_empty_records(self):
        self.assertEqual(self.run_case(b"\n\r\na\n",2),(1,b"a\n"))
