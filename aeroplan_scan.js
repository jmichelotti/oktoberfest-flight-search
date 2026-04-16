async (page) => {
  const { execSync } = await import('child_process');
  const { readFileSync } = await import('fs');

  // Load credentials from .env
  const envText = readFileSync('C:\\dev\\oktoberfest-flight-search\\.env', 'utf8');
  const env = {};
  for (const line of envText.split('\n')) {
    const [k, ...v] = line.split('=');
    if (k && v.length) env[k.trim()] = v.join('=').trim();
  }
  const AP_USER = env.AP_USERNAME;
  const AP_PASS = env.AP_PASSWORD;

  // ---- Login helper ----
  async function doLogin() {
    // Navigate to home and click Sign in
    await page.goto('https://www.aircanada.com/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(5000);

    // Click Sign in
    const signInBtn = page.locator('button:has-text("Sign in")').first();
    if (await signInBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await signInBtn.click();
    } else {
      // Try via evaluate
      await page.evaluate(() => {
        const el = Array.from(document.querySelectorAll('a, button')).find(e => /^sign in$/i.test((e.textContent||'').trim()));
        if (el) el.click();
      });
    }

    // Wait for login page
    await page.waitForURL(/clogin|login/, { timeout: 15000 });
    await page.waitForTimeout(3000);

    // Type credentials
    const emailField = page.getByRole('textbox', { name: 'Aeroplan number or email' });
    await emailField.pressSequentially(AP_USER, { delay: 30 });
    await page.waitForTimeout(300);

    const passField = page.getByRole('textbox', { name: 'Password' });
    await passField.pressSequentially(AP_PASS, { delay: 30 });
    await page.waitForTimeout(300);

    // Click Sign in button
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.waitForTimeout(8000);

    // Should be on 2FA screen — click "Send Code" for email
    await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'))
        .filter(b => /^send code$/i.test((b.textContent||'').trim()));
      const emailBtn = btns.find(b => (b.parentElement.textContent||'').includes('@gmail.com'));
      if (emailBtn) emailBtn.click();
    });
    await page.waitForTimeout(3000);

    // Poll for OTP via gmail_otp.py
    let otpCode = null;
    try {
      const result = execSync(
        'python gmail_otp.py --poll --sender aeroplan --timeout 90',
        { cwd: 'C:\\dev\\jal-flights-tracker', encoding: 'utf8', timeout: 100000 }
      );
      const parsed = JSON.parse(result.trim());
      otpCode = parsed.code;
    } catch (e) {
      return { error: 'OTP poll failed', detail: e.message?.slice(0, 200) };
    }

    if (!otpCode) return { error: 'No OTP code retrieved' };

    // Enter code using page.fill (critical for Gigya)
    await page.locator('input[name="emailCode_0"]').fill(otpCode);
    await page.waitForTimeout(500);

    // Click Submit via coordinates (Gigya has 21 hidden submit buttons)
    const codeBox = await page.locator('input[name="emailCode_0"]').boundingBox();
    if (codeBox) {
      await page.mouse.click(codeBox.x + codeBox.width / 2, codeBox.y + codeBox.height + 80);
    }

    // Wait for redirect back to home
    await page.waitForTimeout(12000);

    // Verify login succeeded
    const bodyText = await page.evaluate(() => document.body.innerText.slice(0, 300));
    const loggedIn = /Justin|0 pts|My Aeroplan/i.test(bodyText);
    return loggedIn;
  }

  // ---- Check if logged in ----
  async function isLoggedIn() {
    const url = page.url();
    if (/clogin|login/i.test(url)) return false;
    const text = await page.evaluate(() => document.body.innerText.slice(0, 300));
    return /Justin|0 pts|My Aeroplan/i.test(text);
  }

  // ---- Detect page state after navigation ----
  // Returns: 'results' | 'no_flights' | 'invalid_route' | 'login_redirect' | 'unknown'
  async function detectPageState() {
    const text = await page.evaluate(() => document.body.innerText);
    if (/flights?\s+found/i.test(text)) return 'results';
    if (/no flights available/i.test(text)) return 'no_flights';
    if (/we couldn.t find|unable to find|invalid|not.+available.+route|please.+enter.+valid/i.test(text)) return 'invalid_route';
    const url = page.url();
    if (/clogin|login/i.test(url)) return 'login_redirect';
    return 'unknown';
  }

  // ---- Extract business flights from current results page ----
  async function extractFlights(org, dest, date, url) {
    return await page.evaluate(({ org, dest, date, url }) => {
      const body = document.body.innerText;
      const totalMatch = body.match(/(\d+)\s+flights?\s+found/i);
      if (!totalMatch) return [];

      const sections = body.split(/\d+ of \d+/);
      const results = [];

      for (let i = 1; i < sections.length; i++) {
        const s = sections[i];
        const depMatch = s.match(/(\d{1,2}:\d{2})\s*\n(Non-stop|1 stop)/);
        if (!depMatch) continue;

        const stops = depMatch[2] === 'Non-stop' ? 0 : 1;
        const durMatch = s.match(/(\d+hr\d*m?)/);
        const arrMatch = s.match(/\+\d\s*\n(\d{1,2}:\d{2})/);
        const opMatch = s.match(/(?:Operated by|Includes travel operated by)\s+([^\n]+)/);

        const fareList = [];
        const farePattern = /([\d.]+K)\s*\n\s*\+\s*CA\s*\$(\d+)|—/g;
        let m;
        while ((m = farePattern.exec(s)) !== null) {
          if (m[0] === '—') fareList.push(null);
          else fareList.push({ pts: m[1], fee: 'CA$' + m[2] });
        }

        const bizFare = fareList[2] || null;
        if (!bizFare) continue;
        if (stops > 1) continue;

        const ptsNum = Math.round(parseFloat(bizFare.pts.replace('K', '')) * 1000);
        const stopMatch = s.match(/\n([A-Z]{3})\s*(?:-\s*[A-Z]{3})?\s*\n\+\s*\d+h/);
        const stopAirport = stops === 1 && stopMatch ? stopMatch[1] : '';
        const seatsMatch = s.match(/(\d+)\s+seats?\s+left/i);

        const dep = depMatch[1];
        const arr = arrMatch ? arrMatch[1] : '';
        const dur = durMatch ? durMatch[1] : '';
        const operator = opMatch ? opMatch[1].trim()
          .replace(/Includes travel operated by /i, '')
          .replace(/Operated by /i, '') : '';

        const depClean = dep.replace(/:/g, '');
        const durClean = dur.replace(/\s/g, '');
        const fp = stops === 0
          ? `${org}|${dest}|${date}|${depClean}-${durClean}-nonstop`
          : `${org}|${dest}|${date}|${depClean}-${durClean}-${stopAirport}`;

        results.push({
          Fingerprint: fp,
          Origin: org,
          Destination: dest,
          'Depart Date': date,
          'Airline(s)': operator,
          Stops: stops,
          'Stop Airports': stopAirport,
          'Flight Numbers': '',
          'Dep Time': dep,
          'Arr Time': arr ? arr + '+1' : '',
          Duration: dur.replace('hr', 'H, ').replace('m', 'M'),
          Cabin: 'Business',
          Points: ptsNum,
          Fees: bizFare.fee,
          'Seats Left': seatsMatch ? seatsMatch[1] : '',
          'Search URL': url
        });
      }
      return results;
    }, { org, dest, date, url });
  }

  // ---- Main scan loop ----
  const origins = ['IAD', 'DCA', 'BWI'];
  const dests = ['CDG', 'FRA', 'ZRH'];
  const dates = ['2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18'];
  const allResults = [];
  const failures = [];
  const noFlights = [];   // combos where page loaded fine but no availability
  const skipped = [];     // combos where airport/route is invalid
  let loginCount = 0;
  let combosDone = 0;

  // Initial login check
  await page.goto('https://www.aircanada.com/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);
  if (!(await isLoggedIn())) {
    const loginOk = await doLogin();
    if (!loginOk) return { error: 'Initial login failed', loginResult: loginOk };
    loginCount++;
  }

  for (const org of origins) {
    for (const dest of dests) {
      for (const date of dates) {
        combosDone++;
        const url = `https://www.aircanada.com/aeroplan/redeem/availability/outbound?org0=${org}&dest0=${dest}&departureDate0=${date}&ADT=1&YTH=0&CHD=0&INF=0&INS=0&lang=en-CA&tripType=O&marketCode=INT`;

        try {
          await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });

          // Wait for results or any terminal state
          try {
            await page.waitForSelector('text=/flights? found|no flights available/i', { timeout: 20000 });
          } catch (e) {
            await page.waitForTimeout(5000);
          }

          let state = await detectPageState();

          // If login redirect or unknown, try re-login once
          if (state === 'login_redirect' || state === 'unknown') {
            const loginOk = await doLogin();
            if (!loginOk) {
              failures.push({ org, dest, date, error: 'Re-login failed' });
              continue;
            }
            loginCount++;

            // Retry this combo
            await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
            try {
              await page.waitForSelector('text=/flights? found|no flights available/i', { timeout: 20000 });
            } catch (e) {
              await page.waitForTimeout(10000);
            }
            state = await detectPageState();
          }

          if (state === 'no_flights') {
            noFlights.push({ org, dest, date });
            continue;
          }

          if (state === 'invalid_route') {
            skipped.push({ org, dest, date, reason: 'invalid route or airport' });
            continue;
          }

          if (state !== 'results') {
            failures.push({ org, dest, date, error: `unknown page state: ${state}` });
            continue;
          }

          await page.waitForTimeout(2000);
          const flights = await extractFlights(org, dest, date, url);
          allResults.push(...flights);

        } catch (err) {
          failures.push({ org, dest, date, error: err.message?.slice(0, 100) });
        }
      }
    }
  }

  // Save results to file
  const { writeFileSync } = await import('fs');
  writeFileSync(
    'C:\\dev\\oktoberfest-flight-search\\session_results.json',
    JSON.stringify(allResults, null, 2)
  );

  return {
    combosDone,
    totalFlights: allResults.length,
    loginCount,
    noFlightsCount: noFlights.length,
    noFlights,
    skippedCount: skipped.length,
    skipped,
    failures: failures.length,
    failureDetails: failures
  };
}
