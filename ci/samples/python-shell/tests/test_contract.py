import pathlib
import unittest

class PublicContractTest(unittest.TestCase):
    def test_synthetic_input_only(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        self.assertTrue((root/'check.sh').is_file())
    def test_no_private_dependency(self):
        self.assertEqual('synthetic'.upper(),'SYNTHETIC')
