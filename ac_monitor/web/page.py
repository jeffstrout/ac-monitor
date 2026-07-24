"""The single-page dashboard + control panel (served at ``/``).

Responsive: fluid layout, touch-friendly controls, horizontally scrollable
tables on narrow screens, and light/dark following ``prefers-color-scheme``.
Self-contained (inline CSS/JS, no framework) to fit the appliance deploy.
"""

DASHBOARD = """<!doctype html>
<title>AC Monitor</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--card:#f5f5f5;
       --ok:#0a7d00;--bad:#c00;--accent:#0a8f2f}
 @media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e8e8e8;--muted:#9aa0a6;
       --line:#333;--card:#1e2126;--ok:#4caf50;--bad:#ef5350;--accent:#2e7d32}}
 *{box-sizing:border-box}
 body{font:16px/1.45 system-ui,-apple-system,sans-serif;margin:0;background:var(--bg);color:var(--fg)}
 .wrap{max-width:760px;margin:0 auto;padding:1rem}
 h1{font-size:1.35rem;margin:.2rem 0} h2{font-size:1.05rem;margin:1.6rem 0 .5rem}
 .big{font-size:2.2rem;font-weight:600}
 .muted{color:var(--muted)} .ok{color:var(--ok)} .fault,.fail{color:var(--bad)}
 .scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:.5rem}
 table{border-collapse:collapse;width:100%;min-width:340px}
 td,th{padding:.55rem .65rem;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
 tr:last-child td{border-bottom:0}
 button{font:inherit;min-height:44px;padding:.4rem .85rem;margin:.15rem;border:1px solid var(--line);
        border-radius:.55rem;background:var(--card);color:var(--fg);cursor:pointer}
 button:active{transform:translateY(1px)}
 .row{display:flex;flex-wrap:wrap;gap:.6rem;align-items:end}
 .field{display:flex;flex-direction:column;gap:.2rem;font-size:.82rem;color:var(--muted)}
 input{font:inherit;min-height:44px;padding:.4rem .5rem;border:1px solid var(--line);
        border-radius:.5rem;background:var(--bg);color:var(--fg);width:100%}
 .field input{width:9rem} .field.sm input{width:5rem}
 .pill{display:inline-block;border-radius:1rem;padding:.15rem .75rem;font-size:.82rem;color:#fff}
 .on{background:var(--accent)} .off{background:#8a8a8a}
 .toggle{display:flex;align-items:center;gap:.6rem;margin:.5rem 0}
 @media (max-width:480px){.wrap{padding:.75rem}h1{font-size:1.2rem}.big{font-size:1.9rem}.field input{width:100%}}
</style>
<div class="wrap">
<h1>AC Monitor</h1>

<p>Air-side ΔT: <span class="big" id="dt">–</span> <span id="mode" class="muted"></span></p>
<div class="scroll"><table id="temps"></table></div>
<p>Fan: <b id="fan">–</b> &nbsp; Bus: <b id="bus">–</b></p>
<p id="faults"></p>

<h2>Outputs</h2>
<div class="toggle">Split-flap display (slot 2):
 <span id="displayState" class="pill off">?</span>
 <button onclick="toggle('display')">Toggle</button></div>
<div class="toggle">MQTT output:
 <span id="mqttState" class="pill off">?</span>
 <button onclick="toggle('mqtt')">Toggle</button></div>
<div class="row">
 <label class="field">host<input id="mqttHost"></label>
 <label class="field sm">port<input id="mqttPort" value="1883"></label>
 <label class="field">user<input id="mqttUser"></label>
 <label class="field">pass<input id="mqttPass" type="password"></label>
 <button onclick="saveMqtt()">Save broker</button>
</div>

<h2>Calibration</h2>
<p class="muted">Hold a probe at a known temperature, then capture. Two captures
 (ice + boiling) fit that channel's gain/offset. Boiling °C:
 <input id="boilC" value="99.4" style="width:5rem;display:inline-block"> (Tyler, TX)</p>
<div class="scroll"><table id="cal"></table></div>

<p class="muted" id="foot"></p>
</div>
<script>
async function j(u,o){ return (await fetch(u,o)).json(); }
async function post(u,b){ return j(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}); }
async function toggle(w){ await post('/api/toggle/'+w,{}); refresh(); }
async function saveMqtt(){ await post('/api/mqtt/config',{host:mqttHost.value,port:parseInt(mqttPort.value)||1883,username:mqttUser.value,password:mqttPass.value}); refresh(); }
async function capture(role,k){ await post('/api/calibrate/capture',{role:role,known_c:k}); refreshCal(); }
async function resetCal(role){ await post('/api/calibrate/reset',{role:role}); refreshCal(); }
function pill(el,on){ el.className='pill '+(on?'on':'off'); el.textContent=on?'ON':'OFF'; }
function sgn(x){ return (x>=0?'+':'')+x.toFixed(1); }   // explicit +/- sign

async function refresh(){
 let s; try{ s=await j('/api/state'); }catch(e){ return; }
 const u=s.unit;
 dt.textContent = s.delta_t==null?'–':sgn(s.delta_t)+'°'+u;
 mode.textContent = s.mode?('('+s.mode+')'):'';
 temps.innerHTML='<tr><th>Channel</th><th>Temp</th></tr>'+Object.entries(s.temps).map(([k,v])=>{
   const val=s.health[k]&&v!=null? sgn(v)+'°'+u : '<span class=fail>FAIL</span>';
   return `<tr><td>${k}</td><td>${val}</td></tr>`;}).join('');
 fan.textContent = s.fan_running==null?'FAIL':(s.fan_running?'RUNNING':'IDLE');
 bus.innerHTML = s.i2c_ok?'<span class=ok>OK</span>':'<span class=fault>DOWN</span>';
 const af=Object.entries(s.faults).filter(([k,v])=>v).map(([k])=>k);
 faults.innerHTML = af.length?'<span class=fault>Faults: '+af.join(', ')+'</span>':'<span class=ok>No faults</span>';
 pill(displayState,s.toggles.display_push); pill(mqttState,s.toggles.mqtt);
 const v=s.version||{}, t=s.last_poll_at?new Date(s.last_poll_at*1000).toLocaleTimeString():'–';
 foot.textContent=`updated ${t} · poll #${s.poll_count} · build ${v.commit||''}`;
}
async function refreshCal(){
 let c; try{ c=await j('/api/calibration'); }catch(e){ return; }
 const boil=parseFloat(boilC.value)||99.4;
 cal.innerHTML='<tr><th>Channel</th><th>gain</th><th>offset</th><th>captures</th><th>actions</th></tr>'+
  Object.entries(c).map(([role,d])=>{
   const cap=d.captures.map(p=>`${p[0]}→${p[1]}`).join(', ');
   return `<tr><td>${role}${d.custom?' *':''}</td><td>${d.gain}</td><td>${d.offset}</td>`+
    `<td class=muted>${cap}</td><td><button onclick="capture('${role}',0)">Ice 0°</button>`+
    `<button onclick="capture('${role}',${boil})">Boil</button>`+
    `<button onclick="resetCal('${role}')">Reset</button></td></tr>`;}).join('');
}
refresh(); refreshCal(); setInterval(refresh,2000);
</script>
"""
