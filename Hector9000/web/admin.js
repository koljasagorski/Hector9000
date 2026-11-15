/* Admin dashboard. The browser already holds the HTTP Basic login for this origin,
 * so fetch() calls are authenticated automatically. Dangerous actions need two taps. */
(function () {
    "use strict";
    var h = H.h, REFRESH_MS = 5000;
    var ov = null, tab = "overview";

    var TABS = [["overview", "Übersicht"], ["errors", "Fehler"], ["bottles", "Flaschen"],
        ["actions", "Aktionen"], ["chat", "Chat"], ["system", "System"], ["history", "Verlauf"]];
    var KINDS = { boot: "Start", clean: "Reinigung", dry: "Trocknung", valves_open: "Ventile auf", valves_close: "Ventile zu",
        reboot: "Neustart", kiosk_exit: "Kiosk verlassen", doom: "Doom", service: "Dienst", admin: "Admin" };
    var STATUS = { ok: "ok", partial: "unvollständig", failed: "fehlgeschlagen", aborted: "abgebrochen", running: "läuft" };

    function pill(text, cls) { return h("span", { class: "pill " + (cls || ""), text: text }); }
    function sev(value, warn, bad) { return value === null || value === undefined ? "alt" : value >= bad ? "sev-bad" : value >= warn ? "sev-warn" : ""; }
    function panelHead(text, sub) { return h("h2", {}, [text, sub ? h("span", { class: "sub", text: sub }) : null]); }

    // ------------------------------------------------------------- actions ---
    function run(action, args, done) {
        return H.postAction(action, args).then(function (r) {
            H.toast(r.ok ? "Erledigt" : (r.error || "Fehlgeschlagen"), !r.ok);
            if (r.ok && (action === "save_notify" || action === "reset_notify")) draft = null;   // show what the server now has
            load();
            if (done) done(r);
        }).catch(function () { H.toast("Keine Verbindung", true); });
    }

    // a button that needs a second tap within 4 s
    function dangerButton(label, action, args, cls) {
        var armed = false, timer;
        var btn = h("button", { class: "btn " + (cls || "danger"), type: "button", text: label });
        btn.addEventListener("click", function () {
            if (!armed) {
                armed = true;
                btn.textContent = "Sicher? Nochmal tippen";
                btn.classList.add("armed");
                timer = setTimeout(reset, 4000);
                return;
            }
            clearTimeout(timer);
            reset();
            run(action, args);
        });
        function reset() { armed = false; btn.textContent = label; btn.classList.remove("armed"); }
        return btn;
    }
    function actionButton(label, action, args, cls) {
        return h("button", { class: "btn " + (cls || ""), type: "button", text: label, onclick: function () { run(action, args); } });
    }

    // -------------------------------------------------------------- alerts ---
    function alerts() {
        var box = H.clear(H.$("alerts")), s = ov.stats, d = Date.now() / 1000 - ov.clock.server_time;
        var unacked = ov.errors.filter(function (e) { return !e.acked; }).length;
        if (Math.abs(d) > 120) {
            box.appendChild(h("div", { class: "hint" }, [
                "Die Uhr der Maschine geht " + H.dur(Math.abs(d)) + (d > 0 ? " nach" : " vor") + ". Statistik-Zeiten stimmen dann nicht. ",
                h("button", { class: "btn small", type: "button", text: "Uhr jetzt stellen",
                    onclick: function () { run("set_time", { epoch: Math.floor(Date.now() / 1000) }); } })
            ]));
        }
        if (unacked) box.appendChild(h("div", { class: "hint bad" }, [unacked + " unquittierte Fehler – ", h("a", { href: "#errors", text: "ansehen" })]));
        var missing = ov.processes ? Object.keys(ov.processes).filter(function (k) { return !ov.processes[k]; }) : [];
        if (missing.length) box.appendChild(h("div", { class: "hint bad", text: "Prozess läuft nicht: " + missing.join(", ") }));
        if (ov.health.throttle_reasons && ov.health.throttle_reasons.length)
            box.appendChild(h("div", { class: "hint", text: "Raspberry Pi meldet: " + ov.health.throttle_reasons.join(", ") }));
        if (ov.mqtt_exposed && ov.mqtt_exposed.length)
            box.appendChild(h("div", { class: "hint bad", text: "Der MQTT-Broker ist im Netzwerk erreichbar (Port " + ov.mqtt_exposed.join(", ") +
                "): jeder im WLAN kann den Hector steuern. Siehe Reiter „System“." }));
    }

    // ------------------------------------------------------------- overview ---
    function dosesTable(rows) {
        if (!rows.length) return H.empty("Noch keine Drinks");
        return h("div", { class: "scroll" }, h("table", { class: "t" }, [
            h("tr", {}, ["Zeit", "Drink", "Status", "ml", "Dauer"].map(function (t) { return h("th", { text: t }); }))
        ].concat(rows.map(function (r) {
            return h("tr", {}, [h("td", { text: H.dateTime(r.ts) }), h("td", { text: r.drink }),
                h("td", {}, pill(STATUS[r.status] || r.status, r.status)), h("td", { text: H.num(r.volume_ml) }),
                h("td", { text: r.duration_s ? H.num(r.duration_s, 1) + " s" : "–" })]);
        }))));
    }

    function overview() {
        var hl = ov.health, live = ov.live, main = document.createDocumentFragment();
        main.appendChild(h("div", { class: "tiles" }, [
            H.tile(live.state === "dosing" ? live.drink : "Bereit", live.state === "dosing" ? "Dosiert · " + live.progress + " %" : "Status", live.state === "dosing" ? "" : "alt"),
            H.tile(H.withUnit(hl.cpu_temp, "°C", 1), "CPU-Temperatur", sev(hl.cpu_temp, 75, 82)),
            H.tile(H.num(hl.load1, 2), "Last (1 min)", "alt"),
            H.tile(hl.mem_pct === null ? "–" : H.pct(hl.mem_pct), "Arbeitsspeicher", sev(hl.mem_pct, 85, 92)),
            H.tile(hl.disk_pct === null ? "–" : H.pct(hl.disk_pct), "Speicherkarte", sev(hl.disk_pct, 85, 92)),
            H.tile(H.dur(hl.uptime), "Läuft seit", "alt"),
            H.tile(H.num(ov.stats.recent.today), "Drinks heute"),
            H.tile(H.num(ov.stats.errors.last_24h), "Fehler (24 h)", ov.stats.errors.last_24h ? "sev-warn" : "alt")
        ]));
        var temp = ov.samples.map(function (s) { return { x: s.ts, y: s.cpu_temp }; });
        var load = ov.samples.map(function (s) { return { x: s.ts, y: s.load1 }; });
        main.appendChild(h("div", { class: "grid cols-2" }, [
            h("section", { class: "card" }, [panelHead("CPU-Temperatur", "letzte 6 Stunden"), H.line(temp, { unit: " °C", limit: 75 })]),
            h("section", { class: "card" }, [panelHead("Last", "letzte 6 Stunden"), H.line(load, { min: 0 })])
        ]));
        main.appendChild(h("section", { class: "card" }, [panelHead("Letzte Drinks"), dosesTable(ov.doses.slice(0, 8))]));
        var open = ov.errors.filter(function (e) { return !e.acked; }).slice(0, 5);
        if (open.length) main.appendChild(h("section", { class: "card" }, [panelHead("Offene Fehler"), errorsTable(open, false)]));
        return main;
    }

    // --------------------------------------------------------------- errors ---
    function errorsTable(rows, withAck) {
        if (!rows.length) return H.empty("Keine Fehler 🎉");
        return h("div", { class: "scroll" }, h("table", { class: "t" }, [
            h("tr", {}, ["Zeit", "Quelle", "Stufe", "Meldung"].map(function (t) { return h("th", { text: t }); }))
        ].concat(rows.map(function (e) {
            return h("tr", { class: e.acked ? "acked" : "" }, [h("td", { text: H.dateTime(e.ts) }), h("td", { text: e.source }),
                h("td", {}, pill(e.level, e.level)), h("td", { text: e.message })]);
        }))));
    }
    function errors() {
        var unacked = ov.errors.filter(function (e) { return !e.acked; }).length;
        return h("section", { class: "card" }, [
            panelHead("Fehler", ov.stats.errors.total + " insgesamt · " + unacked + " offen"),
            unacked ? h("p", {}, actionButton("Alle quittieren", "ack_errors", {}, "fill")) : null,
            errorsTable(ov.errors, true),
            h("p", { class: "note", text: "Fehler kommen vom Controller (Dosierung, Ausnahmen), von der Überwachung (Temperatur, Dienste) und – wenn eingerichtet – gehen sie zusätzlich in den Matrix-Chat." })
        ]);
    }

    // -------------------------------------------------------------- bottles ---
    function bottles() {
        var rows = ov.bottles.map(function (b) {
            var cap = h("input", { type: "number", min: 50, max: 5000, step: 50, value: b.capacity_ml, "aria-label": "Größe " + b.name });
            var low = b.remaining_pct !== null && b.remaining_pct <= 15;
            return h("tr", {}, [
                h("td", {}, [b.name, " ", b.alcohol ? pill("Alk", "muted") : null]),
                h("td", {}, b.remaining_pct === null
                    ? h("span", { class: "note", text: "unbekannt – nach dem Auffüllen „Aufgefüllt“ tippen" })
                    : h("div", { class: "field" }, [h("div", { class: "pbar" }, h("i", { class: low ? "low" : "", style: "width:" + b.remaining_pct + "%" })),
                        h("span", { text: H.num(b.remaining_ml) + " ml · " + H.num(b.remaining_pct, 0) + " %" })])),
                h("td", { text: H.num(b.used_ml) + " ml" }),
                h("td", { text: H.num(b.total_used_ml / 1000, 1) + " l" }),
                h("td", {}, h("div", { class: "field nowrap" }, [cap, h("span", { text: "ml" })])),
                h("td", {}, h("div", { class: "actions" }, [
                    h("button", { class: "btn small fill", type: "button", text: "Aufgefüllt", onclick: function () { run("refill", { code: b.code, capacity_ml: cap.value }); } }),
                    h("button", { class: "btn small", type: "button", text: "Größe speichern", onclick: function () { run("set_bottle", { code: b.code, capacity_ml: cap.value }); } })]))
            ]);
        });
        return h("section", { class: "card" }, [
            panelHead("Flaschen", "geschätzter Füllstand"),
            rows.length ? h("div", { class: "scroll" }, h("table", { class: "t" }, [
                h("tr", {}, ["Zutat", "Füllstand", "Seit Auffüllen", "Insgesamt", "Flaschengröße", ""].map(function (t) { return h("th", { text: t }); }))
            ].concat(rows))) : H.empty("Noch keine Zutaten bekannt"),
            h("p", { class: "note", text: "Hector hat keinen Füllstandssensor: der Wert ist Flaschengröße minus dosierte Menge seit „Aufgefüllt“. Ab 15 % kommt eine Warnung (Matrix, wenn eingerichtet)." })
        ]);
    }

    // -------------------------------------------------------------- actions ---
    function actions() {
        var drift = Date.now() / 1000 - ov.clock.server_time;
        var busy = ov.live.state === "dosing";
        return h("div", {}, [
            h("section", { class: "card" }, [panelHead("Maschine", busy ? "gerade wird ein Drink gemacht – Wartung gesperrt" : ""),
                h("div", { class: "actions" }, [
                    h("button", { class: "btn", type: "button", disabled: busy, text: "Reinigen", onclick: function () { run("clean"); } }),
                    h("button", { class: "btn", type: "button", disabled: busy, text: "Trocknen", onclick: function () { run("dry"); } }),
                    h("button", { class: "btn", type: "button", disabled: busy, text: "Alle Ventile öffnen", onclick: function () { run("open_valves"); } }),
                    actionButton("Alle Ventile schließen", "close_valves")])]),
            h("section", { class: "card" }, [panelHead("System"),
                h("div", { class: "actions" }, [
                    dangerButton("Kiosk beenden (Vollbild verlassen)", "kiosk_exit", {}),
                    dangerButton("Neustart", "reboot", {})]),
                h("p", { class: "note", text: "Nach dem Kiosk-Ende lässt sich die Oberfläche am Gerät mit kiosk.sh neu starten. Der Neustart dauert etwa eine Minute." })]),
            h("section", { class: "card" }, [panelHead("LED-Leiste"),
                h("div", { class: "actions" }, ["theme", "random", "idle", "off"].map(function (m) {
                    return actionButton({ theme: "Theme", random: "Zufall", idle: "Ruhe", off: "Aus" }[m], "led_mode", { mode: m });
                }))]),
            h("section", { class: "card" }, [panelHead("Uhr", "Maschine ↔ dieses Gerät"),
                h("p", { text: "Abweichung: " + (Math.abs(drift) < 2 ? "keine" : H.dur(Math.abs(drift)) + (drift > 0 ? " (Maschine geht nach)" : " (Maschine geht vor)")) +
                    " · NTP: " + (ov.clock.synced === null ? "unbekannt" : ov.clock.synced ? "synchron" : "nicht synchron") }),
                h("p", {}, h("button", { class: "btn", type: "button", text: "Uhr der Maschine auf dieses Gerät stellen",
                    onclick: function () { run("set_time", { epoch: Math.floor(Date.now() / 1000) }); } })),
                h("p", { class: "note", text: "Ohne Netz hat der Pi keine Uhr und startet mit der Zeit des letzten Herunterfahrens. Das verfälscht Tages- und Uhrzeitstatistiken." })]),
            h("section", { class: "card" }, [panelHead("Export", "CSV"),
                h("div", { class: "actions" }, [["doses", "Drinks"], ["ingredient_use", "Zutaten"], ["errors", "Fehler"], ["events", "Ereignisse"], ["samples", "Messwerte"]].map(function (t) {
                    return h("a", { class: "btn", href: "/api/admin/export?table=" + t[0], text: t[1] + " (CSV)" });
                }))])
        ]);
    }

    // ----------------------------------------------------------------- chat ---
    var LEVELS = [["info", "Alles (auch Hinweise)"], ["warning", "Warnungen und schlimmer"],
        ["error", "Nur Fehler"], ["critical", "Nur Kritisches"]];
    var MSGTYPES = [["m.text", "Normale Nachricht (meldet sich auf dem Handy)"], ["m.notice", "Bot-Hinweis (meist ohne Ton)"]];
    var draft = null;     // unsaved form values; they survive the 5 s refresh. The server never sends the token back.

    function chatDraft() {
        if (!draft) {
            var c = ov.notify_config;
            draft = { enabled: c.source === "dashboard" ? !!c.stored_enabled : true, homeserver: c.homeserver || "",
                room: c.room || "", token: "", level: c.level, msgtype: c.msgtype };
        }
        return draft;
    }
    function row(label, input, hint) {
        return h("div", { class: "formrow" }, [h("label", { class: "lbl", text: label }, null), input,
            hint ? h("span", { class: "note", text: hint }) : null]);
    }
    function textField(key, type, placeholder, label) {
        var d = chatDraft();
        var el = h("input", { type: type, class: "wide", value: d[key], placeholder: placeholder, "aria-label": label,
            autocomplete: type === "password" ? "new-password" : "off", spellcheck: "false", autocapitalize: "off" });
        el.addEventListener("input", function () { d[key] = el.value; });
        return el;
    }
    function selectField(key, options, label) {
        var d = chatDraft();
        var el = h("select", { class: "wide", "aria-label": label }, options.map(function (o) {
            return h("option", { value: o[0], selected: d[key] === o[0], text: o[1] });
        }));
        el.addEventListener("change", function () { d[key] = el.value; });
        return el;
    }
    function saveChat() {
        var d = chatDraft();
        run("save_notify", { enabled: d.enabled, homeserver: d.homeserver, room: d.room, token: d.token, level: d.level, msgtype: d.msgtype });
    }

    function chat() {
        var c = ov.notify_config, n = ov.notifier, d = chatDraft();
        var plain = location.protocol === "http:" && !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
        var source = { dashboard: "Einstellungen aus diesem Dashboard", env: "Einstellungen aus hector.env – Speichern übernimmt sie hierher",
            none: "noch nicht eingerichtet" }[c.source];
        var enabledBox = h("input", { type: "checkbox", id: "chat-enabled", checked: d.enabled });
        enabledBox.addEventListener("change", function () { d.enabled = enabledBox.checked; });
        var buttons = [
            h("button", { class: "btn fill", type: "button", text: "Speichern", onclick: saveChat }),
            h("button", { class: "btn", type: "button", disabled: !n.enabled, text: "Raum beitreten", onclick: function () { run("join_matrix"); } }),
            h("button", { class: "btn", type: "button", disabled: !n.enabled, text: "Testnachricht senden", onclick: function () { run("test_notification"); } })
        ];
        if (c.source === "dashboard") buttons.push(dangerButton("Gespeicherte Einstellungen löschen", "reset_notify", {}));
        return h("div", { class: "grid cols-2" }, [
            h("section", { class: "card" }, [panelHead("Matrix-Chat", n.enabled ? "aktiv" : "aus"),
                h("p", { class: "note", text: source }),
                !c.editable ? h("div", { class: "hint bad", text: "Hier kann nichts gespeichert werden (kein Zustandsordner)." }) : null,
                plain ? h("div", { class: "hint", text: "Diese Seite ist unverschlüsselt (HTTP): Der Token geht im Klartext durchs WLAN. " +
                    "Besser über einen SSH-Tunnel eingeben (ssh -L 8080:localhost:8080 …, dann http://localhost:8080/admin)." }) : null,
                h("div", { class: "formrow" }, h("label", { class: "check" }, [enabledBox, " Fehler in den Matrix-Chat senden"])),
                row("Homeserver", textField("homeserver", "text", "https://matrix.example.org", "Homeserver"), "Muss mit https:// beginnen."),
                row("Raum-ID", textField("room", "text", "!abc123:example.org", "Raum-ID"),
                    "Beginnt mit ! (nicht der Alias mit #). In Element: Raum-Einstellungen → Erweitert. Der Raum muss unverschlüsselt sein."),
                row("Zugangstoken des Bots", textField("token", "password", c.token_set ? "gespeichert (leer = behalten)" : "syt_…", "Zugangstoken"),
                    c.token_set ? "Der gespeicherte Token wird nie angezeigt und nur an den bisherigen Homeserver geschickt: ändert sich die Adresse, den Token neu eintragen." : "Wie man ihn holt: docs/matrix.md."),
                row("Welche Meldungen", selectField("level", LEVELS, "Mindest-Stufe")),
                row("Nachrichtentyp", selectField("msgtype", MSGTYPES, "Nachrichtentyp")),
                h("div", { class: "actions" }, buttons),
                h("details", { class: "help" }, [h("summary", { text: "So richtest du den Bot ein" }), h("ol", {}, [
                    h("li", { text: "Ein eigenes Bot-Konto auf einem Matrix-Server anlegen (nicht dein privates)." }),
                    h("li", { text: "Einen unverschlüsselten Raum anlegen und das Bot-Konto einladen." }),
                    h("li", { text: "Den Zugangstoken des Bots holen (Befehl in docs/matrix.md)." }),
                    h("li", { text: "Oben Homeserver, Raum-ID und Token eintragen und speichern." }),
                    h("li", { text: "„Raum beitreten“ drücken (nimmt die Einladung an), dann „Testnachricht senden“." })])])]),
            h("section", { class: "card" }, [panelHead("Status", n.backends.length ? n.backends.join(", ") : "kein Dienst"),
                h("dl", { class: "kv" }, [h("dt", { text: "Mindest-Stufe" }), h("dd", { text: n.min_level }),
                    h("dt", { text: "Gesendet" }), h("dd", { text: String(n.sent) }),
                    h("dt", { text: "In der Warteschlange" }), h("dd", { text: String(n.queued) }),
                    h("dt", { text: "Unterdrückt (Flut)" }), h("dd", { text: String(n.suppressed) }),
                    h("dt", { text: "Letzter Fehler" }), h("dd", { text: n.last_error || "–" }),
                    h("dt", { text: "Offene Alarme" }), h("dd", { text: n.active_alerts.join(", ") || "–" })]),
                h("p", { class: "note", text: "Meldungen warten bei fehlendem Netz bis zu 6 Stunden. Gleiche Meldungen kommen höchstens einmal pro Stunde. " +
                    "Fehler stehen immer im Reiter „Fehler“, auch wenn der Chat aus ist." })])
        ]);
    }

    // --------------------------------------------------------------- system ---
    function system() {
        var hl = ov.health, v = ov.versions;
        function ver(x) { return x ? x.rev + " (" + x.branch + ")" : "unbekannt"; }
        var procs = ov.processes ? Object.keys(ov.processes).map(function (k) { return h("span", {}, [pill(k, ov.processes[k] ? "" : "error"), " "]); }) : "nicht geprüft";
        var svc = Object.keys(ov.services).map(function (k) { return h("span", {}, [pill(k + ": " + ov.services[k].state, ov.services[k].state === "online" ? "" : "error"), " "]); });
        return h("div", { class: "grid cols-2" }, [
            h("section", { class: "card" }, [panelHead("Netzwerk & Versionen"), h("dl", { class: "kv" }, [
                h("dt", { text: "Adressen" }), h("dd", { text: ov.ips.join(", ") || "–" }),
                h("dt", { text: "Backend" }), h("dd", { text: ver(v.backend) }),
                h("dt", { text: "WebUI" }), h("dd", { text: ver(v.webui) }),
                h("dt", { text: "MQTT-Nachrichten" }), h("dd", { text: H.num(ov.messages) }),
                h("dt", { text: "Browser-Zeit" }), h("dd", { text: new Date().toLocaleString("de-DE") }),
                h("dt", { text: "Maschinen-Zeit" }), h("dd", { text: new Date(ov.clock.server_time * 1000).toLocaleString("de-DE") })])]),
            h("section", { class: "card" }, [panelHead("Prozesse & Dienste"), h("dl", { class: "kv" }, [
                h("dt", { text: "Prozesse" }), h("dd", {}, procs),
                h("dt", { text: "MQTT-Dienste" }), h("dd", {}, svc.length ? svc : "keine Meldung")])]),
            h("section", { class: "card" }, [panelHead("Gesundheit"), h("dl", { class: "kv" }, [
                h("dt", { text: "CPU-Temperatur" }), h("dd", { text: H.withUnit(hl.cpu_temp, "°C", 1) }),
                h("dt", { text: "Last" }), h("dd", { text: H.num(hl.load1, 2) }),
                h("dt", { text: "Speicher" }), h("dd", { text: hl.mem_pct === null ? "–" : H.pct(hl.mem_pct) }),
                h("dt", { text: "Karte" }), h("dd", { text: hl.disk_pct === null ? "–" : H.pct(hl.disk_pct) }),
                h("dt", { text: "Läuft seit" }), h("dd", { text: H.dur(hl.uptime) }),
                h("dt", { text: "Pi-Status" }), h("dd", { text: hl.throttled === null ? "kein Pi / unbekannt" : (hl.throttle_reasons.join(", ") || "alles in Ordnung") }),
                h("dt", { text: "MQTT-Broker" }), h("dd", {}, ov.mqtt_exposed === null || ov.mqtt_exposed === undefined ? "noch nicht geprüft"
                    : (ov.mqtt_exposed.length ? [pill("im Netzwerk erreichbar: Port " + ov.mqtt_exposed.join(", "), "error"),
                        h("div", { class: "hint", text: "Alles läuft auf diesem Gerät, der Broker braucht nur 127.0.0.1. Auf dem alten Pi sperrt hector9000-mqtt-guard die Ports (deploy/sd-update), im schlanken OS ist der Broker ohnehin nur lokal." })]
                        : pill("nur lokal", "")))])]),
            h("section", { class: "card" }, [panelHead("Hinweise"), h("p", { class: "note", text:
                "Öffentliche Statistik: /  ·  Admin: /admin (Benutzer „admin“, Passwort steht in der Datei admin_password im Zustandsordner, siehe docs/stats.md). " +
                "Die Verbindung ist unverschlüsselt (HTTP) – nur im vertrauenswürdigen Netz verwenden." })])
        ]);
    }

    // -------------------------------------------------------------- history ---
    function history() {
        var rows = ov.events.map(function (e) {
            return h("tr", {}, [h("td", { text: H.dateTime(e.ts) }), h("td", {}, pill(KINDS[e.kind] || e.kind, "muted")), h("td", { text: e.detail || "" })]);
        });
        return h("div", { class: "grid cols-2" }, [
            h("section", { class: "card" }, [panelHead("Ereignisse", "die letzten 100"),
                rows.length ? h("div", { class: "scroll" }, h("table", { class: "t" }, [h("tr", {}, ["Zeit", "Art", "Details"].map(function (t) { return h("th", { text: t }); }))].concat(rows))) : H.empty("Keine Ereignisse")]),
            h("section", { class: "card" }, [panelHead("Drinks", "die letzten 50"), dosesTable(ov.doses)])
        ]);
    }

    var VIEWS = { overview: overview, errors: errors, bottles: bottles, actions: actions, chat: chat, system: system, history: history };

    function tabs() {
        var nav = H.clear(H.$("tabs")), unacked = ov.errors.filter(function (e) { return !e.acked; }).length;
        TABS.forEach(function (t) {
            nav.appendChild(h("a", { class: "btn" + (tab === t[0] ? " on" : ""), href: "#" + t[0], role: "tab", "aria-selected": tab === t[0] ? "true" : "false" },
                [t[1], t[0] === "errors" && unacked ? h("span", { class: "badge", text: unacked }) : null]));
        });
    }

    function render() {
        if (!ov) return;
        var focused = document.activeElement;
        if (focused && /^(INPUT|SELECT|TEXTAREA)$/.test(focused.tagName) && H.$("main").contains(focused)) return;   // don't eat typing
        var main = H.clear(H.$("main"));
        main.appendChild(VIEWS[tab]());
        tabs();
        alerts();
        var dosing = ov.live.state === "dosing";
        H.$("live").className = "live" + (dosing ? " busy" : "");
        H.$("live-text").textContent = dosing ? ov.live.drink + " · " + ov.live.progress + " %" : "Bereit";
        H.$("foot").textContent = "Stand " + H.dateTime(ov.clock.server_time) + " · aktualisiert alle " + REFRESH_MS / 1000 + " s";
    }

    function load() {
        return H.getJSON("/api/admin/overview").then(function (data) { ov = data; render(); }).catch(function (e) {
            if (!ov) H.clear(H.$("main")).appendChild(H.empty("Keine Verbindung oder nicht angemeldet"));
            H.$("live-text").textContent = "keine Verbindung";
        });
    }

    function route() {
        var wanted = location.hash.replace("#", "");
        tab = VIEWS[wanted] ? wanted : "overview";
        render();
    }

    H.themeButton(H.$("theme-btn"));
    window.addEventListener("hashchange", route);
    route();
    load();
    setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
})();
