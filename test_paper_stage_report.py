import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch
from paper_stage_report import instrument, execute, error_code


class StageTests(unittest.TestCase):
    def test_preserves_result(self):
        trace = {"calls": {}}
        self.assertEqual(instrument(lambda x: x+1, "size", trace)(2), 3)
        self.assertEqual(trace["calls"], {"size": 1})

    def test_innermost_failure(self):
        trace = {"calls": {}}
        def fail(): raise RuntimeError("Universe unavailable")
        inner = instrument(fail, "universe", trace)
        with self.assertRaises(RuntimeError): instrument(inner, "scan", trace)()
        self.assertEqual(trace["failed_stage"], "universe")
        self.assertEqual(error_code(RuntimeError("Universe unavailable")), "UNIVERSE_SOURCE_UNAVAILABLE")

    def test_execution_restores_functions_and_does_not_enable_writes(self):
        class API:
            def __init__(self, enabled): self.enabled = enabled
            def call(self, *args, **kwargs): return {}
        def universe(): raise RuntimeError("Insufficient verified assets")
        fake = SimpleNamespace(API=API, universe=universe)
        for name in ("account", "snapshot", "manage", "scan", "size"):
            setattr(fake, name, lambda: None)
        def run(api, mode, expected):
            self.assertFalse(api.enabled)
            api.call("GET", "/v2/assets")
            fake.universe()
        fake.run = run
        with patch.dict(sys.modules, {"paper_execution": fake}):
            result = execute("scan", False, "expected")
        self.assertEqual(result["error_code"], "INSUFFICIENT_VERIFIED_ASSETS")
        self.assertEqual(result["diagnostics"]["failed_stage"], "universe")
        self.assertIs(fake.universe, universe)


if __name__ == "__main__": unittest.main()
