from pathlib import Path
import tempfile
import unittest
from candidatelab.config import Config
from candidatelab.engine import run
from candidatelab.wizard import collect
from test_questionnaire import answers_for

class PresetTests(unittest.TestCase):
    def test_wpa2_boundary_and_ascii_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out"
            seeds=["a"*7,"b"*8,"c"*63,"d"*64," café xx", "  spaces  ", "tab\tword"]
            result=run(Config(seeds=seeds,output=str(output),preset="wpa2",min_length=None,max_length=None))
            self.assertEqual(output.read_bytes(), ("b"*8+"\n"+"c"*63+"\n  spaces  \n").encode())
            self.assertEqual((result.accepted,result.filtered),(3,4))

    def test_wpa2_default_user_maximum_remains_40(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out"
            run(Config(seeds=["a"*8,"b"*41],output=str(output),preset="wpa2"))
            self.assertEqual(output.read_bytes(), b"aaaaaaaa\n")

    def test_wpa2_rules_apply_after_transformation(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out"
            run(Config(seeds=["short"],output=str(output),preset="wpa2",transforms={"suffixes":["123"]}))
            self.assertEqual(output.read_bytes(), b"short123\n")

    def test_raw_hex_64_is_not_a_passphrase(self):
        with tempfile.TemporaryDirectory() as folder:
            result=run(Config(seeds=["a1"*32],output=str(Path(folder)/"out"),preset="wpa2",max_length=None))
            self.assertEqual(result.accepted,0)

    def test_invalid_settings_and_linebreaks(self):
        for extra in ({"preset":"unknown"},{"preset":"wpa2","max_length":7},{"preset":"wpa2","min_length":64,"max_length":None},{"seeds":["password\nvalue"]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                Config.from_dict({"seeds":["word"],**extra})

    def test_wizard_wifi_selection(self):
        config=collect(answers_for(Choose="2"),lambda _:None)
        self.assertEqual(config.preset,"wpa2")
        self.assertEqual((config.min_length,config.max_length),(1,40))
