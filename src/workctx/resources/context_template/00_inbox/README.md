# Inbox

Place unprocessed artifacts under `raw/`. Optional sidecar metadata may accompany an artifact. The system creates manifests under `manifests/` and moves suspicious content to `quarantine/`.

An artifact remains pending until a validated canonical transaction commits. Do not manually move it to `01_processed` before that point.
