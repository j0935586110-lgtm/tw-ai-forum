'use strict';
/*
 * 把第三方登入（SSO）外掛的設定寫進 NodeBB。
 *
 * 為什麼要這支：金鑰不該出現在指令列或對話裡。這裡從 secrets 檔讀憑證，
 * 直接寫進 NodeBB 的設定（等同後台填欄位，但可重複執行、可版控流程）。
 *
 * 用法：
 *   node set-plugin-settings.js sso-google  /home/j/.hermes/secrets/forum-google-oauth.json
 *   node set-plugin-settings.js sso-github  /home/j/.hermes/secrets/forum-github-oauth.json
 *   node set-plugin-settings.js sso-facebook /home/j/.hermes/secrets/forum-facebook-oauth.json
 *
 * secrets 檔格式：{"client_id": "...", "client_secret": "..."}
 */
process.env.NODE_ENV = 'production';

const path = require('path');
const fs = require('fs');
const nconf = require('nconf');

nconf.argv().env({ separator: '__' });
nconf.file({ file: path.join(process.cwd(), 'config.json') });

const [namespace, file] = process.argv.slice(2);
if (!namespace || !file) {
	console.error('用法：node set-plugin-settings.js <sso-xxx> <憑證檔.json>');
	process.exit(2);
}

const creds = JSON.parse(fs.readFileSync(file, 'utf8'));
if (!creds.client_id || !creds.client_secret) {
	console.error('憑證檔缺少 client_id 或 client_secret');
	process.exit(3);
}

(async () => {
	const db = require('./src/database');
	await db.init();
	await db.initSessionStore();

	const meta = require('./src/meta');
	const settings = { id: creds.client_id, secret: creds.client_secret };
	// 外掛讀的欄位：id（用戶端 ID）、secret（用戶端密碼）、autoconfirm（自動開通）
	if (creds.autoconfirm) {
		settings.autoconfirm = 'on';
	}
	await meta.settings.set(namespace, settings);

	// 覆核：讀回來確認寫進去了（值不印出）
	const back = await meta.settings.get(namespace);
	console.log(`${namespace}: id 已設定=${!!back.id}｜secret 已設定=${!!back.secret}｜長度=${(back.secret || '').length}`);

	await db.close();
	process.exit(0);
})().catch((err) => {
	console.error('失敗：', err.message);
	process.exit(1);
});
