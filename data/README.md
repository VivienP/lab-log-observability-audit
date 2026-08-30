# Dataset acquisition and provenance

The audit uses three archive files. Put them in `data/raw/` without extracting them. The reproduction command verifies byte size, SHA-256, and MD5 before parsing.

Repository code is MIT-licensed. The datasets below are **not** covered by that MIT licence.

## 1. Flex-Cat Chemspeed log

- Dataset: *Dataset and code for the publication "An Autonomous Lab for Data-Driven Homogeneous Catalysis"*
- Creators: Jeffrey Bennett and Milad Abolhasani (North Carolina State University)
- Release: Zenodo record `18930287`, version `v1`
- DOI: [10.5281/zenodo.18930287](https://doi.org/10.5281/zenodo.18930287)
- Required file: `Flex_cat_data.zip` (7,182,559 bytes)
- Required member: `Flex_cat_data/Chemspeed/JAB_Oct_11_2024_VIP2 2025-05-26 14.23.22 2025-07-05 13.30.20/Eventlog.txt`
- Licence: **CC BY 4.0**, as reported by the Zenodo REST API field `metadata.license.id` for record `18930287` (checked 2026-08-30). The HTML landing page does not always surface that field prominently; the API record is the pin used here.

The raw archive is not redistributed in this repository. Obtain it directly from Zenodo. `data/derived/observations.csv` is a derived table of automated volume comparisons from the Chemspeed Eventlog. It is not a manual classification. Reuse of that table requires CC BY 4.0 attribution to Bennett and Abolhasani, the DOI above, and a note that the rows were extracted and compared by this audit.

## 2. Batch Distillation anomaly metadata and operation logs

- Dataset: *Batch Distillation Data for Developing Machine Learning Anomaly Detection Methods*
- Creators: Justus Arweiler, Indra Jungjohann, Jennifer Werner, Aparna Muraleedharan, Jochen Schmid, Heike Leitte, Jakob Burger, Kerstin Münnemann, Michael Bortz, Fabian Jirasek, and Hans Hasse
- Pinned release: Zenodo record `21535243`, version `1.1.2`
- Concept DOI: [10.5281/zenodo.17395543](https://doi.org/10.5281/zenodo.17395543)
- Required files:
  - `00_Batch_Distillation_Plant_M-202210_Timeseries_Label_Anomaly_Metadata.zip`
  - `07_Batch_Distillation_Plant_M-202210_Tabular_Operation_Logs.zip`
- Licence: **CC BY 4.0** (Zenodo record and companion Scientific Data article)

This repository does not bundle the archives so the source release remains the acquisition authority. `results/recovery_windows.csv` is a derived table of temporal matches. Reuse requires CC BY 4.0 attribution to the dataset creators, the concept DOI above, and a note that the matching window and coverage definition come from this audit.

The coverage and background figures derived from these archives are defined in [`../METHODOLOGY.md`](../METHODOLOGY.md). Coverage is an observability/activity proxy: it does not mean the recovery action was observed, and a silent window does not mean that no operator or controller intervention occurred.

## Download

PowerShell:

```powershell
New-Item -ItemType Directory -Force data/raw | Out-Null
Invoke-WebRequest 'https://zenodo.org/records/18930287/files/Flex_cat_data.zip?download=1' -OutFile 'data/raw/Flex_cat_data.zip'
Invoke-WebRequest 'https://zenodo.org/records/21535243/files/00_Batch_Distillation_Plant_M-202210_Timeseries_Label_Anomaly_Metadata.zip?download=1' -OutFile 'data/raw/00_Batch_Distillation_Plant_M-202210_Timeseries_Label_Anomaly_Metadata.zip'
Invoke-WebRequest 'https://zenodo.org/records/21535243/files/07_Batch_Distillation_Plant_M-202210_Tabular_Operation_Logs.zip?download=1' -OutFile 'data/raw/07_Batch_Distillation_Plant_M-202210_Tabular_Operation_Logs.zip'
```

macOS/Linux:

```bash
mkdir -p data/raw
curl -L 'https://zenodo.org/records/18930287/files/Flex_cat_data.zip?download=1' -o data/raw/Flex_cat_data.zip
curl -L 'https://zenodo.org/records/21535243/files/00_Batch_Distillation_Plant_M-202210_Timeseries_Label_Anomaly_Metadata.zip?download=1' -o data/raw/00_Batch_Distillation_Plant_M-202210_Timeseries_Label_Anomaly_Metadata.zip
curl -L 'https://zenodo.org/records/21535243/files/07_Batch_Distillation_Plant_M-202210_Tabular_Operation_Logs.zip?download=1' -o data/raw/07_Batch_Distillation_Plant_M-202210_Tabular_Operation_Logs.zip
```

Then run:

```text
uv run --frozen python scripts/reproduce.py
```

All hashes, sizes, source URLs, retrieval date, release identifiers, creators, licences, and expected result invariants are machine-readable in [`manifest.json`](manifest.json).

## Derived records

- `data/derived/observations.csv` contains the 60 Chemspeed transfer-endpoint comparisons. They are automated exact-decimal comparisons, not manual classifications and not independent physical measurements. Derived from Flex-Cat (CC BY 4.0).
- `results/recovery_windows.csv` contains one row for each of the 79 included labelled recoveries, including the anchor source, window, event row references, and match classification. Derived from Batch Distillation (CC BY 4.0). A `matched` classification means log activity in the window, not observed recovery.
- `results/background_null.csv` and `results/figures/recovery_activity_vs_background.png` contain the random-anchor background comparison for the same recoveries. Derived from Batch Distillation (CC BY 4.0); the null construction is this audit's.
