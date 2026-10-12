"""Статическая проверка: ссылки на необъявленные глобальные имена (ловит опечатки без запуска pygame).
Запуск: python tools/check_names.py"""
from __future__ import annotations

import ast
import builtins
import symtable
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def walk(t: symtable.SymbolTable, known: set[str], path: Path, out: list[str]) -> None:
    for sym in t.get_symbols():
        if sym.is_global() and sym.is_referenced() and not sym.is_assigned() and sym.get_name() not in known:
            out.append(f"{path.relative_to(SRC)}: имя «{sym.get_name()}» нигде не определено ({t.get_name()})")
    for ch in t.get_children():
        walk(ch, known, path, out)


def main() -> int:
    out: list[str] = []
    for p in sorted(SRC.rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text)
        walk(symtable.symtable(text, str(p), "exec"), module_names(tree), p, out)
    for line in out:
        print(line)
    print("проверено файлов:", len(list(SRC.rglob('*.py'))), "| проблем:", len(out))
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
