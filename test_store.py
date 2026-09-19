import unittest
from unittest.mock import patch

import httpx

from plan_store import BlobStore, StoreConflict, StoreUnavailable


class BlobContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "test-not-a-real-token", "BLOB_STORE_ID": "store_test12345"})
        self.environment.start()
        self.store = BlobStore()

    def tearDown(self):
        self.environment.stop()

    async def test_read_bypasses_cache_and_compression(self):
        def handler(request):
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            self.assertEqual(request.url.params["cache"], "0")
            return httpx.Response(200, json={"plans": []}, headers={"etag": '"version1"'})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("plan_store.httpx.AsyncClient", return_value=client):
            self.assertEqual(await self.store.read("account-test"), ({"plans": []}, '"version1"'))

    async def test_weak_etag_never_used_for_conditional_write(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}, headers={"etag": 'W/"weak"'})))
        with patch("plan_store.httpx.AsyncClient", return_value=client), self.assertRaises(StoreUnavailable):
            await self.store.read("account-test")

    async def test_pagination_includes_every_account(self):
        def handler(request):
            if request.url.params.get("cursor") == "next":
                return httpx.Response(200, json={"blobs": [{"pathname": "excify-eur-huf-ai-v2/development/account-b.json"}], "hasMore": False})
            return httpx.Response(200, json={"blobs": [{"pathname": "excify-eur-huf-ai-v2/development/account-a.json"}], "hasMore": True, "cursor": "next"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("plan_store.httpx.AsyncClient", return_value=client):
            self.assertEqual(await self.store.keys(), ["account-a", "account-b"])

    async def test_conditional_write_conflict_is_not_overwritten(self):
        def handler(request):
            self.assertEqual(request.headers["x-if-match"], '"old-version"')
            self.assertEqual(request.headers["x-allow-overwrite"], "1")
            return httpx.Response(412)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("plan_store.httpx.AsyncClient", return_value=client), self.assertRaises(StoreConflict):
            await self.store.write("account-test", {"plans": []}, '"old-version"')


if __name__ == "__main__":
    unittest.main()
