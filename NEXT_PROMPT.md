Continue work on the oktoberfest-flight-search tracker at `C:\dev\oktoberfest-flight-search`. Read `CLAUDE.md` in full before doing anything — scroll to the Phase 2 (Air France / Flying Blue) section. The date picker is now solved (working recipe is documented in the "Date picker — working recipe" subsection). A new, harder blocker is in the way.

**Your single task:** figure out how to get airfrance.us Flying Blue award search results to actually render, so we can extract IAD→CDG 2026-09-16 Business and then scale to the 36-combo grid.

## The blocker (discovered 2026-04-15)

After filling the Miles form and submitting, the results page loads a skeleton UI that never resolves. Console shows `/gql/v1` requests failing with HTTP2 protocol errors; a direct `fetch()` from the page returns **HTTP 403**. Cookie jar carries Akamai Bot Manager markers (`_abck`, `bm_sz`, `bm_mi`, `bm_sv`). This is detection-of-automation — the edge is blocking us, not the Angular app.

The "Miles balance 0" shown on the results page is a symptom, not the cause: the same GQL endpoint that would fetch the balance is also 403'd.

Screenshot of the stuck state: `failures/2026-04-15-af-akamai-block.png`.

## Strategies to try (rough priority order)

1. **Check whether it's transient.** Akamai sometimes relaxes after a cool-down. Start the session by navigating to airfrance.us and immediately probing `fetch('/gql/v1?operationName=Hello')` — if it's 200, proceed normally; if still 403, skip to strategy 2.

2. **Try KLM.com instead.** Same Flying Blue credentials work on klm.com. KLM's bot posture may differ from AF's. If KLM returns results, the Phase 2 implementation can target klm.com; the form shell is Angular Material too, so the date-picker recipe in CLAUDE.md should mostly carry over. This is the highest-leverage fallback.

3. **Warm the Playwright profile via human interaction.** Launch the MCP browser, navigate to airfrance.us, and **have the user** (via manual screenshare or an external VNC) do a full manual search — move the mouse, click, type slowly, accept cookies — so Akamai's sensor-data check emits a valid `_abck` cookie. Then run the automation against the warmed profile. If the cookie stays valid across `browser_close`, subsequent runs may inherit the good state.

4. **Playwright stealth patches.** The MCP-launched Chrome likely has `navigator.webdriver === true` and other tell-tale signals. Without wrapping MCP, we can partially mask these by injecting via `browser_evaluate` on every page load — but Akamai fingerprints TLS / HTTP2 behavior below the JS layer, so this rarely wins alone.

5. **Punt Phase 2.** If all the above fail, Phase 1 (United) already covers FRA and ZRH; the value of Phase 2 is mainly the CDG nonstop (AF 55) and occasional cheaper saver pricing. Document the block as a hard stop in CLAUDE.md, remove Phase 2 from the schedule, and have the user check CDG manually with a real browser session when they care.

## Do NOT

- Do not try to defeat Akamai by hardcoding cookies, scraping sensor-data from another context, or any technique that feels adversarial toward the airline's bot defenses. If strategies 1–3 fail cleanly, move to 4, then 5. Don't keep escalating.
- Do not touch Phase 1 (United) — it's working.
- Do not delete the `.playwright-mcp/` profile. It's the cookie-persistence mechanism the whole tracker relies on.

## When you're done (or stuck)

- Update CLAUDE.md's Phase 2 section with whatever you learned — whether a strategy worked, or a new dead end to document.
- Commit and push with a descriptive message.
- Delete `NEXT_PROMPT.md` if Phase 2 is either fully working or fully abandoned. Otherwise, rewrite it again with the next clean handoff.

## Effort and expectations

- Use sonnet-4-6 (not max). This is exploration.
- Time-box to 30 minutes. If none of strategies 1–4 work in that window, document strategy 5 and hand back.
