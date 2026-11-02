const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/ui.js'),'utf8');
const apiCode=source.slice(source.indexOf('async function apiRequest('),source.indexOf("window.addEventListener('pagehide'"));
const syncCode=source.split(/\r?\n/).find(line=>line.startsWith('async function sync()'));
function setup({acknowledged=false,cancel=false,offline=false,modal=false}={}){
 const calls=[],dialogs=[],toasts=[];
 const node={classList:{add(){},remove(){},toggle(){}}};
 const state={error:'',status:'Ready',t:0,sent:0,acks:0,preview:true,connection:null,fps:0,battery:null,events:[]};
 const control={offline,acknowledged};
 const ctx={hasBackend:true,token:'test',clientId:'test',AbortController,setTimeout,clearTimeout,
  clone:x=>JSON.parse(JSON.stringify(x)),activeThemedDialog:modal?{}:null,
  connected:false,syncPending:false,dirty:false,settingsDirty:false,failures:0,
  localEvents:[],performance:{now:()=>0},now:()=>0,$:()=>node,
  document:{querySelector:()=>node},toast:msg=>toasts.push(msg),
  themedDialog:async o=>{dialogs.push(o);return cancel?null:true},
  fetch:async(url,options)=>{
   calls.push(url);
   if(control.offline)throw Error('Offline');
   let data={ok:true};
   if(url==='/api/state')data=state;
   if(url==='/api/notice')data={acknowledged:control.acknowledged,text:'Current notice',notice_sha256:'test-hash'};
   if(url==='/api/notice/decision'&&JSON.parse(options.body).decision==='acknowledged')control.acknowledged=true;
   return {ok:true,json:async()=>data};
  }
 };
 vm.createContext(ctx);vm.runInContext(apiCode+'\n'+syncCode,ctx);
 return {ctx,control,calls,dialogs,toasts};
}
async function sync(t){await t.ctx.sync();await new Promise(setImmediate)}
(async()=>{
 let t=setup();await sync(t);
 assert.equal(t.dialogs.length,1,'first successful page sync must prompt without device operation');
 assert(t.calls.includes('/api/notice/decision'));
 assert(!t.calls.some(p=>p.includes('/driver/')||p==='/api/start'));
 await sync(t);assert.equal(t.dialogs.length,1,'polling must not duplicate prompt');
 t=setup({acknowledged:true});await sync(t);assert.equal(t.dialogs.length,0);
 t=setup({cancel:true});await sync(t);await sync(t);
 assert.equal(t.dialogs.length,1,'cancel must not create a polling prompt loop');
 await assert.rejects(t.ctx.api('driver/write',{}),/已取消/);
 assert.equal(t.dialogs.length,2,'device actions still need acknowledgement after cancellation');
 assert(!t.calls.includes('/api/driver/write'));
 t=setup({offline:true});await sync(t);assert.equal(t.dialogs.length,0);
 t.control.offline=false;await sync(t);assert.equal(t.dialogs.length,1,'reconnect must prompt');
 t=setup({modal:true});await sync(t);assert.equal(t.dialogs.length,0);
 t.ctx.activeThemedDialog=null;await sync(t);assert.equal(t.dialogs.length,1,'wait for existing dialog to close');
 t=setup({modal:true});
 await assert.rejects(t.ctx.api('driver/write',{}),error=>error.code==='notice_busy');
 assert(!t.calls.includes('/api/notice/decision'),'an unseen dialog must not be logged as cancelled');
 console.log('PASS: 6 startup scenarios: first open, saved acknowledgement, cancellation, reconnect, modal deferral, no false cancellation.');
})().catch(error=>{console.error(error);process.exitCode=1});

