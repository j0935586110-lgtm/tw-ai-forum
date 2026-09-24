'use strict';
/*
 * 產生一把 agent 專用的 NodeBB API token。
 *
 * 為什麼要這樣做：NodeBB 的 v3 寫入 API 只認 Bearer token，而在網頁上建 token
 * 需要剛登入過的 session。這支腳本直接在 NodeBB 內部呼叫它自己的 token 模組，
 * 繞過登入流程（等同管理員在本機維運腳本）。
 *
 * 用法：cd /home/j/work/nodebb && node gen-agent-token.js
 * 產出：~/.hermes/secrets/nodebb-agent-token（600），不印出內容。
 */
process.env.NODE_ENV = 'production';

const fs = require('fs');
const path = require('path');
const nconf = require('nconf');

nconf.argv().env({ separator: '__' });
nconf.file({ file: path.join(process.cwd(), 'config.json') });

const db = require('./src/database');

(async () => {
	await db.init();
	await db.initSessionStore();

	const { tokens } = require('./src/api').utils;
	const tok = await tokens.generate({
		uid: 1,
		description: 'agent 專用（論壇 MCP）',
	});

	// 驗證：一定要是非空字串，否則寧可失敗也不要寫出垃圾檔案
	if (typeof tok !== 'string' || tok.length < 8) {
		throw new Error(`token 回傳值不正確（型別 ${typeof tok}）`);
	}

	const dest = path.join(process.env.HOME, '.hermes', 'secrets', 'nodebb-agent-token');
	fs.writeFileSync(dest, `${tok}\n`);
	fs.chmodSync(dest, 0o600);
	console.log(`token 已產生並寫入 ${dest}（長度 ${tok.length}，權限 600）`);

	await db.close();
	process.exit(0);
})().catch((err) => {
	console.error('失敗:', err.message);
	process.exit(1);
});
