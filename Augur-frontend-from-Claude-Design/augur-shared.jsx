// augur-shared.jsx — palette, type, data shared across the three options.
// Palette derived from Vindingur's named tokens:
//   Natt (night), Mose (moss), Bein (bone), Sand, Leirstein (claystone), Lin (linen)

const VINDINGUR = {
  natt:        "#171c1c",
  nattSoft:    "#26302a",
  mose:        "#5b6b4f",
  moseDeep:    "#3f4d36",
  bein:        "#e8dcc4",
  sand:        "#c8b58e",
  sandDeep:    "#a89370",
  leirstein:   "#9d5a3f",
  leirsteinDp: "#7a4029",
  lin:         "#f3ead4",
  linDeep:     "#ece2cd",
  ink:         "#171c1c",
  ink2:        "#3a3b34",
  ink3:        "#6e6a60",
  ink4:        "#9a948a",
  rule:        "#c8c2b3",
  ruleSoft:    "#dcd6c6",
};

// State colors — five qualitative bands, all in the earth register.
// Improving → moss · Stable → sand light · Strained → sand deep / ochre
// Deteriorating → leirstein · Crisis → leirstein deep (ember)
const STATES = {
  Improving:     { fill: "#5b6b4f", ring: "#3f4d36", label: "Improving"     },
  Stable:        { fill: "#c8b58e", ring: "#a89370", label: "Stable"        },
  Strained:      { fill: "#b08a4d", ring: "#8a6a33", label: "Strained"      },
  Deteriorating: { fill: "#9d5a3f", ring: "#7a4029", label: "Deteriorating" },
  Crisis:        { fill: "#6e3424", ring: "#4f2316", label: "Crisis"        },
};

// Five world dimensions — each with latinate binomial subtitle
// (echoes Linnaean herbarium plates).
const AUGUR_DIMS = [
  {
    key: "econ",
    name: "Economic Stability",
    latin: "Stabilitas œconomica",
    short: "ECON",
    state: "Strained",
    dir: "down",
    rate: "decelerating",
    accel: "drift narrowing",
    note: "Real-rate compression offset by widening sovereign spreads.",
    series: genSeries(48, 0.62, [ 0.05, -0.08, -0.12 ], 1337),
  },
  {
    key: "geo",
    name: "Geopolitical Tension",
    latin: "Tensio inter gentes",
    short: "GEO",
    state: "Deteriorating",
    dir: "down",
    rate: "accelerating",
    accel: "second-order rising",
    note: "Three concurrent corridors; coupling tightening across them.",
    series: genSeries(48, 0.78, [ 0.12, 0.08, 0.18 ], 4242),
  },
  {
    key: "res",
    name: "Resource Availability",
    latin: "Copia rerum",
    short: "RES",
    state: "Strained",
    dir: "down",
    rate: "steady decline",
    accel: "zero",
    note: "Phosphate and gallium constraints compounding; not yet substitutable.",
    series: genSeries(48, 0.55, [ -0.04, -0.06, -0.05 ], 9001),
  },
  {
    key: "env",
    name: "Environmental Stress",
    latin: "Tensio terrae",
    short: "ENV",
    state: "Deteriorating",
    dir: "flat",
    rate: "plateau (high)",
    accel: "zero",
    note: "Anomaly index plateaued at elevated level; no reversion signal.",
    series: genSeries(48, 0.72, [ 0.02, 0.01, 0.0 ], 555),
  },
  {
    key: "struct",
    name: "Structural Change",
    latin: "Mutatio structurae",
    short: "STR",
    state: "Improving",
    dir: "up",
    rate: "accelerating",
    accel: "compounding",
    note: "Adaptation velocity in semiconductors and energy outpacing baseline disruption.",
    series: genSeries(48, 0.40, [ 0.06, 0.10, 0.14 ], 7777),
  },
];

const AUGUR_CHANGES = [
  { dim: "geo",    impact: 5, body: "Iranian transit-fee proposal for Hormuz formally tabled.", meta: "Brent +4.2%", time: "06:14 UTC", root: "Iran–Israel" },
  { dim: "res",    impact: 5, body: "Russian phosphate export quota tightened 18%.",            meta: "DAP +7.1%",   time: "04:02 UTC", root: "Fertilizer" },
  { dim: "econ",   impact: 4, body: "ECB minutes signal earlier dovish pivot; Bund 2s10s flattens 11bp.", meta: "Bund −11bp", time: "09:47 UTC", root: "Fiscal" },
  { dim: "res",    impact: 4, body: "MOFCOM rare-earth licensing extended to Ga, Ge precursors.", meta: "Ga +12.4%", time: "02:30 UTC", root: "Semis" },
  { dim: "env",    impact: 3, body: "Brazilian soy harvest revised −3.1% on Mato Grosso heat dome.", meta: "Soy +2.6%", time: "19:10 UTC", root: "Fertilizer" },
  { dim: "struct", impact: 3, body: "Hyperscaler order book flat MoM for first time in eleven quarters.", meta: "AI capex Δ≈0", time: "Q1", root: "AI" },
  { dim: "econ",   impact: 2, body: "Argentine peso parallel-rate spread narrows to 6.2%, tightest in 19 months.", meta: "ARS gap 6.2%", time: "EOD", root: "Fiscal" },
];

const AUGUR_TOPICS = [
  { title: "Iran–Israel and regional energy",       gist: "Hormuz transit risk, LNG redirection, inventory cycles.",  nodes: 142, edges: 487, weight: "high" },
  { title: "Fertilizer and the global food chain",  gist: "Phosphate, potash, urea — substitution geometry.",         nodes:  96, edges: 312, weight: "high" },
  { title: "Semiconductor supply geography",        gist: "Taiwan concentration, ASML throughput, Ga/Ge licensing.",  nodes: 118, edges: 421, weight: "high" },
  { title: "U.S. fiscal trajectory",                gist: "Coupon issuance, foreign sponsorship, term premium.",      nodes:  84, edges: 268, weight: "med"  },
  { title: "Climate-trade frictions",               gist: "CBAM, transition tariffs, implicit carbon price.",         nodes:  67, edges: 198, weight: "med"  },
  { title: "AI labor displacement",                 gist: "Task-level substitution, occupational migration latency.", nodes:  59, edges: 174, weight: "med"  },
  { title: "Sahel destabilization",                 gist: "Coup contagion, Saharan transit, Mediterranean intake.",   nodes:  73, edges: 224, weight: "med"  },
];

const AUGUR_LOCAL = {
  name: "San Francisco, California",
  coord: "37.77 N · 122.42 W",
  dims: [
    { key: "econ",   state: "Stable",        dir: "flat" },
    { key: "geo",    state: "Strained",      dir: "down" },
    { key: "res",    state: "Strained",      dir: "down" },
    { key: "env",    state: "Deteriorating", dir: "down" },
    { key: "struct", state: "Improving",     dir: "up"   },
  ],
  changes: [
    "PG&E summer reserve margin revised to 11.4% — third monthly downgrade.",
    "Bay Area median rent decouples downward from national series.",
    "Semis sector job-postings index +18% MoM, clustered in 95054 and 94043.",
  ],
};

const AUGUR_EVENTS = [
  { t: 0.02, label: "Russia–Ukraine"      },
  { t: 0.16, label: "Energy crisis peak"  },
  { t: 0.28, label: "SVB stress"          },
  { t: 0.41, label: "Israel–Gaza"         },
  { t: 0.55, label: "Red Sea escalation"  },
  { t: 0.68, label: "AI capex inflection" },
  { t: 0.79, label: "Yen carry unwind"    },
  { t: 0.91, label: "Hormuz tabling"      },
];

// quasi-deterministic series
function genSeries(n, base, slopes, seed) {
  const pts = [];
  let v = base;
  let s = seed;
  const rand = () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
  for (let i = 0; i < n; i++) {
    const ph = i / n;
    const sl = ph < .33 ? slopes[0] : ph < .66 ? slopes[1] : slopes[2];
    v += sl / n + (rand() - .5) * 0.07;
    v = Math.max(0.04, Math.min(0.96, v));
    pts.push(v);
  }
  return pts;
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared atoms

// Sparkline ink — draws as a fine ink line, tail darker.
// `tone` selects the line color from the palette.
function AugurSpark({ series, color, height = 56, width = 220, tail = 8, dot = true }) {
  const pad = 4;
  const min = Math.min(...series), max = Math.max(...series);
  const rng = Math.max(0.05, max - min);
  const pts = series.map((v, i) => {
    const x = pad + (i / (series.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (v - min) / rng) * (height - pad * 2);
    return [x, y];
  });
  const path = (arr) => arr.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{display:"block", width:"100%", height}}>
      <path d={path(pts)} fill="none" stroke={color || "#6e6a60"} strokeWidth=".8" opacity=".55" />
      <path d={path(pts.slice(-tail))} fill="none" stroke={color || "#171c1c"} strokeWidth="1.4" />
      {dot && <circle cx={last[0]} cy={last[1]} r="2.2" fill={color || "#171c1c"} />}
    </svg>
  );
}

// Direction arrow as a small ink glyph (no emoji)
function DirGlyph({ dir, color = "currentColor" }) {
  const g = dir === "up" ? "↗" : dir === "down" ? "↘" : "→";
  return <span style={{ color, fontFamily: "'JetBrains Mono', monospace", fontWeight: 500 }}>{g}</span>;
}

// Almanac-ribbon scrubber, shared across options
function AlmanacScrubber({ t, onChange, events, palette, accent }) {
  const trackRef = React.useRef(null);
  const NOW_YEAR = 2026;
  const startYear = NOW_YEAR - 4;

  const onMouse = (e) => {
    if (!trackRef.current) return 0;
    const r = trackRef.current.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  };
  const onDown = (e) => {
    onChange(onMouse(e));
    const move = (ev) => onChange(onMouse(ev));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const shown = React.useMemo(() => {
    const ms = Date.UTC(startYear, 0, 1);
    const span = Date.UTC(NOW_YEAR, 4, 13) - ms;
    return new Date(ms + span * t);
  }, [t]);
  const isLive = t > 0.998;

  return (
    <div className="alm-scrub" style={{
      background: palette.lin, borderTop: `1px solid ${palette.natt}`,
      padding: "10px 36px 12px", fontFamily: "'Newsreader', serif", color: palette.natt
    }}>
      <div style={{display:"flex", justifyContent:"space-between", alignItems:"baseline",
                   fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:14, color: palette.ink3,
                   marginBottom:6}}>
        <div>Almanac · showing graph state at
          <span style={{margin:"0 8px", fontFamily:"'JetBrains Mono', monospace", color: palette.natt,
                        fontSize:11, fontStyle:"normal", letterSpacing:".02em"}}>
            {shown.toUTCString().replace("GMT","UTC")}
          </span>
        </div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10.5, fontStyle:"normal",
                     letterSpacing:".08em", color: isLive ? palette.mose : palette.leirstein, textTransform:"uppercase"}}>
          {isLive ? "● live" : `◷ ${Math.round((1-t) * 365 * 4)}d past`}
        </div>
      </div>
      <div ref={trackRef} onMouseDown={onDown}
           style={{position:"relative", height:32, borderTop:`1px solid ${palette.ink}`,
                   borderBottom:`1px solid ${palette.ink}`, cursor:"ew-resize", userSelect:"none",
                   background: `repeating-linear-gradient(90deg, transparent 0 23px, ${palette.rule} 23px 24px)`}}>
        {/* year labels */}
        {[0,1,2,3,4].map(i => {
          const tt = i / 4;
          return (
            <div key={i} style={{position:"absolute", left:`${tt*100}%`, top:0, bottom:0,
                                 borderLeft: `1px solid ${palette.ink3}`}}>
              <div style={{position:"absolute", bottom:"100%", left:0, transform:"translate(2px,-2px)",
                           fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, color: palette.ink3,
                           letterSpacing:".04em"}}>{startYear + i}</div>
            </div>
          );
        })}
        {/* events */}
        {(events || []).map((e,i) => (
          <div key={i} style={{position:"absolute", left:`${e.t*100}%`, top:6, bottom:6, width:1, background: palette.natt}}>
            <div style={{position:"absolute", bottom:"100%", left:"50%",
                         transform: e.t > .9 ? "translate(-95%,-3px)" : e.t < .06 ? "translate(-5%,-3px)" : "translate(-50%,-3px)",
                         fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:11,
                         color: palette.ink2, whiteSpace:"nowrap", background: palette.lin, padding:"0 4px"}}>
              {e.label}
            </div>
          </div>
        ))}
        {/* playhead */}
        <div style={{position:"absolute", left:`${t*100}%`, top:-6, bottom:-6, width:0,
                     borderLeft:`1.5px solid ${accent || palette.leirstein}`}}>
          <div style={{position:"absolute", top:-1, left:-4, width:8, height:8, background: accent || palette.leirstein}}/>
          <div style={{position:"absolute", bottom:-1, left:-4, width:8, height:8, background: accent || palette.leirstein}}/>
        </div>
      </div>
      <div style={{display:"flex", justifyContent:"space-between", marginTop:6,
                   fontFamily:"'JetBrains Mono', monospace", fontSize:10, color: palette.ink4, letterSpacing:".04em"}}>
        <div style={{display:"flex", gap:14}}>
          <span style={{cursor:"pointer"}} onClick={() => onChange(Math.max(0, t - 0.005))}>← 1d</span>
          <span style={{cursor:"pointer"}} onClick={() => onChange(Math.min(1, t + 0.005))}>+1d →</span>
          <span style={{cursor:"pointer", color: palette.natt}} onClick={() => onChange(1)}>NOW</span>
        </div>
        <span>drag · click track</span>
      </div>
    </div>
  );
}

// Export to window so other JSX scripts see them.
Object.assign(window, {
  VINDINGUR, STATES,
  AUGUR_DIMS, AUGUR_CHANGES, AUGUR_TOPICS, AUGUR_LOCAL, AUGUR_EVENTS,
  AugurSpark, DirGlyph, AlmanacScrubber,
});
