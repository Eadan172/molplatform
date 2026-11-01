# MolClaw workflow references bundled with MolPlatform Scale MVP

These are original MolClaw skill/workflow documents included to retain the upstream workflow instructions and provenance. See `MOLCLAW_LICENSE` for the upstream license.

## Wired in this MVP
- `molclaw-mol-basic-metrics` and `molclaw-drug-likeness` are implemented via a constrained RDKit descriptor calculation (MW, LogP, HBD/HBA, TPSA, rotatable bonds, QED, Ro5 violations).
- `molclaw-docking-screening` contributes workflow gates and count/provenance discipline. Runtime execution calls the installed AutoDock Vina CLI directly.
- `molclaw-quickvina-docking` is a reference only; the MVP invokes the configured `VINA_BINARY` (default `vina`).
- `molclaw-smiles-valid-check` is implemented through RDKit parsing.
- `molclaw-admet` is reference-only. No validated ADMET predictor is connected; requests are rejected as `needs_tool` rather than guessed.
- `molclaw-visualize-molecule` is reference-only; report charts are implemented, but a full 3D molecular renderer is not yet wired.
- The L2 GROMACS workflow informs prerequisites and logging. The executable path runs `gmx mdrun` from an uploaded prebuilt `.tpr`; protein/ligand preparation is not automated.

MCP servers and all external tools described upstream are not installed or launched automatically by copying these files. Configure them separately if you later add further skills.
