from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from sentry_nodestore_s3.backend import S3PassthroughDjangoNodeStorage


class CompressionTest(TestCase):
    def setUp(self):
        client = patch("sentry_nodestore_s3.backend.boto3.client")
        self.s3 = self.enterContext(client).return_value

    def round_trip(self, bucket, payload):
        store = S3PassthroughDjangoNodeStorage(bucket_name=bucket)
        store._set_bytes("event", payload)
        written = self.s3.put_object.call_args.kwargs
        response = written.copy()
        response["Body"] = BytesIO(written["Body"])
        self.s3.get_object.return_value = response
        self.assertEqual(store._get_bytes("event"), payload)
        return written

    def test_express_compressed(self):
        payload = b"example event" * 256
        written = self.round_trip("events--usw2-az1--x-s3", payload)
        self.assertLess(len(written["Body"]), len(payload))
        self.assertNotIn("ContentEncoding", written)
        self.assertEqual(written["Metadata"]["sentry-codec"], "zstd")

    def test_express_plain(self):
        written = self.round_trip("events--usw2-az1--x-s3", b"{}")
        self.assertEqual(written["Body"], b"{}")
        self.assertNotIn("sentry-codec", written.get("Metadata", {}))
        self.assertNotIn("ContentEncoding", written)

    def test_standard_content_encoding(self):
        written = self.round_trip("events", b"example event" * 256)
        self.assertEqual(written["ContentEncoding"], "zstd")
        self.assertNotIn("sentry-codec", written.get("Metadata", {}))
