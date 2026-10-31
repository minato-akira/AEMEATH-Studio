'use strict';
(function(scope){
const beat=(t,bpm=72)=>{const p=((t*bpm/60)%1+1)%1;const pulse=(c,w)=>{const u=Math.abs(p-c)/w;return u<1?(1-u*u)**3:0};return Math.min(1,pulse(.16,.12)+.62*pulse(.36,.13))};
function dataflowLevel(x,y,t,speed){const lane=Math.floor(x),phase=((lane*73+lane*lane*19)%101)/101*8.2,head=((t*speed*2+phase)%8.2+8.2)%8.2-1.2,behind=head-y;if(behind<-.35||behind>1.5)return 0;const u=behind<0?1+behind/.35:1-behind/1.5;return (u*u*(3-2*u))**(behind<0?1:2)}
function hsv(h){const i=Math.floor(h*6),f=h*6-i,q=1-f;return [[1,f,0],[q,1,0],[0,1,f],[0,q,1],[f,0,1],[1,0,q]][i%6].map(v=>Math.floor(v*255+.5))}
function render(config,keys,t,events=[],identify=null){
 const positions=new Map(keys.map(k=>[k.id,[k.x+k.w/2,k.y+.5]]));const colors={};for(const k of keys)colors[k.id]=[0,0,0];
 if(identify!==null){for(const k of keys)colors[k.id]=identify.includes(k.id)?[70,170,215]:[0,0,0];return colors}
 for(const g of config.groups){let rgb=[1,3,5].map(i=>parseInt(g.color.slice(i,i+2),16));
 g.keys.forEach((k,index)=>{let phase=t*g.speed,level=g.brightness*config.settings.master,color=rgb;const effect=g.effect;
 if(effect==='呼吸')level*=.5-.5*Math.cos(phase*Math.PI*2);
 else if(effect==='彩虹')color=hsv(((phase*.2+index/Math.max(1,g.keys.length))%1+1)%1);
 else if(effect==='波浪')level*=(.5+.5*Math.sin(phase*Math.PI*2-positions.get(k)[0]*.55))**2;
 else if(effect==='閃爍')level*=Math.floor(phase*2)%2;
 else if(effect==='心跳')level*=beat(t,g.bpm);
 else if(effect==='核心資料流')level*=dataflowLevel(...positions.get(k),t,g.speed);
 else if(effect==='觸鍵星光'||effect==='星光漣漪'){
 let energy=0;for(const [key,when] of events){const age=t-when;if(age<0||age>2||!positions.has(key))continue;let value=0;
 if(effect==='觸鍵星光')value=key===k?Math.exp(-age*5*g.speed):0;
 else{const [x,y]=positions.get(k),[a,b]=positions.get(key);const dist=Math.hypot(x-a,y-b);value=Math.exp(-(((dist-age*8*g.speed)/.7)**2))*Math.exp(-age*1.8)}energy=Math.max(energy,value)}level*=energy;
 }else if(effect==='熄滅')level=0;
 const bg=g.background_color||'#000000',floor=['心跳','熄滅'].includes(effect)?0:(g.background_brightness||0)*config.settings.master;colors[k]=color.map((v,i)=>Math.max(0,Math.min(255,Math.max(v*level,parseInt(bg.slice(1+i*2,3+i*2),16)*floor))));});}
 return colors;
}
const darkCache=new Map();
function faithful(target,actual){const total=target.reduce((a,b)=>a+b,0),out=actual.reduce((a,b)=>a+b,0);if(total===0)return out===0;if(out===0)return false;return target.reduce((s,a,i)=>s+Math.abs(a/total-actual[i]/out),0)<=.08+1e-12}
function rawQuantize(rgb,wireless){const steps=wireless?[8,4,8]:[1,1,1],limits=wireless?[31,63,31]:[255,255,255];return rgb.map((n,i)=>Math.min(limits[i],Math.floor(n/steps[i]+.5))*steps[i])}
function outputFaithful(rgb,q,wireless){return faithful(rgb,q)&&(wireless||faithful(rgb.map((v,i)=>v/[3,2,2][i]),q.map((v,i)=>Math.floor(v/[3,2,2][i]))))}
function darkFloor(chroma,wireless){
 const key=wireless+':'+chroma.join(',');if(darkCache.has(key))return darkCache.get(key);
 const steps=wireless?[8,4,8]:[1,1,1],limits=wireless?[31,63,31]:[255,255,255];
 let upper=26*chroma.reduce((s,n,i)=>s+(n?(wireless?[7,3,7]:[.5,.5,.5])[i]:0),0)/chroma.reduce((a,b)=>a+b,0);
 if(!wireless)upper=Math.max(upper,26*chroma.reduce((s,n,i)=>s+(n?[5/6,.75,.75][i]:0),0)/chroma.reduce((s,n,i)=>s+n/[3,2,2][i],0));
 upper=Math.min(255,upper);const edgeSet=new Set([0,upper]);
 chroma.forEach((ratio,i)=>{if(ratio)for(let j=0;j<limits[i];j++){const v=(j+.5)*steps[i]/ratio;if(v<upper)edgeSet.add(v)}});
 const edges=[...edgeSet].sort((a,b)=>a-b);let floor=0;
 for(let i=edges.length-2;i>=0;i--){const sample=chroma.map(n=>n*(edges[i]+edges[i+1])/2);if(!outputFaithful(sample,rawQuantize(sample,wireless),wireless)){floor=edges[i+1];break}}
 if(darkCache.size>=2048)darkCache.delete(darkCache.keys().next().value);darkCache.set(key,floor);return floor;
}
function quantizeColor(rgb,mode='USB'){
 const wireless=mode.startsWith('2.4G'),peak=Math.max(...rgb);if(!peak)return [0,0,0];
 const chroma=rgb.map(n=>Math.round(n/peak*1e8)/1e8);if(peak+1e-7<darkFloor(chroma,wireless))return [0,0,0];
 const q=rawQuantize(rgb,wireless);return outputFaithful(rgb,q,wireless)?q:[0,0,0];
}
function output(colors,settings,mode='USB'){
 const out={};
 for(const k in colors){const profile=settings.color_profiles?.heart&&[24,25,27,28,39,40,41,42,43,44,55,56,57,58,59,71,72,73,74,84].includes(Number(k))?{...settings,...settings.color_profiles.heart}:settings,gains=[profile.red_gain??1,profile.green_gain??1,profile.blue_gain??1],order=profile.color_order||'RGB';const peak=Math.max(...colors[k]);if(!peak){out[k]=[0,0,0];continue}
 const chroma=colors[k].map((n,i)=>n/peak*gains[i]),scale=255*(peak/255)**(profile.gamma??1)/Math.max(1,...chroma),c=chroma.map(n=>n*scale);
 out[k]=quantizeColor([...order].map(v=>c['RGB'.indexOf(v)]),mode);
 }return out;
}
function light(c){return c.map(v=>{const x=v/255;return Math.round(255*(x<=.0031308?12.92*x:1.055*x**(1/2.4)-.055))})}
scope.ResonanceVisual={beat,dataflowLevel,render,output,quantizeColor,light};if(typeof module!=='undefined')module.exports=scope.ResonanceVisual;
})(typeof window==='undefined'?globalThis:window);
