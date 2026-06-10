// option-astrolabe.jsx
// Augur as a brass astrolabe / polar almanac.
// Five concentric rings = five dimensions, each tinted by current state.
// Time is angular — full revolution = 4 years. Series wraps as a polar trace.
// 24h-change markers sit near the "now" radius on their parent ring.

(() => {
const { useState, useMemo } = React;

const TAU = Math.PI * 2;

// Ring radii from center, inner → outer
const RING_R = { econ: 90, geo: 140, res: 190, env: 240, struct: 290 };
const RING_KEYS = ["econ", "geo", "res", "env", "struct"];
const CENTER = 380;
const SVG_SIZE = 760;
const OUTER_R = 340; // time scrubber rail

// Convert (t∈[0,1], r) → cartesian. t=0 at 12 o'clock, increases clockwise.
function polar(t, r) {
  const a = -Math.PI / 2 + t * TAU;
  return [CENTER + Math.cos(a) * r, CENTER + Math.sin(a) * r];
}

function ringTrace(series, baseR, amp = 22) {
  const min = Math.min(...series), max = Math.max(...series);
  const rng = Math.max(0.05, max - min);
  return series.map((v, i) => {
    const t = i / (series.length - 1);
    const dev = (v - min) / rng - 0.5; // -.5..+.5
    return polar(t, baseR + dev * amp * 2);
  });
}

function Astrolabe({ palette, selected, onSelect, scrub, hoverChange, onHoverChange }) {
  // Memoize traces
  const traces = useMemo(() => {
    const out = {};
    AUGUR_DIMS.forEach(d => { out[d.key] = ringTrace(d.series, RING_R[d.key]); });
    return out;
  }, []);

  const nowT = scrub;
  // 24h changes sit slightly outside the now angle (cluster near it)
  const changeAngles = useMemo(() => {
    return AUGUR_CHANGES.map((c, i) => {
      // cluster within ±0.5° of now
      const offset = (i - AUGUR_CHANGES.length / 2) * 0.004;
      return { ...c, idx: i, t: Math.max(0, Math.min(1, nowT + offset)) };
    });
  }, [nowT]);

  return (
    <svg viewBox={`0 0 ${SVG_SIZE} ${SVG_SIZE}`} width="100%" height="100%"
         style={{display:"block", maxWidth: SVG_SIZE, maxHeight: SVG_SIZE, margin:"0 auto"}}>
      <defs>
        <radialGradient id="as-vellum" cx="50%" cy="50%" r="55%">
          <stop offset="0%"  stopColor={palette.lin}/>
          <stop offset="100%" stopColor={palette.bein}/>
        </radialGradient>
        <filter id="as-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3"/>
          <feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .04 0"/>
          <feComposite in2="SourceGraphic" operator="in"/>
        </filter>
      </defs>

      {/* vellum disk */}
      <circle cx={CENTER} cy={CENTER} r={OUTER_R + 18} fill="url(#as-vellum)" stroke={palette.natt} strokeWidth="0.8"/>
      <circle cx={CENTER} cy={CENTER} r={OUTER_R + 12} fill="none" stroke={palette.natt} strokeWidth="0.4"/>
      <circle cx={CENTER} cy={CENTER} r={OUTER_R + 18} fill="none" filter="url(#as-grain)"/>

      {/* radial year ticks on outer scrubber rail */}
      {[0, 1, 2, 3, 4].map(i => {
        const t = i / 4;
        const [x1, y1] = polar(t, OUTER_R - 6);
        const [x2, y2] = polar(t, OUTER_R + 6);
        const [lx, ly] = polar(t, OUTER_R + 18);
        return (
          <g key={i}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={palette.ink3} strokeWidth="0.8"/>
            <text x={lx} y={ly + 3} textAnchor="middle"
                  fontFamily="'JetBrains Mono', monospace" fontSize="9.5"
                  fill={palette.ink3} letterSpacing=".06em">{2022 + i}</text>
          </g>
        );
      })}
      {/* minor quarter ticks */}
      {Array.from({length: 16}, (_, i) => i).map(i => {
        if (i % 4 === 0) return null;
        const t = i / 16;
        const [x1, y1] = polar(t, OUTER_R - 3);
        const [x2, y2] = polar(t, OUTER_R + 3);
        return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={palette.ink4} strokeWidth="0.4"/>;
      })}

      {/* historical events on outer rail */}
      {AUGUR_EVENTS.map((e, i) => {
        const [x, y] = polar(e.t, OUTER_R);
        const [lx, ly] = polar(e.t, OUTER_R - 24);
        return (
          <g key={i}>
            <circle cx={x} cy={y} r="2.2" fill={palette.natt}/>
            <text x={lx} y={ly} textAnchor="middle"
                  fontFamily="'Cormorant Garamond', serif" fontStyle="italic"
                  fontSize="9" fill={palette.ink3}
                  transform={`rotate(${(e.t * 360 - 0) - (e.t < .5 ? -90 : 90)}, ${lx}, ${ly})`}>
              {e.label}
            </text>
          </g>
        );
      })}

      {/* rings — for each dimension */}
      {AUGUR_DIMS.map(d => {
        const st = STATES[d.state];
        const r = RING_R[d.key];
        const isSel = selected === d.key;
        const dim = selected && !isSel;
        return (
          <g key={d.key} style={{opacity: dim ? 0.35 : 1, transition: "opacity .25s"}}
             onClick={() => onSelect(isSel ? null : d.key)}
             onMouseEnter={(e) => { e.currentTarget.style.cursor = "pointer"; }}>
            {/* hover/select halo */}
            {isSel && <circle cx={CENTER} cy={CENTER} r={r} fill="none" stroke={st.fill}
                              strokeWidth="22" opacity="0.16"/>}
            {/* nominal ring (hairline) */}
            <circle cx={CENTER} cy={CENTER} r={r} fill="none"
                    stroke={palette.ink3} strokeWidth="0.45" strokeDasharray="2 3"/>
            {/* trace as polar path */}
            <path d={traces[d.key].map((p,i) => (i ? "L" : "M") + p[0].toFixed(1)+","+p[1].toFixed(1)).join(" ")}
                  fill="none" stroke={st.fill} strokeWidth={isSel ? 2 : 1.2} opacity={isSel ? 1 : 0.85}
                  strokeLinejoin="round"/>
            {/* tail (last quarter) thicker */}
            <path d={traces[d.key].slice(-12).map((p,i) => (i ? "L" : "M") + p[0].toFixed(1)+","+p[1].toFixed(1)).join(" ")}
                  fill="none" stroke={st.ring} strokeWidth={isSel ? 2.6 : 1.8}/>
            {/* "planet" — current value marker */}
            {(() => {
              const last = traces[d.key][traces[d.key].length - 1];
              return (
                <g>
                  <circle cx={last[0]} cy={last[1]} r="5" fill={palette.lin} stroke={st.ring} strokeWidth="0.8"/>
                  <circle cx={last[0]} cy={last[1]} r="3.2" fill={st.fill}/>
                </g>
              );
            })()}
            {/* ring label — at the 9 o'clock side, on the ring */}
            <g>
              {(() => {
                const [lx, ly] = polar(0.75, r);
                return (
                  <>
                    <rect x={lx - 36} y={ly - 8} width="72" height="14" fill={palette.lin}
                          stroke={isSel ? st.ring : palette.rule} strokeWidth="0.5"/>
                    <text x={lx} y={ly + 3} textAnchor="middle"
                          fontFamily="'JetBrains Mono', monospace" fontSize="9"
                          letterSpacing=".14em" fill={isSel ? st.ring : palette.ink2}>
                      {d.short}
                    </text>
                  </>
                );
              })()}
            </g>
          </g>
        );
      })}

      {/* 24h change markers — small ticks adjacent to "now" radius, color-coded by dim */}
      {changeAngles.map((c) => {
        const dim = AUGUR_DIMS.find(d => d.key === c.dim);
        const st = STATES[dim.state];
        const r = RING_R[c.dim];
        const [x, y] = polar(c.t, r);
        // marker slightly outside the trace
        const [ox, oy] = polar(c.t, r + 10);
        const hovered = hoverChange === c.idx;
        return (
          <g key={c.idx}
             style={{cursor:"pointer"}}
             onMouseEnter={() => onHoverChange(c.idx)}
             onMouseLeave={() => onHoverChange(null)}
             onClick={() => onSelect(c.dim)}>
            <line x1={x} y1={y} x2={ox} y2={oy} stroke={palette.natt} strokeWidth="0.6"/>
            <circle cx={ox} cy={oy} r={hovered ? 4 : 2.6} fill={st.fill} stroke={palette.natt} strokeWidth="0.6"/>
            {hovered && (
              <text x={ox + 8} y={oy + 3} fontFamily="'Cormorant Garamond', serif" fontStyle="italic"
                    fontSize="11" fill={palette.natt}>{c.meta}</text>
            )}
          </g>
        );
      })}

      {/* "now" radius arm */}
      {(() => {
        const [x1, y1] = polar(nowT, 50);
        const [x2, y2] = polar(nowT, OUTER_R + 4);
        return (
          <g>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={palette.leirstein} strokeWidth="1"/>
            <circle cx={x2} cy={y2} r="3" fill={palette.leirstein}/>
          </g>
        );
      })()}

      {/* center compass-rose */}
      <g>
        <circle cx={CENTER} cy={CENTER} r="42" fill={palette.lin} stroke={palette.natt} strokeWidth="0.6"/>
        <circle cx={CENTER} cy={CENTER} r="32" fill="none" stroke={palette.ink3} strokeWidth="0.3"/>
        {/* cardinal points */}
        {[0, 0.25, 0.5, 0.75].map((t, i) => {
          const [x1, y1] = polar(t, 8);
          const [x2, y2] = polar(t, 38);
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={palette.natt} strokeWidth="0.5"/>;
        })}
        {/* diagonal */}
        {[0.125, 0.375, 0.625, 0.875].map((t, i) => {
          const [x1, y1] = polar(t, 10);
          const [x2, y2] = polar(t, 30);
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={palette.ink3} strokeWidth="0.3"/>;
        })}
        <text x={CENTER} y={CENTER - 4} textAnchor="middle"
              fontFamily="'Cormorant Garamond', serif" fontStyle="italic"
              fontSize="14" fill={palette.natt}>Augur</text>
        <text x={CENTER} y={CENTER + 10} textAnchor="middle"
              fontFamily="'JetBrains Mono', monospace" fontSize="7.5"
              letterSpacing=".24em" fill={palette.ink3}>MMXXVI</text>
      </g>

      {/* Reset hit area — click outside rings to deselect */}
      <circle cx={CENTER} cy={CENTER} r={OUTER_R + 18} fill="transparent"
              onClick={() => onSelect(null)} style={{pointerEvents: "visibleFill"}}/>
    </svg>
  );
}

// Right rail — detail
function AstrolabeRail({ palette, selected, onPick, hoverChange }) {
  const dim = selected ? AUGUR_DIMS.find(x => x.key === selected) : null;
  const changes = selected ? AUGUR_CHANGES.filter(c => c.dim === selected) : AUGUR_CHANGES;
  const heading = selected ? dim.name : "The world, in summary";
  const latin = selected ? dim.latin : "Mundus in compendio";
  const headState = selected ? STATES[dim.state] : null;

  return (
    <div style={{display:"flex", flexDirection:"column", height:"100%",
                 background: palette.lin, borderLeft: `1px solid ${palette.natt}`,
                 padding: "26px 30px"}}>
      <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                   color: palette.ink3, marginBottom:8}}>
        {selected ? `RING · ${dim.short}` : "OVERVIEW"}
      </div>
      <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                   fontSize: 28, lineHeight:1.05, color: palette.natt}}>
        {heading}
      </div>
      <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize:14, color: palette.ink3,
                   marginTop: 2, fontStyle:"italic"}}>{latin}</div>

      {selected ? (
        <>
          <div style={{marginTop:18, display:"flex", alignItems:"center", gap:10}}>
            <div style={{width:12, height:12, background: headState.fill, border:`1px solid ${headState.ring}`, borderRadius:"50%"}}/>
            <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize:18, color: palette.natt}}>
              {headState.label} · <span style={{fontStyle:"italic", color: headState.ring}}>{dim.dir === "up" ? "↑" : dim.dir === "down" ? "↓" : "→"} {dim.rate}</span>
            </div>
          </div>
          <div style={{marginTop:14, fontFamily:"'Newsreader', serif", fontSize:14.5, lineHeight:1.5, color: palette.ink2}}>
            {dim.note}
          </div>
          <div style={{marginTop:14, fontFamily:"'JetBrains Mono', monospace", fontSize:10, color: palette.ink3, letterSpacing:".04em"}}>
            rate · {dim.rate}<br/>
            accel · {dim.accel}
          </div>
        </>
      ) : (
        <div style={{marginTop:14, fontFamily:"'Newsreader', serif", fontSize:14, lineHeight:1.55, color: palette.ink2}}>
          Of the five rings, two are <i>deteriorating</i>, two <i>strained</i>, one <i>improving</i>.
          Coupling between geopolitical and resource rings has tightened across
          the trailing twenty-eight days. Click a ring to drill in; click any tick
          near the now-arm to read a recent change.
        </div>
      )}

      <div style={{marginTop:22, fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                   letterSpacing:".18em", color: palette.ink3, marginBottom:6}}>
        {selected ? "NEW EDGES · 24H" : "TOP CHANGES · 24H"}
      </div>
      <div style={{flex:1, minHeight: 0, overflowY:"auto", paddingRight: 6, marginRight: -6}}>
        {changes.map((c, i) => {
          const d = AUGUR_DIMS.find(x => x.key === c.dim);
          const st = STATES[d.state];
          const isHov = AUGUR_CHANGES.indexOf(c) === hoverChange;
          return (
            <div key={i} onClick={() => onPick(c.dim)}
                 style={{borderTop: i === 0 ? `1px solid ${palette.natt}` : `1px solid ${palette.ruleSoft}`,
                         padding:"9px 0", cursor:"pointer",
                         background: isHov ? palette.bein : "transparent"}}>
              <div style={{display:"flex", justifyContent:"space-between",
                           fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, color: palette.ink3,
                           letterSpacing:".06em", marginBottom:4}}>
                <span>{d.short} · {c.time}</span>
                <span style={{color: st.ring}}>{c.meta}</span>
              </div>
              <div style={{fontFamily:"'Newsreader', serif", fontSize:13, lineHeight:1.4, color: palette.natt}}>
                {c.body}
              </div>
            </div>
          );
        })}
      </div>

      {/* Local mirror — small compass at the bottom of the rail */}
      <div style={{marginTop: 18, paddingTop: 14, borderTop: `1px solid ${palette.natt}`}}>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>
          YOUR LATITUDE · {AUGUR_LOCAL.coord}
        </div>
        <div style={{display:"flex", gap:14, alignItems:"center"}}>
          {/* mini astrolabe */}
          <svg viewBox="0 0 80 80" width="74" height="74">
            <circle cx="40" cy="40" r="36" fill={palette.bein} stroke={palette.natt} strokeWidth="0.6"/>
            {AUGUR_LOCAL.dims.map((d, i) => {
              const st = STATES[d.state];
              const r = 8 + i * 6;
              return <circle key={d.key} cx="40" cy="40" r={r} fill="none" stroke={st.fill} strokeWidth="2"/>;
            })}
            <circle cx="40" cy="40" r="3" fill={palette.natt}/>
          </svg>
          <div style={{flex:1}}>
            <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:15, color: palette.natt}}>
              {AUGUR_LOCAL.name}
            </div>
            <div style={{marginTop:6}}>
              {AUGUR_LOCAL.changes.slice(0, 2).map((c, i) => (
                <div key={i} style={{fontFamily:"'Newsreader', serif", fontSize:12, lineHeight:1.35,
                                     color: palette.ink2, marginTop:4}}>· {c}</div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Topics — radial labels around the astrolabe
function AstrolabeTopics({ palette, expanded, onToggle }) {
  return (
    <div style={{padding: "10px 36px 16px", background: palette.bein,
                 borderTop:`1px solid ${palette.natt}`}}>
      <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                   color: palette.ink3, marginBottom:8}}>
        CAUSAL DOMAINS · TAP TO TRANSIT
      </div>
      <div style={{display:"grid", gridTemplateColumns:"repeat(7, 1fr)", gap:0, borderTop:`1px solid ${palette.rule}`}}>
        {AUGUR_TOPICS.map((t, i) => (
          <div key={i} onClick={() => onToggle(i)}
               style={{padding:"10px 12px",
                       borderRight: i < AUGUR_TOPICS.length-1 ? `1px solid ${palette.rule}` : "none",
                       cursor:"pointer", background: expanded === i ? palette.lin : "transparent"}}>
            <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                         fontSize:14, lineHeight:1.2, color: palette.natt}}>{t.title}</div>
            <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9, color: palette.ink3,
                         marginTop:4, letterSpacing:".04em"}}>
              {t.nodes}·{t.edges}{t.weight === "high" ? " ●" : ""}
            </div>
            {expanded === i && (
              <div style={{marginTop:6, fontFamily:"'Newsreader', serif", fontSize:12, lineHeight:1.4,
                           color: palette.ink2, fontStyle:"italic"}}>
                {t.gist}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function AstrolabeOption() {
  const palette = VINDINGUR;
  const [selected, setSelected] = useState(null);
  const [hoverChange, setHoverChange] = useState(null);
  const [scrub, setScrub] = useState(1);
  const [openTopic, setOpenTopic] = useState(null);

  return (
    <div style={{
      width:"100%", height:"100%",
      display:"flex", flexDirection:"column",
      background: palette.bein,
      fontFamily:"'Newsreader', serif",
      color: palette.natt,
      position:"relative",
    }}>
      {/* HEADER */}
      <div style={{padding:"14px 36px 8px", borderBottom: `1px solid ${palette.ink}`,
                   display:"grid", gridTemplateColumns:"1fr auto 1fr", alignItems:"end"}}>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3}}>
          MMXXVI · ASTROLABIUM · ANNO XIII MAII
        </div>
        <div style={{textAlign:"center"}}>
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize: 28, color: palette.natt}}>Augur</div>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".34em",
                       color: palette.ink3, marginTop:2}}>INSTRUMENTUM CAUSARUM</div>
        </div>
        <div style={{textAlign:"right", fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                     letterSpacing:".08em", color: palette.ink3}}>
          J. HARROW · SFO · 11:42 UTC
        </div>
      </div>

      {/* BODY: astrolabe + rail */}
      <div style={{flex:1, display:"grid", gridTemplateColumns:"1fr 420px", minHeight:0}}>
        <div style={{padding:"20px 20px 10px", display:"flex", alignItems:"center", justifyContent:"center",
                     minHeight: 0, position:"relative"}}>
          <Astrolabe palette={palette}
                     selected={selected}
                     onSelect={setSelected}
                     scrub={scrub}
                     hoverChange={hoverChange}
                     onHoverChange={setHoverChange} />
        </div>
        <AstrolabeRail palette={palette} selected={selected} onPick={setSelected}
                       hoverChange={hoverChange}/>
      </div>

      {/* TOPICS */}
      <AstrolabeTopics palette={palette} expanded={openTopic} onToggle={i => setOpenTopic(openTopic === i ? null : i)}/>

      {/* SCRUBBER */}
      <AlmanacScrubber t={scrub} onChange={setScrub} events={AUGUR_EVENTS}
                       palette={palette} accent={palette.leirstein}/>

      {/* ASK */}
      <div style={{position:"absolute", right:24, bottom:152,
                   fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                   color: palette.ink3, background: palette.lin, padding:"6px 10px",
                   border:`1px solid ${palette.rule}`}}>
        Ask <span style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                          fontSize:13, textTransform:"none", letterSpacing:0, color: palette.natt}}>Augur</span>
        <span style={{marginLeft:8, padding:"0 4px", border:`1px solid ${palette.rule}`, fontSize:9}}>⌘K</span>
      </div>
    </div>
  );
}

window.AstrolabeOption = AstrolabeOption;
})();
