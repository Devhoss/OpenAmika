"""Launcher helpers: which launch.py a command line runs, local-only role detection, relative interpreter paths.

Offline; imports launch.py without running it.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("openamika_launch", ROOT / "launch.py")
launch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launch)


class LauncherTargetTests(unittest.TestCase):
    def test_relative_launcher_is_unknown(self):
        self.assertEqual(launch.launcher_target(r"C:\envs\brain\python.exe -X utf8 launch.py"), "")

    def test_own_launcher_by_absolute_path(self):
        own = os.path.normcase(str(ROOT / "launch.py"))
        cmd = f'"C:\\envs\\brain\\python.exe" -X utf8 "{ROOT / "launch.py"}" --no-browser'
        self.assertEqual(launch.launcher_target(cmd), own)

    def test_other_checkout_is_recognised(self):
        other = ROOT.parent / "SomeOtherCheckout" / "launch.py"
        cmd = f'"C:\\envs\\brain\\python.exe" -X utf8 "{other}"'
        target = launch.launcher_target(cmd)
        self.assertTrue(target)
        self.assertNotEqual(target, os.path.normcase(str(ROOT / "launch.py")))

    def test_unbalanced_quotes_do_not_raise(self):
        self.assertEqual(launch.launcher_target('python "launch.py'), "")


class LocalRolesTests(unittest.TestCase):
    def _with_roles(self, selections):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "config").mkdir()
        (Path(tmp.name) / "config" / "llm_roles.json").write_text(json.dumps({"selections": selections}), "utf-8")
        old = launch.ROOT
        launch.ROOT = Path(tmp.name)
        self.addCleanup(setattr, launch, "ROOT", old)

    def test_all_ollama(self):
        self._with_roles({r: f"{r}:ollama:qwen3:8b" for r in ("deep", "surface", "reflection", "memory_write")})
        self.assertTrue(launch.roles_all_local())

    def test_mixed_roles_need_the_key(self):
        self._with_roles({"deep": "deep:z-ai/glm-5#no-reasoning", "surface": "surface:ollama:qwen3:8b",
                          "reflection": "reflection:ollama:qwen3:8b", "memory_write": "memory_write:ollama:qwen3:8b"})
        self.assertFalse(launch.roles_all_local())

    def test_shipped_defaults_use_openrouter(self):
        self.assertFalse(launch.roles_all_local())

    def test_missing_tools_json_is_not_a_problem(self):
        self._with_roles({})
        self.assertEqual(launch.load_tools(), (None, None))
        (launch.ROOT / "config" / "tools.json").write_text("{not json", "utf-8")
        tools, error = launch.load_tools()
        self.assertIsNone(tools)
        self.assertIn("not valid JSON", error)


class ShippedSettingsTests(unittest.TestCase):
    def test_example_uses_relative_interpreters(self):
        cfg = json.loads((ROOT / "settings.example.json").read_text("utf-8"))
        for value in (cfg["brain_python"], cfg["speech_python"], cfg["embedder"]["python"], cfg["vision"]["python"]):
            self.assertFalse(Path(value).is_absolute(), value)
            self.assertTrue(str(launch.resolve(value)).startswith(str(ROOT)))

    def test_ports_are_distinct(self):
        cfg = json.loads((ROOT / "settings.example.json").read_text("utf-8"))
        ports = [cfg["brain_port"], cfg["chat_port"], cfg["embedder"]["port"], cfg["vision"]["port"]]
        self.assertEqual(len(set(ports)), len(ports))

    def test_shipped_identity_is_neutral(self):
        cfg = json.loads((ROOT / "config" / "build_f.json").read_text("utf-8"))
        self.assertEqual(cfg["user_name"], "User")
        self.assertEqual(cfg["city"], "")
        self.assertFalse(cfg["onboarded"])


if __name__ == "__main__":
    unittest.main()
