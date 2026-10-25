"""Isolated CPU ComfyUI with a copied plugin and fake HTTP; NEVER calls Tencent.

Run with the portable Python. Use http://127.0.0.1:8199 and load the fixture
workflow from the Workflows sidebar. Quality control 4 simulates no-face failure.
Ctrl+C stops this test instance and removes its temporary configuration.
"""

import json
from pathlib import Path
import runpy
import shutil
import sys
import tempfile

from PIL import Image
import requests

PLUGIN = Path(__file__).resolve().parents[1]
COMFY_ROOT = PLUGIN.parents[1]


ORIGINAL_REQUEST = requests.Session.request
MOCK_CALLS = 0


def fake_request(self, method, url, **kwargs):
    global MOCK_CALLS
    if url != "https://iai.tencentcloudapi.com/":
        return ORIGINAL_REQUEST(self, method, url, **kwargs)
    MOCK_CALLS += 1
    print(f"OFFLINE MOCK CompareFace invocation #{MOCK_CALLS}", flush=True)
    request = json.loads(kwargs["data"])
    if request["QualityControl"] == 4:
        payload = {"Error": {"Code": "InvalidParameterValue.NoFaceInPhoto", "Message": "fixture"},
                   "RequestId": "MOCK-UI-FAILURE"}
    else:
        payload = {"Score": 68.123456789, "FaceModelVersion": "3.0",
                   "RequestId": "MOCK-UI-TEST-NOT-A-CLOUD-RESULT"}
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"Response": payload}).encode()
    return response


if __name__ == "__main__":
    requests.Session.request = fake_request
    with tempfile.TemporaryDirectory(prefix="tencent-face-ui-") as temporary:
        root = Path(temporary)
        copied = root / "custom_nodes" / PLUGIN.name
        shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("config.json", "__pycache__", "dist"))
        (copied / "config.json").write_text(json.dumps({
            "secret_id": "OFFLINE_TEST_ID", "secret_key": "OFFLINE_TEST_KEY"
        }), encoding="utf-8")
        workflows = root / "user" / "default" / "workflows"
        workflows.mkdir(parents=True)
        (root / "input").mkdir()
        for name, color in (("fixture_a.png", "steelblue"), ("fixture_b.png", "tan")):
            Image.new("RGB", (256, 256), color).save(root / "input" / name)
        workflow = json.loads((PLUGIN / "examples" / "face_compare.json").read_text(encoding="utf-8"))
        workflow["nodes"][0]["widgets_values"][0] = "fixture_a.png"
        workflow["nodes"][1]["widgets_values"][0] = "fixture_b.png"
        workflow["nodes"][2]["title"] = "离线界面测试（模拟分数）"
        (workflows / "face-compare-offline-test.json").write_text(json.dumps(workflow), encoding="utf-8")
        sys.path.insert(0, str(COMFY_ROOT))
        sys.argv = [str(COMFY_ROOT / "main.py"), "--cpu", "--listen", "127.0.0.1", "--port", "8199",
                    "--base-directory", str(root), "--user-directory", str(root / "user"),
                    "--temp-directory", str(root / "temp"), "--database-url", "sqlite:///:memory:",
                    "--disable-assets", "--disable-auto-launch", "--offline", "--disable-all-custom-nodes",
                    "--whitelist-custom-nodes", PLUGIN.name]
        print("OFFLINE UI FIXTURE: port 8199; all CompareFace calls mocked; quality 4 simulates error.", flush=True)
        runpy.run_path(str(COMFY_ROOT / "main.py"), run_name="__main__")
