const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const read=n=>fs.readFileSync(path.join(__dirname,'../web',n),'utf8');
const ui=read('ui.js'),driver=read('driver-ui.js'),color=read('color-ui.js'),workspace=read('workspace-ui.js');
const clone=x=>JSON.parse(JSON.stringify(x));
const line=(s,prefix)=>s.split(/\r?\n/).find(x=>x.startsWith(prefix));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
function context(extra={}){const c={clone,JSON,console,window:{addEventListener(){}},toast(){},...extra};vm.createContext(c);return c}
(async()=>{
 let request=deferred(),clean=0,dirty=0;
 let c=context({cfg:{settings:{theme:'星海'}},api:()=>request.promise,setClean:()=>clean++,setDirty:()=>dirty++,drawLayers(){}});
 vm.runInContext(ui.slice(ui.indexOf('let configSavePending='),ui.indexOf('function setTheme()')),c);
 let saving=c.apply(false);c.cfg.settings.theme='晨霧';request.resolve({config:{settings:{theme:'星海'}}});
 assert.equal(await saving,false);assert.equal(c.cfg.settings.theme,'晨霧');assert.equal(clean,0);assert.equal(dirty,1);
 request=deferred();saving=c.apply(false);assert.equal(await c.apply(false),false,'parallel save must not submit');
 request.resolve({config:clone(c.cfg)});assert.equal(await saving,true);assert.equal(clean,1);
 request=deferred();saving=c.apply(false);vm.runInContext('settingsRevision++',c);request.resolve({config:clone(c.cfg)});
 assert.equal(await saving,false,'unsaved preference controls must survive in-flight save');

 let colors=0,paint=0;const node={classList:{add(){},remove(){},toggle(){}}};
 const saved={settings:{theme:'晨霧',gamma:2},groups:[{effect:'心跳'}],active_mode:'native'};
 c=context({hasBackend:true,syncPending:false,connected:false,dirty:false,settingsDirty:false,failures:0,
  cfg:{settings:{theme:'星海'},active_mode:'leisure'},api:async()=>({config:clone(saved),autostart_actual:false,t:0,error:'',status:'ready',connection:null,preview:true,fps:0,battery:null,events:[]}),
  loadGroup(){},setupSettings(){},setClean(){},performance:{now:()=>0},now:()=>0,localEvents:[],
  $:()=>node,document:{querySelector:()=>node},promptStartupNotice(){},
  colorDraft:{normal:{gamma:1}},colorDirty:false,colorTestActive:false,readSavedColors(){colors++;c.colorDraft.normal.gamma=c.cfg.settings.gamma},
  workspaceMode:'leisure',workspaceBusy:false,schemeDirty:false,paintWorkspace(){paint++}});
 vm.runInContext(color.slice(color.indexOf('const previousColorConfigLoaded=')),c);
 vm.runInContext(workspace.slice(workspace.indexOf('let workspaceInitialized='),workspace.indexOf('window.studioReady.then')),c);
 vm.runInContext('workspaceInitialized=true',c);
 vm.runInContext(line(ui,'async function sync()'),c);
 await c.sync();assert.equal(c.workspaceMode,'native');assert.equal(c.colorDraft.normal.gamma,2);assert.equal(colors,1);assert.equal(paint,1);
 c.connected=false;c.colorDirty=true;c.colorDraft.normal.gamma=1.7;await c.sync();assert.equal(c.colorDraft.normal.gamma,1.7,'dirty calibration must survive reconnect');
 c.connected=false;c.schemeDirty=true;c.cfg.active_mode='leisure';await c.sync();assert.equal(c.cfg.active_mode,'leisure','dirty scheme must survive reconnect');

 let confirms=0,reads=0;
 c=context({macroDraft:[],pendingBindings:[],stopRecording(){},themedConfirm:async()=>{confirms++;return false},deviceCall:async()=>{reads++;return {}},
  $:()=>({}),showCurrentBinding(){},macroList(){},fillOptions(){},cfg:{macro_names:{}},Option:function(){}});
 vm.runInContext(driver.slice(driver.indexOf("let macroBaseline="),driver.indexOf('function currentBinding()')),c);
 c.macroDraft.push({id:0,actions:[{keyCode:4}]});await c.loadDevice();assert.equal(confirms,1);assert.equal(reads,0);assert.equal(c.macroDraft.length,1);
 c.macroDraft=[];request=deferred();c.deviceCall=()=>request.promise;
 const loading=c.loadDevice();c.macroDraft.push({id:1,actions:[]});request.resolve({macros:[]});await loading;
 assert.equal(c.macroDraft[0].id,1,'editing during read must preserve draft');
 let actions=['down','delay','up'];c.moveMacroAction(actions,0,1);assert.deepEqual(actions,['delay','down','up']);
 c.moveMacroAction(actions,1,2);assert.deepEqual(actions,['delay','up','down']);c.moveMacroAction(actions,2,0);assert.deepEqual(actions,['down','delay','up']);

 // Exercise the real native-apply closure: a successful write must not POST config again.
 const nativeCode=workspace.slice(workspace.indexOf(' async function applyNative()'),workspace.indexOf(' applyNativeWorkspace=applyNative;'));
 const calls=[];const control={disabled:false};
 c=context({cfg:{native_lighting:{mode:1}},updateNative(){},native:{querySelectorAll:()=>[control]},$:()=>control,
  endColorTest:async()=>{},sync:async()=>{},api:async(p,b)=>{calls.push(p);return {simulated:true,native_lighting:b}}});
 vm.runInContext(nativeCode,c);await c.applyNative();assert.deepEqual(calls,['native-lighting']);assert.equal(c.cfg.active_mode,'native');
 console.log('PASS: save concurrency, preference edits, first-load/reconnect synchronization, dirty drafts, macro reload cancellation/read race, drag ordering, native single save.');
})().catch(e=>{console.error(e);process.exitCode=1});
