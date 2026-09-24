// 遠端 MCP 端對端測試（用 Node/undici，與 Grok／Gemini CLI 同一類客戶端）。
// 用法：node nodebb-mcp/live_e2e.mjs [access_token]
const BASE = process.env.MCP_URL || "https://forum-mcp.928174.xyz/mcp";
const TOKEN = process.argv[2] || process.env.MCP_TOKEN || "";

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
  (cond ? pass++ : fail++);
  console.log(`${cond ? "✓" : "✗"} ${name}${extra && !cond ? " — " + extra : ""}`);
};

async function rpc(method, params, { token, id } = {}) {
  const headers = { "Content-Type": "application/json", "Accept": "application/json, text/event-stream" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(BASE, {
    method: "POST",
    headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: id ?? Math.floor(Math.random() * 1e6), method, params }),
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* SSE 或多段回應 */ }
  return { status: res.status, json, text };
}

const call = (name, args, opts) => rpc("tools/call", { name, arguments: args }, opts);

// 1. healthz
const hz = await fetch(BASE.replace(/\/mcp$/, "/healthz")).then(r => r.json());
ok("healthz 正常", hz.ok === true && hz.tools === 9, JSON.stringify(hz));

// 2. 握手
const init = await rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "node-e2e", version: "1.0" } });
ok("initialize", init.status === 200 && init.json?.result?.serverInfo?.name === "nodebb-forum", init.text.slice(0, 80));

// 3. 工具清單
const tl = await rpc("tools/list", {});
ok("tools/list 9 個", tl.json?.result?.tools?.length === 9, String(tl.json?.result?.tools?.length));

// 4. 公開工具（不帶存取碼）
const cats = await call("forum_list_categories", {});
ok("讀看板（免存取碼）", cats.json?.result?.isError === false && /cid=1/.test(cats.json?.result?.content?.[0]?.text || ""), cats.text.slice(0, 100));

const recent = await call("forum_recent", { limit: 3 });
ok("讀最新主題（免存取碼）", recent.json?.result?.isError === false, recent.text.slice(0, 100));

// 5. 受保護工具，不帶存取碼 → 應該被拒絕且不寫入
const denied = await call("forum_reply", { tid: 2, content: "should not appear" });
const deniedText = denied.json?.result?.content?.[0]?.text || "";
ok("寫入被擋（沒帶存取碼）", denied.json?.result?.isError === true && /Bearer/.test(deniedText), deniedText.slice(0, 80));

// 6. 帶存取碼 → 真的能用
if (TOKEN) {
  const who = await call("forum_whoami", {}, { token: TOKEN });
  ok("帶存取碼 → 我是誰", who.json?.result?.isError === false && /uid/.test(who.json?.result?.content?.[0]?.text || ""), who.text.slice(0, 120));

  const search = await call("forum_search", { query: "測試", limit: 3 }, { token: TOKEN });
  ok("帶存取碼 → 搜尋", search.json?.result?.isError === false, search.text.slice(0, 120));
} else {
  console.log("（未提供存取碼 → 跳過授權後的測試）");
}

// 7. 錯誤處理
const bad = await rpc("no/such/method", {});
ok("未知方法回 -32601", bad.json?.error?.code === -32601, bad.text.slice(0, 80));

console.log(`\n結果：${pass} 通過 / ${fail} 失敗`);
process.exit(fail ? 1 : 0);
