/* Shared helpers for the statistics and admin pages: themes, formatting, tiny DOM builder, charts.
 * No libraries on purpose: the Pi may be offline. Everything user-visible is set with
 * textContent, never innerHTML, so drink names from the database cannot inject markup. */
(function () {
    "use strict";
    var H = window.H = {};

    // ------------------------------------------------------------- themes ---
    H.THEMES = [["dark", "Dark"], ["light", "Light"], ["pink", "Zero Day Pink"], ["green", "Rabbit Green"],
        ["teal", "Teal"], ["autumn", "Autumn"], ["yellow", "Danger Yellow"], ["lilac", "Cute Lilac"]];
    var KEY = "hector9000.theme";

    function store(op, key, value) {          // localStorage can be blocked: never depend on it
        try { return op === "get" ? localStorage.getItem(key) : localStorage.setItem(key, value); } catch (e) { return null; }
    }
    H.theme = function () {
        var saved = store("get", KEY);
        return H.THEMES.some(function (t) { return t[0] === saved; }) ? saved : "dark";
    };
    H.applyTheme = function (id) { document.documentElement.setAttribute("data-theme", id); store("set", KEY, id); };
    H.nextTheme = function () {
        var i = H.THEMES.findIndex(function (t) { return t[0] === H.theme(); });
        var next = H.THEMES[(i + 1) % H.THEMES.length];
        H.applyTheme(next[0]);
        return next[1];
    };
    document.documentElement.setAttribute("data-theme", H.theme());

    // ---------------------------------------------------------------- dom ---
    H.$ = function (id) { return document.getElementById(id); };
    H.h = function (tag, attrs, kids) {
        var el = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            var v = attrs[k];
            if (v === null || v === undefined || v === false) return;
            if (k === "class") el.className = v;
            else if (k === "text") el.textContent = v;
            else if (k === "style") el.setAttribute("style", v);
            else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
            else el.setAttribute(k, v === true ? "" : v);
        });
        [].concat(kids === undefined ? [] : kids).forEach(function (kid) {
            if (kid === null || kid === undefined || kid === false) return;
            el.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
        });
        return el;
    };
    var NS = "http://www.w3.org/2000/svg";
    H.svg = function (tag, attrs, kids) {
        var el = document.createElementNS(NS, tag);
        Object.keys(attrs || {}).forEach(function (k) {
            if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
        });
        [].concat(kids === undefined ? [] : kids).forEach(function (kid) {
            if (kid !== null && kid !== undefined) el.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
        });
        return el;
    };
    H.clear = function (el) { while (el.firstChild) el.removeChild(el.firstChild); return el; };
    H.fill = function (id, kids) {
        var el = H.clear(H.$(id));
        [].concat(kids).forEach(function (k) { if (k) el.appendChild(k); });
    };

    // ---------------------------------------------------------- formatting ---
    var DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
    var DAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
    H.DAYS = DAYS; H.DAYS_LONG = DAYS_LONG;
    H.num = function (n, digits) {
        if (n === null || n === undefined || isNaN(n)) return "–";
        return Number(n).toLocaleString("de-DE", { maximumFractionDigits: digits === undefined ? 0 : digits });
    };
    H.dur = function (s) {
        if (s === null || s === undefined) return "–";
        s = Math.round(s);
        if (s < 60) return s + " s";
        if (s < 3600) return Math.floor(s / 60) + " min" + (s % 60 ? " " + (s % 60) + " s" : "");
        if (s < 86400) return Math.floor(s / 3600) + " h " + Math.floor((s % 3600) / 60) + " min";
        return Math.floor(s / 86400) + " d " + Math.floor((s % 86400) / 3600) + " h";
    };
    H.ago = function (ts, now) {
        if (!ts) return "–";
        var d = Math.max(0, now - ts);
        if (d < 45) return "gerade eben";
        if (d < 3600) return "vor " + Math.round(d / 60) + " min";
        if (d < 86400) return "vor " + Math.round(d / 3600) + " h";
        return "vor " + Math.round(d / 86400) + " d";
    };
    function two(n) { return (n < 10 ? "0" : "") + n; }
    H.time = function (ts) { if (!ts) return "–"; var d = new Date(ts * 1000); return two(d.getHours()) + ":" + two(d.getMinutes()); };
    H.date = function (ts) { var d = new Date(ts * 1000); return two(d.getDate()) + "." + two(d.getMonth() + 1) + "."; };
    H.dateTime = function (ts) { return ts ? H.date(ts) + " " + H.time(ts) : "–"; };
    H.isoDay = function (iso) { var p = iso.split("-"); return p[2] + "." + p[1] + "."; };
    H.pct = function (n) { return H.num(n, 1) + " %"; };

    // --------------------------------------------------------------- fetch ---
    H.getJSON = function (url) {
        return fetch(url, { cache: "no-store", credentials: "same-origin" }).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        });
    };
    H.postAction = function (action, args) {
        return fetch("/api/admin/action", {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Hector-Admin": "1" },
            body: JSON.stringify(Object.assign({ action: action }, args || {}))
        }).then(function (r) { return r.json().catch(function () { return { ok: false, error: "HTTP " + r.status }; }); });
    };
    var toastTimer;
    H.toast = function (text, bad) {
        var el = H.$("toast");
        if (!el) return;
        el.textContent = text;
        el.className = "toast show" + (bad ? " bad" : "");
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.className = "toast"; }, 2800);
    };

    // -------------------------------------------------------------- blocks ---
    H.withUnit = function (n, unit, digits) { return n === null || n === undefined ? "–" : H.num(n, digits) + " " + unit; };
    H.tile = function (value, label, cls, unit) {
        if (typeof value === "string" && value.length > 7) cls = (cls || "") + " txt";
        return H.h("div", { class: "tile " + (cls || "") }, [
            H.h("div", { class: "num" }, [value, unit ? H.h("small", { text: " " + unit }) : null]),
            H.h("div", { class: "lbl", text: label })
        ]);
    };
    H.empty = function (text) { return H.h("div", { class: "empty", text: text || "Noch keine Daten" }); };

    // horizontal ranking bars: rows = [{label, value, text, alc}]
    H.hbars = function (rows, max) {
        max = max || Math.max.apply(null, rows.map(function (r) { return r.value; }).concat([1]));
        var box = H.h("div");
        rows.forEach(function (r) {
            box.appendChild(H.h("div", { class: "hbar", title: r.title || null }, [
                H.h("span", { class: "lbl", text: r.label }),
                H.h("span", { class: "bar" }, H.h("i", { class: r.alc ? "alc" : "", style: "width:" + Math.max(1, 100 * r.value / max) + "%" })),
                H.h("span", { class: "val", text: r.text })
            ]));
        });
        return box;
    };

    // vertical bars: values[], labels[] (label shown if non-empty), opts {hi: index, fmt, height}
    H.vbars = function (values, labels, opts) {
        opts = opts || {};
        var n = values.length, max = Math.max.apply(null, values.concat([1]));
        var svg = H.svg("svg", { class: "chart vb", viewBox: "0 0 " + n + " 100", preserveAspectRatio: "none", role: "img",
            "aria-label": opts.aria || "Diagramm", style: "height:" + (opts.height || 130) + "px" });
        svg.appendChild(H.svg("line", { class: "axis", x1: 0, x2: n, y1: 100, y2: 100, "vector-effect": "non-scaling-stroke" }));
        values.forEach(function (v, i) {
            var hgt = v > 0 ? Math.max(1.5, 92 * v / max) : 0;
            svg.appendChild(H.svg("rect", { class: "b" + (opts.hi === i ? " hi" : ""), x: i + 0.12, y: 100 - hgt, width: 0.76, height: hgt },
                H.svg("title", {}, (labels[i] || i) + ": " + (opts.fmt ? opts.fmt(v) : H.num(v)))));
        });
        var row = H.h("div", { class: "vl", style: "grid-template-columns:repeat(" + n + ",1fr)" },
            labels.map(function (l) { return H.h("span", { text: l }); }));
        return H.h("div", { class: "vbox" }, [
            opts.showMax === false ? null : H.h("div", { class: "vmax", text: "max " + (opts.fmt ? opts.fmt(max) : H.num(max)) }), svg, row]);
    };

    // line chart: points [{x, y}], opts {min, max, unit, limit, height}
    H.line = function (points, opts) {
        opts = opts || {};
        var W = 600, Hh = 100;
        var ys = points.map(function (p) { return p.y; }).filter(function (y) { return y !== null && y !== undefined; });
        if (ys.length < 2) return H.empty("Zu wenig Messwerte");
        var lo = opts.min !== undefined ? opts.min : Math.min.apply(null, ys);
        var hi = opts.max !== undefined ? opts.max : Math.max.apply(null, ys);
        if (hi - lo < 1e-6) { hi = lo + 1; }
        var x0 = points[0].x, x1 = points[points.length - 1].x || x0 + 1;
        function X(x) { return (x - x0) / Math.max(x1 - x0, 1) * W; }
        function Y(y) { return Hh - 4 - (y - lo) / (hi - lo) * (Hh - 12); }
        var d = "", first = true;
        points.forEach(function (p) {
            if (p.y === null || p.y === undefined) { first = true; return; }
            d += (first ? "M" : "L") + X(p.x).toFixed(1) + " " + Y(p.y).toFixed(1) + " ";
            first = false;
        });
        var svg = H.svg("svg", { class: "chart vb", viewBox: "0 0 " + W + " " + Hh, preserveAspectRatio: "none", role: "img",
            "aria-label": opts.aria || "Verlauf", style: "height:" + (opts.height || 110) + "px" });
        svg.appendChild(H.svg("path", { class: "area", d: d + "L" + W + " " + Hh + " L0 " + Hh + " Z" }));
        svg.appendChild(H.svg("path", { class: "ln", d: d }));
        svg.appendChild(H.svg("line", { class: "axis", x1: 0, x2: W, y1: Hh, y2: Hh, "vector-effect": "non-scaling-stroke" }));
        if (opts.limit !== undefined && opts.limit >= lo && opts.limit <= hi) {
            svg.appendChild(H.svg("line", { class: "lim", x1: 0, x2: W, y1: Y(opts.limit), y2: Y(opts.limit), "vector-effect": "non-scaling-stroke" }));
        }
        var u = opts.unit || "";
        return H.h("div", { class: "vbox" }, [
            H.h("div", { class: "vmax", text: "min " + H.num(lo, 1) + u + " · max " + H.num(hi, 1) + u }), svg,
            H.h("div", { class: "vl two" }, [H.h("span", { text: opts.startLabel || H.time(x0) }), H.h("span", { text: opts.endLabel || H.time(x1) })])
        ]);
    };

    // donut: parts [{value, cls}] (first = accent, second = muted)
    H.donut = function (frac, center) {
        var r = 54, c = 2 * Math.PI * r;
        var svg = H.svg("svg", { viewBox: "0 0 150 150", role: "img", "aria-label": "Anteil" });
        svg.appendChild(H.svg("circle", { cx: 75, cy: 75, r: r, fill: "none", "stroke-width": 22, style: "stroke:var(--muted)" }));
        svg.appendChild(H.svg("circle", { cx: 75, cy: 75, r: r, fill: "none", "stroke-width": 22, style: "stroke:var(--accent)",
            "stroke-dasharray": (c * Math.max(0, Math.min(1, frac))).toFixed(1) + " " + c.toFixed(1), transform: "rotate(-90 75 75)" }));
        svg.appendChild(H.svg("text", { x: 75, y: 83, "text-anchor": "middle", style: "font-family:var(--font-head);font-size:30px;fill:var(--fg)" }, center));
        return svg;
    };

    // 7 x 24 heatmap, matrix[weekday][hour]
    H.heatmap = function (matrix) {
        var max = Math.max.apply(null, [].concat.apply([], matrix).concat([1]));
        var box = H.h("div", { class: "heat" });
        box.appendChild(H.h("div"));
        for (var hr = 0; hr < 24; hr++) box.appendChild(H.h("div", { class: "h", text: hr % 6 === 0 ? String(hr) : "" }));
        matrix.forEach(function (row, d) {
            box.appendChild(H.h("div", { class: "r", text: H.DAYS[d] }));
            row.forEach(function (v, hour) {
                box.appendChild(H.h("div", { class: "c", title: H.DAYS[d] + " " + hour + " Uhr: " + v },
                    v ? H.h("i", { style: "opacity:" + (0.2 + 0.8 * v / max).toFixed(2) }) : null));
            });
        });
        return box;
    };

    H.themeButton = function (btn) {
        btn.addEventListener("click", function () { H.toast("Farbschema: " + H.nextTheme()); });
    };
})();
