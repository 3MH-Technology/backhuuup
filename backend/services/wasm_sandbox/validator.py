"""AST-based source validator.

SECURITY NOTE: This is defense-in-depth ONLY. The primary security boundary
is the WASM runtime itself. The Python WASM module does NOT include dangerous
modules (os, subprocess, socket, ctypes, etc.). This validator catches only
trivial static patterns and cannot detect dynamically constructed code.
"""
import ast
import logging
import re

from .policy import RE_CONTROL_CHARS, SealedPolicy

logger = logging.getLogger("wolfhost.wasm.validator")

DENIED_IMPORTS = frozenset({
    "os", "subprocess", "socket", "ctypes", "signal",
    "multiprocessing", "threading", "inspect", "importlib",
    "pkgutil", "runpy", "code", "codeop", "compileall",
    "py_compile", "zipimport", "mmap", "syscall", "ptrace",
    "fcntl", "termios", "tty", "pty", "grp", "pwd", "spwd",
    "crypt", "curses", "dbm", "sqlite3", "telnetlib",
    "ftplib", "smtplib", "poplib", "imaplib", "nntplib",
    "socketserver", "xmlrpc", "cgi", "cgitb",
    "webbrowser", "antigravity",
})

DENIED_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__"})

DENIED_ATTR_PREFIXES = (
    "builtins.eval", "builtins.exec", "builtins.compile",
    "builtins.__import__", "builtins.open",
    "os.", "subprocess.", "socket.", "ctypes.",
)

DANGEROUS_PATTERNS = [
    re.compile(r"\b__import__\s*\("),
    re.compile(r"\bgetattr\s*\(\s*(__builtins__|builtins|globals|locals)"),
    re.compile(r"\bglobals\s*\(\s*\)"),
    re.compile(r"\blocals\s*\(\s*\)"),
    re.compile(r"\bvars\s*\(\s*\)"),
    re.compile(r"\b__subclasses__\s*\(\)"),
    re.compile(r"\bsys\.modules\b"),
]


class ValidationError(Exception):
    pass


def validate_source(code: str, policy: SealedPolicy | None = None) -> str:
    """Validate Python source. Returns cleaned code or raises."""
    if policy is None:
        policy = SealedPolicy(bot_id="__validator__", user_id=0)

    encoded = code.encode("utf-8")
    if len(encoded) > policy.max_source_bytes:
        raise ValidationError(f"Source exceeds {policy.max_source_bytes // 1024} KB")

    code = RE_CONTROL_CHARS.sub("", code)

    for pat in DANGEROUS_PATTERNS:
        if pat.search(code):
            raise ValidationError(f"Blocked pattern: {pat.pattern[:40]}")

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValidationError(f"Syntax: {e}")

    denied = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in DENIED_IMPORTS:
                    denied.append(f"import {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in DENIED_IMPORTS:
                    denied.append(f"from {node.module}")

        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in DENIED_CALL_NAMES:
                    denied.append(f"call {node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                full = _resolve_attr(node.func)
                for prefix in DENIED_ATTR_PREFIXES:
                    if full.startswith(prefix):
                        denied.append(f"call {full}")

    depth = _max_nesting_depth(tree)
    if depth > policy.max_ast_nesting_depth:
        denied.append(f"nesting {depth} > {policy.max_ast_nesting_depth}")

    if denied:
        raise ValidationError("; ".join(denied[:10]))

    return code


def _resolve_attr(node: ast.Attribute) -> str:
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _max_nesting_depth(tree: ast.Module) -> int:
    max_d = 0
    targets = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
               ast.If, ast.For, ast.While, ast.Try, ast.With)

    def walk(n, d):
        nonlocal max_d
        max_d = max(max_d, d)
        for c in ast.iter_child_nodes(n):
            walk(c, d + 1 if isinstance(c, targets) else d)

    walk(tree, 0)
    return max_d
