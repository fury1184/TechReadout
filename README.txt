TechReadOut CPU Lookup + CPU Identity + Name Cleanup Patch
==========================================================

Changes:
- App version bumped from 3.5.13 to 3.5.14.
- Intel ARK recognizes both legacy /ark/products/ URLs and Intel's current /products/sku/.../specifications.html URLs.
- Intel ARK parsing supports current rendered label/value pages as well as the older tech-label DOM.
- Legacy Xeons such as X5660/X5650/L5640/W3680/E5640 get strict CPU identities for validation and duplicate matching.
- Intel socket values such as FCLGA1366,LGA1366 normalize to LGA1366.
- Intel CPU lookup chain: Intel ARK -> CPU-Monkey -> TechPowerUp -> Open WebUI.
- AMD CPU lookup chain: AMD Official -> CPU-Monkey -> TechPowerUp -> Open WebUI.
- CPU-Monkey is vendor-neutral and supports Intel plus AMD families including Ryzen, Threadripper, EPYC, FX, Athlon, Phenom, Sempron, and Opteron when the family is present in the query.
- Compact Intel names such as i5-9600k and e5-2696v4 normalize to CPU-Monkey's exact stable URLs.
- IMPORTANT: if direct CPU-Monkey access is blocked, CPU-Monkey may use the remaining Scrape.Do call instead of giving up and spending that call on a broad TechPowerUp search.
- If Intel ARK/AMD Official plus CPU-Monkey use the normal-depth paid-call budget, TechReadOut skips the extra TPU paid request and continues to Open WebUI instead of returning a budget-exhausted error.
- CPU-Monkey's "LGA 1151-2" label is normalized to TechReadOut/Intel's physical socket name LGA1151.
- AMD Official lookup uses AMD.com's first-party search/pages, then parses cores, threads, base/boost clocks, TDP, socket, and architecture/codename when available.
- Common bare AMD model searches such as 5800X, 5700X3D, and 7945HX are recognized as CPUs.
- AMD Official and CPU-Monkey have source/trust labels in lookup review UI.
- CPU duplicate matching is strict when both models have a recognizable CPU identity.
- JSON spec import/canonical-name matching is CPU-generation aware. E5-2680, E5-2680 v2, E5-2680 v3, and E5-2680 v4 are separate CPUs.
- Manual spec fields are cleared when the model changes or a lookup misses, preventing stale specs from a previous CPU from being saved.
- CPU-Monkey page text such as "Benchmarks & Specs" is stripped from CPU model names before saving.
- Normalize Names also removes that CPU-Monkey page-title text from existing CPU records, so previously-added entries can be cleaned without editing each one manually.
- Lookup cache revision changed so stale misses from the earlier CPU lookup chain are not reused.

Files replaced:
  app/version.py
  app/scrapers/lookup.py
  app/scrapers/scoring.py
  app/scrapers/validation.py
  app/duplicates.py
  app/name_normalization.py
  app/models.py
  app/templates/inventory/add.html

Important CPU identity behavior:
  Xeon E5-2680       != Xeon E5-2680 v2
  Xeon E5-2680 v2    != Xeon E5-2680 v3
  Xeon E5-2680 v3    != Xeon E5-2680 v4
  Core i5-9600K       != Core i5-9600KF

Name-cleanup examples:
  Core i5-9600K Benchmarks & Specs        -> Core i5-9600K
  Xeon E5-1680 v2 Benchmarks & Specs      -> Xeon E5-1680 v2
  Xeon E5-2696 v4 - Benchmarks & Specifications -> Xeon E5-2696 v4
  Ryzen 7 5800X Benchmark, Test and Specs -> Ryzen 7 5800X

Verified lookup examples:
  i5-9600k                   -> https://www.cpu-monkey.com/en/cpu-intel_core_i5_9600k
  Intel Core i5-9600K        -> 6C/6T, 3.70/4.60 GHz, 95 W, LGA1151 parser result
  e5-2696v4                  -> Intel Xeon E5-2696 v4 CPU-Monkey slug
  AMD Ryzen 7 5800X          -> AMD Ryzen 7 5800X CPU-Monkey slug
  AMD EPYC 7452              -> AMD EPYC 7452 CPU-Monkey slug
  FX-8370                    -> AMD FX-8370 CPU-Monkey slug
  Ryzen Threadripper 1950X   -> AMD Ryzen Threadripper 1950X CPU-Monkey slug
  Intel Xeon X5660           -> current Intel ARK URL/parser path

CPU canonical-import regression test:
  Existing spec: Intel Xeon E5-2680 v2
  Import model:  Intel Xeon E5-2680
  Result:        NOT treated as a duplicate

After copying the files, restart/rebuild the TechReadOut application container as you normally do.
For CPUs already saved with "Benchmarks & Specs" in the name, open Normalize Names, preview the changes, and apply the CPU rename proposals.
