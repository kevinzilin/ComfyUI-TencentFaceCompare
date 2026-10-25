from comfy_api.latest import ComfyExtension

from .nodes import TencentFaceCompare

WEB_DIRECTORY = "./web"


class TencentFaceCompareExtension(ComfyExtension):
    async def get_node_list(self):
        return [TencentFaceCompare]


async def comfy_entrypoint():
    return TencentFaceCompareExtension()
