'use strict';
const colorFields=['color_order','gamma','red_gain','green_gain','blue_gain'];
const neutralColor={color_order:'RGB',gamma:1,red_gain:1,green_gain:1,blue_gain:1};
let colorDraft=null,colorOriginal=null,colorDirty=false,colorCompare='after',colorTestActive=false,colorRevision=0,colorTimer=null,colorQueue=Promise.resolve(),colorSentAt=0;
function copyColor(s){return Object.fromEntries(colorFields.map(k=>[k,s[k]??neutralColor[k]]))}
function colorArea(){return $('calibration-area').value}
function selectedColor(draft=colorDraft){return colorArea()==='heart'?(draft.heart||draft.normal):draft.normal}
function editableColor(){if(colorArea()==='heart'&&!colorDraft.heart)colorDraft.heart=clone(colorDraft.normal);return selectedColor()}
function colorPreviewSettings(){if(!colorDraft)return cfg.settings;return {...cfg.settings,...colorDraft.normal,color_profiles:colorDraft.heart?{heart:colorDraft.heart}:{}}}
function readSavedColors(){colorOriginal={normal:copyColor(cfg.settings),heart:cfg.settings.color_profiles?.heart?copyColor(cfg.settings.color_profiles.heart):null};colorDraft=clone(colorOriginal);colorDirty=false;paintColorControls()}
function paintColorControls(){if(!colorDraft)return;const p=selectedColor();$('s-color_order').value=p.color_order;for(const n of colorFields.filter(k=>k!=='color_order')){$('s-'+n).value=Math.round(p[n]*100);$('n-'+n).value=p[n].toFixed(2);$(n==='gamma'?'gamma-value':n.replace('_','-')+'-value').textContent=p[n].toFixed(2)}$('color-profile-note').textContent=colorArea()==='heart'?(colorDraft.heart?'心型使用獨立校色，不改動一般按鍵。':'心型目前沿用一般校色；調整後會建立獨立設定。'):'此設定也適用於尚未建立獨立校色的心型。';$('color-inherit').classList.toggle('hidden',colorArea()!=='heart');$('color-dirty').textContent=colorDirty?'尚未儲存':'已儲存';$('color-dirty').classList.toggle('dirty',colorDirty);paintColorComparison()}
function changedColor(){colorDirty=true;colorCompare='after';paintColorControls();queueColorTest()}
function colorRgb(){return [1,3,5].map(i=>parseInt($('calibration-color').value.slice(i,i+2),16))}
function paintColorComparison(){
 if(!colorDraft)return;const rgb=colorRgb(),level=Number($('calibration-level').value)/100,mode=live?.connection||(cfg.settings.connection==='2.4G'?'2.4G':'USB');
 $('calibration-hex').textContent=$('calibration-color').value.toUpperCase();$('calibration-level-value').textContent=Math.round(level*100)+'%';
 for(const [name,draft]of [['before',colorOriginal],['after',colorDraft]]){const c=ResonanceVisual.output({0:rgb.map(v=>v*level)},selectedColor(draft),mode)[0];$('color-'+name+'-swatch').style.background=`rgb(${ResonanceVisual.light(c).join(',')})`;$('color-'+name+'-rgb').textContent=c.join(' / ')}
 for(const b of document.querySelectorAll('[data-compare]'))b.classList.toggle('active',b.dataset.compare===colorCompare);
}
function queueColorTest(){paintColorComparison();if(!colorTestActive)return;clearTimeout(colorTimer);const revision=++colorRevision;colorTimer=setTimeout(()=>{colorQueue=colorQueue.catch(()=>{}).then(async()=>{if(!colorTestActive||revision!==colorRevision)return;const body={rgb:colorRgb(),keys:colorArea()==='heart'?heart:ids.filter(k=>!heart.includes(k)),brightness:Number($('calibration-level').value)/100,settings:clone(selectedColor(colorCompare==='before'?colorOriginal:colorDraft))};try{await api('calibrate',body);colorSentAt=performance.now();await sync();$('calibration-status').textContent=`正在實機顯示${colorCompare==='before'?'原設定':'調整後'} · 60 秒後恢復燈效`;}catch(e){colorTestActive=false;toast(e.message);$('calibration-status').textContent=e.message}})},140)}
async function endColorTest(){clearTimeout(colorTimer);colorRevision++;const wasActive=colorTestActive||live?.calibration;colorTestActive=false;if(!wasActive)return;colorQueue=colorQueue.catch(()=>{}).then(async()=>{try{await api('calibrate',{rgb:null});if(live)live.calibration=null;$('calibration-status').textContent='已結束試色，恢復燈效。'}catch(e){toast(e.message)}});await colorQueue}
async function startColorTest(){if(cfg.settings.master===0){toast('整體亮度目前是 0，請先在首頁調高並套用亮度');return}try{if(!live?.running){await api('start',{});await sync()}colorTestActive=true;queueColorTest()}catch(e){toast(e.message)}}
async function saveColors(){try{const baseline=await api('state'),saved=clone(colorDraft);Object.assign(baseline.config.settings,saved.normal);baseline.config.settings.color_profiles=saved.heart?{heart:saved.heart}:{};await api('config',baseline.config);Object.assign(cfg.settings,saved.normal);cfg.settings.color_profiles=saved.heart?{heart:clone(saved.heart)}:{};colorOriginal=saved;colorDirty=JSON.stringify(colorDraft)!==JSON.stringify(saved);paintColorControls();toast('一般按鍵與心型校色已儲存')}catch(e){toast(e.message)}}
function setupColor(){
 readSavedColors();$('calibration-area').onchange=()=>{colorCompare='after';paintColorControls();queueColorTest()};
 for(const n of colorFields.filter(k=>k!=='color_order')){
  $('s-'+n).oninput=()=>{editableColor()[n]=Number($('s-'+n).value)/100;changedColor()};
  $('n-'+n).onchange=()=>{const value=Number($('n-'+n).value),lo=n==='gamma'?.15:0,hi=n==='gamma'?3:2;if(!Number.isFinite(value)||value<lo||value>hi){toast(`請輸入 ${lo}～${hi}`);paintColorControls();return}editableColor()[n]=Math.round(value*100)/100;changedColor()};
 }
 $('s-color_order').onchange=()=>{editableColor().color_order=$('s-color_order').value;changedColor()};
 for(const b of document.querySelectorAll('[data-correct]'))b.onclick=()=>{const [field,delta]=b.dataset.correct.split(':');const p=editableColor();p[field]=Math.round(Math.min(2,Math.max(0,p[field]+Number(delta)))*100)/100;changedColor()};
 for(const b of document.querySelectorAll('[data-gamma]'))b.onclick=()=>{editableColor().gamma=Number(b.dataset.gamma);changedColor()};
 for(const b of document.querySelectorAll('[data-rgb]'))b.onclick=()=>{$('calibration-color').value='#'+b.dataset.rgb.split(',').map(n=>Number(n).toString(16).padStart(2,'0')).join('');queueColorTest()};
 for(const id of ['calibration-color','calibration-level'])$(id).oninput=queueColorTest;
 for(const b of document.querySelectorAll('[data-compare]'))b.onclick=()=>{colorCompare=b.dataset.compare;queueColorTest()};
 $('calibration-start').onclick=startColorTest;$('calibration-stop').onclick=endColorTest;$('color-save').onclick=saveColors;
 $('color-cancel').onclick=async()=>{await endColorTest();colorDraft=clone(colorOriginal);colorDirty=false;paintColorControls();toast('已取消未儲存的校色調整')};
 $('color-reset').onclick=()=>{if(colorArea()==='heart')colorDraft.heart=clone(neutralColor);else colorDraft.normal=clone(neutralColor);changedColor()};
 $('color-inherit').onclick=()=>{colorDraft.heart=null;changedColor()};
 $('dataflow-preset').onclick=()=>{useHeart();group().effect='核心資料流';group().color='#66D2FF';group().brightness=.75;group().speed=.7;loadGroup();setDirty();toast('已預覽垂直資料流，套用後輸出')};
 setInterval(()=>{if(!colorTestActive)return;if(live?.calibration){const remaining=Math.ceil(Math.max(0,60-(performance.now()-colorSentAt)/1000));$('calibration-status').textContent=`實機${colorCompare==='before'?'原設定':'調整後'} · 約 ${remaining} 秒後恢復燈效`;}else if(colorSentAt&&performance.now()-colorSentAt>2000){colorTestActive=false;$('calibration-status').textContent='試色已結束，恢復燈效；草稿仍可儲存。'}},500);
 window.addEventListener('beforeunload',e=>{if(colorDirty){e.preventDefault();e.returnValue=''}});
}
window.studioReady.then(setupColor);
const previousColorConfigLoaded=window.onStudioConfigLoaded;
window.onStudioConfigLoaded=()=>{previousColorConfigLoaded?.();if(colorDraft&&!colorDirty&&!colorTestActive)readSavedColors()};
