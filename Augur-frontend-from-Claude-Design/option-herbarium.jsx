// option-herbarium.jsx
// Augur as a Linnaean herbarium plate. Five specimens = five world dimensions.
// Status is colour-first (state hue), word-second (revealed on hover/click).
// Click a specimen → it unfurls into a detail panel underneath.

(() => {
const { useState, useMemo } = React;

// ─────────────────────────────────────────────────────────────────────────────
// Specimen — a procedurally-drawn botanical illustration.
// `seed` makes each specimen quietly distinct.

function Specimen({ dim, selected, hovered, onClick, onHover, palette, size = "default" }) {
  const state = STATES[dim.state];
  const isCompact = size === "compact";
  const W = isCompact ? 140 : 200;
  const H = isCompact ? 220 : 320;

  // procedural leaf positions
  const seed = dim.key.charCodeAt(0) + dim.key.charCodeAt(1);
  const leaves = useMemo(() => {
    const arr = [];
    const n = 5 + (seed % 3);
    let s = seed * 13;
    const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
    for (let i = 0; i < n; i++) {
      const yT = 0.18 + (i / (n + 0.5)) * 0.62;
      const side = i % 2 === 0 ? 1 : -1;
      const angle = (20 + rnd() * 28) * side;
      const len = 0.22 + rnd() * 0.10;
      arr.push({ yT, side, angle, len });
    }
    return arr;
  }, [seed]);

  const stemX = W / 2;
  const stemTop = H * 0.18;
  const stemBot = H * 0.92;

  const stroke = palette.ink;
  const stroke2 = palette.ink3;

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => onHover?.(dim.key)}
      onMouseLeave={() => onHover?.(null)}
      style={{
        position: "relative",
        cursor: "pointer",
        width: W, height: H,
        flex: "0 0 auto",
        opacity: selected ? 1 : hovered === null ? 1 : hovered === dim.key ? 1 : 0.6,
        transition: "opacity .25s ease",
      }}
    >
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{display:"block"}}>
        {/* mounting paper rectangle (very subtle) */}
        <rect x="0.5" y="0.5" width={W-1} height={H-1} fill="none" stroke={selected ? state.fill : palette.ruleSoft} strokeWidth={selected ? 1.2 : 0.5} />

        {/* botanical specimen — central stem */}
        <path d={`M ${stemX} ${stemBot} C ${stemX - 4} ${stemBot * 0.7}, ${stemX + 4} ${stemBot * 0.45}, ${stemX} ${stemTop}`}
              fill="none" stroke={stroke} strokeWidth="0.9" />

        {/* leaves */}
        {leaves.map((lf, i) => {
          const y = stemBot - (stemBot - stemTop) * (1 - lf.yT);
          const x = stemX;
          const dx = lf.side * Math.cos((lf.angle - 90) * Math.PI/180) * (W * lf.len);
          const dy = Math.sin((lf.angle - 90) * Math.PI/180) * (W * lf.len) - 6;
          const ex = x + dx, ey = y + dy;
          const mx = x + dx * 0.45, my = y + dy * 0.45 - 6 * lf.side;
          // leaf shape: two bezier curves forming a teardrop
          const lw = W * 0.06;
          return (
            <g key={i}>
              <path d={`M ${x} ${y} Q ${mx} ${my} ${ex} ${ey}`} fill="none" stroke={stroke} strokeWidth="0.7" />
              <path d={`M ${x} ${y}
                        C ${mx - lw * lf.side * 0.4} ${my - 3}, ${ex - 6 * lf.side} ${ey - 4}, ${ex} ${ey}
                        C ${ex - 4 * lf.side} ${ey + 6}, ${mx + lw * lf.side * 0.3} ${my + 4}, ${x} ${y} Z`}
                    fill={selected ? state.fill + "22" : "none"}
                    stroke={stroke2} strokeWidth="0.6" />
              {/* leaf mid-vein */}
              <path d={`M ${x} ${y} L ${ex} ${ey}`} stroke={stroke2} strokeWidth="0.4" opacity="0.7" />
            </g>
          );
        })}

        {/* the "fruit" / state indicator at the top of the stem */}
        <g>
          {/* subtle ring */}
          <circle cx={stemX} cy={stemTop} r={isCompact ? 11 : 14} fill={palette.lin} stroke={state.ring} strokeWidth="0.7" />
          <circle cx={stemX} cy={stemTop} r={isCompact ? 8 : 10} fill={state.fill} stroke={state.ring} strokeWidth="0.6" />
          {/* direction glyph inscribed on the fruit */}
          <text x={stemX} y={stemTop + 4} textAnchor="middle"
                fontFamily="'JetBrains Mono', monospace" fontSize={isCompact ? 10 : 12}
                fill={dim.state === "Improving" ? palette.lin : palette.lin} fontWeight="500">
            {dim.dir === "up" ? "↑" : dim.dir === "down" ? "↓" : "→"}
          </text>
        </g>

        {/* hand-pinned label — typeset */}
        <g>
          <line x1={W * 0.18} x2={W * 0.82} y1={H - 8} y2={H - 8} stroke={stroke} strokeWidth="0.4" />
          <text x={W/2} y={H - 18} textAnchor="middle"
                fontFamily="'Cormorant Garamond', serif" fontStyle="italic"
                fontSize={isCompact ? 11 : 13.5} fill={palette.ink2}>
            {dim.latin}
          </text>
          <text x={W/2} y={H - 32} textAnchor="middle"
                fontFamily="'JetBrains Mono', monospace"
                fontSize={isCompact ? 8.5 : 9.5} letterSpacing=".18em" fill={palette.ink3}>
            {dim.short} · PL.{(seed % 90 + 10)}
          </text>
        </g>
      </svg>

      {/* corner pin (decorative) */}
      <span style={{position:"absolute", top:6, left:6, width:6, height:6, background: palette.leirstein, border:`1px solid ${palette.natt}`, transform:"rotate(45deg)"}}/>
      <span style={{position:"absolute", top:6, right:6, width:6, height:6, background: palette.leirstein, border:`1px solid ${palette.natt}`, transform:"rotate(45deg)"}}/>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Detail panel — shows the global overview by default, or a specimen deep-dive
// when one is selected. Always sits below the specimen row.

function DetailPanel({ dim, palette, onClose }) {
  if (!dim) return null;
  const state = STATES[dim.state];
  const changes = AUGUR_CHANGES.filter(c => c.dim === dim.key);

  return (
    <div style={{
      borderTop: `1px solid ${palette.natt}`,
      background: palette.lin,
      padding: "22px 48px 30px",
      display: "grid",
      gridTemplateColumns: "320px 1fr 280px",
      gap: 36,
      animation: "hbFade .35s ease",
    }}>
      {/* left — specimen identity */}
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>SPECIMEN · {dim.short}</div>
        <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                     fontSize: 30, lineHeight:1.05, color: palette.natt}}>{dim.latin}</div>
        <div style={{fontFamily:"'Newsreader', serif", fontSize: 14.5,
                     color: palette.ink2, marginTop:4}}>{dim.name}</div>

        <div style={{marginTop: 20, display:"flex", gap:12, alignItems:"center"}}>
          <div style={{width:10, height:10, background: state.fill, border:`1px solid ${state.ring}`, borderRadius:"50%"}}/>
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize: 18, color: palette.natt}}>
            {state.label}
            <span style={{margin:"0 8px", color: palette.ink3}}>·</span>
            <span style={{fontStyle:"italic", color: state.ring}}>
              {dim.dir === "up" ? "↑ rising" : dim.dir === "down" ? "↓ falling" : "→ flat"}
            </span>
          </div>
        </div>

        <div style={{marginTop:18, fontFamily:"'Newsreader', serif", fontSize:14.5, lineHeight:1.5,
                     color: palette.ink2, fontStyle:"italic", maxWidth:"34ch"}}>
          {dim.note}
        </div>

        <div style={{marginTop:18, fontFamily:"'JetBrains Mono', monospace", fontSize:10.5,
                     color: palette.ink3, letterSpacing:".04em", lineHeight:1.7}}>
          rate · {dim.rate}<br/>
          accel · {dim.accel}<br/>
          sample · 48mo monthly
        </div>

        <div style={{marginTop:24}}>
          <span onClick={onClose} style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                letterSpacing:".18em", color: palette.leirstein, cursor:"pointer",
                borderBottom:`1px solid ${palette.leirstein}`, paddingBottom:2}}>
            ← BACK TO PLATE
          </span>
        </div>
      </div>

      {/* center — sparkline as ink drawing */}
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>VELOCITY · 48 MO.</div>
        <div style={{position:"relative", padding:"4px 0", borderTop:`1px solid ${palette.rule}`,
                     borderBottom:`1px solid ${palette.rule}`}}>
          <AugurSpark series={dim.series} color={state.ring} height={120} width={520} tail={8} />
          <div style={{position:"absolute", left:6, top:6, fontFamily:"'JetBrains Mono', monospace",
                       fontSize:9, color: palette.ink4}}>2022.05</div>
          <div style={{position:"absolute", right:6, top:6, fontFamily:"'JetBrains Mono', monospace",
                       fontSize:9, color: palette.ink4}}>NOW</div>
        </div>

        <div style={{marginTop:18, fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                     fontSize:15, color: palette.ink2, lineHeight:1.45, maxWidth:"60ch"}}>
          The trailing eight observations carry the line. Magnitude is qualitative;
          the curve itself is the argument.
        </div>
      </div>

      {/* right — 24h changes branching off */}
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>NEW EDGES · 24H</div>
        {changes.length === 0 && (
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", color: palette.ink3, fontSize:14}}>
            No structural changes registered for this specimen in the last revolution.
          </div>
        )}
        {changes.map((c, i) => (
          <div key={i} style={{borderTop: i === 0 ? `1px solid ${palette.natt}` : `1px solid ${palette.ruleSoft}`,
                               padding: "10px 0"}}>
            <div style={{display:"flex", justifyContent:"space-between", marginBottom:4,
                         fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                         color: palette.ink3, letterSpacing:".04em"}}>
              <span>{c.time}</span>
              <span style={{color: state.ring}}>{c.meta}</span>
            </div>
            <div style={{fontFamily:"'Newsreader', serif", fontSize:13.5, lineHeight:1.4, color: palette.natt}}>
              {c.body}
            </div>
            <div style={{marginTop:6, fontFamily:"'JetBrains Mono', monospace", fontSize:9.5,
                         letterSpacing:".1em", color: palette.ink3}}>
              ROOT · {c.root.toUpperCase()}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Global (no specimen selected) — show 24h overview
function GlobalDetail({ palette, onPick }) {
  return (
    <div style={{
      borderTop: `1px solid ${palette.natt}`,
      background: palette.lin,
      padding: "22px 48px 30px",
      display: "grid", gridTemplateColumns: "320px 1fr 280px", gap: 36,
    }}>
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>PLATE · OVERVIEW</div>
        <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize: 30,
                     lineHeight:1.05, color: palette.natt}}>
          The world,<br/>in summary.
        </div>
        <div style={{marginTop:18, fontFamily:"'Newsreader', serif", fontSize:14.5, lineHeight:1.55,
                     color: palette.ink2}}>
          Of the five load-bearing dimensions, <i>two</i> are deteriorating, <i>two</i> strained,
          and <i>one</i> improving — adaptation velocity in structural change is the only
          counter-current. Coupling between geopolitical and resource specimens has
          tightened across the trailing twenty-eight days.
        </div>
        <div style={{marginTop:20, fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                     letterSpacing:".18em", color: palette.ink3, lineHeight:1.7}}>
          confidence · moderate, widening<br/>
          ingestion · 14,402 sources nominal<br/>
          last full re-derivation · 09:14 utc
        </div>
      </div>

      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>NEW EDGES · 24H · TOP SEVEN</div>
        <div>
          {AUGUR_CHANGES.map((c, i) => {
            const d = AUGUR_DIMS.find(x => x.key === c.dim);
            const sc = STATES[d.state];
            return (
              <div key={i} onClick={() => onPick(c.dim)}
                   style={{borderTop: i === 0 ? `1px solid ${palette.natt}` : `1px solid ${palette.ruleSoft}`,
                           padding: "9px 0", cursor: "pointer",
                           display:"grid", gridTemplateColumns:"24px 56px 1fr 90px 14px", gap:14,
                           alignItems:"baseline"}}>
                <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, color: palette.ink4}}>
                  {String(i+1).padStart(2,"0")}
                </div>
                <div style={{display:"flex", alignItems:"center", gap:6}}>
                  <span style={{width:8, height:8, background: sc.fill, border:`1px solid ${sc.ring}`,
                                borderRadius:"50%", display:"inline-block"}}/>
                  <span style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5,
                                letterSpacing:".1em", color: palette.ink3}}>{d.short}</span>
                </div>
                <div style={{fontFamily:"'Newsreader', serif", fontSize:13.5, lineHeight:1.35,
                             color: palette.natt}}>{c.body}</div>
                <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, color: sc.ring,
                             textAlign:"right"}}>{c.meta}</div>
                <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                             fontSize:14, color: palette.leirstein}}>→</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* right — local mirror as a pinned mini-specimen */}
      <div>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>YOUR LATITUDE</div>
        <div style={{border:`1px solid ${palette.natt}`, background: palette.bein, padding:"14px 14px 16px",
                     position:"relative"}}>
          <span style={{position:"absolute", top:-5, left:14, width:10, height:10, background: palette.leirstein,
                        border:`1px solid ${palette.natt}`, transform:"rotate(45deg)"}}/>
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:17,
                       color: palette.natt}}>{AUGUR_LOCAL.name}</div>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, color: palette.ink3,
                       marginBottom:10}}>{AUGUR_LOCAL.coord}</div>
          <div style={{display:"flex", gap:10, marginBottom:12}}>
            {AUGUR_LOCAL.dims.map(d => {
              const sc = STATES[d.state];
              return (
                <div key={d.key} style={{flex:1, textAlign:"center"}}>
                  <div style={{width:14, height:14, background: sc.fill, border:`1px solid ${sc.ring}`,
                               borderRadius:"50%", margin:"0 auto 4px"}}/>
                  <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:8, letterSpacing:".08em",
                               color: palette.ink3}}>
                    {AUGUR_DIMS.find(x=>x.key===d.key).short}
                  </div>
                </div>
              );
            })}
          </div>
          {AUGUR_LOCAL.changes.map((c,i) => (
            <div key={i} style={{borderTop: `1px solid ${palette.ruleSoft}`, padding:"7px 0",
                                 fontFamily:"'Newsreader', serif", fontSize:12.5, lineHeight:1.4,
                                 color: palette.ink2}}>{c}</div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function HerbariumOption() {
  const palette = VINDINGUR;
  const [selected, setSelected] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [scrub, setScrub] = useState(1);
  const [openTopic, setOpenTopic] = useState(null);

  return (
    <div style={{
      width:"100%", height:"100%",
      display:"flex", flexDirection:"column",
      background: `${palette.bein}`,
      fontFamily:"'Newsreader', serif",
      color: palette.natt,
    }}>
      <style>{`
        @keyframes hbFade { from { opacity: 0; transform: translateY(6px);} to { opacity: 1; transform: none;} }
        .hb-spec:hover .hb-cap { color: ${palette.leirstein}; }
        .hb-topic:hover { background: ${palette.lin}; }
      `}</style>

      {/* PLATE HEADER — minimal, almanac-style */}
      <div style={{padding:"16px 48px 8px", borderBottom: `1px solid ${palette.ink}`,
                   display:"grid", gridTemplateColumns:"1fr auto 1fr", alignItems:"end"}}>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3}}>
          MMXXVI · PL. CDXIV · ANNO XIII MAII
        </div>
        <div style={{textAlign:"center"}}>
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize: 30, letterSpacing:".02em",
                       color: palette.natt}}>Augur</div>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".34em",
                       color: palette.ink3, marginTop:2}}>HERBARIUM MUNDI</div>
        </div>
        <div style={{textAlign:"right", fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                     letterSpacing:".08em", color: palette.ink3}}>
          OBSERVATIO · J. HARROW &nbsp; LOC · SFO
        </div>
      </div>

      {/* SPECIMEN ROW — the world, colour first */}
      <div style={{padding: "30px 48px 28px",
                   display:"flex", justifyContent:"space-around", alignItems:"flex-start",
                   borderBottom: `1px solid ${palette.rule}`,
                   background: `radial-gradient(ellipse at center top, ${palette.lin} 0%, ${palette.bein} 70%)`}}>
        {AUGUR_DIMS.map(d => (
          <div className="hb-spec" key={d.key}>
            <Specimen dim={d} palette={palette}
                      selected={selected === d.key}
                      hovered={hovered}
                      onClick={() => setSelected(selected === d.key ? null : d.key)}
                      onHover={setHovered} />
          </div>
        ))}
      </div>

      {/* DETAIL PANEL — overview by default, specimen view when one is selected */}
      <div style={{flex:1, minHeight: 0, overflow:"hidden"}}>
        {selected
          ? <DetailPanel dim={AUGUR_DIMS.find(x => x.key === selected)} palette={palette}
                         onClose={() => setSelected(null)} />
          : <GlobalDetail palette={palette} onPick={setSelected} />}
      </div>

      {/* TOPICS — botanical "thread" cards, expand on click */}
      <div style={{padding: "16px 48px 16px", borderTop:`1px solid ${palette.natt}`,
                   borderBottom:`1px solid ${palette.rule}`, background: palette.bein}}>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:10}}>
          CAUSAL THREADS · {AUGUR_TOPICS.length} ACTIVE SUB-GRAPHS
        </div>
        <div style={{display:"grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 0,
                     borderTop:`1px solid ${palette.rule}`}}>
          {AUGUR_TOPICS.map((t, i) => (
            <div key={i} className="hb-topic"
                 onClick={() => setOpenTopic(openTopic === i ? null : i)}
                 style={{padding:"12px 14px", borderRight: i < AUGUR_TOPICS.length-1 ? `1px solid ${palette.rule}` : "none",
                         cursor:"pointer", position:"relative"}}>
              {/* botanical thread glyph (vertical) */}
              <svg width="2" height="36" viewBox="0 0 2 36"
                   style={{position:"absolute", left:6, top:14}}>
                <path d="M1 0 C 1.3 12, 0.7 24, 1 36" stroke={t.weight === "high" ? palette.leirstein : palette.mose}
                      strokeWidth="0.8" fill="none"/>
              </svg>
              <div style={{paddingLeft:14}}>
                <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                             fontSize:14.5, lineHeight:1.2, color: palette.natt}}>{t.title}</div>
                <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9, color: palette.ink3,
                             marginTop:6, letterSpacing:".04em"}}>
                  {t.nodes}·{t.edges}
                </div>
                {openTopic === i && (
                  <div style={{marginTop:8, fontFamily:"'Newsreader', serif", fontSize:12, lineHeight:1.4,
                               color: palette.ink2, fontStyle:"italic"}}>
                    {t.gist}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ALMANAC RIBBON SCRUBBER */}
      <AlmanacScrubber t={scrub} onChange={setScrub} events={AUGUR_EVENTS}
                       palette={palette} accent={palette.leirstein} />

      {/* ASK · subscript */}
      <div style={{position:"absolute", right:24, bottom:120,
                   fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                   color: palette.ink3, background: palette.lin, padding: "6px 10px",
                   border: `1px solid ${palette.rule}`}}>
        Ask <span style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                          fontSize:13, textTransform:"none", letterSpacing:0, color: palette.natt}}>Augur</span>
        <span style={{marginLeft:8, padding:"0 4px", border:`1px solid ${palette.rule}`, fontSize:9}}>⌘K</span>
      </div>
    </div>
  );
}

window.HerbariumOption = HerbariumOption;
})();
