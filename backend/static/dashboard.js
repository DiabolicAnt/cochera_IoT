// Dashboard de la practica 3: telemetria + Random Forest + control bidireccional por WebSocket.
const NODO = 'cochera-01';
const PUNTOS = 120;
const el = id => document.getElementById(id);
const PUERTA = {CERRADA: 'Cerrada', ABRIENDO: 'Se está abriendo', ABIERTA: 'Abierta', CERRANDO: 'Se está cerrando', ALERTA: 'Cierre sin confirmar'};
const CLASE_CSS = {'Libre': 'c-libre', 'Vehículo en acceso': 'c-acceso', 'Vehículo estacionado': 'c-estacionado',
  'Puerta en operación': 'c-operacion', 'Intrusión': 'c-intrusion', 'Falla de sensor': 'c-falla'};
const estado = {socket: null, ultimoMensaje: 0, ultimaTelemetria: 0, periodo: null, comandos: new Map(), ultimaClase: null};

// Una tarjeta por cada cambio de clase, con las variables que lo provocaron (maximo 12).
function cambioDeClase(rf, d, hora) {
  if (!rf.clase || rf.clase === estado.ultimaClase) return;
  estado.ultimaClase = rf.clase;
  const lista = el('historialRf');
  lista.querySelector('.vacio')?.remove();
  const li = document.createElement('li');
  li.className = CLASE_CSS[rf.clase] || '';
  const t = document.createElement('strong'); t.textContent = rf.clase;
  const a = document.createElement('span'); a.textContent = `${hora} · ${(rf.probabilidad * 100).toFixed(0)} %`;
  const b = document.createElement('span'); b.textContent = `ultra ${mm(d.ultrasonico_mm)} · ToF ${mm(d.tof_mm)}`;
  const c = document.createElement('span'); c.textContent = `reed ${d.reed_cerrado ? 'cerrado' : 'abierto'} · apertura ${d.apertura_pct} %`;
  li.append(t, a, b, c);
  lista.prepend(li);
  while (lista.children.length > 12) lista.lastElementChild.remove();
}

function grafica(id, series, opcionesY) {
  return new Chart(el(id), {
    type: 'line',
    data: {labels: [], datasets: series.map(([label, color]) => ({label, data: [], borderColor: color, backgroundColor: color,
      borderWidth: 2, pointRadius: 0, tension: 0.25, spanGaps: false}))},
    options: {animation: false, responsive: true, maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
      scales: {x: {ticks: {maxTicksLimit: 6, color: '#8a9086'}, grid: {display: false}},
               y: Object.assign({ticks: {color: '#8a9086'}, grid: {color: '#eef0ea'}}, opcionesY)},
      plugins: {legend: {labels: {boxWidth: 12, color: '#3a4238'}}}}
  });
}
const gSensores = grafica('graficaSensores', [['Ultrasónico (entrada)', '#c07a12'], ['ToF (estacionamiento)', '#1f4f78']], {min: 0, suggestedMax: 2000});
const gLatencia = grafica('graficaLatencia', [['L (ms)', '#2d6142']], {beginAtZero: true});

function agregar(chart, etiqueta, valores) {
  chart.data.labels.push(etiqueta);
  valores.forEach((v, i) => chart.data.datasets[i].data.push(v));
  if (chart.data.labels.length > PUNTOS) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(d => d.data.shift());
  }
  chart.update('none');
}

const mm = v => v === null || v === undefined ? 'inválida' : `${Math.round(v)} mm`;

function telemetria(t, conHistorial = true) {
  estado.ultimaTelemetria = Date.now();
  const hora = new Date(t.received_at).toLocaleTimeString('es-MX', {hour12: false});
  const d = t.data || {};
  agregar(gSensores, hora, [d.ultrasonico_mm ?? null, d.tof_mm ?? null]);
  if (t.latencia_ms !== null && t.latencia_ms !== undefined) agregar(gLatencia, hora, [t.latencia_ms]);
  el('valoresSensores').textContent = `ultra ${mm(d.ultrasonico_mm)} · ToF ${mm(d.tof_mm)} · reed ${d.reed_cerrado ? 'cerrado' : 'abierto'} · apertura ${d.apertura_pct ?? '—'} %`;
  el('latActual').textContent = t.latencia_ms !== null && t.latencia_ms !== undefined ? `${Math.round(t.latencia_ms)} ms` : '—';

  const rf = t.rf || {};
  el('rfClase').textContent = rf.clase || 'Modelo no cargado';
  el('rfDetalle').textContent = rf.clase ? `Probabilidad ${(rf.probabilidad * 100).toFixed(1)} % · inferencia ${rf.inferencia_us} µs` : '—';
  el('cardRf').className = `card rf ${CLASE_CSS[rf.clase] || ''}`;
  if (conHistorial) cambioDeClase(rf, d, hora);

  el('puerta').textContent = PUERTA[t.door.state] || t.door.state;
  el('puertaDetalle').textContent = `${t.door.reed_closed ? 'Reed cerrado' : 'Reed abierto'} · ${t.door.mode === 'AUTO' ? 'automático' : 'manual'} · servo ${t.door.servo_command_deg}°`;
  const dentro = t.vehicle.inside || t.vehicle.arrival;
  el('coche').textContent = dentro ? 'Hay coche dentro' : 'No hay coche dentro';
  el('coche').className = `chip ${dentro ? 'si' : ''}`;
  if (t.control) marcarPeriodo(t.control.periodo_ms);
}

function marcarPeriodo(periodo) {
  estado.periodo = periodo;
  el('periodo').textContent = `envío cada ${periodo} ms`;
  document.querySelectorAll('[data-accion="periodo"]').forEach(b => b.classList.toggle('activo', Number(b.dataset.valor) === periodo));
}

function snapshot(s) {
  el('pillBroker').textContent = s.broker_connected ? 'Broker conectado' : 'Broker desconectado';
  el('pillBroker').className = `pill ${s.broker_connected ? 'ok' : 'mal'}`;
  const nodo = (s.nodes || []).find(n => n.node_id === NODO);
  el('pillNodo').textContent = nodo?.online ? 'Cochera en vivo' : 'Cochera sin señal';
  el('pillNodo').className = `pill ${nodo?.online ? 'ok' : 'mal'}`;
  if (!estado.ultimaTelemetria && nodo) telemetria(nodo.latest);
  const st = (s.stats || {})[NODO];
  if (st) {
    el('latDetalle').textContent = `promedio ${st.latencia_prom_ms ?? '—'} ms · p95 ${st.latencia_p95_ms ?? '—'} ms`;
    el('perdida').textContent = `${st.p_pct.toFixed(2)} %`;
    el('perdidaDetalle').textContent = `${st.received} recibidos · secuencia ${st.seq_min}–${st.seq_max}`;
  } else {
    el('perdida').textContent = '—'; el('perdidaDetalle').textContent = 'Sin datos desde el último reinicio';
  }
}

function comando(c) {
  const lista = el('bitacora');
  lista.querySelector('.vacio')?.remove();
  const clave = c.id ?? `r${Date.now()}`;
  let li = estado.comandos.get(clave);
  if (!li) {
    li = document.createElement('li');
    lista.prepend(li);
    estado.comandos.set(clave, li);
  }
  const nombre = c.accion === 'periodo' ? `periodo ${c.valor} ms` : c.accion;
  const hora = new Date(c.t || Date.now()).toLocaleTimeString('es-MX', {hour12: false});
  const [texto, css] = {
    enviado: ['enviado por MQTT…', 'espera'],
    confirmado: [`confirmado por la ESP32 en ${c.rtt_ms} ms`, 'ok'],
    sin_confirmar: ['sin confirmación en 10 s', 'error'],
    rechazado: [`rechazado: ${c.detalle}`, 'error'],
  }[c.estado] || [c.estado, ''];
  li.innerHTML = '';
  const a = document.createElement('span'); a.textContent = `${hora} · #${c.id ?? '—'} ${nombre}`;
  const b = document.createElement('span'); b.className = css; b.textContent = texto;
  li.append(a, b);
}

async function cargarEventos() {
  try {
    const filas = await (await fetch(`/api/eventos?node_id=${NODO}&limit=10`, {cache: 'no-store'})).json();
    const cuerpo = el('tablaEventos');
    cuerpo.replaceChildren();
    for (const f of filas) {
      const tr = document.createElement('tr');
      const fecha = new Date(f.fecha_registro).toLocaleTimeString('es-MX', {hour12: false});
      [f.id, f.sequence_id, f.variables_json, f.clase_predicha_rf ?? '—', f.latencia_ms ?? '—', fecha].forEach((v, i) => {
        const td = document.createElement('td'); td.textContent = v; if (i === 2) td.className = 'json'; tr.append(td);
      });
      cuerpo.append(tr);
    }
    if (!filas.length) cuerpo.innerHTML = '<tr><td colspan="6" class="vacio">Sin registros todavía.</td></tr>';
  } catch { /* Se reintenta en el siguiente ciclo. */ }
}

function conectar() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  estado.socket = ws;
  ws.onopen = () => { el('pillWs').textContent = 'WebSocket conectado'; el('pillWs').className = 'pill ok'; };
  ws.onmessage = e => {
    estado.ultimoMensaje = Date.now();
    const m = JSON.parse(e.data);
    if (m.type === 'snapshot') snapshot(m);
    else if (m.type === 'telemetry' && m.data.node_id === NODO) telemetria(m.data);
    else if (m.type === 'comando') comando(m);
  };
  ws.onclose = () => {
    el('pillWs').textContent = 'WebSocket desconectado'; el('pillWs').className = 'pill mal';
    setTimeout(conectar, 2000);
  };
}

document.querySelectorAll('[data-accion]').forEach(boton => boton.addEventListener('click', () => {
  if (estado.socket?.readyState !== WebSocket.OPEN) return;
  const valor = boton.dataset.valor ? Number(boton.dataset.valor) : null;
  estado.socket.send(JSON.stringify({tipo: 'comando', accion: boton.dataset.accion, valor, node_id: NODO}));
}));
el('reiniciar').addEventListener('click', async () => {
  await fetch('/stats/reset', {method: 'POST'});
  gLatencia.data.labels = []; gLatencia.data.datasets[0].data = []; gLatencia.update('none');
});
setInterval(() => { if (Date.now() - estado.ultimoMensaje > 5000) estado.socket?.close(); }, 2000);
// Al abrir: cambios de clase recientes (tabla eventos) y ultimas lecturas para las graficas.
async function cargarHistorial() {
  try {
    const eventos = (await (await fetch(`/api/eventos?node_id=${NODO}&limit=5000`, {cache: 'no-store'})).json()).reverse();
    for (const e of eventos) {
      const hora = new Date(e.fecha_registro).toLocaleTimeString('es-MX', {hour12: false});
      cambioDeClase({clase: e.clase_predicha_rf, probabilidad: e.probabilidad_rf}, JSON.parse(e.variables_json), hora);
    }
    const lecturas = await (await fetch(`/api/readings?node_id=${NODO}&limit=${PUNTOS}`, {cache: 'no-store'})).json();
    lecturas.forEach(t => telemetria(t, false));
    (await (await fetch('/api/comandos', {cache: 'no-store'})).json()).forEach(comando);
  } catch { /* Sin historial: el dashboard se llena con los datos en vivo. */ }
  conectar();
}

setInterval(cargarEventos, 2000);
cargarEventos();
cargarHistorial();
