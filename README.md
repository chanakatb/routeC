# Next C bus (Unitrans, Davis)

Open the page and a popup shows the next C line bus at two stops:

- **Silo at Main Island**, going **outbound** to Wake Forest
- **Wake Forest at Cuarto**, going **inbound** to Silo

With live GPS turned on, the page also shows a map of where each C bus is right now.
Arrival times then come from the buses' GPS and are marked **Live**. Without live GPS, the page
shows the official schedule, marked **Scheduled**. Every time is shown in Davis time (PST/PDT).

## What's in this folder
| File | What it does |
|---|---|
| `index.html` | The page: popup, map, and countdowns |
| `config.js` | Where you paste your Worker address to turn on live GPS |
| `build_schedule.py` | Turns the Unitrans schedule into `schedule.json` (times, stops, route line) |
| `.github/workflows/update-schedule.yml` | Rebuilds `schedule.json` every night |
| `worker/worker.js` | A small Cloudflare Worker that keeps your live GPS key private |

## Part 1: Put the page on GitHub (scheduled times)
1. Create a **public** repo, for example `c-bus`. Upload everything except the `.github` folder.
2. Go to **Add file → Create new file**. Name it `.github/workflows/update-schedule.yml` and paste in that file's contents.
3. Go to **Actions → Update schedule → Run workflow**. Wait for `schedule.json` to appear in the repo.
4. Go to **Settings → Pages**, choose **Deploy from a branch**, then **main** and **/ (root)**.
5. After a minute or two, your page is live at `https://YOUR-USERNAME.github.io/c-bus/`.
   On your phone, use **Add to Home Screen**.

## Part 2: Turn on live GPS
Unitrans' live bus data comes from **Swiftly**, and reading it requires a free API key.
Your page is public, so the key can't go in the page itself. Instead, a free Cloudflare Worker holds it.

1. **Get a Swiftly key:** request one at https://goswift.ly/realtime-api-key and ask for the agency **Unitrans**.
   Swiftly will send you the key. If they tell you an "agency key" other than `unitrans`, write it down for step 3.
2. **Make the Worker:** sign up at https://dash.cloudflare.com (free). Go to **Workers & Pages → Create → Worker**
   and name it `c-bus-live`. Choose **Edit code**, replace everything with the contents of `worker/worker.js`, and click **Deploy**.
3. **Add your settings:** open the Worker and go to **Settings → Variables and secrets**. Add:
   - `SWIFTLY_KEY`, type **Secret**: your Swiftly key
   - `ALLOWED_ORIGIN`, type **Text**: `https://YOUR-USERNAME.github.io` (this stops other sites from using your Worker)
   - `AGENCY`, type **Text**: only needed if Swiftly gave you something other than `unitrans`
4. **Test it:** open `https://c-bus-live.YOUR-NAME.workers.dev/check` in your browser.
   You should see `"ok": true` and a list of routes. If `"ok"` is false, the message explains why.
   A 401 or 403 means the key is wrong. A 404 means the agency name is wrong.
5. **Connect the page:** in your GitHub repo, edit `config.js` and set
   `window.LIVE_URL = "https://c-bus-live.YOUR-NAME.workers.dev";`. Then commit the change.

Reload the page. Live buses appear on the map and update every 15 seconds. Tap a bus to see:
- its direction (outbound or inbound)
- its next stop
- how long ago its GPS last reported

## Notes
- **Free limits:** Cloudflare's free plan allows 100,000 requests a day. The Worker caches each answer for
  10 seconds, so even several phones checking at once stay far below that limit.
- **When live data is missing:** if a bus has no live prediction yet, its time stays **Scheduled**. If the live connection
  drops for more than two minutes, the page switches back to scheduled times on its own.
- **New terms:** add each new term name to `TERMS` in `index.html`. Times update automatically either way.
- **Optional text alerts:** see `optional-text-alerts/`.
