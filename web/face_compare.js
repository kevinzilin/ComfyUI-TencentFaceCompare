import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_ID = "TencentFaceCompare";
const reports = new WeakMap();

function show(node, text) {
    const element = reports.get(node);
    if (element) element.value = text;
}

function eventNode(id) {
    return id == null ? null : app.graph?.getNodeById(id);
}

api.addEventListener("executing", ({ detail }) => {
    show(eventNode(detail), "正在比对，请稍候…");
});
api.addEventListener("execution_error", ({ detail }) => {
    show(eventNode(detail?.node_id), "本次比对失败，请查看节点错误详情。\n本次没有有效分数。");
});
api.addEventListener("execution_interrupted", ({ detail }) => {
    show(eventNode(detail?.node_id), "本次执行已中断，没有新的有效分数。");
});

app.registerExtension({
    name: "TencentFaceCompare.Report",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_ID) return;
        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onCreated?.apply(this, arguments);
            const text = document.createElement("textarea");
            text.readOnly = true;
            text.setAttribute("aria-label", "人脸比对报告");
            text.value = "连接两张图片后运行。图片会发送至腾讯云，调用按账户服务规则计费。";
            text.style.cssText = "width:100%;height:100%;box-sizing:border-box;resize:none;padding:10px;" +
                "background:var(--comfy-input-bg);color:var(--input-text);border:1px solid var(--border-color);" +
                "font:13px/1.6 sans-serif;";
            this.addDOMWidget("face_compare_report", "customtext", text, { serialize: false });
            reports.set(this, text);
            this.setSize([Math.max(this.size[0], 410), Math.max(this.size[1], 430)]);
            return result;
        };
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = onExecuted?.apply(this, arguments);
            show(this, Array.isArray(message?.text) ? message.text.join("\n") : "本次没有有效结果。");
            return result;
        };
    },
});
