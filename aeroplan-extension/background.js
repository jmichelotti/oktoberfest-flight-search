const ORIGINS = ['IAD', 'DCA', 'BWI'];
const DESTINATIONS = ['CDG', 'FRA', 'ZRH'];
const DATES = ['2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18'];
const MIXED_CABIN_MIN_PCT = 85;
const ALERT_THRESHOLD = 150000;

let stopRequested = false;

function buildUrl(org, dest, date) {
  return `https://www.aircanada.com/aeroplan/redeem/availability/outbound` +
    `?org0=${org}&dest0=${dest}&departureDate0=${date}` +
    `&ADT=1&YTH=0&CHD=0&INF=0&INS=0&lang=en-CA&tripType=O&marketCode=INT`;
}

function broadcast(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

function waitForTabLoad(tabId) {
  return new Promise((resolve) => {
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, 30000);
  });
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function waitForResults(tabId, timeoutMs = 35000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const body = document.body?.innerText || '';
          if (/\d+\s+flights?\s+found/i.test(body)) return 'results';
          if (/no flights available/i.test(body)) return 'no_flights';
          if (/we're sorry|error code/i.test(body)) return 'error';
          if (/clogin|login/i.test(window.location.href)) return 'login';
          return 'loading';
        }
      });
      if (result.result && result.result !== 'loading') return result.result;
    } catch (e) {
      // Page still navigating
    }
    await sleep(2000);
  }
  return 'timeout';
}

function extractFlights(org, dest, date, minPct) {
  const body = document.body.innerText;
  const totalMatch = body.match(/(\d+)\s+flights?\s+found/i);
  if (!totalMatch) {
    return { state: /no flights/i.test(body) ? 'no_flights' : 'unknown', flights: [], rejected: 0 };
  }

  const bizCells = document.querySelectorAll('[class*="flight-cabin-cell"][aria-label*="Business Class"]');
  const results = [];
  let rejected = 0;
  const seen = new Set();

  for (const cell of bizCells) {
    const aria = cell.getAttribute('aria-label') || '';
    const cls = cell.className || '';

    const ptsMatch = aria.match(/from\s+(\d+)\s+points/i);
    if (!ptsMatch) continue;
    const pts = parseInt(ptsMatch[1]);

    const feeMatch = aria.match(/plus\s+\$(\d+\.?\d*)\s+CANADIAN/i);
    const fee = feeMatch ? 'CA$' + feeMatch[1] : '';

    const seatsMatch = aria.match(/(\d+)\s+seats?\s+left/i);
    const seatsLeft = seatsMatch ? seatsMatch[1] : '';

    const mixedEl = cell.querySelector('.mixed-cabin-percentage');
    let bizPct = 100;
    if (mixedEl) {
      const pctMatch = mixedEl.textContent.match(/(\d+)%/);
      if (pctMatch) bizPct = parseInt(pctMatch[1]);
    }

    const segPattern = /SEG-([A-Z]{2}\d+)-([A-Z]{3})([A-Z]{3})-\d{4}-\d{2}-\d{2}-(\d{4})/g;
    const segments = [];
    let sm;
    while ((sm = segPattern.exec(cls)) !== null) {
      segments.push({ flight: sm[1], from: sm[2], to: sm[3], time: sm[4] });
    }

    const flightNums = segments.map(s => s.flight);
    const stopAirports = [];
    if (segments.length > 1) {
      for (let j = 0; j < segments.length - 1; j++) stopAirports.push(segments[j].to);
    }
    const stops = Math.max(0, segments.length - 1);
    if (stops > 1) continue;

    const flightStr = flightNums.join('+');
    const fp = flightStr
      ? `${org}|${dest}|${date}|${flightStr}`
      : `${org}|${dest}|${date}|${pts}`;
    if (seen.has(fp)) continue;
    seen.add(fp);

    if (bizPct < minPct) { rejected++; continue; }

    let depTime = '', arrTime = '', duration = '', operator = '';
    let row = cell.closest('[class*="bound-row"], [class*="flight-row"], [class*="bound-card"]');
    if (!row) row = cell.parentElement?.parentElement;
    if (row) {
      const rowText = row.innerText || '';
      const depM = rowText.match(/(\d{1,2}:\d{2})\s*\n(Non-stop|1 stop)/);
      if (depM) depTime = depM[1];
      const durM = rowText.match(/(\d+hr\d*m?)/);
      if (durM) duration = durM[1];
      const arrM = rowText.match(/\+\d\s*\n(\d{1,2}:\d{2})/);
      if (arrM) arrTime = arrM[1] + '+1';
      const opM = rowText.match(/(?:Operated by|Includes travel operated by)\s+([^\n]+)/);
      if (opM) operator = opM[1].trim().replace(/Includes travel operated by /i, '').replace(/Operated by /i, '');
    }

    const AM = {UA:'United',LH:'Lufthansa',LX:'Swiss',LO:'LOT',OS:'Austrian',SN:'Brussels',SK:'SAS',TK:'Turkish',TP:'TAP',AC:'Air Canada',NH:'ANA',AV:'Avianca',ET:'Ethiopian',CA:'Air China',EW:'Eurowings',AF:'Air France',KL:'KLM',DL:'Delta'};
    const airlines = [...new Set(flightNums.map(f => AM[f.slice(0,2)] || f.slice(0,2)))].join(', ');

    results.push({
      Fingerprint: fp,
      Origin: org,
      Destination: dest,
      'Depart Date': date,
      'Airline(s)': airlines || operator || 'Unknown',
      Stops: stops,
      'Stop Airports': stopAirports.join(', '),
      'Flight Numbers': flightStr,
      'Dep Time': depTime,
      'Arr Time': arrTime,
      Duration: duration.replace('hr', 'H, ').replace('m', 'M'),
      Cabin: 'Business',
      'Business Pct': bizPct,
      Points: pts,
      Fees: fee,
      'Seats Left': seatsLeft,
      'Search URL': window.location.href
    });
  }
  return { state: 'results', flights: results, rejected, total: parseInt(totalMatch[1]) };
}

async function runScan() {
  stopRequested = false;
  const allResults = [];
  const noFlightsList = [];
  const failuresList = [];
  let totalRejected = 0;

  const combos = [];
  for (const org of ORIGINS) {
    for (const dest of DESTINATIONS) {
      for (const date of DATES) {
        combos.push({ org, dest, date });
      }
    }
  }

  const tab = await chrome.tabs.create({ url: buildUrl(combos[0].org, combos[0].dest, combos[0].date), active: true });
  const tabId = tab.id;

  for (let i = 0; i < combos.length; i++) {
    if (stopRequested) break;

    const { org, dest, date } = combos[i];
    const label = `${org}>${dest} ${date}`;
    broadcast({ type: 'progress', done: i, total: combos.length, label });

    try {
      if (i > 0) {
        const url = buildUrl(org, dest, date);
        await chrome.tabs.update(tabId, { url });
      }

      await waitForTabLoad(tabId);
      await sleep(3000);

      const state = await waitForResults(tabId);

      if (state === 'login') {
        broadcast({ type: 'error', message: 'Not logged in! Log into Aeroplan first, then retry.' });
        return;
      }

      if (state === 'no_flights') {
        noFlightsList.push(label);
        continue;
      }

      if (state === 'error') {
        failuresList.push(label + ' (page error)');
        continue;
      }

      if (state === 'timeout') {
        failuresList.push(label + ' (timeout)');
        continue;
      }

      await sleep(2000);

      const [result] = await chrome.scripting.executeScript({
        target: { tabId },
        args: [org, dest, date, MIXED_CABIN_MIN_PCT],
        func: extractFlights
      });

      const data = result.result;

      if (!data || data.state === 'unknown') {
        failuresList.push(label + ' (unknown state)');
        continue;
      }
      if (data.state === 'no_flights') {
        noFlightsList.push(label);
        continue;
      }

      totalRejected += data.rejected || 0;
      allResults.push(...data.flights);

    } catch (e) {
      failuresList.push(label + ': ' + (e.message || String(e)).slice(0, 80));
    }
  }

  try { chrome.tabs.remove(tabId); } catch (e) {}

  // Download results
  const jsonStr = JSON.stringify(allResults, null, 2);
  const dataUrl = 'data:application/json;base64,' + btoa(unescape(encodeURIComponent(jsonStr)));
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  chrome.downloads.download({
    url: dataUrl,
    filename: `aeroplan-scan-${timestamp}.json`,
    saveAs: false
  });

  const minPts = allResults.length > 0 ? Math.min(...allResults.map(r => r.Points)) : null;
  const alerts = allResults.filter(r => r.Points <= ALERT_THRESHOLD).length;
  const mixed = allResults.filter(r => (r['Business Pct'] || 100) < 100).length;

  broadcast({
    type: 'done',
    summary: {
      searched: combos.length,
      withResults: combos.length - noFlightsList.length - failuresList.length,
      noFlights: noFlightsList.length,
      failures: failuresList.length,
      flights: allResults.length,
      mixed,
      rejected: totalRejected,
      minPts,
      alerts
    }
  });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'startScan') runScan();
  if (msg.action === 'stopScan') stopRequested = true;
});
