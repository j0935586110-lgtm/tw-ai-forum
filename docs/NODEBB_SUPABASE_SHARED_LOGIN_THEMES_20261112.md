# 台灣 AI 論壇 × 冒險者公會：NodeBB / Supabase OAuth 共用登入驗收

日期：2026-11-12。**隔離測試，不修改正式 NodeBB、MongoDB、Supabase Auth、Google Cloud 憑證。**

## 結論

Google 作為共用身分提供者是合理方案，但更重要的是讓 NodeBB 透過 OIDC/OAuth2 Client 信任公會現有 Supabase Auth，使用同一個會員 UUID。現階段真正的跨站共用登入 **尚未完成**；已證實網路與函式庫相容性的必要條件，不應誤報為成功登入。

## 機器上實測事實

- 正式 NodeBB：v4.16.0；已安裝 nodebb-plugin-sso-google v4.0.2，現有論壇 Google 登入服務可用。
- NodeBB SSO 多提供者官方 GitHub 插件 NodeBB/nodebb-plugin-sso-oauth2-multiple：隔離 cloned source package.json v2.1.3，nbbpm.compatibility ^4.14.0，包含設定 usePkce 的開關、state=true、PKCE 支援、可自訂 authorization URL、token URL、userinfo 路由、可從 OIDC discovery 讀取這三條端點；尚未安裝於正式 NodeBB。
- Supabase 專案 wrwsjlydozfzejixofwm 官方 OIDC discovery HTTP 200：issuer、authorize、token、userinfo 可讀，支持 S256 code challenge、authorization_code、client_secret_basic/post/none。
- **可重跑 OAuth 授權前半段的測試**：`node tools/sso-lab/verify-oidc-pkce.cjs`。使用 NodeBB 自己已安裝的 passport-oauth2 1.8.0（與上游 SSO 外掛使用的 OAuth2Strategy API 相同），以虛構且未註冊的 OAuth client_id 實際產生授權 URL，驗證 callback、state、S256 code_challenge、無 client_secret；測試不會進入任何真人帳號。
- 公會 Supabase Google Provider 公開狀態 external.google=false；Google Cloud 上現存的論壇 Client ID/secret只在原論壇專屬檔案。已知儲存的 Google redirect_uri 是 https://forum.928174.xyz/auth/google/callback，**尚無 Supabase callback 已加進 Google Console allowlist 的證據**；授權端點在無登入 cookie 時回 HTTP 200，不能證明回呼真的被允許。
- 不會在正式站直接啟用新 SSO 外掛；兩個獨立 Google 登入不等於共用 NodeBB/Supabase Session 或已驗證帳號所有權。

## 下一步完整閉環

1. 在使用者控制的 Google Cloud OAuth Client 建立/調整 Authorized redirect URIs，加入 https://wrwsjlydozfzejixofwm.supabase.co/auth/v1/callback；為 NodeBB 測試用獨立 URI 留名額。不要暴露 Client Secret。
2. 透過 Supabase Authentication > Providers > Google 設定 Client ID/Secret 並啟用。測新會員／既有會員 Google 登入與 redirect allowlist。
3. 為論壇註冊 Supabase OAuth Client（回呼為 https://forum.928174.xyz/auth/guild/callback，但初次必須在獨立測試論壇域名），於隔離 NodeBB 安裝 SSO OAuth2 Multiple，設定上述 OIDC 端點、scope openid email profile、S256 PKCE、userinfo、唯一 user key=sub。
4. 使用者必須分別驗證擁有既有 NodeBB 帳號與公會帳號後，才可連結；不可憑 email/nickname 相同直接合併帳號。至少用兩真人帳號測 Google → 公會 → NodeBB、論壇發文及撤權／登出；保留原論壇 Google 插件作安全回退。
5. 將身份綁定到 AI MCP 的會員權限：只有 OAuth 持有人能發自己的論壇文章，禁止平台共用 NodeBB 管理員 Token。

## 已安裝並符合 NodeBB 4.16 相容宣告的開源主題

| 主題 | 現機版本 | 宣告相容 | 用途 |
| --- | --- | --- | --- |
| NodeBB/nodebb-theme-harmony | 3.3.4 | ^4.16.0 | 官方預設、簡約、RWD，建議保留 |
| NodeBB/nodebb-theme-persona | 15.3.3 | ^4.16.0 | 傳統論壇樣式 |
| NodeBB/nodebb-theme-lavender | 8.1.0 | ^4.15.0 | 卡片式區塊 |
| barisusakli/nodebb-theme-peace | 3.1.3 | ^4.15.1 | 左側導覽、Bootswatch skin |

新增已研究並隔離 clone 的 **NodeBB/nodebb-theme-quickstart v0.1.0**：以 Harmony 作 baseTheme，manifest 相容 ^4.14.0、MIT，適合開一個只有公會色票與公會徽章的子主題。直接復用 Harmony 的更新與 RWD；不應建立新論壇引擎。NodeBB 官方 docs: https://docs.nodebb.org/development/themes/

額外可直接借用的模組：
- NodeBB-Community/nodebb-plugin-recent-cards：展示近期文章/卡片，需先實測 v4；此為被動維護的社群外掛。
- NodeBB/nodebb-rewards-essentials：現有 NodeBB 的原生信譽/獎勵，可用於非貨幣的徽章/會員群組；**不可等同公會 G 幣**。
- NodeBB-Community/nodebb-plugin-reputation-rules：限制刷讚，有反作弊規則，但也需先驗證 v4 相容性。
- 避免直接選用 nodebb-theme-pewter 舊遊戲主題、nodebb-plugin-openfantasy 老舊 RPG 插件，與 NodeBB v4.16 兼容性無證據。

## 產品決策

公會 Chat + 任務布告欄仍使用原來 React UI、NodeBB 作社群後端及公開 API；**NodeBB 的主題不會自動改變站內 React /community 的外觀**。因此先完成 Google/OIDC 共用身份，之後才考慮以 Harmony 子主題讓需要進 NodeBB 發文/管理的頁面視覺一致。真正論壇站內以自己帳號發文，需要獨立會員授權或 OAuth 代理，不能借管理員 API Token。
