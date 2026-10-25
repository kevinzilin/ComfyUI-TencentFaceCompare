"""Tencent Cloud TC3 signing using only Python's standard library.

Reference: https://cloud.tencent.com/document/api/213/30654
The caller sends exactly the bytes passed to this signer.
"""

from datetime import datetime, timezone
import hashlib
import hmac

CONTENT_TYPE = "application/json; charset=utf-8"
SIGNED_HEADERS = "content-type;host;x-tc-action"


def canonical_request(body, host, action):
    headers = f"content-type:{CONTENT_TYPE}\nhost:{host.lower()}\nx-tc-action:{action.lower()}\n"
    return f"POST\n/\n\n{headers}\n{SIGNED_HEADERS}\n{hashlib.sha256(body).hexdigest()}"


def authorization(secret_id, secret_key, body, timestamp, host, service, action):
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    scope = f"{date}/{service}/tc3_request"
    digest = hashlib.sha256(canonical_request(body, host, action).encode("utf-8")).hexdigest()
    to_sign = f"TC3-HMAC-SHA256\n{timestamp}\n{scope}\n{digest}"

    def mac(key, value):
        return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()

    date_key = mac(("TC3" + secret_key).encode("utf-8"), date)
    service_key = mac(date_key, service)
    signing_key = mac(service_key, "tc3_request")
    signature = mac(signing_key, to_sign).hex()
    return f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders={SIGNED_HEADERS}, Signature={signature}"
