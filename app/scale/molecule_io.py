"""Streaming molecule-file reader and chunk splitter for large libraries."""
import csv
from pathlib import Path
from typing import Iterator

SUPPORTED = {".csv", ".smi", ".smiles", ".txt", ".sdf"}

def iter_smiles(path: str | Path) -> Iterator[dict[str, str]]:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".csv":
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV 缺少表头。至少需要 smiles 列；name 列可选。")
            columns = {str(k).strip().lower(): k for k in reader.fieldnames if k}
            smiles_col = next((columns[k] for k in ("smiles", "canonical_smiles", "isomeric_smiles", "structure") if k in columns), None)
            name_col = next((columns[k] for k in ("name", "id", "compound_id", "molecule_id", "title") if k in columns), None)
            if not smiles_col:
                raise ValueError("CSV 未找到 smiles/canonical_smiles/structure 列。")
            for i, row in enumerate(reader, start=1):
                s = (row.get(smiles_col) or "").strip()
                yield {"name": (row.get(name_col) or f"compound_{i}").strip() if name_col else f"compound_{i}", "smiles": s}
    elif ext in {".smi", ".smiles", ".txt"}:
        with p.open("r", encoding="utf-8-sig") as f:
            for i, line in enumerate(f, start=1):
                t = line.strip()
                if not t or t.startswith("#"): continue
                parts = t.split()
                if i == 1 and parts[0].lower() in {"smiles", "canonical_smiles"}: continue
                yield {"name": parts[1] if len(parts) > 1 else f"compound_{i}", "smiles": parts[0]}
    elif ext == ".sdf":
        try:
            from rdkit import Chem
        except ImportError as e:
            raise RuntimeError("读取 SDF 需要 RDKit。请在计算 worker 环境安装 RDKit。") from e
        supplier = Chem.SDMolSupplier(str(p), removeHs=False)
        for i, mol in enumerate(supplier, start=1):
            if mol is None:
                yield {"name": f"invalid_sdf_record_{i}", "smiles": ""}
                continue
            name = mol.GetProp("_Name") if mol.HasProp("_Name") else f"compound_{i}"
            yield {"name": name, "smiles": Chem.MolToSmiles(Chem.RemoveHs(mol))}
    else:
        raise ValueError(f"不支持的分子库格式：{ext}。支持 CSV、SMI、SMILES、TXT、SDF。")

def split_molecule_file(path: str | Path, out_dir: str | Path, chunk_size: int) -> list[str]:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    chunks, writer, f, count = [], None, None, 0
    try:
        for item in iter_smiles(path):
            if not f or count >= chunk_size:
                if f: f.close()
                chunk_path = out / f"input_{len(chunks):05d}.csv"
                f = chunk_path.open("w", encoding="utf-8", newline="")
                writer = csv.DictWriter(f, fieldnames=["name", "smiles"])
                writer.writeheader(); chunks.append(str(chunk_path)); count = 0
            writer.writerow(item); count += 1
    finally:
        if f: f.close()
    if not chunks:
        raise ValueError("分子文件为空，或没有可识别的 SMILES 记录。")
    return chunks

def count_csv_data_rows(path: str | Path) -> int:
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return max(0, sum(1 for _ in f) - 1)
