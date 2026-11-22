(() => {
  const DATA = window.METRO_OD_DATA;
  if (!DATA) return;

  const MODE_COLORS = {
    "METRO": "#d90429",
    "BUS": "#0d9488",
    "ZONA PAGA": "#d97706",
    "METROTREN": "#7c3aed"
  };

  const PLOT_BASE = {
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#ffffff",
    font: { family: "Inter, -apple-system, sans-serif", color: "#334155", size: 11 },
    legend: { orientation: "h", y: 1.14, x: 0, font: { size: 10.5 } },
    hoverlabel: { bgcolor: "#0f172a", bordercolor: "#0f172a", font: { color: "#ffffff", size: 11.5 } }
  };

  const PLOT_CFG = { displayModeBar: false, responsive: true };

  const state = {
    year: "2023",
    mode: "ALL",
    period: "ALL",
    purpose: "ALL",
    commune: "ALL",
    direction: "BOTH",
    topN: 35,
    showArcs: true,
    showHubs: true,
    showCommunes: true,
    showGtfs: true,
    showFuture: true,
    useGoogleMaps: false,
    gmapKey: window.METRO_GMAP_KEY || localStorage.getItem("METRO_OD_GMAP_KEY") || ""
  };

  const selCommune = document.getElementById("sel-commune");
  Object.keys(DATA.communes).sort().forEach(c => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    selCommune.appendChild(opt);
  });

  const selStation = document.getElementById("sel-station-profile");
  DATA.metro_2023_profiles.forEach((st, idx) => {
    const opt = document.createElement("option");
    opt.value = idx;
    opt.textContent = `${st.station} (${st.comuna}) · ${Math.round(st.total_daily).toLocaleString("es-CL")}/d`;
    selStation.appendChild(opt);
  });

  // Leaflet Light Map (Carto Positron)
  const leafletMap = L.map("leaflet-map", {
    center: [-33.455, -70.655],
    zoom: 11,
    preferCanvas: true
  });

  const positronTiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: "&copy; OpenStreetMap &copy; CARTO",
    subdomains: "abcd",
    maxZoom: 19
  }).addTo(leafletMap);

  const voyagerTiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    attribution: "&copy; OpenStreetMap &copy; CARTO"
  });

  const satTiles = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    attribution: "Tiles &copy; Esri"
  });

  L.control.layers({
    "Claro (Positron)": positronTiles,
    "Calles (Voyager)": voyagerTiles,
    "Satélite (Esri)": satTiles
  }, null, { position: "topright" }).addTo(leafletMap);

  const gtfsLayer = L.layerGroup().addTo(leafletMap);
  const futureLayer = L.layerGroup().addTo(leafletMap);
  const arcsLayer = L.layerGroup().addTo(leafletMap);
  const hubsLayer = L.layerGroup().addTo(leafletMap);
  const communesLayer = L.layerGroup().addTo(leafletMap);

  let gmap = null;
  let gmapOverlays = [];

  function clearGmapOverlays() {
    gmapOverlays.forEach(o => o.setMap(null));
    gmapOverlays = [];
  }

  function getCurvedPoints(lat1, lon1, lat2, lon2, segments = 24) {
    const pts = [];
    const dx = lon2 - lon1;
    const dy = lat2 - lat1;
    const offset = 0.14;
    const midLat = (lat1 + lat2) / 2 - dx * offset;
    const midLon = (lon1 + lon2) / 2 + dy * offset;
    for (let i = 0; i <= segments; i++) {
      const t = i / segments;
      const lat = (1 - t) * (1 - t) * lat1 + 2 * (1 - t) * t * midLat + t * t * lat2;
      const lon = (1 - t) * (1 - t) * lon1 + 2 * (1 - t) * t * midLon + t * t * lon2;
      pts.push([lat, lon]);
    }
    return pts;
  }

  function filterFlowsForYear(yr) {
    return DATA.od_flows.filter(f => {
      if (f.year !== yr) return false;
      if (state.mode !== "ALL" && f.mode !== state.mode) return false;
      if (state.period !== "ALL" && f.macro_period !== state.period) return false;
      if (state.purpose !== "ALL" && f.purpose !== state.purpose) return false;
      if (state.commune !== "ALL") {
        if (state.direction === "ORIGIN" && f.origin !== state.commune) return false;
        if (state.direction === "DEST" && f.dest !== state.commune) return false;
        if (state.direction === "BOTH" && f.origin !== state.commune && f.dest !== state.commune) return false;
      }
      return true;
    });
  }

  function aggregateCorridors(rawFlows) {
    const map = new Map();
    for (const f of rawFlows) {
      const key = `${f.origin}|${f.dest}|${f.mode}`;
      if (!map.has(key)) {
        map.set(key, {
          origin: f.origin, dest: f.dest, mode: f.mode,
          trips: 0, intermodal_trips: 0, weighted_min: 0, weighted_km: 0, weighted_stages: 0
        });
      }
      const r = map.get(key);
      r.trips += f.trips;
      r.intermodal_trips += f.intermodal_trips;
      r.weighted_min += f.trips * (f.avg_min || 0);
      r.weighted_km += f.trips * (f.avg_km || 0);
      r.weighted_stages += f.trips * (f.avg_stages || 1);
    }
    return Array.from(map.values())
      .map(r => ({
        ...r,
        avg_min: r.trips > 0 ? r.weighted_min / r.trips : 0,
        avg_km: r.trips > 0 ? r.weighted_km / r.trips : 0,
        avg_stages: r.trips > 0 ? r.weighted_stages / r.trips : 1,
        intermodal_pct: r.trips > 0 ? (r.intermodal_trips / r.trips) * 100 : 0
      }))
      .sort((a, b) => b.trips - a.trips);
  }

  function summarizeTotals(corridors) {
    let trips = 0, inter = 0, wMin = 0, wKm = 0, wStg = 0;
    const modeTotals = {};
    for (const c of corridors) {
      trips += c.trips;
      inter += c.intermodal_trips;
      wMin += c.trips * c.avg_min;
      wKm += c.trips * c.avg_km;
      wStg += c.trips * c.avg_stages;
      modeTotals[c.mode] = (modeTotals[c.mode] || 0) + c.trips;
    }
    let domMode = "—", domVal = 0;
    for (const [m, v] of Object.entries(modeTotals)) {
      if (v > domVal) { domVal = v; domMode = m; }
    }
    return {
      trips,
      intermodalPct: trips > 0 ? (inter / trips) * 100 : 0,
      avgMin: trips > 0 ? wMin / trips : 0,
      avgKm: trips > 0 ? wKm / trips : 0,
      avgStages: trips > 0 ? wStg / trips : 0,
      domMode,
      domPct: trips > 0 ? (domVal / trips) * 100 : 0
    };
  }

  function focusCommune(name) {
    state.commune = name;
    selCommune.value = name;
    if (name !== "ALL" && DATA.communes[name]) {
      const c = DATA.communes[name];
      leafletMap.setView([c.lat, c.lon], 12);
      if (gmap) { gmap.setCenter({ lat: c.lat, lng: c.lon }); gmap.setZoom(12); }
    } else {
      leafletMap.setView([-33.455, -70.655], 11);
      if (gmap) { gmap.setCenter({ lat: -33.455, lng: -70.655 }); gmap.setZoom(11); }
    }
    renderDashboard();
  }

  function renderDashboard() {
    const otherYear = state.year === "2023" ? "2019" : "2023";
    const corridors = aggregateCorridors(filterFlowsForYear(state.year));
    const prevCorridors = aggregateCorridors(filterFlowsForYear(otherYear));
    const currSum = summarizeTotals(corridors);
    const prevSum = summarizeTotals(prevCorridors);

    // 1. KPIs
    document.getElementById("kpi-trips").textContent = Math.round(currSum.trips).toLocaleString("es-CL");
    const tripDelta = prevSum.trips > 0 ? ((currSum.trips - prevSum.trips) / prevSum.trips) * 100 : 0;
    document.getElementById("kpi-trips-vs").innerHTML =
      `vs. ${otherYear}: <span class="delta ${tripDelta >= 0 ? "pos" : "neg"}">${tripDelta >= 0 ? "+" : ""}${tripDelta.toFixed(1)}%</span>`;
    document.getElementById("kpi-intermodal").textContent = `Intermodal: ${currSum.intermodalPct.toFixed(1)}%`;

    document.getElementById("kpi-time").textContent = `${currSum.avgMin.toFixed(1)} min`;
    document.getElementById("kpi-time-vs").textContent = `${otherYear}: ${prevSum.avgMin.toFixed(1)} min`;
    document.getElementById("kpi-stages").textContent = `Etapas: ${currSum.avgStages.toFixed(2)}`;

    document.getElementById("kpi-dist").textContent = `${currSum.avgKm.toFixed(2)} km`;
    document.getElementById("kpi-dist-vs").textContent = `${otherYear}: ${prevSum.avgKm.toFixed(2)} km`;
    const speedKmh = currSum.avgMin > 0 ? currSum.avgKm / (currSum.avgMin / 60) : 0;
    document.getElementById("kpi-speed").textContent = `Vel.: ${speedKmh.toFixed(1)} km/h`;

    const topC = corridors[0];
    document.getElementById("kpi-top-od").textContent = topC ? `${topC.origin} → ${topC.dest}` : "Sin datos";
    document.getElementById("kpi-dom-mode").textContent = `${currSum.domMode} (${currSum.domPct.toFixed(0)}%)`;

    const filteredHubs = DATA.stop_hubs
      .filter(h =>
        h.year === state.year &&
        (state.mode === "ALL" || h.mode === state.mode) &&
        (state.commune === "ALL" || h.comuna === state.commune)
      )
      .sort((a, b) => b.total - a.total);
    document.getElementById("kpi-hub-top").textContent = filteredHubs[0] ? filteredHubs[0].name : "—";

    // 2. Map Layers
    gtfsLayer.clearLayers();
    futureLayer.clearLayers();
    arcsLayer.clearLayers();
    hubsLayer.clearLayers();
    communesLayer.clearLayers();
    if (state.useGoogleMaps && gmap) clearGmapOverlays();

    if (state.showGtfs && DATA.metro_lines_gtfs) {
      DATA.metro_lines_gtfs.forEach(line => {
        if (!line.points || line.points.length < 2) return;
        L.polyline(line.points, { color: line.color, weight: 3, opacity: 0.78 })
          .bindTooltip(`<strong>Metro ${line.name}</strong> (GTFS)`, { sticky: true })
          .addTo(gtfsLayer);

        if (state.useGoogleMaps && gmap && window.google) {
          gmapOverlays.push(new google.maps.Polyline({
            path: line.points.map(p => ({ lat: p[0], lng: p[1] })),
            geodesic: true,
            strokeColor: line.color,
            strokeOpacity: 0.8,
            strokeWeight: 3.2,
            map: gmap
          }));
        }
      });
    }

    if (state.showFuture && DATA.future_lines) {
      const futurePalette = { L7: "#0284c7", L8: "#ea580c", L9: "#db2777" };
      DATA.future_lines.forEach(fl => {
        const key = fl.name.slice(0, 2);
        const col = futurePalette[key] || fl.color;
        const coords = fl.stations.map(s => [s[1], s[2]]);

        L.polyline(coords, { color: col, weight: 2.8, dashArray: "6, 6", opacity: 0.92 })
          .bindTooltip(`<strong>${fl.name}</strong>`, { sticky: true })
          .addTo(futureLayer);

        fl.stations.forEach(s => {
          L.circleMarker([s[1], s[2]], {
            radius: 3.2, fillColor: col, color: "#ffffff", weight: 1, fillOpacity: 0.95
          }).bindTooltip(`<strong>${s[0]}</strong><br/>${fl.name}`).addTo(futureLayer);

          if (state.useGoogleMaps && gmap && window.google) {
            gmapOverlays.push(new google.maps.Marker({
              position: { lat: s[1], lng: s[2] },
              map: gmap,
              title: `${s[0]} (${fl.name})`,
              icon: {
                path: google.maps.SymbolPath.CIRCLE,
                scale: 3.6, fillColor: col, fillOpacity: 0.95, strokeColor: "#ffffff", strokeWeight: 1
              }
            }));
          }
        });

        if (state.useGoogleMaps && gmap && window.google) {
          gmapOverlays.push(new google.maps.Polyline({
            path: coords.map(p => ({ lat: p[0], lng: p[1] })),
            strokeOpacity: 0,
            icons: [{
              icon: { path: "M 0,-1 0,1", strokeOpacity: 0.95, strokeWeight: 2.8, scale: 3, strokeColor: col },
              offset: "0",
              repeat: "13px"
            }],
            map: gmap
          }));
        }
      });
    }

    const topCorridors = corridors.slice(0, state.topN);
    const maxTrips = topCorridors.length > 0 ? topCorridors[0].trips : 1;

    if (state.showArcs) {
      topCorridors.forEach(c => {
        const orig = DATA.communes[c.origin];
        const dest = DATA.communes[c.dest];
        if (!orig || !dest || c.origin === c.dest) return;
        const color = MODE_COLORS[c.mode] || "#2563eb";
        const weight = Math.max(1.5, Math.min(7.5, 1.4 + (c.trips / maxTrips) * 6.0));
        const pts = getCurvedPoints(orig.lat, orig.lon, dest.lat, dest.lon);

        const poly = L.polyline(pts, { color, weight, opacity: 0.62 })
          .bindTooltip(
            `<strong>${c.origin} → ${c.dest}</strong> (${c.mode})<br/>` +
            `Viajes: ${Math.round(c.trips).toLocaleString("es-CL")} · Intermodal: ${c.intermodal_pct.toFixed(1)}%<br/>` +
            `Tiempo: ${c.avg_min.toFixed(1)} min · Ruta: ${c.avg_km.toFixed(1)} km`,
            { sticky: true }
          );
        poly.on("click", () => focusCommune(c.origin));
        poly.addTo(arcsLayer);

        if (state.useGoogleMaps && gmap && window.google) {
          gmapOverlays.push(new google.maps.Polyline({
            path: pts.map(p => ({ lat: p[0], lng: p[1] })),
            geodesic: false,
            strokeColor: color,
            strokeOpacity: 0.65,
            strokeWeight: weight,
            map: gmap
          }));
        }
      });
    }

    if (state.showHubs) {
      const maxHub = filteredHubs.length > 0 ? filteredHubs[0].total : 1;
      filteredHubs.slice(0, 100).forEach(h => {
        const color = MODE_COLORS[h.mode] || "#2563eb";
        const radius = Math.max(3.5, Math.min(11, 3.5 + Math.sqrt(h.total / maxHub) * 7.5));
        const m = L.circleMarker([h.lat, h.lon], {
          radius, fillColor: color, color: "#ffffff", weight: 1.2, fillOpacity: 0.85
        }).bindTooltip(
          `<strong>${h.name}</strong> (${h.mode})<br/>` +
          `Comuna: ${h.comuna} · Servicio: ${h.service}<br/>` +
          `Subidas exp.: ${Math.round(h.total).toLocaleString("es-CL")}`
        );
        m.on("click", () => focusCommune(h.comuna));
        m.addTo(hubsLayer);

        if (state.useGoogleMaps && gmap && window.google) {
          gmapOverlays.push(new google.maps.Marker({
            position: { lat: h.lat, lng: h.lon },
            map: gmap,
            title: `${h.name} (${h.mode}) · ${Math.round(h.total).toLocaleString("es-CL")} subidas`,
            icon: {
              path: google.maps.SymbolPath.CIRCLE,
              scale: radius, fillColor: color, fillOpacity: 0.88, strokeColor: "#ffffff", strokeWeight: 1.2
            }
          }));
        }
      });
    }

    if (state.showCommunes) {
      Object.values(DATA.communes).forEach(c => {
        const isSel = state.commune === c.name;
        const cm = L.circleMarker([c.lat, c.lon], {
          radius: isSel ? 7.5 : 4.5,
          fillColor: isSel ? "#2563eb" : "#0f172a",
          color: "#ffffff",
          weight: 1.8,
          fillOpacity: isSel ? 1 : 0.85
        }).bindTooltip(`<strong>Centroide: ${c.name}</strong><br/>Ancla de arcos OD (click para filtrar)`);
        cm.on("click", () => focusCommune(isSel ? "ALL" : c.name));
        cm.addTo(communesLayer);

        if (state.useGoogleMaps && gmap && window.google) {
          const gm = new google.maps.Marker({
            position: { lat: c.lat, lng: c.lon },
            map: gmap,
            title: `Centroide: ${c.name} (click para filtrar)`,
            zIndex: isSel ? 900 : 500,
            icon: {
              path: google.maps.SymbolPath.CIRCLE,
              scale: isSel ? 7.5 : 5,
              fillColor: isSel ? "#2563eb" : "#0f172a",
              fillOpacity: 0.92,
              strokeColor: "#ffffff",
              strokeWeight: 1.8
            }
          });
          gm.addListener("click", () => focusCommune(isSel ? "ALL" : c.name));
          gmapOverlays.push(gm);
        }
      });
    }

    // 3. OD Table
    const tbody = document.getElementById("od-tbody");
    tbody.innerHTML = "";
    document.getElementById("tbl-count").textContent = `${corridors.length} pares`;
    corridors.slice(0, 50).forEach(c => {
      const col = MODE_COLORS[c.mode] || "#2563eb";
      const tr = document.createElement("tr");
      tr.className = "clickable";
      tr.innerHTML = `
        <td><strong>${c.origin}</strong> → ${c.dest}</td>
        <td><span class="tag" style="background:${col}14; color:${col}; border-color:${col}33;">${c.mode}</span></td>
        <td class="num">${Math.round(c.trips).toLocaleString("es-CL")}</td>
        <td class="num">${c.intermodal_pct.toFixed(1)}%</td>
        <td class="num">${c.avg_min.toFixed(1)} min</td>
        <td class="num">${c.avg_km.toFixed(1)} km</td>
      `;
      tr.addEventListener("click", () => focusCommune(c.origin));
      tbody.appendChild(tr);
    });

    // 4. Dynamic Selection Summary
    const summaryEl = document.getElementById("selection-summary");
    const lines = [];
    if (topC) {
      lines.push(
        `<strong>Corredor principal (${state.year}):</strong> ${topC.origin} → ${topC.dest} (${topC.mode}) concentra <strong>${Math.round(topC.trips).toLocaleString("es-CL")}</strong> viajes expandidos con promedio de <strong>${topC.avg_min.toFixed(1)} min</strong> (${topC.avg_km.toFixed(1)} km).`
      );
    }
    const metroList = corridors.filter(c => c.mode === "METRO");
    const busList = corridors.filter(c => c.mode === "BUS");
    const mTrips = metroList.reduce((s, r) => s + r.trips, 0);
    const bTrips = busList.reduce((s, r) => s + r.trips, 0);
    if (mTrips > 0 && bTrips > 0) {
      const mMin = metroList.reduce((s, r) => s + r.trips * r.avg_min, 0) / mTrips;
      const bMin = busList.reduce((s, r) => s + r.trips * r.avg_min, 0) / bTrips;
      const diff = bMin - mMin;
      lines.push(
        `<strong>Diferencial modal de duración:</strong> Metro promedia <strong>${mMin.toFixed(1)} min</strong> frente a <strong>${bMin.toFixed(1)} min</strong> en Bus (${diff >= 0 ? "+" + diff.toFixed(1) + " min en Bus" : "+" + Math.abs(diff).toFixed(1) + " min en Metro"}).`
      );
    }
    if (state.commune !== "ALL") {
      const out = DATA.od_flows.filter(f => f.year === state.year && f.origin === state.commune && (state.period === "ALL" || f.macro_period === state.period)).reduce((s, f) => s + f.trips, 0);
      const inc = DATA.od_flows.filter(f => f.year === state.year && f.dest === state.commune && (state.period === "ALL" || f.macro_period === state.period)).reduce((s, f) => s + f.trips, 0);
      const ratio = inc > 0 ? out / inc : 1;
      const cls = ratio > 1.25 ? "comuna generadora" : ratio < 0.8 ? "comuna atractora" : "flujo balanceado";
      lines.push(
        `<strong>Balance de ${state.commune}:</strong> ${Math.round(out).toLocaleString("es-CL")} salidas vs. ${Math.round(inc).toLocaleString("es-CL")} llegadas (razón <strong>${ratio.toFixed(2)}×</strong>, ${cls}).`
      );
    } else {
      lines.push(
        `<strong>Comparativa interanual:</strong> Duración media de <strong>${prevSum.avgMin.toFixed(1)} min</strong> (${otherYear}) a <strong>${currSum.avgMin.toFixed(1)} min</strong> (${state.year}), con <strong>${currSum.intermodalPct.toFixed(1)}%</strong> de viajes intermodales.`
      );
    }
    summaryEl.innerHTML = lines.map(l => `<li>${l}</li>`).join("");

    renderHalfHourChart();
  }

  function renderHalfHourChart() {
    const modes = state.mode === "ALL" ? ["METRO", "BUS", "ZONA PAGA", "METROTREN"] : [state.mode];
    const traces = [];
    let maxVal = 0;
    modes.forEach(m => {
      const r23 = DATA.half_hour_curves.filter(r => r.year === "2023" && r.mode === m);
      const r19 = DATA.half_hour_curves.filter(r => r.year === "2019" && r.mode === m);
      r23.forEach(r => { if (r.expanded_trips > maxVal) maxVal = r.expanded_trips; });
      r19.forEach(r => { if (r.expanded_trips > maxVal) maxVal = r.expanded_trips; });
      traces.push({
        x: r23.map(r => r.mh), y: r23.map(r => r.expanded_trips),
        name: `${m} 2023`, type: "scatter", mode: "lines",
        line: { color: MODE_COLORS[m], width: 2.2 }
      });
      traces.push({
        x: r19.map(r => r.mh), y: r19.map(r => r.expanded_trips),
        name: `${m} 2019`, type: "scatter", mode: "lines",
        line: { color: MODE_COLORS[m], width: 1.4, dash: "dot" }, opacity: 0.65
      });
    });
    const yTop = maxVal > 0 ? Math.ceil(maxVal * 1.18) : 1000;
    Plotly.react("chart-halfhour", traces, {
      ...PLOT_BASE,
      legend: { orientation: "h", y: 1.32, x: 0, font: { size: 9.8 } },
      margin: { t: 56, r: 14, b: 36, l: 50 },
      xaxis: { tickangle: -45, nticks: 12, gridcolor: "#f1f5f9" },
      yaxis: { range: [0, yTop], gridcolor: "#eef2f6", zeroline: false }
    }, PLOT_CFG);
  }

  function renderStationProfileChart() {
    const st = DATA.metro_2023_profiles[parseInt(selStation.value, 10) || 0];
    if (!st || !st.half_hours) return;
    const entries23 = Array.isArray(st.half_hours)
      ? st.half_hours.map(h => [h.mh, h.val])
      : Object.entries(st.half_hours);
    const xs = entries23.map(([k]) => String(k).replace(/:00$/, ""));
    const ys23 = entries23.map(([, v]) => Number(v) || 0);
    const hh19 = st.half_hours_2019 || {};
    const ys19 = entries23.map(([k]) => Number(hh19[k]) || 0);
    const has19 = ys19.some(v => v > 0);
    const yTop = Math.max(...ys23, ...ys19, 10) * 1.18;

    const traces = [];
    if (has19) {
      traces.push({
        x: xs,
        y: ys19,
        name: "Abril 2019",
        type: "bar",
        marker: { color: "#cbd5e1" }
      });
    }
    traces.push({
      x: xs,
      y: ys23,
      name: "Abril 2023",
      type: "bar",
      marker: { color: "#d90429" }
    });

    Plotly.react("chart-station", traces, {
      ...PLOT_BASE,
      barmode: "group",
      bargap: 0.18,
      legend: { orientation: "h", y: 1.24, x: 0, font: { size: 10 } },
      margin: { t: 34, r: 12, b: 38, l: 48 },
      xaxis: { tickangle: -45, nticks: 14 },
      yaxis: { range: [0, yTop], gridcolor: "#eef2f6" }
    }, PLOT_CFG);
  }

  function renderAnalyticalTabs() {
    const fw = DATA.full_week;

    // Tab 2: Expansion — L7, L8, L9 Counterfactual
    if (fw.expansion_ml_scenarios) {
      const sc = fw.expansion_ml_scenarios;
      Plotly.react("chart-exp-l789", [
        {
          x: sc.map(r => r.name), y: sc.map(r => r.new_trips / 1000),
          name: "Nuevos Metro (Bus → Metro)", type: "bar", marker: { color: "#d90429" }
        },
        {
          x: sc.map(r => r.name), y: sc.map(r => r.upgraded / 1000),
          name: "Upgrade a Metro Directo", type: "bar", marker: { color: "#0d9488" },
          text: sc.map(r => `${(r.total / 1000).toFixed(1)}k (${r.hrs_saved.toLocaleString("es-CL")} h/d)`),
          textposition: "outside"
        }
      ], {
        ...PLOT_BASE,
        barmode: "stack",
        margin: { t: 34, r: 16, b: 44, l: 50 },
        yaxis: { title: "Miles de viajes / día", range: [0, 168], gridcolor: "#eef2f6" }
      }, PLOT_CFG);
    }

    if (fw.expansion_ml_communes) {
      const cm = fw.expansion_ml_communes;
      Plotly.react("chart-exp-communes-ml", [{
        y: cm.map(r => r.comuna),
        x: cm.map(r => r.gain_pp),
        type: "bar", orientation: "h",
        marker: { color: cm.map(r => r.dist_drop >= 1.0 ? "#2563eb" : "#0d9488") },
        text: cm.map(r => `+${r.gain_pp.toFixed(1)} pp (${r.benefited.toLocaleString("es-CL")}/d)`),
        textposition: "outside"
      }], {
        ...PLOT_BASE,
        margin: { t: 10, r: 108, b: 38, l: 105 },
        xaxis: { title: "Ganancia cuota modal Metro (pp)", range: [0, 21.5], gridcolor: "#eef2f6" },
        yaxis: { autorange: "reversed" }
      }, PLOT_CFG);
    }

    const cC = fw.expansion_communes;
    Plotly.react("chart-exp-metro", [{
      x: cC.map(r => r.comuna),
      y: cC.map(r => r.dm),
      type: "bar",
      marker: { color: cC.map(r => r.group === "L3/L6" ? "#d90429" : "#94a3b8") },
      text: cC.map(r => `${r.dm >= 0 ? "+" : ""}${r.dm.toFixed(1)}`),
      textposition: "outside"
    }], {
      ...PLOT_BASE,
      margin: { t: 24, r: 14, b: 82, l: 48 },
      xaxis: { tickangle: -38 },
      yaxis: { title: "Δ cuota Metro (pp)", range: [-4, 20], zeroline: true, zerolinecolor: "#cbd5e1", gridcolor: "#eef2f6" }
    }, PLOT_CFG);

    Plotly.react("chart-exp-time", [
      { x: cC.map(r => r.comuna), y: cC.map(r => r.min19), name: "2019 (min)", type: "bar", marker: { color: "#94a3b8" } },
      { x: cC.map(r => r.comuna), y: cC.map(r => r.min23), name: "2023 (min)", type: "bar", marker: { color: "#2563eb" } }
    ], {
      ...PLOT_BASE,
      barmode: "group",
      margin: { t: 30, r: 14, b: 82, l: 48 },
      xaxis: { tickangle: -38 },
      yaxis: { title: "Minutos promedio", gridcolor: "#eef2f6" }
    }, PLOT_CFG);

    // Tab 3: Intermodal PM
    const d1 = DATA.eda.h1_time_decomp;
    const comps = [
      { key: "entrada", name: "Acceso", color: "#94a3b8" },
      { key: "espera1", name: "Espera 1", color: "#f59e0b" },
      { key: "vehiculo1", name: "Vehículo 1", color: "#2563eb" },
      { key: "caminata", name: "Caminata trasb.", color: "#7c3aed" },
      { key: "espera2", name: "Espera trasb. (te1)", color: "#d90429" },
      { key: "vehiculo2", name: "Vehículo 2", color: "#0d9488" },
      { key: "egreso", name: "Egreso", color: "#cbd5e1" }
    ];
    Plotly.react("chart-inter-decomp", comps.map(c => ({
      y: d1.map(r => r.label), x: d1.map(r => r[c.key]),
      name: c.name, type: "bar", orientation: "h", marker: { color: c.color }
    })), {
      ...PLOT_BASE,
      barmode: "stack",
      margin: { t: 34, r: 18, b: 36, l: 135 },
      xaxis: { title: "Minutos promedio", gridcolor: "#eef2f6" },
      yaxis: { autorange: "reversed" }
    }, PLOT_CFG);

    Plotly.react("chart-inter-cards", [{
      y: fw.intermodal_cards.map(r => r.label),
      x: fw.intermodal_cards.map(r => r.pct),
      type: "bar", orientation: "h",
      marker: { color: fw.intermodal_cards.map((r, i) => i === 0 ? "#d90429" : i === 2 ? "#0d9488" : "#94a3b8") },
      text: fw.intermodal_cards.map(r => `${r.pct}% (${Math.round(r.cards / 1000)}k)`),
      textposition: "outside"
    }], {
      ...PLOT_BASE,
      margin: { t: 14, r: 76, b: 36, l: 165 },
      xaxis: { title: "% tarjetas-día Bus→Metro matutinas", range: [0, 42], gridcolor: "#eef2f6" },
      yaxis: { autorange: "reversed" }
    }, PLOT_CFG);

    const stA = fw.intermodal_stations;
    Plotly.react("chart-inter-stations", [
      { x: stA.map(r => r.station), y: stA.map(r => r.bm), name: "Mañana: Bus → Metro", type: "bar", marker: { color: "#2563eb" } },
      {
        x: stA.map(r => r.station), y: stA.map(r => r.mb), name: "Tarde: Metro → Bus validado",
        type: "bar", marker: { color: "#d90429" },
        text: stA.map(r => `${r.ret_pct}%`), textposition: "outside"
      }
    ], {
      ...PLOT_BASE,
      barmode: "group",
      margin: { t: 32, r: 16, b: 74, l: 54 },
      xaxis: { tickangle: -28 },
      yaxis: { title: "Viajes laborales / día", gridcolor: "#eef2f6" }
    }, PLOT_CFG);

    // Tab 4: Temporal Demand
    const bB = fw.temporal_blocks;
    Plotly.react("chart-temp-vol", [
      { x: bB.map(r => r.bloque), y: bB.map(r => r.t19 / 1000), name: "Abril 2019", type: "bar", marker: { color: "#94a3b8" } },
      {
        x: bB.map(r => r.bloque), y: bB.map(r => r.t23 / 1000), name: "Abril 2023", type: "bar",
        marker: { color: bB.map(r => r.var_pct < -30 ? "#d90429" : "#2563eb") },
        text: bB.map(r => `${r.var_pct.toFixed(1)}%`), textposition: "outside"
      }
    ], {
      ...PLOT_BASE,
      barmode: "group",
      margin: { t: 34, r: 16, b: 48, l: 52 },
      yaxis: { title: "Miles de viajes / día", range: [0, 1060], gridcolor: "#eef2f6" }
    }, PLOT_CFG);

    const dow = [
      { d: "Lun", v19: 2.254, v23: 1.723, pct: -23.6 },
      { d: "Mar", v19: 2.252, v23: 1.729, pct: -23.2 },
      { d: "Mié", v19: 2.240, v23: 1.733, pct: -22.7 },
      { d: "Jue", v19: 2.272, v23: 1.752, pct: -22.9 },
      { d: "Vie", v19: 2.309, v23: 1.705, pct: -26.1 },
      { d: "Sáb", v19: 1.159, v23: 0.925, pct: -20.2 },
      { d: "Dom", v19: 0.603, v23: 0.505, pct: -16.2 }
    ];
    Plotly.react("chart-temp-dow", [
      { x: dow.map(r => r.d), y: dow.map(r => r.v19), name: "Abril 2019 (M)", type: "bar", marker: { color: "#94a3b8" } },
      {
        x: dow.map(r => r.d), y: dow.map(r => r.v23), name: "Abril 2023 (M)", type: "bar",
        marker: { color: dow.map(r => r.d === "Vie" ? "#d90429" : "#2563eb") },
        text: dow.map(r => `${r.pct.toFixed(1)}%`), textposition: "outside"
      }
    ], {
      ...PLOT_BASE,
      barmode: "group",
      margin: { t: 34, r: 16, b: 40, l: 48 },
      yaxis: { title: "Millones de viajes Metro", range: [0, 2.65], gridcolor: "#eef2f6" }
    }, PLOT_CFG);

    Plotly.react("chart-temp-share", [{
      x: bB.map(r => r.bloque),
      y: bB.map(r => +(r.s23 - r.s19).toFixed(2)),
      type: "bar",
      marker: { color: bB.map(r => (r.s23 - r.s19) >= 0 ? "#0d9488" : "#d90429") },
      text: bB.map(r => { const d = r.s23 - r.s19; return `${d >= 0 ? "+" : ""}${d.toFixed(2)} pp`; }),
      textposition: "outside"
    }], {
      ...PLOT_BASE,
      margin: { t: 24, r: 16, b: 44, l: 50 },
      yaxis: { title: "Δ participación diaria (pp)", range: [-3.4, 3.2], zeroline: true, zerolinecolor: "#cbd5e1", gridcolor: "#eef2f6" }
    }, PLOT_CFG);

    // Tab 5: Dataset Coverage
    Plotly.react("chart-ds-coverage", [
      {
        x: fw.coverage.map(r => r.label), y: fw.coverage.map(r => r.daily_trips / 1e6),
        name: "Viajes diarios (M)", type: "bar", marker: { color: "#2563eb" },
        text: fw.coverage.map(r => (r.daily_trips / 1e6).toFixed(2) + "M"), textposition: "outside"
      },
      {
        x: fw.coverage.map(r => r.label), y: fw.coverage.map(r => r.sin_bajada_pct),
        name: "% sin bajada estimada", type: "scatter", mode: "lines+markers+text",
        line: { color: "#d97706", width: 2.2 }, marker: { size: 7 },
        text: fw.coverage.map(r => r.sin_bajada_pct.toFixed(1) + "%"), textposition: "top center", yaxis: "y2"
      }
    ], {
      ...PLOT_BASE,
      margin: { t: 34, r: 50, b: 46, l: 48 },
      yaxis: { title: "Millones / día", range: [0, 5.4], gridcolor: "#eef2f6" },
      yaxis2: { title: "% sin bajada", overlaying: "y", side: "right", range: [0, 50], showgrid: false }
    }, PLOT_CFG);
  }

  // Google Maps Silver Light Style
  function activateGoogleMaps(apiKey) {
    state.useGoogleMaps = true;
    document.getElementById("leaflet-map").style.display = "none";
    const canvas = document.getElementById("gmap-canvas");
    canvas.style.display = "block";

    window.initMetroGoogleMap = function () {
      gmap = new google.maps.Map(canvas, {
        center: { lat: -33.455, lng: -70.655 },
        zoom: 11,
        mapTypeControl: true,
        streetViewControl: false,
        styles: [
          { elementType: "geometry", stylers: [{ color: "#f5f5f5" }] },
          { elementType: "labels.icon", stylers: [{ visibility: "off" }] },
          { elementType: "labels.text.fill", stylers: [{ color: "#616161" }] },
          { elementType: "labels.text.stroke", stylers: [{ color: "#f5f5f5" }] },
          { featureType: "road", elementType: "geometry", stylers: [{ color: "#ffffff" }] },
          { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#dadada" }] },
          { featureType: "water", elementType: "geometry", stylers: [{ color: "#d6e4f0" }] },
          { featureType: "poi.park", elementType: "geometry", stylers: [{ color: "#e5efe5" }] }
        ]
      });
      new google.maps.TransitLayer().setMap(gmap);
      renderDashboard();
    };

    if (window.google && window.google.maps) {
      window.initMetroGoogleMap();
    } else {
      const s = document.createElement("script");
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&callback=initMetroGoogleMap`;
      s.async = true;
      document.head.appendChild(s);
    }
  }

  // Event Wiring
  document.getElementById("sel-year").addEventListener("change", e => { state.year = e.target.value; renderDashboard(); });
  document.getElementById("sel-period").addEventListener("change", e => { state.period = e.target.value; renderDashboard(); });
  document.getElementById("sel-purpose").addEventListener("change", e => { state.purpose = e.target.value; renderDashboard(); });
  selCommune.addEventListener("change", e => focusCommune(e.target.value));
  document.getElementById("sel-direction").addEventListener("change", e => { state.direction = e.target.value; renderDashboard(); });
  document.getElementById("rng-topn").addEventListener("input", e => {
    state.topN = parseInt(e.target.value, 10);
    document.getElementById("lbl-topn").textContent = state.topN;
    renderDashboard();
  });
  ["chk-arcs", "chk-hubs", "chk-communes", "chk-gtfs", "chk-future"].forEach(id => {
    const prop = {
      "chk-arcs": "showArcs", "chk-hubs": "showHubs", "chk-communes": "showCommunes",
      "chk-gtfs": "showGtfs", "chk-future": "showFuture"
    }[id];
    document.getElementById(id).addEventListener("change", e => { state[prop] = e.target.checked; renderDashboard(); });
  });
  selStation.addEventListener("change", renderStationProfileChart);

  document.querySelectorAll("#mode-pills .seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#mode-pills .seg-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.mode = btn.dataset.mode;
      renderDashboard();
    });
  });

  document.getElementById("btn-reset").addEventListener("click", () => {
    state.year = "2023"; state.mode = "ALL"; state.period = "ALL"; state.purpose = "ALL";
    state.commune = "ALL"; state.direction = "BOTH"; state.topN = 35;
    document.getElementById("sel-year").value = "2023";
    document.getElementById("sel-period").value = "ALL";
    document.getElementById("sel-purpose").value = "ALL";
    selCommune.value = "ALL";
    document.getElementById("sel-direction").value = "BOTH";
    document.getElementById("rng-topn").value = 35;
    document.getElementById("lbl-topn").textContent = "35";
    document.querySelectorAll("#mode-pills .seg-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === "ALL"));
    focusCommune("ALL");
  });

  // Tab Navigation
  document.querySelectorAll("#main-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#main-tabs .tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = document.getElementById(btn.dataset.tab);
      panel.classList.add("active");
      setTimeout(() => {
        if (btn.dataset.tab === "tab-map") {
          leafletMap.invalidateSize();
          Plotly.Plots.resize("chart-halfhour");
          Plotly.Plots.resize("chart-station");
        } else {
          panel.querySelectorAll(".chart, .chart-sm, .chart-lg").forEach(el => {
            if (el.id) Plotly.Plots.resize(el.id);
          });
        }
      }, 50);
    });
  });

  // Automatic graceful fallback if Google Maps rejects key/referrer
  window.gm_authFailure = function () {
    console.warn("Google Maps authentication failed — falling back to Leaflet Carto Positron.");
    state.useGoogleMaps = false;
    document.getElementById("gmap-canvas").style.display = "none";
    document.getElementById("leaflet-map").style.display = "block";
    leafletMap.invalidateSize();
    renderDashboard();
  };

  // Initial Render
  renderDashboard();
  renderStationProfileChart();
  renderAnalyticalTabs();
  if (state.gmapKey) {
    activateGoogleMaps(state.gmapKey);
  }
})();
