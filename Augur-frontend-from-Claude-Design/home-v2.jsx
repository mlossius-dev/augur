// home-v2.jsx — Augur Home, refined after review.
// Hero = central verdict dial (answers "is the world improving?" at a glance)
// + plate overview paragraph (left) + your latitude (right).
// Five domain cards with clear subcontent preview + explicit click affordance.
// Topics and 24h edges merged into one "causal threads" section.

const { useState, useEffect, useMemo } = React;

// ─────────────────────────────────────────────────────────────────────────────
// Topic ↔ change linkage (merge "top edges" and "causal threads")
const ROOT_TO_TOPIC = { "Iran–Israel": 0, "Fertilizer": 1, "Semis": 2, "Fiscal": 3, "AI": 5 };
const topicChanges = (ti) => AUGUR_CHANGES.filter(c => ROOT_TO_TOPIC[c.root] === ti);

// Band → position on the verdict dial, 0..1 left→right
const BAND_U = { Improving: 0.1, Stable: 0.3, Strained: 0.5, Deteriorating: 0.7, Crisis: 0.9 };
const BAND_ORDER = ["Improving", "Stable", "Strained", "Deteriorating", "Crisis"];

// ─────────────────────────────────────────────────────────────────────────────
// VERDICT DIAL — engraved half-instrument. Needle = composite world state.

function VerdictDial({ palette, latin }) {
  const W = 560, H = 300, cx = W / 2, cy = 262, R = 198;
  const pt = (u, r) => {
    const a = Math.PI + u * Math.PI;
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  };
  const arcPath = (u0, u1, r) => {
    const [x0, y0] = pt(u0, r), [x1, y1] = pt(u1, r);
    return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  };

  const COMPOSITE_U = 0.62; // between strained and deteriorating, leaning det.

  // domain dots stacked by band
  const dots = useMemo(() => {
    const byBand = {};
    return AUGUR_DIMS.map(d => {
      const u = BAND_U[d.state];
      byBand[u] = (byBand[u] || 0) + 1;
      return { d, u, layer: byBand[u] - 1 };
    });
  }, []);

  const [nx, ny] = pt(COMPOSITE_U, R - 30);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{display:"block", maxWidth: 580, margin:"0 auto"}}>
      {/* outer engraved frame */}
      <path d={arcPath(0, 1, R + 14)} fill="none" stroke={palette.natt} strokeWidth="0.8"/>
      <path d={arcPath(0, 1, R + 10)} fill="none" stroke={palette.ink3} strokeWidth="0.35"/>

      {/* band segments */}
      {BAND_ORDER.map((b, i) => {
        const st = STATES[b];
        const u0 = i / 5 + 0.006, u1 = (i + 1) / 5 - 0.006;
        return (
          <g key={b}>
            <path d={arcPath(u0, u1, R)} fill="none" stroke={st.fill} strokeWidth="16" strokeLinecap="butt"/>
            <path d={arcPath(u0, u1, R)} fill="none" stroke={st.ring} strokeWidth="0.5" strokeLinecap="butt"
                  transform={`translate(0,0)`} opacity="0.0"/>
            {/* band label along arc */}
            {(() => {
              const um = (u0 + u1) / 2;
              const [lx, ly] = pt(um, R + 26);
              const deg = (um * 180) - 90;
              return (
                <text x={lx} y={ly} textAnchor="middle"
                      fontFamily="'JetBrains Mono', monospace" fontSize="8.5" letterSpacing=".16em"
                      fill={palette.ink3}
                      transform={`rotate(${deg}, ${lx}, ${ly})`}>
                  {b.toUpperCase()}
                </text>
              );
            })()}
          </g>
        );
      })}

      {/* fine ticks */}
      {Array.from({length: 21}, (_, i) => i / 20).map((u, i) => {
        const big = i % 4 === 0;
        const [x0, y0] = pt(u, R - 12);
        const [x1, y1] = pt(u, big ? R - 20 : R - 16);
        return <line key={i} x1={x0} y1={y0} x2={x1} y2={y1} stroke={palette.ink3} strokeWidth={big ? 0.7 : 0.4}/>;
      })}

      {/* domain dots — where each of the five sits on the scale */}
      {dots.map(({ d, u, layer }) => {
        const st = STATES[d.state];
        const r = R - 38 - layer * 17;
        const [x, y] = pt(u, r);
        return (
          <g key={d.key}>
            <circle cx={x} cy={y} r="5" fill={st.fill} stroke={st.ring} strokeWidth="0.8"/>
            <text x={x} y={y - 9} textAnchor="middle"
                  fontFamily="'JetBrains Mono', monospace" fontSize="7.5" letterSpacing=".14em"
                  fill={palette.ink2}>{d.short}</text>
          </g>
        );
      })}

      {/* needle */}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={palette.natt} strokeWidth="1.6"/>
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={palette.leirstein} strokeWidth="0.7"/>
      <circle cx={cx} cy={cy} r="7" fill={palette.natt}/>
      <circle cx={cx} cy={cy} r="3" fill={palette.lin}/>

      {/* 28-day direction marker — small arrow at needle tip */}
      {(() => {
        const [ax, ay] = pt(COMPOSITE_U + 0.035, R - 52);
        return (
          <text x={ax} y={ay} textAnchor="middle"
                fontFamily="'JetBrains Mono', monospace" fontSize="13" fill={palette.leirstein}>↘</text>
        );
      })()}

      {/* hairline base */}
      <line x1={cx - R - 14} y1={cy} x2={cx + R + 14} y2={cy} stroke={palette.natt} strokeWidth="0.8"/>
      {latin && (
        <text x={cx - R - 10} y={cy + 16} fontFamily="'Cormorant Garamond', serif" fontStyle="italic"
              fontSize="11" fill={palette.ink3}>status mundi</text>
      )}
      <text x={cx + R + 10} y={cy + 16} textAnchor="end"
            fontFamily="'JetBrains Mono', monospace" fontSize="8.5" letterSpacing=".12em"
            fill={palette.ink3}>COMPOSITE · 5 DOMAINS · 28D</text>
    </svg>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// DOMAIN CARD — pressable, previews its subcontent.

function DomainCard({ dim, palette, selected, onClick, latin }) {
  const st = STATES[dim.state];
  const changes = AUGUR_CHANGES.filter(c => c.dim === dim.key);
  const top = changes[0];
  const isSel = selected === dim.key;

  return (
    <div className={"ag-card" + (isSel ? " ag-card-sel" : "")}
         onClick={onClick}
         style={{
           flex: 1, minWidth: 0, background: palette.lin,
           border: `1px solid ${isSel ? st.ring : palette.natt}`,
           boxShadow: isSel ? `0 3px 0 ${st.ring}` : `0 2px 0 ${palette.natt}22`,
           cursor: "pointer", position: "relative",
           display: "flex", flexDirection: "column",
         }}>
      {/* state colour strip */}
      <div style={{height: 6, background: st.fill, borderBottom: `1px solid ${st.ring}`}}></div>

      <div style={{padding: "12px 14px 10px", flex: 1, display:"flex", flexDirection:"column"}}>
        <div style={{display:"flex", justifyContent:"space-between", alignItems:"baseline"}}>
          <div>
            <div style={{fontFamily:"'Newsreader', serif", fontWeight:600, fontSize:14.5,
                         color: palette.natt, lineHeight:1.15}}>{dim.name}</div>
            {latin && <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                                   fontSize:11.5, color: palette.ink3, marginTop:1}}>{dim.latin}</div>}
          </div>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9, letterSpacing:".12em",
                       color: palette.ink4}}>{dim.short}</div>
        </div>

        {/* state word — colour-coded, large */}
        <div style={{display:"flex", alignItems:"baseline", gap:8, marginTop:8}}>
          <span style={{fontFamily:"'Cormorant Garamond', serif", fontWeight:600, fontSize:23,
                        color: st.ring, lineHeight:1}}>{st.label}</span>
          <span style={{fontFamily:"'JetBrains Mono', monospace", fontSize:13, color: st.ring}}>
            {dim.dir === "up" ? "↗" : dim.dir === "down" ? "↘" : "→"}
          </span>
          <span style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:13,
                        color: palette.ink3}}>{dim.rate}</span>
        </div>

        {/* sparkline preview */}
        <div style={{marginTop:8, borderTop:`1px solid ${palette.ruleSoft}`, paddingTop:6}}>
          <AugurSpark series={dim.series} color={st.ring} height={38} width={220} dot={true}/>
        </div>

        {/* 24h preview — what's inside */}
        <div style={{marginTop:8, borderTop:`1px solid ${palette.ruleSoft}`, paddingTop:7, flex:1}}>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9, letterSpacing:".14em",
                       color: palette.ink3, marginBottom:4}}>
            24H · {changes.length} NEW EDGE{changes.length === 1 ? "" : "S"}
          </div>
          {top ? (
            <div style={{fontFamily:"'Newsreader', serif", fontSize:12, lineHeight:1.35,
                         color: palette.ink2, fontStyle:"italic",
                         display:"-webkit-box", WebkitLineClamp:2, WebkitBoxOrient:"vertical",
                         overflow:"hidden"}}>
              {top.body}
            </div>
          ) : (
            <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:12,
                         color: palette.ink4}}>Quiet revolution; no new edges.</div>
          )}
        </div>

        {/* explicit affordance */}
        <div className="ag-card-cta"
             style={{marginTop:10, paddingTop:8, borderTop:`1px solid ${palette.rule}`,
                     display:"flex", justifyContent:"space-between", alignItems:"center",
                     fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".14em",
                     color: isSel ? st.ring : palette.leirstein}}>
          <span>{isSel ? "CLOSE LEDGER" : "OPEN LEDGER"}</span>
          <span style={{fontSize:12}}>{isSel ? "↑" : "→"}</span>
        </div>
      </div>
    </div>
  );
}

// Expanded ledger under the card row
function DomainLedger({ dim, palette, onClose }) {
  const st = STATES[dim.state];
  const changes = AUGUR_CHANGES.filter(c => c.dim === dim.key);
  return (
    <div style={{border:`1px solid ${st.ring}`, borderTop:`3px solid ${st.fill}`,
                 background: palette.lin, padding:"20px 28px 24px", marginTop:-1,
                 display:"grid", gridTemplateColumns:"300px 1fr 300px", gap:32,
                 animation:"agFade .3s ease"}}>
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".16em",
                     color: palette.ink3, marginBottom:6}}>LEDGER · {dim.short}</div>
        <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:26,
                     lineHeight:1.05, color: palette.natt}}>{dim.latin}</div>
        <div style={{marginTop:12, fontFamily:"'Newsreader', serif", fontSize:14, lineHeight:1.5,
                     color: palette.ink2}}>{dim.note}</div>
        <div style={{marginTop:14, fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                     color: palette.ink3, lineHeight:1.7, letterSpacing:".04em"}}>
          rate · {dim.rate}<br/>accel · {dim.accel}<br/>sample · 48 mo monthly
        </div>
        <div style={{marginTop:16}}>
          <span onClick={onClose} style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5,
                letterSpacing:".16em", color: palette.leirstein, cursor:"pointer",
                borderBottom:`1px solid ${palette.leirstein}`, paddingBottom:2}}>↑ CLOSE</span>
        </div>
      </div>
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".16em",
                     color: palette.ink3, marginBottom:6}}>VELOCITY · 48 MO</div>
        <div style={{borderTop:`1px solid ${palette.rule}`, borderBottom:`1px solid ${palette.rule}`,
                     padding:"4px 0"}}>
          <AugurSpark series={dim.series} color={st.ring} height={110} width={520}/>
        </div>
        <div style={{display:"flex", justifyContent:"space-between",
                     fontFamily:"'JetBrains Mono', monospace", fontSize:9, color: palette.ink4, marginTop:3}}>
          <span>2022.05</span><span>2024</span><span>NOW</span>
        </div>
      </div>
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".16em",
                     color: palette.ink3, marginBottom:6}}>NEW EDGES · 24H</div>
        {changes.length === 0 && (
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:13.5,
                       color: palette.ink3}}>No structural changes this revolution.</div>
        )}
        {changes.map((c, i) => (
          <div key={i} style={{borderTop: i === 0 ? `1px solid ${palette.natt}` : `1px solid ${palette.ruleSoft}`,
                               padding:"8px 0"}}>
            <div style={{display:"flex", justifyContent:"space-between",
                         fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, color: palette.ink3,
                         marginBottom:3, letterSpacing:".04em"}}>
              <span>{c.time}</span><span style={{color: st.ring}}>{c.meta}</span>
            </div>
            <div style={{fontFamily:"'Newsreader', serif", fontSize:13, lineHeight:1.4, color: palette.natt}}>
              {c.body}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CAUSAL THREADS — topics merged with their 24h edges.

function CausalThreads({ palette }) {
  const [open, setOpen] = useState(null);
  return (
    <div>
      {AUGUR_TOPICS.map((t, i) => {
        const linked = topicChanges(i);
        const latest = linked[0];
        const isOpen = open === i;
        return (
          <div key={i} className="ag-thread" onClick={() => setOpen(isOpen ? null : i)}
               style={{borderTop: i === 0 ? `1px solid ${palette.natt}` : `1px solid ${palette.ruleSoft}`,
                       padding:"11px 6px", cursor:"pointer",
                       background: isOpen ? palette.lin : "transparent"}}>
            <div style={{display:"grid", gridTemplateColumns:"30px 380px 1fr 130px 22px", gap:18,
                         alignItems:"baseline"}}>
              <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, color: palette.ink4}}>
                {String(i + 1).padStart(2, "0")}
              </div>
              <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:17.5,
                           color: palette.natt, lineHeight:1.15}}>
                {t.title}
                {t.weight === "high" && <span style={{color: palette.leirstein, marginLeft:8, fontSize:13}}>●</span>}
              </div>
              <div style={{fontFamily:"'Newsreader', serif", fontSize:13, lineHeight:1.4,
                           color: latest ? palette.ink2 : palette.ink4}}>
                {latest
                  ? <span><span style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9,
                                        letterSpacing:".1em", color: palette.leirstein,
                                        marginRight:8}}>24H</span>{latest.body}</span>
                  : <span style={{fontStyle:"italic"}}>No new edges this revolution.</span>}
              </div>
              <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, color: palette.ink3,
                           textAlign:"right", letterSpacing:".04em"}}>{t.nodes} · {t.edges}</div>
              <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:12,
                           color: palette.leirstein, textAlign:"right"}}>{isOpen ? "↑" : "→"}</div>
            </div>
            {isOpen && (
              <div style={{padding:"10px 0 4px 48px", display:"grid",
                           gridTemplateColumns:"360px 1fr", gap:28, animation:"agFade .25s ease"}}>
                <div style={{fontFamily:"'Newsreader', serif", fontSize:13.5, lineHeight:1.5,
                             color: palette.ink2, fontStyle:"italic"}}>{t.gist}</div>
                <div>
                  {linked.length > 1 && linked.slice(1).map((c, j) => (
                    <div key={j} style={{borderTop:`1px solid ${palette.ruleSoft}`, padding:"6px 0",
                                         fontFamily:"'Newsreader', serif", fontSize:12.5, lineHeight:1.4,
                                         color: palette.ink2}}>
                      <span style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9, color: palette.ink4,
                                    marginRight:8}}>{c.time}</span>{c.body}
                    </div>
                  ))}
                  <div style={{marginTop:8, fontFamily:"'JetBrains Mono', monospace", fontSize:9.5,
                               letterSpacing:".14em", color: palette.leirstein}}>
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

// ─────────────────────────────────────────────────────────────────────────────
// YOUR LATITUDE — kept from option A, tightened.

function YourLatitude({ palette }) {
  return (
    <div style={{border:`1px solid ${palette.natt}`, background: palette.lin,
                 padding:"14px 16px 16px", position:"relative", height:"100%", boxSizing:"border-box"}}>
      <span style={{position:"absolute", top:-5, left:16, width:10, height:10, background: palette.leirstein,
                    border:`1px solid ${palette.natt}`, transform:"rotate(45deg)"}}></span>
      <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".16em",
                   color: palette.ink3, marginBottom:6}}>YOUR LATITUDE</div>
      <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:18,
                   color: palette.natt}}>{AUGUR_LOCAL.name}</div>
      <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, color: palette.ink4,
                   marginBottom:10}}>{AUGUR_LOCAL.coord}</div>
      <div style={{display:"flex", gap:8, marginBottom:10}}>
        {AUGUR_LOCAL.dims.map(d => {
          const st = STATES[d.state];
          return (
            <div key={d.key} style={{flex:1, textAlign:"center"}} title={`${d.key} · ${d.state}`}>
              <div style={{height:16, background: st.fill, border:`1px solid ${st.ring}`}}></div>
              <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:7.5, letterSpacing:".08em",
                           color: palette.ink3, marginTop:3}}>
                {AUGUR_DIMS.find(x => x.key === d.key).short}
              </div>
            </div>
          );
        })}
      </div>
      {AUGUR_LOCAL.changes.map((c, i) => (
        <div key={i} style={{borderTop:`1px solid ${palette.ruleSoft}`, padding:"6px 0",
                             fontFamily:"'Newsreader', serif", fontSize:12, lineHeight:1.4,
                             color: palette.ink2}}>{c}</div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LIVE HEADER — dynamic clocks + ingestion statistics.

function toRoman(n) {
  const map = [[1000,"M"],[900,"CM"],[500,"D"],[400,"CD"],[100,"C"],[90,"XC"],
               [50,"L"],[40,"XL"],[10,"X"],[9,"IX"],[5,"V"],[4,"IV"],[1,"I"]];
  let out = "";
  for (const [v, s] of map) { while (n >= v) { out += s; n -= v; } }
  return out;
}

// Quietly-wandering live counters. Each metric holds 1h/4h/24h window values
// that drift around a base rate — increments only, small, once per second.
const METRICS = [
  { key: "payloads", label: "PAYLOADS", base1h: 51400,  jitter: 30,  delta: false },
  { key: "signals",  label: "SIGNALS",  base1h: 9380,   jitter: 9,   delta: false },
  { key: "nodes",    label: "NODES",    base1h: 18,     jitter: 0.4, delta: true  },
  { key: "edges",    label: "EDGES",    base1h: 31,     jitter: 0.7, delta: true  },
];

function useLiveStats() {
  const [stats, setStats] = useState(() => {
    const s = {};
    METRICS.forEach(m => {
      s[m.key] = { h1: m.base1h, h4: Math.round(m.base1h * 3.92), h24: Math.round(m.base1h * 23.4), w: 0 };
    });
    return s;
  });
  useEffect(() => {
    const id = setInterval(() => {
      setStats(prev => {
        const next = {};
        METRICS.forEach(m => {
          const p = prev[m.key];
          const w = Math.max(-m.jitter * 40, Math.min(m.jitter * 40, p.w + (Math.random() - 0.48) * m.jitter * 2));
          next[m.key] = {
            w,
            h1:  Math.round(m.base1h + w),
            h4:  Math.round(m.base1h * 3.92 + w * 2.3),
            h24: p.h24 + Math.max(0, Math.round((Math.random() - 0.2) * m.jitter)),
          };
        });
        return next;
      });
    }, 1000);
    return () => clearInterval(id);
  }, []);
  return stats;
}

function fmtN(n) { return n.toLocaleString("en-US"); }

function LiveHeader({ palette }) {
  const [now, setNow] = useState(() => new Date());
  const stats = useLiveStats();
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const pad = (n) => String(n).padStart(2, "0");
  const localT = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  const utcT   = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
  const tzName = useMemo(() => {
    try {
      return new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
        .formatToParts(now).find(p => p.type === "timeZoneName")?.value || "LOC";
    } catch { return "LOC"; }
  }, []);
  const dateLine = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }).toUpperCase();
  const doy = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 864e5);

  const mono = "'JetBrains Mono', monospace";

  return (
    <div>
      {/* masthead */}
      <div style={{padding:"15px 48px 9px", borderBottom:`1px solid ${palette.natt}`,
                   display:"grid", gridTemplateColumns:"1fr auto 1fr", alignItems:"end"}}>
        <div>
          <div style={{fontFamily: mono, fontSize:10, letterSpacing:".18em", color: palette.ink2}}>
            {dateLine} {toRoman(now.getFullYear())} · PL. {toRoman(doy)}
          </div>
          <div style={{fontFamily: mono, fontSize:9, letterSpacing:".1em", color: palette.ink4, marginTop:4}}>
            GRAPH BUILD r9.4 · LAST FULL RE-DERIVATION 09:14 UTC
          </div>
        </div>
        <div style={{textAlign:"center"}}>
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize:30, color: palette.natt}}>Augur</div>
          <div style={{fontFamily: mono, fontSize:9, letterSpacing:".34em", color: palette.ink3, marginTop:2}}>HERBARIUM MUNDI</div>
        </div>
        <div style={{textAlign:"right"}}>
          <div style={{fontFamily: mono, fontSize:11, letterSpacing:".08em", color: palette.natt,
                       fontVariantNumeric:"tabular-nums"}}>
            <span style={{color: palette.ink3, fontSize:9, letterSpacing:".16em", marginRight:6}}>{tzName}</span>{localT}
            <span style={{color: palette.ink4, margin:"0 8px"}}>·</span>
            <span style={{color: palette.ink3, fontSize:9, letterSpacing:".16em", marginRight:6}}>UTC</span>{utcT}
          </div>
          <div style={{fontFamily: mono, fontSize:9, letterSpacing:".1em", color: palette.ink4, marginTop:4}}>
            OBSERVATIO · J. HARROW · SFO
          </div>
        </div>
      </div>

      {/* ingestion statistics strip */}
      <div style={{display:"flex", alignItems:"stretch", padding:"0 48px",
                   borderBottom:`1px solid ${palette.natt}`, background: palette.lin}}>
        <div style={{display:"flex", alignItems:"center", gap:7, padding:"8px 18px 8px 0",
                     borderRight:`1px solid ${palette.rule}`}}>
          <span style={{width:6, height:6, borderRadius:"50%", background: palette.mose,
                        border:`1px solid ${palette.moseDeep}`}}></span>
          <span style={{fontFamily: mono, fontSize:9, letterSpacing:".18em", color: palette.ink2}}>LIVE</span>
        </div>
        {METRICS.map((m, i) => {
          const s = stats[m.key];
          const sign = m.delta ? "+" : "";
          return (
            <div key={m.key} style={{display:"flex", alignItems:"baseline", gap:14,
                                     padding:"8px 18px", flex: m.delta ? "0 0 auto" : 1,
                                     borderRight: i < METRICS.length - 1 ? `1px solid ${palette.rule}` : "none"}}>
              <span style={{fontFamily: mono, fontSize:9, letterSpacing:".18em", color: palette.ink3}}>
                {m.label}
              </span>
              {[["1H", s.h1], ["4H", s.h4], ["24H", s.h24]].map(([w, v]) => (
                <span key={w} style={{fontFamily: mono, fontSize:10.5, color: palette.natt,
                                      fontVariantNumeric:"tabular-nums", whiteSpace:"nowrap"}}>
                  <span style={{fontSize:8, color: palette.ink4, letterSpacing:".1em", marginRight:5}}>{w}</span>
                  {sign}{fmtN(v)}
                </span>
              ))}
            </div>
          );
        })}
        <div style={{display:"flex", alignItems:"center", marginLeft:"auto", paddingLeft:18,
                     borderLeft:`1px solid ${palette.rule}`}}>
          <span style={{fontFamily: mono, fontSize:9, letterSpacing:".12em", color: palette.ink4}}>
            14,402 SOURCES NOMINAL
          </span>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// APP

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "latin": true,
  "showAsk": true,
  "surface": "bein"
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const palette = VINDINGUR;
  const [selected, setSelected] = useState(null);
  const [scrub, setScrub] = useState(1);
  const bg = t.surface === "lin" ? palette.lin : palette.bein;
  const heroBg = t.surface === "lin" ? palette.linDeep : palette.bein;

  return (
    <div style={{minHeight:"100vh", display:"flex", flexDirection:"column", background: bg,
                 color: palette.natt, position:"relative"}}>
      <style>{`
        @keyframes agFade { from { opacity: 0; transform: translateY(5px);} to { opacity: 1; transform: none;} }
        .ag-card { transition: transform .15s ease, box-shadow .15s ease; }
        .ag-card:hover { transform: translateY(-3px); box-shadow: 0 5px 0 rgba(23,28,28,.25) !important; }
        .ag-card:hover .ag-card-cta { color: #7a4029 !important; }
        .ag-thread:hover { background: ${palette.lin}; }
      `}</style>

      {/* HEADER — live clocks + ingestion stats */}
      <LiveHeader palette={palette}/>

      {/* HERO — overview · verdict · latitude */}
      <div style={{display:"grid", gridTemplateColumns:"330px 1fr 330px", gap:36,
                   padding:"26px 48px 22px", alignItems:"stretch",
                   background:`radial-gradient(ellipse at center top, ${palette.lin} 0%, ${heroBg} 75%)`,
                   borderBottom:`1px solid ${palette.rule}`}}>
        {/* plate overview — one paragraph */}
        <div style={{display:"flex", flexDirection:"column", justifyContent:"center"}}>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".16em",
                       color: palette.ink3, marginBottom:8}}>PLATE · OVERVIEW</div>
          <div style={{fontFamily:"'Newsreader', serif", fontSize:14.5, lineHeight:1.6, color: palette.ink2}}>
            The day's signal concentrates in the Gulf: Iran's Hormuz transit-fee proposal
            inverted Brent's term structure, while Russia's tightened phosphate quota is
            decoupling fertilizer from the urea benchmark. The ECB's dovish minutes and a
            flat hyperscaler order book mark the two quieter inflections. The lone
            counter-current remains structural — adaptation in semiconductors and energy
            still outruns baseline disruption.
          </div>
          <div style={{marginTop:12, fontFamily:"'JetBrains Mono', monospace", fontSize:9.5,
                       color: palette.ink4, letterSpacing:".06em", lineHeight:1.7}}>
            confidence · moderate, widening
          </div>
        </div>

        {/* verdict dial */}
        <div style={{textAlign:"center", display:"flex", flexDirection:"column", justifyContent:"flex-end"}}>
          <VerdictDial palette={palette} latin={t.latin}/>
          <div style={{marginTop:14}}>
            <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:15,
                         color: palette.ink3}}>Is the world improving?</div>
            <div style={{fontFamily:"'Cormorant Garamond', serif", fontWeight:600, fontSize:40,
                         lineHeight:1.05, color: palette.natt, marginTop:2}}>
              Not at present<span style={{color: palette.leirstein}}>.</span>
            </div>
            <div style={{fontFamily:"'Newsreader', serif", fontSize:13.5, color: palette.ink2,
                         marginTop:6, fontStyle:"italic"}}>
              Strained, worsening slowly — two domains deteriorating, two strained, one improving.
            </div>
          </div>
        </div>

        <YourLatitude palette={palette}/>
      </div>

      {/* DOMAIN CARDS */}
      <div style={{padding:"18px 48px 6px"}}>
        <div style={{display:"flex", justifyContent:"space-between", alignItems:"baseline", marginBottom:10}}>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".16em",
                       color: palette.ink3}}>FIVE DOMAINS · CLICK A CARD TO OPEN ITS LEDGER</div>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".08em",
                       color: palette.ink4}}>28-DAY DIRECTION VS TRAILING 180</div>
        </div>
        <div style={{display:"flex", gap:14, alignItems:"stretch"}}>
          {AUGUR_DIMS.map(d => (
            <DomainCard key={d.key} dim={d} palette={palette} latin={t.latin}
                        selected={selected}
                        onClick={() => setSelected(selected === d.key ? null : d.key)}/>
          ))}
        </div>
        {selected && (
          <DomainLedger dim={AUGUR_DIMS.find(x => x.key === selected)} palette={palette}
                        onClose={() => setSelected(null)}/>
        )}
      </div>

      {/* CAUSAL THREADS */}
      <div style={{padding:"20px 48px 26px", flex:1}}>
        <div style={{display:"flex", justifyContent:"space-between", alignItems:"baseline", marginBottom:8}}>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".16em",
                       color: palette.ink3}}>CAUSAL THREADS · NEWEST EDGE SHOWN INLINE · CLICK TO UNFOLD</div>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, color: palette.ink4,
                       letterSpacing:".08em"}}>7 OF 28 SUB-GRAPHS</div>
        </div>
        <CausalThreads palette={palette}/>
      </div>

      {/* SCRUBBER */}
      <AlmanacScrubber t={scrub} onChange={setScrub} events={AUGUR_EVENTS}
                       palette={palette} accent={palette.leirstein}/>

      {/* ASK */}
      {t.showAsk && (
        <div style={{position:"fixed", right:24, bottom:104, zIndex:40,
                     fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, background: palette.lin, padding:"6px 10px",
                     border:`1px solid ${palette.rule}`, cursor:"pointer"}}>
          Ask <span style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:13,
                            letterSpacing:0, color: palette.natt}}>Augur</span>
          <span style={{marginLeft:8, padding:"0 4px", border:`1px solid ${palette.rule}`, fontSize:9}}>⌘K</span>
        </div>
      )}

      <TweaksPanel>
        <TweakSection label="Surface" />
        <TweakRadio label="Paper tone" value={t.surface}
                    options={["bein", "lin"]}
                    onChange={(v) => setTweak("surface", v)} />
        <TweakSection label="Detail" />
        <TweakToggle label="Latin binomials" value={t.latin}
                     onChange={(v) => setTweak("latin", v)} />
        <TweakToggle label="Conversation entry" value={t.showAsk}
                     onChange={(v) => setTweak("showAsk", v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
