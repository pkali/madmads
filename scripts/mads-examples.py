#!/usr/bin/env python3
"""Run configurable MADS example regression checks."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExampleCase:
    id: str
    source: Path
    cwd: Path
    mode: str
    output: str
    asmout: str | None = None
    mads_args: tuple[str, ...] = ()
    expect_stage: str | None = None
    expect_message: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run configurable MADS example regression checks.")
    parser.add_argument(
        "--config",
        default="scripts/mads-examples.json",
        help="Path to the example regression config file.",
    )
    parser.add_argument(
        "--example",
        action="append",
        dest="examples",
        help="Run only the named example id. May be passed multiple times.",
    )
    parser.add_argument(
        "--artifact-root",
        default="tmp/examples-regression",
        help="Directory for generated artifacts.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip rebuilding Mad-Assembler/mads before running checks.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List configured example ids and exit.",
    )
    return parser.parse_args()


def iter_raw_cases(raw_config: Any) -> list[dict[str, Any]]:
    if isinstance(raw_config, list):
        return list(raw_config)

    if not isinstance(raw_config, dict):
        raise ValueError("config must be a list or an object keyed by mode")

    grouped_cases: list[dict[str, Any]] = []
    for mode_name, entries in raw_config.items():
        if not isinstance(entries, list):
            raise ValueError(f"config group {mode_name!r} must be a list")
        for entry in entries:
            if "mode" not in entry:
                entry = {**entry, "mode": mode_name}
            grouped_cases.append(entry)

    return grouped_cases


def load_cases(config_path: Path, repo_root: Path) -> list[ExampleCase]:
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    raw_cases = iter_raw_cases(raw_config)
    cases: list[ExampleCase] = []

    for raw_case in raw_cases:
        case = ExampleCase(
            id=raw_case["id"],
            source=repo_root / raw_case["source"],
            cwd=repo_root / raw_case.get("cwd", str(Path(raw_case["source"]).parent)),
            mode=raw_case["mode"],
            output=raw_case["output"],
            asmout=raw_case.get("asmout"),
            mads_args=tuple(raw_case.get("mads_args", [])),
            expect_stage=raw_case.get("expect_stage"),
            expect_message=raw_case.get("expect_message"),
        )
        cases.append(case)

    return cases


def run_checked(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def run_capture(command: list[str], cwd: Path, log_path: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    return result


def build_mads(repo_root: Path) -> None:
    print("==> Building MADS")
    run_checked(["fpc", "-Mdelphi", "-vh", "-O3", "mads.pas"], repo_root / "Mad-Assembler")


def assemble_source(case: ExampleCase, mads_bin: Path, out_dir: Path, asmout_path: Path | None) -> tuple[Path, Path]:
    output_path = out_dir / f"orig{Path(case.output).suffix}"
    label_path = out_dir / "orig.lab"
    command = [
        str(mads_bin),
        str(case.source),
        f"-o:{output_path}",
        f"-t:{label_path}",
        *case.mads_args,
    ]
    if asmout_path is not None:
        command.append(f"-A:{asmout_path}")

    run_checked(command, case.cwd)
    return output_path, label_path


def assemble_roundtrip(case: ExampleCase, mads_bin: Path, out_dir: Path, asmout_path: Path) -> tuple[Path, Path]:
    output_path = out_dir / f"roundtrip{Path(case.output).suffix}"
    label_path = out_dir / "roundtrip.lab"
    command = [
        str(mads_bin),
        str(asmout_path),
        f"-o:{output_path}",
        f"-t:{label_path}",
    ]
    run_checked(command, out_dir)
    return output_path, label_path


def expect_failure(case: ExampleCase, stage: str, result: subprocess.CompletedProcess[str]) -> bool:
    if case.mode != "known-failing":
        return False
    if case.expect_stage != stage:
        return False
    if result.returncode == 0:
        return False
    if case.expect_message and case.expect_message not in (result.stdout + result.stderr):
        return False
    return True


def run_case(case: ExampleCase, mads_bin: Path, artifact_root: Path) -> None:
    out_dir = artifact_root / case.id
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"==> Example {case.id} mode={case.mode}")

    asmout_name = case.asmout or f"{case.source.stem}.a65"
    needs_asmout = case.mode == "asmout-roundtrip" or (
        case.mode == "known-failing" and case.expect_stage in {"assemble-roundtrip", "compare"}
    )
    asmout_path = out_dir / asmout_name if needs_asmout else None

    if case.mode == "known-failing":
        orig_output_path = out_dir / f"orig{Path(case.output).suffix}"
        orig_label_path = out_dir / "orig.lab"
        source_command = [
            str(mads_bin),
            str(case.source),
            f"-o:{orig_output_path}",
            f"-t:{orig_label_path}",
            *case.mads_args,
        ]
        if asmout_path is not None:
            source_command.append(f"-A:{asmout_path}")

        source_result = run_capture(source_command, case.cwd, out_dir / "orig.log")
        if expect_failure(case, "assemble-source", source_result):
            print(f"OK  {case.id} known failure at assemble-source")
            return
        if source_result.returncode != 0:
            raise SystemExit(f"FAIL {case.id} unexpected assemble-source failure")

        if case.expect_stage == "compare":
            roundtrip_output_path, _ = assemble_roundtrip(case, mads_bin, out_dir, asmout_path)
            if orig_output_path.read_bytes() == roundtrip_output_path.read_bytes():
                raise SystemExit(f"FAIL {case.id} unexpectedly matched bytes")
            print(f"OK  {case.id} known failure at compare")
            return

        roundtrip_command = [
            str(mads_bin),
            str(asmout_path),
            f"-o:{out_dir / ('roundtrip' + Path(case.output).suffix)}",
            f"-t:{out_dir / 'roundtrip.lab'}",
        ]
        roundtrip_result = run_capture(roundtrip_command, out_dir, out_dir / "roundtrip.log")
        if expect_failure(case, "assemble-roundtrip", roundtrip_result):
            print(f"OK  {case.id} known failure at assemble-roundtrip")
            return
        if roundtrip_result.returncode != 0:
            raise SystemExit(f"FAIL {case.id} unexpected assemble-roundtrip failure")
        raise SystemExit(f"FAIL {case.id} expected known failure did not occur")

    orig_output_path, _ = assemble_source(case, mads_bin, out_dir, asmout_path)

    if case.mode == "assemble-only":
        print(f"OK  {case.id} assembled")
        return

    if case.mode != "asmout-roundtrip" or asmout_path is None:
        raise ValueError(f"unsupported mode for {case.id}: {case.mode}")

    roundtrip_output_path, _ = assemble_roundtrip(case, mads_bin, out_dir, asmout_path)

    if orig_output_path.read_bytes() != roundtrip_output_path.read_bytes():
        raise SystemExit(f"FAIL {case.id} roundtrip mismatch")

    print(f"OK  {case.id} roundtrip matched")


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    config_path = (repo_root / args.config).resolve()
    artifact_root = (repo_root / args.artifact_root).resolve()
    mads_bin = repo_root / "Mad-Assembler" / "mads"

    cases = load_cases(config_path, repo_root)
    if args.list:
        for case in cases:
            print(f"{case.id}: {case.mode} -> {case.source.relative_to(repo_root)}")
        return 0

    requested = set(args.examples or [])
    if requested:
        cases = [case for case in cases if case.id in requested]
        missing = requested.difference(case.id for case in cases)
        if missing:
            raise SystemExit(f"unknown example id(s): {', '.join(sorted(missing))}")

    if not args.skip_build:
        build_mads(repo_root)

    artifact_root.mkdir(parents=True, exist_ok=True)
    for case in cases:
        run_case(case, mads_bin, artifact_root)

    mode_counts: dict[str, int] = {}
    for case in cases:
        mode_counts[case.mode] = mode_counts.get(case.mode, 0) + 1

    mode_summary = ", ".join(f"{mode}={count}" for mode, count in sorted(mode_counts.items()))
    print(f"==> Example regression checks passed ({len(cases)} cases: {mode_summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())