/* Grid programme guide for the HTML menu. ES5 only (stock webkitGl39 / Chromium 39).
   menu.html calls Guide.init(api) once, Guide.show(view) / Guide.hide() from render(),
   and Guide.key(command) for remote keys while the guide is on screen.
   Navigation is local to the page; the server is asked for one 2-hour window at a time
   (GET /api/epg?start=T), and OK on a programme posts /api/guide to open its options. */
(function () {
    'use strict';
    var GW = 968;                 // grid width in px (2 hours)
    var CELLS_LEFT = 184;         // cells start this far into #gRows (channel column + gap)
    var ROWS = 8, ROWH = 44;
    var MAX_WINDOWS = 12, STALE_MS = 180000;
    var AHEAD = 8 * 86400;        // same limit as guide.py
    var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    var api = null, shown = false, started = false;
    var noteCell = null, noteText = '', noteUntil = 0;   // "Recording scheduled" on a cell
    var windows = {}, windowOrder = [], loading = {};
    var chans = [], offset = 0, slot = 1800, span = 7200, skew = 0;
    var row = 0, top = 0, win = 0, anchor = 0;
    var picUrl = '';
    var badIcons = {};            // logo URLs that failed to load: show the number instead

    function $(id) { return document.getElementById(id); }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text) { e.textContent = text; }
        return e;
    }
    function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function nowT() { return Math.floor(Date.now() / 1000 + skew); }
    function slotFloor(t) { return Math.floor(t / slot) * slot; }
    function minWin() { return slotFloor(nowT()); }
    function local(t) { return new Date((t + offset) * 1000); }   // read with getUTC*
    function clock(t) {
        var d = local(t), h = d.getUTCHours();
        return (h % 12 || 12) + ':' + pad(d.getUTCMinutes()) + (h < 12 ? 'am' : 'pm');
    }
    function dayNo(t) { return Math.floor((t + offset) / 86400); }
    function dayLabel(t) {
        var diff = dayNo(t) - dayNo(nowT()), d = local(t);
        if (diff === 0) { return 'Today'; }
        if (diff === 1) { return 'Tomorrow'; }
        return DAYS[d.getUTCDay()] + ' ' + d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()];
    }

    // ---- Data ----
    function load(w, quiet) {
        if (loading[w]) { return; }
        loading[w] = true;
        api.request('GET', '/api/epg?start=' + w, null, function (data) {
            delete loading[w];
            if (!data || !data.channels) {
                if (!windows[w]) { windows[w] = { failed: true, at: Date.now() }; remember(w); }
                if (shown && !quiet) { draw(); }
                return;
            }
            skew = data.now - Date.now() / 1000;
            offset = data.offset || 0; slot = data.slot || slot; span = data.span || span;
            chans = data.channels;
            windows[data.start] = { events: data.events || {}, at: Date.now() };
            remember(data.start);
            if (data.start !== w) {                        // server clamped the window
                delete windows[w];
                if (win === w) { win = data.start; }
            }
            if (shown) { draw(); }
        });
    }
    function remember(w) {
        var i = windowOrder.indexOf(w);
        if (i >= 0) { windowOrder.splice(i, 1); }
        windowOrder.push(w);
        while (windowOrder.length > MAX_WINDOWS) { delete windows[windowOrder.shift()]; }
    }
    function ensure(w) {
        var d = windows[w];
        if (!d || d.failed && Date.now() - d.at > 10000) {
            if (d && d.failed) { delete windows[w]; }
            load(w);
        } else if (Date.now() - d.at > STALE_MS) {
            load(w, true);   // keep showing the old copy until the new one arrives
        }
    }

    // Contiguous cells for one channel across window w; gaps become "no information" cells.
    function cellsFor(uuid, w) {
        var d = windows[w], end = w + span, out = [], t = w, list, i, e;
        if (!d) { return [{ kind: 'loading', start: w, stop: end }]; }
        if (d.failed) { return [{ kind: 'failed', start: w, stop: end }]; }
        list = d.events[uuid] || [];
        for (i = 0; i < list.length; i++) {
            e = list[i];
            if (e.stop <= t) { continue; }
            if (e.start > t) { out.push({ kind: 'none', start: t, stop: e.start }); }
            out.push(e);
            t = e.stop;
            if (t >= end) { break; }
        }
        if (t < end) { out.push({ kind: 'none', start: t, stop: end }); }
        if (out.length && out[0].kind === 'none' && out[0].start === w && !list.length) {
            out[0].stop = end;
        }
        return out;
    }
    function cellAt(cells, t) {
        for (var i = 0; i < cells.length; i++) {
            if (cells[i].start <= t && t < cells[i].stop) { return i; }
        }
        return t < cells[0].start ? 0 : cells.length - 1;
    }
    function focused() {
        if (!chans.length) { return null; }
        var cells = cellsFor(chans[row].uuid, win);
        return cells[cellAt(cells, Math.max(anchor, win))];
    }

    // ---- Drawing ----
    function draw() {
        var w = win, end = win + span, px = GW / span, i, j, r, ch, cells, c, x, wd, node, logo, img;
        $('gDay').textContent = dayLabel(w);
        var times = $('gTimes');
        clear(times);
        for (i = 0; i < span / slot; i++) {
            node = el('div', '', clock(w + i * slot));
            node.style.left = Math.round(i * slot * px) + 'px';
            times.appendChild(node);
        }
        var rows = $('gRows');
        clear(rows);
        var sel = focused();
        for (r = 0; r < ROWS && top + r < chans.length; r++) {
            ch = chans[top + r];
            var line = el('div', 'gRow' + (top + r === row ? ' cur' : ''));
            line.style.top = (r * ROWH) + 'px';
            logo = el('div', 'gLogo');
            logo.appendChild(el('div', 'gNum', ch.number));
            logo.appendChild(el('div', 'gName', ch.name));
            if (ch.icon && !badIcons[ch.icon]) {
                img = el('img');
                img.onerror = (function (box, im, url) {
                    return function () {
                        badIcons[url] = true;          // don't flash a broken logo on every redraw
                        if (im.parentNode) { box.removeChild(im); }
                        box.className = 'gLogo';
                    };
                }(logo, img, ch.icon));
                img.src = ch.icon;
                logo.appendChild(img);
                logo.className = 'gLogo hasImg';
            }
            line.appendChild(logo);
            cells = cellsFor(ch.uuid, w);
            for (j = 0; j < cells.length; j++) {
                c = cells[j];
                var s = Math.max(c.start, w), e = Math.min(c.stop, end);
                x = Math.round((s - w) * px);
                wd = Math.round((e - w) * px) - x - 4;
                if (wd < 2) { continue; }
                var cls = ['gCell'];
                if (c.kind) { cls.push('none'); }
                if (!c.kind && c.start <= nowT() && nowT() < c.stop) { cls.push('now'); }
                if (c.start < w) { cls.push('cl'); }
                if (c.stop > end) { cls.push('cr'); }
                if (c.rec) { cls.push('rec'); }
                if (wd < 64) { cls.push('tiny'); }
                if (top + r === row && c === sel) { cls.push('sel'); }
                node = el('div', cls.join(' '));
                node.style.left = (CELLS_LEFT + x) + 'px';
                node.style.width = wd + 'px';
                node.appendChild(el('div', 'gArrL', '‹'));
                node.appendChild(el('div', 'gT', c.kind === 'loading' ? 'Loading…' :
                    c.kind === 'failed' ? 'Guide unavailable' : c.kind ? 'No information' : c.title));
                node.appendChild(el('div', 'gDot'));
                node.appendChild(el('div', 'gArrR', '›'));
                line.appendChild(node);
            }
            rows.appendChild(line);
        }
        // Current time marker and clock.
        var t = nowT();
        $('gClock').textContent = clock(t);
        if (t >= w && t < end) {
            x = 248 + Math.round((t - w) * px);
            $('gNow').style.left = x + 'px'; $('gNow').style.display = 'block';
            $('gNowTag').style.left = x + 'px'; $('gNowTag').style.display = 'block';
        } else {
            $('gNow').style.display = 'none'; $('gNowTag').style.display = 'none';
        }
        // Channel scroll position.
        var thumb = $('gThumb');
        if (chans.length > ROWS) {
            var h = Math.max(24, Math.round(348 * ROWS / chans.length));
            thumb.style.height = h + 'px';
            thumb.style.top = Math.round((348 - h) * top / (chans.length - ROWS)) + 'px';
            $('gScroll').style.visibility = 'visible';
        } else {
            $('gScroll').style.visibility = 'hidden';
        }
        details(sel, chans.length ? chans[row] : null);
    }

    function setPic(ch, ev) {
        var pic = $('gPic'), url = ev && ev.image || ch && ch.icon || '';
        $('gPicNum').textContent = ch ? ch.number : '';
        $('gPicName').textContent = ch ? ch.name : '';
        if (url === picUrl) { return; }
        picUrl = url;
        pic.className = '';
        pic.style.backgroundImage = 'none';
        if (!url) { return; }
        var probe = new Image();
        probe.onload = function () {
            if (picUrl !== url) { return; }
            pic.style.backgroundImage = 'url("' + url + '")';
            // Programme stills fill the box; channel logos sit centred at a modest size.
            pic.style.backgroundSize = ev && ev.image ? 'cover' : 'auto 50%';
            pic.className = 'hasImg';
        };
        probe.src = url;
    }

    function details(c, ch) {
        var badge = $('gBadge');
        setPic(ch, c && !c.kind ? c : null);
        badge.className = '';
        if (!ch) {
            $('gTitleText').textContent = 'Loading guide…';
            $('gEpisode').textContent = ''; $('gDesc').textContent = ''; clear($('gWhen'));
            return;
        }
        var when = $('gWhen');
        clear(when);
        if (!c || c.kind) {
            $('gTitleText').textContent = c && c.kind === 'failed' ? 'Guide unavailable' :
                c && c.kind === 'loading' ? 'Loading…' : 'No programme information';
            $('gEpisode').textContent = '';
            $('gDesc').textContent = c && c.kind === 'failed' ? 'Tvheadend did not answer. Move to try again.' : '';
            when.appendChild(document.createTextNode(ch.number + '  ' + ch.name));
            return;
        }
        var t = nowT(), mins = Math.round((c.stop - c.start) / 60);
        $('gTitleText').textContent = c.title;
        if (noteCell === c && new Date().getTime() < noteUntil) { badge.textContent = noteText; badge.className = 'on rec'; }
        else if (c.rec) { badge.textContent = 'RECORDING'; badge.className = 'on rec'; }
        else if (c.start <= t && t < c.stop) { badge.textContent = 'ON NOW'; badge.className = 'on'; }
        $('gEpisode').textContent = c.episode || '';
        $('gDesc').textContent = c.desc || '';
        when.appendChild(el('b', '', (dayNo(c.start) === dayNo(t) ? '' : dayLabel(c.start) + '  ') +
            clock(c.start) + ' – ' + clock(c.stop)));
        var extra = '   ' + mins + ' min';
        if (c.start <= t && t < c.stop) { extra += ', ' + Math.max(1, Math.round((c.stop - t) / 60)) + ' min left'; }
        when.appendChild(document.createTextNode(extra + '   ·   ' + ch.number + '  ' + ch.name));
    }

    // ---- Navigation ----
    function moveTo(newWin, t) {
        win = Math.max(minWin(), Math.min(minWin() + AHEAD, newWin));
        ensure(win);
        var c = focused();
        anchor = c ? Math.max(c.start, win) : Math.max(t, win);
        if (c && (c.kind === 'loading')) { anchor = Math.max(t, win); }
    }
    function horizontal(dir) {
        var c = focused(), end = win + span, t;
        if (!c) { return; }
        if (dir > 0) {
            t = c.stop;
            if (t < end) { anchor = t; moveTo(win, t); }
            else { anchor = t; moveTo(Math.max(win + slot, slotFloor(t) - slot), t); }
        } else {
            if (c.start > win) { t = c.start - 1; anchor = t; moveTo(win, t); }
            else if (win > minWin()) {
                t = Math.max(c.start - 1, minWin());
                anchor = t;
                moveTo(Math.min(win - slot, slotFloor(t)), t);
            }
        }
    }
    function vertical(dir) {
        var next = Math.max(0, Math.min(chans.length - 1, row + dir));
        if (next === row) { return; }
        row = next;
        if (row < top) { top = row; }
        if (row >= top + ROWS) { top = row - ROWS + 1; }
    }

    window.Guide = {
        init: function (handlers) { api = handlers; },
        visible: function () { return shown; },
        show: function () {
            var first = !started;
            shown = true;
            if (!/(^| )guide( |$)/.test(document.body.className)) {   // render() resets the class
                document.body.className = (document.body.className + ' guide').replace(/^ /, '');
            }
            if (first) { started = true; win = minWin(); anchor = nowT(); row = 0; top = 0; }
            if (win < minWin()) { win = minWin(); }
            ensure(win);
            draw();
        },
        hide: function () {
            if (!shown) { return; }
            shown = false;
            document.body.className = document.body.className.replace(/(^| )guide\b/, '');
        },
        key: function (cmd) {
            if (!shown) { return false; }
            if (cmd === 'BACK') { api.back(); return true; }
            if (cmd === 'RECORD') {
                // Record key: book the focused programme at once (the cell gets its badge).
                var rc = focused();
                if (rc && !rc.kind && chans.length && api.record) {
                    api.record(chans[row].uuid, rc.id, function (res) {
                        if (res && res.ok) { rc.rec = true; }
                        noteCell = rc; noteText = (res && res.message) || 'Could not schedule';
                        noteUntil = new Date().getTime() + 4000;
                        draw();
                        window.setTimeout(function () { if (shown) { draw(); } }, 4100);
                    });
                }
                return true;
            }
            if (cmd === 'OK') {
                var c = focused();
                if (c && !c.kind && chans.length) { api.select(chans[row].uuid, c.id); }
                return true;
            }
            if (cmd === 'UP') { vertical(-1); }
            else if (cmd === 'DOWN') { vertical(1); }
            else if (cmd === 'LEFT') { horizontal(-1); }
            else if (cmd === 'RIGHT') { horizontal(1); }
            else { return false; }
            draw();
            return true;
        },
        tick: function () { if (shown) { ensure(win); draw(); } }
    };
    window.setInterval(function () { window.Guide.tick(); }, 30000);
}());
