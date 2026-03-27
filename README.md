### MadMads - MADS with a simple assembler output

## Current status

MadMads is a working fork of MADS that adds a lowered text-output mode alongside the normal binary output.

What users get today:

- a new `-A` or `-A:file.a65` switch that writes simple assembler output,
- executable help output that identifies the fork as `MadMads`,
- byte-exact Scorch round-trips for both Atari targets currently used for validation,
- readable lowering of common helper pseudo-ops such as `MVA`, `MVX`, `MVY`, `MWA`, `MWX`, `MWY`, `ADB`, `SBB`, `ADW`, `SBW`, `CPW`, `INW`, and `DEW`,
- structural lowering of skip-prefix pseudo-ops such as `SEQ`, `SNE`, `SPL`, `SMI`, `SCC`, `SCS`, `SVC`, and `SVS`,
- lowering of MADS anonymous labels and `@+` / `@-` references to generated standard labels,
- lowering of indexed post-adjust operands such as `ADDR,X+` and `ADDR,Y-` to plain instructions,
- lowering of dual-address `ORG A,B` regions to plain `ORG` output with logical labels preserved as constants,
- preservation of symbolic byte/word data where it is safe to do so,
- normalized output directives such as `.BYTE` and `.WORD` even when the original source used shorthand forms like `.BY` or `.WO`,
- removal of redundant `OPT R+` / `OPT R-` state from generated asmout.

Current validation status:

- `mads -A scorch.asm -d:TARGET=800` reproduces the original Atari 800 output byte-for-byte,
- `mads -A scorch.asm -d:TARGET=5200` reproduces the original 5200 cartridge image byte-for-byte.

## Quick start

Build MadMads:

```sh
fpc -Mdelphi -vh -O3 ~/projects/madmads/Mad-Assembler/mads.pas
```

Generate lowered asm output:

```sh
~/projects/madmads/Mad-Assembler/mads ~/projects/scorch_src/scorch.asm -o:scorch.xex -l:scorch.lst -A:scorch800.a65
```

Round-trip validation for Scorch uses the public Scorch sources from https://github.com/pkali/scorch_src.

```sh
git clone https://github.com/pkali/scorch_src ~/projects/scorch_src
cd ~/projects/madmads
./scripts/mads-roundtrip.sh 800
./scripts/mads-roundtrip.sh 5200
```

Postprocess generated asmout for another assembler dialect:

```sh
python3 scripts/asmout_postprocess.py --list-dialects
python3 scripts/asmout_postprocess.py --dialect omc scorch800.a65 scorch800.omc.a65 --map-file scorch800.omc.map
python3 scripts/asmout_postprocess.py --dialect ca65 scorch800.a65 scorch800.ca65.asm
```

The current `ca65` profile is intentionally conservative. It establishes the multi-backend framework and applies only syntax normalizations that are low-risk across dialects, while the `omc` profile remains the more aggressive compatibility pass.

For a local `cc65` / `ca65` toolchain, use the wrapper script:

```sh
scripts/cc65-tool.sh ca65 --version
scripts/cc65-tool.sh ld65 --version
```

If `cc65` is not installed system-wide, the wrapper also supports a repo-local extracted package under `tmp/cc65-local/root`.

Probe the current `ca65` backend against Scorch:

```sh
scripts/ca65-probe.sh 800
scripts/ca65-probe.sh 5200
```

## Working goal

Extend MADS so it can emit not only binary output, but also a second output format: a plain 6502 assembly listing that represents the fully expanded program after macros, expressions, includes, and most MADS-specific conveniences have already been resolved.

The target is not "pretty source regeneration". The target is a practical interchange/debug format that:

- can be assembled by a simple 6502 assembler such as Atari Assembler/Editor,
- preserves the final code layout and addresses,
- keeps symbolic names where they are still meaningful,
- expands MADS-only shortcuts into ordinary instructions,
- is simple and predictable enough to inspect and diff.

In short: this project should make MADS act as a source-to-source lowering tool, from rich MADS syntax to plain assembler syntax.

## What problem this solves

MADS is powerful, but the distance between source and final machine code can become large. The existing `.lst` output is close to useful, but it is still a listing, not a clean reassemblable source file.

The intended new output should help with:

- debugging generated code,
- auditing macro-heavy sources,
- understanding final control flow and data layout,
- exporting code to older or simpler assemblers,
- producing a stable "lowered" form for comparison and review.

## Concrete success criteria

For an input program that MADS can assemble, the new output mode should ideally produce a text file that:

- contains standard 6502 mnemonics and simple directives,
- contains labels and constants needed to assemble successfully,
- reproduces the same bytes as MADS for the supported feature subset,
- does not require MADS macros, MADS procedures, or other high-level syntax,
- is readable enough that a human can follow it.

The first version does not need to support every MADS feature. It only needs a clearly defined subset that is useful and mechanically correct.

## Non-goals for phase 1

To keep this realistic, the first implementation should explicitly avoid trying to solve all of MADS at once.

Not required in phase 1:

- reproducing original formatting,
- recreating the original macro structure,
- preserving comments from the original source,
- perfectly minimizing the number of emitted lines,
- supporting every target CPU and every exotic directive,
- generating source that matches original author intent.

This is a lowering pass, not a decompiler and not a source beautifier.

## Example

MADS source:

```asm
    mva #%00111110 dmactl    ; set new screen width
    mva <ant dlptr
    mva >ant dlptr+1
```

Desired lowered output:

```asm
    LDA #$3E
    STA DMACTL
    LDA #<ANT
    STA DLPTR
    LDA #>ANT
    STA DLPTR+1
```

## Important architectural observation

The obvious idea is to hook into the byte writer and emit text instead of bytes. After looking at MADS, that is probably too late in the pipeline.

Why:

- `put_dst` writes raw bytes to the output buffer.
- `save_dst` decides when bytes should be written and also handles file headers and block transitions.
- by that point, instruction boundaries are mostly gone,
- labels, addressing intent, and source-level grouping have already been flattened,
- many outputs are just byte streams, so converting there would be closer to disassembly than to source lowering.

That means a pure "replace byte output with text output" hook would likely produce fragile results and lose too much structure.

## Better integration point

The better seam appears to be earlier: after MADS has already resolved a source line into a concrete instruction or data fragment, but before everything is irreversibly flattened into the final byte stream.

From a quick code reading, the relevant pieces are:

- instruction/data bytes are accumulated in intermediate buffers such as `t_ins`,
- `save_lst('a')` is called around places where assembled output for the current line is finalized,
- relocatable chunks are later flushed through `flush_link`,
- final file writing goes through `save_dst` and `put_dst`.

This suggests the project should introduce a new emission layer that observes finalized line-level output, not raw file bytes.

## Proposed model

Add a second backend beside the existing binary backend.

The binary backend keeps doing what MADS already does.

The new text backend should receive events such as:

- start new assembly address / block,
- define label,
- emit instruction with addressing mode and operand text,
- emit data bytes or words,
- reserve storage,
- emit constant definition,
- flush block / end file.

That backend can then write a simple assembler file using a narrow, well-defined directive set.

## Chosen output dialect

For ease of testing, the lowered output should stay compatible with MADS too, while remaining simple enough to be accepted by old-school 6502 assemblers.

Current dialect decisions:

- `ORG` for address changes,
- `=` for constant definitions instead of `EQU`,
- `.BYTE $FF, $FF, ...` for byte data,
- `.WORD $FFFF, $FFFF, ...` for word data,
- `<expr` for low byte extraction,
- `>expr` for high byte extraction,
- all mnemonics upper case,
- output should prefer symbolic operands over resolved numeric literals when the symbol is still valid.

Example:

```asm
SCREEN_ADDR = $4000

ORG $2000
START
    LDA #<SCREEN_ADDR
    STA DLPTR
    LDA #>SCREEN_ADDR
    STA DLPTR+1
    JMP MAIN

TABLE
    .BYTE $00, $01, $7F, $FF
PTRS
    .WORD TABLE, TABLE+4, >TABLE
```

### Notes on compatibility

- Using `ORG`, `=`, `.BYTE`, `.WORD`, `<`, and `>` keeps the format close to both MADS and classic 6502 practice.
- Phase 1 should avoid fancy pseudo-ops unless they are strictly needed by Scorch.
- If a construct cannot be represented cleanly in the chosen dialect, the backend should either lower it to plain instructions/data or fail with a clear diagnostic.

## Scorch-driven support matrix

The real target is not "support random MADS features". The real target is: support the constructs needed to lower Scorch successfully.

Based on the available [Mad-Assembler/scorch.lst](Mad-Assembler/scorch.lst), the first matrix should be:

### Must support in v1

- plain 6502 instructions with upper-case mnemonics,
- labels and forward/backward label references,
- constant definitions lowered as `NAME = expr`,
- `ORG`,
- `.BYTE`,
- `.WORD`,
- `<` and `>` byte extraction in operands,
- normal addressing forms: immediate, absolute, absolute indexed, zero page, zero page indexed, indirect forms, relative branches,
- expression operands such as `LABEL+1`,
- code emitted from expanded macros,
- data emitted from `DTA` once converted to `.BYTE` or `.WORD` form,
- binary asset inclusion from `INS`, lowered to explicit data output when necessary,
- included files after expansion,
- compile-time conditionals after resolution,
- generated symbol names when they are required to keep branches and jumps readable.

### Input constructs seen in Scorch that should disappear in lowered output

- `.MACRO` / `.ENDM`,
- `.PROC` / `.ENDP` as source structuring aids,
- shorthand helper macros such as `mva` and similar multi-instruction helpers,
- `.IF`, `.IFNDEF`, `.ELSE`, `.ENDIF`,
- `.ELIF`,
- `.DEF`,
- `ICL`,
- project-specific declarations such as `.ZPVAR`,
- startup/output controls such as `OPT` and `INI`,
- enum declarations such as `.ENUM` / `.ENDE`,
- parameter syntax such as `%0`, `%1`, `:1`, `#:1`.

These are acceptable in source, but the lowered output should contain only the final plain-assembler result.

### Likely lowering rules needed for Scorch

- `.DEF NAME = expr` -> `NAME = expr`,
- `DTA` strings/numbers -> `.BYTE` and `.WORD`,
- `INS 'file'[,offset[,length]]` -> explicit emitted data in `.BYTE` / `.WORD` form, or a backend-specific raw-data expansion step,
- `.ZPVAR name .byte/.word` -> one or more plain symbol definitions, possibly followed by `=` assignments,
- macro-generated instruction sequences -> explicit instruction lines,
- `.PROC` / `.ENDP` -> no required structural output beyond labels and resolved local names,
- conditional compilation -> only the selected branch appears in output,
- `ICL` trees -> flattened into one final output stream.

### Can be deferred unless Scorch proves it needs them

- `.LOCAL` reconstruction as structured local-label scopes,
- structs, enums, arrays, and other higher-level data declarations,
- relocatable object features,
- source comment preservation,
- pretty formatting beyond stable, readable output.

## Construct-level feasibility inside MADS

After reading the MADS implementation, the three Scorch-critical constructs do not all sit at the same level of abstraction.

### `DTA`

`DTA` is relatively promising for lowering.

- MADS parses `DTA` and related typed data directives through a dedicated data path,
- numeric and string fragments are normalized before final byte emission,
- data items are funneled through helpers such as `save_dta` and `save_dtaS`.

This means `DTA` is not just an opaque byte dump. There is still enough structure to emit `.BYTE` and `.WORD` text if the backend hooks in early enough.

Practical conclusion:

- `DTA` can probably share the main lowered-output backend,
- the backend should preserve item boundaries when possible,
- fallback to plain `.BYTE` is acceptable when the original `DTA` shape is awkward.

### `.ZPVAR`

`.ZPVAR` is mostly an allocation and symbol-definition mechanism, not a direct data-emission feature.

- it assigns zero-page addresses,
- it marks variables as zero-page allocated,
- later stages materialize labels and, for some variable kinds, initial data.

That matches Scorch well: the important thing is not to reproduce `.ZPVAR` itself, but to emit plain symbol definitions that preserve the chosen addresses.

Practical conclusion:

- `.ZPVAR` should be lowered into ordinary symbol assignments,
- it does not need a dedicated output syntax of its own,
- `.PROC` / local-scope interactions matter only insofar as names must stay unique and readable.

### `INS`

`INS` is the least friendly construct for a clean shared backend.

- MADS opens the file directly,
- reads raw bytes into buffers,
- and, for real `INS`, writes those bytes through `save_dst` in the final pass.

By that point, the source construct has effectively become a byte stream.

Practical conclusion:

- `INS` should not be handled by trying to reconstruct intent from final bytes,
- the lowering backend should probably intercept `INS` at directive level,
- the simplest textual lowering is to expand included binary data into explicit `.BYTE` lines,
- this may need a dedicated helper path instead of the same event flow used for normal instructions.

## Resulting design implication

The lowered-output feature probably needs two closely related mechanisms:

- a structured line-oriented backend for instructions, labels, constants, `ORG`, and `DTA`-like data,
- a directive-specific expansion path for constructs such as `INS` that otherwise collapse directly into raw bytes.

This is still compatible with the main project direction. It just means "one backend" does not necessarily imply "one identical hook" for every source construct.

## Concrete hook strategy inside MADS

The ordinary instruction path is more encouraging than the raw byte writer suggested at first.

For normal CPU instructions, MADS computes a compact per-instruction result before final file emission:

- `oblicz_mnemonik(...)` resolves the mnemonic, addressing mode, operand size, and final opcode bytes,
- its result contains instruction length and the generated bytes for that one instruction,
- ordinary line output is then staged in the current-line buffer and later flushed by `save_lst('a')`,
- `save_lst('a')` emits bytes from the line buffer, while relocatable chunks use `t_ins` and are flushed separately.

That means there is a realistic hook boundary before `save_dst(...)` and after the instruction has already been fully resolved.

### Recommended hook levels

Use three hook levels rather than forcing every construct through one late byte stream.

1. Instruction hook

- triggered when a call to `oblicz_mnemonik(...)` or `asm_mnemo(...)` has produced a final instruction record,
- receives mnemonic class, operand text if available, final byte count, and generated bytes,
- ideal for ordinary instructions and macro-expanded instruction sequences.

2. Structured-data hook

- triggered from `DTA` / `.BYTE` / `.WORD` handling before values are flattened irreversibly,
- receives typed data items or a normalized data-item list,
- ideal for preserving `.BYTE` and `.WORD` output rather than falling back to one-byte-at-a-time emission.

3. Directive-expansion hook

- triggered directly from special directives such as `INS`, `.ZPVAR`, maybe later `DS`,
- receives directive-specific metadata,
- responsible for lowering to plain text forms such as symbol assignments or explicit `.BYTE` blocks.

### Minimal internal API sketch

The first implementation does not need a sophisticated IR. A tiny record-oriented sink should be enough.

Possible event set:

- `BeginBlock(address)`
- `DefineLabel(name, address)`
- `DefineSymbol(name, value)`
- `EmitInstruction(bytes, address, sourceTextHint)`
- `EmitByteData(items, address)`
- `EmitWordData(items, address)`
- `Reserve(address, size)`
- `EmitBinaryInclude(address, fileName, offset, length, addValue)`
- `EndBlock()`

Two details matter here:

- `sourceTextHint` should be optional, because some instructions will be easier to regenerate from existing parsed text than from bytes alone,
- for `INS`, the event can still be high-level even if the final lowering expands it into many `.BYTE` lines.

### What should not be the primary hook

Avoid using `save_dst(...)` or `put_dst(...)` as the main text-backend interface.

At that level:

- instruction boundaries are no longer reliable,
- symbolic intent is mostly gone,
- `INS` and many other constructs are already reduced to raw byte output,
- the result would drift toward disassembly instead of lowering.

### Best first implementation slice

The safest first slice appears to be:

- hook ordinary instructions after `oblicz_mnemonik(...)`,
- hook `DTA` through its dedicated data path,
- lower `.ZPVAR` into symbol definitions,
- treat `INS` through a dedicated expansion helper,
- keep `save_dst(...)` unchanged as the binary backend.

That gives a narrow additive path with minimal risk to the existing assembler.

## Prototype status

A first code prototype now exists in MADS.

At this point the prototype is no longer just a hook experiment. The user-visible current behavior is summarized at the top of this README; this section focuses on the implementation characteristics that matter when extending it further.

Internally, the current prototype:

- emits `ORG` on discontinuous address flow,
- lowers `EQU` / `SET` / simple `.DEF` definitions to plain `NAME = value` lines,
- preserves `.PROC` / `.ENDP` for round-trip safety while dropping redundant `OPT R` directives,
- lowers `INS` to explicit chunked `.BYTE` output,
- lowers `.ZPVAR` allocations to plain symbol definitions,
- lowers MADS anonymous labels and `@+` / `@-` style references to generated `__ASMOUT_ANON_*` labels,
- preserves special label-kind metadata so helper forms such as `@enum(...)` survive expression evaluation,
- prefers trustworthy source-text reuse for instructions and simple data lines, with fallback to synthesized `.BYTE` / `.WORD` output when necessary,
- performs size-aware pseudo-branch lowering so the reassembled output keeps original MADS sizing.I am not sure about versioning yet...
Maybe 
`Ma, based on mads 2.1.8 (2026/03/26)`


Verified optimizer note:

- `OPT R+` is global assembler state in MADS, not file-local state,
- it applies across `ICL` boundaries,
- enabling it in a parent file affects included files,
- enabling it in an included file remains in effect after control returns to the parent file,
- in current MADS it drives the `MV?` / `MW?` register-macro optimization path rather than acting as a general repeated-instruction peephole.

Important limitations of the current prototype:

- it is intentionally conservative,
- it still does not attempt to normalize or interpret non-standard directives for simpler assemblers beyond the cases already handled in asmout,
- data lowering is still incomplete for cases beyond the current line-oriented `.BYTE` / `.WORD` reconstruction,
- this is a hook-validation prototype, not the final backend.

Technical validation notes:

- `@enum(label1|label2|...)` assembles correctly again,
- preserved `.PROC` / `.ENDP` blocks restore a large part of procedure-local structure in generated Scorch output,
- Scorch round-trips now close fully for both Atari targets with `-A` enabled,
- `ADW` lowering matches original MADS sizing in the Scorch-dependent cases,
- skip-prefix pseudo-ops `SEQ`, `SNE`, `SPL`, `SMI`, `SCC`, `SCS`, `SVC`, and `SVS` lower structurally to real `Bxx` skip wrappers without breaking macro-local labels or branch span,
- MADS anonymous labels are lowered to generated standard labels such as `__ASMOUT_ANON_*`,
- the remaining `.lab` differences are mostly label-table noise from readable helper labels such as `__ASMOUT_*`.

## Suggested phase breakdown

### Phase 1: define the output dialect

Before changing code, define the exact textual format.

Questions that still need precise answers:

- Should labels be emitted as bare labels or `LABEL:` labels?
- Do we want `.WORD >TABLE`-style expressions, or should high-byte tables always be lowered to `.BYTE >TABLE`?
- Should reserved space use `ORG` gaps only, or should we allow a simple reserve directive if Scorch needs it?
- Should unsupported constructs fail, warn, or fall back to data bytes?

### Phase 2: support a useful subset

Start with the subset that should get Scorch closest to working:

- plain 6502 instructions,
- labels,
- constants,
- `ORG`,
- `.BYTE` and `.WORD`,
- simple expressions including `<` and `>`,
- expanded macro output once it becomes ordinary instructions.

This alone would already make the feature useful.

### Phase 3: handle MADS-specific cases deliberately

Examples:

- anonymous/local labels,
- generated labels,
- relocatable blocks,
- structures and enumerations,
- include-generated symbols,
- cases where one source line expands into many output lines.

## Implementation strategy

The least risky path is probably:

1. define a tiny internal representation for "lowered output lines",
2. populate it at the point where MADS already knows the final opcode/data for the current source construct,
3. keep the binary writer unchanged,
4. add a command-line switch for text emission,
5. compare binary assembled output against reassembled lowered output on sample programs.

This keeps the existing binary path stable and makes the new mode additive.

## Risks

- MADS is a large single-file Pascal codebase with many implicit global-state interactions.
- some features may only exist as side effects on byte arrays, not as explicit semantic objects,
- the listing machinery may be helpful but is probably not a complete API for structured output,
- trying to support every directive immediately will make the feature brittle.

So the project should be judged by a supported subset plus clear failure modes, not by "supports all of MADS on day one".

## Immediate next step

The next concrete step should be to define the remaining edge cases of the dialect with a few worked examples:

- instruction lowering,
- label emission,
- constant emission,
- data blocks including `DTA` conversion,
- zero-page variable lowering,
- unsupported constructs.

Once that format is fixed, it becomes much easier to decide exactly where to hook into MADS and what data must be preserved during assembly.

## Build notes

Build MADS:

```sh
fpc -Mdelphi -vh -O3 ~/projects/madmads/Mad-Assembler/mads.pas
```

Round-trip validation for Scorch uses the public Scorch sources from https://github.com/pkali/scorch_src. Clone that repository next to this one as `scorch_src`, or adjust the validation script inputs to wherever you keep it.

```sh
git clone https://github.com/pkali/scorch_src ~/projects/scorch_src
```

Round-trip validation for Scorch:

```sh
cd ~/projects/madmads
./scripts/mads-roundtrip.sh 800
./scripts/mads-roundtrip.sh 5200
```

Using MadMads:

```sh
~/projects/madmads/Mad-Assembler/mads ~/projects/scorch_src/scorch.asm -o:scorch.xex -l:scorch.lst -A:scorch800.a65
```
