// Exercise the actual extension with minimal browser/ComfyUI doubles; no cloud calls.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../web/face_compare.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "");
let extension;
const listeners = {};
const widgets = [];
class Node {
    constructor() { this.id = 7; this.size = [200, 200]; }
    addDOMWidget(name, type, element, options) { widgets.push({ name, type, element, options }); }
    setSize(size) { this.size = size; }
}
const node = new Node();
const app = {
    registerExtension(value) { extension = value; },
    graph: { getNodeById(id) { return String(id) === "7" ? node : null; } },
};
const api = { addEventListener(name, listener) { listeners[name] = listener; } };
vm.runInNewContext(source, { app, api, document: {
    createElement() { return { style: {}, setAttribute() {} }; },
} });
await extension.beforeRegisterNodeDef(Node, { name: "TencentFaceCompare" });
node.onNodeCreated();
assert.equal(widgets.length, 1);
assert.equal(widgets[0].options.serialize, false);
assert.equal(widgets[0].element.readOnly, true);
node.onExecuted({ text: ["人脸相似度分数：68.12"] });
assert.match(widgets[0].element.value, /68.12/);
listeners.executing({ detail: "7" });
assert.doesNotMatch(widgets[0].element.value, /68.12/);
listeners.execution_error({ detail: { node_id: "7" } });
assert.match(widgets[0].element.value, /没有有效分数/);
node.onExecuted({ text: ["人脸相似度分数：80.13"] });
listeners.execution_interrupted({ detail: { node_id: "7" } });
assert.doesNotMatch(widgets[0].element.value, /80.13/);
listeners.executing({ detail: null });
console.log("Frontend checks passed: display, nonserialized report, start/error/interruption clear stale scores.");
