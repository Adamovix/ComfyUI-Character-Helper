import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const ICON = {
    dice: '<svg viewBox="0 0 16 16"><rect x="2" y="2" width="12" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="5.5" cy="5.5" r="1.1"/><circle cx="10.5" cy="10.5" r="1.1"/><circle cx="8" cy="8" r="1.1"/></svg>',
    lock: '<svg viewBox="0 0 16 16"><rect x="3.5" y="7" width="9" height="6.5" rx="1.3"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    unlock: '<svg viewBox="0 0 16 16"><rect x="3.5" y="7" width="9" height="6.5" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 7V5a2.5 2.5 0 0 1 4.6-1.3" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    undo: '<svg viewBox="0 0 16 16"><path d="M6 3 2.5 6.5 6 10M3 6.5h6.5a3.5 3.5 0 0 1 0 7H7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    clear: '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    person: '<svg viewBox="0 0 16 16"><circle cx="8" cy="5" r="2.8"/><path d="M2.5 14a5.5 5.5 0 0 1 11 0z"/></svg>',
};
const USUAL_LOOK_HINT = "“Usual look” fills in their tags.";
const MAX_COUNT = 9;
const UNDO_STEPS = 30;

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

/** GET, or POST when a body is given, against the node's backend (routes.py). */
async function request(path, body) {
    const opts = body ? { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } } : undefined;
    const r = await api.fetchApi("/character_helper/" + path, opts);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
}

// The fields ({id, label, count, group}) and rating choices are defined once, in the
// tag database (tools/field_rules.py), and fetched before the widget is registered.
let FIELDS = [], RATINGS = [], DEFAULT_RATING = "";
try {
    ({ fields: FIELDS, ratings: RATINGS, default_rating: DEFAULT_RATING } = await request("meta"));
} catch (err) {
    console.error("[Character Helper] could not load the field list", err);
}
const FIELD_LABEL = Object.fromEntries(FIELDS.map((f) => [f.id, f.label]));

const stylesheet = el("link");
stylesheet.rel = "stylesheet";
stylesheet.href = new URL("./character_helper.css", import.meta.url).href;
document.head.appendChild(stylesheet);

// ------------------------------------------------------------------ dropdown
// One shared suggestion list, attached to <body> so the node can't clip it.
const dropdown = {
    el: null, items: [], index: -1, owner: null,

    ensure() {
        if (this.el) return;
        this.el = el("div", "chh-dropdown");
        this.el.addEventListener("mousedown", (e) => {
            e.preventDefault(); // keep focus in the input
            const option = e.target.closest(".chh-option");
            if (option && this.owner) this.owner.accept(this.items[+option.dataset.i]);
        });
        document.body.appendChild(this.el);
    },

    /** preselect: highlight the first item, so Enter takes it. */
    show(owner, items, title, preselect) {
        this.ensure();
        this.owner = owner; this.items = items; this.index = preselect && items.length ? 0 : -1;
        if (!items.length) return this.hide();
        this.el.replaceChildren();
        if (title) this.el.appendChild(el("div", "chh-dd-title", title));
        items.forEach((item, i) => {
            const option = el("div", "chh-option" + (i === this.index ? " chh-active" : ""));
            option.dataset.i = i;
            option.appendChild(el("span", "chh-o-tag")).textContent = item.tag;
            const hint = this.hint(item, owner.id);
            if (hint) option.appendChild(el("span", "chh-o-hint")).textContent = hint;
            option.appendChild(el("span", "chh-o-count")).textContent = shortCount(Math.abs(item.count || 0));
            this.el.appendChild(option);
        });
        this.place(owner);
    },

    /** The grey note next to a suggestion: why it matched or where it belongs. */
    hint(item, fieldId) {
        if (item.alias) return "← " + item.alias;
        if (item.kind === "character") return item.series;
        if (item.kind === "series") return "series";
        if (item.hint) return item.hint;
        return item.field !== fieldId ? FIELD_LABEL[item.field] : "";
    },

    /** Below the input, or above it when there is no room below. */
    place(owner) {
        const input = owner.input.getBoundingClientRect();
        const width = Math.max(260, Math.min(420, owner.panel.el.getBoundingClientRect().width));
        this.el.style.display = "block";
        this.el.style.width = width + "px";
        this.el.style.left = Math.max(4, Math.min(input.left, window.innerWidth - width - 8)) + "px";
        const height = this.el.offsetHeight;
        const fitsBelow = window.innerHeight - input.bottom >= height + 8;
        const fitsAbove = input.top > height + 8;
        this.el.style.top = (fitsBelow || !fitsAbove ? input.bottom + 4 : input.top - height - 4) + "px";
    },

    hide() {
        if (this.el) this.el.style.display = "none";
        this.items = []; this.index = -1;
    },

    get open() { return this.items.length > 0; },

    move(step) {
        if (!this.open) return;
        const options = this.el.querySelectorAll(".chh-option");
        options[this.index]?.classList.remove("chh-active");
        const from = this.index < 0 && step < 0 ? 0 : this.index;   // "up" from nothing = last item
        this.index = (from + step + options.length) % options.length;
        options[this.index].classList.add("chh-active");
        options[this.index].scrollIntoView({ block: "nearest" });
    },

    current() { return this.index >= 0 ? this.items[this.index] : null; },
};

// ----------------------------------------------------------------------- row
// One field: label, tags as chips with an input, dice count, dice, lock.
class Row {
    constructor(panel, field, startsGroup) {
        const { id, label } = field;
        this.panel = panel; this.id = id;
        this.querySeq = 0;   // lets a slow autocomplete answer be dropped when a newer one was asked for
        this.el = el("div", "chh-row" + (startsGroup ? " chh-group" : ""));
        this.el.dataset.field = id;
        const name = el("label", "chh-label");
        name.textContent = label;
        this.box = el("div", "chh-box");
        this.input = el("input", "chh-input");
        this.input.type = "text";
        this.input.spellcheck = false;
        this.input.autocomplete = "off";
        this.input.setAttribute("aria-label", label);
        this.box.appendChild(this.input);
        this.count = el("input", "chh-count");
        this.count.type = "number"; this.count.min = 0; this.count.max = MAX_COUNT;
        this.count.title = "How many tags the dice adds" + (id === "character" ? " (0 = no named character)" : "");
        this.dice = el("button", "chh-btn", ICON.dice);
        this.dice.title = id === "character" ? "Random character" : `Random ${label} that fits the other fields`;
        this.lock = el("button", "chh-btn chh-lock");
        this.lock.title = "Lock: keep this field when randomizing, filling or clearing everything";
        this.el.append(name, this.box, this.count, this.dice, this.lock);

        name.addEventListener("click", () => this.input.focus());
        this.box.addEventListener("mousedown", (e) => {
            if (e.target === this.input) return;
            e.preventDefault();   // clicking a chip must not take the focus (and the typed text) away
            if (e.target === this.box) this.input.focus();
        });
        this.box.addEventListener("click", (e) => this.onChipClick(e));
        this.input.addEventListener("input", () => this.query());
        this.input.addEventListener("focus", () => this.query());
        this.input.addEventListener("click", () => { if (!dropdown.open) this.query(); });
        this.input.addEventListener("blur", () => this.onBlur());
        this.input.addEventListener("keydown", (e) => this.onKey(e));
        this.input.addEventListener("paste", (e) => this.onPaste(e));
        this.count.addEventListener("change", () => {
            panel.state.counts[id] = Math.max(0, Math.min(MAX_COUNT, parseInt(this.count.value) || 0));
            panel.changed();
        });
        this.dice.addEventListener("click", () => panel.randomize([id]));
        this.lock.addEventListener("click", () => panel.setLock(id, !panel.state.locks[id]));
        // typing here must not trigger ComfyUI's canvas shortcuts
        for (const node of [this.input, this.count]) {
            for (const type of ["keydown", "keyup", "keypress"]) node.addEventListener(type, (e) => e.stopPropagation());
        }
    }

    get tags() { return this.panel.state.fields[this.id]; }

    render() {
        this.box.querySelectorAll(".chh-chip").forEach((chip) => chip.remove());
        this.tags.forEach((tag, i) => {
            const chip = el("span", "chh-chip");
            chip.dataset.i = i;
            chip.title = "Click to edit";
            chip.appendChild(el("span", "chh-chip-text")).textContent = tag;
            const remove = el("button", "chh-x", "×");
            remove.title = "Remove";
            remove.tabIndex = -1;
            chip.appendChild(remove);
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

    /** Add tags, skipping the ones the field already has. */
    add(tags) {
        for (const tag of tags) {
            if (!this.tags.some((t) => t.toLowerCase() === tag.toLowerCase())) this.tags.push(tag);
        }
    }

    /** Turn the typed text into tags, exactly as typed. */
    commitInput() {
        const typed = splitTags(this.input.value);
        this.input.value = "";
        if (!typed.length) return;
        this.panel.snapshot();
        this.add(typed);
        this.panel.changed();
    }

    /** Take a suggestion from the dropdown. */
    accept(item) {
        this.input.value = "";
        this.panel.snapshot();
        if (item.kind === "character") {
            this.panel.state.fields.character = item.series ? [item.tag, item.series] : [item.tag];
            this.panel.state.locks.character = true;   // a hand-picked character survives "Randomize"
            this.panel.changed();
            dropdown.hide();
            this.panel.say(USUAL_LOOK_HINT);
            return;
        }
        this.add([item.tag]);
        this.panel.changed();
        this.query();   // keep offering what goes with the new tag
    }

    onChipClick(e) {
        const chip = e.target.closest(".chh-chip");
        if (!chip) return;
        const tag = this.tags[+chip.dataset.i];
        this.commitInput();
        this.panel.snapshot();
        this.tags.splice(this.tags.indexOf(tag), 1);
        this.panel.changed();
        if (!e.target.closest(".chh-x")) {   // clicked the text, not the ×: edit the tag
            this.input.value = tag;
            this.input.focus();
            this.input.select();
        }
    }

    onKey(e) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            if (dropdown.open) dropdown.move(e.key === "ArrowDown" ? 1 : -1);
            else this.query();
            e.preventDefault();
        } else if (e.key === "Enter" || e.key === "Tab") {
            // takes the highlighted suggestion; with the list closed (Escape) the text is kept as typed
            const suggestion = dropdown.owner === this ? dropdown.current() : null;
            const typed = this.input.value.trim();
            if (suggestion) this.accept(suggestion);
            else if (typed) { this.commitInput(); dropdown.hide(); }
            if (e.key === "Enter" || suggestion) e.preventDefault();   // a plain Tab still moves on
        } else if (e.key === ",") {
            this.commitInput();
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
        if (!/[,\n]/.test(text)) return;   // a single tag: let it land in the input
        e.preventDefault();
        this.panel.snapshot();
        this.add(splitTags(text));
        this.panel.changed();
    }

    onBlur() {
        this.querySeq++;
        this.commitInput();
        if (dropdown.owner === this) dropdown.hide();
    }

    /** Show matches for the typed text, or - in an empty input - what fits the other fields. */
    async query() {
        const seq = ++this.querySeq;
        const typed = this.input.value.trim();
        try {
            let items, title;
            if (typed) {
                items = await request(`autocomplete?field=${this.id}&q=${encodeURIComponent(typed)}`);
            } else {
                items = await request("suggest", { field: this.id, context: this.panel.state.fields, rating: this.panel.state.rating });
                title = this.id === "character" ? "popular characters" : "goes well with the rest";
            }
            if (seq !== this.querySeq || document.activeElement !== this.input) return;
            dropdown.show(this, items, title, !!typed);
        } catch (err) {
            console.warn("[Character Helper]", err);
        }
    }
}

// --------------------------------------------------------------------- panel
// The whole form.  Its state is the widget's value:
//   { fields: {id: [tags]}, locks: {id: true}, counts: {id: n}, rating }
class Panel {
    constructor(node) {
        this.node = node;
        this.undoStack = [];
        this.state = Panel.blank();
        this.el = el("div", "chh-panel");
        this.inner = this.el.appendChild(el("div", "chh-inner"));

        const bar = this.inner.appendChild(el("div", "chh-bar"));
        const button = (cls, html, title, onClick) => {
            const b = el("button", "chh-tool " + cls, html);
            b.title = title;
            b.addEventListener("click", onClick);
            return bar.appendChild(b);
        };
        button("chh-primary", ICON.dice + "<span>Randomize</span>", "Roll every unlocked field", () => this.randomize(this.unlocked()));
        button("", ICON.person + "<span>Usual look</span>", "Fill the unlocked fields with the character's most common tags", () => this.usualLook());
        bar.appendChild(el("span", "chh-spacer"));
        this.rating = bar.appendChild(el("select", "chh-rating"));
        this.rating.title = "Highest Danbooru rating the dice and suggestions draw from (milder ratings are included)";
        for (const r of RATINGS) this.rating.appendChild(new Option(r[0].toUpperCase() + r.slice(1), r));
        this.rating.addEventListener("change", () => { this.state.rating = this.rating.value; this.changed(); });
        this.undoButton = button("chh-icon", ICON.undo, "Undo the last change", () => this.undo());
        button("chh-icon", ICON.clear, "Clear every unlocked field", () => this.clear());

        this.rows = FIELDS.map((field, i) => new Row(this, field, i > 0 && field.group !== FIELDS[i - 1].group));
        this.inner.append(...this.rows.map((row) => row.el));
        this.status = this.inner.appendChild(el("div", "chh-status"));
        if (!FIELDS.length) this.say("Could not reach the Character Helper backend – see the ComfyUI console.", true, true);

        // clicks in the form are not for the canvas (dragging the node, its context menu)
        for (const type of ["pointerdown", "mousedown", "dblclick", "contextmenu"]) {
            this.inner.addEventListener(type, (e) => e.stopPropagation());
        }
        this.fittedHeight = 0;
        new ResizeObserver(() => this.fit()).observe(this.inner);
        this.render();
    }

    static blank() {
        return {
            fields: Object.fromEntries(FIELDS.map((f) => [f.id, []])),
            locks: {},
            counts: Object.fromEntries(FIELDS.map((f) => [f.id, f.count])),
            rating: DEFAULT_RATING,
        };
    }

    // ---- the widget's value
    serialize() { return JSON.stringify(this.state); }

    /** Take a saved value; anything missing or malformed falls back to the blank state. */
    load(value) {
        let saved = null;
        try { saved = JSON.parse(value); } catch { /* not ours: start blank */ }
        const state = Panel.blank();
        for (const { id } of FIELDS) {
            const tags = saved?.fields?.[id];
            if (Array.isArray(tags)) state.fields[id] = tags.map(String).filter(Boolean);
            if (saved?.locks?.[id]) state.locks[id] = true;
            if (Number.isInteger(saved?.counts?.[id])) state.counts[id] = saved.counts[id];
        }
        if (RATINGS.includes(saved?.rating)) state.rating = saved.rating;
        this.state = state;
        this.undoStack = [];
        this.render();
    }

    render() {
        for (const row of this.rows) row.render();
        this.rating.value = this.state.rating;
        this.undoButton.disabled = !this.undoStack.length;
    }

    /** Call after every change to the state. */
    changed() {
        this.render();
        this.widget?.callback?.(this.serialize());
        this.node.graph?.setDirtyCanvas?.(true, true);
    }

    // ---- node size: follows the height of the form, keeps the width the user chose
    height() { return this.inner.offsetHeight + 4; }

    fit() {
        const height = this.height();
        if (!height || Math.abs(height - this.fittedHeight) < 1) return;
        this.fittedHeight = height;
        const { node } = this;
        if (!node.size) return;
        const size = node.computeSize();
        node.setSize([Math.max(node.size[0], size[0]), size[1]]);
        node.setDirtyCanvas?.(true, true);
    }

    // ---- undo
    /** Call before changing the state, so the change can be undone. */
    snapshot() {
        this.undoStack.push(JSON.stringify(this.state));
        if (this.undoStack.length > UNDO_STEPS) this.undoStack.shift();
    }

    undo() {
        if (!this.undoStack.length) return;
        this.state = JSON.parse(this.undoStack.pop());
        this.changed();
    }

    // ---- actions
    /** A short note under the fields; it clears itself unless ``keep`` is set. */
    say(text, isError = false, keep = false) {
        this.status.textContent = text;
        this.status.classList.toggle("chh-error", isError);
        clearTimeout(this.sayTimer);
        if (!keep) this.sayTimer = setTimeout(() => (this.status.textContent = ""), 4000);
    }

    unlocked() { return FIELDS.map((f) => f.id).filter((id) => !this.state.locks[id]); }

    setLock(id, on) {
        if (on) this.state.locks[id] = true; else delete this.state.locks[id];
        this.changed();
    }

    clear() {
        this.snapshot();
        for (const id of this.unlocked()) this.state.fields[id] = [];
        this.changed();
    }

    /** Run a backend action with the buttons disabled. */
    async busy(action) {
        this.el.classList.add("chh-busy");
        try { await action(); } catch (err) {
            console.error("[Character Helper]", err);
            this.say("Could not reach the Character Helper backend.", true);
        } finally { this.el.classList.remove("chh-busy"); }
    }

    /** Fill the unlocked fields with the most common tags of the character. */
    async usualLook() {
        const name = this.state.fields.character[0];
        if (!name) return this.say("Pick a character first.", true);
        await this.busy(async () => {
            let preset;
            try { preset = await request("character?name=" + encodeURIComponent(name)); } catch {
                return this.say(`No data for "${name}".`, true);
            }
            this.snapshot();
            for (const id of this.unlocked()) {
                if (id !== "character") this.state.fields[id] = preset.fields[id];
            }
            this.changed();
        });
    }

    /** Roll the given fields: one (its dice) or every unlocked one (Randomize). */
    async randomize(ids) {
        if (!ids.length) return this.say("Every field is locked.", true);
        await this.busy(async () => {
            const { fields } = await request("random", {
                fields: ids, counts: this.state.counts, context: this.state.fields, rating: this.state.rating,
            });
            this.snapshot();
            Object.assign(this.state.fields, fields);
            this.changed();
            if (ids.length > 1) return;
            if (!fields[ids[0]].length) this.say("Nothing found for this field.", true);
            else if (ids[0] === "character") this.say(USUAL_LOOK_HINT);
        });
    }
}

app.registerExtension({
    name: "CharacterHelper.Node",
    getCustomWidgets() {
        return {
            CHARACTER_HELPER_FIELDS(node, inputName) {
                const panel = new Panel(node);
                panel.widget = node.addDOMWidget(inputName, "CHARACTER_HELPER_FIELDS", panel.el, {
                    getValue: () => panel.serialize(),
                    setValue: (value) => panel.load(value),
                    getMinHeight: () => panel.height(),
                    hideOnZoom: false,
                });
                return { widget: panel.widget };
            },
        };
    },
    nodeCreated(node) {
        if (node.comfyClass === "CharacterHelper" && node.size[0] < 460) node.setSize([460, node.size[1]]);
    },
});
