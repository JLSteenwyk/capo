import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.providers import Providers, StructuredOutputError
from capo.runtime import Runtime
import test_capo
from test_capo import FakeProviders


class FlakyReviewer(FakeProviders):
    """The reviewer answers in prose instead of JSON a given number of times."""

    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def call(self, provider, prompt, schema, cwd, directory):
        if "You are the reviewer" in prompt and self.failures:
            self.failures -= 1
            self.calls.append((provider, prompt))
            raise StructuredOutputError("reviewer did not return the requested JSON")
        return super().call(provider, prompt, schema, cwd, directory)


class RuntimeRetryTests(unittest.TestCase):
    # Reuse the repository fixture without re-collecting test_capo's own tests.
    setUp = test_capo.RepositoryCase.setUp
    tearDown = test_capo.RepositoryCase.tearDown
    objective = test_capo.RepositoryCase.objective

    def test_one_malformed_reply_is_retried_within_the_call_budget(self):
        objective = self.objective()
        fake = FlakyReviewer(failures=1)
        result = Runtime(self.store, fake).run(objective["id"])
        self.assertEqual(result["status"], "completed")
        reviews = [prompt for provider, prompt in fake.calls if "You are the reviewer" in prompt]
        self.assertEqual(len(reviews), 2)
        self.assertIn("format_retry", reviews[1])
        self.assertEqual(result["calls"], 5)  # planner, implementer, reviewer x2, acceptance

    def test_repeated_malformed_replies_still_stop_the_objective(self):
        objective = self.objective()
        with self.assertRaises(StructuredOutputError):
            Runtime(self.store, FlakyReviewer(failures=2)).run(objective["id"])
        self.assertNotEqual(self.store.get(objective["id"])["status"], "completed")

    def test_retry_never_exceeds_the_worker_call_budget(self):
        objective = self.objective(max_calls=3)
        with self.assertRaisesRegex(ValueError, "budget exhausted"):
            Runtime(self.store, FlakyReviewer(failures=1)).run(objective["id"])
        self.assertEqual(self.store.get(objective["id"])["calls"], 3)


class GrokEnvelopeTests(unittest.TestCase):
    def test_missing_structured_output_is_a_distinct_retryable_error(self):
        envelope = {"structuredOutput": None, "structuredOutputError": "model did not produce structured output",
                    "stopReason": "end_turn", "result": "A prose review instead of JSON."}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('capo.providers.run_cli', return_value=json.dumps(envelope)):
            with self.assertRaises(StructuredOutputError):
                Providers(timeout=5).call('grok', 'Review.', {'type': 'object'}, Path(tmp), Path(tmp) / 'out')


if __name__ == '__main__':
    unittest.main()
