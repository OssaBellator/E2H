"""One-use integration patch for declarative compiler oracles."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/e2h/compiler.py",
    "from e2h.runner import RunResult, RunStatus, run_capsule\n",
    "from e2h.oracles import (\n"
    "    ORACLE_MUTATION_ENV,\n"
    "    OracleTemplate,\n"
    "    compile_oracle,\n"
    "    oracle_mutation_id,\n"
    "    oracle_mutation_operator,\n"
    ")\n"
    "from e2h.runner import RunResult, RunStatus, run_capsule\n",
)
replace_once(
    "src/e2h/compiler.py",
    '_RESERVED_MUTATION_ENV = frozenset({"E2H_MUTATION_ID", "E2H_PROPOSAL_ID"})\n',
    '_RESERVED_MUTATION_ENV = frozenset({"E2H_MUTATION_ID", "E2H_PROPOSAL_ID"})\n'
    "_RESERVED_CHECK_ENV = _RESERVED_MUTATION_ENV | {ORACLE_MUTATION_ENV}\n",
)
replace_once(
    "src/e2h/compiler.py",
    "\n\nclass CompilerSpec(StrictModel):\n",
    "\n\ndef _generated_oracle_mutations(\n"
    "    oracles: list[OracleTemplate],\n"
    ") -> list[EnvironmentMutation]:\n"
    "    return [\n"
    "        EnvironmentMutation(\n"
    "            id=oracle_mutation_id(oracle),\n"
    "            description=f\"Mutate {oracle.kind} oracle {oracle.id}\",\n"
    "            env={ORACLE_MUTATION_ENV: oracle_mutation_operator(oracle)},\n"
    "            check_ids=[oracle.id],\n"
    "        )\n"
    "        for oracle in oracles\n"
    "    ]\n"
    "\n\nclass CompilerSpec(StrictModel):\n",
)
replace_once(
    "src/e2h/compiler.py",
    "    checks: list[CommandCheck] = Field(min_length=1, max_length=1000)\n"
    "    mutations: list[EnvironmentMutation] = Field(default_factory=list, max_length=_MAX_MUTATIONS)\n",
    "    checks: list[CommandCheck] = Field(default_factory=list, max_length=1000)\n"
    "    oracles: list[OracleTemplate] = Field(default_factory=list, max_length=100)\n"
    "    auto_mutate_oracles: bool = True\n"
    "    mutations: list[EnvironmentMutation] = Field(default_factory=list, max_length=_MAX_MUTATIONS)\n",
)
old_validator = '''    @model_validator(mode="after")
    def checks_and_mutations_must_be_consistent(self) -> CompilerSpec:
        _ensure_json(self.metadata, "compiler metadata")
        check_ids = [check.id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("command check ids must be unique")
        mutation_ids = [mutation.id for mutation in self.mutations]
        if len(mutation_ids) != len(set(mutation_ids)):
            raise ValueError("mutation ids must be unique")
        known = set(check_ids)
        for check in self.checks:
            reserved = sorted(key for key in check.env if key.upper() in _RESERVED_MUTATION_ENV)
            if reserved:
                raise ValueError(
                    "command environments must not override reserved E2H mutation identifiers: "
                    + ", ".join(reserved)
                )
        for mutation in self.mutations:
            missing = sorted(set(mutation.check_ids) - known)
            if missing:
                raise ValueError(f"mutation references unknown checks: {', '.join(missing)}")
        if len(self.checks) > self.limits.max_commands:
            raise ValueError("checks exceeds limits.max_commands")
        return self
'''
new_validator = '''    @model_validator(mode="after")
    def checks_and_mutations_must_be_consistent(self) -> CompilerSpec:
        _ensure_json(self.metadata, "compiler metadata")
        compiled_oracles = [compile_oracle(oracle) for oracle in self.oracles]
        check_ids = [check.id for check in self.checks] + [check.id for check in compiled_oracles]
        if not check_ids:
            raise ValueError("at least one command check or oracle is required")
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("check and oracle ids must be unique")
        generated_mutations = (
            _generated_oracle_mutations(self.oracles) if self.auto_mutate_oracles else []
        )
        all_mutations = [*self.mutations, *generated_mutations]
        if len(all_mutations) > _MAX_MUTATIONS:
            raise ValueError(f"combined mutations exceeds {_MAX_MUTATIONS}")
        mutation_ids = [mutation.id for mutation in all_mutations]
        if len(mutation_ids) != len(set(mutation_ids)):
            raise ValueError("mutation ids must be unique")
        known = set(check_ids)
        for check in self.checks:
            reserved = sorted(key for key in check.env if key.upper() in _RESERVED_CHECK_ENV)
            if reserved:
                raise ValueError(
                    "command environments must not override reserved E2H mutation identifiers: "
                    + ", ".join(reserved)
                )
        for mutation in all_mutations:
            missing = sorted(set(mutation.check_ids) - known)
            if missing:
                raise ValueError(f"mutation references unknown checks: {', '.join(missing)}")
        if len(check_ids) > self.limits.max_commands:
            raise ValueError("checks and oracles exceeds limits.max_commands")
        return self
'''
replace_once("src/e2h/compiler.py", old_validator, new_validator)
replace_once(
    "src/e2h/compiler.py",
    '    """Compile sanitized evidence and trusted check declarations into a draft proposal."""\n'
    "    if not bundle.provenance.redaction_enabled and not spec.allow_unredacted:\n",
    '    """Compile sanitized evidence and trusted check declarations into a draft proposal."""\n'
    "    compiled_checks = [*spec.checks, *(compile_oracle(oracle) for oracle in spec.oracles)]\n"
    "    compiled_mutations = [\n"
    "        *spec.mutations,\n"
    "        *(\n"
    "            _generated_oracle_mutations(spec.oracles)\n"
    "            if spec.auto_mutate_oracles\n"
    "            else []\n"
    "        ),\n"
    "    ]\n"
    "    if not bundle.provenance.redaction_enabled and not spec.allow_unredacted:\n",
)
replace_once(
    "src/e2h/compiler.py",
    '            "evidence": [reference.model_dump(mode="json") for reference in references],\n',
    '            "evidence": [reference.model_dump(mode="json") for reference in references],\n'
    '            "oracles": [oracle.model_dump(mode="json") for oracle in spec.oracles],\n',
)
replace_once(
    "src/e2h/compiler.py",
    "        success=SuccessSpec(commands=spec.checks),\n",
    "        success=SuccessSpec(commands=compiled_checks),\n",
)
replace_once(
    "src/e2h/compiler.py",
    "    if not spec.mutations:\n",
    "    if not compiled_mutations:\n",
)
replace_once(
    "src/e2h/compiler.py",
    "        mutations=spec.mutations,\n",
    "        mutations=compiled_mutations,\n",
)

replace_once(
    "src/e2h/compiler_cli.py",
    '    console.print(\n        f"[green]Valid[/green] {spec.id} "\n        f"({len(spec.checks)} checks, {len(spec.mutations)} mutations)"\n    )\n',
    '    generated_mutations = len(spec.oracles) if spec.auto_mutate_oracles else 0\n'
    '    console.print(\n'
    '        f"[green]Valid[/green] {spec.id} "\n'
    '        f"({len(spec.checks) + len(spec.oracles)} checks, "\n'
    '        f"{len(spec.mutations) + generated_mutations} mutations)"\n'
    '    )\n',
)

replace_once(
    "src/e2h/__init__.py",
    "from e2h.models import TaskCapsule\n",
    "from e2h.models import TaskCapsule\n"
    "from e2h.oracles import ArtifactOracle, FileOracle, JsonOracle, OracleEvaluation\n",
)
for name in ("ArtifactOracle", "FileOracle", "JsonOracle", "OracleEvaluation"):
    replace_once(
        "src/e2h/__init__.py",
        '    "CapsuleProposal",\n',
        f'    "{name}",\n    "CapsuleProposal",\n',
    )
replace_once("src/e2h/__init__.py", '__version__ = "0.4.0"', '__version__ = "0.5.0"')
replace_once("pyproject.toml", 'version = "0.4.0"', 'version = "0.5.0"')

replace_once(
    "README.md",
    "The repository now contains four connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, and a review-gated **capsule compiler**.",
    "The repository now contains five connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, and declarative **file, JSON, and artifact oracles**.",
)
replace_once(
    "README.md",
    "- Capsule materialization only after matching review and verification evidence.\n",
    "- Capsule materialization only after matching review and verification evidence.\n"
    "- Declarative file, RFC 6901 JSON, and artifact digest/size oracle templates.\n"
    "- Automatic operator-specific oracle mutations for strong verification.\n",
)
oracle_section = '''## Declarative oracles

Compiler specifications may declare `file`, `json`, and `artifact` oracles alongside command checks. Oracles are compiled into ordinary bounded `CommandCheck` entries that execute without shell interpolation, so materialized capsules remain compatible with the replay runner and trace model.

File oracles support presence, absence, exact UTF-8 text, contained text, and SHA-256 checks. JSON oracles use RFC 6901 pointers with equality, presence, and absence modes. Artifact oracles enforce byte-size bounds and optional SHA-256 digests. All paths remain relative, reject parent traversal, and are resolved against the check working directory to prevent symlink escapes.

By default, each oracle receives a generated mutation probe. Presence checks are inverted, JSON equality values are structurally changed, and content/artifact checks receive a digest mismatch. Strong verification therefore proves that the baseline passes and every declared oracle detects its operator-specific regression. Set `auto_mutate_oracles: false` only when mutations are supplied by another trusted workflow.

'''
replace_once(
    "README.md",
    "## Architecture direction\n",
    oracle_section + "## Architecture direction\n",
)
replace_once(
    "ROADMAP.md",
    "- [ ] Richer file, JSON, and artifact oracle templates and mutation operators.\n",
    "- [x] Richer file, JSON, and artifact oracle templates and mutation operators.\n",
)

Path(__file__).unlink()
