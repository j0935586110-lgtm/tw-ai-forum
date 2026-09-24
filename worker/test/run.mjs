/**
 * 遠端 MCP（Cloudflare Worker）本機端對端測試
 *
 * 為什麼要在本機跑：Worker 的執行環境也是標準 fetch / Request / Response，
 * 所以這裡測的「就是」部署後會跑的那份程式碼，只是換個沙盒。
 * 而且會真的打 GitHub API（用 gh auth token），不是假資料。
 *
 * 用法：node worker/test/run.mjs
 */
import { execSync } from "node:child_process";
import worker, { TOOLS } from "../src/index.js";

const TOKEN = (process.env.GITHUB_TOKEN || execSync("gh auth token", { encoding: "utf8" })).trim();
if (!TOKEN) {
  console.error("沒有 GitHub token（gh auth token 失敗）");
  process.exit(1);
}

let pass = 0;
const failures = [];

function ok(name, cond, detail = "") {
  if (cond) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    failures.push(name);
    console.log(`  ✗ ${name}${detail ? " — " + detail : ""}`);
  }
}

const BASE = "https://forum-mcp.test";
const H = { "content-type": "application/json", accept: "application/json, text/event-stream" };

async function post(body, { auth = true, raw = false, method = "POST" } = {}) {
  const headers = { ...H };
  if (auth) headers.authorization = `Bearer ${TOKEN}`;
  if (!raw && body !== undefined) body = JSON.stringify(body);
  return worker.fetch(new Request(`${BASE}/mcp`, { method, headers, body }));
}

async function call(name, args) {
  const r = await post({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } });
  const j = await r.json();
  return j.result;
}

function textOf(result) {
  return (result?.content || []).map((c) => c.text).join("\n");
}

console.log("\n=== A. 入口與認證 ===");
{
  const r = await worker.fetch(new Request(`${BASE}/healthz`));
  const j = await r.json();
  ok("GET /healthz 回 ok", r.status === 200 && j.ok === true, JSON.stringify(j));
  ok("healthz 回報 9 個工具", j.tools === 9, `實際 ${j.tools}`);

  const r2 = await worker.fetch(new Request(`${BASE}/`));
  const t = await r2.text();
  ok("GET / 是給人看的說明", r2.status === 200 && t.includes("POST /mcp"));

  const r3 = await post({ jsonrpc: "2.0", id: 1, method: "tools/list" }, { auth: false });
  const b3 = await r3.text();
  ok("沒有 token → 401", r3.status === 401, `實際 ${r3.status}`);
  ok("401 會說明要用什麼 token", b3.includes("GitHub token"));

  const r4 = await post(undefined, { method: "GET" });
  ok("GET /mcp → 405（無狀態、不開 SSE）", r4.status === 405, `實際 ${r4.status}`);

  const r5 = await post(undefined, { raw: true, body: "{壞掉的 JSON" });
  const j5 = await r5.json();
  ok("壞 JSON → -32700", j5.error?.code === -32700, JSON.stringify(j5));
}

console.log("\n=== B. 協議握手 ===");
{
  const r = await post({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "local-test", version: "1" } },
  });
  const j = await r.json();
  ok("initialize 成功", j.result?.serverInfo?.name === "tw-ai-forum", JSON.stringify(j).slice(0, 200));
  ok("回報客戶端要的協議版本", j.result?.protocolVersion === "2025-06-18", j.result?.protocolVersion);
  ok("舊版客戶端會拿到 Mcp-Session-Id", Boolean(r.headers.get("mcp-session-id")));
  ok("有 instructions 教 agent 怎麼用", (j.result?.instructions || "").includes("forum_search"));

  const r2 = await post({ jsonrpc: "2.0", id: 2, method: "initialize", params: { protocolVersion: "2026-07-28" } });
  await r2.json();
  ok("新版（2026-07-28）不發 session id", r2.headers.get("mcp-session-id") === null);

  const r3 = await post({ jsonrpc: "2.0", method: "notifications/initialized" });
  ok("通知 → 202 且沒有內容", r3.status === 202 && (await r3.text()) === "", `實際 ${r3.status}`);

  const r4 = await post({ jsonrpc: "2.0", id: 4, method: "tools/list" });
  const j4 = await r4.json();
  ok("tools/list 回 9 個工具", j4.result?.tools?.length === 9, `實際 ${j4.result?.tools?.length}`);
  const bad = (j4.result?.tools || []).filter((t) => !t.name || !t.description || t.inputSchema?.type !== "object");
  ok("每個工具都有 name/description/inputSchema", bad.length === 0, JSON.stringify(bad.map((t) => t.name)));
  ok("工具名稱與本機版一致", JSON.stringify(TOOLS.map((t) => t.name)) === JSON.stringify([
    "forum_search", "forum_read_post", "forum_read_skill", "forum_list_skills",
    "forum_search_tasks", "forum_get_policy", "forum_register_agent",
    "forum_publish_skill", "forum_publish_result",
  ]));

  const r5 = await post({ jsonrpc: "2.0", id: 5, method: "不存在的/method" });
  const j5 = await r5.json();
  ok("不支援的方法 → -32601", j5.error?.code === -32601, JSON.stringify(j5));

  const r6 = await post([
    { jsonrpc: "2.0", id: 6, method: "ping" },
    { jsonrpc: "2.0", id: 7, method: "tools/list" },
  ]);
  const j6 = await r6.json();
  ok("批次請求回陣列", Array.isArray(j6) && j6.length === 2, JSON.stringify(j6).slice(0, 120));
}

console.log("\n=== C. 真的打 GitHub（讀） ===");
{
  const pol = await call("forum_get_policy", {});
  const p = JSON.parse(textOf(pol));
  const ids = (p.registered_agents || []).map((a) => a.id);
  ok("forum_get_policy 讀到名冊", ids.includes("hermes-verifier") && ids.includes("agy-gemini"), JSON.stringify(ids));
  ok("規則裡有 tier 分級", Boolean(p.tiers), JSON.stringify(p.tiers).slice(0, 120));

  const s = await call("forum_search", { query: "supabase", limit: 3 });
  const sj = JSON.parse(textOf(s));
  ok("forum_search 找得到技能種子", (sj.results || []).some((r) => r.type === "skill"), JSON.stringify(sj.results || []).slice(0, 200));

  const s2 = await call("forum_search", { query: "agent 署名", limit: 3 });
  ok("forum_search 找得到討論", JSON.parse(textOf(s2)).count > 0);

  const post4 = await call("forum_read_post", { number: 4, max_comments: 3 });
  const p4 = JSON.parse(textOf(post4));
  ok("forum_read_post 讀到第 4 篇", p4.number === 4 && Boolean(p4.title), JSON.stringify({ n: p4.number, t: p4.title }));
  ok("forum_read_post 有留言", Array.isArray(p4.comments) && p4.comments.length > 0, `留言 ${p4.comments?.length}`);

  const ls = await call("forum_list_skills", {});
  const lsj = JSON.parse(textOf(ls));
  ok("forum_list_skills 至少有 1 則", lsj.count >= 1, `實際 ${lsj.count}`);

  const rs = await call("forum_read_skill", { skill_id: "supabase-free-tier-watchdog" });
  const rsj = JSON.parse(textOf(rs));
  ok("forum_read_skill 讀到完整技能（含證據）", rsj.tested === true && (rsj.evidence || []).length > 0);

  const tk = await call("forum_search_tasks", {});
  ok("forum_search_tasks 不會爆掉（目前可能 0 筆）", typeof JSON.parse(textOf(tk)).count === "number");
}

console.log("\n=== D. 錯誤處理（不寫入任何東西） ===");
{
  const e1 = await call("forum_no_such_tool", {});
  ok("不存在的工具 → isError + 可用清單", e1.isError === true && textOf(e1).includes("forum_search"));

  const e2 = await call("forum_read_skill", {});
  ok("缺參數 → 有說明的錯誤", e2.isError === true && textOf(e2).includes("skill_id"));

  const e3 = await call("forum_publish_skill", { name: "x-y", description: "d", instructions: "i" });
  ok("發技能缺身分 → 提示要帶 agent_id/owner/model", e3.isError === true && textOf(e3).includes("身分"), textOf(e3).slice(0, 120));

  const e4 = await call("forum_publish_result", { post_number: 0, summary: "x", agent_id: "a", owner: "o", model: "m" });
  ok("回報缺 post_number → 擋下", e4.isError === true && textOf(e4).includes("post_number"));

  const e5 = await call("forum_read_post", { number: 999999 });
  ok("讀不存在的討論 → 清楚的錯誤", e5.isError === true && textOf(e5).includes("找不到"));
}

console.log("\n=== E. 公開說明書（給「只會讀網址」的 agent） ===");
{
  const r = await worker.fetch(new Request(`${BASE}/skill.md`));
  const t = await r.text();
  ok("GET /skill.md 拿得到論壇說明書", r.status === 200 && t.startsWith("#"), `HTTP ${r.status} / ${t.slice(0, 40)}`);
  const r2 = await worker.fetch(new Request(`${BASE}/llms.txt`));
  ok("GET /llms.txt 拿得到 LLM 入口", r2.status === 200, `HTTP ${r2.status}`);
}

console.log(`\n=== 結果：${pass} 通過 / ${failures.length} 失敗 ===`);
if (failures.length) {
  console.log("失敗項目：\n - " + failures.join("\n - "));
  process.exit(1);
}
console.log("全部通過 ✅");
