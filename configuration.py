"""Local-only credentials. Never include file contents in errors or repr."""

from dataclasses import dataclass, field
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.json")
REGIONS = frozenset({
    "ap-bangkok", "ap-beijing", "ap-chengdu", "ap-chongqing",
    "ap-guangzhou", "ap-nanjing", "ap-shanghai", "ap-singapore",
})


class FaceCompareError(RuntimeError):
    """Only safe, user-facing messages may be put in this exception."""


@dataclass(frozen=True)
class Settings:
    secret_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    region: str = "ap-guangzhou"
    timeout_seconds: int = 30


def config_fingerprint():
    # Only file metadata enters ComfyUI's cache, never credentials or their hash.
    try:
        stat = CONFIG_PATH.stat()
        return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except OSError:
        return "config-unavailable"


def load_settings():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise FaceCompareError(
            "未找到配置：请在 ComfyUI-TencentFaceCompare 目录将 "
            "config.example.json 复制为 config.json，并填写腾讯云密钥。"
        ) from None
    except (OSError, UnicodeError, ValueError):
        raise FaceCompareError("无法读取 config.json：请检查文件权限及 UTF-8 JSON 格式。") from None
    if not isinstance(data, dict):
        raise FaceCompareError("config.json 必须是 JSON 对象。")
    for key in ("secret_id", "secret_key"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or value.strip().startswith("YOUR_"):
            raise FaceCompareError(f"请在本地 config.json 填写有效的 {key}。")
    region = data.get("region", "ap-guangzhou")
    if not isinstance(region, str) or region not in REGIONS:
        raise FaceCompareError("config.json 的 region 不受支持，请参考配置说明。")
    timeout = data.get("timeout_seconds", 30)
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise FaceCompareError("timeout_seconds 必须是 1 至 300 的整数秒数。")
    return Settings(data["secret_id"].strip(), data["secret_key"].strip(), region, timeout)
