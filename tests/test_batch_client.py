"""Offline unit tests for BatchSemanticScholarClient."""
import json
import unittest
from arxiv_pipeline.discovery.api import ApiResponse, RemoteError
from arxiv_pipeline.references.batch_client import BatchSemanticScholarClient, adapt_batch_reference


class BatchClientTests(unittest.TestCase):
    def test_adapt_batch_reference(self):
        ref = {
            "paperId": "s2_123",
            "title": "A Great Paper",
            "authors": [{"name": "Alice"}],
            "externalIds": {"ArXiv": "2001.00001"}
        }
        adapted = adapt_batch_reference(ref)
        self.assertIn("citedPaper", adapted)
        self.assertEqual(adapted["citedPaper"]["paperId"], "s2_123")
        self.assertEqual(adapted["citedPaper"]["externalIds"]["ArXiv"], "2001.00001")

    def test_post_batch_success(self):
        calls = []
        def fake_transport(url, timeout, headers, body=None):
            calls.append((url, headers, body))
            payload = [
                {
                    "paperId": "p1",
                    "title": "Paper 1",
                    "externalIds": {"ArXiv": "2504.17523"},
                    "referenceCount": 1,
                    "references": [
                        {
                            "paperId": "ref1",
                            "title": "Ref 1",
                            "authors": [{"name": "Bob"}],
                            "externalIds": {"ArXiv": "2001.00001"}
                        }
                    ]
                },
                None  # second paper not found
            ]
            return ApiResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8"))

        client = BatchSemanticScholarClient(api_key="synthetic-key", delay=0.0, transport=fake_transport)
        results = client.fetch_batch(["2504.17523", "2504.17524"])
        self.assertEqual(len(results), 2)
        self.assertIsNotNone(results[0])
        self.assertIsNone(results[1])
        self.assertEqual(results[0]["externalIds"]["ArXiv"], "2504.17523")
        self.assertEqual(len(results[0]["references"]), 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("x-api-key", calls[0][1])
        self.assertEqual(calls[0][1]["x-api-key"], "synthetic-key")

    def test_adaptive_batch_split_on_oversize_or_failure(self):
        attempts = []
        def fake_transport(url, timeout, headers, body=None):
            req_ids = json.loads(body)["ids"]
            attempts.append(len(req_ids))
            if len(req_ids) > 2:
                # Simulate payload too large or 400
                return ApiResponse(400, {}, b'{"error": "Too many papers"}')
            # Succeed for smaller batches <= 2
            data = [{"paperId": f"p_{i}", "externalIds": {"ArXiv": aid.replace("ARXIV:", "")}, "references": []}
                    for i, aid in enumerate(req_ids)]
            return ApiResponse(200, {}, json.dumps(data).encode("utf-8"))

        client = BatchSemanticScholarClient(api_key="test", delay=0.0, transport=fake_transport, sleep=lambda _: None)
        results = client.fetch_batch_adaptive(["2504.17523", "2504.17524", "2504.17525", "2504.17526"])
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r is not None for r in results))
        self.assertIn(4, attempts)
        self.assertIn(2, attempts)


if __name__ == "__main__":
    unittest.main()
