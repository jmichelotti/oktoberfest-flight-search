const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const status = document.getElementById('status');
const progressFill = document.getElementById('progressFill');
const resultsDiv = document.getElementById('results');

let scanning = false;

startBtn.addEventListener('click', () => {
  scanning = true;
  startBtn.disabled = true;
  stopBtn.style.display = 'inline-block';
  status.textContent = 'Starting scan...';
  chrome.runtime.sendMessage({ action: 'startScan' });
});

stopBtn.addEventListener('click', () => {
  scanning = false;
  stopBtn.style.display = 'none';
  startBtn.disabled = false;
  status.textContent = 'Stopped.';
  chrome.runtime.sendMessage({ action: 'stopScan' });
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'progress') {
    const pct = Math.round((msg.done / msg.total) * 100);
    progressFill.style.width = pct + '%';
    status.textContent = `[${msg.done}/${msg.total}] ${msg.label}`;
  }
  if (msg.type === 'done') {
    startBtn.disabled = false;
    stopBtn.style.display = 'none';
    progressFill.style.width = '100%';
    const s = msg.summary;
    status.textContent = `Done! ${s.flights} flights captured.`;
    resultsDiv.innerHTML = `
      <div class="stat"><span class="label">Combos searched:</span><span>${s.searched}</span></div>
      <div class="stat"><span class="label">With results:</span><span>${s.withResults}</span></div>
      <div class="stat"><span class="label">No flights:</span><span>${s.noFlights}</span></div>
      <div class="stat"><span class="label">Failures:</span><span>${s.failures}</span></div>
      <div class="stat"><span class="label">Total Business flights:</span><span>${s.flights}</span></div>
      <div class="stat"><span class="label">Mixed-cabin (≥85%):</span><span>${s.mixed}</span></div>
      <div class="stat"><span class="label">Rejected (&lt;85%):</span><span>${s.rejected}</span></div>
      ${s.minPts ? `<div class="stat"><span class="label">Min points:</span><span class="warn">${(s.minPts/1000).toFixed(0)}K</span></div>` : ''}
      <div class="stat"><span class="label">Alerts (≤150K):</span><span class="warn">${s.alerts}</span></div>
    `;
  }
  if (msg.type === 'error') {
    status.textContent = 'Error: ' + msg.message;
    startBtn.disabled = false;
    stopBtn.style.display = 'none';
  }
});
