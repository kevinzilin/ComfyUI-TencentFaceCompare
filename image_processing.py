"""Prepare JPEG payloads in memory without mutating incoming IMAGE tensors."""

import base64
from dataclasses import dataclass, field
from io import BytesIO

import numpy as np
from PIL import Image
import torch

from .configuration import FaceCompareError

MAX_BASE64_BYTES = 5_000_000
MAX_LONG_EDGE = 4000
MIN_SHORT_EDGE = 64


@dataclass(frozen=True)
class PreparedImage:
    base64_data: str = field(repr=False)
    original_size: tuple[int, int]
    submitted_size: tuple[int, int]

    def describe(self, label):
        ow, oh = self.original_size
        sw, sh = self.submitted_size
        change = "已等比例缩小" if self.original_size != self.submitted_size else "未缩放"
        return f"图片 {label}：{ow}×{oh} → {sw}×{sh}（{change}）"


def _jpeg_base64(image):
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _resize(image, factor, label):
    size = (int(image.width * factor), int(image.height * factor))
    if min(size) < MIN_SHORT_EDGE:
        raise FaceCompareError(f"图片 {label} 缩小后短边将小于 64 像素，请提供宽高比更合适的图片。")
    return image.resize(size, Image.Resampling.LANCZOS)


def prepare_image(tensor, label):
    if not isinstance(tensor, torch.Tensor) or tensor.ndim != 4:
        raise FaceCompareError(f"图片 {label} 必须是 [1, 高, 宽, 通道] 格式的 ComfyUI IMAGE。")
    batch, height, width, channels = tensor.shape
    if batch != 1:
        raise FaceCompareError(f"图片 {label} 仅支持单张输入，当前批次为 {batch}；请先提取一张图片。")
    if channels not in (1, 3, 4):
        raise FaceCompareError(f"图片 {label} 只支持灰度、RGB 或 RGBA 通道。")
    if min(height, width) < MIN_SHORT_EDGE:
        raise FaceCompareError(f"图片 {label} 为空或短边不足 64 像素，请使用更大的图片。")
    pixels = tensor[0].detach().to(device="cpu", dtype=torch.float32).numpy()
    if not np.isfinite(pixels).all():
        raise FaceCompareError(f"图片 {label} 含 NaN 或无穷数值，请检查上游图像节点。")
    pixels = np.rint(np.clip(pixels, 0.0, 1.0) * 255.0).astype(np.uint8)
    if channels == 1:
        image = Image.fromarray(pixels[:, :, 0]).convert("RGB")
    elif channels == 4:
        rgba = Image.fromarray(pixels)
        image = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(image, rgba).convert("RGB")
    else:
        image = Image.fromarray(pixels)
    if max(image.size) > MAX_LONG_EDGE:
        image = _resize(image, MAX_LONG_EDGE / max(image.size), label)
    encoded = _jpeg_base64(image)
    while len(encoded) > MAX_BASE64_BYTES:
        image = _resize(image, 0.85, label)
        encoded = _jpeg_base64(image)
    return PreparedImage(encoded, (width, height), image.size)
