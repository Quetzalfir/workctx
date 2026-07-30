# Start here

This repository is prepared for an AI implementation lead.

## Human operator

Repository content, code, comments, schemas, prompts, and public documentation must be written in English. Before speaking to the human operator, read `.agents/operator.local.yaml` when it exists. That ignored local file selects the interaction language and display name without leaking personal preferences into the public repository. Fall back to `.agents/operator.example.yaml` and English when no local file exists.

## Recommended first session

Open the repository root in the strongest implementation agent available and provide this prompt:

```text
Act as the implementation lead for this repository.

Read AGENTS.md and then read .agents/plan/initial/README.md in its required order.
The repository is an implementation scaffold, not a completed product.

Your responsibilities are to:
1. validate and refine the initial architecture without casually changing product principles;
2. create a dependency-aware execution plan;
3. decide which work packages can run in parallel and which must be sequential;
4. create self-contained worker work orders under .agents/work-orders/;
5. give the human operator a copyable prompt for each worker agent they choose;
6. review every worker report, diff, commit, test result, and contract;
7. request corrections when acceptance criteria are not met;
8. integrate only validated work;
9. maintain implementation status, risks, ADRs, tests, and public documentation;
10. keep all repository artifacts in English while reporting in the configured interaction language.

Do not delegate foundational decisions before you understand the complete initial plan.
Do not accept a worker's claim without inspecting the actual files and running the required tests.
Begin by producing an implementation-lead briefing in the configured interaction language that identifies the first execution wave,
its dependencies, the work orders you will create, and the validation gates.
```

## Required reading order for the implementation lead

1. `AGENTS.md`
2. `.agents/plan/initial/README.md`
3. `.agents/plan/initial/00-executive-brief.md`
4. `.agents/plan/initial/01-product-scope.md`
5. `.agents/plan/initial/02-architecture.md`
6. `.agents/plan/initial/03-reference-and-retrieval-model.md`
7. `.agents/plan/initial/05-agent-orchestration-protocol.md`
8. `.agents/plan/initial/06-implementation-work-packages.md`
9. `.agents/plan/initial/07-test-strategy.md`
10. `.agents/plan/initial/12-definition-of-done.md`
11. `.agents/plan/initial/13-skill-and-agent-adapter-design.md`

The remaining plan documents are mandatory before approving the related work package.

## Delegation model

The default workflow is manual-agent-compatible:

1. The lead creates a work-order directory.
2. The human operator opens the selected agent in the assigned Git worktree.
3. The human operator copies `prompt.md` to that worker.
4. The worker implements only the contract scope.
5. The worker writes `report.md` and `report.json`.
6. The human operator returns the report or points the lead to the completed worktree.
7. The lead independently reviews the changes and runs validation.
8. The lead accepts, requests revision, or rejects the delivery.

This works even when the agents cannot directly communicate with one another.
