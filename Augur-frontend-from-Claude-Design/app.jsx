// Augur — Home view
// Single-file React prototype. Data + components inline.
// Aesthetic: Economist/FT interior · Bloomberg terminal · Tufte
// No icons except direction glyphs. Typography over imagery.

const { useState, useEffect, useMemo, useRef, useCallback } = React;

// ─────────────────────────────────────────────────────────────────────────────
// DATA
// ─────────────────────────────────────────────────────────────────────────────

const BAND_SCALE = ["Improving", "Stable", "Strained", "Deteriorating", "Crisis"];

const DIMENSIONS = [
  {
    key: "econ",
    name: "Economic Stability",
    band: "Strained",
    bandIdx: 2,
    direction: "down",     // direction of change
    glyph: "↘",
    note: "Real-rate compression offset by widening sovereign spreads.",
    rate: "Decelerating",
    accel: "Drift narrowing",
    series: genSeries(48, 0.62, [0.05, -0.08, -0.12]),
  },
  {
    key: "geo",
    name: "Geopolitical Tension",
    band: "Deteriorating",
    bandIdx: 3,
    direction: "down",
    glyph: "↘↘",
    note: "Three concurrent corridors; coupling tightening across them.",
    rate: "Accelerating",
    accel: "Second-order rising",
    series: genSeries(48, 0.78, [0.12, 0.08, 0.18]),
  },
  {
    key: "res",
    name: "Resource Availability",
    band: "Strained",
    bandIdx: 2,
    direction: "down",
    glyph: "↘",
    note: "Phosphate and gallium constraints compounding; not yet substitutable.",
    rate: "Steady decline",
    accel: "Zero",
    series: genSeries(48, 0.55, [-0.04, -0.06, -0.05]),
  },
  {
    key: "env",
    name: "Environmental Stress",
    band: "Deteriorating",
    bandIdx: 3,
    direction: "flat",
    glyph: "→",
    note: "Anomaly index plateaued at elevated level; no reversion signal.",
    rate: "Plateau (high)",
    accel: "Zero",
    series: genSeries(48, 0.72, [0.02, 0.01, 0.0]),
  },
  {
    key: "struct",
    name: "Structural Change",
    band: "Improving",
    bandIdx: 0,
    direction: "up",
    glyph: "↗",
    note: "Adaptation velocity in semis & energy outpacing baseline disruption.",
    rate: "Accelerating",
    accel: "Compounding",
    series: genSeries(48, 0.40, [0.06, 0.10, 0.14]),
  },
];

// generate a quasi-deterministic sparkline series
function genSeries(n, base, slopes) {
  // slopes split across thirds
  const pts = [];
  let v = base;
  let seed = 1337;
  const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  for (let i = 0; i < n; i++) {
    const phase = i / n;
    const slope = phase < .33 ? slopes[0] : phase < .66 ? slopes[1] : slopes[2];
    v += slope / n + (rand() - .5) * 0.06;
    v = Math.max(0.02, Math.min(0.98, v));
    pts.push(v);
  }
  return pts;
}

const CHANGES_24H = [
  {
    n: 1, dim: "Geopolitical", impact: 5,
    body: "Iranian transit-fee proposal for Hormuz formally tabled at OPEC+ sideline; Brent term structure inverts at 14-month tenor.",
    meta: ["Brent +4.2%", "06:14 GMT"],
    metaDir: "dn",
    edges: 23,
  },
  {
    n: 2, dim: "Resources", impact: 5,
    body: "Russian phosphate export quota tightened 18%; OCP Morocco unable to absorb, futures decoupling from urea benchmark.",
    meta: ["DAP +7.1%", "04:02 GMT"],
    metaDir: "dn",
    edges: 19,
  },
  {
    n: 3, dim: "Economic", impact: 4,
    body: "ECB minutes signal earlier dovish pivot; Bund 2s10s flattens 11bp intraday, swap-spread regime change.",
    meta: ["Bund 2s10s −11bp", "11:47 CET"],
    metaDir: "up",
    edges: 14,
  },
  {
    n: 4, dim: "Resources", impact: 4,
    body: "MOFCOM rare-earth licensing extended to Ga, Ge precursors; Korean and Taiwanese inventory drawdowns enter month four.",
    meta: ["Ga spot +12.4%", "02:30 UTC"],
    metaDir: "dn",
    edges: 12,
  },
  {
    n: 5, dim: "Environmental", impact: 3,
    body: "Brazilian soy harvest revised −3.1% on Mato Grosso heat dome; cascade risk to feed-grain and protein complex.",
    meta: ["CBOT soy +2.6%", "16:10 BRT"],
    metaDir: "dn",
    edges: 9,
  },
  {
    n: 6, dim: "Structural", impact: 3,
    body: "Hyperscaler order book flat month-on-month for first time in eleven quarters — capex curve inflection candidate.",
    meta: ["AI-capex Δ ≈ 0", "Q1 close"],
    metaDir: "flat",
    edges: 11,
  },
  {
    n: 7, dim: "Economic", impact: 2,
    body: "Argentine peso parallel-rate spread narrows to 6.2%, the tightest in nineteen months; informal market thinning.",
    meta: ["ARS gap 6.2%", "EOD"],
    metaDir: "up",
    edges: 6,
  },
];

const LOCAL = {
  name: "San Francisco, California",
  coord: "37.7749° N · 122.4194° W",
  dims: [
    { name: "Economic",       band: "Stable",        glyph: "→",  dir: "flat" },
    { name: "Geopolitical",   band: "Strained",      glyph: "↘",  dir: "down" },
    { name: "Resources",      band: "Strained",      glyph: "↘",  dir: "down" },
    { name: "Environmental", band: "Deteriorating", glyph: "↘",  dir: "down" },
    { name: "Structural",    band: "Improving",     glyph: "↗",  dir: "up" },
  ],
  notes: "Regional exposure is dominated by structural rotation (AI capex inflection) and second-order energy effects from the Hormuz scenario. Water-table stress remains a tail factor for Central Valley feedstocks.",
  changes: [
    { tag: "Power", body: "PG&E summer reserve margin revised to 11.4% — third consecutive monthly downgrade." },
    { tag: "Housing", body: "Bay Area median rent index decouples downward from national series for the first time since 2019." },
    { tag: "Labor", body: "Semis sector job-postings index +18% MoM; concentrated in 95054 and 94043." },
  ],
};

const TOPICS = [
  {
    title: "Iran–Israel and regional energy corridors",
    gist: "Coupling between Hormuz transit risk, LNG redirection, and European inventory cycles.",
    nodes: "142 nodes · 487 edges",
    weight: "high",
  },
  {
    title: "Fertilizer constraint and the global food chain",
    gist: "Phosphate, potash, and urea — substitution geometry and harvest-cycle lag.",
    nodes: "96 nodes · 312 edges",
    weight: "high",
  },
  {
    title: "Semiconductor supply geography",
    gist: "Taiwan concentration, ASML throughput, and the Ga/Ge precursor licensing regime.",
    nodes: "118 nodes · 421 edges",
    weight: "high",
  },
  {
    title: "U.S. fiscal trajectory and Treasury markets",
    gist: "Coupon issuance composition, foreign sponsorship, and the term-premium regime.",
    nodes: "84 nodes · 268 edges",
    weight: "med",
  },
  {
    title: "Climate-trade frictions",
    gist: "CBAM, transition tariffs, and the implicit carbon price embedded in cross-border flows.",
    nodes: "67 nodes · 198 edges",
    weight: "med",
  },
  {
    title: "AI labor displacement and wage dispersion",
    gist: "Task-level substitution rates, occupational migration latency, and wage variance widening.",
    nodes: "59 nodes · 174 edges",
    weight: "med",
  },
  {
    title: "Sahel destabilization and migration pressure",
    gist: "Coup contagion vectors, Saharan transit corridors, and Mediterranean reception capacity.",
    nodes: "73 nodes · 224 edges",
    weight: "med",
  },
];

// Scrubber event ticks — historical waypoints
const SCRUB_EVENTS = [
  { t: 0.02,  label: "Russia–Ukraine onset" },
  { t: 0.16,  label: "Energy crisis peak" },
  { t: 0.28,  label: "SVB / banking stress" },
  { t: 0.41,  label: "Israel–Gaza onset" },
  { t: 0.55,  label: "Houthi Red Sea escalation" },
  { t: 0.68,  label: "AI capex inflection (1st)" },
  { t: 0.79,  label: "Yen carry unwind" },
  { t: 0.91,  label: "Hormuz transit-fee tabling" },
  { t: 1.00,  label: "Now" },
];

// ─────────────────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────────────────

function dirClass(d){ return d === "up" ? "up" : d === "down" ? "down" : ""; }

function Sparkline({ series, dir, ledger }) {
  const w = 240, h = 64, pad = 4;
  const min = Math.min(...series), max = Math.max(...series);
  const rng = Math.max(0.05, max - min);
  const pts = series.map((v, i) => {
    const x = pad + (i / (series.length - 1)) * (w - pad * 2);
    const y = pad + (1 - (v - min) / rng) * (h - pad * 2);
    return [x, y];
  });
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  // recent slope segment
  const last8 = pts.slice(-8);
  const d2 = last8.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const stroke = dir === "down" ? "var(--down)" : dir === "up" ? "var(--up)" : "var(--ink)";

  // gridlines for Tufte mode
  const gridlines = [];
  for (let i = 1; i < 4; i++) {
    const y = pad + (i / 4) * (h - pad * 2);
    gridlines.push(<line key={i} className="gridline" x1={pad} y1={y} x2={w - pad} y2={y} />);
  }

  // dot for now point
  const last = pts[pts.length - 1];

  // band shading: high-stress band on top
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {gridlines}
      <path d={d} fill="none" stroke="var(--ink-3)" strokeWidth="1" />
      <path d={d2} fill="none" stroke={stroke} strokeWidth="1.5" />
      <circle cx={last[0]} cy={last[1]} r="2.2" fill={stroke} />
    </svg>
  );
}

function BandStrip({ idx, dir }) {
  const cls = dir === "down" ? "down" : dir === "up" ? "up" : "";
  return (
    <div className="band-strip">
      {BAND_SCALE.map((b, i) => (
        <i key={b} className={i === idx ? `on ${cls}` : ""} title={b} />
      ))}
      <span className="lbl">{BAND_SCALE[idx]}</span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// REGIONS
// ─────────────────────────────────────────────────────────────────────────────

function Masthead({ now }) {
  const dateStr = now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  const timeStr = now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }) + " UTC";
  return (
    <>
      <div className="mast">
        <div className="left">
          <div className="meta-row">
            <span><b>{dateStr}</b></span>
            <span>{timeStr}</span>
          </div>
          <div className="meta-row" style={{marginTop:6}}>
            <span>Edition <b>MMXXVI · 134</b></span>
            <span>Graph build <b>2026.05.13–r9.4</b></span>
          </div>
        </div>
        <div className="wordmark">
          Augur
          <span className="sub">A Reasoning Prosthetic · Vol. III</span>
        </div>
        <div className="right">
          <div className="meta-row right">
            <span>Operator <b>j.harrow</b></span>
            <span>Locus <b>SFO</b></span>
          </div>
          <div className="meta-row right" style={{marginTop:6}}>
            <span>Subscription <b>Cartographer</b></span>
            <span>Ingestion <b>14,402 sources nominal</b></span>
          </div>
        </div>
      </div>
      <div className="subhead">
        <div className="crumbs">
          <a className="active">Home</a>
          <a>Causal graph</a>
          <a>Topics</a>
          <a>Forecasts</a>
          <a>Counter-arguments</a>
          <a>Methodology</a>
          <a>Sources</a>
        </div>
        <div>Confidence regime <span style={{color:'var(--ink)',fontFamily:'var(--mono)'}}>moderate · widening</span></div>
      </div>
    </>
  );
}

function Region1State() {
  return (
    <section className="region">
      <div className="region-num">I.</div>
      <div className="region-head">
        <div className="q">Is the world<br/>improving, or worsening?</div>
        <div className="lede">
          <span className="dropcap">A</span>n indicative cross-section across five
          load-bearing dimensions. Bands are qualitative; the apparatus prefers
          calibrated language to false precision. Direction reads the last
          twenty-eight days against the trailing one-eighty.
        </div>
        <div className="stamp">As of 11:42 UTC</div>
      </div>
      <div className="dim-grid">
        {DIMENSIONS.map(d => (
          <div className="dim" key={d.key}>
            <div className="name">{d.name}</div>
            <div className="band-row">
              <div className="band">{d.band}</div>
              <div className={`glyph ${dirClass(d.direction)}`}>{d.glyph}</div>
            </div>
            <BandStrip idx={d.bandIdx} dir={d.direction} />
            <div className="annot">{d.note}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Region2Velocity({ ledger }) {
  return (
    <section className="region">
      <div className="region-num">II.</div>
      <div className="region-head">
        <div className="q">How fast?</div>
        <div className="lede">
          Trailing forty-eight months, sampled monthly. The darker segment
          marks the most recent eight observations. Rates and accelerations
          are stated qualitatively in the margin; their numerical forms appear
          in the underlying graph view.
        </div>
        <div className="stamp">2022.05 — 2026.05</div>
      </div>
      <div className="spark-grid">
        {DIMENSIONS.map(d => (
          <div className={`spark ${ledger ? 'ledger' : ''}`} key={d.key}>
            <div className="axis-top">
              <span>{d.name.split(" ")[0].toUpperCase()}</span>
              <span>{d.bandIdx === 0 ? "low stress" : d.bandIdx === 4 ? "crisis" : ""}</span>
            </div>
            <Sparkline series={d.series} dir={d.direction} ledger={ledger} />
            <div className="axis-bot">
              <span>2022</span>
              <span>2024</span>
              <span>now</span>
            </div>
            <div className="annot">
              <div><span className="rate">Rate</span>{d.rate}.</div>
              <div style={{marginTop:2}}><span className="rate">Accel</span>{d.accel}.</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Region3Changes() {
  return (
    <section className="region">
      <div className="region-num">III.</div>
      <div className="region-head">
        <div className="q">What changed<br/>in the last 24 hours?</div>
        <div className="lede">
          Edges added, weights revised, or nodes promoted in the past 24 hours,
          ranked by downstream impact across the active topic set. Magnitude
          is the small bar beneath each entry — five segments, qualitative.
        </div>
        <div className="stamp">7 of 142 surfaced</div>
      </div>
      <div className="ranked">
        {CHANGES_24H.map(c => (
          <div className="ranked-row" key={c.n}>
            <div className="n">{String(c.n).padStart(2, "0")}</div>
            <div>
              <div className="tag-cell">{c.dim}</div>
              <div className="impact-bar" style={{width: 60}}>
                {[0,1,2,3,4].map(i => <i key={i} className={i < c.impact ? "on" : ""} />)}
              </div>
            </div>
            <div className="body">{c.body}</div>
            <div className="meta">
              <div className={`delta ${c.metaDir}`}>{c.meta[0]}</div>
              <div style={{marginTop:4}}>{c.meta[1]}</div>
              <div style={{marginTop:4, color:'var(--ink-4)'}}>{c.edges} edges</div>
            </div>
            <div className="drill">→</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Region4Local() {
  return (
    <section className="region">
      <div className="region-num">IV.</div>
      <div className="region-head" style={{gridTemplateColumns:'1fr auto'}}>
        <div className="q">What does<br/>this mean for me?</div>
        <div className="stamp">Geolocation · approximate</div>
      </div>
      <div className="local">
        <div className="loc-head">
          <div className="name">{LOCAL.name}</div>
          <div className="coord">{LOCAL.coord}</div>
        </div>
        <div className="loc-dims">
          {LOCAL.dims.map(d => (
            <div className="loc-dim" key={d.name}>
              <div className="nm">{d.name}</div>
              <div className="bnd">{d.band}</div>
              <div className={`gl ${dirClass(d.dir)}`}>{d.glyph}</div>
            </div>
          ))}
        </div>
        <div className="loc-notes">
          <span className="lbl">Interpretation</span>
          {LOCAL.notes}
        </div>
        <div>
          <span className="lbl smallcap" style={{display:'block', marginBottom: 8, fontFamily:'var(--sans)'}}>Local changes · 24h</span>
          <div className="loc-changes">
            {LOCAL.changes.map((c, i) => (
              <div className="loc-change" key={i}>
                <span className="ct">{c.tag}</span>{c.body}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function Region5Topics() {
  return (
    <section className="region">
      <div className="region-num">V.</div>
      <div className="region-head">
        <div className="q">Where to look<br/>more closely.</div>
        <div className="lede">
          Concrete causal domains, each a sub-graph with its own bands,
          velocity, and 24-hour ledger. Listed in approximate order of
          current load on the operator's attention budget.
        </div>
        <div className="stamp">{TOPICS.length} of 28 visible</div>
      </div>
      <div className="topics">
        {TOPICS.map((t, i) => (
          <div className="topics-row" key={i}>
            <div className="idx">{String(i + 1).padStart(2, "0")}</div>
            <div className="ttl">{t.title}</div>
            <div className="gist">{t.gist}</div>
            <div className="nodes">{t.nodes}</div>
            <div className="arr">→</div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// SCRUBBER
// ─────────────────────────────────────────────────────────────────────────────

function Scrubber({ t, onChange, playing, setPlaying, rangeYears, setRangeYears }) {
  const trackRef = useRef(null);
  const [hover, setHover] = useState(null);

  const onMouse = useCallback((e) => {
    if (!trackRef.current) return;
    const r = trackRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    return x;
  }, []);

  const onDown = (e) => {
    const x = onMouse(e);
    onChange(x);
    const move = (ev) => onChange(onMouse(ev));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // ticks — major every year, minor every quarter
  const NOW_YEAR = 2026;
  const startYear = NOW_YEAR - rangeYears;
  const yearMarks = [];
  for (let y = startYear; y <= NOW_YEAR; y++) {
    const tt = (y - startYear) / rangeYears;
    yearMarks.push({ t: tt, label: y, major: true });
  }
  const minorMarks = [];
  for (let i = 0; i < rangeYears * 4; i++) {
    const tt = i / (rangeYears * 4);
    if (tt > 0 && tt < 1) minorMarks.push({ t: tt, major: false });
  }
  const allMarks = [...yearMarks, ...minorMarks];

  // play animation
  useEffect(() => {
    if (!playing) return;
    let raf;
    const tick = () => {
      onChange((prev => Math.min(1, (typeof prev === "number" ? prev : t) + 0.0008))(t));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, t, onChange]);

  // shown time
  const shown = useMemo(() => {
    const ms = Date.UTC(startYear, 0, 1);
    const span = Date.UTC(NOW_YEAR, 4, 13) - ms;
    return new Date(ms + span * t);
  }, [t, startYear]);

  const isLive = t > 0.998;

  return (
    <div className="scrub-wrap">
      <div className="scrub-hd">
        <div>
          Time scrubber ·
          <span style={{margin:'0 8px'}}>showing graph state at</span>
          <span className="now">{shown.toUTCString().replace("GMT", "UTC")}</span>
        </div>
        <div className="live">
          {isLive ? <><i style={{background:'var(--up)'}}/> Live · realtime ingest</> :
            <span style={{color:'var(--down)'}}>◷ historical · {Math.round((1 - t) * rangeYears * 365)} days ago</span>}
        </div>
      </div>
      <div className="scrub-track" ref={trackRef} onMouseDown={onDown}
           onMouseMove={(e) => setHover(onMouse(e))} onMouseLeave={() => setHover(null)}>
        <div className="ticks">
          {allMarks.map((m, i) => (
            <div key={i} className={`tick ${m.major ? "maj" : ""}`} style={{left: `${m.t * 100}%`}} />
          ))}
          {yearMarks.map((m, i) => (
            <div key={"l" + i} className="tick-lbl" style={{left: `${m.t * 100}%`}}>{m.label}</div>
          ))}
        </div>
        <div className="events">
          {SCRUB_EVENTS.map((e, i) => (
            <div key={i} className="event" style={{left: `${e.t * 100}%`}}>
              <div className="lbl" style={{
                transform: e.t > 0.92 ? "translate(-95%, -2px)" : e.t < 0.06 ? "translate(-5%, -2px)" : "translate(-50%, -2px)"
              }}>{e.label}</div>
            </div>
          ))}
        </div>
        <div className="playhead" style={{left: `${t * 100}%`}} />
      </div>
      <div className="scrub-foot">
        <div className="controls">
          <button onClick={() => setPlaying(!playing)} className={playing ? "on" : ""}>{playing ? "Pause" : "Play"}</button>
          <button onClick={() => onChange(Math.max(0, t - 0.02))}>−1d</button>
          <button onClick={() => onChange(Math.min(1, t + 0.02))}>+1d</button>
          <button onClick={() => onChange(1)}>Now</button>
          <button onClick={() => setRangeYears(rangeYears === 4 ? 1 : rangeYears === 1 ? 10 : 4)}>
            Range {rangeYears}y
          </button>
        </div>
        <div>← arrow keys · drag · click track</div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// APP
// ─────────────────────────────────────────────────────────────────────────────

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "surface": "warm",
  "density": "regular",
  "typePair": "Source Serif + Plex",
  "ledger": false,
  "showConvo": true,
  "showCoupling": false
}/*EDITMODE-END*/;

const TYPE_PAIRS = {
  "Source Serif + Plex": { serif: '"Source Serif 4", Georgia, serif', sans: '"IBM Plex Sans", system-ui, sans-serif', mono: '"IBM Plex Mono", monospace' },
  "Newsreader + Plex":   { serif: '"Newsreader", Georgia, serif',     sans: '"IBM Plex Sans", system-ui, sans-serif', mono: '"IBM Plex Mono", monospace' },
  "Spectral + Plex":     { serif: '"Spectral", Georgia, serif',       sans: '"IBM Plex Sans", system-ui, sans-serif', mono: '"IBM Plex Mono", monospace' },
  "Lora + Plex":         { serif: '"Lora", Georgia, serif',           sans: '"IBM Plex Sans", system-ui, sans-serif', mono: '"IBM Plex Mono", monospace' },
};

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [scrub, setScrub] = useState(1.0);
  const [playing, setPlaying] = useState(false);
  const [rangeYears, setRangeYears] = useState(4);
  const [now] = useState(() => new Date(Date.UTC(2026, 4, 13, 11, 42)));

  // Apply surface + density + type to document
  useEffect(() => {
    document.body.dataset.surface = t.surface;
    document.body.dataset.density = t.density;
    const fonts = TYPE_PAIRS[t.typePair] || TYPE_PAIRS["Source Serif + Plex"];
    document.documentElement.style.setProperty("--serif", fonts.serif);
    document.documentElement.style.setProperty("--sans", fonts.sans);
    document.documentElement.style.setProperty("--mono", fonts.mono);
  }, [t.surface, t.density, t.typePair]);

  // keyboard nav
  useEffect(() => {
    const fn = (e) => {
      if (e.key === "ArrowLeft") setScrub(s => Math.max(0, s - (e.shiftKey ? 0.05 : 0.005)));
      else if (e.key === "ArrowRight") setScrub(s => Math.min(1, s + (e.shiftKey ? 0.05 : 0.005)));
      else if (e.key === " ") { e.preventDefault(); setPlaying(p => !p); }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, []);

  return (
    <>
      <Masthead now={now} />
      <Region1State />
      <Region2Velocity ledger={t.ledger} />
      <div className="split">
        <Region3Changes />
        <Region4Local />
      </div>
      <Region5Topics />

      <div className="colophon">
        <div>Augur · a reasoning prosthetic · methodology under <i>Methodology</i> · all bands qualitative, all rates qualitative.</div>
        <div className="right">© MMXXVI · build r9.4 · graph hash 8f3a··d402</div>
      </div>

      <Scrubber t={scrub} onChange={setScrub} playing={playing} setPlaying={setPlaying}
                rangeYears={rangeYears} setRangeYears={setRangeYears} />

      {t.showConvo && (
        <div className="convo" onClick={() => alert("Ask Augur (placeholder)")}>
          Ask <span style={{fontFamily:'var(--serif)', fontStyle:'italic', textTransform:'none', letterSpacing:0}}>Augur</span>
          <span className="kbd">⌘K</span>
        </div>
      )}

      <TweaksPanel>
        <TweakSection label="Typography" />
        <TweakSelect label="Type pairing" value={t.typePair}
          options={Object.keys(TYPE_PAIRS)}
          onChange={(v) => setTweak("typePair", v)} />
        <TweakRadio label="Density" value={t.density}
          options={["compact", "regular", "loose"]}
          onChange={(v) => setTweak("density", v)} />

        <TweakSection label="Surface" />
        <TweakRadio label="Tone" value={t.surface}
          options={["warm", "cool", "ink"]}
          onChange={(v) => setTweak("surface", v)} />

        <TweakSection label="Information design" />
        <TweakToggle label="Tufte ledger lines"
          value={t.ledger}
          onChange={(v) => setTweak("ledger", v)} />
        <TweakToggle label="Show conversation entry"
          value={t.showConvo}
          onChange={(v) => setTweak("showConvo", v)} />
      </TweaksPanel>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
