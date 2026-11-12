#!/usr/bin/env node
'use strict';
/**
 * NON-DESTRUCTIVE LAB ONLY. Does not log anyone in, register clients, edit NodeBB
 * or change Supabase. Requires a local NodeBB tree with passport-oauth2 installed.
 * Usage:
 *   NODEBB_PATH=/home/j/work/nodebb node tools/sso-lab/verify-oidc-pkce.cjs
 */
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const base=(process.env.SUPABASE_URL||'https://wrwsjlydozfzejixofwm.supabase.co').replace(/\/+$/,'');
const nodebb=process.env.NODEBB_PATH||'/home/j/work/nodebb';
const manifest=JSON.parse(fs.readFileSync(path.join(nodebb,'package.json'),'utf8'));
const installed=JSON.parse(fs.readFileSync(path.join(nodebb,'node_modules/nodebb-plugin-sso-google/package.json'),'utf8'));
const {Strategy}=require(path.join(nodebb,'node_modules/passport-oauth2'));
async function main(){
 const issuer=base+'/auth/v1';
 const config=await fetch(issuer+'/.well-known/openid-configuration',{signal:AbortSignal.timeout(11000)}).then(async r=>{assert.equal(r.status,200,'OIDC metadata');return r.json()});
 assert.equal(config.issuer,issuer);
 assert.equal(config.authorization_endpoint,issuer+'/oauth/authorize');
 assert.equal(config.token_endpoint,issuer+'/oauth/token');
 assert.equal(config.userinfo_endpoint,issuer+'/oauth/userinfo');
 assert.ok(config.code_challenge_methods_supported.includes('S256'));
 assert.equal(manifest.version,'4.16.0');
 const options={
  authorizationURL:config.authorization_endpoint,
  tokenURL:config.token_endpoint,
  clientID:'test-oidc-client-not-registered',
  clientSecret:'fake-local-test-only',
  callbackURL:'http://localhost:15678/auth/guild/callback',
  state:true,
  pkce:true,
  passReqToCallback:true,
 };
 let authUrl;
 const strategy=new Strategy(options,(_req,_access,_refresh,profile,done)=>done(null,profile));
 strategy.redirect=url=>{authUrl=new URL(url)};
 strategy.error=err=>{throw err};
 strategy.fail=err=>{throw Error(String(err))};
 strategy.authenticate({url:'/auth/guild',query:{},headers:{host:'localhost:15678'},session:{},connection:{encrypted:false}},{scope:['openid','email','profile']});
 assert.ok(authUrl,'Passport strategy must redirect to Supabase');
 assert.equal(authUrl.origin,base);
 assert.equal(authUrl.pathname,'/auth/v1/oauth/authorize');
 assert.equal(authUrl.searchParams.get('redirect_uri'),options.callbackURL);
 assert.equal(authUrl.searchParams.get('response_type'),'code');
 assert.equal(authUrl.searchParams.get('code_challenge_method'),'S256');
 assert.match(authUrl.searchParams.get('code_challenge')||'',/^[A-Za-z0-9_-]{43}$/);
 assert.ok(authUrl.searchParams.has('state'));
 assert.equal(authUrl.searchParams.has('client_secret'),false);
 console.log('PASS '+JSON.stringify({
  nodebb_version:manifest.version,
  google_plugin_installed:installed.version,
  oidc_discovery:200,
  provider_origin:authUrl.origin,
  supports_s256_pkce:true,
  state:true,
  challenge_encoded:true,
  secrets_in_browser_url:false,
  limitation:'No OAuth client registered or Google provider enabled; no member login performed.',
 }));
}
main().catch(err=>{console.error('FAIL',err.message);process.exitCode=1;});
