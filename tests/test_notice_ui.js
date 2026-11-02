const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const ui=fs.readFileSync(path.join(__dirname,'../web/ui.js'),'utf8');
const code=ui.slice(ui.indexOf('async function apiRequest('),ui.indexOf("window.addEventListener('pagehide'"));
function context(handler,dialogResult=true){
 const requests=[],dialogs=[];
 const ctx={hasBackend:true,token:'test-token',clientId:'test-client',AbortController,setTimeout,clearTimeout,
  clone:x=>JSON.parse(JSON.stringify(x)),
  themedDialog:async options=>{dialogs.push(options);return typeof dialogResult==='function'?dialogResult(options):dialogResult},
  fetch:async(url,options)=>{const body=options.body?JSON.parse(options.body):undefined;requests.push({url,body});const reply=await handler(url,body);return {ok:(reply.status||200)<400,json:async()=>reply.data}}
 };
 vm.createContext(ctx);vm.runInContext(code,ctx);return {ctx,requests,dialogs};
}
(async()=>{
 // Cancel must not send confirmation or a device request.
 let t=context(url=>({data:{acknowledged:false,text:'Notice',notice_sha256:'v1'}}),null);
 await assert.rejects(t.ctx.api('driver/write',{}),/已取消/);
 assert(!t.requests.some(r=>r.url==='/api/driver/write'));
 assert.equal(t.requests.find(r=>r.url==='/api/notice/decision').body.decision,'cancelled');
 // Saving acknowledgement failure must not allow a device operation.
 t=context(url=>url.endsWith('/decision')?{status:500,data:{error:'save failed'}}:{data:{acknowledged:false,text:'Notice',notice_sha256:'v1'}});
 await assert.rejects(t.ctx.api('driver/write',{}),/save failed/);
 assert(!t.requests.some(r=>r.url==='/api/driver/write'));
 // Concurrent operations share one explicit acknowledgement.
 t=context(url=>({data:url==='/api/notice'?{acknowledged:false,text:'Notice',notice_sha256:'v1'}:{ok:true}}));
 await Promise.all([t.ctx.api('driver/write',{id:1}),t.ctx.api('driver/write',{id:2})]);
 assert.equal(t.dialogs.length,1);
 assert.equal(t.requests.filter(r=>r.url==='/api/notice/decision').length,1);
 assert.equal(t.requests.filter(r=>r.url==='/api/driver/write').length,2);
 // Only a server-declared pre-operation rejection may retry after re-confirmation.
 let writes=0,reads=0;
 t=context(url=>{
  if(url==='/api/notice')return {data:{acknowledged:++reads===1,text:'New notice',notice_sha256:'v2'}};
  if(url==='/api/driver/write'&&++writes===1)return {status:428,data:{error:'Updated',code:'notice_required'}};
  return {data:{ok:true}};
 });
 await t.ctx.api('driver/write',{id:1});
 assert.equal(writes,2);assert.equal(t.dialogs.length,1);
 // An ordinary operation failure must never be retried.
 writes=0;
 t=context(url=>url==='/api/notice'?{data:{acknowledged:true}}:(writes++,{status:500,data:{error:'device failed'}}));
 await assert.rejects(t.ctx.api('driver/write',{}),/device failed/);
 assert.equal(writes,1);
 console.log('PASS: 5 frontend confirmation scenarios (cancel, save failure, concurrency, stale notice, no unsafe retry).');
})().catch(error=>{console.error(error);process.exitCode=1});

