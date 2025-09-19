let selectedPIDs = [];          // array of python-OBD command names (e.g., "RPM", "SPEED")
let supportedNames = [];        // full list from backend
let sidebarOpen = false;
let lastState = null;

function titleizeName(name) {
  // Turn "FUEL_TRIM_SHORT_BANK1" -> "Fuel Trim Short Bank1"
  return (name || "").toLowerCase().replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function makeCard(key,label,value,suffix=""){
  const w=document.createElement("div"); w.className="card";
  w.innerHTML=`<div class="label">${label}</div><div class="value" id="${key}">${(value ?? "—")}${suffix}</div>`;
  return w;
}

function renderCards(state){
  const cards=document.getElementById("cards"); cards.innerHTML="";
  // Always show VIN & adapter status
  cards.appendChild(makeCard("vin","VIN",state.vin || "—"));
  (selectedPIDs||[]).forEach(name=>{
    // read from dyn_values
    const val = (state.dyn_values||{})[name];
    cards.appendChild(makeCard(name, titleizeName(name), val));
  });
  const adapter=document.createElement("div"); adapter.className="card";
  adapter.innerHTML=`<div class="label">Adapter</div>
    <div class="${state.adapter_ok?"pill":"pill err"}">${state.adapter_ok?"Connected":"Not connected"}</div>
    <div class="muted" id="err">${state.last_error||""}</div>`;
  cards.appendChild(adapter);
  const sc=document.getElementById("suppCount"); if(sc) sc.textContent = state.supported_count ?? 0;
}

function renderDTCs(state){
  const mil = state.mil;
  const cnt = state.dtc_count_reported;
  const line = document.getElementById("milLine");
  line.textContent = `MIL: ${mil === true ? "ON" : mil === false ? "OFF" : "—"} • ECU-reported DTC count: ${Number.isInteger(cnt) ? cnt : "—"}`;

  function renderList(elId, list){
    const el = document.getElementById(elId);
    if (!list || !list.length){ el.textContent = "None"; return; }
    el.innerHTML = "";
    list.forEach(item => {
      const code = item.code || item;
      const desc = item.desc || "";
      const row = document.createElement("div");
      row.className = "dtc-row";
      row.innerHTML = `<span class="code"><strong>${code}</strong></span>${desc ? " — " + desc : ""}`;
      el.appendChild(row);
    });
  }

  renderList("dtc-stored",    state.dtc_list_stored || state.dtcs_stored);
  renderList("dtc-pending",   state.dtc_list_pending || state.dtcs_pending);
  renderList("dtc-permanent", state.dtc_list_permanent || state.dtcs_permanent);

  const ffBox = document.getElementById("ff");
  const ff = state.last_freeze_frame || {};
  ffBox.innerHTML = Object.keys(ff).length
    ? `<div class="ff"><div class="label">Freeze Frame</div><pre style="margin:0;white-space:pre-wrap;">${JSON.stringify(ff, null, 2)}</pre></div>`
    : "";
}

function toggleSidebar(){
  sidebarOpen = !sidebarOpen;
  const sb = document.getElementById("sidebar");
  sb.style.display = sidebarOpen ? "block":"none";
  if (sidebarOpen && lastState){
    supportedNames = lastState.supported_names || supportedNames || [];
    selectedPIDs = lastState.selected_pids || selectedPIDs || [];
    buildPIDList(); // (re)build with current state
  }
}

function buildPIDList(filter=""){
  const holder = document.getElementById("pid-list");
  holder.innerHTML = "";
  const q = (filter || "").trim().toLowerCase();
  const list = supportedNames.filter(n => !q || n.toLowerCase().includes(q));
  if (!list.length){
    holder.innerHTML = `<div class="muted" style="padding:8px;">No matches</div>`;
    return;
  }
  list.sort();
  list.forEach(n=>{
    const id = `pid_${n}`;
    const checked = selectedPIDs.includes(n);
    const row = document.createElement("label");
    row.className = "chk wide";
    row.innerHTML = `<input type="checkbox" id="${id}" data-name="${n}" ${checked?"checked":""}/>
                     <span><strong>${n}</strong> — ${titleizeName(n)}</span>`;
    holder.appendChild(row);
  });
}

function filterPIDList(){
  const q = document.getElementById("pidSearch").value || "";
  buildPIDList(q);
}

function selectAllVisible(){
  const checks=document.querySelectorAll('#pid-list input[type="checkbox"]');
  checks.forEach(c=>{ c.checked = true; });
}
function clearAllVisible(){
  const checks=document.querySelectorAll('#pid-list input[type="checkbox"]');
  checks.forEach(c=>{ c.checked = false; });
}

async function savePIDs(){
  // read visible ticks; merge with previously selected items that are not visible under current filter
  const visible=document.querySelectorAll('#pid-list input[type="checkbox"]');
  const set=new Set(selectedPIDs); // start with existing
  // First: remove all visible names, then re-add based on check state (so visibility acts as editable slice)
  visible.forEach(c=>{ set.delete(c.dataset.name); });
  visible.forEach(c=>{ if(c.checked){ set.add(c.dataset.name); } });
  const picked = Array.from(set).sort();

  try{
    const r=await fetch("/set-pids",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({selected_pids:picked})});
    const d=await r.json();
    if(d.ok){
      selectedPIDs=d.selected_pids;
      toggleSidebar(); // close
    } else {
      alert("Failed to save PIDs: "+(d.error||"unknown"));
    }
  }catch(e){
    alert("Failed to save PIDs: "+e.message);
  }
}

let refreshTimer=null;
async function refresh(){
  try{
    const r=await fetch("/api/state");
    const state=await r.json();
    lastState = state;

    // Update our local caches unless sidebar is open (don’t stomp user edits)
    if (!sidebarOpen){
      supportedNames = state.supported_names || supportedNames || [];
      selectedPIDs   = state.selected_pids   || selectedPIDs   || [];
    }

    renderCards(state);
    renderDTCs(state);
  }catch(e){
    console.error(e);
  }finally{
    refreshTimer = setTimeout(refresh,1000);
  }
}

refresh();
