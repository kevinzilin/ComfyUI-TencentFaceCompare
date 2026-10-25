from comfy_api.latest import io, ui

from .configuration import FaceCompareError, config_fingerprint, load_settings
from .image_processing import prepare_image
from .tencent_client import compare_faces

STRATEGIES = {"置信度最高": 0, "面积最大": 1}


class TencentFaceCompare(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="TencentFaceCompare",
            display_name="腾讯云人脸相似度",
            category="腾讯云/人脸",
            description="将两张图片发送至腾讯云进行人脸相似度比对，调用按账户服务规则计费。分数不是百分比。",
            search_aliases=["人脸比对", "人脸相似度", "Tencent Face Compare"],
            inputs=[
                io.Image.Input("image_a", display_name="图片 A"),
                io.Image.Input("image_b", display_name="图片 B"),
                io.Combo.Input("face_matching_strategy", display_name="人脸选择策略",
                               options=list(STRATEGIES), default="置信度最高"),
                io.Int.Input("quality_control", display_name="图片质量要求", default=0, min=0, max=4,
                             tooltip="0 不控制；1 较低；2 一般；3 较高；4 很高。"),
                io.Boolean.Input("rotate_detection", display_name="旋转检测", default=False),
                io.Int.Input("refresh_id", display_name="重新请求编号", default=0, min=0, max=2147483647,
                             tooltip="修改此数值可重新调用腾讯云；相同输入保留正常缓存。"),
            ],
            outputs=[io.Float.Output("score", display_name="分数"),
                     io.String.Output("report", display_name="比对报告")],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return config_fingerprint()

    @classmethod
    def execute(cls, image_a, image_b, face_matching_strategy="置信度最高",
                quality_control=0, rotate_detection=False, refresh_id=0):
        if face_matching_strategy not in STRATEGIES:
            raise FaceCompareError("请选择有效的人脸选择策略。")
        if type(quality_control) is not int or not 0 <= quality_control <= 4:
            raise FaceCompareError("图片质量要求必须为 0 至 4。")
        if type(rotate_detection) is not bool:
            raise FaceCompareError("旋转检测必须为开或关。")
        settings = load_settings()
        a = prepare_image(image_a, "A")
        b = prepare_image(image_b, "B")
        result = compare_faces(settings, a, b, STRATEGIES[face_matching_strategy], quality_control, rotate_detection)
        report = "\n".join([
            f"人脸相似度分数：{result.score:.2f}",
            "腾讯云原始分数，非相似百分比；本节点不作身份判定。",
            f"算法版本：{result.model_version}",
            f"人脸选择策略：{face_matching_strategy}",
            f"图片质量要求：{quality_control}；旋转检测：{'开启' if rotate_detection else '关闭'}",
            a.describe("A"), b.describe("B"), f"RequestId：{result.request_id}",
        ])
        return io.NodeOutput(result.score, report, ui=ui.PreviewText(report))
