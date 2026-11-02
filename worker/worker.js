// Cloudflare Worker: live Unitrans GPS + arrival predictions for the web page.
// Keeps your Swiftly API key secret, decodes the GTFS-Realtime feeds, and returns
// small JSON for just the routes the page asks for.
//
// Settings (Worker → Settings → Variables and secrets):
//   SWIFTLY_KEY     (secret)  your Swiftly real-time API key
//   AGENCY          (text)    Swiftly agency key, default "unitrans"
//   ALLOWED_ORIGIN  (text)    e.g. https://yourname.github.io  (optional; blocks other sites)
//
// Routes:
//   /live?route=C          vehicles + predictions for route ids (comma-separated)
//   /check                 shows whether the key and agency work

const FEEDS = {
  vehicles: "gtfs-rt-vehicle-positions",
  trips: "gtfs-rt-trip-updates",
};

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const origin = req.headers.get("Origin") || "";
    const allowed = env.ALLOWED_ORIGIN || "*";
    const cors = {
      "Access-Control-Allow-Origin": allowed === "*" ? "*" : allowed,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (allowed !== "*" && origin && origin !== allowed)
      return json({ error: "This site isn’t allowed to use this Worker." }, 403, cors);
    if (!env.SWIFTLY_KEY)
      return json({ error: "SWIFTLY_KEY is not set on the Worker." }, 500, cors);

    const agency = env.AGENCY || "unitrans";

    if (url.pathname === "/check") {
      const r = await fetchFeed(agency, FEEDS.vehicles, env.SWIFTLY_KEY);
      if (!r.ok) return json({ ok: false, status: r.status, agency, body: await r.text() }, 200, cors);
      const feed = decodeFeed(new Uint8Array(await r.arrayBuffer()));
      const routes = [...new Set(feed.vehicles.map(v => v.routeId).filter(Boolean))].sort();
      return json({ ok: true, agency, vehiclesNow: feed.vehicles.length, routesSeen: routes }, 200, cors);
    }

    if (url.pathname === "/live") {
      const routes = new Set((url.searchParams.get("route") || "C").split(",").map(s => s.trim()));
      // Share one Swiftly call per 10 s across everyone using the page.
      const cacheKey = new Request(`https://cache.local/${agency}/${[...routes].sort().join(",")}`);
      const cache = caches.default;
      const hit = await cache.match(cacheKey);
      if (hit) return withCors(hit, cors);

      const [vr, tr] = await Promise.all([
        fetchFeed(agency, FEEDS.vehicles, env.SWIFTLY_KEY),
        fetchFeed(agency, FEEDS.trips, env.SWIFTLY_KEY),
      ]);
      if (!vr.ok && !tr.ok)
        return json({ error: `Swiftly returned ${vr.status}. Check SWIFTLY_KEY and AGENCY.` }, 502, cors);

      const v = vr.ok ? decodeFeed(new Uint8Array(await vr.arrayBuffer())) : { vehicles: [], updates: [] };
      const t = tr.ok ? decodeFeed(new Uint8Array(await tr.arrayBuffer())) : { vehicles: [], updates: [] };
      const body = {
        fetched: Math.floor(Date.now() / 1000),
        feedTime: v.timestamp || t.timestamp || null,
        vehicles: v.vehicles.filter(x => routes.has(x.routeId)),
        updates: t.updates.filter(x => routes.has(x.routeId)),
      };
      const res = json(body, 200, { "Cache-Control": "max-age=10" });
      ctx.waitUntil(cache.put(cacheKey, res.clone()));
      return withCors(res, cors);
    }

    return json({ error: "Use /live or /check" }, 404, cors);
  },
};

function fetchFeed(agency, feed, key) {
  return fetch(`https://api.goswift.ly/real-time/${encodeURIComponent(agency)}/${feed}`, {
    headers: { Authorization: key, Accept: "application/x-protobuf" },
  });
}
function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json", ...headers },
  });
}
function withCors(res, cors) {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(cors)) r.headers.set(k, v);
  return r;
}

// ---- Minimal GTFS-Realtime protobuf decoder (only the fields we need) ----
function reader(buf) {
  let pos = 0;
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const varint = () => {
    let result = 0, shift = 0, b;
    do {
      b = buf[pos++];
      result += (b & 0x7f) * 2 ** shift;
      shift += 7;
    } while (b & 0x80);
    return result;
  };
  return {
    get done() { return pos >= buf.length; },
    tag() { const t = varint(); return [Math.floor(t / 8), t & 7]; },
    varint,
    bytes() { const n = varint(); const out = buf.subarray(pos, pos + n); pos += n; return out; },
    string() { return new TextDecoder().decode(this.bytes()); },
    float() { const f = dv.getFloat32(pos, true); pos += 4; return f; },
    double() { const f = dv.getFloat64(pos, true); pos += 8; return f; },
    skip(wt) {
      if (wt === 0) varint();
      else if (wt === 1) pos += 8;
      else if (wt === 2) { const n = varint(); pos += n; }
      else if (wt === 5) pos += 4;
      else throw new Error("bad wire type " + wt);
    },
  };
}
function parse(buf, handlers, obj = {}) {
  const r = reader(buf);
  while (!r.done) {
    const [field, wt] = r.tag();
    const h = handlers[field];
    if (h) h(r, obj, wt); else r.skip(wt);
  }
  return obj;
}
const toSigned = n => (n > 0x7fffffff && n <= 0xffffffff ? n - 0x100000000 : n);

const tripDesc = b => parse(b, {
  1: (r, o) => (o.tripId = r.string()),
  2: (r, o) => (o.startTime = r.string()),
  3: (r, o) => (o.startDate = r.string()),
  5: (r, o) => (o.routeId = r.string()),
  6: (r, o) => (o.directionId = r.varint()),
});
const vehDesc = b => parse(b, {
  1: (r, o) => (o.id = r.string()),
  2: (r, o) => (o.label = r.string()),
});
const position = b => parse(b, {
  1: (r, o) => (o.lat = r.float()),
  2: (r, o) => (o.lon = r.float()),
  3: (r, o) => (o.bearing = r.float()),
  4: (r, o) => r.double(),
  5: (r, o) => (o.speed = r.float()),
});
const stEvent = b => parse(b, {
  1: (r, o) => (o.delay = toSigned(r.varint())),
  2: (r, o) => (o.time = r.varint()),
});
const stUpdate = b => parse(b, {
  1: (r, o) => (o.seq = r.varint()),
  2: (r, o) => (o.arr = stEvent(r.bytes()).time),
  3: (r, o) => (o.dep = stEvent(r.bytes()).time),
  4: (r, o) => (o.stopId = r.string()),
  5: (r, o) => (o.rel = r.varint()),
});

function decodeFeed(buf) {
  const out = { timestamp: null, vehicles: [], updates: [] };
  parse(buf, {
    1: r => parse(r.bytes(), { 3: rr => (out.timestamp = rr.varint()) }),
    2: r => {
      parse(r.bytes(), {
        3: rr => {
          const tu = parse(rr.bytes(), {
            1: (x, o) => Object.assign(o, tripDesc(x.bytes())),
            2: (x, o) => (o.stops = o.stops || []).push(stUpdate(x.bytes())),
            3: (x, o) => (o.vehicleId = vehDesc(x.bytes()).id),
            4: (x, o) => (o.ts = x.varint()),
          });
          out.updates.push({
            tripId: tu.tripId, routeId: tu.routeId, directionId: tu.directionId,
            vehicleId: tu.vehicleId,
            stops: (tu.stops || []).filter(s => s.rel !== 1).map(s => ({
              stopId: s.stopId, seq: s.seq, arr: s.arr, dep: s.dep,
            })),
          });
        },
        4: rr => {
          const vp = parse(rr.bytes(), {
            1: (x, o) => Object.assign(o, tripDesc(x.bytes())),
            2: (x, o) => Object.assign(o, position(x.bytes())),
            3: (x, o) => (o.seq = x.varint()),
            4: (x, o) => (o.status = x.varint()),
            5: (x, o) => (o.ts = x.varint()),
            7: (x, o) => (o.stopId = x.string()),
            8: (x, o) => { const d = vehDesc(x.bytes()); o.id = d.id; o.label = d.label; },
          });
          if (vp.lat != null) out.vehicles.push(vp);
        },
      });
    },
  });
  return out;
}
