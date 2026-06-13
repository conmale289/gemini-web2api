import tempfile
import unittest
from collections import Counter

from gemini_web2api.account_pool import AccountPool


class AccountPoolTests(unittest.TestCase):
    def test_rotation_uses_all_accounts(self):
        """Both accounts should be selected over multiple calls."""
        cfg = {
            "accounts": [
                {"id": "a1", "cookie": "SID=1; SAPISID=s1", "weight": 1},
                {"id": "a2", "cookie": "SID=2; SAPISID=s2", "weight": 1},
            ]
        }
        pool = AccountPool(cfg)
        ids = [pool.next_account()["id"] for _ in range(20)]
        counts = Counter(ids)
        self.assertIn("a1", counts)
        self.assertIn("a2", counts)

    def test_weighted_rotation_respects_weight(self):
        """Higher weight account should be selected more often."""
        cfg = {
            "accounts": [
                {"id": "a1", "cookie": "SID=1; SAPISID=s1", "weight": 1},
                {"id": "a2", "cookie": "SID=2; SAPISID=s2", "weight": 3},
            ]
        }
        pool = AccountPool(cfg)
        ids = [pool.next_account()["id"] for _ in range(100)]
        counts = Counter(ids)
        # a2 should be selected significantly more than a1
        self.assertGreater(counts["a2"], counts["a1"])

    def test_backoff_failover(self):
        """Failed account should be avoided."""
        cfg = {
            "accounts": [
                {"id": "a1", "cookie": "SID=1; SAPISID=s1"},
                {"id": "a2", "cookie": "SID=2; SAPISID=s2"},
            ]
        }
        pool = AccountPool(cfg)
        # Fail a1 multiple times to put it in hard backoff
        a1 = {"id": "a1"}
        for _ in range(3):
            pool.report_failure(a1)
        # Next selections should prefer a2
        ids = [pool.next_account()["id"] for _ in range(10)]
        counts = Counter(ids)
        self.assertGreater(counts.get("a2", 0), counts.get("a1", 0))

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

    def test_success_heals_account(self):
        """Reporting success should improve account health."""
        cfg = {"accounts": [{"id": "a1", "cookie": "SID=1; SAPISID=s1"}]}
        pool = AccountPool(cfg)
        acct = pool.next_account()
        pool.report_failure(acct)
        pool.report_failure(acct)
        pool.report_success(acct)
        pool.report_success(acct)
        # Should still be usable
        next_acct = pool.next_account()
        self.assertEqual(next_acct["id"], "a1")

    def test_single_account_works(self):
        """Single account pool should always return that account."""
        cfg = {"accounts": [{"id": "solo", "cookie": "SID=x; SAPISID=sp"}]}
        pool = AccountPool(cfg)
        ids = [pool.next_account()["id"] for _ in range(5)]
        self.assertTrue(all(i == "solo" for i in ids))


if __name__ == "__main__":
    unittest.main()
