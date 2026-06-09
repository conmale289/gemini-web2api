import tempfile
import unittest

from gemini_web2api.account_pool import AccountPool


class AccountPoolTests(unittest.TestCase):
    def test_round_robin_rotation(self):
        cfg = {
            "accounts": [
                {"id": "a1", "cookie": "SID=1; SAPISID=s1", "weight": 1},
                {"id": "a2", "cookie": "SID=2; SAPISID=s2", "weight": 1},
            ]
        }
        pool = AccountPool(cfg)
        ids = [pool.next_account()["id"] for _ in range(4)]
        self.assertEqual(ids, ["a1", "a2", "a1", "a2"])

    def test_weighted_rotation(self):
        cfg = {
            "accounts": [
                {"id": "a1", "cookie": "SID=1; SAPISID=s1", "weight": 1},
                {"id": "a2", "cookie": "SID=2; SAPISID=s2", "weight": 2},
            ]
        }
        pool = AccountPool(cfg)
        ids = [pool.next_account()["id"] for _ in range(6)]
        self.assertEqual(ids, ["a1", "a2", "a2", "a1", "a2", "a2"])

    def test_backoff_failover(self):
        cfg = {
            "accounts": [
                {"id": "a1", "cookie": "SID=1; SAPISID=s1"},
                {"id": "a2", "cookie": "SID=2; SAPISID=s2"},
            ]
        }
        pool = AccountPool(cfg)
        first = pool.next_account()
        self.assertEqual(first["id"], "a1")
        pool.report_failure(first)
        second = pool.next_account()
        self.assertEqual(second["id"], "a2")

    def test_legacy_cookie_file_fallback(self):
        with tempfile.NamedTemporaryFile("w+", delete=True) as f:
            f.write("SID=legacy; SAPISID=legacy_sapisid")
            f.flush()
            cfg = {
                "accounts": [],
                "cookie_file": f.name,
                "auth_user": "1",
                "xsrf_token": "x",
            }
            pool = AccountPool(cfg)
            acct = pool.next_account()
            self.assertEqual(acct["id"], "legacy")
            self.assertEqual(acct["auth_user"], "1")
            self.assertEqual(acct["xsrf_token"], "x")
            self.assertIn("SID=legacy", acct["cookie"])
            self.assertEqual(acct["sapisid"], "legacy_sapisid")


if __name__ == "__main__":
    unittest.main()
