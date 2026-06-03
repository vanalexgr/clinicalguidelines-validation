# STATUS

Running log. Codex updates this after every task.

| Task | Status | Notes / decisions | Open [AUTHOR ACTION] |
|------|--------|-------------------|----------------------|
| T00 Bootstrap | done | install succeeds with existing pins; CI workflow + OpenWebUI API doc added | |
| T01 Schemas & IO | done | schemas, io.py, llm.py, and tests shipped | confirm judge model strings |
| T02 Benchmark loader | done | schema-backed loader + validate-benchmark CLI shipped; seed file validates | expand + verify gold keys |
| T03 Corpus index | done | recommendation index loader, template builder stub, and citation existence helper shipped | populate from ESVS corpus |
| T04 Agent client + runner | done | HTTP client, dry-run path, normalized answer runner, and cache/blinding tests shipped | confirm CGIO API shape |
| T05 Judge | done | judge runner, strict JSON parse/repair flow, separate failure log at outputs/metrics/judge_failures.jsonl, and no-network tests shipped | |
| T06 Deterministic metrics | TODO | | |
| T07 Aggregation/agreement/passfail | TODO | | |
| T08 Discordance export | TODO | | |
| T09 Report | TODO | | decide N per query type |
| T10 Human calibration | TODO | | schedule rater time |
