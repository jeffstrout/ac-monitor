"""The single-page dashboard + control panel (served at ``/``).

Responsive: fluid layout, touch-friendly controls, horizontally scrollable
tables on narrow screens. Styled from the shared homelab design tokens
(``/static/tokens.css``); everything app-specific stays inline here, so the
appliance still deploys as one file plus one stylesheet.

Light only, per homelab-standards — one palette means one set of contrast
decisions to verify, and this panel is read in a lit plant room.
"""

DASHBOARD = """<!doctype html>
<title>AC Monitor</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="stylesheet" href="/static/tokens.css">
<style>
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{font:var(--hl-text-base)/var(--hl-leading) var(--hl-font-sans);margin:0;
      background:var(--hl-canvas);color:var(--hl-fg)}
 .wrap{max-width:760px;margin:0 auto;
       padding:var(--hl-space-4) max(var(--hl-space-4),env(safe-area-inset-right))
               var(--hl-space-6) max(var(--hl-space-4),env(safe-area-inset-left))}
 h1{font-size:var(--hl-text-lg);margin:var(--hl-space-1) 0;font-weight:var(--hl-weight-semibold)}
 h2{font-size:var(--hl-text-xs);text-transform:uppercase;letter-spacing:.05em;
    color:var(--hl-fg-muted);margin:var(--hl-space-6) 0 var(--hl-space-3)}

 /* Headline metric: air-side ΔT is what this appliance exists to report. */
 .hero{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--hl-space-2) var(--hl-space-4);
       margin:var(--hl-space-3) 0 var(--hl-space-4);padding:var(--hl-space-4);
       background:var(--hl-surface);border:1px solid var(--hl-border);
       border-radius:var(--hl-radius)}
 .big{font-family:var(--hl-font-mono);font-variant-numeric:tabular-nums;
      font-size:clamp(2rem,11vw,2.6rem);font-weight:var(--hl-weight-semibold);
      line-height:1;letter-spacing:-.02em}
 .muted{color:var(--hl-fg-muted)}
 .ok{color:var(--hl-ok)} .fault,.fail{color:var(--hl-crit);font-weight:var(--hl-weight-semibold)}
 .status{display:flex;flex-wrap:wrap;gap:var(--hl-space-2) var(--hl-space-5);
         align-items:center;margin:var(--hl-space-2) 0}

 .scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;
         border:1px solid var(--hl-border);border-radius:var(--hl-radius);
         background:var(--hl-surface)}
 table{border-collapse:collapse;width:100%;min-width:300px}
 td,th{padding:var(--hl-space-3);border-bottom:1px solid var(--hl-border);
       text-align:left;white-space:nowrap;font-variant-numeric:tabular-nums}
 th{background:var(--hl-canvas);font-size:var(--hl-text-xs);
    font-weight:var(--hl-weight-semibold);letter-spacing:var(--hl-tracking-label);
    color:var(--hl-fg-muted)}
 tr:last-child td{border-bottom:0}

 button{font:var(--hl-weight-semibold) var(--hl-text-sm)/1 var(--hl-font-sans);
        min-height:44px;padding:var(--hl-space-2) var(--hl-space-4);margin:2px 0;
        border:1px solid var(--hl-border);border-radius:var(--hl-radius);
        background:var(--hl-surface);color:var(--hl-fg);cursor:pointer;
        touch-action:manipulation}
 button:hover{background:var(--hl-sunken)}
 button:active{transform:translateY(1px)}
 /* Primary action per group; Capture overwrites a calibration earned with a
    thermometer, so it stays secondary on purpose. */
 button.primary{background:var(--hl-accent);border-color:var(--hl-accent);
                color:var(--hl-fg-on-accent)}
 button.primary:hover{background:var(--hl-accent-hover)}

 .row{display:flex;flex-wrap:wrap;gap:var(--hl-space-3);align-items:end}
 .field{display:flex;flex-direction:column;gap:var(--hl-space-1);
        font-size:var(--hl-text-xs);font-weight:var(--hl-weight-semibold);
        letter-spacing:var(--hl-tracking-label);color:var(--hl-fg-muted)}
 input{font:var(--hl-text-base)/1 var(--hl-font-sans);min-height:44px;
       padding:var(--hl-space-2);border:1px solid var(--hl-border);
       border-radius:var(--hl-radius-sm);background:var(--hl-surface);
       color:var(--hl-fg);width:100%}
 input:focus{border-color:var(--hl-accent);box-shadow:var(--hl-focus-ring);outline:none}
 .field input{width:9rem} .field.sm input{width:5rem}

 /* Pills: coloured text on a wash, plus a dot — state never rests on hue
    alone. Toggles use the ACCENT, not green: an output you switch is
    interaction, and green is reserved for health. */
 .pill{display:inline-flex;align-items:center;gap:var(--hl-space-2);
       border-radius:var(--hl-radius-pill);padding:5px var(--hl-space-3);
       font-size:12px;font-weight:var(--hl-weight-semibold);white-space:nowrap;
       border:1px solid currentColor}
 .pill::before{content:"";width:6px;height:6px;border-radius:var(--hl-radius-pill);
               background:currentColor;flex:none}
 .on{color:var(--hl-accent);background:var(--hl-accent-wash)}
 .off{color:var(--hl-fg-muted);background:var(--hl-sunken)}
 /* system_status is an operating MODE (Cooling/Heating/Fan/Idle), not health —
    none of those values is a fault, so it is informational, never red. */
 .sys{color:var(--hl-info);background:var(--hl-info-wash)}
 /* Health is separate: derived from the I2C bus and the active faults. */
 .health-ok{color:var(--hl-ok);background:var(--hl-ok-wash)}
 .health-bad{color:var(--hl-crit);background:var(--hl-crit-wash)}

 .toggle{display:flex;flex-wrap:wrap;align-items:center;gap:var(--hl-space-3);
         margin:var(--hl-space-3) 0;font-size:var(--hl-text-sm)}
 .capcell{display:flex;flex-wrap:wrap;gap:var(--hl-space-1);align-items:center}
 .capcell input{width:4.5rem}
 @media (max-width:520px){
   .wrap{padding:var(--hl-space-3)}
   .field{flex:1 1 45%} .field input,.field.sm input{width:100%}
   td,th{padding:var(--hl-space-2)}
 }
</style>
<div class="wrap">
<h1>AC Monitor</h1>

<div class="hero">
 <span class="big" id="dt">–</span>
 <span class="muted">air-side ΔT <span id="mode"></span></span>
 <span class="pill sys" id="sysStatus">–</span>
 <span class="pill health-ok" id="healthPill">–</span>
</div>
<div class="scroll"><table id="temps"></table></div>
<p class="status">Fan: <b id="fan">–</b> <span>Bus: <b id="bus">–</b></span></p>
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
 <button class="primary" onclick="saveMqtt()">Save broker</button>
</div>

<h2>Calibration</h2>
<p class="muted">Dip a probe in water at a known temperature, read it with a good
 thermometer, type that value into the channel's box and press Capture. Two
 well-separated captures (e.g. cold and hot water) fit that channel's gain/offset
 — they don't have to be exactly ice or boiling.</p>
<div class="scroll"><table id="cal"></table></div>

<p class="muted" id="foot"></p>
</div>
<script>
const LABELS={output_air:'Supply_Air',input_air:'Return_Air',suction_line:'Suction_Line',liquid_line:'Liquid_Line'};
const label=k=>LABELS[k]||k;
async function j(u,o){ return (await fetch(u,o)).json(); }
async function post(u,b){ return j(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}); }
async function toggle(w){ await post('/api/toggle/'+w,{}); refresh(); }
async function saveMqtt(){ await post('/api/mqtt/config',{host:mqttHost.value,port:parseInt(mqttPort.value)||1883,username:mqttUser.value,password:mqttPass.value}); refresh(); }
let curUnit='F';
async function captureEntered(role){
 const el=document.getElementById('cap_'+role);
 const val=parseFloat(el.value);
 if(isNaN(val)){ el.focus(); return; }
 const kc=(curUnit==='F')?(val-32)*5/9:val;   // entered value is in the display unit -> °C
 await post('/api/calibrate/capture',{role:role,known_c:kc});
 el.value=''; refreshCal();
}
async function resetCal(role){ await post('/api/calibrate/reset',{role:role}); refreshCal(); }
function pill(el,on){ el.className='pill '+(on?'on':'off'); el.textContent=on?'ON':'OFF'; }
function sgn(x){ return (x>=0?'+':'')+x.toFixed(1); }   // explicit +/- sign

async function refresh(){
 let s; try{ s=await j('/api/state'); }catch(e){ return; }
 const u=s.unit; curUnit=u;
 const dtCell = s.delta_t==null?'–':sgn(s.delta_t)+'°'+u;
 dt.textContent = dtCell;
 mode.textContent = s.mode?('('+s.mode+')'):'';
 sysStatus.textContent = s.system_status||'–';
 // Health is the bus plus any active fault — NOT system_status, which is only
 // the operating mode (Cooling/Heating/Fan/Idle) and is never itself a problem.
 const activeFaults=Object.entries(s.faults).filter(([k,v])=>v).map(([k])=>k);
 const healthy = s.i2c_ok && activeFaults.length===0;
 healthPill.textContent = healthy?'Healthy':(s.i2c_ok?'Fault':'Bus down');
 healthPill.className = 'pill '+(healthy?'health-ok':'health-bad');
 temps.innerHTML='<tr><th>Channel</th><th>Temp</th></tr>'+Object.entries(s.temps).map(([k,v])=>{
   const val=s.health[k]&&v!=null? sgn(v)+'°'+u : '<span class=fail>FAIL</span>';
   let row=`<tr><td>${label(k)}</td><td>${val}</td></tr>`;
   if(k==='input_air') row+=`<tr><td>ΔT</td><td><b>${dtCell}</b></td></tr>`;   // ΔT after return air
   return row;}).join('');
 fan.textContent = s.fan_running==null?'FAIL':(s.fan_running?'RUNNING':'IDLE');
 bus.innerHTML = s.i2c_ok?'<span class=ok>OK</span>':'<span class=fault>DOWN</span>';
 faults.innerHTML = activeFaults.length
   ? '<span class=fault>Faults: '+activeFaults.join(', ')+'</span>'
   : '<span class=ok>No faults</span>';
 pill(displayState,s.toggles.display_push); pill(mqttState,s.toggles.mqtt);
 const v=s.version||{}, t=s.last_poll_at?new Date(s.last_poll_at*1000).toLocaleTimeString():'–';
 foot.textContent=`updated ${t} · poll #${s.poll_count} · build ${v.commit||''}`;
}
async function refreshCal(){
 let c; try{ c=await j('/api/calibration'); }catch(e){ return; }
 cal.innerHTML='<tr><th>Channel</th><th>gain</th><th>offset</th><th>captures °C</th><th>actions</th></tr>'+
  Object.entries(c).map(([role,d])=>{
   const cap=d.captures.map(p=>`${p[0]}→${p[1]}`).join(', ');
   return `<tr><td>${label(role)}${d.custom?' *':''}</td><td>${d.gain}</td><td>${d.offset}</td>`+
    `<td class=muted>${cap}</td><td><div class=capcell>`+
    `<input id="cap_${role}" placeholder="°${curUnit}">`+
    `<button onclick="captureEntered('${role}')">Capture</button>`+
    `<button onclick="resetCal('${role}')">Reset</button></div></td></tr>`;}).join('');
}
refresh(); refreshCal(); setInterval(refresh,2000);
</script>
"""
