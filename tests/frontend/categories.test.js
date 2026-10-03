// Frontend test for custom categories (jsdom, Firebase mocked, no network).
// Run from the project root:
//   npm install --no-save jsdom
//   node tests/frontend/categories.test.js
const { JSDOM } = require("jsdom");
const fs = require("fs");
const FE = require("path").join(__dirname, "..", "..", "frontend") + "/";
const html = fs.readFileSync(FE + "index.html", "utf8");
let src = fs.readFileSync(FE + "app.js", "utf8");
src = src.replace(/^import[\s\S]*?from\s+"[^"]+";\s*$/gm, "");
src += "\nwindow.__t = { get CATS() { return CATS; }, get customCats() { return customCats; } };";

let fails = 0;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

function makeEnv(initial) {
  const store = JSON.parse(JSON.stringify(initial || {}));
  const listeners = [];
  const DEL = { __del: true };
  const segs = (a) => a.slice(1).join("/");
  const notify = () => listeners.forEach((l) => l());
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const mergeDeep = (t, s) => { for (const k of Object.keys(s)) { if (s[k] && s[k].__del) delete t[k]; else if (s[k] && typeof s[k] === "object" && !Array.isArray(s[k]) && !s[k].__ts) { t[k] = t[k] && typeof t[k] === "object" ? t[k] : {}; mergeDeep(t[k], s[k]); } else t[k] = s[k]; } };
  let n = 0;
  const m = {
    initializeApp: () => ({}), getAuth: () => ({ currentUser: { getIdToken: async () => "tok" } }),
    GoogleAuthProvider: function () {}, signInWithPopup() {}, signInWithRedirect() {}, signOut() {},
    onAuthStateChanged: (a, cb) => setTimeout(() => cb({ uid: "u1", displayName: "Test", email: "t@x.com" }), 0),
    initializeFirestore: () => ({}), persistentLocalCache: () => ({}), persistentMultipleTabManager: () => ({}),
    collection: (db, ...p) => ({ path: p.join("/"), col: true }), doc: (db, ...p) => ({ path: p.join("/") }),
    query: (c) => c, orderBy: () => null, serverTimestamp: () => ({ __ts: 1 }), deleteField: () => DEL,
    addDoc: async (c, d) => { store[c.path + "/auto" + ++n] = clone(d); setTimeout(notify, 0); },
    setDoc: async (r, d, o) => { if (o && o.merge) { store[r.path] = store[r.path] || {}; mergeDeep(store[r.path], clone(Object.assign({}, d))); /* keep DEL */ for (const k of Object.keys(d)) if (d[k] && typeof d[k] === "object" && !Array.isArray(d[k])) for (const kk of Object.keys(d[k])) if (d[k][kk] && d[k][kk].__del) delete store[r.path][k][kk]; } else store[r.path] = clone(d); setTimeout(notify, 0); },
    updateDoc: async (r, d) => { Object.assign(store[r.path], clone(d)); setTimeout(notify, 0); },
    deleteDoc: async (r) => { delete store[r.path]; setTimeout(notify, 0); },
    onSnapshot: (ref, cb) => {
      const fire = () => {
        if (ref.col) { const pre = ref.path + "/"; const docs = Object.keys(store).filter((k) => k.startsWith(pre) && !k.slice(pre.length).includes("/")).map((k) => ({ id: k.slice(pre.length), data: () => store[k] })); cb({ docs }); }
        else cb({ data: () => store[ref.path] });
      };
      listeners.push(fire); setTimeout(fire, 0); return () => {};
    },
  };
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  w.Element.prototype.scrollIntoView = function () {}; w.scrollTo = () => {}; w.print = () => {};
  w.fetch = () => Promise.reject(new Error("no net")); w.__m = m; w.confirm = () => true;
  w.__m.firebaseConfig = { apiKey: "abc", authDomain: "x", projectId: "x" };
  const names = Object.keys(m).filter((k) => k !== "firebaseConfig").join(", ");
  w.eval(`const { ${names} } = window.__m; const firebaseConfig = window.__m.firebaseConfig; const API_BASE = "";\n` + src);
  return { w, d: w.document, store };
}
const $ = (e, id) => e.d.getElementById(id);
const fire = (e, el, type) => el.dispatchEvent(new e.w.Event(type, { bubbles: true }));
const opts = (e, id) => [...$(e, id).options].map((o) => o.value);

(async () => {
  const E = makeEnv({ "users/u1": { budget: 40000, catBudgets: {} } });
  await tick(50);
  ok(JSON.stringify(opts(E, "cat")) === JSON.stringify(["Food", "Travel", "Shopping", "Bills", "Other", "__new__"]), "initial dropdown = 5 built-ins + '+ New category'");
  ok(opts(E, "fc").length === 6, "filter dropdown has All + 5");

  // choose "+ New category" from the expense form
  const cat = $(E, "cat"); cat.value = "Bills"; fire(E, cat, "focus"); cat.value = "__new__"; fire(E, cat, "change");
  ok($(E, "dcat").hasAttribute("open"), "dialog opens when '+ New category' is chosen");
  ok(cat.value === "Bills", "select goes back to previous value while dialog is open");

  const add = (name) => { $(E, "cname").value = name; fire(E, $(E, "cform"), "submit"); };
  add("Groceries"); await tick(50);
  ok(E.store["users/u1"].customCats && E.store["users/u1"].customCats[0].name === "Groceries", "saved to Firestore user doc");
  ok(cat.value === "Groceries", "new category auto-selected in the form");
  ok(!$(E, "dcat").hasAttribute("open"), "dialog closed after add from form");
  ok(opts(E, "rcat").includes("Groceries") && opts(E, "scat").includes("Groceries") && opts(E, "fc").includes("Groceries"), "appears in recurring, split and filter dropdowns");
  ok(opts(E, "cat").indexOf("Groceries") === 4 && opts(E, "cat")[5] === "Other", "'Other' stays last");

  // validation
  E.w.eval("1"); const mcat = $(E, "mcat"); mcat.click();
  for (const [n, label] of [["groceries", "duplicate (case-insensitive)"], ["Other", "built-in name"], ["a.b", "dot"], ["constructor", "reserved word"], ["__x", "leading __"], ["", "empty"]]) {
    add(n); ok($(E, "cerr").textContent.length > 0, "rejects " + label + ": " + $(E, "cerr").textContent);
  }
  ok(E.w.__t.customCats.length === 1, "no invalid category saved");

  // XSS check
  add("<img src=x onerror=1>"); await tick(30);
  ok(!$(E, "cb").querySelector("img") && !$(E, "clist").querySelector("img") && !$(E, "cat").querySelector("img"), "HTML in a category name is escaped everywhere");
  const del = (n) => { const b = [...$(E, "clist").querySelectorAll("[data-dc]")].find((x) => x.dataset.dc === n); b.click(); };
  del("<img src=x onerror=1>"); await tick(30);
  ok(E.w.__t.customCats.length === 1, "delete unused category works");

  // use it: add an expense
  $(E, "title").value = "Weekly shopping"; $(E, "amount").value = "5000"; $(E, "date").value = "2026-10-01"; $(E, "cat").value = "Groceries";
  fire(E, $(E, "form"), "submit"); await tick(50);
  const exp = Object.entries(E.store).filter(([k]) => k.startsWith("users/u1/expenses/"));
  ok(exp.length === 1 && exp[0][1].category === "Groceries", "expense saved with custom category");
  ok($(E, "leg").textContent.includes("Groceries"), "donut legend shows custom category");
  ok([...$(E, "cb").querySelectorAll("[data-cb]")].some((i) => i.dataset.cb === "Groceries"), "category limit row exists for it");
  ok($(E, "rows").innerHTML.includes("#10b981"), "custom colour used in the table dot");

  // set a limit, then delete the category -> expenses move to Other, limit removed
  const lim = $(E, "cb").querySelector('[data-cb="Groceries"]'); lim.value = "8000"; fire(E, lim, "change"); await tick(30);
  ok(E.store["users/u1"].catBudgets.Groceries === 8000, "limit saved for custom category");
  mcat.click(); del("Groceries"); await tick(60);
  const exp2 = Object.entries(E.store).filter(([k]) => k.startsWith("users/u1/expenses/"));
  ok(exp2[0][1].category === "Other", "expense moved to Other after delete");
  ok(!("Groceries" in (E.store["users/u1"].catBudgets || {})), "category limit removed");
  ok(E.w.__t.customCats.length === 0 && !opts(E, "cat").includes("Groceries"), "gone from dropdowns");

  // reload with categories already stored
  const E2 = makeEnv({ "users/u1": { customCats: [{ name: "Gym", color: "#ef4444" }, { name: "Gym", color: "#000000" }, { name: "Food", color: "#fff" }, { name: "Pets", color: "javascript:x" }] } });
  await tick(50);
  ok(JSON.stringify(opts(E2, "cat")) === JSON.stringify(["Food", "Travel", "Shopping", "Bills", "Gym", "Pets", "Other", "__new__"]), "categories load on startup; duplicates/invalid ignored: " + opts(E2, "cat"));
  ok(E2.w.__t.customCats[1].color === "#94a3b8", "bad colour value sanitised");

  // Urdu mode keeps working
  $(E2, "lang").click(); await tick(30);
  ok(!!$(E2, "cat").options[opts(E2, "cat").indexOf("__new__")].textContent.includes("نیا"), "Urdu label for '+ New category'");
  console.log(fails ? `\n${fails} FAILED` : "\nALL PASSED"); process.exit(fails ? 1 : 0);
})().catch((e) => { console.error("ERR", e); process.exit(2); });
