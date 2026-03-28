#!/usr/bin/env python3
"""Census MADS example sources and suggest expanded regression config."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENTRYPOINT_STEMS = {
    "main",
    "demo",
    "test",
    "example",
    "helloworld",
    "boot",
    "loader",
    "detect",
    "adventure",
    "cosmic",
    "openlet",
    "usokoban",
    "xbios",
}

ENTRYPOINT_SUBSTRINGS = (
    "demo",
    "test",
    "example",
    "detect",
    "loader",
    "player",
    "hello",
)

IGNORED_SUBTREES = (
    "ATARI7800/asteroids",
    "C64/IcebloxPlus",
    "demoscene/Knight_src",
    "demoscene/Visdom-II",
    "games/5dots",
    "games/NightraidersAtari",
    "games/atari-inform-interpreter",
    "games/pacman",
    "games/pad",
    "players/pro_tracker_1.5",
    "sprites/chars",
    "sprites/chars_ng",
    "sprites/char_sprites",
    "sprites/shanti",
)

FAILURE_BUCKET_RULES = (
    ("missing-org", "No ORG specified"),
    ("missing-files", "Cannot open or create file"),
    ("undeclared-labels", "Undeclared label"),
    ("unsupported-directives", "Unknown directive"),
    ("illegal-instructions", "Illegal instruction"),
    ("syntax", "Unexpected end of line"),
    ("syntax", "Improper syntax"),
)


@dataclass(frozen=True)
class CensusResult:
    relative_source: str
    case_id: str
    success: bool
    returncode: int
    first_error: str | None


@dataclass(frozen=True)
class ExampleFileIndex:
    lower_paths: dict[str, Path]
    by_name: dict[str, list[Path]]
    by_stem: dict[str, list[Path]]


def normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def build_file_index(examples_root: Path) -> ExampleFileIndex:
    lower_paths: dict[str, Path] = {}
    by_name: dict[str, list[Path]] = {}
    by_stem: dict[str, list[Path]] = {}

    for path in examples_root.rglob("*"):
        if not path.is_file():
            continue
        lower_paths[path.as_posix().lower()] = path
        by_name.setdefault(path.name.lower(), []).append(path)
        by_stem.setdefault(path.stem.lower(), []).append(path)

    return ExampleFileIndex(lower_paths=lower_paths, by_name=by_name, by_stem=by_stem)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe example sources for standalone source assembly.")
    parser.add_argument(
        "--config",
        default="scripts/mads-examples.json",
        help="Existing example config to preserve current asmout-roundtrip entries.",
    )
    parser.add_argument(
        "--artifact-root",
        default="tmp/example-census",
        help="Directory for generated logs and summary outputs.",
    )
    parser.add_argument(
        "--examples-root",
        default="Mad-Assembler/examples",
        help="Directory containing example sources to probe.",
    )
    parser.add_argument(
        "--write-config",
        help="Optional path to also write the suggested config to.",
    )
    parser.add_argument(
        "--write-batches",
        help="Optional path to write a review-batch markdown summary to.",
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


def load_roundtrip_sources(config_path: Path, repo_root: Path) -> set[str]:
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    raw_cases = iter_raw_cases(raw_config)
    result: set[str] = set()
    for raw_case in raw_cases:
        if raw_case.get("mode") == "asmout-roundtrip":
            source = str((repo_root / raw_case["source"]).relative_to(repo_root))
            result.add(source)
    return result


def make_case_id(relative_source: str) -> str:
    base = Path(relative_source).with_suffix("").as_posix().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return normalized or "example"


def relative_example_path(relative_source: str) -> str:
    return relative_source.removeprefix("Mad-Assembler/examples/")


def is_ignored_subtree(relative_source: str) -> bool:
    example_path = relative_example_path(relative_source)
    return any(
        example_path == prefix or example_path.startswith(prefix + "/")
        for prefix in IGNORED_SUBTREES
    )


def looks_like_entrypoint(relative_source: str) -> bool:
    example_path = relative_example_path(relative_source)
    parts = example_path.split("/")
    stem = normalize_name(Path(parts[-1]).stem)

    if len(parts) == 1:
        return True

    if Path(parts[-1]).stem.startswith("_"):
        return False

    parent = normalize_name(parts[-2])
    if stem == parent:
        return True

    if stem in ENTRYPOINT_STEMS:
        return True

    return any(token in stem for token in ENTRYPOINT_SUBSTRINGS)


def should_include_in_config(result: CensusResult, repo_root: Path, file_index: ExampleFileIndex) -> bool:
    if is_ignored_subtree(result.relative_source):
        return False

    if not looks_like_entrypoint(result.relative_source):
        return False

    if result.first_error and "No ORG specified" in result.first_error:
        return False

    if classify_missing_dependency(result, repo_root, file_index) == "missing-files-external-asset":
        return False

    return True


def failure_bucket(result: CensusResult, repo_root: Path, file_index: ExampleFileIndex) -> str:
    missing_bucket = classify_missing_dependency(result, repo_root, file_index)
    if missing_bucket is not None:
        return missing_bucket

    error = result.first_error or ""
    for bucket, needle in FAILURE_BUCKET_RULES:
        if needle in error:
            return bucket
    return "other"


def summarize_batches(results: list[CensusResult], repo_root: Path, file_index: ExampleFileIndex) -> str:
    included_failures = [
        result for result in results if not result.success and should_include_in_config(result, repo_root, file_index)
    ]
    by_bucket: dict[str, list[CensusResult]] = {}
    by_prefix: dict[str, list[CensusResult]] = {}

    for result in included_failures:
        bucket = failure_bucket(result, repo_root, file_index)
        by_bucket.setdefault(bucket, []).append(result)

        example_path = relative_example_path(result.relative_source)
        parts = example_path.split("/")
        prefix = "/".join(parts[:2]) if len(parts) > 1 else parts[0]
        by_prefix.setdefault(prefix, []).append(result)

    lines = [
        "# MADS Example Review Batches",
        "",
        "Generated from the broad example census after pruning noisy support-module trees and nested non-entrypoint files.",
        "",
        "## Ignored noisy subtrees",
        "",
    ]
    lines.extend(f"- {prefix}" for prefix in IGNORED_SUBTREES)
    lines.extend([
        "",
        "## Failure-class batches",
        "",
    ])

    for bucket, bucket_results in sorted(by_bucket.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"### {bucket} ({len(bucket_results)} cases)")
        for result in sorted(bucket_results, key=lambda item: item.relative_source)[:12]:
            lines.append(f"- {relative_example_path(result.relative_source)}")
        if len(bucket_results) > 12:
            lines.append(f"- ... {len(bucket_results) - 12} more")
        lines.append("")

    lines.extend([
        "## Subtree batches",
        "",
    ])
    for prefix, prefix_results in sorted(by_prefix.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(prefix_results) < 2:
            continue
        lines.append(f"### {prefix} ({len(prefix_results)} cases)")
        for result in sorted(prefix_results, key=lambda item: item.relative_source)[:12]:
            lines.append(f"- {Path(result.relative_source).name}: {result.first_error or 'failure'}")
        if len(prefix_results) > 12:
            lines.append(f"- ... {len(prefix_results) - 12} more")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def first_error_line(text: str) -> str | None:
    for line in text.splitlines():
        upper = line.upper()
        if "ERROR:" in upper or upper.startswith("FAIL "):
            return line.strip()
    return None


def extract_missing_reference(first_error: str | None) -> str | None:
    if not first_error:
        return None

    match = re.search(r"Cannot open or create file '([^']*)", first_error)
    if match is None:
        return None

    return match.group(1)


def classify_missing_dependency(result: CensusResult, repo_root: Path, file_index: ExampleFileIndex) -> str | None:
    missing_reference = extract_missing_reference(result.first_error)
    if missing_reference is None:
        return None

    source_dir = (repo_root / result.relative_source).parent
    candidate_path = (source_dir / missing_reference).resolve()
    candidate_lower = candidate_path.as_posix().lower()
    if candidate_lower in file_index.lower_paths:
        if file_index.lower_paths[candidate_lower] == candidate_path:
            return "missing-files-exact"
        return "missing-files-case-mismatch"

    name_matches = file_index.by_name.get(Path(missing_reference).name.lower(), [])
    if name_matches:
        return "missing-files-path-mismatch"

    stem_matches = file_index.by_stem.get(Path(missing_reference).stem.lower(), [])
    if stem_matches:
        return "missing-files-generated-or-alt-ext"

    if Path(missing_reference).suffix.lower() in {".raw", ".fnt", ".xex", ".pic"}:
        return "missing-files-external-asset"

    return "missing-files-internal"


def run_probe(repo_root: Path, mads_bin: Path, source_file: Path, artifact_dir: Path) -> CensusResult:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_path = artifact_dir / "build.log"
    command = [
        str(mads_bin),
        str(source_file),
        f"-o:{artifact_dir / 'out.obx'}",
        f"-t:{artifact_dir / 'out.lab'}",
    ]
    result = subprocess.run(command, cwd=source_file.parent, text=True, capture_output=True)
    combined = result.stdout + result.stderr
    log_path.write_text(combined, encoding="utf-8")

    relative_source = source_file.relative_to(repo_root).as_posix()
    return CensusResult(
        relative_source=relative_source,
        case_id=make_case_id(relative_source.removeprefix("Mad-Assembler/examples/")),
        success=result.returncode == 0,
        returncode=result.returncode,
        first_error=first_error_line(combined),
    )


def build_suggested_config(
    existing_config: dict[str, Any],
    census_results: list[CensusResult],
    repo_root: Path,
    file_index: ExampleFileIndex,
) -> dict[str, Any]:
    asmout_roundtrip = list(existing_config.get("asmout-roundtrip", []))

    assemble_only: list[dict[str, Any]] = []
    known_failing: list[dict[str, Any]] = []
    for result in census_results:
        if not should_include_in_config(result, repo_root, file_index):
            continue

        entry = {
            "id": result.case_id,
            "source": result.relative_source,
            "output": "out.obx",
        }
        if result.success:
            assemble_only.append(entry)
        else:
            known_failing.append({
                **entry,
                "expect_stage": "assemble-source",
            })

    return {
        "asmout-roundtrip": asmout_roundtrip,
        "assemble-only": assemble_only,
        "known-failing": known_failing,
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    config_path = (repo_root / args.config).resolve()
    artifact_root = (repo_root / args.artifact_root).resolve()
    examples_root = (repo_root / args.examples_root).resolve()
    mads_bin = (repo_root / "Mad-Assembler" / "mads").resolve()

    if not mads_bin.exists():
        raise SystemExit(f"missing MADS binary: {mads_bin}")

    existing_config = json.loads(config_path.read_text(encoding="utf-8"))
    preserved_roundtrip = load_roundtrip_sources(config_path, repo_root)

    shutil.rmtree(artifact_root, ignore_errors=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    source_files = sorted(
        path
        for pattern in ("*.asm", "*.ASM")
        for path in examples_root.rglob(pattern)
    )

    seen_paths: set[Path] = set()
    unique_sources: list[Path] = []
    for source_file in source_files:
        if source_file in seen_paths:
            continue
        seen_paths.add(source_file)
        unique_sources.append(source_file)

    census_results: list[CensusResult] = []
    file_index = build_file_index(examples_root)
    for source_file in unique_sources:
        relative_to_repo = source_file.relative_to(repo_root).as_posix()
        if relative_to_repo in preserved_roundtrip:
            continue

        artifact_dir = artifact_root / source_file.relative_to(examples_root).with_suffix("")
        result = run_probe(repo_root, mads_bin, source_file, artifact_dir)
        census_results.append(result)
        status = "OK" if result.success else "FAIL"
        print(f"{status}\t{result.relative_source}\t{result.first_error or ''}")

    suggested_config = build_suggested_config(existing_config, census_results, repo_root, file_index)
    summary = {
        "total_probed": len(census_results),
        "assemble_only": sum(1 for result in census_results if result.success),
        "known_failing": sum(1 for result in census_results if not result.success),
        "included_assemble_only": len(suggested_config["assemble-only"]),
        "included_known_failing": len(suggested_config["known-failing"]),
    }
    review_batches = summarize_batches(census_results, repo_root, file_index)

    (artifact_root / "results.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "results": [result.__dict__ for result in census_results],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (artifact_root / "results.tsv").write_text(
        "\n".join(
            [
                "status\tsource\tfirst_error",
                *[
                    f"{'OK' if result.success else 'FAIL'}\t{result.relative_source}\t{result.first_error or ''}"
                    for result in census_results
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (artifact_root / "suggested-mads-examples.json").write_text(
        json.dumps(suggested_config, indent=2) + "\n",
        encoding="utf-8",
    )

    (artifact_root / "review-batches.md").write_text(review_batches, encoding="utf-8")

    if args.write_config:
        (repo_root / args.write_config).resolve().write_text(
            json.dumps(suggested_config, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.write_batches:
        (repo_root / args.write_batches).resolve().write_text(review_batches, encoding="utf-8")

    print(
        "==> Census complete "
        f"(probed={summary['total_probed']}, assemble-only={summary['assemble_only']}, known-failing={summary['known_failing']}, "
        f"included-assemble-only={summary['included_assemble_only']}, included-known-failing={summary['included_known_failing']})"
    )
    print(f"==> Suggested config: {artifact_root / 'suggested-mads-examples.json'}")
    print(f"==> Review batches: {artifact_root / 'review-batches.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())