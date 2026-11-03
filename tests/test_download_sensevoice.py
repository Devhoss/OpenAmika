"""scripts/download_sensevoice.py against a local HTTP server: download, verify, repair, fallback. Offline."""
import contextlib
import hashlib
import http.server
import importlib.util
import io
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("download_sensevoice", ROOT / "scripts" / "download_sensevoice.py")
dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl)

BLOBS = {"config.yaml": b"encoder: test\n" * 40, "tokens.json": b'["<unk>", "a", "b"]' * 30}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        name = self.path.rsplit("/", 1)[-1]
        if "/missing/" in self.path or name not in BLOBS:
            self.send_error(404)
            return
        data = BLOBS[name]
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class DownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (dl.DEST, dl.FILES, dl.SOURCES)
        dl.DEST = Path(self.tmp.name)
        dl.FILES = {n: (len(b), hashlib.sha256(b).hexdigest()) for n, b in BLOBS.items()}
        dl.SOURCES = (self.base + "/missing/{name}", self.base + "/huggingface.co/{name}", self.base + "/ok/{name}")

    def tearDown(self):
        dl.DEST, dl.FILES, dl.SOURCES = self.saved
        self.tmp.cleanup()

    def run_main(self, *argv):
        import sys
        old = sys.argv
        sys.argv = ["download_sensevoice.py", *argv]
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = dl.main()
        finally:
            sys.argv = old
        return rc, out.getvalue()

    def test_downloads_verifies_and_skips_404_sources(self):
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        for name, blob in BLOBS.items():
            self.assertEqual((dl.DEST / name).read_bytes(), blob)
        self.assertEqual(self.run_main("--verify")[0], 0)

    def test_verify_reports_missing_and_corrupt(self):
        rc, out = self.run_main("--verify")
        self.assertEqual(rc, 1)
        self.assertIn("is missing", out)
        self.run_main()
        (dl.DEST / "config.yaml").write_bytes(b"broken")
        rc, out = self.run_main("--verify")
        self.assertEqual(rc, 1)
        self.assertIn("wrong size or checksum", out)
        self.assertEqual(self.run_main()[0], 0)          # a normal run repairs it
        self.assertEqual((dl.DEST / "config.yaml").read_bytes(), BLOBS["config.yaml"])

    def test_finished_part_file_is_promoted(self):
        (dl.DEST / "config.yaml.part").write_bytes(BLOBS["config.yaml"])
        dl.SOURCES = (self.base + "/missing/{name}",)     # nothing downloadable: the .part must be enough
        dl.FILES = {"config.yaml": dl.FILES["config.yaml"]}
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertTrue((dl.DEST / "config.yaml").is_file())

    def test_wrong_bytes_are_never_kept(self):
        dl.FILES = {"config.yaml": (len(BLOBS["config.yaml"]), "0" * 64)}
        rc, _ = self.run_main()
        self.assertEqual(rc, 1)
        self.assertFalse((dl.DEST / "config.yaml").exists())

    def test_pins_cover_the_whole_model_folder(self):
        self.assertEqual(set(self.saved[1]), {"model.pt", "config.yaml", "configuration.json", "am.mvn",
                                               "chn_jpn_yue_eng_ko_spectok.bpe.model", "tokens.json"})


if __name__ == "__main__":
    unittest.main()
