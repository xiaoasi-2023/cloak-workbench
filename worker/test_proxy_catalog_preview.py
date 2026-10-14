"""Editor diagnostics must describe the saved route and survive cosmetic saves."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import BrowserWorker


class PreviewPersistenceTests(unittest.TestCase):
    def test_only_matching_route_results_saved_and_preserved_on_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = BrowserWorker(Path(directory), Path("missing.exe"))
            catalog = worker.catalog
            try:
                catalog.upsert({"id": "fixture", "name": "Fixture", "proxy": "fixture-region-US-asn-AS64500:synthetic@gateway.invalid:443"})
                result = {"reachable": True, "egressIp": "203.0.113.1", "isp": "Fixture ISP", "qualityScore": 96}
                with patch("proxy_catalog.diagnose_proxy", return_value=result):
                    catalog.draft_test({"id": "fixture"})
                    self.assertEqual(catalog.entry("fixture")["check"], result)
                    catalog.draft_test({"id": "fixture", "proxy": "http://different.invalid:80"})
                    self.assertEqual(catalog.entry("fixture")["check"], result)
                catalog.upsert({"id": "fixture", "name": "Renamed"})
                restored = BrowserWorker(Path(directory), Path("missing.exe"))
                self.assertEqual(restored.catalog.entry("fixture")["check"], result)
                restored.shutdown()
                catalog.upsert({"id": "fixture", "proxyPassword": "new-synthetic-secret"})
                self.assertIsNone(catalog.entry("fixture")["check"])
            finally:
                worker.shutdown()


if __name__ == "__main__":
    unittest.main()
