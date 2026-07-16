# LAC Customer Portal V3 — Handoff Guide

**File:** `lac-portal-v3.html` · Vanilla JS, no framework, no build step.
Same architecture as your current V2 (`apiGet` + `STANDALONE` mock + render functions), so it drops into the existing Frappe Web Page.

---

## 1. What this is

A production-ready rebuild of the customer portal that applies every point from the design review. Open the HTML file directly in a browser and it runs in **STANDALONE mock mode** (fake data, no backend). Deployed inside Frappe it automatically switches to **live mode** and calls your real endpoints.

### The 10/10 checklist — what changed vs V2

| Review issue | Fix in V3 |
|---|---|
| A1 Desktop = scaled-up phone | Real responsive shell: **left rail + top bar ≥900px**, **bottom tab bar <900px** (CSS media queries, one codebase) |
| A2 Cart FAB collides with tab bar | Cart is a **persistent header icon** with count badge → opens a right **drawer** (desktop) / **bottom sheet** (mobile). FAB removed |
| A3 Everything is a bottom sheet | Screens are full views; only the cart is an overlay. Deep flows should become routes (see §5) |
| A4 Home = three heroes | **One hero** (balance + tier). Check-in demoted to a compact strip inside the hero |
| B1 No routing / no Back | **Hash routing** (`#/home … #/profile`) + `history.pushState` for the cart → browser & Android **Back** work, links are shareable |
| C1 Emoji icon system | **One SVG line-icon set** (`IP` map + `icon()`), tinted with brand colors |
| C2 Localisation | **All Mongolian**, consistent |
| C3 Heavy ambient bg | Static gradient only; `prefers-reduced-motion` respected |
| D1 Global spinner / waterfall | **Skeleton loaders**; `me`+`products` load in parallel and paint immediately; club/articles load lazily on first visit |
| D2 Touch targets / a11y | 44px hit areas, `aria-label` on every icon button, visible focus via native buttons |
| E1 Buried commerce info | **Free-delivery threshold** surfaced live in the cart (“Үнэгүй хүргэлт хүртэл ₮X”) |

---

## 2. Deploy into the Frappe Web Page

The portal lives in Web Page **`lac-customer-portal-v2`** (route `customer-portal`). Two options:

**A. Split into the two fields (matches current setup)**
1. **Main Section (HTML):** paste everything from the first `<style>` opening tag through the closing `</div>` of `#toast-root` — i.e. the `<style>…</style>` block **plus** the three markup divs (`#app`, `#overlay`, `#toast-root`). Keep the `.page-head/.navbar{display:none}` rules at the top of the style block — they hide Frappe’s chrome.
2. **JavaScript field:** paste the contents **inside** the `<script>…</script>` tag (not the tag itself).
3. Set the page **Content Type = HTML**, **Full Width = yes**, **Show Title = no**.

**B. Keep as one file** — serve `lac-portal-v3.html` as-is from a route/asset. Everything (fonts link, style, markup, script) is self-contained.

> Back up the current page first. You already keep snapshots (`customer-portal-backup-*`) — make one more before pasting.

### The STANDALONE switch
```js
var STANDALONE = (location.protocol === 'file:')
  || (typeof window.frappe === 'undefined' && !location.hostname)
  || location.hostname === 'localhost';
```
- **true** → uses the `MOCK` object (great for design review / offline demo).
- **false** (inside Frappe) → calls the real endpoints below.
Remove the `localhost` clause if you want live data on a local bench.

---

## 3. Endpoint contract (live mode)

V3 calls the **same methods your V2 already uses**, with graceful fallback to mock if any fail:

| Method (GET) | Used for | Expected `message` shape |
|---|---|---|
| `portal_get_my_customer` | Hero balance, tier, name, streak | `{ customer: { customer_name, mobile_no, current_loyalty_tier, custom_loyalty_points, tier_total_purchase, tier_threshold, custom_member_date, custom_checkin_streak } }` |
| `cp_products` | Shop grid + featured | `{ items:[{id/item_code, name, cat, price, rating, img, tagline}], categories:[…] }` |
| `portal_get_missions` | Club missions | `{ missions:[{label, reward, progress, target, completed}] }` |
| `portal_get_badges` | Club badges | `{ badges:[{label/name, icon, earned}] }` |
| `portal_get_articles` | Knowledge | `{ articles:[{title, category, minutes, reward_points}] }` |

The normalisation logic (real shape → UI shape) is already written in `loadClub` / `loadArticles`, so you mostly need the endpoints to return the fields above. Tier codes map via `TIER_MN` (`Silver→Мөнгөн`, `Gold→Алт`, …) — adjust that map to your exact ERPNext tier names.

---

## 4. Design system (already in the CSS `:root`)

- **Fonts:** Spectral (serif / headings), Hanken Grotesk (sans / body)
- **Colors:** `--ink #0C1B33` · `--crimson #D71E2B` · `--teal #0E9C8E` · `--gold #C79A3A` · `--bg #EDF2FA` · `--muted #5E7085`
- **Accent knob:** everything primary reads `--accent` (defaults to crimson). Change one line to re-theme.
- **Breakpoint:** `900px` (rail ↔ tab bar). Product grid `3→2` cols; quick-actions `4→2` under 520px.
- **Icons:** add a new one by putting its SVG path(s) in the `IP` map, then `icon('name')`.

---

## 5. What's included now (V3.1) & what remains

**Built in this file** — every main flow, all Back-aware (hash routes / `pushState` sheets):

- **5 tab screens** — Home, Shop (search + category filter), LAC Club, Knowledge, Profile
- **Product detail** — `#/product/:id`, with add-to-cart + supplement disclaimer
- **Cart drawer** — header icon → side drawer / bottom sheet, live free-delivery nudge
- **Checkout** — `#/checkout`: address → delivery method (Энгийн / UBCab) → Minu → **success** screen
- **Health assessment quiz** — `#/assess`: multi-step → product recommendation
- **AI advisor** — `#/advisor`: chat UI (canned replies; swap in your real backend)
- **Orders** — `#/orders`: history, status pills, reorder, track
- **Subscriptions** — `#/subs`: active list, cancel / reschedule
- **Referral**, **Notifications**, **Order tracking** — Back-aware bottom sheets

**Still to wire (backend, not layout):**
- **Real endpoints** for the new screens — checkout/order create, order list, subscription list, referral, tracking. The UI reads from `MOCK.*`; point each `renderX`/loader at your method (mirror the pattern in `loadClub`).
- **Payment** — `confirmOrder()` currently just shows the success screen. Hook it to your **Minu** create-payment call + `startPayPoll` (reuse V2's polling), then show `successHTML()` on confirmation.
- **AI advisor** — `LAC.send()` pushes a canned reply; connect to your assistant endpoint.
- **Address CRUD form**, **change-phone OTP**, **reviews/ratings** — small forms from V2; add as sheets using the `openSheet`/`renderSheet` pattern already in the file.

Two commerce/trust must-dos (already done): free-delivery threshold shows in the cart; the medical disclaimer shows on product detail.

---

## 6. Accessibility & performance notes
- Every icon-only button has `aria-label`; keep that when you add buttons.
- Keep hit areas ≥44px (the `.iconbtn`, `.add`, `.qty button`, `.tab` sizes already comply).
- Skeletons render during load — keep them for any new async screen.
- Background is a static gradient (no animation loop) — good for low-end Android. Don’t reintroduce always-on blurred/animated layers.

---

*Questions or want the checkout/quiz/advisor screens built out in this same style — say the word.*
