const el = id => document.getElementById(id);
const names = {CERRADA:'Cerrada',ABRIENDO:'Abriendo',APERTURA_ORDENADA:'Apertura ordenada',ABIERTA:'Abierta',CERRANDO:'Cerrando',ALERTA:'Cierre sin confirmar',FALLO:'Revisar cochera',CALIBRACION:'En calibración',DESCONOCIDA:'Posición sin confirmar'};
const hints = {APERTURA_ORDENADA:'Ángulo enviado; no hay sensor de apertura completa.',ABIERTA:'El servo terminó de abrir; esperando al carro.',ALERTA:'El reed no detecta el imán; revisa la puerta.',FALLO:'Consulta el monitor serie y revisa los sensores.'};
const emptyLabel = {exterior:'Libre',interior:'Vacío'};
const state = {socket:null,connected:false,lastMessage:0,broker:false,nodes:[],selected:'',history:[],latest:null,retry:0,staleMs:7000,stats:{}};
const text = (id, value) => {el(id).textContent = value;};
function badge(id, value, kind='') {text(id,value);el(id).className = id === 'serverBadge' ? `badge ${kind}` : `small-badge ${kind}`;}
function currentNode() {return state.nodes.find(n=>n.node_id===state.selected);}
function notice(message) {el('notice').hidden=!message;text('notice',message);}

function render() {
  const n = currentNode(), data = state.latest;
  const online = Boolean(state.connected && state.broker && n?.online && Date.now()-state.lastMessage < 5000);
  badge('serverBadge', !state.connected ? 'Sin servidor' : !state.broker ? 'Sin broker' : online ? 'En vivo' : 'Sin señal', online?'online':'offline');
  if(!state.connected) notice('Sin conexión con el servidor. Se muestran las últimas lecturas; intentando reconectar.');
  else if(!state.broker) notice('El servidor está disponible, pero el broker MQTT está desconectado.');
  else if(!data) notice('Esperando telemetría. Puedes iniciar el simulador mientras reparas la maqueta.');
  else if(!online) notice('El dispositivo no está enviando datos recientes. Los valores visibles corresponden a la última lectura guardada.');
  else if(data.source==='simulation') notice('Modo demostración: estas lecturas son simuladas, no provienen de la maqueta.');
  else notice('');
  document.querySelector('.sensor-stack').classList.toggle('muted-values',!online);
  text('deviceStatus',online?'Conectado':data?'Sin señal':'Esperando');
  if(!data) return;
  text('sourceBadge',data.source==='simulation'?'SIMULACIÓN':'MAQUETA');
  el('sourceBadge').className=`source ${data.source==='simulation'?'simulation':''}`;
  text('doorState',names[data.door.state] || data.door.state);
  text('doorHint',hints[data.door.state] || 'Último estado recibido de la cochera.');
  text('mode',data.door.mode==='AUTO'?'Automático':'Manual');
  text('reed',data.door.reed_closed?'Reed confirma cerrado':'Reed: no cerrada');
  el('reedDot').classList.toggle('active',data.door.reed_closed);
  text('angle',`${data.door.servo_command_deg}° ordenados`);
  el('doorDrawing').setAttribute('opacity',['APERTURA_ORDENADA','ABIERTA'].includes(data.door.state)?'0.16':'1');
  el('carDrawing').setAttribute('opacity',data.vehicle.inside?'1':'0');
  for(const side of ['exterior','interior']) {
    const r=data.readings[side], empty=!r.valid && r.error==='SIN_OBJETO';
    text(`${side}Value`,r.valid?r.filtered_mm.toLocaleString('es-MX',{maximumFractionDigits:1}):empty?emptyLabel[side]:'—');
    el(`${side}Value`).nextElementSibling.hidden=!r.valid;
    text(`${side}Raw`,r.valid?`Crudo: ${r.raw_mm} mm`:empty?'Sin objeto en alcance':`${r.error || 'Inválido'}`);
    badge(`${side}Validity`,r.valid?'Válido':empty?'Sin objeto':'Inválido',r.valid || empty?'valid':'invalid');
  }
  text('exteriorZone',data.vehicle.exterior_clear?'Entrada libre':'Libre no confirmado');
  text('interiorZone',data.vehicle.inside?'Carro detectado':'Presencia no confirmada');
  if(data.tag) {
    text('linkTitle','TAG DEL VEHÍCULO');
    text('tag',data.tag.present?'Detectado':'No detectado');
    text('rssi',`ESP32-C3 · último RSSI: ${data.tag.rssi_dbm} dBm`);
  } else {
    const net=data.network;
    text('linkTitle','GATEWAY ESP32-C3');
    text('tag',net.wifi_connected?`WiFi ${net.wifi_rssi_dbm ?? '—'} dBm`:'Sin WiFi');
    text('rssi',`MQTT QoS ${net.qos ?? '—'} · publish() ${net.publish_us ?? '—'} µs`);
  }
  const s=state.stats[data.node_id];
  text('loss',s?`${s.loss_pct.toLocaleString('es-MX')} % pérdida`:'—');
  text('jitter',s?`Jitter ${s.jitter_ms ?? '—'} ms · cada ${s.mean_interval_ms ?? '—'} ms · ${s.received} recibidos`:'Sin estadísticas todavía');
  const age=Math.max(0,(n?.age_ms || 0)+Date.now()-(state.snapshotAt || Date.now()));
  text('lastSeen',`Última lectura hace ${Math.floor(age/1000)} s`);
  text('sequence',`#${data.sequence_id}`);
  text('clock',data.time_synced?'Hora sincronizada':'Sin NTP · hora de recepción disponible');
  el('showPayload').disabled=false;
}

function svgElement(name,attrs={},content) {
  const node=document.createElementNS('http://www.w3.org/2000/svg',name);
  for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
  if(content!==undefined) node.textContent=content;
  return node;
}
function chart(id,side) {
  const svg=el(id);svg.replaceChildren();
  const rows=state.history.slice(-120), all=rows.flatMap(d=>{const r=d.readings[side];return r.valid?[r.raw_mm,r.filtered_mm]:[];});
  const max=Math.max(100,Math.ceil(Math.max(0,...all)/100)*100);
  for(let i=0;i<4;i++) {const y=16+i*48;svg.append(svgElement('line',{x1:43,y1:y,x2:522,y2:y,stroke:'#e8ece5'}));svg.append(svgElement('text',{x:35,y:y+4,'text-anchor':'end',fill:'#92998f','font-size':10},Math.round(max*(1-i/3))));}
  if(!rows.length){svg.append(svgElement('text',{x:283,y:96,'text-anchor':'middle',fill:'#92998f','font-size':13},'Tus lecturas aparecerán aquí'));return;}
  for(const kind of ['raw_mm','filtered_mm']) {
    let path='',segment=false;
    const first=rows[0].received_at,span=Math.max(1,rows[rows.length-1].received_at-first);
    rows.forEach((d,i)=>{const r=d.readings[side];if(!r.valid){segment=false;return;}if(i && (d.boot_id!==rows[i-1].boot_id || d.received_at-rows[i-1].received_at>4000))segment=false;const x=43+(d.received_at-first)*479/span,y=160-144*r[kind]/max;path+=`${segment?'L':'M'}${x.toFixed(1)},${y.toFixed(1)} `;segment=true;});
    svg.append(svgElement('path',{d:path,fill:'none',stroke:kind==='raw_mm'?'#a5afa2':'#285c40','stroke-width':kind==='raw_mm'?1.5:2.4,'stroke-dasharray':kind==='raw_mm'?'4 4':'none'}));
    if(rows.length===1 && rows[0].readings[side].valid)svg.append(svgElement('circle',{cx:43,cy:160-144*rows[0].readings[side][kind]/max,r:3,fill:'#285c40'}));
  }
  for(const [row,x,anchor] of [[rows[0],43,'start'],[rows[rows.length-1],522,'end']])svg.append(svgElement('text',{x,y:184,'text-anchor':anchor,fill:'#92998f','font-size':10},new Date(row.received_at).toLocaleTimeString('es-MX')));
}
function renderHistory() {
  chart('chartExterior','exterior');chart('chartInterior','interior');
  const body=el('historyRows');body.replaceChildren();
  for(const d of [...state.history].reverse()) {
    const tr=document.createElement('tr');
    for(const value of [new Date(d.received_at).toLocaleTimeString('es-MX'),d.source==='simulation'?'Simulación':'Maqueta',d.sequence_id,...['exterior','interior'].map(s=>d.readings[s].valid?`${d.readings[s].filtered_mm} mm`:'Inválido'),names[d.door.state]||d.door.state]){const td=document.createElement('td');td.textContent=value;tr.append(td);}
    body.append(tr);
  }
  if(!state.history.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='Todavía no hay registros para este dispositivo.';tr.append(td);body.append(tr);}
}
async function loadHistory() {
  const selected=state.selected;if(!selected)return;
  try {
    const response=await fetch(`/api/readings?node_id=${encodeURIComponent(selected)}&limit=120`,{cache:'no-store'});
    if(!response.ok)throw new Error('Historial no disponible');
    const rows=await response.json();if(selected!==state.selected)return;
    const merged=new Map([...rows,...state.history].map(d=>[`${d.boot_id}:${d.sequence_id}`,d]));
    state.history=[...merged.values()].sort((a,b)=>a.received_at-b.received_at).slice(-120);renderHistory();
    text('historyHint','Últimos 120 registros del dispositivo seleccionado.');
  } catch {text('historyHint','No se pudo cargar el historial. Se volverá a intentar al reconectar.');}
}
function selectNode(id) {
  state.selected=id;state.history=[];state.latest=currentNode()?.latest || null;
  el('export').href=`/api/export.csv?node_id=${encodeURIComponent(id)}`;
  render();renderHistory();loadHistory();
}
function snapshot(message) {
  state.broker=message.broker_connected;state.nodes=message.nodes;state.snapshotAt=Date.now();state.staleMs=message.stale_ms;state.stats=message.stats || {};
  const options=state.nodes.map(n=>n.node_id),oldOptions=[...el('node').options].map(o=>o.value);
  if(options.join('|')!==oldOptions.join('|')){
    el('node').replaceChildren();
    if(!options.length){const option=new Option('Esperando dispositivo…','');el('node').append(option);}
    for(const id of options)el('node').append(new Option(id,id));
  }
  if(!state.selected && options.length)selectNode(state.nodes.find(n=>n.online)?.node_id || options[0]);
  el('node').value=state.selected;
  state.latest=currentNode()?.latest || state.latest;
  render();
}
function connect() {
  clearTimeout(state.retry);
  const socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws`);state.socket=socket;
  socket.onopen=()=>{state.connected=true;state.lastMessage=Date.now();loadHistory();};
  socket.onmessage=e=>{
    state.lastMessage=Date.now();
    const message=JSON.parse(e.data);
    if(message.type==='snapshot')snapshot(message);
    else if(message.type==='telemetry' && message.data.node_id===state.selected){
      const d=message.data,key=`${d.boot_id}:${d.sequence_id}`;
      if(!state.history.some(v=>`${v.boot_id}:${v.sequence_id}`===key)){state.history.push(d);state.history=state.history.slice(-120);renderHistory();}
      if(!state.latest || d.boot_id!==state.latest.boot_id || d.sequence_id>state.latest.sequence_id)state.latest=d;
      render();
    }
  };
  socket.onerror=()=>socket.close();
  socket.onclose=()=>{if(state.socket!==socket)return;state.connected=false;render();state.retry=setTimeout(connect,2000);};
}
setInterval(()=>{
  if(state.connected && Date.now()-state.lastMessage>5000){state.connected=false;state.socket.close();render();}
  else render();
},1000);
el('node').addEventListener('change',e=>selectNode(e.target.value));
document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-selected',b===button?'true':'false');});
  el('overview').hidden=button.dataset.view!=='overview';el('history').hidden=button.dataset.view!=='history';
  if(button.dataset.view==='history')loadHistory();
}));
el('showPayload').addEventListener('click',()=>{text('payloadText',JSON.stringify(state.latest,null,2));el('payloadDialog').showModal();});
el('closePayload').addEventListener('click',()=>el('payloadDialog').close());
let installPrompt;
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;el('install').hidden=false;});
el('install').addEventListener('click',async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;el('install').hidden=true;}});
if('serviceWorker' in navigator && window.isSecureContext)navigator.serviceWorker.register('/sw.js').catch(()=>{});
renderHistory();connect();
