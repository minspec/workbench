// How a claim is backed: re-run it, or attest to it. Never both, never
// neither, and never one wearing the other's clothes.
//
// A contract can pin the SHAPE of a receipt. It cannot pin that the
// receipt was earned. That sentence closed the write-up of this
// session's CUE work and it is the gap this file exists to close.
//
// There are exactly two ways a claim can be backed, and they are not
// interchangeable:
//
//   VERIFY    the claim can be re-derived. Anyone with the repository
//             runs the command at the recorded state and compares
//             digests. Trust in the claimant is not required, because
//             nothing rests on their word -- the reproduction
//             instruction IS the evidence.
//
//   CERTIFY   the claim cannot be re-derived, because it is a judgement,
//             or an observation of something that has since moved, or a
//             probe that costs money to repeat. An adjudicator's verdict
//             on a disagreement is the clearest case: re-running
//             anything produces the disagreement again, never the
//             ruling. Here trust in the claimant IS load-bearing, so the
//             claimant must be named and what they were shown must be
//             pinned.
//
// THE CENTRAL RULE, and the reason this is CUE rather than a convention:
// an attestation has NO FIELD in which to claim reproducibility. Not a
// boolean that must be false -- no slot at all. A closed struct cannot
// be given one, so "this was verified" is not a sentence an attestation
// can express, however much whoever is writing it would like to.
//
// The same shape as a judgement that cannot claim binding force: the
// admissible values are what stop the claim, not a reviewer's attention.
//
// WHAT CERTIFICATION CAN STILL BE CHECKED FOR. A judgement cannot be
// re-derived, but its GROUNDING can be. Every quote it rests on must
// appear verbatim in what the attestor was shown; it must cite at least
// one source of each required kind; the attestor must be someone the
// registry admits. Those are mechanical, they either ran or they did
// not, and #Grounding records which. That is the whole difference
// between an attestation and an opinion.

package harness

// --------------------------------------------------------------------

#Evidence: #Receipt | #Attestation

// ----------------------------------------------------------- receipt

// A claim anyone can re-derive. The fields are the reproduction.
#Receipt: close({
	// Fixed, so the two kinds can be told apart by a reader and by a
	// program without inspecting which fields happen to be present.
	kind: "receipt"

	claim: #NonEmpty

	// Everything needed to run it again. `cwd` is relative to the
	// repository root: an absolute path is a fact about one machine.
	argv: [#NonEmpty, ...#NonEmpty]
	cwd:  #NonEmpty

	// The state it ran against. A receipt without this is a claim about
	// an unnamed tree, and the tree has since moved.
	head_sha:   #Digest
	tree_dirty: bool

	exit_code:     int
	duration_ms:   int & >=0
	stdout_sha256: #Digest
	stderr_sha256: #Digest

	// Who ran it. Recorded for attribution, NOT relied upon: the whole
	// point is that a reader need not believe them.
	actor: #NonEmpty
	at:    #NonEmpty
})

// -------------------------------------------------------- attestation

// A claim that rests on someone's judgement.
//
// Note what is absent and cannot be added: argv, exit_code, output
// digests, any `reproducible` or `verified` field. A closed struct has
// no room for them, so an attestation cannot be written that claims to
// be a reproduction.
#Attestation: close({
	kind: "attestation"

	claim: #NonEmpty

	// Why re-running is not available. Required, because "we could have
	// verified this and did not" and "this cannot be verified" are
	// different situations, and only one of them is acceptable.
	not_reproducible_because: "judgement" | "external-state-moved" |
		"costly-to-repeat" | "one-time-observation"

	attestor: #Attestor

	// EXACTLY what the attestor was shown, by digest. Without it the
	// attestation floats: it cannot be said what the judgement was a
	// judgement OF, and a later reader cannot reconstruct the question.
	saw: #Digest

	// The mechanical checks that were run on the grounding. Not on the
	// judgement -- that is what cannot be checked -- but on whether the
	// judgement is anchored to what the attestor saw.
	grounding: #Grounding

	at: #NonEmpty
})

#Attestor: close({
	// Recorded, NOT admitted. The comment here used to say "must be
	// admitted by the registry"; there is no registry, and a claimant
	// cannot admit themselves, so the field says what it is: a name
	// this attestation carries. See #Admissible for why that is a
	// reason to exclude the form rather than to invent a list.
	name: #NonEmpty

	// A model attesting is not a person attesting, and a reader is
	// entitled to weigh them differently.
	kind: "model" | "person" | "tool"

	// For a model, the resolved identity from its own trace -- never the
	// alias requested. An alias is a moving target; the resolved name is
	// an identity.
	resolved: #NonEmpty

	// What the attestor could NOT see. An adjudicator with no repository
	// access is more trustworthy on a packet, not less, and that is only
	// legible if the withholding is recorded beside the verdict.
	withheld: [...#NonEmpty] | #DeclaredAbsent
})

// The bridge. A judgement cannot be re-derived; its anchoring can.
#Grounding: close({
	// Every quote the claim rests on, checked to occur verbatim in what
	// the attestor saw. A paraphrase fails: it is the point at which a
	// judgement starts drifting from its evidence.
	quotes_verbatim: bool

	// The kinds of source that must each be cited at least once -- for
	// an adjudication, the specification and the observation. A verdict
	// citing only one side has heard only one side.
	required_citations: [#NonEmpty, ...#NonEmpty]
	citations_met:      bool

	// The tool that ran these checks, so "grounded" is not itself an
	// unbacked claim. Circularity stops here: this is a receipt's job.
	checked_by: #NonEmpty
})

// ---------------------------------------------------------- admission

// Evidence that may be relied on.
//
// A receipt qualifies by being re-runnable at a named state. An
// attestation qualifies only when its grounding checks actually PASSED
// -- an attestation whose quotes were never verified is an opinion with
// a schema, which is worse than an opinion, because the schema reads as
// diligence.
// Split per kind rather than written as one conditional. `if kind == …`
// inside a disjunction cannot resolve: the field is not in scope until
// the disjunction is decided, and CUE says so with `reference "kind" not
// found`. Two admissible forms, unioned, says the same thing and
// evaluates.
#AdmissibleReceipt: #Receipt & {
	// A claim about a dirty tree names no state anyone can return to.
	tree_dirty: false
}

// An attestation whose quotes were never checked is an opinion with a
// schema -- worse than an opinion, because the schema reads as
// diligence. So this is what admission WOULD require.
//
// It is deliberately NOT part of #Admissible, and that is the honest
// position rather than a gap. Every condition here is set by the
// claimant: the two grounding booleans are ticked by whoever wrote the
// attestation, `required_citations` is a list they choose, and
// `#Attestor.name` -- whose comment promised admission "by the
// registry" -- was `#NonEmpty` with no registry anywhere in the
// repository (Codex, PR #40).
//
// A name union would not fix it. HARNESSES refuses an unknown harness
// because the LAUNCHER does that lookup; nothing on the attestation
// path is an external lookup, so a list of admitted names is one the
// claimant reads and then writes their own name from. That relocates
// #NonEmpty into a list and leaves #Admissible looking like diligence,
// which is the exact failure this file exists to name.
//
// So: an attestation stays a describable judgement -- the way #Context
// can describe a leak -- and #Admissible means receipts until something
// other than the claimant can admit an actor.
#AdmissibleAttestation: #Attestation & {
	grounding: quotes_verbatim: true
	grounding: citations_met:   true
}

#Admissible: #AdmissibleReceipt

// A claim requiring reproduction cannot be met by an attestation. Stated
// as a definition so a caller asks for the strength it needs, rather
// than accepting whatever arrived and hoping.
#MustVerify: #AdmissibleReceipt
