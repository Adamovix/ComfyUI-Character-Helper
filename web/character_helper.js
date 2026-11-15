import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Keep in sync with FIELDS in tools/field_rules.py: [id, label, default dice count]
const FIELDS = [
    ["character", "character", 1],
    ["subject", "subject", 2],
    ["hair", "hair", 3],
    ["eyes", "eyes", 1],
    ["body", "body", 2],
    ["outfit", "outfit", 2],
    ["top", "top", 3],
    ["bottom", "bottom", 2],
    ["legwear", "legs & feet", 2],
    ["accessories", "accessories", 3],
    ["expression", "expression", 2],
    ["pose", "pose", 2],
    ["view", "view", 1],
    ["background", "background", 2],
];
const FIELD_LABEL = Object.fromEntries(FIELDS.map(([id, label]) => [id, label]));
// rows that get a little extra space above them, to group the list visually
const GROUP_START = new Set(["hair", "outfit", "expression"]);
const RATINGS = [["sfw", "SFW"], ["any", "Any"], ["nsfw", "NSFW"]];

const ICON = {
    dice: '<svg viewBox="0 0 16 16"><rect x="2" y="2" width="12" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="5.5" cy="5.5" r="1.1"/><circle cx="10.5" cy="10.5" r="1.1"/><circle cx="8" cy="8" r="1.1"/></svg>',
    lock: '<svg viewBox="0 0 16 16"><rect x="3.5" y="7" width="9" height="6.5" rx="1.3"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    unlock: '<svg viewBox="0 0 16 16"><rect x="3.5" y="7" width="9" height="6.5" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 7V5a2.5 2.5 0 0 1 4.6-1.3" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    undo: '<svg viewBox="0 0 16 16"><path d="M6 3 2.5 6.5 6 10M3 6.5h6.5a3.5 3.5 0 0 1 0 7H7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    clear: '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    person: '<svg viewBox="0 0 16 16"><circle cx="8" cy="5" r="2.8"/><path d="M2.5 14a5.5 5.5 0 0 1 11 0z"/></svg>',
};

const link = document.createElement("link");
link.rel = "stylesheet";
link.href = new URL("./character_helper.css", import.meta.url).href;
document.head.appendChild(link);

function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
}

function shortCount(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return Math.round(n / 1e3) + "k";
    return String(n);
}

function splitTags(text) {
    return text.split(/[,\n]/).map((t) => t.trim()).filter(Boolean);
}

async function getJSON(path, body) {
    const opts = body ? { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } } : undefined;
    const r = await api.fetchApi("/character_helper/" + path, opts);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
}

// ------------------------------------------------------------------ dropdown
// One shared suggestion list, attached to <body> so the node can't clip it.
const dropdown = {
    el: null, items: [], index: -1, owner: null,
    ensure() {
        if (this.el) return;
        this.el = el("div", "chh-dropdown");
        this.el.addEventListener("mousedown", (e) => {
            e.preventDefault(); // keep focus in the input
            const item = e.target.closest(".chh-option");
            if (item && this.owner) this.owner.accept(this.items[+item.dataset.i]);
        });
        document.body.appendChild(this.el);
    },
    show(owner, input, items, title, preselect) {
        this.ensure();
        this.owner = owner; this.items = items; this.index = preselect && items.length ? 0 : -1;
        if (!items.length) return this.hide();
        this.el.replaceChildren();
        if (title) this.el.appendChild(el("div", "chh-dd-title", title));
        items.forEach((it, i) => {
            const row = el("div", "chh-option" + (i === this.index ? " chh-active" : ""));
            row.dataset.i = i;
            row.appendChild(el("span", "chh-o-tag")).textContent = it.tag;
            let hint = it.hint || "";
            if (it.alias) hint = "← " + it.alias;
            else if (it.kind === "character" && it.series) hint = it.series;
            else if (it.kind === "series") hint = "series";
            else if (!hint && it.field && it.field !== owner.id) hint = FIELD_LABEL[it.field] || it.field;
            if (hint) row.appendChild(el("span", "chh-o-hint")).textContent = hint;
            row.appendChild(el("span", "chh-o-count")).textContent = shortCount(Math.abs(it.count || 0));
            this.el.appendChild(row);
        });
        const r = input.getBoundingClientRect();
        const host = owner.panel.el.getBoundingClientRect();
        this.el.style.display = "block";
        const width = Math.max(260, Math.min(420, host.width));
        this.el.style.width = width + "px";
        this.el.style.left = Math.max(4, Math.min(r.left, window.innerWidth - width - 8)) + "px";
        const below = window.innerHeight - r.bottom;
        const h = this.el.offsetHeight;
        this.el.style.top = (below < h + 8 && r.top > h + 8 ? r.top - h - 4 : r.bottom + 4) + "px";
    },
    hide() {
        if (this.el) this.el.style.display = "none";
        this.items = []; this.index = -1;
    },
    get open() { return !!this.el && this.el.style.display === "block" && this.items.length > 0; },
    move(d) {
        if (!this.open) return;
        const rows = this.el.querySelectorAll(".chh-option");
        rows[this.index]?.classList.remove("chh-active");
        this.index = (this.index + d + rows.length + (this.index < 0 && d < 0 ? 1 : 0)) % rows.length;
        rows[this.index].classList.add("chh-active");
        rows[this.index].scrollIntoView({ block: "nearest" });
    },
    current() { return this.index >= 0 ? this.items[this.index] : null; },
};

// ----------------------------------------------------------------------- row
class Row {
    constructor(panel, id, label, count) {
        this.panel = panel; this.id = id; this.seq = 0;
        this.el = el("div", "chh-row" + (GROUP_START.has(id) ? " chh-group" : ""));
        this.el.dataset.field = id;
        const lab = el("label", "chh-label");
        lab.textContent = label;
        this.box = el("div", "chh-box");
        this.input = el("input", "chh-input");
        this.input.type = "text";
        this.input.spellcheck = false;
        this.input.autocomplete = "off";
        this.input.setAttribute("aria-label", label);
        this.box.appendChild(this.input);
        this.count = el("input", "chh-count");
        this.count.type = "number"; this.count.min = 0; this.count.max = 9; this.count.value = count;
        this.count.title = "How many tags the dice adds" + (id === "character" ? " (0 = no named character)" : "");
        this.dice = el("button", "chh-btn", ICON.dice);
        this.dice.title = id === "character" ? "Random character (fills in their usual look)" : `Random ${label} that fits the other fields`;
        this.lock = el("button", "chh-btn chh-lock", ICON.unlock);
        this.lock.title = "Lock: keep this field when randomizing, filling or clearing everything";
        this.el.append(lab, this.box, this.count, this.dice, this.lock);

        lab.addEventListener("click", () => this.input.focus());
        this.box.addEventListener("mousedown", (e) => {
            if (e.target === this.box) { e.preventDefault(); this.input.focus(); }
        });
        this.box.addEventListener("click", (e) => this.onChipClick(e));
        this.input.addEventListener("input", () => this.query());
        this.input.addEventListener("focus", () => this.query());
        this.input.addEventListener("click", () => { if (!dropdown.open) this.query(); });
        this.input.addEventListener("blur", () => this.onBlur());
        this.input.addEventListener("keydown", (e) => this.onKey(e));
        this.input.addEventListener("paste", (e) => this.onPaste(e));
        this.count.addEventListener("change", () => {
            const n = Math.max(0, Math.min(9, parseInt(this.count.value) || 0));
            this.count.value = n;
            panel.state.counts[id] = n;
            panel.changed(false);
        });
        this.dice.addEventListener("click", () => panel.randomize([id]));
        this.lock.addEventListener("click", () => panel.setLock(id, !panel.state.locks[id]));
        for (const node of [this.input, this.count]) {
            for (const type of ["keydown", "keyup", "keypress"]) node.addEventListener(type, (e) => e.stopPropagation());
        }
    }

    get tags() { return this.panel.state.fields[this.id]; }

    render() {
        this.box.querySelectorAll(".chh-chip").forEach((c) => c.remove());
        this.tags.forEach((tag, i) => {
            const chip = el("span", "chh-chip");
            chip.dataset.i = i;
            chip.title = "Click to edit";
            chip.appendChild(el("span", "chh-chip-text")).textContent = tag;
            const x = el("button", "chh-x", "×");
            x.title = "Remove";
            x.tabIndex = -1;
            chip.appendChild(x);
            this.box.insertBefore(chip, this.input);
        });
        this.input.placeholder = this.id === "character" && !this.tags.length ? "type a name…" : "";
        this.box.classList.toggle("chh-empty", !this.tags.length);
        const locked = !!this.panel.state.locks[this.id];
        this.lock.innerHTML = locked ? ICON.lock : ICON.unlock;
        this.lock.classList.toggle("chh-on", locked);
        this.el.classList.toggle("chh-locked", locked);
        this.count.value = this.panel.state.counts[this.id];
    }

    onChipClick(e) {
        const chip = e.target.closest(".chh-chip");
        if (!chip) return;
        const i = +chip.dataset.i;
        const [tag] = this.tags.slice(i, i + 1);
        this.panel.snapshot();
        this.commitInput(false);
        this.tags.splice(this.tags.indexOf(tag), 1);
        if (!e.target.closest(".chh-x")) {   // clicked the text: put it back into the input to edit
            this.input.value = tag;
            this.panel.changed();
            this.input.focus();
            this.input.select();
            return;
        }
        this.panel.changed();
    }

    add(tags) {
        let added = false;
        for (const t of tags) {
            if (!this.tags.some((x) => x.toLowerCase() === t.toLowerCase())) { this.tags.push(t); added = true; }
        }
        return added;
    }

    commitInput(notify = true) {
        const typed = splitTags(this.input.value);
        this.input.value = "";
        if (!typed.length) return false;
        if (notify) this.panel.snapshot();
        this.add(typed);
        if (notify) this.panel.changed();
        return true;
    }

    accept(item) {
        this.input.value = "";
        this.panel.snapshot();
        if (item.kind === "character") {
            this.panel.state.fields.character = [item.tag].concat(item.series ? [item.series] : []);
            this.panel.state.locks.character = true;   // a hand-picked character survives "Randomize"
            this.panel.changed();
            dropdown.hide();
            this.panel.applyCharacter(item.tag, false);
            return;
        }
        this.add([item.tag]);
        this.panel.changed();
        this.query();   // keep offering what goes with the new tag
    }

    onKey(e) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            if (!dropdown.open) this.query();
            else dropdown.move(e.key === "ArrowDown" ? 1 : -1);
            e.preventDefault();
        } else if (e.key === "Enter" || e.key === "Tab") {
            // takes the highlighted suggestion; with the list closed (Escape) the text is kept as typed
            const cur = dropdown.open && dropdown.owner === this ? dropdown.current() : null;
            if (cur) { this.accept(cur); e.preventDefault(); }
            else if (this.input.value.trim()) { this.commitInput(); dropdown.hide(); if (e.key === "Enter") e.preventDefault(); }
            else if (e.key === "Enter") e.preventDefault();
        } else if (e.key === ",") {
            if (this.input.value.trim()) this.commitInput();
            dropdown.hide();
            e.preventDefault();
        } else if (e.key === "Backspace" && !this.input.value && this.tags.length) {
            this.panel.snapshot();
            this.tags.pop();
            this.panel.changed();
            e.preventDefault();
        } else if (e.key === "Escape") {
            if (dropdown.open) dropdown.hide(); else this.input.blur();
        }
    }

    onPaste(e) {
        const text = e.clipboardData?.getData("text") || "";
        if (!/[,\n]/.test(text)) return;
        e.preventDefault();
        this.panel.snapshot();
        this.add(splitTags(text));
        this.panel.changed();
    }

    onBlur() {
        this.seq++;
        this.commitInput();
        if (dropdown.owner === this) dropdown.hide();
    }

    async query() {
        const seq = ++this.seq;
        const q = this.input.value.trim();
        try {
            let items, title;
            if (q) {
                items = await getJSON(`autocomplete?field=${this.id}&limit=12&q=${encodeURIComponent(q)}`);
            } else {
                items = await getJSON("suggest", { field: this.id, context: this.panel.state.fields, rating: this.panel.state.rating });
                title = this.id === "character" ? "popular characters" : "goes well with the rest";
            }
            if (seq !== this.seq || document.activeElement !== this.input) return;
            dropdown.show(this, this.input, items, title, !!q);
        } catch (err) {
            console.warn("[Character Helper]", err);
        }
    }
}

// --------------------------------------------------------------------- panel
class Panel {
    constructor(node) {
        this.node = node;
        this.undoStack = [];
        this.state = Panel.blank();
        this.el = el("div", "chh-panel");
        this.inner = this.el.appendChild(el("div", "chh-inner"));

        const bar = this.inner.appendChild(el("div", "chh-bar"));
        const button = (cls, html, title, fn) => {
            const b = el("button", "chh-tool " + cls, html);
            b.title = title;
            b.addEventListener("click", fn);
            bar.appendChild(b);
            return b;
        };
        button("chh-primary", ICON.dice + "<span>Randomize</span>", "Roll every unlocked field", () => this.randomize(null));
        button("", ICON.person + "<span>Usual look</span>", "Fill the unlocked fields with the character's most common tags", () => this.applyCharacter(null, true));
        bar.appendChild(el("span", "chh-spacer"));
        this.rating = el("select", "chh-rating");
        this.rating.title = "Which pictures the dice and suggestions draw from";
        for (const [value, label] of RATINGS) this.rating.appendChild(new Option(label, value));
        this.rating.addEventListener("change", () => { this.state.rating = this.rating.value; this.changed(false); });
        bar.appendChild(this.rating);
        this.undoBtn = button("chh-icon", ICON.undo, "Undo the last change", () => this.undo());
        button("chh-icon", ICON.clear, "Clear every unlocked field", () => this.clear());

        this.rows = {};
        for (const [id, label, count] of FIELDS) {
            this.rows[id] = new Row(this, id, label, count);
            this.inner.appendChild(this.rows[id].el);
        }
        this.status = this.inner.appendChild(el("div", "chh-status"));

        // keep canvas shortcuts and panning away from the form
        for (const type of ["pointerdown", "mousedown", "dblclick", "contextmenu"]) {
            this.inner.addEventListener(type, (e) => e.stopPropagation());
        }
        this.lastHeight = 0;
        new ResizeObserver(() => this.fit()).observe(this.inner);
        this.render();
    }

    static blank() {
        return {
            v: 1,
            fields: Object.fromEntries(FIELDS.map(([id]) => [id, []])),
            locks: {},
            counts: Object.fromEntries(FIELDS.map(([id, , n]) => [id, n])),
            rating: "sfw",
        };
    }

    // ---- value <-> widget
    serialize() { return JSON.stringify(this.state); }

    load(value) {
        let data = value;
        if (typeof value === "string") {
            try { data = JSON.parse(value); } catch { data = null; }
        }
        const s = Panel.blank();
        if (data && typeof data === "object") {
            for (const [id] of FIELDS) {
                const tags = data.fields?.[id];
                if (Array.isArray(tags)) s.fields[id] = tags.map(String).filter(Boolean);
                if (data.locks?.[id]) s.locks[id] = true;
                if (Number.isFinite(data.counts?.[id])) s.counts[id] = data.counts[id];
            }
            if (RATINGS.some(([r]) => r === data.rating)) s.rating = data.rating;
        }
        this.state = s;
        this.undoStack = [];
        this.render();
    }

    render() {
        for (const row of Object.values(this.rows)) row.render();
        this.rating.value = this.state.rating;
        this.undoBtn.disabled = !this.undoStack.length;
    }

    height() { return this.inner.offsetHeight + 4; }

    fit() {
        const h = this.height();
        if (!h || Math.abs(h - this.lastHeight) < 1) return;
        this.lastHeight = h;
        const node = this.node;
        if (!node?.computeSize || !node.size) return;
        // grow or shrink the node with the form, keep whatever width the user chose
        const size = node.computeSize();
        node.setSize([Math.max(node.size[0], size[0]), size[1]]);
        node.setDirtyCanvas?.(true, true);
    }

    changed(render = true) {
        if (render) this.render();
        this.widget?.callback?.(this.serialize());
        this.node.graph?.setDirtyCanvas?.(true, true);
    }

    snapshot() {
        this.undoStack.push(JSON.stringify(this.state));
        if (this.undoStack.length > 30) this.undoStack.shift();
    }

    undo() {
        const last = this.undoStack.pop();
        if (!last) return;
        this.state = JSON.parse(last);
        this.changed();
    }

    say(text, isError) {
        this.status.textContent = text || "";
        this.status.classList.toggle("chh-error", !!isError);
        clearTimeout(this.sayTimer);
        if (text) this.sayTimer = setTimeout(() => (this.status.textContent = ""), 4000);
    }

    unlocked() { return FIELDS.map(([id]) => id).filter((id) => !this.state.locks[id]); }

    setLock(id, on) {
        if (on) this.state.locks[id] = true; else delete this.state.locks[id];
        this.changed();
    }

    clear() {
        this.snapshot();
        for (const id of this.unlocked()) this.state.fields[id] = [];
        this.changed();
    }

    async busy(fn) {
        this.el.classList.add("chh-busy");
        try { await fn(); } catch (err) {
            console.error("[Character Helper]", err);
            this.say("Could not reach the Character Helper backend.", true);
        } finally { this.el.classList.remove("chh-busy"); }
    }

    /** Fill the unlocked fields with a character's usual look. */
    async applyCharacter(name, takeSnapshot) {
        name = name || this.state.fields.character[0];
        if (!name) return this.say("Pick a character first.", true);
        await this.busy(async () => {
            let preset;
            try { preset = await getJSON("character?name=" + encodeURIComponent(name)); } catch {
                return this.say(`No data for "${name}".`, true);
            }
            if (takeSnapshot) this.snapshot();
            for (const id of this.unlocked()) {
                if (id !== "character" && preset.fields[id]) this.state.fields[id] = preset.fields[id];
            }
            if (!this.state.locks.character || this.state.fields.character[0] === preset.character) {
                this.state.fields.character = preset.fields.character;
            }
            this.changed();
        });
    }

    /** ids = one field (its dice) or null for every unlocked field. */
    async randomize(ids) {
        const single = ids?.length === 1 ? ids[0] : null;
        const fields = ids || this.unlocked();
        if (!fields.length) return this.say("Every field is locked.", true);
        await this.busy(async () => {
            const res = await getJSON("random", {
                fields, counts: this.state.counts, context: this.state.fields, rating: this.state.rating,
            });
            this.snapshot();
            Object.assign(this.state.fields, res.fields);
            this.changed();
            if (single === "character" && res.fields.character?.length) await this.applyCharacter(res.fields.character[0], false);
            else if (single && !res.fields[single]?.length) this.say("Nothing found for this field.", true);
        });
    }
}

app.registerExtension({
    name: "CharacterHelper.Node",
    getCustomWidgets() {
        return {
            CHARACTER_HELPER_FIELDS(node, inputName) {
                const panel = new Panel(node);
                const widget = node.addDOMWidget(inputName, "CHARACTER_HELPER_FIELDS", panel.el, {
                    getValue: () => panel.serialize(),
                    setValue: (v) => panel.load(v),
                    getMinHeight: () => panel.height(),
                    hideOnZoom: false,
                });
                panel.widget = widget;
                node.chhPanel = panel;
                return { widget };
            },
        };
    },
    nodeCreated(node) {
        if (node.comfyClass !== "CharacterHelper") return;
        if (node.size[0] < 460) node.setSize([460, node.size[1]]);
    },
});
