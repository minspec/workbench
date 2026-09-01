// Everything a dispatched role is permitted to see, as one closed shape.
//
// Three mechanisms already guard a role's context, and each works:
// isolation.py decides what the harness may bring from the operator's
// machine, stage.py decides what the snapshot holds, and the
// instruction files in force decide which rules apply. Nothing checked
// that all three RAN.
//
// On 2026-08-23 that gap opened. The launcher's isolation step is
// `eval "$(isolation.py --sh ...)"`, and eval reports the status of the
// text it evaluated -- so a module that was absent produced no output,
// became `eval ""`, and succeeded. An agent went out carrying the
// operator's entire configuration and no error was raised anywhere. The
// guard was fine. The absence of the guard was invisible.
//
// A procedure that must be remembered will eventually not be. A closed
// definition cannot be forgotten, because the missing field is a type
// error rather than a skipped step. That is the whole reason this file
// is CUE and not another checker:
//
//   extra context   -> a field not in the definition -> does not evaluate
//   missing context -> a required field absent       -> does not evaluate
//
// #Context is the SHAPE of an observed dispatch. #Dispatchable is the
// LAW -- the subset of shapes that may actually be launched. They are
// separate on purpose: a context that describes a leak is a valid
// observation and an invalid dispatch, and conflating the two would
// leave no way to write the leak down.

package harness

// ---------------------------------------------------------------- ids

#HarnessName: "claude" | "codex" | "grok"

// Hex digest, lower case. Long enough to be an identity, not a hint.
#Digest: =~"^[0-9a-f]{64}$"

#NonEmpty: string & !=""

// Absence has to be SAID. An omitted list and an empty list mean
// entirely different things -- "nobody looked" and "we looked and found
// none" -- and a schema that accepts a missing field silently turns the
// first into the second. Anywhere a collection may legitimately be
// empty, it is spelled as this instead, carrying the reason.
#DeclaredAbsent: close({
	declared_absent: #NonEmpty
})

// ------------------------------------------------------------ harness

// What the harness itself brought, and how that was established.
//
// `applied` is what the launcher DID. `observed` is what was then found
// to be true. They are separate fields because a context must not be
// able to assert its own cleanliness: applying the right flags is an
// intention, and only the observation is evidence. The pair is what
// makes a false claim expensive to write down.
//
#Isolation: close({
	mechanism: "flags" | "home"

	// The argv fragment and environment overrides actually used.
	flags: [...#NonEmpty] | #DeclaredAbsent
	env: {[#NonEmpty]: #NonEmpty} | #DeclaredAbsent

	// For a home-mechanism harness, the minimal home and the complete
	// list of what was linked into it. Credentials are the one thing
	// that crosses, deliberately; enumerating them here is what makes
	// "and nothing else" checkable. Optional HERE, and required by
	// #Dispatchable -- which is where every other refusal lives, and
	// which keeps this struct a description of what was done rather
	// than a judgement about it.
	home?:       #NonEmpty
	auth_files?: [...#NonEmpty]

	observed: #IsolationObserved
})

#IsolationObserved: close({
	// True means the operator's own configuration reached the agent.
	// It is not a warning: see #Dispatchable.
	operator_config_present: bool

	// HOW that was determined. A bare boolean is a claim; this is the
	// method behind it, so a reader can tell a probe from a guess.
	// "unisolated arm answered YES, isolated arm answered NO" is
	// evidence. "isolated" is not.
	evidence: #NonEmpty

	// When the check was last actually run, and against which harness
	// build. Every isolation fact is true of one version on one day.
	checked_at:      #NonEmpty
	harness_version: #NonEmpty
})

#Harness: close({
	name:      #HarnessName
	version:   #NonEmpty
	isolation: #Isolation
})

// ------------------------------------------------------------- staged

// What the snapshot holds, and the proof that it holds only that.
#Staged: close({
	root: #NonEmpty

	// Every file present, relative to root, and its digest. The list is
	// the observation; `count` is not derived from it here because CUE
	// would then be checking arithmetic rather than agreement -- the
	// extractor states both and a mismatch is a finding.
	files: [...#StagedFile]
	count: int & >=0

	// The manifest that produced it, carried so a reader can re-derive
	// the staging rather than trust it.
	given: [...#NonEmpty]
	withheld: [...#NonEmpty] | #DeclaredAbsent

	// Build noise dropped on the way in. Reported rather than silently
	// omitted: a rule that quietly removes files is the failure this
	// whole file exists to prevent, committed inside the fix.
	noise_dropped: [...#NonEmpty] | #DeclaredAbsent

	proof: #StagingProof
})

#StagedFile: close({
	path:   #NonEmpty
	sha256: #Digest
	bytes:  int & >=0
})

#StagingProof: close({
	tool: #NonEmpty

	// Files present that a withheld pattern matches. Must be empty to
	// dispatch; recorded rather than asserted so a breach is
	// describable.
	withheld_present: [...#NonEmpty]

	// Given patterns that matched nothing. An empty snapshot satisfies
	// every withholding rule perfectly, so this is the half that stops
	// "nothing leaked" being read as "the firewall worked".
	given_unmet: [...#NonEmpty]
})

// ----------------------------------------------------------- doctrine

// The instruction files in force, in inheritance order from the
// repository root down to the working directory.
//
// Session-start load is assembled from the working directory UPWARD.
// Measured 2026-08-26 on this machine (Claude Code 2.1.246, Codex
// 0.148.0, Grok 1.0.5): Codex embeds root AGENTS.md and not CLAUDE.md;
// Grok loads root CLAUDE.md and AGENTS.md; Claude auto-loads CLAUDE.md
// and not AGENTS.md. Claude 2.1.246 also attaches a subtree CLAUDE.md
// on first read into that subtree, so a doctrine file below the
// snapshot root CAN reach a root-cwd agent -- lazily, and only
// CLAUDE.md. Recording the chain that was actually in force -- rather
// than the files that were present -- is the difference between a rule
// being available and a rule applying.
#Doctrine: #DoctrineChain | #DeclaredAbsent

#DoctrineChain: close({
	// Non-empty by construction. A dispatch with no doctrine at all is
	// legitimate, but it has to say so via #DeclaredAbsent rather than
	// by presenting an empty list that looks like a chain.
	files: [#DoctrineFile, ...#DoctrineFile]
})

#DoctrineFile: close({
	path:   #NonEmpty
	sha256: #Digest

	// Whether the agent actually received it, and how that was known.
	// Presence on disk is not receipt.
	in_force: bool
	evidence: #NonEmpty
})

// -------------------------------------------------------------- brief

// The instructions a role was given.
//
// This is the field the first version of #Context did not have, and its
// absence is where both of the day's real defects came from. Staged
// files are proved. Harness configuration is probed. The BRIEF was prose
// typed fresh each time, validated by nothing -- and it is the largest
// surface of the three.
//
// Twice in one session, two authors of the same seam were handed
// different interfaces. Five extractors take `--root DIR --out PATH`;
// seven take a positional root and print to stdout, because their brief
// said "importable modules" and never said how they would be called.
// vet.py looks for a fault sidecar at `<fault>.md`; the planter wrote
// `<fault>.meta.json`, because the brief I gave it said to. In both
// cases the shared specification fixed the shape of the DATA and never
// the shape of the INTERFACE, so the authors met at the shape and missed
// at the door.
//
// An interface written twice in prose is an interface that will differ.
// Written once here and referenced by both roles, it cannot.
#Brief: close({
	role: #NonEmpty

	// What the role must produce. Unified with `task.produces` by
	// #Dispatchable, so a deliverable the task does not expect — or an
	// expectation with no deliverable — is refused rather than noted.
	// This comment used to promise a check against a manifest field
	// named `returns`; no such field exists on anything here, and a
	// comment describing a guard that was never written is worse than
	// silence, because it stops the next reader looking (Codex, PR #40).
	deliverables: [#Deliverable, ...#Deliverable]

	// The shape of the report. Retyping this per dispatch is how a
	// field quietly goes missing, and a missing field in a report is
	// indistinguishable from an honest "nothing to say".
	report_fields: [#NonEmpty, ...#NonEmpty]

	// Constraints stated to the role. Present so they can be compared
	// across roles, and so a rule can be shown to have been given.
	rules: [...#NonEmpty] | #DeclaredAbsent

	// The exact bytes handed over. A brief that cannot be identified
	// cannot be shown to be the one that produced a result.
	sha256: #Digest
	bytes:  int & >0
})

#Deliverable: close({
	path: #NonEmpty

	// The interface this deliverable must satisfy, when more than one
	// role touches it. Null ONLY when nothing else consumes it -- and
	// that is a claim, so it is spelled rather than left absent.
	interface: #Interface | #DeclaredAbsent
})

// One seam, declared once, referenced by every role that meets there.
#Interface: close({
	name: #NonEmpty

	// How it is invoked, if it is invoked.
	argv?: [...#NonEmpty]

	// Where its output goes. "stdout" and "file" are not
	// interchangeable and assuming either is what broke the extractors.
	output?: "stdout" | "file" | "both"

	// Fields a consumer will read. A producer that omits one and a
	// consumer that requires it is the fault-sidecar defect exactly.
	fields?: [...#NonEmpty]

	// Where this seam is written down, so a disagreement has an arbiter
	// that is not one of the two authors.
	declared_at: #NonEmpty
})

// --------------------------------------------------------------- task

// What a role doing this task must be given, declared once for the task
// rather than re-decided at every dispatch.
//
// Closure answers only half the question. A closed #Context guarantees
// nothing EXTRA reached the agent; it says nothing about whether what
// did reach it was ENOUGH. Those are different failures and only one of
// them was guarded.
//
// The unguarded one happened today. An extractor author was given
// sections 4 to 6 of a plan as its specification, and section 6 refers
// to sections 9 and 10, which were not in the excerpt. The role reported
// it -- "sections 9 and 10 are not in the SPEC excerpt, yet section 6
// steps 3-5 depend on them" -- and emitted explicit unresolved markers
// rather than inventing the missing rules. That was the AGENT catching
// it. Nothing in the machinery would have.
//
// A hand-cut slice of a document is not a projection: a projection is
// closed under its own references, and a slice is whatever somebody's
// sed range happened to cover.
#Task: close({
	name: #NonEmpty

	// Everything a role needs. Stated on the TASK, so two dispatches of
	// the same task cannot disagree about what it takes to do it.
	requires: [#Requirement, ...#Requirement]

	produces: [#Deliverable, ...#Deliverable]
	report_fields: [#NonEmpty, ...#NonEmpty]
})

#Requirement: close({
	// In words, so a human can tell whether the glob below is right.
	what: #NonEmpty

	// The glob that must match something staged. Same mechanism as the
	// manifest's `given_unmet`, for the same reason: a requirement that
	// matches nothing is under-supply, and under-supply is invisible
	// until the role guesses.
	satisfied_by: #NonEmpty

	// What the role cannot do without it. Present because a requirement
	// nobody can justify is one that will be dropped by whoever next
	// tries to make a snapshot smaller.
	why: #NonEmpty
})

// ------------------------------------------------------------ context

#Context: close({
	role:    #NonEmpty
	harness: #Harness
	staged:  #Staged
	doctrine: #Doctrine

	// What the role was actually told. Without it a context describes
	// the room and not the instructions given inside it.
	brief: #Brief

	// The task being performed, and what checking it against the staged
	// set found. Both lists must be empty to dispatch; they are recorded
	// rather than asserted so under-supply is describable.
	task: #Task

	// Requirements whose glob matched nothing that was staged.
	unmet_requirements: [...#NonEmpty]

	// Documents that were given but that refer to material which was
	// not -- section cross-references, cited files, named appendices.
	// A slice is not closed under its own references; a projection is.
	dangling_references: [...#NonEmpty]

	// The law this context was projected from. Without it a context is
	// unfalsifiable: it cannot be shown stale, because there is nothing
	// to compare against. With it, "is this brief current" is one digest
	// comparison rather than a reading.
	derived_from: #Digest

	// Free-text notes are deliberately NOT permitted. The struct is
	// closed, so a field nobody agreed to cannot be added to smuggle
	// context past the definition -- which is the entire point.
})

// ------------------------------------------------------------ the law

// A context that may actually be dispatched.
//
// Everything below is a refusal, and each one is an incident:
//
//   operator_config_present   a personal instruction file reached the
//                             system prompt of every dispatched agent
//   withheld_present          a firewall that was intended, not proved
//   given_unmet / count > 0   an empty snapshot that "leaked nothing"
#Dispatchable: #Context & {
	harness: isolation: observed: operator_config_present: false
	staged: proof: withheld_present: []
	staged: proof: given_unmet: []
	staged: count: >0

	// `count` is stated by the extractor rather than derived, so that a
	// disagreement between it and the list is itself a finding. That
	// makes it the wrong thing to gate on alone: `files: []` with
	// `count: 1` passed, and an empty snapshot satisfies every
	// withholding rule perfectly. The GATE reads the list (Codex, #40).
	staged: files: [_, ...]

	// ...and the stated count must be the list's length. #Staged keeps
	// the two as separate observations on purpose, so that a snapshot
	// tool disagreeing with itself is describable — but a context is not
	// DISPATCHABLE on a false measurement, and `count: 999` beside one
	// file passed every check here (Codex, PR #40 round three).
	staged: count: len(staged.files)

	// An isolation observation is true of one harness build on one day.
	// A context could name version 3 and carry a clean observation taken
	// against version 1 -- and a release is exactly when a new discovery
	// path appears (Codex, PR #40).
	harness: isolation: observed: harness_version: harness.version

	// The brief and the task state the same contract twice, and until
	// now nothing made the two copies agree — so a context could hand
	// one author an interface the other author's task declared absent,
	// which is precisely the disagreement the independence method is
	// built to make impossible. The positive control encoded exactly
	// that and was still dispatchable (Codex, PR #40).
	task: #Task
	task: produces: brief.deliverables
	task: report_fields: brief.report_fields

	// The flags half of the rule below. For a flags-mechanism harness
	// the argv IS the isolation, so declaring it absent is the same
	// missing-evidence-in-a-complete-record shape as a home-mechanism
	// context with no home.
	if harness.isolation.mechanism == "flags" {
		harness: isolation: flags: [_, ...]
	}

	// A home-mechanism harness must name the home and the complete
	// credential list. Optional let a codex or grok context omit both
	// and still satisfy #Dispatchable -- missing isolation evidence
	// wearing the shape of a complete record (Codex, PR #40). An empty
	// least one credential is named, because that list is the evidence.
	if harness.isolation.mechanism == "home" {
		harness: isolation: home: #NonEmpty

		// At least ONE, and this is not pedantry: `[...#NonEmpty]` is
		// satisfied by an ABSENT field, because CUE infers the empty list
		// and the result is concrete — so the omitting document validated
		// (Grok, second read of PR #40). Same trap as unification being a
		// default rather than only a comparison. A home-mechanism harness
		// that links no credential cannot authenticate at all; build_home
		// refuses to construct one, so a context claiming otherwise
		// describes a dispatch that could not have happened.
		harness: isolation: auth_files: [_, ...]
	}

	// The outer role and the role the brief addresses are the same one.
	// Otherwise a launcher can send a reviewer's instructions to an
	// extractor and the schema will authorise it (Codex, PR #40).
	// Written this way round on purpose: inside `brief: {role: ...}` a
	// bare `role` resolves to brief's OWN field and constrains nothing.
	// Measured 2026-08-23 -- the mismatching document was still accepted
	// until the reference ran the other direction.
	// `brief` is named here as well as referenced: a struct literal's
	// scope holds only the fields IT declares, not the ones unification
	// brings in from #Context, so without this line the reference below
	// is "not found" rather than a constraint.
	brief: #Brief
	role:  brief.role

	// Sufficiency. Closure above stops what should not be there; these
	// stop what should be and is not. A context can be perfectly clean
	// and still leave a role guessing.
	unmet_requirements: []
	dangling_references: []
}
