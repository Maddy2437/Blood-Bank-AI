# Data Dictionary — Blood Bank Decision Support System

Provenance legend: 🟢 REAL (unmodified) · 🟡 SYNTHETIC (generated, anchored to real data) · 🔵 RULE/CONFIG (documented reference data, not clinical instruction)

---

## 🟢 `supply_data.csv` — REAL, unmodified
Daily national/regional blood collection totals. **Source granularity: system-wide aggregate.**

| Column | Type | Description |
|---|---|---|
| `date` | date | Calendar day, 2006-01-01 to 2026-09-04, no gaps |
| `blood_group` | string | ABO group only (A, B, AB, O) — no Rh in this feed |
| `units_collected` | int | Total units collected across the system that day, for that ABO group |

**Row count:** 30,208. **Verified:** no nulls, no duplicate (date, blood_group) pairs, no negatives.

---

## 🟢 `demand_data.csv` — REAL, unmodified (provenance inferred — see note)
Daily national/regional blood demand totals. Same granularity and date coverage as `supply_data.csv`.

> **Provenance note:** unlike `supply_data.csv`, this file was not explicitly labeled "real" in your original brief. It's classified REAL here on strong internal-consistency evidence (93.78% exact row-level match with `inventory_data.units_issued`, with the remaining 6.22% being exactly the days demand exceeds supply — a real shortage signature, not noise). Full reasoning in `validation_report.md` Section 0.

| Column | Type | Description |
|---|---|---|
| `date` | date | Calendar day |
| `blood_group` | string | ABO group (A, B, AB, O) |
| `units_demanded` | int | Total units demanded that day, for that ABO group |

**Row count:** 30,208.

---

## 🟢 `inventory_data.csv` — REAL, unmodified (provenance inferred — see note)
Daily system-wide inventory ledger. Same date/group coverage.

> **Provenance note:** not explicitly labeled "real" in your original brief. Classified REAL here because `units_received` matches `supply_data.units_collected` exactly for 100% of 30,208 rows, and the closing-inventory formula holds exactly for 100% of rows — a level of exact multi-field consistency very unlikely in independently fabricated data. Full reasoning in `validation_report.md` Section 0.

| Column | Type | Description |
|---|---|---|
| `date` | date | Calendar day |
| `blood_group` | string | ABO group |
| `opening_inventory` | int | Units on hand at start of day |
| `units_received` | int | = `supply_data.units_collected` for that day/group (verified 100% match) |
| `units_issued` | int | Units actually issued (≤ demand on shortage days) |
| `units_discarded` | int | Units discarded (expiry/quality, not itemized) |
| `closing_inventory` | int | = opening + received − issued − discarded (verified exact, 100% of rows) |

**Row count:** 30,208. `closing_inventory[day N]` = `opening_inventory[day N+1]` verified for 100% of rows.

**Use in this system:** these three REAL tables are the intended input to the **forecasting and shortage-risk-detection** modules (not yet built — this deliverable is the data layer only).

---

## 🟡 `donors.csv` — SYNTHETIC (original fields unchanged, `rh_factor`/`abo_rh` added)

| Column | Type | Provenance | Description |
|---|---|---|---|
| `donor_id` | string | 🟢 unchanged | Unique donor identifier, e.g. `D100000` |
| `name` | string | 🟢 unchanged | Synthetic name (pre-existing, not real PII) |
| `blood_group` | string | 🟢 unchanged | ABO group only, as originally provided |
| `rh_factor` | string | 🟡 **new** | `+` or `-`, assigned deterministically per donor (see Assumptions) |
| `abo_rh` | string | 🟡 **new**, derived | Convenience concatenation, e.g. `O+`. Denormalized on purpose for direct compatibility-matching joins; always equals `blood_group + rh_factor` |
| `phone` | string | 🟢 unchanged | Synthetic phone number (Malaysian format — cosmetic artifact of original data, left as-is, see README) |
| `last_donation_date` | date | 🟢 unchanged | Verified 100% consistent with max date in `donation_history.csv` per donor |
| `donation_count` | int | 🟢 unchanged | Verified 100% consistent with row count in `donation_history.csv` per donor |
| `contact_opt_in` | string | 🟢 unchanged | Yes/No |

**Row count:** 8,000 (unchanged).

**Assumption flagged:** `rh_factor` is assigned via a seeded random draw at 94% Rh+ / 6% Rh−, per donor, deterministically (same donor always gets the same result on re-run). This ratio is drawn from a published systematic review of ~1.43M Indian blood donors (Patidar et al. 2021, *ISBT Science Series*: 94.13% Rh+, 5.87% Rh−), applied uniformly across all four ABO groups as a simplification. **This must be replaced with the real donor registry's actual Rh distribution before production use.**

---

## 🟡 `donation_history.csv` — SYNTHETIC, provided source / provenance to be confirmed, unchanged
Copied through with **zero modification** (byte-verified against the original source file). Already 100% internally consistent with `donors.csv` on import — that perfect consistency, plus clean sequential IDs, is itself part of why this is classified synthetic rather than real (see `validation_report.md` Section 0 for full reasoning). Not claimed as real hospital/operational data.

| Column | Type | Description |
|---|---|---|
| `donation_id` | string | Unique donation event ID, e.g. `DN0000001` |
| `donor_id` | string | FK → `donors.donor_id` |
| `donation_date` | date | Date of that donation event |
| `units_donated` | int | 1 or 2. **See Assumptions/Flags in README** — 2 units from a single whole-blood donation event is not standard practice and is not resolved here. |

**Row count:** 39,414.

---

## 🟡 `blood_units.csv` — SYNTHETIC, generated
The unit-level (physical bag) operational layer. **Every row traces to exactly one row in `donation_history.csv`** (provided source data, provenance to be confirmed — see Section 0 of `validation_report.md`) — no unit is invented independently.

| Column | Type | Description |
|---|---|---|
| `unit_id` | string | Unique unit ID, e.g. `BU0000001` |
| `donation_id` | string | FK → `donation_history.donation_id` |
| `donor_id` | string | FK → `donors.donor_id` (denormalized for convenience) |
| `abo_group` | string | A/B/AB/O, copied from the donor |
| `rh_factor` | string | +/-, copied from the donor |
| `blood_group` | string | `abo_group + rh_factor`, e.g. `O+` |
| `collection_date` | date | = the donation's `donation_date` |
| `expiry_date` | date | = `collection_date` + 42 days (CPDA-1 whole-blood/RBC shelf life — see Assumptions) |
| `volume_ml` | int | 450 (constant — standard single whole-blood donation volume) |
| `status` | string | `Available` \| `Issued` \| `Discarded` \| `Expired` |
| `discard_reason` | string, nullable | `Expired (shelf life exceeded)` or `Quality Hold (heuristic, non-clinical placeholder)`; null unless status is Discarded/Expired |
| `issue_id` | string, nullable | FK → `blood_issues.issue_id`; null unless status is Issued |

**Row count:** 42,155 (= exact sum of `units_donated` across all 39,414 donation events — verified).

---

## 🟡 `blood_requests.csv` — SYNTHETIC, generated
Patient-level blood requests. **No patient names or hospital identifiers** — deliberately minimal, no unnecessary PII.

| Column | Type | Description |
|---|---|---|
| `request_id` | string | Unique request ID, e.g. `BR0000001` |
| `request_date` | date | Date of the request |
| `abo_group` | string | Requested ABO group |
| `rh_factor` | string | Requested Rh factor |
| `blood_group` | string | Combined, e.g. `A-` |
| `units_requested` | int | 1–4 (heuristic distribution, see Assumptions) |
| `priority` | string | `Routine` \| `Urgent` \| `Emergency` — **heuristic placeholder, not clinically derived** |
| `status` | string | `Fulfilled` \| `Partial` \| `Unfulfilled` — computed from actual simulated issuance, not assigned |
| `units_fulfilled` | int | Actual units issued against this request (≤ `units_requested`) |

**Row count:** 32,077. Sizing method: see README "How request volume was derived."

---

## 🟡 `blood_issues.csv` — SYNTHETIC, generated
The actual issuance transactions — output of a FEFO (first-expiry-first-out), priority-ordered allocation simulation.

| Column | Type | Description |
|---|---|---|
| `issue_id` | string | Unique issue ID, e.g. `BI0000001` |
| `request_id` | string | FK → `blood_requests.request_id` |
| `unit_id` | string | FK → `blood_units.unit_id` |
| `issue_date` | date | = the request's `request_date` |
| `exact_match` | string | `Yes` if the issued unit's blood group exactly matches the request; `No` if it was a compatible substitute (e.g. O− issued for an A+ request) |

**Row count:** 38,084. Each `unit_id` appears at most once (a physical unit can only be issued once — verified).

---

## 🔵 `compatibility_rules.csv` — RULE/CONFIG, not real or synthetic patient data
Standard ABO/Rh(D) **whole blood / packed RBC** compatibility matrix (Landsteiner's law + the Rh rule). All 8×8 = 64 donor×recipient combinations enumerated.

| Column | Type | Description |
|---|---|---|
| `donor_group` | string | One of 8 ABO/Rh groups |
| `recipient_group` | string | One of 8 ABO/Rh groups |
| `compatible` | string | `Yes`/`No` |
| `rule_type` | string | Always "Whole blood / packed RBC transfusion" in this version |
| `source_note` | string | States the textbook basis and explicitly flags that plasma/platelet compatibility (which follows *reverse*-ABO logic) is **not** covered, and that clinical sign-off is required before use |

**Row count:** 64 (27 compatible pairs, 37 incompatible pairs — matches expected count for standard ABO/Rh RBC rules).
