"""Offline tests. Uses real ComfyUI types, mocks HTTP, never cloud requests."""

import asyncio
import base64
import importlib.util
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN.parents[1]))
spec = importlib.util.spec_from_file_location("tencent_face_compare_test", PLUGIN / "__init__.py")
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)

from tencent_face_compare_test import configuration as cfg, image_processing as images, nodes, tencent_client as client, signing
from PIL import Image
import numpy as np
import torch
import requests

torch.set_num_threads(2)
SETTINGS = cfg.Settings("test-secret-id", "test-secret-key")


def tensor(h=64, w=64, c=3):
    return torch.full((1, h, w, c), 0.5)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.override = patch.object(cfg, "CONFIG_PATH", self.path)
        self.override.start()
        self.addCleanup(self.override.stop)

    def write(self, **overrides):
        data = dict(secret_id="test-secret-id", secret_key="test-secret-key")
        data.update(overrides)
        self.path.write_text(json.dumps(data), encoding="utf-8-sig")

    def test_missing_configuration_does_not_prevent_registration(self):
        extension = asyncio.run(package.comfy_entrypoint())
        self.assertEqual(asyncio.run(extension.get_node_list()), [nodes.TencentFaceCompare])
        self.assertEqual(cfg.config_fingerprint(), "config-unavailable")
        with self.assertRaisesRegex(cfg.FaceCompareError, "config.example.json"):
            cfg.load_settings()

    def test_defaults_and_secret_repr(self):
        self.write()
        settings = cfg.load_settings()
        self.assertEqual(settings.region, "ap-guangzhou")
        self.assertEqual(settings.timeout_seconds, 30)
        self.assertNotIn("test-secret", repr(settings))

    def test_invalid_configuration_messages_do_not_echo_values(self):
        for overrides in ({"secret_key": ""}, {"secret_id": "YOUR_SECRET_ID"},
                          {"region": "test-secret-key"}, {"region": []},
                          {"timeout_seconds": True}, {"timeout_seconds": 0}):
            with self.subTest(overrides=overrides):
                self.write(**overrides)
                with self.assertRaises(cfg.FaceCompareError) as result:
                    cfg.load_settings()
                self.assertNotIn("test-secret", str(result.exception))

    def test_malformed_json_is_redacted(self):
        self.path.write_text('{"secret_key":"sensitive-broken', encoding="utf-8")
        with self.assertRaises(cfg.FaceCompareError) as result:
            cfg.load_settings()
        self.assertNotIn("sensitive-broken", str(result.exception))

    def test_file_changes_invalidate_cache_without_using_secrets(self):
        self.write()
        first = cfg.config_fingerprint()
        self.assertEqual(first, cfg.config_fingerprint())
        self.write(region="ap-shanghai")
        stat = self.path.stat()
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertNotEqual(first, cfg.config_fingerprint())
        self.assertNotIn("secret", str(cfg.config_fingerprint()))


class ImageTests(unittest.TestCase):
    def decode(self, prepared):
        return Image.open(BytesIO(base64.b64decode(prepared.base64_data)))

    def test_rgb_is_jpeg_without_input_mutation(self):
        original = tensor()
        before = original.clone()
        prepared = images.prepare_image(original, "A")
        output = self.decode(prepared)
        self.assertEqual((output.mode, output.format, output.size), ("RGB", "JPEG", (64, 64)))
        self.assertTrue(torch.equal(original, before))
        self.assertNotIn(prepared.base64_data, repr(prepared))

    def test_grayscale_expands_channels(self):
        output = np.array(self.decode(images.prepare_image(tensor(c=1), "A")))
        np.testing.assert_array_equal(output[:, :, 0], output[:, :, 1])
        np.testing.assert_array_equal(output[:, :, 1], output[:, :, 2])

    def test_transparent_rgba_composited_on_white(self):
        rgba = torch.zeros((1, 64, 64, 4))
        output = np.array(self.decode(images.prepare_image(rgba, "A")))
        self.assertTrue((output == 255).all())
        self.assertEqual(rgba.sum().item(), 0)

    def test_long_edge_resized_proportionally(self):
        result = images.prepare_image(tensor(h=128, w=8000), "A")
        self.assertEqual(result.submitted_size, (4000, 64))
        self.assertIn("已等比例缩小", result.describe("A"))

    def test_real_noise_payload_reduced_below_base64_limit(self):
        pixels = np.random.default_rng(123).random((1, 2300, 2300, 3), dtype=np.float32)
        result = images.prepare_image(torch.from_numpy(pixels), "A")
        self.assertLess(result.submitted_size[0], 2300)
        self.assertLessEqual(len(result.base64_data), 5_000_000)
        self.assertGreaterEqual(min(result.submitted_size), 64)

    def test_rejects_batch_empty_small_invalid_channel_and_nonfinite(self):
        cases = [torch.zeros(2, 64, 64, 3), torch.zeros(0, 64, 64, 3),
                 tensor(h=63), tensor(h=0), tensor(c=2), tensor()[0],
                 torch.full((1, 64, 64, 3), float("nan")),
                 torch.full((1, 64, 64, 3), float("inf"))]
        for value in cases:
            with self.subTest(shape=value.shape):
                with self.assertRaises(cfg.FaceCompareError):
                    images.prepare_image(value, "B")

    def test_extreme_aspect_ratio_fails_before_short_edge_below_64(self):
        with self.assertRaisesRegex(cfg.FaceCompareError, "缩小后短边"):
            images.prepare_image(tensor(h=64, w=4500), "A")

    def test_size_limit_cannot_shrink_forever(self):
        with patch.object(images, "MAX_BASE64_BYTES", 1):
            with self.assertRaisesRegex(cfg.FaceCompareError, "缩小后短边"):
                images.prepare_image(tensor(), "A")


class SigningTests(unittest.TestCase):
    # Canonical request and hash published in Tencent's TC3 signing documentation.
    BODY = br'{"Limit": 1, "Filters": [{"Values": ["\u672a\u547d\u540d"], "Name": "instance-name"}]}'

    def test_official_canonical_request_vector(self):
        canonical = signing.canonical_request(self.BODY, "cvm.tencentcloudapi.com", "DescribeInstances")
        self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(),
                         "7019a55be8395899b900fb5564e4200d984910f34794a27cb3fb7d10ff6a1e84")

    def test_signature_matches_independent_official_sdk_vector(self):
        # Fixed public test key; expected HMAC cross-checked once with Tencent's
        # Sign.sign_tc3, then frozen here. This test does not import the SDK.
        auth = signing.authorization("public-test-id", "public-test-key", self.BODY,
                                     1551113065, "cvm.tencentcloudapi.com", "cvm", "DescribeInstances")
        self.assertTrue(auth.endswith("Signature=140ef3c9bc50560e82ab8162947599563f574e803fdc0728ccc7b65cbf169740"))
        # This timestamp is the following calendar day in Asia/Shanghai.
        self.assertIn("/2019-02-25/cvm/tc3_request", auth)

    def test_body_change_changes_signature(self):
        arguments = (1551113065, "iai.tencentcloudapi.com", "iai", "CompareFace")
        a = signing.authorization("id", "key", b'{"ImageA":"a"}', *arguments)
        b = signing.authorization("id", "key", b'{"ImageA": "a"}', *arguments)
        self.assertNotEqual(a, b)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.a = images.PreparedImage("a-payload", (64, 64), (64, 64))
        self.b = images.PreparedImage("b-payload", (64, 64), (64, 64))
        self.payload = {"Score": 68.123456789, "FaceModelVersion": "3.0", "RequestId": "test-request-123"}

    def response(self, payload=None, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps({"Response": self.payload if payload is None else payload}).encode()
        return response

    def call(self):
        return client.compare_faces(SETTINGS, self.a, self.b, 1, 2, True)

    def test_exact_signed_http_body_and_no_retry(self):
        with patch.object(client.requests, "post", return_value=self.response()) as post:
            result = self.call()
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://iai.tencentcloudapi.com/")
        kwargs = post.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertNotIn("json", kwargs)
        self.assertIsInstance(kwargs["data"], bytes)
        self.assertEqual(json.loads(kwargs["data"]), {
            "ImageA": "a-payload", "ImageB": "b-payload", "FaceModelVersion": "3.0",
            "FaceMatchingStrategy": 1, "QualityControl": 2, "NeedRotateDetection": 1,
        })
        headers = kwargs["headers"]
        self.assertEqual(headers["X-TC-Version"], "2020-03-03")
        self.assertEqual(headers["X-TC-Action"], "CompareFace")
        self.assertEqual(headers["X-TC-Region"], "ap-guangzhou")
        self.assertEqual(headers["Authorization"], signing.authorization(
            SETTINGS.secret_id, SETTINGS.secret_key, kwargs["data"], int(headers["X-TC-Timestamp"]),
            "iai.tencentcloudapi.com", "iai", "CompareFace"))
        self.assertEqual(requests.Session().get_adapter("https://").max_retries.total, 0)
        self.assertEqual(result.score, 68.123456789)

    def test_errors_redacted_and_request_id_preserved(self):
        cases = {
            "InvalidParameterValue.NoFaceInPhoto": "未检测到人脸",
            "FailedOperation.FaceQualityNotQualified": "质量不符合",
            "AuthFailure.SecretIdNotFound": "鉴权失败",
            "ResourceUnavailable.NotExist": "开通",
            "ResourceUnavailable.LowBalance": "余额不足",
            "RequestLimitExceeded": "限流",
            "FailedOperation.RequestTimeout": "超时",
            "ClientNetworkError": "网络",
        }
        for code, hint in cases.items():
            payload = {"Error": {"Code": code, "Message": "SECRET_SIGNATURE_AND_IMAGE_PAYLOAD"},
                       "RequestId": "test-request-123"}
            with self.subTest(code=code), patch.object(client.requests, "post", return_value=self.response(payload)) as post:
                try:
                    self.call()
                    self.fail("Expected failure")
                except cfg.FaceCompareError as error:
                    rendered = "".join(traceback.format_exception(error))
                    self.assertIn(hint, rendered)
                    self.assertIn(code, rendered)
                    self.assertIn("test-request-123", rendered)
                    self.assertNotIn("SECRET_SIGNATURE_AND_IMAGE_PAYLOAD", rendered)
                post.assert_called_once()

    def test_unknown_network_error_redacted(self):
        for exception in (requests.Timeout, requests.ConnectionError, RuntimeError):
            with self.subTest(exception=exception), patch.object(client.requests, "post", side_effect=exception("PRIVATE_PAYLOAD")) as post:
                with self.assertRaises(cfg.FaceCompareError) as result:
                    self.call()
                self.assertNotIn("PRIVATE_PAYLOAD", str(result.exception))
                post.assert_called_once()

    def test_invalid_score_is_not_zero(self):
        for value in [None, True, float("nan"), float("inf"), "0.999"]:
            self.payload["Score"] = value
            with self.subTest(value=value), patch.object(client.requests, "post", return_value=self.response()):
                with self.assertRaisesRegex(cfg.FaceCompareError, "分数缺失或无效"):
                    self.call()

    def test_model_mismatch_rejected(self):
        self.payload["FaceModelVersion"] = "2.0"
        with patch.object(client.requests, "post", return_value=self.response()):
            with self.assertRaisesRegex(cfg.FaceCompareError, "非 3.0"):
                self.call()

    def test_bad_json_html_and_redirect_never_return_score(self):
        for body, status in [(b'<html>PRIVATE_PAYLOAD</html>', 502), (b'[]', 200),
                             (b'{"Response":{"Score":70,"FaceModelVersion":"3.0"}}', 302)]:
            response = requests.Response()
            response.status_code = status
            response._content = body
            with self.subTest(status=status), patch.object(client.requests, "post", return_value=response):
                with self.assertRaises(cfg.FaceCompareError) as result:
                    self.call()
                self.assertNotIn("PRIVATE_PAYLOAD", str(result.exception))


class NodeTests(unittest.TestCase):
    def test_schema_registers_and_has_only_public_parameters(self):
        schema = nodes.TencentFaceCompare.GET_SCHEMA()
        self.assertTrue(schema.is_output_node)
        self.assertEqual(schema.node_id, "TencentFaceCompare")
        self.assertEqual([out.id for out in schema.outputs], ["score", "report"])
        self.assertEqual([item.id for item in schema.inputs], [
            "image_a", "image_b", "face_matching_strategy", "quality_control", "rotate_detection", "refresh_id"])

    def test_report_rounding_and_float_precision(self):
        with patch.object(nodes, "load_settings", return_value=SETTINGS), \
             patch.object(nodes, "compare_faces", return_value=client.Comparison(68.123456789, "3.0", "test-request-123")):
            result = nodes.TencentFaceCompare.execute(tensor(), tensor())
        self.assertEqual(result.result[0], 68.123456789)
        self.assertIn("68.12", result.result[1])
        self.assertIn("RequestId：test-request-123", result.result[1])
        self.assertIn("未缩放", result.result[1])
        self.assertEqual(result.ui.as_dict()["text"], (result.result[1],))
        self.assertNotIn("secret", result.result[1])

    def test_batch_failure_never_calls_cloud(self):
        with patch.object(nodes, "load_settings", return_value=SETTINGS), \
             patch.object(nodes, "compare_faces") as compare:
            with self.assertRaisesRegex(cfg.FaceCompareError, "单张输入"):
                nodes.TencentFaceCompare.execute(torch.zeros(2, 64, 64, 3), tensor())
            compare.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
