"""Original OPDS peer wire checks; real guest execution is a separate receipt."""
import base64
import http.client
from pathlib import Path
import runpy
import threading
import unittest
import xml.etree.ElementTree as ET


NETWORK = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/test-crossink-network.py"))


class OpdsSiblingPeerTests(unittest.TestCase):
    def setUp(self):
        self.book = b"Original EPUB byte fixture" * 256
        self.peer = NETWORK["OPDSFixture"](self.book)
        self.addCleanup(self.peer.close)
        self.headers = {"Authorization": "Basic " + base64.b64encode(
            (NETWORK["FIXTURE_USER"] + ":" + NETWORK["FIXTURE_PASSWORD"]).encode()).decode()}

    def fetch(self, path, headers=None):
        channel = http.client.HTTPConnection("127.0.0.1", self.peer.port, timeout=2)
        try:
            channel.request("GET", path, headers=headers or {})
            response = channel.getresponse()
            return response.status, response.read()
        finally:
            channel.close()

    def test_second_catalog_exact_encoded_query_and_original_acquisition(self):
        self.assertEqual(self.fetch("/catalog-two/")[0], 401)
        code, body = self.fetch("/catalog-two/", self.headers)
        root = ET.fromstring(body)
        atom = "{http://www.w3.org/2005/Atom}"
        self.assertEqual(code, 200)
        self.assertEqual(root.find(atom + "title").text, "Original Search Catalog")
        self.assertEqual(root.find(atom + "link").attrib["href"], "search.xml?q={searchTerms}")
        self.assertEqual(self.fetch("/catalog-two/search.xml?q=a+b", self.headers)[0], 400)
        code, body = self.fetch("/catalog-two/search.xml?q=a%20b", self.headers)
        entries = ET.fromstring(body).findall(atom + "entry")
        self.assertEqual(code, 200)
        self.assertEqual([entry.find(atom + "title").text for entry in entries], ["Search Result", "Cancelled Result"])
        self.assertEqual([entry.find(atom + "link").attrib["href"] for entry in entries],
                         ["/catalog-two/result.epub", "/catalog-two/cancel.epub"])
        self.assertEqual(self.fetch("/catalog-two/result.epub", self.headers), (200, self.book))

    def test_controlled_cancel_peer_declares_full_body_sends_prefix_then_gate_eof(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.peer.opds_faults["/catalog-two/cancel.epub"] = [{"kind": "stall", "prefix_bytes": 4096, "gate": gate}]
        channel = http.client.HTTPConnection("127.0.0.1", self.peer.port, timeout=2)
        self.addCleanup(channel.close)
        channel.request("GET", "/catalog-two/cancel.epub", headers=self.headers)
        response = channel.getresponse()
        self.assertEqual(int(response.getheader("Content-Length")), len(self.book))
        self.assertEqual(response.read(4096), self.book[:4096])
        record = self.peer.snapshot()[-1]
        self.assertEqual(record["response_size"], 4096)
        self.assertEqual(record["original_response_size"], len(self.book))
        self.assertEqual(record["intentional_peer_fault"], "stall")
        gate.set()
        with self.assertRaises(http.client.IncompleteRead):
            response.read()

    def test_pagination_links_are_feed_level_and_supported_directory_paths(self):
        atom = "{http://www.w3.org/2005/Atom}"
        for path, rel, href, title in (("/catalog-paged/", "next", "page-two/", "Page One Entry"),
                                     ("/catalog-paged/page-two/", "previous", "/catalog-paged/", "Page Two Entry")):
            self.assertEqual(self.fetch(path)[0], 401)
            code, body = self.fetch(path, self.headers)
            root = ET.fromstring(body)
            self.assertEqual(code, 200)
            self.assertEqual(root.find(atom + "link").attrib["rel"], rel)
            self.assertEqual(root.find(atom + "link").attrib["href"], href)
            self.assertEqual(root.find(atom + "entry").find(atom + "title").text, title)
        self.assertEqual(self.fetch("/catalog-paged/not-a-page/", self.headers)[0], 404)


if __name__ == "__main__":
    unittest.main()
