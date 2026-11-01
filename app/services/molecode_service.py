"""Thin MoleCode adapter for SMILES ↔ Mermaid graphs.

Conversion is deterministic and RDKit-validated. It does not run docking,
molecular dynamics, or high-throughput virtual screening. An optional
OpenAI-compatible client is available only when the existing LLM_* settings
are configured; structure conversion itself does not call a model.
"""
from __future__ import annotations

import io
import os
import re
from contextlib import contextmanager, redirect_stdout
from typing import Any, Iterator

from rdkit import Chem
from rdkit import RDLogger

from molecode import mol_to_inchi
from molecode import mol_to_mermaid as mc_mol_to_mermaid
from molecode import mol_to_smiles as mc_mol_to_smiles
from molecode.molecule import has_invalid_atoms
from molecode.molecule import mermaid_to_mol as mc_mermaid_to_mol

_MAX_SMILES = 8000
_MAX_MERMAID = 200_000
_STRUCTURE_HINTS = (
    "molecode",
    "mermaid",
    "结构图",
    "分子图",
    "编辑结构",
    "结构编辑",
    "结构校验",
)
_FENCE = re.compile(r"```(?:mermaid)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_GRAPH = re.compile(r"(graph\s+(?:TB|LR)\b[\s\S]*\bend)", re.IGNORECASE)
_STEREO_BOND = re.compile(
    r"([\w_]+)\s*===\|([EZez]|cis|trans|CIS|TRANS)\|\s*([\w_]+)"
)
_BOND = re.compile(r"([\w_]+)\s*(<-->|===|-\.-|-->|---)\s*([\w_]+)")
_ATOM = re.compile(r"([\w_]+?)(?:_(R|S))?\[([^\]]+)\]")
_LABEL = re.compile(r"^([A-Z][a-z]?)(?:H(\d*))?(?:\((\d*[+-])\))?$")
_SMILES_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9@+\-\[\]\(\)=#$:\\/.%]*")
_NAME = re.compile(r"(?:name|名为|名称)\s*[=:：]\s*([^\s,，。]{1,80})", re.IGNORECASE)


class MoleCodeStructureError(ValueError):
    """A SMILES string or MoleCode graph failed RDKit validation."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@contextmanager
def _quiet_chem() -> Iterator[None]:
    RDLogger.DisableLog("rdApp.*")
    try:
        with redirect_stdout(io.StringIO()):
            yield
    finally:
        RDLogger.EnableLog("rdApp.*")


def _clean_name(name: str | None) -> str:
    cleaned = " ".join((name or "").split())
    cleaned = cleaned.translate(str.maketrans("", "", "\"[]\n\r\\"))
    cleaned = cleaned.strip()
    return (cleaned or "Molecule")[:80]


def _mol_from_smiles(smiles: str) -> Chem.Mol:
    text = (smiles or "").strip()
    if not text:
        raise MoleCodeStructureError("SMILES is empty.", code="invalid_smiles")
    if len(text) > _MAX_SMILES:
        raise MoleCodeStructureError("SMILES exceeds the supported length.", code="invalid_smiles")
    if any(ch.isspace() for ch in text):
        raise MoleCodeStructureError("SMILES must be a single token without whitespace.", code="invalid_smiles")
    with _quiet_chem():
        mol = Chem.MolFromSmiles(text)
    if mol is None:
        raise MoleCodeStructureError(f"RDKit could not parse SMILES: {text}", code="invalid_smiles")
    return mol


def _require_mermaid(mermaid: str) -> str:
    text = (mermaid or "").strip()
    if not text:
        raise MoleCodeStructureError("MoleCode graph is empty.", code="invalid_graph")
    if len(text) > _MAX_MERMAID:
        raise MoleCodeStructureError("MoleCode graph exceeds the supported length.", code="invalid_graph")
    return text


def _content_lines(mermaid: str) -> list[str]:
    lines: list[str] = []
    for raw in mermaid.splitlines():
        line = raw.strip()
        if not line or line == "end" or line.startswith(("%%", "graph ", "subgraph ")):
            continue
        lines.append(line)
    return lines


def _declarations(mermaid: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    atoms: dict[str, str] = {}
    bonds: list[tuple[str, str]] = []
    for line in _content_lines(mermaid):
        stereo = _STEREO_BOND.search(line)
        if stereo:
            bonds.append((stereo.group(1), stereo.group(3)))
            continue
        bond = _BOND.search(line)
        if bond:
            bonds.append((bond.group(1), bond.group(3)))
            continue
        atom = _ATOM.search(line)
        if not atom:
            continue
        base, chirality, label = atom.group(1), atom.group(2), atom.group(3)
        atom_id = f"{base}_{chirality}" if chirality else base
        atoms.setdefault(atom_id, label.strip())
    return atoms, bonds


def _known_element(label: str) -> bool:
    match = _LABEL.match(label.strip())
    if not match:
        return False
    element = match.group(1)
    try:
        atomic_num = Chem.Atom(element).GetAtomicNum()
    except Exception:
        return False
    return atomic_num > 0


def _reject_graph_issues(mermaid: str) -> None:
    atoms, bonds = _declarations(mermaid)
    if not atoms:
        raise MoleCodeStructureError(
            "MoleCode graph has no atom nodes. Expected declarations such as Mol_C_1[CH3] inside a graph TB or graph LR block.",
            code="invalid_graph",
        )
    bad = [f"{atom_id}[{label}]" for atom_id, label in atoms.items() if not _known_element(label)]
    if bad:
        shown = ", ".join(bad[:8])
        raise MoleCodeStructureError(
            f"MoleCode graph contains unrecognized atom labels: {shown}.",
            code="invalid_atom",
        )
    seen: set[tuple[str, str]] = set()
    for left, right in bonds:
        if left not in atoms or right not in atoms:
            missing = left if left not in atoms else right
            raise MoleCodeStructureError(
                f"MoleCode graph bond references undeclared atom '{missing}'.",
                code="invalid_graph",
            )
        if left == right:
            raise MoleCodeStructureError(
                f"MoleCode graph contains a self-bond on '{left}'.",
                code="invalid_graph",
            )
        key = tuple(sorted((left, right)))
        if key in seen:
            raise MoleCodeStructureError(
                f"MoleCode graph repeats a bond between '{left}' and '{right}'.",
                code="invalid_graph",
            )
        seen.add(key)


def _parse_molecode(mermaid: str) -> Chem.Mol | None:
    with _quiet_chem():
        try:
            return mc_mermaid_to_mol(mermaid, strict=True)
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] or exc.__class__.__name__
            raise MoleCodeStructureError(
                f"MoleCode graph could not be built: {message}",
                code="invalid_graph",
            ) from exc


def mermaid_to_mol(mermaid: str) -> Chem.Mol:
    """Parse a MoleCode Mermaid graph into a sanitized RDKit molecule.

    Raises MoleCodeStructureError with ``code`` set when the graph is empty,
    references unknown atoms, or fails RDKit sanitization.
    """
    text = _require_mermaid(mermaid)
    _reject_graph_issues(text)
    raw = _parse_molecode(text)
    if raw is None or has_invalid_atoms(raw):
        raise MoleCodeStructureError(
            "MoleCode graph could not be parsed into an RDKit molecule.",
            code="invalid_graph",
        )
    try:
        with _quiet_chem():
            sanitized = Chem.Mol(raw)
            Chem.SanitizeMol(sanitized)
    except Exception as exc:
        message = str(exc).strip().splitlines()[0] or exc.__class__.__name__
        code = "invalid_valence" if "valence" in message.lower() else "rdkit_invalid"
        raise MoleCodeStructureError(
            f"RDKit rejected the MoleCode graph: {message}",
            code=code,
        ) from exc
    return sanitized


def mermaid_to_smiles(mermaid: str) -> str:
    """Return canonical SMILES for a MoleCode graph after RDKit validation."""
    mol = mermaid_to_mol(mermaid)
    smiles = mc_mol_to_smiles(mol)
    if not smiles:
        raise MoleCodeStructureError(
            "RDKit produced no SMILES for the MoleCode graph.",
            code="invalid_graph",
        )
    return smiles


def smiles_to_mermaid(smiles: str, name: str = "Molecule", *, kekulize: bool = True) -> str:
    """Convert SMILES to a MoleCode Mermaid graph and check the round trip."""
    mol = _mol_from_smiles(smiles)
    graph_name = _clean_name(name)
    with _quiet_chem():
        graph = mc_mol_to_mermaid(mol, name=graph_name, kekulize=kekulize)
    back = mermaid_to_mol(graph)
    original = Chem.MolToSmiles(mol, canonical=True)
    recovered = Chem.MolToSmiles(back, canonical=True)
    if original != recovered:
        raise MoleCodeStructureError(
            "MoleCode round-trip did not preserve the RDKit structure.",
            code="round_trip_mismatch",
        )
    return graph


def _inchi_for(mol: Chem.Mol) -> str | None:
    try:
        with _quiet_chem():
            value = mol_to_inchi(mol)
    except Exception:
        return None
    return value or None


def _result(
    *,
    action: str,
    name: str,
    smiles: str,
    mermaid: str,
    mol: Chem.Mol,
    input_smiles: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": "structure.molecode",
        "action": action,
        "name": name,
        "input_smiles": input_smiles,
        "smiles": smiles,
        "canonical_smiles": smiles,
        "mermaid": mermaid,
        "inchi": _inchi_for(mol),
        "rdkit_valid": True,
        "atom_count": mol.GetNumAtoms(),
        "bond_count": mol.GetNumBonds(),
    }


def validate_mermaid(mermaid: str, name: str = "Molecule") -> dict[str, Any]:
    """Validate a MoleCode graph and return canonical SMILES plus the graph."""
    mol = mermaid_to_mol(mermaid)
    smiles = Chem.MolToSmiles(mol, canonical=True)
    return _result(
        action="validate",
        name=_clean_name(name),
        smiles=smiles,
        mermaid=_require_mermaid(mermaid),
        mol=mol,
    )


def run_structure_tool(
    action: str,
    *,
    smiles: str | None = None,
    mermaid: str | None = None,
    name: str = "Molecule",
) -> dict[str, Any]:
    """Agent-callable structure tool.

    ``action`` is ``smiles_to_mermaid``, ``mermaid_to_smiles``, or ``validate``.
    """
    graph_name = _clean_name(name)
    if action == "smiles_to_mermaid":
        if not (smiles or "").strip():
            raise MoleCodeStructureError(
                "smiles is required for action smiles_to_mermaid.",
                code="missing_smiles",
            )
        graph = smiles_to_mermaid(smiles or "", name=graph_name)
        mol = mermaid_to_mol(graph)
        canonical = Chem.MolToSmiles(mol, canonical=True)
        return _result(
            action=action,
            name=graph_name,
            smiles=canonical,
            mermaid=graph,
            mol=mol,
            input_smiles=(smiles or "").strip(),
        )
    if action in ("mermaid_to_smiles", "validate"):
        if not (mermaid or "").strip():
            raise MoleCodeStructureError(
                f"mermaid is required for action {action}.",
                code="missing_graph",
            )
        mol = mermaid_to_mol(mermaid or "")
        canonical = Chem.MolToSmiles(mol, canonical=True)
        payload = _result(
            action=action,
            name=graph_name,
            smiles=canonical,
            mermaid=(mermaid or "").strip(),
            mol=mol,
        )
        return payload
    raise MoleCodeStructureError(
        f"Unsupported structure action: {action}",
        code="unsupported_action",
    )


def mentions_structure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _STRUCTURE_HINTS)


def extract_mermaid(text: str) -> str | None:
    """Return the first MoleCode graph embedded in text, if one is present."""
    if not text:
        return None
    fenced = _FENCE.search(text)
    if fenced and re.search(r"graph\s+(?:TB|LR)\b", fenced.group(1), re.IGNORECASE):
        return fenced.group(1).strip()
    match = _GRAPH.search(text)
    if match:
        return match.group(1).strip()
    return None


def extract_smiles(text: str) -> str | None:
    """Return the longest RDKit-valid SMILES token in text, ignoring Mermaid blocks."""
    if not text:
        return None
    stripped = _FENCE.sub(" ", text)
    stripped = _GRAPH.sub(" ", stripped)
    best: str | None = None
    with _quiet_chem():
        for match in _SMILES_TOKEN.finditer(stripped):
            token = match.group(0)
            if len(token) < 2 or len(token) > _MAX_SMILES:
                continue
            if Chem.MolFromSmiles(token) is None:
                continue
            if best is None or len(token) > len(best):
                best = token
    return best


def name_from_prompt(prompt: str, context: dict[str, Any] | None = None) -> str:
    context = context or {}
    for key in ("name", "molecule_name"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_name(value)
    molecule = context.get("molecule")
    if isinstance(molecule, dict):
        value = molecule.get("name")
        if isinstance(value, str) and value.strip():
            return _clean_name(value)
    match = _NAME.search(prompt or "")
    if match:
        return _clean_name(match.group(1))
    if "阿司匹林" in (prompt or "") or re.search(r"\baspirin\b", prompt or "", re.IGNORECASE):
        return "Aspirin"
    return "Molecule"


def interpret_structure_prompt(prompt: str) -> dict[str, Any]:
    """Run the structure tool against SMILES or a MoleCode graph found in text."""
    graph = extract_mermaid(prompt)
    chosen = name_from_prompt(prompt)
    if graph:
        return run_structure_tool("validate", mermaid=graph, name=chosen)
    smiles = extract_smiles(prompt)
    if smiles:
        return run_structure_tool("smiles_to_mermaid", smiles=smiles, name=chosen)
    raise MoleCodeStructureError(
        "No SMILES or MoleCode graph was found. Provide a SMILES string, or paste a graph TB / graph LR MoleCode block.",
        code="missing_structure",
    )


def _context_value(context: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value
    molecule = context.get("molecule")
    if isinstance(molecule, dict):
        for key in keys:
            value = molecule.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def structure_context_for_discussion(question: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Attach a validated MoleCode graph when a chat is about a molecule structure.

    Returns None when the question is not discussing or editing a structure.
    Validation failures are returned as ``{"ok": False, ...}`` and are not raised,
    so the caller can show the error beside the conversation.
    """
    context = context or {}
    question = question or ""
    mermaid = _context_value(context, "mermaid", "molecode") or extract_mermaid(question)
    smiles = _context_value(context, "smiles")
    if smiles is None and mentions_structure(question):
        smiles = extract_smiles(question)
    if mermaid is None and smiles is None:
        return None
    try:
        if mermaid:
            return run_structure_tool("validate", mermaid=mermaid, name=name_from_prompt(question, context))
        return run_structure_tool(
            "smiles_to_mermaid",
            smiles=smiles or "",
            name=name_from_prompt(question, context),
        )
    except MoleCodeStructureError as exc:
        return {"ok": False, "tool": "structure.molecode", "code": exc.code, "message": str(exc)}


def llm_configured() -> bool:
    """True when the existing LLM_* settings can drive molecode.llm."""
    provider = os.getenv("LLM_PROVIDER", "none").strip().lower()
    return provider not in ("", "none") and bool(os.getenv("LLM_API_KEY", "").strip()) and bool(
        os.getenv("LLM_BASE_URL", "").strip()
    )


def llm_client_from_env():
    """Build ``molecode.LLMClient`` from LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL.

    Returns None when ``LLM_PROVIDER`` is ``none`` or the key/base URL is blank.
    This does not make a network call. Structure conversion does not need it.
    """
    if not llm_configured():
        return None
    from molecode import LLMClient

    base = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    model = os.getenv("LLM_MODEL", "").strip() or None
    return LLMClient(api_key=os.getenv("LLM_API_KEY", "").strip(), base_url=base, model=model)
