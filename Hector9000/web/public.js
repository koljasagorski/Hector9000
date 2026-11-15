/* Public statistics page: read-only, no login. Polls /api/stats every 10 s. */
(function () {
    "use strict";
    var h = H.h, REFRESH_MS = 10000;
    var last = null;

    function card(title, sub, kids) {
        return h("section", { class: "card" }, [
            h("h2", {}, [title, sub ? h("span", { class: "sub", text: sub }) : null])
        ].concat(kids));
    }

    function tiles(list) { return h("div", { class: "tiles" }, list); }

    function hourLabel(hr) { return hr + "–" + ((hr + 1) % 24) + " Uhr"; }

    function render(s) {
        var now = s.generated_at, t = s.totals, r = s.recent, rec = s.records, m = s.machine;
        var main = document.createDocumentFragment();
        var none = t.served === 0;

        main.appendChild(tiles([
            H.tile(H.num(t.served), "Drinks gemixt"),
            H.tile(H.num(t.liters, 1), "Liter ausgeschenkt", "alt"),
            H.tile(H.num(r.today), "Heute · gestern " + H.num(r.yesterday)),
            H.tile(H.num(r.last_hour), "In der letzten Stunde", "alt"),
            H.tile(H.num(s.timing.avg_s, 1), "Ø Zubereitung", "", "s"),
            H.tile(H.num(t.avg_ml), "Ø Drinkgröße", "alt", "ml"),
            H.tile(H.num(s.alcohol.share_pct, 0), "Mit Alkohol", "", "%"),
            H.tile(H.num(rec.active_days), "Aktive Tage", "alt")
        ]));

        if (none) {
            main.appendChild(card("Noch alles leer", "", [H.empty("Der erste Drink fehlt noch – dann geht es hier los.")]));
        } else {
            // ---------------------------------------------------- favourites
            var top = s.by_drink.slice(0, 12);
            main.appendChild(card("Beliebteste Drinks", t.different_drinks + " verschiedene", [
                H.hbars(top.map(function (d) {
                    return { label: d.name, value: d.count, text: d.count + " · " + H.num(d.share, 1) + " %", alc: d.alcohol,
                        title: d.name + ": " + d.count + " Stück, " + H.num(d.volume_ml / 1000, 2) + " l" + (d.avg_s ? ", Ø " + d.avg_s + " s" : "") };
                })),
                h("div", { class: "legend" }, [
                    h("span", {}, [h("i", { class: "sw" }), "alkoholfrei"]),
                    h("span", {}, [h("i", { class: "sw alc" }), "mit Alkohol"])
                ])
            ]));

            // ------------------------------------------------------ timelines
            var hourly = s.last_24h_hourly;
            main.appendChild(h("div", { class: "grid cols-2" }, [
                card("Letzte 24 Stunden", H.num(r.last_24h) + " Drinks", [
                    H.vbars(hourly.map(function (e) { return e.count; }),
                        hourly.map(function (e, i) { return i % 3 === 0 ? new Date(e.ts * 1000).getHours() + "h" : ""; }),
                        { aria: "Drinks pro Stunde" })
                ]),
                card("Letzte 60 Tage", H.num(r.last_7d) + " in 7 Tagen", [
                    H.vbars(s.by_day.map(function (e) { return e.count; }),
                        s.by_day.map(function (e, i) { return i % 10 === 0 ? H.isoDay(e.date) : ""; }),
                        { aria: "Drinks pro Tag" })
                ])
            ]));
            main.appendChild(card("Wochen", "die letzten 12", [
                H.vbars(s.by_week.map(function (e) { return e.count; }),
                    s.by_week.map(function (e, i) { return i % 2 === 0 ? H.isoDay(e.week) : ""; }), { aria: "Drinks pro Woche", height: 90 })
            ]));

            // --------------------------------------------------- when / heat
            main.appendChild(h("div", { class: "grid cols-2" }, [
                card("Uhrzeit", rec.busiest_hour_of_day !== null ? "Stoßzeit " + hourLabel(rec.busiest_hour_of_day) : "", [
                    H.vbars(s.by_hour_of_day, s.by_hour_of_day.map(function (_, i) { return i % 3 === 0 ? String(i) : ""; }),
                        { hi: rec.busiest_hour_of_day, aria: "Drinks nach Uhrzeit" })
                ]),
                card("Wochentag", rec.busiest_weekday !== null ? "am meisten: " + H.DAYS_LONG[rec.busiest_weekday] : "", [
                    H.vbars(s.by_weekday, H.DAYS, { hi: rec.busiest_weekday, aria: "Drinks nach Wochentag" })
                ])
            ]));
            main.appendChild(card("Wann ist was los?", "Wochentag × Uhrzeit", [H.heatmap(s.heatmap)]));

            // -------------------------------------------------- ingredients
            main.appendChild(card("Zutaten", H.num(t.volume_ml / 1000, 1) + " l insgesamt", [
                H.hbars(s.by_ingredient.slice(0, 14).map(function (i) {
                    return { label: i.name, value: i.ml, text: i.ml >= 1000 ? H.num(i.ml / 1000, 1) + " l" : i.ml + " ml",
                        alc: i.alcohol, title: i.name + ": " + i.uses + " Mal verwendet, " + H.num(i.share, 1) + " %" };
                })),
                h("div", { class: "legend" }, [
                    h("span", {}, [h("i", { class: "sw" }), "alkoholfrei"]),
                    h("span", {}, [h("i", { class: "sw alc" }), "Alkohol"])
                ])
            ]));
        }

        // ------------------------------------------------- speed & alcohol
        var tm = s.timing, al = s.alcohol;
        var slow = s.by_drink.filter(function (d) { return d.avg_s; }).sort(function (a, b) { return b.avg_s - a.avg_s; }).slice(0, 4);
        main.appendChild(h("div", { class: "grid cols-2" }, [
            card("Tempo", "pro Drink", [
                tiles([H.tile(H.num(tm.avg_s, 1), "Durchschnitt", "", "s"), H.tile(H.num(tm.median_s, 1), "Median", "alt", "s"),
                    H.tile(H.num(tm.p90_s, 1), "90 % schaffen es in", "", "s"), H.tile(H.num(tm.min_s, 1), "Schnellster", "alt", "s"),
                    H.tile(H.num(tm.max_s, 1), "Langsamster", "", "s"), H.tile(H.num(t.success_pct, 0), "Erfolgsquote", "alt", "%")]),
                slow.length ? h("p", { class: "note", text: "Dauert am längsten: " + slow.map(function (d) { return d.name + " (" + d.avg_s + " s)"; }).join(", ") }) : null
            ]),
            card("Alkohol", "geschätzt", [
                h("div", { class: "donut" }, [
                    H.donut(al.share_pct / 100, H.num(al.share_pct, 0) + "%"),
                    h("div", {}, [
                        h("div", {}, [h("b", { text: H.num(al.drinks) }), " mit Alkohol"]),
                        h("div", {}, [h("b", { text: H.num(t.non_alcoholic) }), " alkoholfrei"]),
                        h("div", {}, [h("b", { text: H.num(al.est_pure_ml) + " ml" }), " reiner Alkohol"]),
                        h("div", {}, [h("b", { text: H.num(al.est_units, 1) }), " Standardgläser (10 g)"]),
                        al.est_per_alcoholic_drink_ml ? h("div", {}, [h("b", { text: H.num(al.est_per_alcoholic_drink_ml, 1) + " ml" }), " pro Alkohol-Drink"]) : null
                    ])
                ]),
                h("p", { class: "note", text: "Schätzung mit üblichen Alkoholgehalten der Zutaten (z. B. Spirituosen 40 %). Bitte verantwortungsvoll trinken." })
            ])
        ]));

        // --------------------------------------------------------- records
        var sess = s.session, ses = s.sessions;
        var recs = [];
        if (rec.best_day) recs.push(H.tile(H.num(rec.best_day.count), "Bester Tag · " + H.isoDay(rec.best_day.date)));
        if (rec.best_hour) recs.push(H.tile(H.num(rec.best_hour.count), "Beste Stunde · " + H.date(rec.best_hour.ts) + " " + H.time(rec.best_hour.ts), "alt"));
        if (rec.most_popular) recs.push(H.tile(rec.most_popular.name, "Beliebtester · " + rec.most_popular.count + "×", "", ""));
        if (rec.rarest) recs.push(H.tile(rec.rarest.name, "Seltenster · " + rec.rarest.count + "×", "alt"));
        if (rec.biggest_drink) recs.push(H.tile(H.num(rec.biggest_drink.ml), "Größter Drink · " + rec.biggest_drink.name, "", "ml"));
        recs.push(H.tile(H.num(rec.streak_longest_days), "Längste Serie", "alt", "Tage"));
        recs.push(H.tile(H.num(rec.streak_current_days), "Aktuelle Serie", "", "Tage"));
        recs.push(H.tile(H.num(rec.avg_per_active_day, 1), "Ø pro aktivem Tag", "alt"));
        if (ses.biggest) recs.push(H.tile(H.num(ses.biggest.count), "Größte Runde · " + H.date(ses.biggest.start), "", "Drinks"));
        if (ses.longest) recs.push(H.tile(H.dur(ses.longest.end - ses.longest.start), "Längste Runde", "alt"));
        if (!none) {
            main.appendChild(card("Rekorde", sess ? "gerade läuft eine Runde: " + sess.count + " Drinks seit " + H.time(sess.start) : "", [tiles(recs)]));
        }

        // --------------------------------------------------------- machine
        var up = m.uptime_s, mach = [];
        mach.push(H.tile(H.dur(up), "Läuft seit"));
        mach.push(H.tile(H.withUnit(m.temp_avg_c, "°C", 1), "Ø Temperatur (24 h)", "alt"));
        mach.push(H.tile(H.withUnit(m.cpu_temp_c, "°C", 1), "Temperatur jetzt"));
        mach.push(H.tile(H.withUnit(m.temp_max_c, "°C", 1), "Maximum (24 h)", "alt"));
        mach.push(H.tile(H.num(m.load1, 2), "Last", ""));
        mach.push(H.tile(m.mem_pct === null || m.mem_pct === undefined ? "–" : H.pct(m.mem_pct), "Speicher belegt", "alt"));
        mach.push(H.tile(m.disk_pct === null || m.disk_pct === undefined ? "–" : H.pct(m.disk_pct), "Karte belegt"));
        mach.push(H.tile(H.num(m.boots), "Starts", "alt"));
        mach.push(H.tile(H.num(s.maintenance.clean), "Reinigungen"));
        mach.push(H.tile(H.num(s.maintenance.dry), "Trocknungen", "alt"));
        mach.push(H.tile(r.since_last_s === null ? "–" : H.dur(r.since_last_s), "Seit dem letzten Drink"));
        mach.push(H.tile(s.maintenance.last_clean_ts ? H.ago(s.maintenance.last_clean_ts, now) : "–", "Zuletzt gereinigt", "alt"));
        var errCls = s.errors.last_24h > 0 ? "sev-warn" : "";
        mach.push(H.tile(H.num(s.errors.last_24h), "Fehler (24 h)", errCls));
        mach.push(H.tile(H.num(s.errors.total), "Fehler insgesamt", "alt"));
        mach.push(H.tile(H.num(t.failed + t.aborted), "Fehlgeschlagene Drinks"));
        mach.push(H.tile(H.num(t.partial), "Unvollständig", "alt"));
        main.appendChild(card("Die Maschine", "Gesundheit & Wartung", [tiles(mach)]));

        // ----------------------------------------------------------- fun
        var f = s.fun;
        main.appendChild(card("Zahlenspielereien", "", [tiles([
            H.tile(H.num(f.bathtubs, 2), "Badewannen (150 l)"),
            H.tile(H.num(f.bottles_070), "Flaschen à 0,7 l", "alt"),
            H.tile(H.num(f.shots_2cl), "Schnapsgläser (2 cl)"),
            H.tile(H.num(f.coffee_cups), "Kaffeetassen", "alt"),
            H.tile(H.num(f.per_hour_avg, 1), "Drinks pro Stunde (Ø)"),
            H.tile(H.num(s.doom.plays), "Mal Doom gespielt", "alt"),
            H.tile(H.dur(s.doom.seconds), "Doom-Spielzeit")
        ])]));

        return main;
    }

    function status(s) {
        var live = H.$("live"), text = H.$("live-text");
        var dosing = s.live && s.live.state === "dosing";
        live.className = "live" + (dosing ? " busy" : "");
        text.textContent = dosing ? s.live.drink + " · " + s.live.progress + " %" : "Bereit";
    }

    function clockHint(s) {
        var drift = Date.now() / 1000 - s.clock.server_time;
        var box = H.clear(H.$("clock-hint"));
        if (Math.abs(drift) > 300) {
            box.appendChild(h("div", { class: "hint", text: "Die Uhr der Maschine weicht um " + H.dur(Math.abs(drift)) + " von deinem Gerät ab – " +
                "Uhrzeiten und Tageswerte stimmen dann möglicherweise nicht." }));
        }
    }

    function load() {
        H.getJSON("/api/stats").then(function (s) {
            last = s;
            var main = H.$("main");
            H.clear(main).appendChild(render(s));
            status(s);
            clockHint(s);
            H.$("foot").textContent = "Stand " + H.dateTime(s.generated_at) + " · aktualisiert sich alle " + REFRESH_MS / 1000 +
                " s · öffentliche Statistik ohne personenbezogene Daten";
        }).catch(function () {
            H.$("live-text").textContent = "keine Verbindung";
            if (!last) H.clear(H.$("main")).appendChild(H.empty("Keine Verbindung zur Maschine"));
        });
    }

    H.themeButton(H.$("theme-btn"));
    load();
    setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
})();
