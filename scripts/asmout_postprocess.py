#!/usr/bin/env python3
"""Postprocess MADS asmout into other assembler dialects.

The script is intentionally standalone so dialect-specific output experiments can
evolve independently of MadMads itself.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


TOP_LEVEL_SYMBOL_RE = re.compile(r"^([@A-Za-z?_][@A-Za-z0-9?._]*)(?=\s*(?:=|$))")
IDENTIFIER_RE = re.compile(r"[@A-Za-z?_][@A-Za-z0-9?._]*")
ACCUMULATOR_SHIFT_RE = re.compile(r"^(\s*)(ROR|ROL|LSR|ASL)(\s*)$", re.IGNORECASE)
ACCUMULATOR_AT_SHIFT_RE = re.compile(r"^(\s*)(ROR|ROL|LSR|ASL)\s+@(\s*)$", re.IGNORECASE)
ORG_RE = re.compile(r"^(\s*)ORG\b\s*(.*)$", re.IGNORECASE)
OPT_RE = re.compile(r"^(\s*)OPT\b.*$", re.IGNORECASE)
LITERAL_EXPR_RE = re.compile(r"^\s*(?:\$[0-9A-Fa-f]+|%[01]+|\d+)\s*$")
BYTE_DIRECTIVE_RE = re.compile(r"^(\s*)\.BYTE\s+(.*)$", re.IGNORECASE)
BINARY_LITERAL_RE = re.compile(r"(?<![A-Za-z0-9?._@])%([01]+)")
CHAR_IMMEDIATE_RE = re.compile(r'#"([^"\\])"')
NEGATIVE_IMMEDIATE_RE = re.compile(r'#-([0-9]+)\b')
LITERAL_EQUATE_RE = re.compile(r"^([@A-Za-z?_][@A-Za-z0-9?._]*)\s*=\s*([^;]+?)\s*$")
LOW_PAGE_INDEXED_LITERAL_RE = re.compile(
    r"^(\s*)(ADC|AND|ASL|CMP|DEC|EOR|INC|LDA|LDX|LDY|LSR|ORA|ROL|ROR|SBC|STA|STX|STY)\s+\$([0-9A-Fa-f]{1,4})\s*,\s*([XxYy])\s*$",
    re.IGNORECASE,
)
SYMBOL_PLUS_LITERAL_RE = re.compile(
    r"^([@A-Za-z?_][@A-Za-z0-9?._]*)(?:\s*([+-])\s*(\$[0-9A-Fa-f]+|%[01]+|\d+))?$"
)
OMC_INDEXED_INSTRUCTION_RE = re.compile(
    r"^(\s*)(ADC|AND|ASL|CMP|DEC|EOR|INC|LDA|LDX|LDY|LSR|ORA|ROL|ROR|SBC|STA|STX|STY)\s+([^,]+?)\s*,\s*([XxYy])\s*$",
    re.IGNORECASE,
)

OMC_ZERO_PAGE_X_OPCODE_MAP = {
    "ADC": 0x75,
    "AND": 0x35,
    "ASL": 0x16,
    "CMP": 0xD5,
    "DEC": 0xD6,
    "EOR": 0x55,
    "INC": 0xF6,
    "LDA": 0xB5,
    "LDY": 0xB4,
    "LSR": 0x56,
    "ORA": 0x15,
    "ROL": 0x36,
    "ROR": 0x76,
    "SBC": 0xF5,
    "STA": 0x95,
    "STY": 0x94,
}

OMC_ZERO_PAGE_Y_OPCODE_MAP = {
    "LDX": 0xB6,
    "STX": 0x96,
}

OMC_ABSOLUTE_Y_OPCODE_MAP = {
    "ADC": 0x79,
    "AND": 0x39,
    "CMP": 0xD9,
    "EOR": 0x59,
    "LDA": 0xB9,
    "LDX": 0xBE,
    "ORA": 0x19,
    "SBC": 0xF9,
    "STA": 0x99,
}


@dataclass(frozen=True)
class BackendSpec:
    name: str
    description: str
    org_keyword: str = "ORG"
    reserve_keyword: str | None = None
    explicit_accumulator: bool = False
    rewrite_binary_literals: bool = False
    rewrite_char_immediates: bool = False
    rewrite_negative_byte_immediates: bool = False
    wrap_byte_line_length: int | None = None
    require_label_colon: bool = False
    drop_opt_directives: bool = False
    rename_unsafe_symbols: bool = False
    normalize_symbol_case: bool = False
    rewrite_angle_paren_expressions: bool = False
    fold_literal_equates_in_org: bool = False
    fold_constant_org_arithmetic: bool = False
    hoist_zero_page_equates: bool = False
    symbol_is_safe: Callable[[str], bool] | None = None
    symbol_encoder: Callable[[str], str] | None = None


@dataclass(frozen=True)
class RewriteContext:
    symbol_map: dict[str, str]
    literal_equates: dict[str, str]


@dataclass(frozen=True)
class RewriteResult:
    lines: list[str]
    symbol_map: dict[str, str]
    wrapped_byte_lines: int


def encode_unsafe_symbol(name: str) -> str:
    encoded = name.encode("utf-8").hex().upper()
    return f"ZX{encoded}"


def omc_symbol_is_safe(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z?][A-Za-z0-9?.]*", name) is not None


def ca65_symbol_is_safe(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None


def encode_ca65_symbol(name: str) -> str:
    encoded = name.encode("utf-8").hex().upper()
    return f"CA65_{encoded}"


BACKENDS: dict[str, BackendSpec] = {
    "omc": BackendSpec(
        name="omc",
        description="OMC / MAC-65 style output with stricter symbol rules and *= origin syntax.",
        org_keyword="*=",
        explicit_accumulator=True,
        rewrite_binary_literals=True,
        rewrite_char_immediates=True,
        wrap_byte_line_length=100,
        rename_unsafe_symbols=True,
        rewrite_angle_paren_expressions=True,
        fold_literal_equates_in_org=True,
        fold_constant_org_arithmetic=True,
        hoist_zero_page_equates=True,
        symbol_is_safe=omc_symbol_is_safe,
        symbol_encoder=encode_unsafe_symbol,
    ),
    "ca65": BackendSpec(
        name="ca65",
        description="Conservative ca65-oriented normalization profile with minimal syntax changes.",
        org_keyword=".org",
        reserve_keyword=".res",
        explicit_accumulator=True,
        rewrite_char_immediates=True,
        rewrite_negative_byte_immediates=True,
        require_label_colon=True,
        drop_opt_directives=True,
        rename_unsafe_symbols=True,
        normalize_symbol_case=True,
        fold_literal_equates_in_org=True,
        hoist_zero_page_equates=True,
        symbol_is_safe=ca65_symbol_is_safe,
        symbol_encoder=encode_ca65_symbol,
    ),
}


def split_comment(line: str) -> tuple[str, str]:
    in_quote: str | None = None

    for idx, ch in enumerate(line):
        if in_quote is not None:
            if ch == in_quote:
                in_quote = None
            continue

        if ch in {'"', "'"}:
            in_quote = ch
            continue

        if ch == ';':
            return line[:idx], line[idx:]

    return line, ""


def build_symbol_map(lines: Iterable[str], backend: BackendSpec) -> dict[str, str]:
    if not backend.rename_unsafe_symbols and not backend.normalize_symbol_case:
        return {}

    mapping: dict[str, str] = {}
    for line in lines:
        if not line or line[:1] in {" ", "\t", ";"}:
            continue

        match = TOP_LEVEL_SYMBOL_RE.match(line)
        if not match:
            continue

        name = match.group(1)
        emitted_name = name

        if backend.rename_unsafe_symbols:
            if backend.symbol_is_safe is None or backend.symbol_encoder is None:
                return {}
            if not backend.symbol_is_safe(name):
                emitted_name = backend.symbol_encoder(name)

        if backend.normalize_symbol_case or emitted_name != name:
            mapping[name.upper()] = emitted_name

    return mapping


def build_literal_equates(lines: Iterable[str]) -> dict[str, str]:
    equates: dict[str, str] = {}

    for line in lines:
        if not line or line[:1] in {" ", "\t", ";"}:
            continue

        match = LITERAL_EQUATE_RE.match(line)
        if not match:
            continue

        name, expr = match.groups()
        expr = expr.strip()
        if LITERAL_EXPR_RE.fullmatch(expr):
            equates[name.upper()] = expr

    return equates


def parse_literal_expr(expr: str) -> int | None:
    expr = expr.strip()
    if expr.startswith("$"):
        return int(expr[1:], 16)
    if expr.startswith("%"):
        return int(expr[1:], 2)
    if expr.isdigit():
        return int(expr, 10)
    return None


def collect_hoisted_zero_page_equates(lines: list[str]) -> tuple[list[str], set[str]]:
    by_name: dict[str, tuple[str, str] | None] = {}
    ordered_names: list[str] = []

    for line in lines:
        if not line or line[:1] in {" ", "\t", ";"}:
            continue

        match = LITERAL_EQUATE_RE.match(line)
        if not match:
            continue

        name, expr = match.groups()
        value = parse_literal_expr(expr)
        if value is None or value > 0xFF:
            continue

        key = name.upper()
        entry = (name, expr.strip())
        if key not in by_name:
            by_name[key] = entry
            ordered_names.append(key)
            continue

        if by_name[key] != entry:
            by_name[key] = None

    hoisted_lines: list[str] = []
    hoisted_names: set[str] = set()
    for key in ordered_names:
        entry = by_name.get(key)
        if entry is None:
            continue
        name, expr = entry
        hoisted_lines.append(f"{name} = {expr}")
        hoisted_names.add(key)

    return hoisted_lines, hoisted_names


def build_context(lines: list[str], backend: BackendSpec) -> RewriteContext:
    literal_equates = build_literal_equates(lines) if backend.fold_literal_equates_in_org else {}
    return RewriteContext(
        symbol_map=build_symbol_map(lines, backend),
        literal_equates=literal_equates,
    )


def rewrite_code_segment(code: str, symbol_map: dict[str, str]) -> str:
    if not symbol_map:
        return code

    out: list[str] = []
    idx = 0
    length = len(code)

    while idx < length:
        ch = code[idx]

        if ch in {'"', "'"}:
            quote = ch
            out.append(ch)
            idx += 1
            while idx < length:
                out.append(code[idx])
                if code[idx] == quote:
                    idx += 1
                    break
                idx += 1
            continue

        match = IDENTIFIER_RE.match(code, idx)
        if match:
            token = match.group(0)
            out.append(symbol_map.get(token.upper(), token))
            idx = match.end()
            continue

        out.append(ch)
        idx += 1

    return "".join(out)


def rewrite_org_expression(expr: str, literal_equates: dict[str, str]) -> str:
    if not literal_equates:
        return expr

    out: list[str] = []
    idx = 0
    length = len(expr)

    while idx < length:
        match = IDENTIFIER_RE.match(expr, idx)
        if match:
            token = match.group(0)
            out.append(literal_equates.get(token.upper(), token))
            idx = match.end()
            continue

        out.append(expr[idx])
        idx += 1

    return "".join(out)


def fold_constant_org_expression(expr: str) -> str | None:
    normalized = expr.strip()
    if normalized == "":
        return None

    def replace_literal(match: re.Match[str]) -> str:
        token = match.group(0)
        value = parse_literal_expr(token)
        if value is None:
            return token
        return str(value)

    python_expr = re.sub(r"\$[0-9A-Fa-f]+|%[01]+|\d+", replace_literal, normalized)
    if re.search(r"[^0-9\s()+\-*/]", python_expr):
        return None

    try:
        value = eval(python_expr, {"__builtins__": {}}, {})
    except Exception:
        return None

    if not isinstance(value, int):
        return None
    if value < 0:
        return str(value)
    return f"${value:X}"


def rewrite_binary_literals(code: str) -> str:
    def repl(match: re.Match[str]) -> str:
        bits = match.group(1)
        return f"${int(bits, 2):X}"

    return BINARY_LITERAL_RE.sub(repl, code)


def rewrite_angle_paren_expressions(code: str) -> str:
    out: list[str] = []
    idx = 0
    length = len(code)
    in_quote: str | None = None

    while idx < length:
        ch = code[idx]

        if in_quote is not None:
            out.append(ch)
            if ch == in_quote:
                in_quote = None
            idx += 1
            continue

        if ch in {'"', "'"}:
            in_quote = ch
            out.append(ch)
            idx += 1
            continue

        if ch in "<>" and idx + 1 < length and code[idx + 1] == "(":
            out.append(ch)
            out.append("[")
            idx += 2
            depth = 1
            while idx < length:
                inner = code[idx]
                if inner == "(":
                    depth += 1
                    out.append(inner)
                    idx += 1
                    continue
                if inner == ")":
                    depth -= 1
                    out.append("]" if depth == 0 else inner)
                    idx += 1
                    if depth == 0:
                        break
                    continue
                out.append(inner)
                idx += 1
            continue

        out.append(ch)
        idx += 1

    return "".join(out)


def convert_parens_to_brackets(text: str) -> str:
    out: list[str] = []
    in_quote: str | None = None

    for ch in text:
        if in_quote is not None:
            out.append(ch)
            if ch == in_quote:
                in_quote = None
            continue

        if ch in {'"', "'"}:
            in_quote = ch
            out.append(ch)
            continue

        if ch == '(':
            out.append('[')
            continue
        if ch == ')':
            out.append(']')
            continue

        out.append(ch)

    return ''.join(out)


def restore_mac65_indirect_addressing(mnemonic: str, operand: str) -> str:
    restored = re.sub(
        r"(^|\s)\[([^\[\]]+?)\]\s*,\s*([Yy])\b",
        lambda match: f"{match.group(1)}({match.group(2)}),{match.group(3)}",
        operand,
    )
    restored = re.sub(
        r"(^|\s)\[([^\[\]]+?)\s*,\s*([Xx])\]",
        lambda match: f"{match.group(1)}({match.group(2)},{match.group(3)})",
        restored,
    )

    if mnemonic.upper() == "JMP":
        match = re.fullmatch(r"(\s*)\[([^\[\]]+)\](\s*)", restored)
        if match:
            leading, inner, trailing = match.groups()
            restored = f"{leading}({inner}){trailing}"

    return restored


def rewrite_mac65_expression_parentheses(code: str) -> str:
    match = re.match(r"^(\s+)([A-Za-z.][A-Za-z0-9._]*)(.*)$", code)
    if not match:
        return convert_parens_to_brackets(code)

    indent, mnemonic, operand = match.groups()
    operand = convert_parens_to_brackets(operand)
    operand = restore_mac65_indirect_addressing(mnemonic, operand)
    return f"{indent}{mnemonic}{operand}"


def atascii_to_internal_char(value: int) -> int:
    if 0x20 <= value <= 0x5F:
        return value - 0x20
    if 0x00 <= value <= 0x1F:
        return value + 0x40
    return value


def rewrite_char_immediates(code: str) -> str:
    def repl(match: re.Match[str]) -> str:
        value = atascii_to_internal_char(ord(match.group(1)))
        return f"#$%02X" % value

    return CHAR_IMMEDIATE_RE.sub(repl, code)


def rewrite_negative_byte_immediates(code: str) -> str:
    def repl(match: re.Match[str]) -> str:
        value = int(match.group(1))
        return f"#$%02X" % ((-value) & 0xFF)

    return NEGATIVE_IMMEDIATE_RE.sub(repl, code)


def normalize_indexed_operand_spacing(code: str) -> str:
    return re.sub(r",\s+([XxYy])\b", r",\1", code)


def resolve_emitted_literal(token: str, context: RewriteContext) -> int | None:
    value_text = context.literal_equates.get(token.upper())
    if value_text is not None:
        return parse_literal_expr(value_text)

    for original_name, emitted_name in context.symbol_map.items():
        if emitted_name.upper() != token.upper():
            continue
        value_text = context.literal_equates.get(original_name)
        if value_text is not None:
            return parse_literal_expr(value_text)

    return None


def resolve_emitted_address_expr(expr: str, context: RewriteContext) -> int | None:
    match = SYMBOL_PLUS_LITERAL_RE.fullmatch(expr.strip())
    if not match:
        return None

    token, operator, offset_text = match.groups()
    base = resolve_emitted_literal(token, context)
    if base is None:
        return None

    if operator is None:
        return base

    offset = parse_literal_expr(offset_text)
    if offset is None:
        return None

    if operator == '+':
        return (base + offset) & 0xFFFF

    return (base - offset) & 0xFFFF


def parse_omc_indexed_operand(code: str, context: RewriteContext) -> tuple[str, str, str, int | None] | None:
    match = LOW_PAGE_INDEXED_LITERAL_RE.match(code)
    if match:
        indent, mnemonic, address_text, index_register = match.groups()
        return indent, mnemonic.upper(), index_register.upper(), int(address_text, 16)

    symbolic_match = OMC_INDEXED_INSTRUCTION_RE.match(code)
    if not symbolic_match:
        return None

    indent, mnemonic, expr, index_register = symbolic_match.groups()
    return indent, mnemonic.upper(), index_register.upper(), resolve_emitted_address_expr(expr, context)


def emit_omc_low_page_indexed(indent: str, mnemonic: str, index_register: str, address: int) -> str | None:
    if index_register == "X":
        opcode = OMC_ZERO_PAGE_X_OPCODE_MAP.get(mnemonic)
        if opcode is None:
            return None
        return f"{indent}.BYTE ${opcode:02X},${address & 0xFF:02X}"

    opcode = OMC_ZERO_PAGE_Y_OPCODE_MAP.get(mnemonic)
    if opcode is not None:
        return f"{indent}.BYTE ${opcode:02X},${address & 0xFF:02X}"

    opcode = OMC_ABSOLUTE_Y_OPCODE_MAP.get(mnemonic)
    if opcode is None:
        return None

    return f"{indent}.BYTE ${opcode:02X},${address & 0xFF:02X},$00"


def rewrite_omc_low_page_indexed(code: str, context: RewriteContext) -> str:
    parsed = parse_omc_indexed_operand(code, context)
    if parsed is None:
        return code

    indent, mnemonic, index_register, address = parsed

    if address is None or address > 0xFF:
        return code

    emitted = emit_omc_low_page_indexed(indent, mnemonic, index_register, address)
    if emitted is None:
        return code

    return emitted


def rewrite_bare_label(code: str, backend: BackendSpec) -> str:
    stripped = code.strip()
    if backend.name == "omc":
        if code[:1] not in {' ', '\t'} and stripped and ' ' not in stripped and '\t' not in stripped:
            if not stripped.startswith('.') and '=' not in stripped and ':' not in stripped:
                return f"{stripped} = *"

    if not backend.require_label_colon:
        return code

    if code[:1] in {' ', '\t'}:
        return code

    if stripped == "":
        return code
    if any(ch.isspace() for ch in stripped):
        return code
    if stripped.startswith('.'):
        return code
    if '=' in stripped or ':' in stripped:
        return code

    return code + ':'


def rewrite_line(line: str, backend: BackendSpec, context: RewriteContext) -> str:
    code, comment = split_comment(line)

    if backend.drop_opt_directives and OPT_RE.match(code):
        return comment.lstrip() if comment else ""

    org_match = ORG_RE.match(code)
    if org_match:
        indent, expr = org_match.groups()
        expr = rewrite_org_expression(expr.lstrip(), context.literal_equates)

        relative_match = re.fullmatch(r"\*\s*\+\s*(.+)", expr)
        if relative_match and backend.fold_constant_org_arithmetic:
            folded_offset = fold_constant_org_expression(relative_match.group(1))
            if folded_offset is not None:
                expr = f"* + {folded_offset}"
                relative_match = re.fullmatch(r"\*\s*\+\s*(.+)", expr)

        if relative_match and backend.reserve_keyword is not None:
            code = f"{indent}{backend.reserve_keyword} {relative_match.group(1)}"
        elif backend.org_keyword != "ORG":
            if backend.fold_constant_org_arithmetic and relative_match is None:
                folded_expr = fold_constant_org_expression(expr)
                if folded_expr is not None:
                    expr = folded_expr
            code = f"{indent}{backend.org_keyword} {expr}"

    if backend.explicit_accumulator:
        at_shift_match = ACCUMULATOR_AT_SHIFT_RE.match(code)
        if at_shift_match:
            indent, mnemonic, trailing = at_shift_match.groups()
            code = f"{indent}{mnemonic.upper()} A{trailing}"

        shift_match = ACCUMULATOR_SHIFT_RE.match(code)
        if shift_match:
            indent, mnemonic, trailing = shift_match.groups()
            code = f"{indent}{mnemonic.upper()} A{trailing}"

    code = rewrite_code_segment(code, context.symbol_map)
    code = rewrite_bare_label(code, backend)

    if backend.rewrite_angle_paren_expressions:
        code = rewrite_angle_paren_expressions(code)
        code = rewrite_mac65_expression_parentheses(code)
    if backend.rewrite_binary_literals:
        code = rewrite_binary_literals(code)
    if backend.rewrite_char_immediates:
        code = rewrite_char_immediates(code)
    if backend.rewrite_negative_byte_immediates:
        code = rewrite_negative_byte_immediates(code)
    if backend.name == "omc":
        code = normalize_indexed_operand_spacing(code)
        code = rewrite_omc_low_page_indexed(code, context)

    return code + comment


def wrap_byte_line(line: str, max_length: int | None) -> list[str]:
    if max_length is None:
        return [line]

    code, comment = split_comment(line)
    match = BYTE_DIRECTIVE_RE.match(code)
    if not match or len(line) <= max_length:
        return [line]

    indent, items_text = match.groups()
    items = [item.strip() for item in items_text.split(',') if item.strip()]
    if not items:
        return [line]

    prefix = f"{indent}.BYTE "
    wrapped: list[str] = []
    current: list[str] = []

    for item in items:
        candidate = prefix + ", ".join(current + [item])
        if current and len(candidate) > max_length:
            wrapped.append(prefix + ", ".join(current))
            current = [item]
        else:
            current.append(item)

    if current:
        wrapped.append(prefix + ", ".join(current))

    if comment:
        if len(wrapped[0] + comment) <= max_length:
            wrapped[0] = wrapped[0] + comment
        else:
            wrapped.insert(0, indent + comment)

    return wrapped


def rewrite_lines(lines: list[str], backend: BackendSpec) -> RewriteResult:
    context = build_context(lines, backend)
    rewritten: list[str] = []
    wrapped_byte_lines = 0
    hoisted_zero_page_lines: list[str] = []
    hoisted_zero_page_names: set[str] = set()

    if backend.hoist_zero_page_equates:
        hoisted_zero_page_lines, hoisted_zero_page_names = collect_hoisted_zero_page_equates(lines)
        rewritten.extend(rewrite_line(line, backend, context) for line in hoisted_zero_page_lines)

    for original_line in lines:
        if hoisted_zero_page_names:
            match = LITERAL_EQUATE_RE.match(original_line)
            if match and match.group(1).upper() in hoisted_zero_page_names:
                continue

        line = rewrite_line(original_line, backend, context)
        wrapped_lines = wrap_byte_line(line, backend.wrap_byte_line_length)
        if len(wrapped_lines) > 1:
            wrapped_byte_lines += len(wrapped_lines) - 1
        rewritten.extend(wrapped_lines)

    return RewriteResult(
        lines=rewritten,
        symbol_map=context.symbol_map,
        wrapped_byte_lines=wrapped_byte_lines,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rewrite MADS asmout into another assembler dialect.")
    parser.add_argument("input", nargs="?", help="Path to input asmout file")
    parser.add_argument("output", nargs="?", help="Path to rewritten output file")
    parser.add_argument(
        "--dialect",
        default="omc",
        choices=sorted(BACKENDS),
        help="Target dialect profile to emit (default: omc)",
    )
    parser.add_argument(
        "--list-dialects",
        action="store_true",
        help="List available dialect profiles and exit",
    )
    parser.add_argument(
        "--map-file",
        help="Optional path to write a generated symbol mapping report",
    )
    args = parser.parse_args()

    if args.list_dialects:
        return args

    if not args.input or not args.output:
        parser.error("input and output are required unless --list-dialects is used")

    return args


def write_map_file(path: Path, symbol_map: dict[str, str]) -> None:
    lines = [f"{src} -> {dst}" for src, dst in sorted(symbol_map.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def list_dialects() -> int:
    for backend in BACKENDS.values():
        print(f"{backend.name}: {backend.description}")
    return 0


def main() -> int:
    args = parse_args()
    if args.list_dialects:
        return list_dialects()

    backend = BACKENDS[args.dialect]
    input_path = Path(args.input)
    output_path = Path(args.output)
    source_text = input_path.read_text(encoding="utf-8", errors="replace")
    lines = source_text.splitlines()

    rewrite_result = rewrite_lines(lines, backend)

    output_text = "\n".join(rewrite_result.lines)
    if source_text.endswith("\n"):
        output_text += "\n"

    output_path.write_text(output_text, encoding="utf-8")

    if args.map_file:
        write_map_file(Path(args.map_file), rewrite_result.symbol_map)

    print(
        f"dialect={backend.name} lines={len(lines)} renamed={len(rewrite_result.symbol_map)} "
        f"wrapped={rewrite_result.wrapped_byte_lines}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())