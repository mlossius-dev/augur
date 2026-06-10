"use strict";

const { useState, useEffect, useRef, useMemo, useCallback } = React;

// ── Palette ───────────────────────────────────────────────────────────────────

const P = {
  natt: "#171c1c", nattSoft: "#26302a",
  mose: "#5b6b4f", moseDeep: "#3f4d36",
  bein: "#e8dcc4", sand: "#c8b58e", sandDeep: "#a89370",
  leirstein: "#9d5a3f", leirsteinDp: "#7a4029",
  lin: "#f3ead4", linDeep: "#ece2cd",
  ink2: "#3a3b34", ink3: "#6e6a60", ink4: "#9a948a",
  rule: "#c8c2b3", ruleSoft: "#dcd6c6",
};

const STATES = {
  Improving:     { fill: "#5b6b4f", ring: "#3f4d36", label: "Improving"     },
  Stable:        { fill: "#c8b58e", ring: "#a89370", label: "Stable"        },
  Strained:      { fill: "#b08a4d", ring: "#8a6a33", label: "Strained"      },
  Deteriorating: { fill: "#9d5a3f", ring: "#7a4029", label: "Deteriorating" },
  Crisis:        { fill: "#6e3424", ring: "#4f2316", label: "Crisis"        },
  Unknown:       { fill: "#9a948a", ring: "#6e6a60", label: "Unknown"       },
};

const DIM_META = {
  economic_stability:   { short: "ECON", latin: "Stabilitas œconomica"  },
  geopolitical_tension: { short: "GEO",  latin: "Tensio inter gentes"   },
  resource_availability:{ short: "RES",  latin: "Copia rerum"           },
  environmental_stress: { short: "ENV",  latin: "Tensio terrae"         },
  structural_change:    { short: "STR",  latin: "Mutatio structurae"    },
};

const BAND_U = { Improving: 0.1, Stable: 0.3, Strained: 0.5, Deteriorating: 0.7, Crisis: 0.9, Unknown: 0.5 };
const BAND_ORDER = ["Improving", "Stable", "Strained", "Deteriorating", "Crisis"];

const SCRUB_EVENTS = [
  { t: 0.02, label: "Russia–Ukraine"      },
  { t: 0.16, label: "Energy crisis peak"  },
  { t: 0.28, label: "SVB stress"          },
  { t: 0.41, label: "Israel–Gaza"         },
  { t: 0.55, label: "Red Sea escalation"  },
  { t: 0.68, label: "AI capex inflection" },
  { t: 0.79, label: "Yen carry unwind"    },
  { t: 0.91, label: "Hormuz tabling"      },
];

const SCRUB_START_MS = Date.UTC(new Date().getFullYear() - 4, 0, 1);
const SCRUB_END_MS   = Date.now();
const SCRUB_SPAN_MS  = SCRUB_END_MS - SCRUB_START_MS;
const SCRUB_START_YR = new Date(SCRUB_START_MS).getFullYear();
const SCRUB_END_YR   = new Date(SCRUB_END_MS).getFullYear();

// ── Adapters ──────────────────────────────────────────────────────────────────

function stateTitle(s) {
  const m = { improving:"Improving", stable:"Stable", strained:"Strained",
              deteriorating:"Deteriorating", crisis:"Crisis", unknown:"Unknown" };
  return (s && m[s.toLowerCase()]) || "Unknown";
}

function dirGlyph(d) {
  return { improving:"↗", steady:"→", worsening:"↘", unknown:"—" }[d] || "→";
}

function adaptDim(d) {
  const st   = stateTitle(d.state);
  const meta = DIM_META[d.dimension] || { short: (d.dimension||"?").slice(0,4).toUpperCase(), latin: "" };
  const series = (d.sparkline || []).map(p => p.total_count > 0 ? p.active_count / p.total_count : 0);
  return {
    key:    d.dimension,
    name:   d.label,
    short:  meta.short,
    latin:  meta.latin,
    state:  st,
    dir:    dirGlyph(d.direction),
    series,
    active: d.active_conditions,
    total:  d.total_conditions,
  };
}

function adaptChange(c) {
  return {
    id:           c.change_id,
    type:         c.change_type,
    summary:      c.summary,
    dim:          c.dimension,
    dimLabel:     c.dimension_label,
    targetId:     c.target_id,
    targetType:   c.target_type,
    targetName:   c.target_name,
    weightBefore: c.weight_before,
    weightAfter:  c.weight_after,
    occurredAt:   c.occurred_at,
  };
}

function compositeU(dims) {
  if (!dims || !dims.length) return 0.5;
  return dims.reduce((a, d) => a + (BAND_U[d.state] ?? 0.5), 0) / dims.length;
}

function verdictHeadline(u) {
  if (u < 0.2)  return "At present, yes.";
  if (u < 0.4)  return "Holding, for now.";
  if (u < 0.6)  return "Under pressure.";
  if (u < 0.75) return "Not at present.";
  return "Under serious stress.";
}

function verdictSubline(dims) {
  if (!dims || !dims.length) return "No dimension data.";
  const counts = {};
  dims.forEach(d => { counts[d.state] = (counts[d.state] || 0) + 1; });
  return BAND_ORDER.filter(b => counts[b]).map(b => `${counts[b]} ${b.toLowerCase()}`).join(", ") + ".";
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function toRoman(n) {
  const map = [[1000,"M"],[900,"CM"],[500,"D"],[400,"CD"],[100,"C"],[90,"XC"],
               [50,"L"],[40,"XL"],[10,"X"],[9,"IX"],[5,"V"],[4,"IV"],[1,"I"]];
  let out = ""; for (const [v,s] of map) { while (n >= v) { out += s; n -= v; } }
  return out;
}

function fmtN(n) { return n == null ? "—" : Number(n).toLocaleString("en-US"); }

function fmtRelTime(iso) {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 60)  return `${m}m ago`;
  const h = Math.floor(ms / 3600000);
  if (h < 24)  return `${h}h ago`;
  return new Date(iso).toLocaleDateString("en-GB", { month: "short", day: "numeric" });
}

const mono = "'JetBrains Mono', monospace";
const serif = "'Newsreader', serif";
const display = "'Cormorant Garamond', serif";

// ── AugurSpark ────────────────────────────────────────────────────────────────

function AugurSpark({ series, color, height = 56, width = 220, tail = 8, dot = true }) {
  if (!series || series.length < 2) {
    return (
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
           style={{ display: "block", width: "100%", height }}>
        <line x1="0" y1={height/2} x2={width} y2={height/2}
              stroke={color || P.ink4} strokeWidth="0.8" opacity="0.4"/>
      </svg>
    );
  }
  const pad = 4;
  const min = Math.min(...series), max = Math.max(...series);
  const rng = Math.max(0.05, max - min);
  const pts = series.map((v, i) => [
    pad + (i / (series.length - 1)) * (width - pad * 2),
    pad + (1 - (v - min) / rng) * (height - pad * 2),
  ]);
  const path = arr => arr.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         style={{ display: "block", width: "100%", height }}>
      <path d={path(pts)} fill="none" stroke={color||P.ink3} strokeWidth="0.8" opacity="0.55"/>
      <path d={path(pts.slice(-tail))} fill="none" stroke={color||P.natt} strokeWidth="1.4"/>
      {dot && <circle cx={last[0]} cy={last[1]} r="2.2" fill={color||P.natt}/>}
    </svg>
  );
}

// ── VerdictDial ───────────────────────────────────────────────────────────────

function VerdictDial({ dims }) {
  const W = 560, H = 300, cx = W / 2, cy = 262, R = 198;

  const pt = (u, r) => {
    const a = Math.PI + u * Math.PI;
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  };
  const arc = (u0, u1, r) => {
    const [x0,y0] = pt(u0,r), [x1,y1] = pt(u1,r);
    return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  };

  const cu = compositeU(dims);
  const byBand = {};
  const dots = (dims || []).map(d => {
    const u = BAND_U[d.state] ?? 0.5;
    byBand[u] = (byBand[u] || 0) + 1;
    return { d, u, layer: byBand[u] - 1 };
  });
  const [nx, ny] = pt(cu, R - 30);
  const [ax, ay] = pt(cu + 0.035, R - 52);
  const worsening = (dims || []).filter(d => d.dir === "↘").length;
  const improving = (dims || []).filter(d => d.dir === "↗").length;
  const trendGlyph = worsening > improving ? "↘" : improving > worsening ? "↗" : "→";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: 580, margin: "0 auto" }}>
      <path d={arc(0,1,R+14)} fill="none" stroke={P.natt} strokeWidth="0.8"/>
      <path d={arc(0,1,R+10)} fill="none" stroke={P.ink3} strokeWidth="0.35"/>

      {BAND_ORDER.map((b, i) => {
        const st = STATES[b];
        const u0 = i/5 + 0.006, u1 = (i+1)/5 - 0.006;
        const um = (u0+u1)/2, [lx,ly] = pt(um, R+26), deg = um*180 - 90;
        return (
          <g key={b}>
            <path d={arc(u0,u1,R)} fill="none" stroke={st.fill} strokeWidth="16" strokeLinecap="butt"/>
            <text x={lx} y={ly} textAnchor="middle" fontFamily={mono} fontSize="8.5" letterSpacing=".16em"
                  fill={P.ink3} transform={`rotate(${deg},${lx},${ly})`}>{b.toUpperCase()}</text>
          </g>
        );
      })}

      {Array.from({ length: 21 }, (_,i) => i/20).map((u,i) => {
        const big = i%4===0, [x0,y0] = pt(u,R-12), [x1,y1] = pt(u, big?R-20:R-16);
        return <line key={i} x1={x0} y1={y0} x2={x1} y2={y1} stroke={P.ink3} strokeWidth={big?.7:.4}/>;
      })}

      {dots.map(({ d, u, layer }) => {
        const st = STATES[d.state] || STATES.Unknown;
        const [x,y] = pt(u, R-38-layer*17);
        return (
          <g key={d.key}>
            <circle cx={x} cy={y} r="5" fill={st.fill} stroke={st.ring} strokeWidth="0.8"/>
            <text x={x} y={y-9} textAnchor="middle" fontFamily={mono} fontSize="7.5" letterSpacing=".14em"
                  fill={P.ink2}>{d.short}</text>
          </g>
        );
      })}

      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={P.natt} strokeWidth="1.6"/>
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={P.leirstein} strokeWidth="0.7"/>
      <circle cx={cx} cy={cy} r="7" fill={P.natt}/>
      <circle cx={cx} cy={cy} r="3" fill={P.lin}/>
      <text x={ax} y={ay} textAnchor="middle" fontFamily={mono} fontSize="13" fill={P.leirstein}>
        {trendGlyph}
      </text>

      <line x1={cx-R-14} y1={cy} x2={cx+R+14} y2={cy} stroke={P.natt} strokeWidth="0.8"/>
      <text x={cx-R-10} y={cy+16} fontFamily={display} fontStyle="italic" fontSize="11" fill={P.ink3}>
        status mundi
      </text>
      <text x={cx+R+10} y={cy+16} textAnchor="end" fontFamily={mono} fontSize="8.5" letterSpacing=".12em" fill={P.ink3}>
        COMPOSITE · 5 DOMAINS · 28D
      </text>
    </svg>
  );
}

// ── DomainCard ────────────────────────────────────────────────────────────────

function DomainCard({ dim, changes, selected, onClick }) {
  const st = STATES[dim.state] || STATES.Unknown;
  const dimChanges = changes.filter(c => c.dim === dim.key);
  const top = dimChanges[0];
  const isSel = selected === dim.key;

  return (
    <div className={"ag-card" + (isSel ? " ag-card-sel" : "")} onClick={onClick}
         style={{ flex:1, minWidth:0,
                  border:`1px solid ${isSel ? st.ring : P.natt}`,
                  boxShadow: isSel ? `0 3px 0 ${st.ring}` : `0 2px 0 ${P.natt}22`,
                  cursor:"pointer", position:"relative", display:"flex", flexDirection:"column" }}>
      <div style={{ height:6, background:st.fill, borderBottom:`1px solid ${st.ring}` }}/>
      <div style={{ padding:"12px 14px 10px", flex:1, display:"flex", flexDirection:"column" }}>
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"baseline" }}>
          <div>
            <div style={{ fontFamily:serif, fontWeight:600, fontSize:14.5, color:P.natt, lineHeight:1.15 }}>
              {dim.name}
            </div>
            {dim.latin && (
              <div style={{ fontFamily:display, fontStyle:"italic", fontSize:11.5, color:P.ink3, marginTop:1 }}>
                {dim.latin}
              </div>
            )}
          </div>
          <div style={{ fontFamily:mono, fontSize:9, letterSpacing:".12em", color:P.ink4 }}>{dim.short}</div>
        </div>

        <div style={{ display:"flex", alignItems:"baseline", gap:8, marginTop:8 }}>
          <span style={{ fontFamily:display, fontWeight:600, fontSize:23, color:st.ring, lineHeight:1 }}>
            {st.label}
          </span>
          <span style={{ fontFamily:mono, fontSize:13, color:st.ring }}>{dim.dir}</span>
        </div>

        <div style={{ marginTop:8, borderTop:`1px solid ${P.ruleSoft}`, paddingTop:6 }}>
          <AugurSpark series={dim.series} color={st.ring} height={38} width={220} dot={true}/>
        </div>

        <div style={{ marginTop:8, borderTop:`1px solid ${P.ruleSoft}`, paddingTop:7, flex:1 }}>
          <div style={{ fontFamily:mono, fontSize:9, letterSpacing:".14em", color:P.ink3, marginBottom:4 }}>
            24H · {dimChanges.length} CHANGE{dimChanges.length===1?"":"S"}
          </div>
          {top ? (
            <div style={{ fontFamily:serif, fontSize:12, lineHeight:1.35, color:P.ink2, fontStyle:"italic",
                          display:"-webkit-box", WebkitLineClamp:2, WebkitBoxOrient:"vertical", overflow:"hidden" }}>
              {top.summary}
            </div>
          ) : (
            <div style={{ fontFamily:display, fontStyle:"italic", fontSize:12, color:P.ink4 }}>
              Quiet; no changes in this window.
            </div>
          )}
        </div>

        <div className="ag-card-cta"
             style={{ marginTop:10, paddingTop:8, borderTop:`1px solid ${P.rule}`,
                      display:"flex", justifyContent:"space-between", alignItems:"center",
                      fontFamily:mono, fontSize:9.5, letterSpacing:".14em",
                      color: isSel ? st.ring : P.leirstein }}>
          <span>{isSel ? "CLOSE LEDGER" : "OPEN LEDGER"}</span>
          <span style={{ fontSize:12 }}>{isSel ? "↑" : "→"}</span>
        </div>
      </div>
    </div>
  );
}

// ── DomainLedger ──────────────────────────────────────────────────────────────

function DomainLedger({ dim, changes, onClose }) {
  const st = STATES[dim.state] || STATES.Unknown;
  const dimChanges = changes.filter(c => c.dim === dim.key);

  const sparkStart = (() => {
    if (!dim.series.length) return "—";
    const d = new Date();
    d.setDate(d.getDate() - dim.series.length * 7);
    return `${d.getFullYear()}.${String(d.getMonth()+1).padStart(2,"0")}`;
  })();

  return (
    <div style={{ border:`1px solid ${st.ring}`, borderTop:`3px solid ${st.fill}`,
                  background:P.lin, padding:"20px 28px 24px", marginTop:-1,
                  display:"grid", gridTemplateColumns:"300px 1fr 300px", gap:32,
                  animation:"agFade .3s ease" }}>
      <div>
        <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.ink3, marginBottom:6 }}>
          LEDGER · {dim.short}
        </div>
        {dim.latin && (
          <div style={{ fontFamily:display, fontStyle:"italic", fontSize:26, lineHeight:1.05, color:P.natt }}>
            {dim.latin}
          </div>
        )}
        <div style={{ marginTop:12, fontFamily:serif, fontSize:14, lineHeight:1.5, color:P.ink2 }}>
          {dim.active} of {dim.total} conditions active.
        </div>
        <div style={{ marginTop:14, fontFamily:mono, fontSize:10, color:P.ink3, lineHeight:1.7, letterSpacing:".04em" }}>
          state · {st.label}<br/>
          trend · {dim.dir}<br/>
          series · ~{dim.series.length} wk weekly active/total ratio
        </div>
        <div style={{ marginTop:16 }}>
          <span onClick={onClose}
                style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.leirstein,
                          cursor:"pointer", borderBottom:`1px solid ${P.leirstein}`, paddingBottom:2 }}>
            ↑ CLOSE
          </span>
        </div>
      </div>

      <div>
        <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.ink3, marginBottom:6 }}>
          VELOCITY · ACTIVE / TOTAL RATIO
        </div>
        <div style={{ borderTop:`1px solid ${P.rule}`, borderBottom:`1px solid ${P.rule}`, padding:"4px 0" }}>
          <AugurSpark series={dim.series} color={st.ring} height={110} width={520}/>
        </div>
        <div style={{ display:"flex", justifyContent:"space-between",
                      fontFamily:mono, fontSize:9, color:P.ink4, marginTop:3 }}>
          <span>{sparkStart}</span><span>NOW</span>
        </div>
      </div>

      <div>
        <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.ink3, marginBottom:6 }}>
          CHANGES · 24H
        </div>
        {dimChanges.length === 0 ? (
          <div style={{ fontFamily:display, fontStyle:"italic", fontSize:13.5, color:P.ink3 }}>
            No structural changes in this window.
          </div>
        ) : dimChanges.map((c, i) => (
          <div key={c.id||i}
               style={{ borderTop: i===0?`1px solid ${P.natt}`:`1px solid ${P.ruleSoft}`, padding:"8px 0" }}>
            <div style={{ display:"flex", justifyContent:"space-between",
                          fontFamily:mono, fontSize:9.5, color:P.ink3, marginBottom:3, letterSpacing:".04em" }}>
              <span>{fmtRelTime(c.occurredAt)}</span>
              <span style={{ color:st.ring }}>
                {c.weightBefore!=null && c.weightAfter!=null
                  ? `${c.weightBefore.toFixed(2)} → ${c.weightAfter.toFixed(2)}`
                  : (c.type||"").replace(/_/g," ")}
              </span>
            </div>
            <div style={{ fontFamily:serif, fontSize:13, lineHeight:1.4, color:P.natt }}>
              {c.summary}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── CausalThreads ─────────────────────────────────────────────────────────────

function CausalThreads({ topics, changes, onTopicClick }) {
  const [open, setOpen] = useState(null);

  if (!topics || topics.length === 0) {
    return (
      <div style={{ fontFamily:display, fontStyle:"italic", fontSize:14, color:P.ink3, padding:"16px 0" }}>
        No topics defined. Topics can be created via the CLI.
      </div>
    );
  }

  return (
    <div>
      {topics.map((t, i) => {
        const isOpen = open === i;
        const words = (t.name || "").toLowerCase().split(/\s+/).filter(w => w.length > 3);
        const linked = changes.filter(c =>
          words.some(w => (c.summary||"").toLowerCase().includes(w))
        );
        const latest = linked[0];

        return (
          <div key={t.topic_id||i} className="ag-thread"
               onClick={() => setOpen(isOpen ? null : i)}
               style={{ borderTop: i===0?`1px solid ${P.natt}`:`1px solid ${P.ruleSoft}`,
                         padding:"11px 6px", cursor:"pointer",
                         background: isOpen ? P.lin : "transparent" }}>
            <div style={{ display:"grid", gridTemplateColumns:"30px 380px 1fr 100px 22px", gap:18, alignItems:"baseline" }}>
              <div style={{ fontFamily:mono, fontSize:10, color:P.ink4 }}>
                {String(i+1).padStart(2,"0")}
              </div>
              <div style={{ fontFamily:display, fontStyle:"italic", fontSize:17.5, color:P.natt, lineHeight:1.15 }}>
                {t.name}
              </div>
              <div style={{ fontFamily:serif, fontSize:13, lineHeight:1.4, color: latest ? P.ink2 : P.ink4 }}>
                {latest ? (
                  <span>
                    <span style={{ fontFamily:mono, fontSize:9, letterSpacing:".1em", color:P.leirstein, marginRight:8 }}>24H</span>
                    {latest.summary}
                  </span>
                ) : (
                  <span style={{ fontStyle:"italic" }}>No new changes this window.</span>
                )}
              </div>
              <div style={{ fontFamily:mono, fontSize:10, color:P.ink3, textAlign:"right", letterSpacing:".04em" }}>
                {t.node_count} nodes
              </div>
              <div style={{ fontFamily:mono, fontSize:12, color:P.leirstein, textAlign:"right" }}>
                {isOpen ? "↑" : "→"}
              </div>
            </div>

            {isOpen && (
              <div style={{ padding:"10px 0 4px 48px", display:"grid",
                            gridTemplateColumns:"360px 1fr", gap:28, animation:"agFade .25s ease" }}>
                <div style={{ fontFamily:serif, fontSize:13.5, lineHeight:1.5, color:P.ink2, fontStyle:"italic" }}>
                  {t.description || "No description available."}
                </div>
                <div>
                  {linked.slice(1, 4).map((c, j) => (
                    <div key={c.id||j}
                         style={{ borderTop:`1px solid ${P.ruleSoft}`, padding:"6px 0",
                                   fontFamily:serif, fontSize:12.5, lineHeight:1.4, color:P.ink2 }}>
                      <span style={{ fontFamily:mono, fontSize:9, color:P.ink4, marginRight:8 }}>
                        {fmtRelTime(c.occurredAt)}
                      </span>
                      {c.summary}
                    </div>
                  ))}
                  <div style={{ marginTop:8, fontFamily:mono, fontSize:9.5, letterSpacing:".14em",
                                color:P.leirstein, cursor:"pointer" }}
                       onClick={(e) => { e.stopPropagation(); onTopicClick(t.topic_id); }}>
                    ENTER SUB-GRAPH →
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── YourLatitude ──────────────────────────────────────────────────────────────

function YourLatitude({ geoData, onRequest }) {
  const diamond = (
    <span style={{ position:"absolute", top:-5, left:16, width:10, height:10,
                   background:P.leirstein, border:`1px solid ${P.natt}`, transform:"rotate(45deg)" }}/>
  );

  const shell = (children) => (
    <div style={{ border:`1px solid ${P.natt}`, background:P.lin,
                  padding:"14px 16px 16px", position:"relative", height:"100%", boxSizing:"border-box" }}>
      {diamond}
      <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.ink3, marginBottom:6 }}>
        YOUR LATITUDE
      </div>
      {children}
    </div>
  );

  if (!geoData) return shell(
    <>
      <div style={{ fontFamily:serif, fontSize:13, color:P.ink2, lineHeight:1.5, marginBottom:12 }}>
        Scope this view to your region.
      </div>
      <button onClick={onRequest}
              style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".14em", color:P.lin,
                        background:P.natt, border:"none", padding:"6px 12px", cursor:"pointer" }}>
        USE MY LOCATION
      </button>
    </>
  );

  if (geoData === "loading") return shell(
    <div style={{ fontFamily:display, fontStyle:"italic", fontSize:14, color:P.ink3 }}>Locating…</div>
  );

  if (geoData === "denied") return shell(
    <div style={{ fontFamily:serif, fontSize:13, color:P.ink3, fontStyle:"italic", lineHeight:1.5 }}>
      Location access denied. Grant geolocation permission to see region-scoped data.
    </div>
  );

  const geoDims = (geoData.dimensions || []).map(adaptDim);

  return shell(
    <>
      <div style={{ fontFamily:display, fontStyle:"italic", fontSize:18, color:P.natt }}>
        {geoData.region?.display_name || "Region"}
      </div>
      <div style={{ fontFamily:mono, fontSize:9.5, color:P.ink4, marginBottom:10 }}>
        continent-scale scope
      </div>

      <div style={{ display:"flex", gap:8, marginBottom:10 }}>
        {geoDims.map(d => {
          const st = STATES[d.state] || STATES.Unknown;
          return (
            <div key={d.key} style={{ flex:1, textAlign:"center" }} title={`${d.short} · ${d.state}`}>
              <div style={{ height:16, background:st.fill, border:`1px solid ${st.ring}` }}/>
              <div style={{ fontFamily:mono, fontSize:7.5, letterSpacing:".08em", color:P.ink3, marginTop:3 }}>
                {d.short}
              </div>
            </div>
          );
        })}
      </div>

      {(geoData.changes || []).slice(0,3).map((c,i) => (
        <div key={c.change_id||i}
             style={{ borderTop:`1px solid ${P.ruleSoft}`, padding:"6px 0",
                       fontFamily:serif, fontSize:12, lineHeight:1.4, color:P.ink2 }}>
          {c.summary}
        </div>
      ))}
    </>
  );
}

// ── LiveHeader ────────────────────────────────────────────────────────────────

function LiveHeader({ statusData }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const pad = n => String(n).padStart(2, "0");
  const localT = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  const utcT   = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
  const tzName  = useMemo(() => {
    try {
      return new Intl.DateTimeFormat("en-US",{timeZoneName:"short"})
        .formatToParts(now).find(p=>p.type==="timeZoneName")?.value || "LOC";
    } catch { return "LOC"; }
  }, []);
  const dateLine = now.toLocaleDateString("en-GB",{weekday:"long",day:"numeric",month:"long"}).toUpperCase();
  const doy = Math.floor((now - new Date(now.getFullYear(),0,0)) / 864e5);

  return (
    <div>
      {/* masthead */}
      <div style={{ padding:"15px 48px 9px", borderBottom:`1px solid ${P.natt}`,
                    display:"grid", gridTemplateColumns:"1fr auto 1fr", alignItems:"end" }}>
        <div>
          <div style={{ fontFamily:mono, fontSize:10, letterSpacing:".18em", color:P.ink2 }}>
            {dateLine} {toRoman(now.getFullYear())} · PL. {toRoman(doy)}
          </div>
          <div style={{ fontFamily:mono, fontSize:9, letterSpacing:".1em", color:P.ink4, marginTop:4 }}>
            {statusData
              ? `GRAPH ${fmtN(statusData.graph.live_nodes)} NODES · ${fmtN(statusData.graph.live_edges)} EDGES`
              : "LOADING GRAPH STATUS…"}
          </div>
        </div>
        <div style={{ textAlign:"center" }}>
          <div style={{ fontFamily:display, fontSize:30, color:P.natt }}>Augur</div>
          <div style={{ fontFamily:mono, fontSize:9, letterSpacing:".34em", color:P.ink3, marginTop:2 }}>
            HERBARIUM MUNDI
          </div>
        </div>
        <div style={{ textAlign:"right" }}>
          <div style={{ fontFamily:mono, fontSize:11, letterSpacing:".08em", color:P.natt, fontVariantNumeric:"tabular-nums" }}>
            <span style={{ color:P.ink3, fontSize:9, letterSpacing:".16em", marginRight:6 }}>{tzName}</span>{localT}
            <span style={{ color:P.ink4, margin:"0 8px" }}>·</span>
            <span style={{ color:P.ink3, fontSize:9, letterSpacing:".16em", marginRight:6 }}>UTC</span>{utcT}
          </div>
          <div style={{ fontFamily:mono, fontSize:9, letterSpacing:".1em", color:P.ink4, marginTop:4 }}>
            CAUSAL GRAPH INTELLIGENCE
          </div>
        </div>
      </div>

      {/* ingestion stats strip */}
      <div style={{ display:"flex", alignItems:"stretch", padding:"0 48px",
                    borderBottom:`1px solid ${P.natt}`, background:P.lin }}>
        <div style={{ display:"flex", alignItems:"center", gap:7, padding:"8px 18px 8px 0",
                      borderRight:`1px solid ${P.rule}` }}>
          <span style={{ width:6, height:6, borderRadius:"50%",
                          background: statusData ? P.mose : P.ink4,
                          border:`1px solid ${statusData ? P.moseDeep : P.ink3}` }}/>
          <span style={{ fontFamily:mono, fontSize:9, letterSpacing:".18em", color:P.ink2 }}>
            {statusData ? "LIVE" : "…"}
          </span>
        </div>

        {statusData ? (
          <>
            <div style={{ display:"flex", alignItems:"baseline", gap:14,
                          padding:"8px 18px", flex:1, borderRight:`1px solid ${P.rule}` }}>
              <span style={{ fontFamily:mono, fontSize:9, letterSpacing:".18em", color:P.ink3 }}>SIGNALS</span>
              {[["1H", statusData.signals.last_1h], ["24H", statusData.signals.last_24h]].map(([w,v]) => (
                <span key={w} style={{ fontFamily:mono, fontSize:10.5, color:P.natt,
                                        fontVariantNumeric:"tabular-nums", whiteSpace:"nowrap" }}>
                  <span style={{ fontSize:8, color:P.ink4, letterSpacing:".1em", marginRight:5 }}>{w}</span>
                  {fmtN(v)}
                </span>
              ))}
            </div>
            <div style={{ display:"flex", alignItems:"baseline", gap:14,
                          padding:"8px 18px", flex:1, borderRight:`1px solid ${P.rule}` }}>
              <span style={{ fontFamily:mono, fontSize:9, letterSpacing:".18em", color:P.ink3 }}>PAYLOADS</span>
              <span style={{ fontFamily:mono, fontSize:10.5, color:P.natt, fontVariantNumeric:"tabular-nums" }}>
                <span style={{ fontSize:8, color:P.ink4, letterSpacing:".1em", marginRight:5 }}>24H</span>
                {fmtN(statusData.payloads.last_24h)}
              </span>
            </div>
            <div style={{ display:"flex", alignItems:"baseline", gap:14, padding:"8px 18px" }}>
              <span style={{ fontFamily:mono, fontSize:9, letterSpacing:".18em", color:P.ink3 }}>NODES</span>
              <span style={{ fontFamily:mono, fontSize:10.5, color:P.natt, fontVariantNumeric:"tabular-nums" }}>
                {fmtN(statusData.graph.live_nodes)}
              </span>
              <span style={{ fontFamily:mono, fontSize:9, letterSpacing:".18em", color:P.ink3, marginLeft:6 }}>EDGES</span>
              <span style={{ fontFamily:mono, fontSize:10.5, color:P.natt, fontVariantNumeric:"tabular-nums" }}>
                {fmtN(statusData.graph.live_edges)}
              </span>
            </div>
          </>
        ) : (
          <div style={{ padding:"8px 18px", fontFamily:mono, fontSize:9.5, color:P.ink4, letterSpacing:".12em" }}>
            Loading system status…
          </div>
        )}
      </div>
    </div>
  );
}

// ── AlmanacScrubber ───────────────────────────────────────────────────────────

function AlmanacScrubber({ asOf, onChange }) {
  const trackRef = useRef(null);

  const asOfMs = asOf ? new Date(asOf).getTime() : SCRUB_END_MS;
  const t = Math.max(0, Math.min(1, (asOfMs - SCRUB_START_MS) / SCRUB_SPAN_MS));
  const isLive = !asOf || t > 0.998;

  const posToIso = useCallback(pos => {
    if (pos > 0.998) return null;
    return new Date(SCRUB_START_MS + pos * SCRUB_SPAN_MS).toISOString();
  }, []);

  const getPos = e => {
    if (!trackRef.current) return t;
    const r = trackRef.current.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  };

  const onDown = e => {
    onChange(posToIso(getPos(e)));
    const move = ev => onChange(posToIso(getPos(ev)));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const stepDay = dir => {
    const base = asOf ? new Date(asOf) : new Date();
    base.setDate(base.getDate() + dir);
    onChange(base > new Date() ? null : base.toISOString());
  };

  const shownDate = isLive ? new Date() : new Date(asOf);
  const shownStr = isLive
    ? "Now — live"
    : shownDate.toUTCString().replace(" GMT", " UTC");

  const yearCount = SCRUB_END_YR - SCRUB_START_YR + 1;

  return (
    <div className="ag-scrubber">
      <div className="ag-scrub-hd">
        <div>
          Almanac · showing graph state at
          <span className="ag-scrub-ts">{shownStr}</span>
        </div>
        <span className={isLive ? "ag-scrub-live-on" : "ag-scrub-live-off"}>
          {isLive ? "● LIVE" : "◷ HISTORICAL"}
        </span>
      </div>

      <div ref={trackRef} className="ag-scrub-track" onMouseDown={onDown}>
        {Array.from({ length: yearCount }, (_, i) => {
          const pos = i / (yearCount - 1);
          return (
            <div key={i} className="ag-scrub-year-mark" style={{ left:`${pos*100}%` }}>
              <span className="ag-scrub-year-label">{SCRUB_START_YR + i}</span>
            </div>
          );
        })}
        {SCRUB_EVENTS.map((e, i) => (
          <div key={i} className="ag-scrub-event" style={{ left:`${e.t*100}%` }}>
            <span className="ag-scrub-event-label" style={{
              transform: e.t > 0.9 ? "translate(-95%,-2px)" : e.t < 0.06 ? "translate(-5%,-2px)" : "translate(-50%,-2px)"
            }}>{e.label}</span>
          </div>
        ))}
        <div className="ag-scrub-playhead" style={{ left:`${t*100}%` }}>
          <div className="ag-scrub-playhead-cap-top"/>
          <div className="ag-scrub-playhead-cap-bot"/>
        </div>
      </div>

      <div className="ag-scrub-foot">
        <div className="ag-scrub-controls">
          <button className="ag-scrub-btn" onClick={() => stepDay(-1)}>← 1d</button>
          <button className="ag-scrub-btn" onClick={() => stepDay(1)}>+1d →</button>
          <button className="ag-scrub-btn" style={{ color:P.natt }} onClick={() => onChange(null)}>NOW</button>
        </div>
        <span>drag · click track</span>
      </div>
    </div>
  );
}

// ── AskAugur ──────────────────────────────────────────────────────────────────

function AskAugur({ open, onClose, asOf }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const handler = e => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const send = async () => {
    const q = input.trim();
    if (!q || sending) return;
    setInput("");
    setSending(true);
    setMessages(prev => [...prev, { role:"user", text:q }]);
    try {
      const res = await fetch("/api/conversation/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question:q, session_id:sessionId, as_of:asOf }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.session_id) setSessionId(data.session_id);
      setMessages(prev => [...prev, {
        role: "assistant",
        text: data.answer,
        meta: data.context ? `${fmtN(data.context.n_nodes)} nodes · ${fmtN(data.context.n_edges)} edges` : null,
      }]);
    } catch (err) {
      setMessages(prev => [...prev, { role:"assistant", text:`Error: ${err.message}` }]);
    } finally {
      setSending(false);
    }
  };

  if (!open) return null;

  return (
    <div className="ag-conv-overlay" onClick={onClose}>
      <div className="ag-conv-panel" onClick={e => e.stopPropagation()}>
        <div className="ag-conv-header">
          <span>ASK <span style={{ fontFamily:display, fontStyle:"italic", fontSize:13, color:P.natt, letterSpacing:0 }}>Augur</span></span>
          <span className="ag-conv-close" onClick={onClose}>✕</span>
        </div>
        <div ref={logRef} className="ag-conv-log">
          {messages.length === 0 && (
            <div style={{ fontFamily:display, fontStyle:"italic", fontSize:13, color:P.ink3, padding:"8px 0" }}>
              Ask anything about the current world state, a topic, or a node in the graph.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`ag-conv-msg ag-conv-msg-${m.role}`}>
              <div className="ag-conv-msg-role">{m.role}</div>
              <div className="ag-conv-msg-text">{m.text}</div>
              {m.meta && <div className="ag-conv-msg-meta">{m.meta}</div>}
            </div>
          ))}
          {sending && (
            <div className="ag-conv-msg ag-conv-msg-assistant">
              <div className="ag-conv-msg-role">augur</div>
              <div className="ag-conv-msg-text" style={{ color:P.ink3, fontStyle:"italic" }}>
                Consulting the graph…
              </div>
            </div>
          )}
        </div>
        <form className="ag-conv-form" onSubmit={e => { e.preventDefault(); send(); }}>
          <input className="ag-conv-input" value={input} onChange={e => setInput(e.target.value)}
                 placeholder="Ask about the graph…" autoFocus/>
          <button type="button" className="ag-conv-clear"
                  onClick={() => { setMessages([]); setSessionId(null); }}>CLR</button>
          <button type="submit" className="ag-conv-submit" disabled={sending}>→</button>
        </form>
      </div>
    </div>
  );
}

// ── TopicListView ─────────────────────────────────────────────────────────────

function TopicListView({ onBack, onTopicSelect, asOf }) {
  const [topics, setTopics] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const p = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
    fetch(`/api/topics${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setTopics(d.topics || []))
      .catch(err => setError(String(err)));
  }, [asOf]);

  return (
    <div className="ag-page">
      <div className="ag-back-link" onClick={onBack}>← Home</div>
      <div className="ag-page-label">CAUSAL TOPICS</div>
      {error && <div className="error-msg">Failed to load topics: {error}</div>}
      {!topics && !error && <div className="loading">Loading topics…</div>}
      {topics && topics.length === 0 && (
        <div className="empty-state">No topics defined. Create topics via the CLI.</div>
      )}
      {topics && topics.map(t => (
        <div key={t.topic_id} onClick={() => onTopicSelect(t.topic_id)}
             style={{ cursor:"pointer", borderBottom:`1px solid ${P.ruleSoft}`, padding:"14px 0" }}>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"baseline" }}>
            <div style={{ fontFamily:serif, fontSize:16, fontWeight:600, color:P.natt }}>{t.name}</div>
            <div className={`state-${t.state}`} style={{ fontFamily:display, fontSize:13, fontStyle:"italic" }}>
              {stateTitle(t.state)}
            </div>
          </div>
          {t.dimension && (
            <div style={{ fontFamily:mono, fontSize:9, letterSpacing:".12em", color:P.ink4, marginTop:2 }}>
              {t.dimension.replace(/_/g," ").toUpperCase()}
            </div>
          )}
          <div style={{ fontFamily:mono, fontSize:9.5, color:P.ink3, marginTop:4 }}>
            {t.node_count} nodes · {t.active_condition_count} conditions active
          </div>
          {t.description && (
            <div style={{ fontFamily:serif, fontSize:13, color:P.ink2, marginTop:6, lineHeight:1.4 }}>
              {t.description}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── TopicDetailView ───────────────────────────────────────────────────────────

function TopicDetailView({ topicId, onBack, onNodeSelect, onEdgeSelect, asOf }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const p = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
    fetch(`/api/topics/${topicId}${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(setData)
      .catch(err => setError(String(err)));
  }, [topicId, asOf]);

  if (error) return (
    <div className="ag-page">
      <div className="ag-back-link" onClick={onBack}>← Topics</div>
      <div className="error-msg">Failed to load topic: {error}</div>
    </div>
  );
  if (!data) return (
    <div className="ag-page">
      <div className="ag-back-link" onClick={onBack}>← Topics</div>
      <div className="loading">Loading topic…</div>
    </div>
  );

  const nodes = data.nodes || [];
  const conditions = nodes.filter(n => n.node_type === "condition");
  const others = nodes.filter(n => n.node_type !== "condition");

  const nodeRow = (n, i) => (
    <div key={n.node_id||i}
         style={{ padding:"8px 0", borderBottom:`1px solid ${P.ruleSoft}`, cursor: n.node_id?"pointer":"default" }}
         onClick={() => n.node_id && onNodeSelect(n.node_id)}>
      <div style={{ display:"flex", gap:10, alignItems:"baseline" }}>
        {n.current_state != null && (
          <span className={`state-${n.current_state}`}
                style={{ fontFamily:mono, fontSize:9, letterSpacing:".08em" }}>
            {stateTitle(n.current_state)}
          </span>
        )}
        <span style={{ fontFamily:mono, fontSize:9, letterSpacing:".08em", color:P.ink4 }}>
          {(n.node_type||"").replace(/_/g," ").toUpperCase()}
        </span>
        <span style={{ fontFamily:serif, fontSize:14, color:P.natt }}>{n.name}</span>
      </div>
      {n.notes && (
        <div style={{ fontFamily:serif, fontSize:12.5, color:P.ink2, marginTop:3, lineHeight:1.4 }}>
          {n.notes}
        </div>
      )}
    </div>
  );

  return (
    <div className="ag-page">
      <div className="ag-back-link" onClick={onBack}>← Topics</div>
      <div style={{ marginBottom:20 }}>
        {data.dimension && (
          <div className="ag-page-label">{data.dimension.replace(/_/g," ").toUpperCase()}</div>
        )}
        <h2 style={{ fontFamily:serif, fontSize:26, fontWeight:600, color:P.natt, margin:"0 0 6px" }}>
          {data.name}
        </h2>
        <div style={{ display:"flex", gap:14, alignItems:"baseline" }}>
          <div className={`state-${data.state}`}
               style={{ fontFamily:display, fontSize:18, fontStyle:"italic" }}>
            {stateTitle(data.state)}
          </div>
          <div style={{ fontFamily:mono, fontSize:9.5, color:P.ink3 }}>
            {data.node_count} nodes · {data.active_condition_count} conditions active
          </div>
        </div>
        {data.description && (
          <div style={{ fontFamily:serif, fontSize:14, lineHeight:1.6, color:P.ink2, marginTop:10, maxWidth:"72ch" }}>
            {data.description}
          </div>
        )}
      </div>

      {conditions.length > 0 && (
        <div className="reasoning-section">
          <div className="reasoning-label">CONDITIONS ({conditions.length})</div>
          {conditions.map(nodeRow)}
        </div>
      )}

      {others.length > 0 && (
        <div className="reasoning-section">
          <div className="reasoning-label">OTHER NODES ({others.length})</div>
          {others.map(nodeRow)}
        </div>
      )}

      {nodes.length === 0 && (
        <div className="empty-state">This topic has no member nodes yet.</div>
      )}
    </div>
  );
}

// ── ReasoningView ─────────────────────────────────────────────────────────────

function ReasoningView({ type, id, onBack, onNodeSelect, onEdgeSelect, asOf }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const p = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
    fetch(`/api/reasoning/${type}/${id}${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(setData)
      .catch(err => setError(String(err)));
  }, [type, id, asOf]);

  if (error) return (
    <div className="ag-page">
      <div className="ag-back-link" onClick={onBack}>← Back</div>
      <div className="error-msg">Failed to load: {error}</div>
    </div>
  );
  if (!data) return (
    <div className="ag-page">
      <div className="ag-back-link" onClick={onBack}>← Back</div>
      <div className="loading">Loading reasoning…</div>
    </div>
  );

  if (type === "node") {
    const node = data.node || data;
    return (
      <div className="ag-page">
        <div className="reasoning-header">
          <div className="ag-back-link" onClick={onBack}>← Back</div>
          <div className="reasoning-node-name">{node.name}</div>
          <div className="reasoning-node-type">{(node.node_type||"").replace(/_/g," ").toUpperCase()}</div>
        </div>

        {node.description && (
          <div className="reasoning-section">
            <div className="reasoning-label">DESCRIPTION</div>
            <div className="reasoning-text">{node.description}</div>
          </div>
        )}

        {data.edges && data.edges.length > 0 && (
          <div className="reasoning-section">
            <div className="reasoning-label">CONNECTED EDGES ({data.edges.length})</div>
            {data.edges.slice(0,10).map((e, i) => (
              <div key={e.edge_id||i}
                   style={{ padding:"8px 0", borderBottom:`1px solid ${P.ruleSoft}`, cursor: e.edge_id?"pointer":"default" }}
                   onClick={() => e.edge_id && onEdgeSelect && onEdgeSelect(e.edge_id)}>
                <div style={{ display:"flex", gap:10, alignItems:"baseline" }}>
                  <span className={`weight-${e.weight_band||"provisional"}`}>
                    {e.weight_band||"?"}
                  </span>
                  <span style={{ fontFamily:serif, fontSize:13.5, color:P.natt }}>
                    {e.source?.name} → {e.target?.name}
                  </span>
                </div>
                {e.reasoning && (
                  <div className="reasoning-text" style={{ marginTop:4, fontSize:"0.85rem" }}>
                    {e.reasoning}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {data.signals && data.signals.length > 0 && (
          <div className="reasoning-section">
            <div className="reasoning-label">SIGNALS ({data.signals.length})</div>
            {data.signals.slice(0,6).map((s, i) => (
              <div key={s.signal_id||i} style={{ padding:"6px 0", borderBottom:`1px solid ${P.ruleSoft}` }}>
                <div style={{ fontFamily:mono, fontSize:9, color:P.ink4 }}>{fmtRelTime(s.content_timestamp)}</div>
                <div style={{ fontFamily:serif, fontSize:13, color:P.natt, marginTop:2 }}>
                  {s.claim_text}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // Edge
  const edge = data.edge || data;
  const src = data.source_node || {};
  const tgt = data.target_node || {};

  const sigRow = (s, i) => (
    <div key={s.signal_id||i} style={{ padding:"6px 0", borderBottom:`1px solid ${P.ruleSoft}` }}>
      <div style={{ fontFamily:mono, fontSize:9, color:P.ink4 }}>
        {s.lens_id ? `${s.lens_id} · ` : ""}{fmtRelTime(s.content_timestamp)}
      </div>
      <div style={{ fontFamily:serif, fontSize:13, color:P.natt, marginTop:2 }}>
        {s.claim_text}
      </div>
    </div>
  );

  return (
    <div className="ag-page">
      <div className="reasoning-header">
        <div className="ag-back-link" onClick={onBack}>← Back</div>
        <div className="reasoning-node-name" style={{ fontSize:"1.1rem" }}>
          <span style={{ cursor: src.node_id?"pointer":"default", borderBottom: src.node_id?`1px dotted ${P.ink3}`:"none" }}
                onClick={() => src.node_id && onNodeSelect && onNodeSelect(src.node_id)}>
            {src.name}
          </span>
          {" → "}
          <span style={{ cursor: tgt.node_id?"pointer":"default", borderBottom: tgt.node_id?`1px dotted ${P.ink3}`:"none" }}
                onClick={() => tgt.node_id && onNodeSelect && onNodeSelect(tgt.node_id)}>
            {tgt.name}
          </span>
        </div>
        <div className="reasoning-node-type">{(edge.edge_type||"").replace(/_/g," ").toUpperCase()}</div>
      </div>

      <div style={{ display:"flex", gap:16, marginBottom:20 }}>
        <span className={`weight-badge weight-${edge.weight_band||"provisional"}`}>
          {edge.weight_band||"?"}
        </span>
        <span style={{ fontFamily:mono, fontSize:10, letterSpacing:".1em",
                        color: edge.deprecated ? P.ink4 : P.mose }}>
          {edge.deprecated ? "DEPRECATED" : "ACTIVE"}
        </span>
      </div>

      {edge.reasoning && (
        <div className="reasoning-section">
          <div className="reasoning-label">REASONING</div>
          <div className="reasoning-text">{edge.reasoning}</div>
        </div>
      )}

      {edge.falsification_criteria && (
        <div className="reasoning-section">
          <div className="reasoning-label">FALSIFICATION CRITERIA</div>
          <div className="falsification-box">{edge.falsification_criteria}</div>
        </div>
      )}

      {data.supporting_signals && data.supporting_signals.length > 0 && (
        <div className="reasoning-section">
          <div className="reasoning-label">SUPPORTING SIGNALS ({data.supporting_signals.length})</div>
          {data.supporting_signals.slice(0,5).map(sigRow)}
        </div>
      )}

      {data.disconfirming_signals && data.disconfirming_signals.length > 0 && (
        <div className="reasoning-section">
          <div className="reasoning-label">DISCONFIRMING SIGNALS ({data.disconfirming_signals.length})</div>
          {data.disconfirming_signals.slice(0,5).map(sigRow)}
        </div>
      )}
    </div>
  );
}

// ── DomainSection ─────────────────────────────────────────────────────────────

function DomainSection({ dims, changes, homeError }) {
  const [selected, setSelected] = useState(null);

  return (
    <div style={{ padding:"18px 48px 6px" }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"baseline", marginBottom:10 }}>
        <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.ink3 }}>
          FIVE DOMAINS · CLICK A CARD TO OPEN ITS LEDGER
        </div>
        <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".08em", color:P.ink4 }}>
          28-DAY DIRECTION VS TRAILING 180
        </div>
      </div>

      {!dims && !homeError && (
        <div style={{ display:"flex", gap:14 }}>
          {[0,1,2,3,4].map(i => (
            <div key={i} style={{ flex:1, height:200, background:P.lin, border:`1px solid ${P.rule}` }}/>
          ))}
        </div>
      )}

      {dims && (
        <>
          <div style={{ display:"flex", gap:14, alignItems:"stretch" }}>
            {dims.map(d => (
              <DomainCard key={d.key} dim={d} changes={changes||[]}
                          selected={selected}
                          onClick={() => setSelected(selected===d.key ? null : d.key)}/>
            ))}
          </div>
          {selected && dims.find(d => d.key === selected) && (
            <DomainLedger
              dim={dims.find(d => d.key === selected)}
              changes={changes||[]}
              onClose={() => setSelected(null)}
            />
          )}
        </>
      )}
    </div>
  );
}

// ── TopicsSection ─────────────────────────────────────────────────────────────

function TopicsSection({ asOf, changes, navigate }) {
  const [topics, setTopics] = useState(null);

  useEffect(() => {
    const p = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
    fetch(`/api/topics${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setTopics(d.topics || []))
      .catch(() => setTopics([]));
  }, [asOf]);

  return (
    <div style={{ padding:"20px 48px 26px", flex:1 }}>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"baseline", marginBottom:8 }}>
        <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.ink3 }}>
          CAUSAL THREADS · NEWEST CHANGE SHOWN INLINE · CLICK TO UNFOLD
        </div>
        <div style={{ display:"flex", gap:16, alignItems:"baseline" }}>
          <span style={{ fontFamily:mono, fontSize:9.5, color:P.ink4, letterSpacing:".08em" }}>
            {topics ? `${topics.length} TOPICS` : "…"}
          </span>
          <span style={{ fontFamily:mono, fontSize:9.5, color:P.leirstein, letterSpacing:".08em", cursor:"pointer" }}
                onClick={() => navigate("topics")}>
            ALL TOPICS →
          </span>
        </div>
      </div>
      <CausalThreads
        topics={topics||[]}
        changes={changes||[]}
        onTopicClick={id => navigate("topic", { topicId: id })}
      />
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

function App() {
  const [view, setView]           = useState({ name:"home" });
  const [homeData, setHomeData]   = useState(null);
  const [homeError, setHomeError] = useState(null);
  const [statusData, setStatus]   = useState(null);
  const [asOf, setAsOf]           = useState(null);
  const [geoData, setGeoData]     = useState(null);
  const [convOpen, setConvOpen]   = useState(false);

  // Load home data whenever asOf changes
  useEffect(() => {
    setHomeError(null);
    const p = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
    fetch(`/api/home${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(setHomeData)
      .catch(err => setHomeError(String(err)));
  }, [asOf]);

  // Load /api/status once then poll every 30s
  useEffect(() => {
    const load = () => {
      fetch("/api/status")
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(setStatus)
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, []);

  // ⌘K / Ctrl+K opens conversation
  useEffect(() => {
    const h = e => { if ((e.metaKey||e.ctrlKey) && e.key==="k") { e.preventDefault(); setConvOpen(v=>!v); } };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, []);

  const requestGeo = () => {
    if (!navigator.geolocation) { setGeoData("denied"); return; }
    setGeoData("loading");
    navigator.geolocation.getCurrentPosition(
      pos => {
        const { latitude, longitude } = pos.coords;
        const params = new URLSearchParams({ lat:latitude, lon:longitude });
        if (asOf) params.set("as_of", asOf);
        fetch(`/api/geo/scope?${params}`)
          .then(r => r.ok ? r.json() : Promise.reject(r.status))
          .then(setGeoData)
          .catch(() => setGeoData("denied"));
      },
      () => setGeoData("denied")
    );
  };

  const dims    = homeData ? homeData.dimensions.map(adaptDim)    : null;
  const changes = homeData ? homeData.changes.map(adaptChange)    : null;

  const navigate = (name, params = {}) => setView({ name, ...params });

  return (
    <div style={{ minHeight:"100vh", display:"flex", flexDirection:"column",
                  background:P.bein, color:P.natt, paddingBottom:80 }}>
      <LiveHeader statusData={statusData}/>

      {/* ── Home ── */}
      {view.name === "home" && (
        <>
          {/* Hero row */}
          <div style={{ display:"grid", gridTemplateColumns:"330px 1fr 330px", gap:36,
                        padding:"26px 48px 22px", alignItems:"stretch",
                        background:`radial-gradient(ellipse at center top, ${P.lin} 0%, ${P.bein} 75%)`,
                        borderBottom:`1px solid ${P.rule}` }}>
            {/* Plate overview */}
            <div style={{ display:"flex", flexDirection:"column", justifyContent:"center" }}>
              <div style={{ fontFamily:mono, fontSize:9.5, letterSpacing:".16em", color:P.ink3, marginBottom:8 }}>
                PLATE · OVERVIEW
              </div>
              {!homeData && !homeError && (
                <div style={{ fontFamily:serif, fontSize:14, color:P.ink3, fontStyle:"italic" }}>Loading…</div>
              )}
              {homeError && (
                <div style={{ fontFamily:mono, fontSize:11, color:P.leirsteinDp }}>{homeError}</div>
              )}
              {homeData && dims && (
                <>
                  <div style={{ fontFamily:serif, fontSize:14.5, lineHeight:1.6, color:P.ink2 }}>
                    {dims.length} dimensions tracked.
                    {dims.filter(d=>d.dir==="↘").length > 0 &&
                      ` ${dims.filter(d=>d.dir==="↘").length} worsening.`}
                    {dims.filter(d=>d.dir==="↗").length > 0 &&
                      ` ${dims.filter(d=>d.dir==="↗").length} improving.`}
                    {" "}
                    {changes && changes.length > 0
                      ? `${changes.length} structural change${changes.length>1?"s":""} in the last 24 hours.`
                      : "No structural changes in the last 24 hours."}
                  </div>
                  <div style={{ marginTop:12, fontFamily:mono, fontSize:9.5, color:P.ink4,
                                letterSpacing:".06em", lineHeight:1.7 }}>
                    confidence · derived from graph state
                  </div>
                </>
              )}
            </div>

            {/* Verdict dial */}
            <div style={{ textAlign:"center", display:"flex", flexDirection:"column", justifyContent:"flex-end" }}>
              {dims ? (
                <VerdictDial dims={dims}/>
              ) : (
                <div style={{ height:300, display:"flex", alignItems:"center", justifyContent:"center",
                              fontFamily:display, fontStyle:"italic", color:P.ink3 }}>
                  {homeError ? "—" : "Loading…"}
                </div>
              )}
              {dims && (
                <div style={{ marginTop:14 }}>
                  <div style={{ fontFamily:display, fontStyle:"italic", fontSize:15, color:P.ink3 }}>
                    Is the world improving?
                  </div>
                  <div style={{ fontFamily:display, fontWeight:600, fontSize:40,
                                lineHeight:1.05, color:P.natt, marginTop:2 }}>
                    {verdictHeadline(compositeU(dims))}
                    <span style={{ color:P.leirstein }}>.</span>
                  </div>
                  <div style={{ fontFamily:serif, fontSize:13.5, color:P.ink2, marginTop:6, fontStyle:"italic" }}>
                    {verdictSubline(dims)}
                  </div>
                </div>
              )}
            </div>

            <YourLatitude geoData={geoData} onRequest={requestGeo}/>
          </div>

          <DomainSection dims={dims} changes={changes} homeError={homeError}/>
          <TopicsSection asOf={asOf} changes={changes} navigate={navigate}/>
        </>
      )}

      {/* ── Topics list ── */}
      {view.name === "topics" && (
        <TopicListView
          onBack={() => navigate("home")}
          onTopicSelect={id => navigate("topic", { topicId:id })}
          asOf={asOf}
        />
      )}

      {/* ── Topic detail ── */}
      {view.name === "topic" && (
        <TopicDetailView
          topicId={view.topicId}
          onBack={() => navigate("topics")}
          onNodeSelect={id => navigate("reasoning", { rType:"node", rId:id })}
          onEdgeSelect={id => navigate("reasoning", { rType:"edge", rId:id })}
          asOf={asOf}
        />
      )}

      {/* ── Reasoning ── */}
      {view.name === "reasoning" && (
        <ReasoningView
          key={`${view.rType}/${view.rId}`}
          type={view.rType}
          id={view.rId}
          onBack={() => navigate("home")}
          onNodeSelect={id => navigate("reasoning", { rType:"node", rId:id })}
          onEdgeSelect={id => navigate("reasoning", { rType:"edge", rId:id })}
          asOf={asOf}
        />
      )}

      <AlmanacScrubber asOf={asOf} onChange={setAsOf}/>

      <button className="ag-ask-btn" onClick={() => setConvOpen(true)}>
        Ask <span className="ag-ask-name">Augur</span>
        <span className="ag-ask-kbd">⌘K</span>
      </button>

      <AskAugur open={convOpen} onClose={() => setConvOpen(false)} asOf={asOf}/>
    </div>
  );
}

// ── Mount ─────────────────────────────────────────────────────────────────────

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
