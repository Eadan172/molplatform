---
name: molecode-structure
description: "Use when an agent needs to show or edit a small-molecule structure as a MoleCode Mermaid graph. Do not use it to run docking, molecular dynamics, HTVS, or ADMET."
license: MIT
metadata:
  skill-author: AtomFlow-AI
  integration: molplatform
---

# MoleCode structure layer

MoleCode ([AtomFlow-AI/MoleCode](https://github.com/AtomFlow-AI/MoleCode), `pip install molecode`, MIT) turns a small molecule into an explicit Mermaid graph so a model can read or edit atoms and bonds. MolPlatform keeps that conversion in one tool. Docking, MD, HTVS, and the Scale workers stay on their existing paths.

## When to use it

- The user pastes SMILES and wants a structure the model can edit.
- The user pastes a MoleCode graph and wants it checked, or turned back into SMILES.
- A chat is discussing one molecule and should see the graph, not only the SMILES string.

## When not to use it

- Scoring a pose, running Vina, or screening a library. Use the docking module.
- Running or interpreting GROMACS / MD trajectories. Use the MD module.
- Computing MW, LogP, QED, or Ro5 filters. Use the existing descriptor path.
- Polymers, Markush abbreviations, or image-to-structure. This integration only validates small-molecule graphs.

## Tool

`structure.molecode` — `POST /api/v1/agent/tools/molecode`

```json
{"action": "smiles_to_mermaid", "smiles": "CC(=O)Oc1ccccc1C(=O)O", "name": "Aspirin"}
```

```json
{"action": "validate", "mermaid": "graph TB\n    subgraph Mol[\"Mol\"]\n        Mol_C_1[CH4]\n    end"}
```

`action` is `smiles_to_mermaid`, `mermaid_to_smiles`, or `validate`. A graph with an unknown atom, a bond to a missing atom, a self-bond, or a valence RDKit rejects returns HTTP 400 and `detail.code` (`invalid_atom`, `invalid_graph`, `invalid_valence`, `invalid_smiles`). The returned molecule is canonical SMILES from a sanitized RDKit mol.

The same helper runs inside `POST /api/v1/agent/tasks` when the prompt mentions MoleCode, Mermaid, or 结构图, and inside `POST /api/v1/ai/chat` when the context includes `smiles`, `mermaid`, or `molecode`.

Python entry points live in `app/services/molecode_service.py`: `smiles_to_mermaid`, `mermaid_to_mol`, `mermaid_to_smiles`, `run_structure_tool`.

## Optional LLM

Graph conversion does not call a model. To drive `molecode.llm` yourself, set the existing variables in `.env` (see `.env.example`):

```bash
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=replace-me
LLM_MODEL=gpt-4o-mini
```

`llm_client_from_env()` passes those values into `molecode.LLMClient` and returns `None` when `LLM_PROVIDER=none` or the key or base URL is blank. Do not commit a real key. `LLMClient` can also read `MOLECODE_API_KEY`, `MOLECODE_BASE_URL`, and `MOLECODE_MODEL` if you construct it without the MolPlatform helper.
