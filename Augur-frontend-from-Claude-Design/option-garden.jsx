// option-garden.jsx
// Augur as a top-down naturalist garden plan.
// Five beds = five dimensions, tinted by current state.
// Cultivation rows + scattered marks show activity (24h changes as plants).
// Topics are labelled paths between beds. Click a bed to enter.

(() => {
const { useState, useMemo } = React;

// Garden bed geometry (inside the walled-garden SVG)
// SVG dims 880 x 600. Origin at top-left. Walls inset 24px.
const SVG_W = 880, SVG_H = 600;
const WALL_PAD = 24;
const BEDS = {
  geo:    { x: 84,  y: 60,  w: 240, h: 150, label: "GEO" },
  econ:   { x: 556, y: 60,  w: 240, h: 150, label: "ECON" },
  env:    { x: 250, y: 232, w: 380, h: 136, label: "ENV" },   // center bed
  res:    { x: 84,  y: 390, w: 240, h: 150, label: "RES" },
  struct: { x: 556, y: 390, w: 240, h: 150, label: "STR" },
};

// Topic-paths anchored between adjacent beds
// Each path has anchor coords + label text + which dims it bridges
function pathsForGarden() {
  return [
    { id: 0, x: 440, y: 38,  label: "Iran–Israel", bridges: ["geo", "econ"], orient: "h" },
    { id: 2, x: 156, y: 300, label: "Fertilizer & food", bridges: ["geo", "res"], orient: "v" },
    { id: 3, x: 720, y: 300, label: "Semiconductor geography", bridges: ["econ", "struct"], orient: "v" },
    { id: 1, x: 440, y: 568, label: "AI labor displacement", bridges: ["res", "struct"], orient: "h" },
  ];
}

// Render the garden bed as cultivation rows + activity marks
function GardenBed({ dim, palette, selected, hovered, onClick, onHover }) {
  const st = STATES[dim.state];
  const b = BEDS[dim.key];
  const changes = AUGUR_CHANGES.filter(c => c.dim === dim.key);
  // distribute plants on a grid
  const rowH = 9;
  const rows = Math.floor((b.h - 28) / rowH);
  // pseudo-random plant positions seeded by dim.key
  const plants = useMemo(() => {
    const out = [];
    let s = dim.key.charCodeAt(0) * 31 + dim.key.charCodeAt(1);
    const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
    // ~2 plants per change, distributed across the bed
    const count = changes.length * 2 + 3;
    for (let i = 0; i < count; i++) {
      const r = Math.floor(rnd() * rows);
      const c = 8 + rnd() * (b.w - 16);
      const y = 18 + r * rowH + 2;
      out.push({ x: c, y, big: i < changes.length });
    }
    return out;
  }, [dim.key, changes.length]);

  const stroke = selected ? st.ring : palette.ink3;
  const strokeW = selected ? 1.6 : 0.6;

  return (
    <g style={{cursor:"pointer", opacity: hovered === null || hovered === dim.key || selected === dim.key ? 1 : 0.55,
               transition:"opacity .25s"}}
       onClick={onClick}
       onMouseEnter={() => onHover(dim.key)}
       onMouseLeave={() => onHover(null)}>
      {/* bed plot fill (tinted) */}
      <rect x={b.x} y={b.y} width={b.w} height={b.h} fill={st.fill} fillOpacity={selected === dim.key ? 0.28 : 0.16}
            stroke={stroke} strokeWidth={strokeW}/>
      {/* cultivation rows */}
      {Array.from({length: rows}, (_, i) => (
        <line key={i}
              x1={b.x + 8} x2={b.x + b.w - 8}
              y1={b.y + 18 + i * rowH} y2={b.y + 18 + i * rowH}
              stroke={st.ring} strokeWidth="0.35" opacity="0.55" strokeDasharray="2 5"/>
      ))}
      {/* plants — small "y" marks */}
      {plants.map((p, i) => (
        <g key={i} transform={`translate(${b.x + p.x}, ${b.y + p.y})`}>
          <line x1="0" y1="0" x2="0" y2={p.big ? -6 : -3} stroke={st.ring} strokeWidth={p.big ? 1 : 0.6}/>
          {p.big && (
            <>
              <line x1="0" y1="-3" x2="-2" y2="-5" stroke={st.ring} strokeWidth=".7"/>
              <line x1="0" y1="-3" x2="2"  y2="-5" stroke={st.ring} strokeWidth=".7"/>
            </>
          )}
        </g>
      ))}
      {/* corner pegs */}
      {[[b.x, b.y], [b.x + b.w, b.y], [b.x, b.y + b.h], [b.x + b.w, b.y + b.h]].map(([cx, cy], i) => (
        <circle key={i} cx={cx} cy={cy} r="1.2" fill={palette.natt}/>
      ))}
      {/* bed label, top-left */}
      <g>
        <rect x={b.x + 6} y={b.y + 6} width="58" height="14" fill={palette.lin} stroke={stroke} strokeWidth="0.4"/>
        <text x={b.x + 35} y={b.y + 16} textAnchor="middle"
              fontFamily="'JetBrains Mono', monospace" fontSize="9.5" letterSpacing=".14em"
              fill={st.ring}>{b.label}</text>
      </g>
      {/* latin binomial along bottom */}
      <text x={b.x + b.w / 2} y={b.y + b.h - 8} textAnchor="middle"
            fontFamily="'Cormorant Garamond', serif" fontStyle="italic" fontSize="12"
            fill={palette.ink2}>{dim.latin}</text>
      {/* state corner dot */}
      <circle cx={b.x + b.w - 12} cy={b.y + 12} r="4" fill={st.fill} stroke={st.ring} strokeWidth="0.6"/>
    </g>
  );
}

// Garden topic path
function GardenPath({ path, palette, hover, onHover, onClick }) {
  const topic = AUGUR_TOPICS[path.id];
  const isHov = hover === path.id;
  const length = path.orient === "h" ? 240 : 130;
  return (
    <g onClick={onClick}
       onMouseEnter={() => onHover(path.id)}
       onMouseLeave={() => onHover(null)}
       style={{cursor:"pointer"}}>
      {/* dashed pathway */}
      {path.orient === "h" ? (
        <line x1={path.x - length/2} y1={path.y} x2={path.x + length/2} y2={path.y}
              stroke={isHov ? palette.leirstein : palette.ink3} strokeWidth={isHov ? 1.2 : 0.7}
              strokeDasharray="1.5 3"/>
      ) : (
        <line x1={path.x} y1={path.y - length/2} x2={path.x} y2={path.y + length/2}
              stroke={isHov ? palette.leirstein : palette.ink3} strokeWidth={isHov ? 1.2 : 0.7}
              strokeDasharray="1.5 3"/>
      )}
      {/* label */}
      <text x={path.x} y={path.y + 4}
            textAnchor="middle"
            fontFamily="'Cormorant Garamond', serif" fontStyle="italic"
            fontSize={isHov ? 13 : 12} fill={isHov ? palette.leirstein : palette.ink2}
            transform={path.orient === "v" ? `rotate(-90, ${path.x}, ${path.y})` : undefined}>
        {topic.title}
      </text>
    </g>
  );
}

function GardenMap({ palette, selected, hovered, onSelect, onHover, pathHover, onPathHover, onPathOpen }) {
  return (
    <svg viewBox={`0 0 ${SVG_W} ${SVG_H}`} width="100%" height="100%" style={{display:"block"}}>
      <defs>
        <pattern id="ga-paper" patternUnits="userSpaceOnUse" width="6" height="6">
          <rect width="6" height="6" fill={palette.lin}/>
        </pattern>
      </defs>

      {/* page / vellum */}
      <rect x="0" y="0" width={SVG_W} height={SVG_H} fill="url(#ga-paper)"/>
      <rect x="6" y="6" width={SVG_W - 12} height={SVG_H - 12} fill="none" stroke={palette.natt} strokeWidth="0.5"/>

      {/* walled garden — outer wall (double hairline) */}
      <rect x={WALL_PAD} y={WALL_PAD} width={SVG_W - WALL_PAD*2} height={SVG_H - WALL_PAD*2}
            fill="none" stroke={palette.natt} strokeWidth="1.2"/>
      <rect x={WALL_PAD + 4} y={WALL_PAD + 4} width={SVG_W - WALL_PAD*2 - 8} height={SVG_H - WALL_PAD*2 - 8}
            fill="none" stroke={palette.natt} strokeWidth="0.4"/>
      {/* corner ornaments — botanical knots */}
      {[[WALL_PAD, WALL_PAD], [SVG_W - WALL_PAD, WALL_PAD],
        [WALL_PAD, SVG_H - WALL_PAD], [SVG_W - WALL_PAD, SVG_H - WALL_PAD]].map(([cx, cy], i) => (
        <g key={i}>
          <circle cx={cx} cy={cy} r="6" fill={palette.lin} stroke={palette.natt} strokeWidth="0.6"/>
          <circle cx={cx} cy={cy} r="3" fill={palette.leirstein} stroke={palette.natt} strokeWidth="0.4"/>
          <line x1={cx-9} y1={cy} x2={cx+9} y2={cy} stroke={palette.natt} strokeWidth="0.4"/>
          <line x1={cx} y1={cy-9} x2={cx} y2={cy+9} stroke={palette.natt} strokeWidth="0.4"/>
        </g>
      ))}

      {/* main paths — cross between beds */}
      {pathsForGarden().map(p =>
        <GardenPath key={p.id} path={p} palette={palette}
                    hover={pathHover} onHover={onPathHover} onClick={() => onPathOpen(p.id)}/>
      )}

      {/* beds */}
      {AUGUR_DIMS.map(d => (
        <GardenBed key={d.key} dim={d} palette={palette}
                   selected={selected} hovered={hovered}
                   onClick={() => onSelect(selected === d.key ? null : d.key)}
                   onHover={onHover}/>
      ))}

      {/* Homestead — your latitude */}
      <g>
        <rect x={WALL_PAD + 14} y={SVG_H - WALL_PAD - 38} width="56" height="22"
              fill={palette.bein} stroke={palette.natt} strokeWidth="0.6"/>
        <path d={`M ${WALL_PAD + 14} ${SVG_H - WALL_PAD - 38}
                  L ${WALL_PAD + 42} ${SVG_H - WALL_PAD - 50}
                  L ${WALL_PAD + 70} ${SVG_H - WALL_PAD - 38} Z`}
              fill={palette.bein} stroke={palette.natt} strokeWidth="0.6"/>
        <text x={WALL_PAD + 42} y={SVG_H - WALL_PAD - 22} textAnchor="middle"
              fontFamily="'JetBrains Mono', monospace" fontSize="9" letterSpacing=".12em"
              fill={palette.natt}>SFO</text>
      </g>

      {/* compass rose, NE */}
      <g transform={`translate(${SVG_W - WALL_PAD - 42}, ${WALL_PAD + 40})`}>
        <circle cx="0" cy="0" r="16" fill={palette.bein} stroke={palette.natt} strokeWidth="0.5"/>
        <line x1="0" y1="-13" x2="0" y2="13" stroke={palette.natt} strokeWidth="0.5"/>
        <line x1="-13" y1="0" x2="13" y2="0" stroke={palette.natt} strokeWidth="0.5"/>
        <path d="M 0 -12 L -3 0 L 0 3 L 3 0 Z" fill={palette.natt}/>
        <text x="0" y="-18" textAnchor="middle"
              fontFamily="'JetBrains Mono', monospace" fontSize="8" letterSpacing=".18em" fill={palette.ink3}>N</text>
      </g>

      {/* peripheral topic markers — the topics that aren't on internal paths */}
      {AUGUR_TOPICS.slice(4).map((t, i) => {
        const ay = 80 + i * 60;
        return (
          <g key={i} transform={`translate(${SVG_W - WALL_PAD - 8}, ${ay})`}>
            <line x1="-12" y1="0" x2="0" y2="0" stroke={palette.ink3} strokeWidth="0.5" strokeDasharray="2 2"/>
            <circle cx="-14" cy="0" r="2" fill={palette.leirstein}/>
            <text x="-22" y="3" textAnchor="end"
                  fontFamily="'Cormorant Garamond', serif" fontStyle="italic" fontSize="11"
                  fill={palette.ink2}>{t.title}</text>
          </g>
        );
      })}

      {/* CARTOUCHE — title cartouche at top */}
      <g transform={`translate(${SVG_W/2}, ${WALL_PAD + 30})`}>
        <rect x="-110" y="-18" width="220" height="36" fill={palette.lin} stroke={palette.natt} strokeWidth="0.5"/>
        <text x="0" y="-3" textAnchor="middle"
              fontFamily="'Cormorant Garamond', serif" fontStyle="italic"
              fontSize="13" fill={palette.ink2}>Hortus Mundi</text>
        <text x="0" y="11" textAnchor="middle"
              fontFamily="'JetBrains Mono', monospace" fontSize="8" letterSpacing=".28em"
              fill={palette.ink3}>PLAN · MMXXVI · ANNO XIII MAII</text>
      </g>
    </svg>
  );
}

// Right rail — detail
function GardenRail({ palette, selected, pathOpen, onPick, onClosePath }) {
  // path open trumps selected when both
  if (pathOpen !== null) {
    const t = AUGUR_TOPICS[pathOpen];
    return (
      <div style={{display:"flex", flexDirection:"column", height:"100%",
                   background: palette.lin, borderLeft:`1px solid ${palette.natt}`,
                   padding:"24px 28px"}}>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3}}>PATHWAY · CAUSAL DOMAIN</div>
        <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:26,
                     color: palette.natt, marginTop:6, lineHeight:1.1}}>{t.title}</div>
        <div style={{marginTop:10, fontFamily:"'Newsreader', serif", fontSize:14, lineHeight:1.5,
                     color: palette.ink2}}>{t.gist}</div>
        <div style={{marginTop:14, fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                     letterSpacing:".06em", color: palette.ink3}}>
          {t.nodes} nodes · {t.edges} edges · weight {t.weight}
        </div>
        <div style={{marginTop:22, fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3}}>BEDS ALONG THIS PATH</div>
        <div style={{display:"flex", gap:10, marginTop:8}}>
          {AUGUR_DIMS.map(d => {
            const st = STATES[d.state];
            return (
              <div key={d.key} onClick={() => onPick(d.key)} style={{
                flex:1, padding:"10px 8px", border:`1px solid ${palette.rule}`, background: palette.bein,
                textAlign:"center", cursor:"pointer"
              }}>
                <div style={{width:10, height:10, background: st.fill, border:`1px solid ${st.ring}`,
                             borderRadius:"50%", margin:"0 auto 4px"}}/>
                <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9,
                             letterSpacing:".1em", color: palette.ink2}}>{d.short}</div>
              </div>
            );
          })}
        </div>
        <div style={{marginTop:22}}>
          <span onClick={onClosePath} style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                letterSpacing:".18em", color: palette.leirstein, cursor:"pointer",
                borderBottom:`1px solid ${palette.leirstein}`, paddingBottom:2}}>
            ← BACK TO GARDEN
          </span>
        </div>
      </div>
    );
  }

  const dim = selected ? AUGUR_DIMS.find(x => x.key === selected) : null;
  const changes = selected ? AUGUR_CHANGES.filter(c => c.dim === selected) : AUGUR_CHANGES;

  return (
    <div style={{display:"flex", flexDirection:"column", height:"100%",
                 background: palette.lin, borderLeft:`1px solid ${palette.natt}`,
                 padding:"24px 28px"}}>
      <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                   color: palette.ink3, marginBottom:8}}>
        {selected ? `BED · ${dim.short}` : "GARDEN · OVERVIEW"}
      </div>
      <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:28,
                   color: palette.natt, lineHeight:1.05}}>
        {selected ? dim.name : "The world,"}
        {!selected && <><br/><span>in cultivation.</span></>}
      </div>
      {selected && (
        <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize:14, color: palette.ink3,
                     marginTop:2, fontStyle:"italic"}}>{dim.latin}</div>
      )}

      {selected ? (
        <>
          <div style={{marginTop:18, display:"flex", alignItems:"center", gap:10}}>
            <div style={{width:12, height:12, background: STATES[dim.state].fill,
                         border:`1px solid ${STATES[dim.state].ring}`, borderRadius:"50%"}}/>
            <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize:18, color: palette.natt}}>
              {STATES[dim.state].label} · <span style={{fontStyle:"italic", color: STATES[dim.state].ring}}>
                {dim.dir === "up" ? "↑" : dim.dir === "down" ? "↓" : "→"} {dim.rate}
              </span>
            </div>
          </div>
          <div style={{marginTop:14, fontFamily:"'Newsreader', serif", fontSize:14.5, lineHeight:1.5,
                       color: palette.ink2}}>{dim.note}</div>
          {/* sparkline */}
          <div style={{marginTop:18, padding:"6px 0", borderTop:`1px solid ${palette.rule}`,
                       borderBottom:`1px solid ${palette.rule}`}}>
            <AugurSpark series={dim.series} color={STATES[dim.state].ring} height={56} width={360}/>
            <div style={{display:"flex", justifyContent:"space-between",
                         fontFamily:"'JetBrains Mono', monospace", fontSize:9, color: palette.ink4,
                         marginTop:2}}>
              <span>2022.05</span><span>NOW</span>
            </div>
          </div>
        </>
      ) : (
        <div style={{marginTop:14, fontFamily:"'Newsreader', serif", fontSize:14, lineHeight:1.55,
                     color: palette.ink2}}>
          A nine-square plan, walled. Five beds under cultivation, four pathways
          binding them. The plant-marks on each bed are <i>new edges in the
          causal graph</i> registered in the trailing twenty-four hours — visible
          density reads as activity.
        </div>
      )}

      <div style={{marginTop:22, fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                   letterSpacing:".18em", color: palette.ink3, marginBottom:6}}>
        {selected ? "PLANT-MARKS · 24H" : "THIS DAY'S HARVEST"}
      </div>
      <div style={{flex:1, minHeight:0, overflowY:"auto", paddingRight:6, marginRight:-6}}>
        {changes.map((c, i) => {
          const d = AUGUR_DIMS.find(x => x.key === c.dim);
          const st = STATES[d.state];
          return (
            <div key={i} onClick={() => onPick(c.dim)}
                 style={{borderTop: i === 0 ? `1px solid ${palette.natt}` : `1px solid ${palette.ruleSoft}`,
                         padding:"9px 0", cursor:"pointer"}}>
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

      {/* Local */}
      <div style={{marginTop:14, paddingTop:12, borderTop:`1px solid ${palette.natt}`}}>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:6}}>HOMESTEAD · {AUGUR_LOCAL.coord}</div>
        <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic", fontSize:15, color: palette.natt}}>
          {AUGUR_LOCAL.name}
        </div>
        <div style={{display:"flex", gap:5, marginTop:8, marginBottom:8}}>
          {AUGUR_LOCAL.dims.map(d => {
            const st = STATES[d.state];
            return (
              <div key={d.key} title={STATES[d.state].label}
                   style={{flex:1, height:10, background: st.fill, border:`1px solid ${st.ring}`}}/>
            );
          })}
        </div>
        {AUGUR_LOCAL.changes.slice(0, 2).map((c, i) => (
          <div key={i} style={{fontFamily:"'Newsreader', serif", fontSize:12, lineHeight:1.35,
                               color: palette.ink2, marginTop:4}}>· {c}</div>
        ))}
      </div>
    </div>
  );
}

function GardenOption() {
  const palette = VINDINGUR;
  const [selected, setSelected] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [pathHover, setPathHover] = useState(null);
  const [pathOpen, setPathOpen] = useState(null);
  const [scrub, setScrub] = useState(1);

  const handleSelect = (k) => { setPathOpen(null); setSelected(k); };
  const handlePathOpen = (id) => { setSelected(null); setPathOpen(id); };

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
          MMXXVI · HORTUS · ANNO XIII MAII
        </div>
        <div style={{textAlign:"center"}}>
          <div style={{fontFamily:"'Cormorant Garamond', serif", fontSize: 28, color: palette.natt}}>Augur</div>
          <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9.5, letterSpacing:".34em",
                       color: palette.ink3, marginTop:2}}>HORTUS MUNDI · A WALLED PLAN</div>
        </div>
        <div style={{textAlign:"right", fontFamily:"'JetBrains Mono', monospace", fontSize:10,
                     letterSpacing:".08em", color: palette.ink3}}>
          KEEPER · J. HARROW · SFO
        </div>
      </div>

      {/* BODY: garden + rail */}
      <div style={{flex:1, display:"grid", gridTemplateColumns:"1fr 420px", minHeight:0}}>
        <div style={{padding:"16px 16px 8px", display:"flex", alignItems:"center", justifyContent:"center",
                     minHeight:0}}>
          <GardenMap palette={palette}
                     selected={selected}
                     hovered={hovered}
                     onSelect={handleSelect}
                     onHover={setHovered}
                     pathHover={pathHover}
                     onPathHover={setPathHover}
                     onPathOpen={handlePathOpen}/>
        </div>
        <GardenRail palette={palette} selected={selected} pathOpen={pathOpen}
                    onPick={handleSelect} onClosePath={() => setPathOpen(null)}/>
      </div>

      {/* TOPICS — index of paths along the bottom */}
      <div style={{padding:"10px 36px 14px", background: palette.bein,
                   borderTop:`1px solid ${palette.natt}`}}>
        <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:10, letterSpacing:".18em",
                     color: palette.ink3, marginBottom:8}}>
          PATHWAYS · CAUSAL DOMAINS · TAP TO ENTER
        </div>
        <div style={{display:"grid", gridTemplateColumns:"repeat(7, 1fr)", gap:0,
                     borderTop:`1px solid ${palette.rule}`}}>
          {AUGUR_TOPICS.map((t, i) => (
            <div key={i} onClick={() => handlePathOpen(i)}
                 style={{padding:"10px 12px",
                         borderRight: i < AUGUR_TOPICS.length-1 ? `1px solid ${palette.rule}` : "none",
                         cursor:"pointer", background: pathOpen === i ? palette.lin : "transparent"}}>
              <div style={{fontFamily:"'Cormorant Garamond', serif", fontStyle:"italic",
                           fontSize:13.5, lineHeight:1.2, color: palette.natt}}>{t.title}</div>
              <div style={{fontFamily:"'JetBrains Mono', monospace", fontSize:9, color: palette.ink3,
                           marginTop:4, letterSpacing:".04em"}}>
                {t.nodes}·{t.edges}{t.weight === "high" ? " ●" : ""}
              </div>
            </div>
          ))}
        </div>
      </div>

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

window.GardenOption = GardenOption;
})();
