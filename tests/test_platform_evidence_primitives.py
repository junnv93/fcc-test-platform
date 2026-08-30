import unittest

from fcc_test_platform.evidence_primitives import is_sha256_hex, sha256_bytes


class TestPlatformEvidencePrimitives(unittest.TestCase):
    def test_sha256_bytes_matches_standard_vectors_and_is_canonical(self):
        vectors = {
            b'': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            b'abc': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
            'FCC evidence'.encode('utf-8'): 'c3b830197bd950eb5221045212de413abed585a6242a15519178fc98f85e71e7',
        }

        for payload, expected in vectors.items():
            with self.subTest(payload=payload):
                digest = sha256_bytes(payload)
                self.assertEqual(digest, expected)
                self.assertEqual(len(digest), 64)
                self.assertTrue(is_sha256_hex(digest))

    def test_accepts_lowercase_sha256_hex(self):
        self.assertTrue(is_sha256_hex('a' * 64))
        self.assertTrue(is_sha256_hex('0123456789abcdef' * 4))

    def test_rejects_placeholders_uppercase_and_wrong_length(self):
        self.assertFalse(is_sha256_hex('<sha256>'))
        self.assertFalse(is_sha256_hex('A' * 64))
        self.assertFalse(is_sha256_hex('g' * 64))
        self.assertFalse(is_sha256_hex('a' * 63))


if __name__ == '__main__':
    unittest.main()
