import React, { useEffect, useMemo, useState } from 'react';
import {
  Bar, CartesianGrid, Cell, ComposedChart, Area, Legend, Line, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

// AI-lab: zelf waarden invullen en de echte modellen van de backend laten rekenen.
// Dezelfde modellen als in de live winkel (ai/model_leeg.json en ai/model_bestelling.json).

const nl1 = new Intl.NumberFormat('nl-BE', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nl0 = new Intl.NumberFormat('nl-BE', { maximumFractionDigits: 0 });
const geld = new Intl.NumberFormat('nl-BE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const BAKKEN = [
  { id: '01', naam: 'Broccoli', emoji: '🥦', cap: 15, stuk: 400 },
  { id: '02', naam: 'Lotuswortel', emoji: '🪷', cap: 15, stuk: 450 },
  { id: '03', naam: 'Aubergine', emoji: '🍆', cap: 10, stuk: 250 },
  { id: '04', naam: 'Groene paprika', emoji: '🫑', cap: 12, stuk: 60 },
];
const DAGEN = ['Ma', 'Di', 'Wo', 'Do', 'Vr', 'Za', 'Zo'];
const STATUS = {
  ok: { kleur: '#27ae60', label: 'In orde' },
  laag: { kleur: '#f39c12', label: 'Binnenkort bijvullen' },
  kritiek: { kleur: '#e74c3c', label: 'Nu bijvullen!' },
  leeg: { kleur: '#7f1d1d', label: 'LEEG' },
};
const PRESETS = [
  { naam: '🔥 Zaterdagpiek', bak: '01', kg: 6, tijd: 17 * 60, weekdag: 5, drukte: 1.4 },
  { naam: '☕ Rustige ochtend', bak: '03', kg: 7, tijd: 9 * 60 + 40, weekdag: 1, drukte: 0.7 },
  { naam: '⚠️ Bijna leeg', bak: '04', kg: 1.2, tijd: 18 * 60 + 10, weekdag: 4, drukte: 1.2 },
  { naam: '🌙 Vlak voor sluiting', bak: '02', kg: 3, tijd: 21 * 60 + 20, weekdag: 6, drukte: 1 },
];

const klok = (min) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
const willekeurig = (a, b) => a + Math.random() * (b - a);
const druktetekst = (d) => (d < 0.75 ? 'rustiger dan normaal' : d <= 1.25 ? 'een gewone dag' : d <= 2 ? 'drukker dan normaal' : 'uitzonderlijk druk');

// Vraagt `url` op, maar pas als de invoer even stilstaat; oudere vragen worden afgebroken.
function useVertraagd(url, wacht = 160) {
  const [staat, setStaat] = useState({ data: null, fout: null, bezig: false });
  useEffect(() => {
    if (!url) return undefined;
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      setStaat((s) => ({ ...s, bezig: true }));
      try {
        const r = await fetch(url, { signal: ctrl.signal });
        const j = await r.json();
        if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText);
        setStaat({ data: j, fout: null, bezig: false });
      } catch (e) {
        if (e.name !== 'AbortError') setStaat((s) => ({ ...s, fout: String(e.message || e), bezig: false }));
      }
    }, wacht);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [url, wacht]);
  return staat;
}

function Veld({ label, waarde, children, uitleg }) {
  return (
    <div className="lab-veld">
      <div className="lab-veld-kop"><span>{label}</span>{waarde !== undefined && <strong>{waarde}</strong>}</div>
      {children}
      {uitleg && <div className="lab-uitleg">{uitleg}</div>}
    </div>
  );
}

function Pil({ status }) {
  const s = STATUS[status] || STATUS.ok;
  return <span className="lab-pil" style={{ color: s.kleur, borderColor: s.kleur }}>{s.label}</span>;
}

// ----------------------------------------------------------------------------- AI 1
function LeegLab({ api }) {
  const [bak, setBak] = useState('01');
  const [kg, setKg] = useState(4);
  const [tijd, setTijd] = useState(16 * 60 + 30);
  const [weekdag, setWeekdag] = useState(5);
  const [drukte, setDrukte] = useState(1.3);
  const [bijgevuld, setBijgevuld] = useState('08:30');
  const b = BAKKEN.find((x) => x.id === bak);

  const kies = (nieuw) => { setBak(nieuw); setKg((k) => Math.min(k, BAKKEN.find((x) => x.id === nieuw).cap)); };
  const preset = (p) => { setBak(p.bak); setKg(p.kg); setTijd(p.tijd); setWeekdag(p.weekdag); setDrukte(p.drukte); setBijgevuld('08:30'); };
  const verras = () => {
    const nb = BAKKEN[Math.floor(Math.random() * 4)];
    setBak(nb.id); setKg(Math.round(willekeurig(0.4, nb.cap * 0.75) * 10) / 10);
    setTijd(Math.round(willekeurig(9.5 * 60, 21 * 60) / 10) * 10); setWeekdag(Math.floor(Math.random() * 7));
    setDrukte(Math.round(willekeurig(0.5, 2) * 10) / 10);
  };

  const url = `${api}/api/lab/leeg?bak=${bak}&kg=${kg}&tijd=${klok(tijd)}&weekdag=${weekdag}&drukte=${drukte}&bijgevuld=${bijgevuld}`;
  const { data, fout, bezig } = useVertraagd(url);
  const v = data?.voorspelling;
  const s = STATUS[data?.status] || STATUS.ok;
  const pct = Math.min(100, (100 * kg) / b.cap);
  const tikken = useMemo(() => {
    if (!data?.verloop?.length) return [];
    const max = data.verloop[data.verloop.length - 1].min;
    const stap = max > 240 ? 60 : max > 90 ? 30 : 15;
    return data.verloop.filter((p) => p.min % stap === 0).map((p) => p.min);
  }, [data]);
  const tijdVan = (m) => data?.verloop?.find((p) => p.min === m)?.tekst ?? '';
  const k = data?.kenmerken;
  const maxBalk = data ? Math.max(v.minuten, data.baseline_min, 1) : 1;

  return (
    <section className="lab-kaart">
      <div className="lab-kaart-kop">
        <div>
          <h2>⏱️ Wanneer is de bak leeg?</h2>
          <p>Beschrijf de toestand van een bak. De AI voorspelt hoeveel openingsminuten het duurt tot hij leeg is als niemand bijvult.</p>
        </div>
        <div className="lab-knoppen">
          <button type="button" className="lab-knop accent" onClick={verras}>🎲 Verras me</button>
          {PRESETS.map((p) => <button key={p.naam} type="button" className="lab-knop" onClick={() => preset(p)}>{p.naam}</button>)}
        </div>
      </div>

      <div className="lab-raster">
        <div className="lab-invoer">
          <Veld label="Welke bak?">
            <div className="lab-segment vier">
              {BAKKEN.map((x) => (
                <button key={x.id} type="button" aria-pressed={x.id === bak} onClick={() => kies(x.id)}>
                  <span className="lab-emoji">{x.emoji}</span>{x.naam}
                </button>
              ))}
            </div>
          </Veld>

          <Veld label="Hoeveel ligt er nog in de bak?" waarde={`${nl1.format(kg)} kg`}
                uitleg={`${nl0.format(pct)}% van een volle bak (${b.cap} kg) · ± ${nl0.format((kg * 1000) / b.stuk)} stuks van ${b.stuk} g`}>
            <div className="lab-bak" aria-hidden="true"><i style={{ width: `${pct}%`, background: s.kleur }} /></div>
            <input type="range" min="0" max={b.cap} step="0.1" value={kg} onChange={(e) => setKg(+e.target.value)} aria-label="Gewicht in de bak (kg)" />
          </Veld>

          <Veld label="Hoe laat is het?" waarde={klok(tijd)} uitleg="De winkel is open van 9u tot 22u.">
            <input type="range" min={9 * 60} max={21 * 60 + 50} step="10" value={tijd} onChange={(e) => setTijd(+e.target.value)} aria-label="Tijdstip" />
          </Veld>

          <Veld label="Welke dag?">
            <div className="lab-segment zeven">
              {DAGEN.map((d, i) => <button key={d} type="button" aria-pressed={i === weekdag} onClick={() => setWeekdag(i)}>{d}</button>)}
            </div>
          </Veld>

          <Veld label="Hoe druk is het vandaag?" waarde={`${nl1.format(drukte)}×`} uitleg={`${druktetekst(drukte)} (1× = een gewone ${['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag'][weekdag]})`}>
            <input type="range" min="0.3" max="3" step="0.1" value={drukte} onChange={(e) => setDrukte(+e.target.value)} aria-label="Drukte" />
          </Veld>

          <Veld label="Laatst bijgevuld om">
            <input type="time" className="lab-tijd" min="08:00" max="21:59" value={bijgevuld} onChange={(e) => setBijgevuld(e.target.value || '08:30')} />
          </Veld>
        </div>

        <div className={`lab-uitvoer${bezig ? ' bezig' : ''}`}>
          {fout && <div className="lab-fout">Kan de AI niet bereiken: {fout}</div>}
          {!data && !fout && <div className="lab-leeg">AI rekent …</div>}
          {data && (
            <>
              <div className="lab-resultaat" style={{ borderColor: s.kleur }}>
                <div className="lab-resultaat-kop"><span>🤖 AI-voorspelling · {b.emoji} {data.product}</span><Pil status={data.status} /></div>
                {v.binnen_horizon ? (
                  <>
                    <div className="lab-groot" style={{ color: s.kleur }}>
                      {data.pct_vol < 3 ? 'Nu leeg' : <>Leeg om {v.leeg.tekst}</>}
                    </div>
                    <div className="lab-sub">
                      over <strong>{nl0.format(v.minuten)} min</strong> · marge {v.vroegst.tekst} – {v.laatst.tekst}
                      {v.leeg.dag > 0 && <span className="lab-dim"> · de nacht telt niet mee, de winkel is dan dicht</span>}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="lab-groot" style={{ color: s.kleur }}>Ruim genoeg</div>
                    <div className="lab-sub">nog meer dan 10 openingsuren voorraad</div>
                  </>
                )}
                <div className="lab-vergelijk">
                  <div className="lab-vergelijk-rij">
                    <span>AI</span>
                    <div className="lab-meter"><i style={{ width: `${(100 * v.minuten) / maxBalk}%`, background: '#2980b9' }} /></div>
                    <strong>{nl0.format(v.minuten)} min</strong>
                  </div>
                  <div className="lab-vergelijk-rij">
                    <span>Simpel</span>
                    <div className="lab-meter"><i style={{ width: `${(100 * data.baseline_min) / maxBalk}%`, background: '#95a5a6' }} /></div>
                    <strong>{nl0.format(data.baseline_min)} min</strong>
                  </div>
                  <div className="lab-uitleg">Simpel = gewicht ÷ verbruik van het laatste uur. De AI kent ook het verkooppatroon per uur en weekdag.</div>
                </div>
              </div>

              <div className="lab-grafiek">
                <div className="lab-grafiek-titel">Verwacht verloop volgens het verkooppatroon × drukte</div>
                <ResponsiveContainer width="100%" height={210}>
                  <ComposedChart data={data.verloop} margin={{ top: 10, right: 12, bottom: 0, left: -10 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="min" type="number" domain={[0, 'dataMax']} ticks={tikken} tickFormatter={tijdVan} fontSize={12} />
                    <YAxis domain={[0, Math.ceil(Math.max(kg, 1))]} tickFormatter={(x) => `${x} kg`} fontSize={12} />
                    <Tooltip formatter={(x) => [`${nl1.format(x)} kg`, 'In de bak']} labelFormatter={(m) => `${tijdVan(m)} (over ${m} min)`} />
                    {v.binnen_horizon && <ReferenceArea x1={v.minuten_min} x2={v.minuten_max} fill="#2980b9" fillOpacity={0.08} />}
                    <Area type="monotone" dataKey="kg" stroke="#16a085" fill="#16a085" fillOpacity={0.18} strokeWidth={2} isAnimationActive={false} />
                    {v.binnen_horizon && <ReferenceLine x={v.minuten} stroke="#2980b9" strokeWidth={2} label={{ value: 'AI: leeg', position: 'insideTopRight', fill: '#2980b9', fontSize: 12 }} />}
                    {data.baseline_min < 600 && <ReferenceLine x={data.baseline_min} stroke="#95a5a6" strokeDasharray="4 4" label={{ value: 'simpel', position: 'insideTopLeft', fill: '#7f8c8d', fontSize: 12 }} />}
                  </ComposedChart>
                </ResponsiveContainer>
                <div className="lab-uitleg">Blauwe band = foutmarge van de AI (in ±80% van de gevallen valt het echte moment hierbinnen).</div>
              </div>

              <details className="lab-details">
                <summary>Wat de AI ziet (de invoer van het model)</summary>
                <dl>
                  <dt>Gewicht in de bak</dt><dd>{nl1.format(k.gewicht / 1000)} kg ({nl0.format(k.pct_vol)}%)</dd>
                  <dt>Verbruik laatste 30 min</dt><dd>{nl0.format(k.verbruik_30)} g/min</dd>
                  <dt>Verbruik laatste 2 uur</dt><dd>{nl0.format(k.verbruik_120)} g/min</dd>
                  <dt>Normaal verkocht komend uur</dt><dd>{nl1.format(k.verwacht_60 / 1000)} kg</dd>
                  <dt>Drukte vandaag t.o.v. normaal</dt><dd>{k.drukte != null ? `${nl1.format(k.drukte)}×` : '–'}</dd>
                  <dt>Volgens het patroon leeg over</dt><dd>{k.profiel_min != null ? `${nl0.format(k.profiel_min)} min` : '–'}</dd>
                  <dt>Minuten tot sluiting</dt><dd>{nl0.format(k.min_tot_sluiting)} min</dd>
                  <dt>Sinds laatste bijvulling</dt><dd>{nl0.format(k.min_sinds_bijvulling)} min</dd>
                </dl>
                <p className="lab-uitleg">In de live winkel komen deze waarden uit de metingen van de weegschaal; hier volgen ze uit jouw invoer.</p>
              </details>
            </>
          )}
          {data?.test?.MAE_model_bak_laag != null && (
            <p className="lab-voet">
              Getest op januari–juni 2023, nooit gezien tijdens het trainen: als de bak minder dan 25% vol is, zit de AI gemiddeld{' '}
              <strong>{nl0.format(data.test.MAE_model_bak_laag)} min</strong> naast, de simpele berekening {nl0.format(data.test.MAE_baseline_bak_laag)} min.
              {data.test.dekking_marge != null && <> Het echte moment viel in {nl0.format(100 * data.test.dekking_marge)}% van de gevallen binnen de marge.</>}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------- AI 2
function BestelLab({ api }) {
  const [lijst, setLijst] = useState(null);
  const [product, setProduct] = useState(102900005116714);
  const [datum, setDatum] = useState('2023-06-01');

  useEffect(() => {
    fetch(`${api}/api/lab/producten`).then((r) => r.json()).then(setLijst).catch(() => setLijst({ beschikbaar: false, producten: [] }));
  }, [api]);

  const url = lijst?.beschikbaar ? `${api}/api/lab/bestelling?product=${product}&datum=${datum}` : null;
  const { data, fout, bezig } = useVertraagd(url, 120);

  const verschuif = (dagen) => {
    const d = new Date(`${datum}T12:00:00`);
    d.setDate(d.getDate() + dagen);
    const s = d.toISOString().slice(0, 10);
    if (lijst && s >= lijst.van && s <= lijst.tot) setDatum(s);
  };
  const willekeurigeDag = (vanaf) => {
    const a = new Date(`${vanaf || lijst.van}T12:00:00`).getTime(), b = new Date(`${lijst.tot}T12:00:00`).getTime();
    setDatum(new Date(a + Math.random() * (b - a)).toISOString().slice(0, 10));
  };

  if (lijst && !lijst.beschikbaar) {
    return <section className="lab-kaart"><h2>🚚 Hoeveel moet de winkel bestellen?</h2><div className="lab-fout">Het bestelmodel is niet geladen in de backend.</div></section>;
  }
  const bakken = lijst?.producten.filter((p) => p.bak) ?? [];
  const andere = lijst?.producten.filter((p) => !p.bak) ?? [];
  const lang = data ? new Date(`${data.datum}T12:00:00`).toLocaleDateString('nl-BE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) : '';

  return (
    <section className="lab-kaart">
      <div className="lab-kaart-kop">
        <div>
          <h2>🚚 Hoeveel moet de winkel bestellen?</h2>
          <p>Kies een product en een dag tussen juli 2020 en juni 2023. De AI rekent zoals om 4u 's ochtends, met enkel de verkopen tot gisteren, en daarna zie je wat er die dag echt verkocht werd.</p>
        </div>
      </div>

      <div className="lab-bestel-invoer">
        <label className="lab-label">Product
          <select value={product} onChange={(e) => setProduct(+e.target.value)}>
            <optgroup label="De vier bakken">
              {bakken.map((p) => <option key={p.item_code} value={p.item_code}>Bak {p.bak} · {p.naam}</option>)}
            </optgroup>
            <optgroup label={`Andere producten van de winkel (${andere.length})`}>
              {andere.map((p) => <option key={p.item_code} value={p.item_code}>{p.naam} · {p.categorie}</option>)}
            </optgroup>
          </select>
        </label>
        <label className="lab-label">Dag
          <span className="lab-datum">
            <button type="button" className="lab-knop" onClick={() => verschuif(-1)} aria-label="Vorige dag">◀</button>
            <input type="date" value={datum} min={lijst?.van} max={lijst?.tot} onChange={(e) => e.target.value && setDatum(e.target.value)} />
            <button type="button" className="lab-knop" onClick={() => verschuif(1)} aria-label="Volgende dag">▶</button>
          </span>
        </label>
        <div className="lab-knoppen">
          <button type="button" className="lab-knop accent" disabled={!lijst} onClick={() => willekeurigeDag()}>🎲 Willekeurige dag</button>
          <button type="button" className="lab-knop" disabled={!lijst} onClick={() => willekeurigeDag(lijst.test_vanaf)}>🧪 Dag uit de testperiode</button>
        </div>
      </div>

      <div className={`lab-bestel-uitvoer${bezig ? ' bezig' : ''}`}>
        {fout && <div className="lab-fout">{fout}</div>}
        {!data && !fout && <div className="lab-leeg">AI rekent …</div>}
        {data && (
          <>
            <div className={`lab-badge ${data.gezien_tijdens_training ? 'train' : 'test'}`}>
              {data.gezien_tijdens_training
                ? '📚 Trainingsperiode: deze dag zat in de data waarmee de AI geleerd heeft'
                : '🧪 Testperiode: deze dag heeft de AI nooit gezien, dit is een eerlijke test'}
            </div>
            <div className="lab-tegels">
              <div className="lab-tegel"><span>Verwachte verkoop</span><strong>{nl1.format(data.verwacht_kg)} kg</strong><small>de helft van de dagen meer, de helft minder</small></div>
              <div className="lab-tegel"><span>Veilige hoeveelheid</span><strong>{nl1.format(data.veilig_kg)} kg</strong><small>volstaat op ±80% van de dagen</small></div>
              <div className="lab-tegel accent"><span>Bestellen</span><strong>{nl1.format(data.bestel_kg)} kg</strong><small>incl. {nl1.format(data.verlies_pct)}% verlies (bederf)</small></div>
              <div className="lab-tegel"><span>Kost</span><strong>{data.kost != null ? `¥${geld.format(data.kost)}` : '–'}</strong><small>{data.inkoopprijs_per_kg != null ? `¥${geld.format(data.inkoopprijs_per_kg)}/kg inkoop` : 'geen inkoopprijs'}</small></div>
            </div>

            <div className="lab-realiteit">
              <div>
                <span className="lab-dim">Echt verkocht op {lang}</span>
                <div className="lab-groot klein">{data.aangeboden ? `${nl1.format(data.echt_kg)} kg` : 'niet verkocht'}</div>
              </div>
              {data.aangeboden && (
                <div className={`lab-oordeel ${data.volstond ? 'goed' : 'tekort'}`}>
                  {data.volstond
                    ? <>✅ <strong>Genoeg besteld.</strong> Na verlies lag er {nl1.format(data.veilig_kg)} kg klaar; er bleef {nl1.format(data.veilig_kg - data.echt_kg)} kg over.</>
                    : <>⚠️ <strong>{nl1.format(data.echt_kg - data.veilig_kg)} kg te weinig.</strong> Dat hoort op ±20% van de dagen te gebeuren: de AI bestelt bewust voor 80% van de dagen, anders gooit de winkel te veel weg.</>}
                  <div className="lab-dim">
                    De verwachting zat {nl1.format(data.fout_kg)} kg naast
                    {data.fout_vorige_week_kg != null && <> · ‘zelfde dag vorige week’ ({nl1.format(data.vorige_week_kg)} kg) zat {nl1.format(data.fout_vorige_week_kg)} kg naast</>}.
                  </div>
                </div>
              )}
              {!data.aangeboden && <div className="lab-oordeel">Dit product lag die dag niet in de winkel (geen verkopen in de kassadata).</div>}
            </div>

            <div className="lab-grafiek">
              <div className="lab-grafiek-titel">De vier weken tot en met deze dag: echte verkoop naast de AI</div>
              <ResponsiveContainer width="100%" height={230}>
                <ComposedChart data={data.reeks} margin={{ top: 10, right: 12, bottom: 0, left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={14} />
                  <YAxis tickFormatter={(x) => `${x} kg`} fontSize={12} />
                  <Tooltip formatter={(x, naam) => [x == null ? 'niet verkocht' : `${nl1.format(x)} kg`, naam]}
                           labelFormatter={(l, p) => (p?.[0] ? `${p[0].payload.weekdag} ${l}` : l)} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="echt_kg" name="Echt verkocht" fill="#bdc3c7" radius={[3, 3, 0, 0]} isAnimationActive={false}>
                    {data.reeks.map((r, i) => <Cell key={r.datum} fill={i === data.reeks.length - 1 ? '#2c3e50' : '#bdc3c7'} />)}
                  </Bar>
                  <Line dataKey="verwacht_kg" name="AI: verwacht" stroke="#e67e22" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Line dataKey="bestel_kg" name="AI: bestellen" stroke="#2980b9" strokeWidth={2} strokeDasharray="5 3" dot={false} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {data.test?.MAE_model_kg != null && (
              <p className="lab-voet">
                Getest op januari–juni 2023 (57 producten, nooit gezien): gemiddeld <strong>{nl1.format(data.test.MAE_model_kg)} kg</strong> fout
                per product per dag, tegenover {nl1.format(data.test.MAE_vorige_week_kg)} kg voor ‘zelfde dag vorige week’. De bestelling
                volstond op {nl0.format(100 * data.test.genoeg_met_bestelling)}% van de dagen.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

export default function AiLab({ api }) {
  return (
    <div className="lab">
      <div className="lab-intro">
        <h2>🧪 AI-lab: probeer het zelf</h2>
        <p>
          Hier rekenen dezelfde twee AI-modellen als in de live winkel. Verander de vakjes en de schuifjes, en de voorspelling
          past zich meteen aan. Probeer maar eens of je de AI kan laten twijfelen.
        </p>
      </div>
      <LeegLab api={api} />
      <BestelLab api={api} />
    </div>
  );
}
