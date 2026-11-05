# Third-party development data

Original project code and third-party datasets are separate materials. Dataset license declarations do not relicense the project's source code or override upstream terms.

## CMB-Clin

- Publisher: FreedomIntelligence / the CMB authors.
- Official repository: https://github.com/FreedomIntelligence/CMB
- Official dataset card: https://huggingface.co/datasets/FreedomIntelligence/CMB
- Repository and dataset revisions: pinned in `download_manifest.json`.
- Publisher-declared license: Apache License 2.0; complete license text retained at `prepared/cmb/LICENSE.txt`.
- Changes: descriptions split into source blocks; titles and question-answer references excluded from generation inputs; potentially problematic timelines segregated; deterministic development/holdout partition created. Original terminology, numerical values and units are preserved.
- `references/cmb_answers_do_not_feed_model.json` preserves upstream references for optional human review. It is not supplied to the model and is not first-course-record ground truth.
- Citation: CMB: A Comprehensive Medical Benchmark in Chinese. https://arxiv.org/abs/2308.08833

The publisher's license declaration is recorded, not an independent adjudication of rights in underlying textbook materials.

## MIMIC-IV Clinical Database Demo 2.2

- Authors: Alistair Johnson, Lucas Bulgarelli, Tom Pollard, Steven Horng, Leo Anthony Celi, Roger Mark.
- Official release: https://physionet.org/content/mimic-iv-demo/2.2/
- Citation: Johnson et al. (2023), MIMIC-IV Clinical Database Demo, version 2.2, PhysioNet. https://doi.org/10.13026/dp1f-ex47
- License: Open Data Commons Open Database License 1.0. The separately attributed derived database in `prepared/mimic_demo/` remains available under ODbL-1.0; full terms are retained in that directory's `LICENSE.txt`.
- Changes: one eligible admission per patient; a six-hour post-admission cutoff applied jointly to chart and availability times; future and missing-time results excluded; minimal demographic and early laboratory information converted to competition-style JSON. No discharge diagnosis, death outcome or patient name is included.
- Common laboratory labels receive an additional Chinese label. Original English names, values and units remain present; no numerical unit conversion is performed.
- Raw download links and hashes are recorded in `download_manifest.json`. Raw files can be obtained using `python tools/fetch_public_data.py` and are excluded from Git.

## Artificial cases and competition format example

`synthetic/adversarial.json` contains project-authored artificial engineering cases, not real patients or clinician-validated cases. `examples/input.demo.json` transcribes the publicly published competition format example; it is educational test material, not a hidden test answer.
