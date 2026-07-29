"""The single-page dashboard + control panel (served at ``/``).

Built from the shared homelab component sheet (``/static/tokens.css`` +
``/static/components.css``) — the shell header, the page column, section
headings, the headline banner, pills, tables, buttons and inputs all come from
there, so this appliance looks like the rest of the fleet rather than merely
sharing its palette. Only genuinely app-specific styling stays inline.

Responsive behaviour is the shared sheet's too: fluid layout, safe-area insets,
horizontally scrollable tables, and touch targets gated on
``@media (pointer: coarse)`` rather than viewport width. This file used to own
all of that and applied the 44px floor unconditionally, which got the phone
right and cost the desktop density.

Light only, per homelab-standards — one palette means one set of contrast
decisions to verify, and this panel is read in a lit plant room.
"""

DASHBOARD = """<!doctype html>
<meta charset="utf-8">
<title>AC Monitor</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="stylesheet" href="/static/tokens.css">
<link rel="stylesheet" href="/static/components.css">
<style>
 /* App-specific only. Anything that exists in components.css must not be
    re-declared here — keeping a local copy alongside the shared one is exactly
    how the three appliances drifted apart in the first place. */
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{font:var(--hl-text-base)/var(--hl-leading) var(--hl-font-sans);margin:0;
      background:var(--hl-canvas);color:var(--hl-fg)}

 /* The page column, section headings, the headline banner, touch targets and
    the narrow-screen table padding all come from components.css now. The
    safe-area insets this file worked out first moved INTO .hl-page, so every
    appliance gets them.

    The 44px floor moved too, and got a better predicate: it was unconditional
    here, which made a desktop pay for touch density. The shared sheet gates it
    on @media (pointer: coarse) — a narrow desktop window is not a touchscreen,
    a landscape tablet is one. That also fixes the input font size: 13px made
    mobile Safari zoom the viewport on focus and never zoom back out, which hit
    every field on this panel on the exact device it is used from. */
 /* Spacing between the banner and the temps table under it — sibling layout,
    not a re-declaration of the shared component's appearance. */
 #hero{margin-bottom:var(--hl-space-4)}
 .muted{color:var(--hl-fg-muted)}
 .ok{color:var(--hl-ok)} .fault,.fail{color:var(--hl-crit);font-weight:var(--hl-weight-semibold)}
 .status{display:flex;flex-wrap:wrap;gap:var(--hl-space-2) var(--hl-space-5);
         align-items:center;margin:var(--hl-space-2) 0}

 .row{display:flex;flex-wrap:wrap;gap:var(--hl-space-3);align-items:end}
 .field{display:flex;flex-direction:column;gap:var(--hl-space-1)}
 .field .hl-input{width:9rem} .field.sm .hl-input{width:5rem}

 .toggle{display:flex;flex-wrap:wrap;align-items:center;gap:var(--hl-space-3);
         margin:var(--hl-space-3) 0;font-size:var(--hl-text-sm)}
 .capcell{display:flex;flex-wrap:wrap;gap:var(--hl-space-1);align-items:center}
 .capcell .hl-input{width:4.5rem}
 @media (max-width:520px){
   .field{flex:1 1 45%} .field .hl-input,.field.sm .hl-input{width:100%}
   /* The actions column — an input plus two buttons — was what forced the
      calibration table to scroll on every phone visit. Stacking it lets the
      table's real content set the width instead. */
   .capcell{flex-direction:column;align-items:stretch}
   .capcell .hl-input{width:100%}
 }
</style>

<header class="hl-header">
 <h1 class="hl-header-name">AC Monitor</h1>
 <span class="hl-pill hl-pill--ok" id="healthPill">–</span>
 <span class="hl-header-spacer"></span>
 <nav class="hl-header-nav">
  <!-- /api/health, not the deprecated /healthz alias — the nav should teach the
       contract spelling. Opened in a new tab so a JSON detour doesn't cost you
       the panel you were reading. -->
  <a class="hl-header-link" href="/api/health" target="_blank" rel="noopener">Health</a>
  <a class="hl-header-link" href="/docs" target="_blank" rel="noopener">API&nbsp;docs</a>
  <a class="hl-header-link" href="/api/state" target="_blank" rel="noopener">State&nbsp;JSON</a>
 </nav>
</header>

<div class="hl-page">
<!-- Headline metric: air-side ΔT is what this appliance exists to report. It is
     the same shape as syslog's summary block — the thing you read before you
     read anything else — so it is .hl-banner now rather than a local .hero. -->
<section class="hl-section">
<div class="hl-banner hl-banner--row" id="hero">
 <span class="hl-banner-metric" id="dt">–</span>
 <span class="muted">air-side ΔT <span id="mode"></span></span>
 <span class="hl-pill hl-pill--info" id="sysStatus">–</span>
 <span class="muted" id="modeSrc"></span>
</div>
<div class="hl-table-wrap"><table class="hl-table" id="temps"></table></div>
<p class="status">Fan: <b id="fan">–</b> <span>Bus: <b id="bus">–</b></span></p>
<p id="faults"></p>
</section>

<section class="hl-section">
<h2 class="hl-section-title">Outputs</h2>
<div class="toggle">Split-flap display (slot 2):
 <span id="displayState" class="hl-pill hl-pill--info">?</span>
 <button class="hl-btn" onclick="toggle('display')">Toggle</button></div>
<div class="toggle">MQTT output:
 <span id="mqttState" class="hl-pill hl-pill--info">?</span>
 <button class="hl-btn" onclick="toggle('mqtt')">Toggle</button></div>
<div class="row">
 <label class="field"><span class="hl-label">host</span><input class="hl-input" id="mqttHost"></label>
 <label class="field sm"><span class="hl-label">port</span><input class="hl-input" id="mqttPort" value="1883"></label>
 <label class="field"><span class="hl-label">user</span><input class="hl-input" id="mqttUser"></label>
 <label class="field"><span class="hl-label">pass</span><input class="hl-input" id="mqttPass" type="password"></label>
 <button class="hl-btn hl-btn--primary" onclick="saveMqtt()">Save broker</button>
</div>
</section>

<section class="hl-section">
<h2 class="hl-section-title">Calibration</h2>
<p class="hl-note">Dip a probe in water at a known temperature, read it with a good
 thermometer, type that value into the channel's box and press Capture. Two
 well-separated captures (e.g. cold and hot water) fit that channel's gain/offset
 — they don't have to be exactly ice or boiling.</p>
<div class="hl-table-wrap"><table class="hl-table" id="cal"></table></div>
</section>
</div>

<!-- Provenance, where every appliance carries it (homelab-standards#8). The
     commit used to sit right-most in the header; the header is the row you
     scan, and "which build is this" is a question you go looking for once.
     Pairing it with the poll line puts liveness and provenance together —
     "is the box alive" and "is it current" are the same investigation. -->
<footer class="hl-footer">
 <span id="foot"></span>
 <span class="hl-footer-spacer"></span>
 <span class="hl-footer-meta" id="build"></span>
</footer>
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
// An output's ON/OFF is the STATE of that output, so it takes a semantic
// colour. It used to take the accent, on the reasoning that "an output you
// switch is interaction" — but the pill isn't the interaction, the Toggle
// button beside it is. Accent on a pill makes four things look actionable when
// only one is (homelab-standards style-guide.md).
function pill(el,on){
 el.className='hl-pill '+(on?'hl-pill--ok':'hl-pill--info');
 el.textContent=on?'ON':'OFF';
}
function sgn(x){ return (x>=0?'+':'')+x.toFixed(1); }   // explicit +/- sign

async function refresh(){
 let s; try{ s=await j('/api/state'); }catch(e){ return; }
 const u=s.unit; curUnit=u;
 const dtCell = s.delta_t==null?'–':sgn(s.delta_t)+'°'+u;
 dt.textContent = dtCell;
 mode.textContent = s.mode?('('+s.mode+')'):'';
 sysStatus.textContent = s.system_status||'–';
 // An operator must be able to tell a reported fact from an inferred guess.
 const SRC={home_assistant:'reported by thermostat',inferred:'inferred from ΔT',
            unavailable:'thermostat unreachable'};
 modeSrc.textContent = SRC[s.mode_source]||'';
 // Cooling/Heating/Fan/Idle is an operating MODE, not health — none of those
 // values is a fault, so it stays neutral. --hl-info is a slate, deliberately
 // not the accent blue it used to be: a status pill must never look like a
 // button (homelab-standards#4).
 sysStatus.className = 'hl-pill hl-pill--'+(s.mode_source==='unavailable'?'warn':'info');
 // Health is the bus plus any active fault — NOT system_status, which is only
 // the operating mode (Cooling/Heating/Fan/Idle) and is never itself a problem.
 const activeFaults=Object.entries(s.faults).filter(([k,v])=>v).map(([k])=>k);
 const healthy = s.i2c_ok && activeFaults.length===0;
 healthPill.textContent = healthy?'Healthy':(s.i2c_ok?'Fault':'Bus down');
 healthPill.className = 'hl-pill hl-pill--'+(healthy?'ok':'crit');
 temps.innerHTML='<tr><th>Channel</th><th class="hl-num">Temp</th></tr>'+Object.entries(s.temps).map(([k,v])=>{
   const val=s.health[k]&&v!=null? sgn(v)+'°'+u : '<span class=fail>FAIL</span>';
   let row=`<tr><td>${label(k)}</td><td class="hl-num">${val}</td></tr>`;
   if(k==='input_air') row+=`<tr><td>ΔT</td><td class="hl-num"><b>${dtCell}</b></td></tr>`;   // ΔT after return air
   return row;}).join('');
 fan.textContent = s.fan_running==null?'FAIL':(s.fan_running?'RUNNING':'IDLE');
 bus.innerHTML = s.i2c_ok?'<span class=ok>OK</span>':'<span class=fault>DOWN</span>';
 faults.innerHTML = activeFaults.length
   ? '<span class=fault>Faults: '+activeFaults.join(', ')+'</span>'
   : '<span class=ok>No faults</span>';
 pill(displayState,s.toggles.display_push); pill(mqttState,s.toggles.mqtt);
 const t=s.last_poll_at?new Date(s.last_poll_at*1000).toLocaleTimeString():'–';
 foot.textContent=`updated ${t} · poll #${s.poll_count}`;
}
// The running build, in the footer where every appliance now carries it
// (homelab-standards#8). This read `s.version` from /api/state, which has no
// such key — snapshot() never exposed one — so the line rendered "build " and
// nothing else for its entire life. /api/version is the only way to confirm a
// Watchtower update landed, so it silently failed at the one job it had.
// Fetched once: it cannot change without the process restarting.
async function loadBuild(){
 try{
  const v=await j('/api/version');
  // Date only — this is provenance you check, not read, and the commit is the
  // part that identifies the build.
  build.textContent=[v.commit,(v.built_at||'').slice(0,10)].filter(Boolean).join(' · ')||'dev';
 }catch(e){ build.textContent=''; }
}
async function refreshCal(){
 let c; try{ c=await j('/api/calibration'); }catch(e){ return; }
 cal.innerHTML='<tr><th>Channel</th><th class="hl-num">gain</th><th class="hl-num">offset</th><th>captures °C</th><th>actions</th></tr>'+
  Object.entries(c).map(([role,d])=>{
   const cap=d.captures.map(p=>`${p[0]}→${p[1]}`).join(', ');
   return `<tr><td>${label(role)}${d.custom?' *':''}</td><td class="hl-num">${d.gain}</td><td class="hl-num">${d.offset}</td>`+
    `<td class=muted>${cap}</td><td><div class=capcell>`+
    `<input class="hl-input" id="cap_${role}" placeholder="°${curUnit}">`+
    `<button class="hl-btn" onclick="captureEntered('${role}')">Capture</button>`+
    `<button class="hl-btn" onclick="resetCal('${role}')">Reset</button></div></td></tr>`;}).join('');
}
refresh(); refreshCal(); loadBuild(); setInterval(refresh,2000);
</script>
"""
