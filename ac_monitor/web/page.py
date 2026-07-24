"""The single-page dashboard + control panel (served at ``/``)."""

DASHBOARD = """<!doctype html>
<title>AC Monitor</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font:15px system-ui,sans-serif;margin:1.5rem;max-width:720px}
 h1{font-size:1.3rem} h2{font-size:1.05rem;margin-top:1.6rem}
 table{border-collapse:collapse;width:100%} td,th{padding:.35rem .5rem;border-bottom:1px solid #ddd;text-align:left}
 .big{font-size:2rem;font-weight:600}
 .fault{color:#b00} .ok{color:#080} .muted{color:#888} .fail{color:#b00}
 button{font:inherit;padding:.25rem .6rem;margin:0 .15rem;cursor:pointer}
 input{font:inherit;padding:.2rem;width:5.5rem}
 .pill{border-radius:1rem;padding:.1rem .6rem;font-size:.85rem}
 .on{background:#0a0;color:#fff} .off{background:#ccc}
</style>
<h1>AC Monitor</h1>

<p>Air-side ΔT: <span class="big" id="dt">–</span> <span id="mode" class="muted"></span></p>
<table id="temps"></table>
<p>Fan: <b id="fan">–</b> &nbsp; Bus: <b id="bus">–</b></p>
<p id="faults"></p>

<h2>Outputs</h2>
<p>
 Split-flap display (slot 2): <span id="displayState" class="pill off">?</span>
 <button onclick="toggle('display')">Toggle</button>
</p>
<p>
 MQTT output: <span id="mqttState" class="pill off">?</span>
 <button onclick="toggle('mqtt')">Toggle</button>
</p>
<p class="muted">MQTT broker:
 host <input id="mqttHost"> port <input id="mqttPort" style="width:4rem">
 user <input id="mqttUser"> pass <input id="mqttPass" type="password">
 <button onclick="saveMqtt()">Save</button>
</p>

<h2>Calibration</h2>
<p class="muted">Hold a probe at a known temperature, then capture. Two captures
 (ice + boiling) fit that channel's gain/offset. Boiling °C:
 <input id="boilC" value="99.4" style="width:4rem"> (Tyler, TX)</p>
<table id="cal"></table>

<p class="muted" id="foot"></p>
<script>
async function j(url,opts){ return (await fetch(url,opts)).json(); }
async function post(url,body){
 return j(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
}

async function toggle(which){ await post('/api/toggle/'+which,{}); refresh(); }
async function saveMqtt(){
 await post('/api/mqtt/config',{host:mqttHost.value,port:parseInt(mqttPort.value)||1883,
   username:mqttUser.value,password:mqttPass.value}); refresh();
}
async function capture(role,knownC){ await post('/api/calibrate/capture',{role:role,known_c:knownC}); refreshCal(); }
async function resetCal(role){ await post('/api/calibrate/reset',{role:role}); refreshCal(); }

function pill(el,on){ el.className='pill '+(on?'on':'off'); el.textContent=on?'ON':'OFF'; }

async function refresh(){
 let s; try{ s = await j('/api/state'); }catch(e){ return; }
 const u=s.unit;
 dt.textContent = s.delta_t==null?'–':s.delta_t.toFixed(1)+'°'+u;
 mode.textContent = s.mode?('('+s.mode+')'):'';
 temps.innerHTML='<tr><th>Channel</th><th>Temp</th></tr>'+Object.entries(s.temps).map(([k,v])=>{
   const val = s.health[k]&&v!=null? v.toFixed(1)+'°'+u : '<span class=fail>FAIL</span>';
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
 let c; try{ c = await j('/api/calibration'); }catch(e){ return; }
 const boil=parseFloat(boilC.value)||99.4;
 cal.innerHTML='<tr><th>Channel</th><th>gain</th><th>offset</th><th>captures</th><th></th></tr>'+
  Object.entries(c).map(([role,d])=>{
   const cap=d.captures.map(p=>`${p[0]}→${p[1]}`).join(', ');
   return `<tr><td>${role}${d.custom?' *':''}</td><td>${d.gain}</td><td>${d.offset}</td>`+
    `<td class=muted>${cap}</td><td>`+
    `<button onclick="capture('${role}',0)">Ice 0°</button>`+
    `<button onclick="capture('${role}',${boil})">Boil</button>`+
    `<button onclick="resetCal('${role}')">Reset</button></td></tr>`;
  }).join('');
}
refresh(); refreshCal(); setInterval(refresh,2000);
</script>
"""
