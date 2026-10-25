"""Direct HTTPS API boundary; raw HTTP errors must never reach ComfyUI logs."""

from dataclasses import dataclass
import json
import math
import re
import time

import requests

from .configuration import FaceCompareError
from .signing import CONTENT_TYPE, authorization

HOST = "iai.tencentcloudapi.com"


@dataclass(frozen=True)
class Comparison:
    score: float
    model_version: str
    request_id: str


ERROR_HINTS = {
    "InvalidParameterValue.NoFaceInPhoto": "图片中未检测到人脸，请使用清晰、完整的人脸图片。",
    "FailedOperation.ImageFacedetectFailed": "人脸检测失败，请检查人脸清晰度、朝向及遮挡。",
    "FailedOperation.FaceQualityNotQualified": "人脸图片质量不符合要求，请更换清晰图片或降低图片质量要求。",
    "FailedOperation.FaceSizeTooSmall": "人脸过小，请先裁剪并保留完整人脸。",
    "FailedOperation.ImageDecodeFailed": "图片解码失败，请重新加载图片后再试。",
    "FailedOperation.ImageResolutionExceed": "提交图片尺寸超限，请缩小图片。",
    "FailedOperation.ImageResolutionTooSmall": "提交图片短边不足 64 像素，请更换图片。",
    "FailedOperation.ImageSizeExceed": "提交图片编码大小超限，请缩小图片。",
    "FailedOperation.RequestTimeout": "腾讯云处理超时，请稍后修改重新请求编号再试。",
    "ResourceUnavailable.NotExist": "请先在腾讯云控制台开通人脸识别服务。",
    "ResourceUnavailable.LowBalance": "腾讯云账户余额不足，请在控制台检查余额。",
    "ResourceUnavailable.InArrears": "腾讯云账户已欠费，请在控制台处理。",
    "ResourceUnavailable.StopUsing": "腾讯云服务已停用，请检查控制台服务状态。",
    "ResourceUnavailable.Freeze": "腾讯云账户已冻结，请检查账户状态。",
    "ClientNetworkError": "网络连接失败或请求超时，请检查网络，稍后修改重新请求编号再试。",
}


def safe_error(code, request_id):
    # Tencent error codes are machine identifiers; do not forward Message/body/URL.
    code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9.]{0,95}", code) else "UnknownError"
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9-]{1,96}", request_id) else "不可用"
    hint = ERROR_HINTS.get(code)
    if hint is None:
        if code.startswith(("AuthFailure", "UnauthorizedOperation")):
            hint = "腾讯云鉴权失败，请检查本地密钥、账户权限及系统时间。"
        elif code.startswith(("RequestLimitExceeded", "LimitExceeded")):
            hint = "腾讯云请求被限流或超过限额，请检查配额并稍后重试。"
        elif code.startswith("ResourceUnavailable"):
            hint = "腾讯云服务当前不可用，请检查服务开通和账户状态。"
        elif code.startswith(("InvalidParameter", "MissingParameter")):
            hint = "腾讯云拒绝了请求参数，请检查节点参数与配置。"
        else:
            hint = "腾讯云请求失败，请凭错误码及 RequestId 排查后手动重试。"
    return f"{hint}\n错误码：{code}\nRequestId：{request_id}"


def compare_faces(settings, image_a, image_b, strategy, quality_control, rotate_detection):
    body = json.dumps({
        "ImageA": image_a.base64_data,
        "ImageB": image_b.base64_data,
        "FaceModelVersion": "3.0",
        "FaceMatchingStrategy": strategy,
        "QualityControl": quality_control,
        "NeedRotateDetection": int(rotate_detection),
    }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": CONTENT_TYPE,
        "Host": HOST,
        "X-TC-Action": "CompareFace",
        "X-TC-Version": "2020-03-03",
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": settings.region,
        "Authorization": authorization(settings.secret_id, settings.secret_key, body,
                                       timestamp, HOST, "iai", "CompareFace"),
    }
    try:
        # requests.post creates a fresh Session with zero retries. Never redirect
        # an authenticated request, and never use json= (which would reserialize).
        response = requests.post(f"https://{HOST}/", data=body, headers=headers,
                                 timeout=settings.timeout_seconds, allow_redirects=False)
    except requests.exceptions.Timeout:
        raise FaceCompareError("腾讯云请求超时，请稍后修改重新请求编号再试；本次没有有效分数。") from None
    except requests.exceptions.RequestException:
        raise FaceCompareError(safe_error("ClientNetworkError", None)) from None
    except Exception:
        raise FaceCompareError("腾讯云连接失败，请检查网络与本地配置后手动重试。") from None

    try:
        status = response.status_code
        try:
            data = response.json()
        except ValueError:
            raise FaceCompareError(f"腾讯云响应不是有效 JSON（HTTP {status}），请检查网络后重试。") from None
    finally:
        response.close()
    payload = data.get("Response") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        raise FaceCompareError(f"腾讯云响应结构无效（HTTP {status}），本次没有有效结果。")
    error = payload.get("Error")
    if isinstance(error, dict):
        raise FaceCompareError(safe_error(error.get("Code"), payload.get("RequestId")))
    if not 200 <= status < 300:
        raise FaceCompareError(f"腾讯云 HTTP 请求失败（HTTP {status}），请检查网络或服务状态后重试。")
    score = payload.get("Score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise FaceCompareError("腾讯云返回的分数缺失或无效，本次比对没有有效结果。")
    if payload.get("FaceModelVersion") != "3.0":
        raise FaceCompareError("腾讯云返回了非 3.0 算法结果，本次比对未采用该分数。")
    request_id = payload.get("RequestId")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,96}", request_id):
        request_id = "不可用"
    return Comparison(float(score), payload["FaceModelVersion"], request_id)
